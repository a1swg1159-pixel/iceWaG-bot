import importlib.util
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


# Load the core module directly so these pure-logic tests do not initialize a
# NoneBot driver or register command matchers as a side effect.
if importlib.util.find_spec("requests") is None:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = RuntimeError
    requests_stub.post = lambda *args, **kwargs: None
    requests_stub.get = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub

CORE_PATH = Path(__file__).parents[1] / "src" / "plugins" / "takumi_b40" / "core.py"
SPEC = importlib.util.spec_from_file_location("takumi_b40_core_test", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CORE
SPEC.loader.exec_module(CORE)

BINDINGS_PATH = (
    Path(__file__).parents[1] / "src" / "plugins" / "takumi_b40" / "bindings.py"
)
BINDINGS_SPEC = importlib.util.spec_from_file_location(
    "takumi_b40_bindings_test", BINDINGS_PATH
)
BINDINGS = importlib.util.module_from_spec(BINDINGS_SPEC)
sys.modules[BINDINGS_SPEC.name] = BINDINGS
BINDINGS_SPEC.loader.exec_module(BINDINGS)

B40Result = CORE.B40Result
BestScore = CORE.BestScore
CatalogChart = CORE.CatalogChart
TakumiAuthError = CORE.TakumiAuthError
build_b40_from_user_data = CORE.build_b40_from_user_data
parse_playfab_scores = CORE.parse_playfab_scores
render_b40_image = CORE.render_b40_image
render_score_list_images = CORE.render_score_list_images
song_contribution = CORE.song_contribution


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


class FakeCatalogResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class TakumiRatingTests(unittest.TestCase):
    def test_rating_formula_boundaries(self):
        constant = 15.0
        expected = {
            800_000: 0.0,
            970_000: 15.0 / 34,
            990_000: 16.0 / 34,
            995_000: 16.5 / 34,
            999_000: 17.0 / 34,
            1_000_000: 17.1 / 34,
            1_010_000: 17.1 / 34,
        }
        for score, contribution in expected.items():
            with self.subTest(score=score):
                self.assertAlmostEqual(
                    song_contribution(score, constant), contribution, places=12
                )

    def test_playfab_score_parsing_uses_best_score(self):
        user_data = {
            "0202_SongScore_Regular_Master": {
                "Value": json.dumps([
                    {"SongID": 153, "Score": 990_000, "Medal": 2},
                    {"SongID": 153, "Score": 999_000, "Medal": 3},
                ])
            },
            "0203_SongScore_Regular_Insanity": {
                "Value": json.dumps([
                    {"SongID": 177, "Score": 1_000_000, "PlayCount": 4}
                ])
            },
        }
        self.assertEqual(
            parse_playfab_scores(user_data),
            {(153, 2): 999_000, (177, 3): 1_000_000},
        )

    def test_build_b40_maps_duplicate_titles_by_official_song_id_order(self):
        user_data = {
            "0202_SongScore_Regular_Master": {
                "Value": json.dumps([
                    {"SongID": 153, "Score": 990_000},
                    {"SongID": 177, "Score": 999_000},
                ])
            }
        }
        catalog = [
            CatalogChart(153, "Same Song", "MASTER", "15"),
            CatalogChart(177, "Same Song", "MASTER", "15"),
        ]
        charts = [
            {
                "chart_id": "same_master_first",
                "title": "Same Song",
                "difficulty": "MASTER",
                "const_value": 15.1,
                "match_config": {"csv_level": "15", "order": 0},
            },
            {
                "chart_id": "same_master_second",
                "title": "Same Song",
                "difficulty": "MASTER",
                "const_value": 15.4,
                "match_config": {"csv_level": "15", "order": 1},
            },
        ]
        result = build_b40_from_user_data(
            user_data, catalog=catalog, charts=charts, fetched_at=1.0
        )
        self.assertEqual(len(result.scores), 2)
        self.assertEqual(result.scores[0].chart_id, "same_master_second")
        self.assertEqual(result.source_score_count, 2)
        self.assertEqual(result.matched_chart_count, 2)
        self.assertEqual(result.unmatched_row_count, 0)
        self.assertEqual(len(result.all_scores), 2)
        self.assertEqual(result.all_scores[0].display_level, "15")

    def test_bundled_catalog_covers_every_chart_constant(self):
        catalog = CORE._load_bundled_catalog()
        game_scores = [
            CORE.GameScore(
                item.song_id, item.title, item.difficulty, item.level,
                1_000_000, item.constant,
            )
            for item in catalog
        ]
        matched, _ = CORE.match_scores(game_scores)
        self.assertEqual(len(matched), len(game_scores))

    def test_online_constant_fills_a_missing_community_chart(self):
        user_data = {
            "0202_SongScore_Regular_Master": {
                "Value": json.dumps([{"SongID": 302, "Score": 999_000}])
            }
        }
        catalog = [CatalogChart(302, "New Song", "MASTER", "13+", 13.9)]

        result = build_b40_from_user_data(
            user_data, catalog=catalog, charts=[], fetched_at=1.0
        )

        self.assertEqual(result.unmatched_row_count, 0)
        self.assertEqual(result.all_scores[0].constant, 13.9)
        self.assertEqual(result.all_scores[0].chart_id, "playfab:302:MASTER")

    def test_online_catalog_is_persisted_and_keeps_exact_constants(self):
        header = [f"column-{index}" for index in range(19)]
        row = [""] * 19
        row[0] = "NewSongInternal"
        row[7:11] = ["60", "120", "139", "155"]
        row[11] = "Dl"
        row[15] = "302"
        row[17] = "New Song"
        row[18] = "true"
        content = (",".join(header) + "\n" + ",".join(row)).encode("utf-8")

        CORE._CATALOG_CACHE = None
        with patch.object(
            CORE.requests, "get", return_value=FakeCatalogResponse(content)
        ), patch.object(CORE, "_save_runtime_catalog") as save:
            catalog = CORE.load_song_catalog(force_refresh=True)

        save.assert_called_once()
        master = next(item for item in catalog if item.difficulty == "MASTER")
        self.assertEqual(master.constant, 13.9)
        self.assertEqual(master.level, "13+")


class TakumiPlayFabTests(unittest.TestCase):
    def test_email_login_and_custom_id_link_use_expected_endpoints(self):
        responses = [
            FakeResponse(200, {"data": {
                "SessionTicket": "ticket", "PlayFabId": "player-1",
                "InfoResultPayload": {
                    "PlayerProfile": {"DisplayName": "TAKUMI Player"}
                },
            }}),
            FakeResponse(200, {"data": {}}),
        ]
        with patch.object(CORE.requests, "post", side_effect=responses) as post:
            account = CORE.link_account("player@example.com", "secret-password")

        self.assertEqual(account.playfab_id, "player-1")
        self.assertEqual(account.display_name, "TAKUMI Player")
        self.assertTrue(account.custom_id.startswith("t3bot_"))
        self.assertTrue(post.call_args_list[0].args[0].endswith("LoginWithEmailAddress"))
        self.assertTrue(post.call_args_list[1].args[0].endswith("LinkCustomID"))
        self.assertEqual(
            post.call_args_list[1].kwargs["headers"]["X-Authorization"], "ticket"
        )
        self.assertNotIn("Password", post.call_args_list[1].kwargs["json"])

    def test_invalid_login_is_user_facing_auth_error(self):
        response = FakeResponse(400, {
            "error": "InvalidEmailOrPassword",
            "errorMessage": "Invalid email or password",
        })
        with patch.object(CORE.requests, "post", return_value=response):
            with self.assertRaises(TakumiAuthError):
                CORE.login_with_email("player@example.com", "wrong-password")


class TakumiBindingTests(unittest.TestCase):
    def test_binding_store_preserves_legacy_entries_without_using_them(self):
        with tempfile.TemporaryDirectory() as directory:
            BINDINGS.BINDINGS_FILE = Path(directory) / "bindings.json"
            BINDINGS.BINDINGS_FILE.write_text(
                json.dumps({"old-user": "legacy-public-id"}), encoding="utf-8"
            )
            self.assertIsNone(BINDINGS.get_binding("old-user"))

            saved = BINDINGS.set_binding(
                "new-user", "t3bot_random", "playfab-id", "Player"
            )
            self.assertEqual(BINDINGS.get_binding("new-user"), saved)
            document = json.loads(
                BINDINGS.BINDINGS_FILE.read_text(encoding="utf-8")
            )
            self.assertEqual(document["old-user"], "legacy-public-id")
            self.assertNotIn("password", document["new-user"])


class TakumiRendererTests(unittest.TestCase):
    def test_bundled_jacket_atlas_contains_official_artwork(self):
        jacket = CORE.load_jacket_image(1)
        self.assertIsNotNone(jacket)
        self.assertEqual(jacket.size, (160, 160))
        _, songs, _, _ = CORE._load_jacket_atlas()
        song_catalog = json.loads(CORE.SONG_CATALOG_PATH.read_text(encoding="utf-8"))
        catalog_ids = {int(item["song_id"]) for item in song_catalog}
        self.assertTrue({int(song_id) for song_id in songs}.issubset(catalog_ids))

    def test_renderer_outputs_full_b40_canvas(self):
        scores = tuple(
            BestScore(
                chart_id=f"chart-{index}",
                title=f"Test Track {index + 1}",
                difficulty=("MASTER", "RAVAGE", "INSANITY")[index % 3],
                constant=16.3 - index * 0.05,
                score=1_000_000 - index * 173,
                contribution=(16.3 - index * 0.05 + 2.1) / 34,
                single_rating=((16.3 - index * 0.05 + 2.1) / 34) * 40,
                rank="S+",
                song_id=index + 1,
            )
            for index in range(40)
        )
        result = B40Result(
            scores=scores,
            rating=sum(item.contribution for item in scores),
            source_score_count=391,
            matched_chart_count=391,
            unmatched_row_count=0,
            fetched_at=time.time(),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = render_b40_image(result, "测试玩家 TAKUMI", Path(directory), "123")
            self.assertTrue(path.exists())
            with Image.open(path) as image:
                self.assertEqual(image.size, (3000, 2556))
                self.assertEqual(image.mode, "RGB")

    def test_score_list_supports_display_level_and_exact_constant(self):
        scores = (
            BestScore(
                "chart-1", "Level Fifteen", "MASTER", 15.4, 999_000,
                0.5, 20.0, "S+", 1, "15",
            ),
            BestScore(
                "chart-2", "Level Fifteen Plus", "INSANITY", 15.8,
                998_000, 0.5, 20.0, "S+", 2, "15+",
            ),
        )
        result = B40Result(
            scores=scores,
            rating=1.0,
            source_score_count=2,
            matched_chart_count=2,
            unmatched_row_count=0,
            fetched_at=time.time(),
            all_scores=scores,
        )
        level_query = CORE.parse_level_query("15+")
        exact_query = CORE.parse_level_query("15.8")
        self.assertFalse(CORE._takumi_score_matches(scores[0], level_query))
        self.assertTrue(CORE._takumi_score_matches(scores[1], level_query))
        self.assertFalse(CORE._takumi_score_matches(scores[0], exact_query))
        self.assertTrue(CORE._takumi_score_matches(scores[1], exact_query))

        with tempfile.TemporaryDirectory() as directory:
            paths = render_score_list_images(
                result, [scores[1]], exact_query, "测试玩家",
                Path(directory), "123",
            )
            self.assertEqual(len(paths), 1)
            with Image.open(paths[0]) as image:
                self.assertEqual(image.size, (3000, 956))
                self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
