"""Offline previews of every CHUNITHM report using simulated records.

Example: python scripts/preview_chunithm.py --jackets tmp/chu_style_refs/jackets
"""

import argparse
import importlib.util
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import chunithm_render as theme

spec = importlib.util.spec_from_file_location(
    "chunithm_preview_core", ROOT / "src/plugins/chunithm_b30/b30_core.py",
)
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)

SONGS = (
    (2802, "Crossmythos Rhapsodia", 15.7),
    (1086, "祈 -我ら神祖と共に歩む者なり-", 15.7),
    (2652, "Forsaken Tale", 15.7),
    (2739, "Aether Crest: Celestial", 15.5),
    (982, "The Metaverse -First story of the SeelischTact-", 15.7),
    (2353, "幻想即興曲", 15.1),
    (103, "エンドマークに希望と涙を添えて", 15.0),
    (2846, "Tru'nembra", 15.6),
)


class PreviewPlayer:
    name = "iceWaG"
    level = 92
    reborn_count = 6
    class_emblem = {"base": 5, "medal": 5}
    rating_floor = 17.42
    trophy_name = "CHUNITHM Mate"
    trophy_color = "normal"

    def __init__(self, collections=None):
        self.collections = collections

    def _collection(self, name):
        if self.collections is not None:
            path = self.collections / name
            if path.exists():
                with Image.open(path) as image:
                    return image.convert("RGBA")
        return None

    def load_map_icon_image(self):
        return self._collection("icon_19.png")

    def load_name_plate_image(self):
        return self._collection("plate_10131.png")

    def load_character_image(self):
        return None

    def load_trophy_image(self):
        return None


class PreviewScore:
    def __init__(self, index, jackets):
        self.id, self.song_name, constant = SONGS[index % len(SONGS)]
        self.origin_id = self.id
        self.level_index = (3, 4, 3, 2, 3, 3, 4, 3)[index % 8]
        self.final_level = f"{constant:.1f}"
        self.raw_level = "15+" if constant >= 15.5 else "15"
        self.level_is_constant = True
        self.score = 1_009_976 - index * 91
        self.rank = "SSS+" if self.score >= 1_009_000 else "SSS" if self.score >= 1_007_500 else "SS+"
        self.rating_floor = constant + 2.12 - index * .013
        self.jacket_path = jackets / f"{self.id}.png"

    def load_jacket_image(self):
        if self.jacket_path.exists():
            with Image.open(self.jacket_path) as source:
                return source.convert("RGBA")
        image = Image.new("RGB", (320, 320), theme.PAPER)
        draw = ImageDraw.Draw(image)
        theme.text(draw, (160, 145), str(self.id), 64, kind="number", anchor="mm")
        return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/chu_mate_preview")
    parser.add_argument("--jackets", type=Path, default=ROOT / "tmp/chu_style_refs/jackets")
    parser.add_argument("--collections", type=Path, help="Optional local demo collection images; never fetched online")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    player = PreviewPlayer(args.collections)
    scores = [PreviewScore(i, args.jackets) for i in range(50)]
    bests = sorted(scores, key=lambda score: score.rating_floor, reverse=True)
    b30_rating = sum(s.rating_floor for s in bests[:30]) / 30
    player.rating_floor = sum(s.rating_floor for s in bests) / 50
    core.create_b30_style_image(player, bests[:30], b30_rating, args.output / "b30.png")
    core.create_b50_style_image(player, bests[:30], bests[30:], player.rating_floor, args.output / "b50.png")
    core.create_push_score_image(player, scores[4], args.output / "push.png")
    core.create_push_score_image(player, scores[0], args.output / "fu.png", title="装福")
    query = core.parse_level_query("15+")
    matched = [s for s in scores if core._chunithm_score_matches(s, query)]
    matched.sort(key=lambda s: (float(s.final_level), s.score), reverse=True)
    score_paths = core.create_chunithm_score_list_images(
        player, matched[:12], query, args.output, "preview",
    )
    score_paths[0].replace(args.output / "score.png")
    entries = [core.FitConstEntry(s.id, s.song_name, s.level_index, float(s.final_level), s)
               for s in scores[:8]]
    fit_path = core.create_chunithm_fitconst_image(
        player, entries, core.parse_constant_range_query("15"), args.output, "preview",
    )
    fit_path.replace(args.output / "fitconst.png")
    # All 14 grades on identical cards expose height/alignment regressions.
    rank_scores = []
    for i, (rank, value) in enumerate(zip(theme.RANK_ASSETS, (
        480000, 590000, 660000, 740000, 780000, 825000, 875000,
        940000, 980000, 993000, 1002500, 1006500, 1008200, 1009980,
    ))):
        score = PreviewScore(i, args.jackets)
        score.rank = rank
        score.score = value
        rank_scores.append(score)
    rank_sheet = theme.render_score_sheet(
        player, [theme.ScoreSection("", rank_scores)], "RANK COLLECTION",
    )
    theme.save_report(rank_sheet, args.output / "ranks.png")
    # Enlarged, offline-only crop for checking the arcade player-panel geometry.
    profile = theme.render_profile(player)
    profile_preview = Image.new("RGB", (profile.width + 40, profile.height + 40), theme.WHITE)
    profile_preview.paste(profile, (20, 20), profile)
    theme.save_report(profile_preview.resize(
        (profile_preview.width * 2, profile_preview.height * 2), theme.LANCZOS,
    ), args.output / "profile.png")
    with Image.open(args.output / "b50.png") as best:
        theme.save_report(best.crop((0, 0, best.width, theme.BEST_HEADER_HEIGHT)),
                          args.output / "header.png")
    for path in sorted(args.output.glob("*.png")):
        with Image.open(path) as image:
            print(f"{path.name}: {image.width}x{image.height}")


if __name__ == "__main__":
    main()
