"""Extract collection art from a locally supplied TAKUMI Unity bundle.

Maintenance-only dependency: UnityPy (not required by the bot).
IDs come from the bundle's DAN CSVs; filenames alone aren't player IDs.
"""
import argparse
import csv
import io
import json
import math
from pathlib import Path

from PIL import Image, ImageOps
import UnityPy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "src/plugins/takumi_b40/collection_assets")
    parser.add_argument("--variants", action="store_true", help="Also extract alternate menu plate textures")
    args = parser.parse_args()
    env = UnityPy.load(str(args.bundle))
    sprites, tables = {}, {}
    for obj in env.objects:
        if obj.type.name not in ("Sprite", "TextAsset"):
            continue
        data = obj.read()
        if obj.type.name == "Sprite":
            sprites[data.m_Name] = data
        elif data.m_Name in ("DAN", "DAN2", "DAN3", "DAN4"):
            tables[data.m_Name] = data.m_Script
    icons, source_map = {}, {}
    prefixes = {"DAN": "IconList_", "DAN2": "icon2_", "DAN3": "Dan3_", "DAN4": "Dan4_"}
    for table, prefix in prefixes.items():
        rows = csv.DictReader(io.StringIO(tables[table].lstrip("\ufeff")))
        for row in rows:
            identity = row.get("アイコンID", "").strip()
            if not identity:
                continue
            number = int(identity.rsplit("_", 1)[-1])
            sprite_name = prefix + str(number)
            if sprite_name not in sprites:
                raise ValueError(f"Missing DAN sprite: {sprite_name}")
            icons[identity] = sprites[sprite_name].image.convert("RGBA")
            source_map[identity] = {"table": table, "sprite": sprite_name}
    size, columns = 192, 10
    atlas = Image.new("RGBA", (size * columns, size * math.ceil(len(icons) / columns)))
    index = {}
    for n, (identity, icon) in enumerate(sorted(icons.items())):
        fitted = ImageOps.contain(icon, (size, size), Image.Resampling.LANCZOS)
        atlas.alpha_composite(fitted, (n % columns * size + (size-fitted.width)//2,
                                      n // columns * size + (size-fitted.height)//2))
        index[identity] = n
    args.output.mkdir(parents=True, exist_ok=True)
    atlas.save(args.output / "icons.webp", lossless=True)
    (args.output / "icons.json").write_text(json.dumps({"cell_size": size, "columns": columns, "icons": index}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plates = {}
    for rank in range(7):
        name = f"Achievement_{rank}"
        if name in sprites:
            target = args.output / f"title_{rank}.png"
            sprites[name].image.save(target)
            plates[str(rank)] = name
        alternate = f"Achievement2_{rank}"
        if args.variants and alternate in sprites:
            sprites[alternate].image.save(args.output / f"title_alt_{rank}.png")
    (args.output / "extraction.json").write_text(json.dumps({"icons": source_map, "title_plates": plates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(icons)} account-ID-mapped icons and {len(plates)} title plates.")
    print("Title sprite names:", [name for name in sprites if name.startswith('Achievement')])


if __name__ == "__main__":
    main()
