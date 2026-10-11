import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops

from src import takumi_collections as collections
from src import takumi_profile as profile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("takumi_collection_test_core", ROOT / "src/plugins/takumi_b40/core.py")
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


def data(icon="d2_15", title="demo", scores=True):
    payload = {
        "0000_PlayerData": {"Value": json.dumps({"IconID": icon, "AchievementID": title, "DanIconID": None})},
        "0700_IconData": {"Value": json.dumps([{"IconId": "d1_01"}, {"IconId": "d1_01"}])},
        "0500_AchievementData": {"Value": json.dumps([
            {"AchievementID": "unlocked", "IsAchieve": True},
            {"AchievementID": "locked", "IsAchieve": False},
            {"AchievementID": "not_boolean", "IsAchieve": "true"}])},
    }
    if scores:
        payload["0202_SongScore_Regular_Master"] = {"Value": '[{"SongID":153,"Score":999000}]'}
    return payload


class CollectionParsingTests(unittest.TestCase):
    def test_equipment_is_separate_from_ownership(self):
        parsed = collections.parse_collections(data())
        self.assertEqual(parsed.icon_id, "d2_15")
        self.assertEqual(parsed.owned_icon_ids, ("d1_01",))
        self.assertEqual(parsed.achieved_title_ids, ("unlocked",))
        self.assertEqual(parsed.dan_icon_id, "")

    def test_malformed_fields_are_isolated(self):
        self.assertEqual(collections.parse_collections(None), collections.PlayerCollections())
        malformed = data()
        malformed["0000_PlayerData"]["Value"] = "broken"
        malformed["0500_AchievementData"]["Value"] = "{}"
        self.assertEqual(collections.parse_collections(malformed).icon_id, "")
        self.assertEqual(collections.parse_collections(malformed).owned_icon_ids, ("d1_01",))
        self.assertEqual(collections.parse_collections(data(icon=[1])).icon_id, "")
        self.assertEqual(collections.parse_collections(data(icon="a\nb")).icon_id, "")

    def test_oversized_field_is_ignored(self):
        payload = {"0000_PlayerData": {"Value": " "*(collections.MAX_METADATA_BYTES+1)}}
        self.assertEqual(collections.parse_collections(payload), collections.PlayerCollections())

    def test_real_title_definition(self):
        catalog = collections._read_catalog(collections.ACHIEVEMENTS_PATH)
        title = catalog["aj_Kosoku_105Gou_XXX_SenIns_5"]
        self.assertEqual(title["rank"], 5)
        self.assertTrue(title["name"].startswith("光速も超えて"))
        self.assertGreater(len(catalog), 1000)

    def test_csv_quoted_newlines_and_headers(self):
        parsed = collections.parse_achievement_csv('称号ID,称号表示名,ランク,解禁情報ID\na,"Hello, world",3,"one\ntwo"\n')
        self.assertEqual(parsed, {"a": {"name": "Hello, world", "rank": 3}})
        with self.assertRaises(ValueError):
            collections.parse_achievement_csv("曲名,作者,難易度\na,b,3")

    def test_unknown_equipment_has_no_fake_default(self):
        parsed = collections.resolve_collections(collections.parse_collections(data()), {})
        self.assertEqual(parsed.title, "")
        self.assertIsNone(parsed.title_rank)
        self.assertIsNone(collections.load_icon("../../title_5.png"))
        self.assertIsNone(collections.load_icon("unmapped-new-icon"))

    def test_icon_crops_are_not_shared(self):
        icon = collections.load_icon("d2_15")
        self.assertEqual(icon.size, (192, 192))
        original = icon.tobytes()
        icon.paste((0, 0, 0, 0), (0, 0, 192, 192))
        self.assertEqual(collections.load_icon("d2_15").tobytes(), original)

    def test_all_extracted_art_is_decodable(self):
        index = json.loads((collections.ASSETS / "icons.json").read_text())
        self.assertEqual(len(index["icons"]), 60)
        for identity in index["icons"]:
            self.assertIsNotNone(collections.load_icon(identity).getbbox())
        for rank in range(7):
            self.assertIsNotNone(profile._title_plate(rank))

    def test_title_texture_stays_inside_result_banner(self):
        for rank in range(7):
            plate = profile._title_plate(rank)
            self.assertEqual(plate.getpixel((610, 4))[3], 0)
            self.assertEqual(plate.getpixel((4, 70))[3], 0)


class MetadataCacheTests(unittest.TestCase):
    def test_offline_render_path_never_downloads(self):
        with patch.object(collections.requests, "get") as get:
            collections.resolve_collections(collections.PlayerCollections(achievement_id="new"))
        get.assert_not_called()

    def test_refresh_failure_retains_titles_and_backs_off(self):
        local = {"demo": {"name": "Title", "rank": 0}}
        with patch.object(collections, "_TITLE_CACHE", local), patch.object(collections, "_TITLE_REFRESH_AT", 0), \
             patch.object(collections.requests, "get", side_effect=collections.requests.RequestException) as get:
            self.assertEqual(collections.load_achievements(allow_network=True), local)
            self.assertEqual(collections.load_achievements(allow_network=True), local)
            self.assertEqual(get.call_count, 1)

    def test_refresh_persists_public_metadata_only(self):
        response = Mock()
        response.iter_content.return_value = ['称号ID,称号表示名,ランク\ndemo,Title,5\n'.encode()]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(collections, "_TITLE_CACHE", {}), patch.object(collections, "_TITLE_REFRESH_AT", 0), \
             patch.object(collections, "RUNTIME_ACHIEVEMENTS_PATH", Path(directory)/"public.json"), \
             patch.object(collections.requests, "get", return_value=response) as get:
            catalog = collections.load_achievements(allow_network=True)
            collections.load_achievements(allow_network=True)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(json.loads((Path(directory)/"public.json").read_text()), catalog)
            self.assertNotIn("headers", get.call_args.kwargs)
            self.assertNotIn("auth", get.call_args.kwargs)
            response.close.assert_called_once()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        core._RESULT_CACHE.clear()
        self.title_patch = patch.object(collections, "_TITLE_CACHE", {"demo": {"name": "My title", "rank": 2}})
        self.title_patch.start()
        self.addCleanup(self.title_patch.stop)
        self.catalog_patch = patch.object(core, "load_song_catalog", return_value=(core.CatalogChart(153, "Test song", "MASTER", "14", 14.2),))
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def test_fetch_requests_only_expected_keys(self):
        with patch.object(core, "_playfab_call", return_value={"Data": {}}) as call:
            core.fetch_user_data("ticket")
        self.assertEqual(set(call.call_args.args[1]["Keys"]), set(core.REGULAR_SCORE_KEYS.values()) | set(collections.COLLECTION_DATA_KEYS))

    def test_score_math_unchanged_by_collections(self):
        with_collection = core.build_b40_from_user_data(data(), charts=[], fetched_at=1)
        without = core.build_b40_from_user_data({"0202_SongScore_Regular_Master": data()["0202_SongScore_Regular_Master"]}, charts=[], fetched_at=1)
        self.assertEqual(with_collection.scores, without.scores)
        self.assertEqual(with_collection.rating, without.rating)
        self.assertEqual(with_collection.collections.title, "My title")

    def test_account_cache_refresh_and_isolation(self):
        with patch.object(core, "login_with_custom_id", return_value=core.PlayFabSession("ticket", "id", "Current name")) as login, \
             patch.object(core, "fetch_user_data", side_effect=[data(), data("d2_01"), data("d2_02")]):
            first = core.get_b40("test-binding-a")
            self.assertIs(core.get_b40("test-binding-a"), first)
            second = core.get_b40("test-binding-b")
            refreshed = core.get_b40("test-binding-a", force_refresh=True)
        self.assertEqual(login.call_count, 3)
        self.assertEqual(first.collections.icon_id, "d2_15")
        self.assertEqual(second.collections.icon_id, "d2_01")
        self.assertEqual(refreshed.collections.icon_id, "d2_02")
        self.assertEqual(refreshed.display_name, "Current name")

    def test_unknown_title_can_refresh_without_changing_scores(self):
        with patch.object(core, "login_with_custom_id", return_value=core.PlayFabSession("ticket", "id")), \
             patch.object(core, "fetch_user_data", return_value=data(title="new")), \
             patch.object(core, "load_achievements", return_value={"new": {"name": "New title", "rank": 6}}) as refresh:
            result = core.get_b40("test-binding-a")
        refresh.assert_called_once_with(allow_network=True)
        self.assertEqual(result.collections.title, "New title")
        self.assertEqual(result.matched_chart_count, 1)

    def test_no_scores_const_still_gets_equipment(self):
        with patch.object(core, "login_with_custom_id", return_value=core.PlayFabSession("ticket", "id", "No scores")), \
             patch.object(core, "fetch_user_data", return_value=data(scores=False)), \
             patch.object(core, "render_const_list_images", return_value=[Path("unused.png")]) as render:
            _, count, played = core.generate_const_list_images("binding", "old name", Path("unused"), "test", "14")
        self.assertGreater(count, 0)
        self.assertEqual(played, 0)
        self.assertEqual(render.call_args.args[2], "No scores")
        self.assertEqual(render.call_args.kwargs["collections"].icon_id, "d2_15")

    def test_unbound_const_never_reads_an_account(self):
        with patch.object(core, "login_with_custom_id") as login, \
             patch.object(core, "render_const_list_images", return_value=[]) as render:
            core.generate_const_list_images(None, "PLAYER", Path("unused"), "test", "14")
        login.assert_not_called()
        self.assertEqual(render.call_args.kwargs["collections"], collections.PlayerCollections())

    def test_panel_has_fixed_dimensions_and_no_shared_player_layers(self):
        equipped = collections.resolve_collections(collections.parse_collections(data()))
        first = profile.player_panel("Account A", 16.3, equipped)
        blank_before = profile.player_panel("Account B", None)
        profile.player_panel("Account C", 18, equipped)
        blank_after = profile.player_panel("Account B", None)
        self.assertEqual(first.size, (1080, 245))
        self.assertIsNone(ImageChops.difference(blank_before, blank_after).getbbox())
        self.assertNotEqual(first.tobytes(), blank_before.tobytes())


if __name__ == "__main__":
    unittest.main()
