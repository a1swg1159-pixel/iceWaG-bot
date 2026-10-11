"""Read one explicitly selected existing binding and render its collections.

Does not start NoneBot or modify the game account. Only rendered images enter
the ignored output directory; tokens and raw account saves are never printed
or persisted. Run with --qq <bound QQ>.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from src.image_credit import append_image_credit
from src.takumi_collections import load_icon
from src.takumi_profile import player_panel, tech

SPEC = importlib.util.spec_from_file_location("takumi_live_preview_core", ROOT / "src/plugins/takumi_b40/core.py")
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq", required=True)
    args = parser.parse_args()
    selected = json.loads((ROOT / "data/takumi_bindings.json").read_text(encoding="utf-8")).get(args.qq)
    if not isinstance(selected, dict) or not selected.get("custom_id"):
        raise SystemExit("No usable selected binding.")
    try:
        result = core.get_b40(selected["custom_id"], force_refresh=True)
    except core.TakumiError as exc:
        raise SystemExit(type(exc).__name__) from None
    name = result.display_name or selected.get("display_name", "PLAYER")
    out = ROOT / "data/takumi_outputs"
    out.mkdir(parents=True, exist_ok=True)
    suffix = str(time.time_ns())
    panel = player_panel(name, result.rating, result.collections)
    canvas = Image.new("RGBA", (1120, 280), "#080e16")
    canvas.alpha_composite(panel, (20, 16))
    path = out / f"collections_{suffix}.png"
    append_image_credit(canvas, tech(18), footer_fill=(8,14,22), text_fill=(194,197,192), rule_fill=(91,176,179)).save(path)
    print("Profile:", path)
    started = time.perf_counter()
    b40 = core.render_b40_image(result, name, out, "preview")
    print("B40:", b40)
    print(f"B40 render and PNG: {time.perf_counter()-started:.2f}s")
    with Image.open(b40) as sheet:
        header = append_image_credit(sheet.crop((0, 0, sheet.width, 374)), tech(23),
                                    footer_fill=(7,19,29), text_fill=(171,205,214), rule_fill=(27,78,96))
        header_path = out / f"header_{suffix}.png"
        header.save(header_path, compress_level=3)
        print("Header:", header_path)
    scores = list(result.all_scores[:10])
    if scores:
        query = core.parse_level_query(str(int(scores[0].constant)))
        matched = [score for score in result.all_scores if core._takumi_score_matches(score, query)]
        print("Score:", core.render_score_list_images(result, matched, query, name, out, "preview")[0])
    paths, total, played = core.generate_const_list_images(selected["custom_id"], name, out, "preview", "15.0")
    if paths:
        print("Const:", paths[0])
    print(json.dumps({"equipped_icon_resolved": load_icon(result.collections.icon_id) is not None,
                      "equipped_title_resolved": bool(result.collections.title),
                      "owned_icon_count": len(result.collections.owned_icon_ids),
                      "achieved_title_count": len(result.collections.achieved_title_ids),
                      "const_total": total, "const_played": played}, ensure_ascii=False))


if __name__ == "__main__":
    main()
