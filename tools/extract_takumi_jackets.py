"""Build the compact TAKUMI³ jacket atlas from an official Android APK.

Usage:
    python tools/extract_takumi_jackets.py path/to/com.thiqxis.takumicubic.apk

This maintenance tool requires UnityPy. Runtime image generation only needs
Pillow and the generated atlas files.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
import zipfile
from pathlib import Path

import UnityPy
from PIL import Image, ImageOps


ROOT = Path(__file__).parents[1]
CATALOG_PATH = ROOT / "src" / "plugins" / "takumi_b40" / "song_catalog.json"
ATLAS_PATH = ROOT / "src" / "plugins" / "takumi_b40" / "jacket_atlas.webp"
INDEX_PATH = ROOT / "src" / "plugins" / "takumi_b40" / "jacket_atlas.json"
CELL_SIZE = 160
COLUMNS = 18


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    args = parser.parse_args()

    songs = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    with zipfile.ZipFile(args.apk) as archive:
        member = "assets/bin/Data/data.unity3d"
        environment = UnityPy.load(archive.read(member))

    resource_manager = next(
        item for item in environment.objects if item.type.name == "ResourceManager"
    )
    container = resource_manager.read_typetree()["m_Container"]
    path_by_name = {}
    for name, pointer in container:
        normalized = _key(name)
        if normalized.startswith("songdatas/") and normalized.endswith("/jacket"):
            path_by_name.setdefault(normalized, int(pointer["m_PathID"]))

    textures = {
        item.path_id: item
        for item in environment.objects
        if item.assets_file.name == "resources.assets" and item.type.name == "Texture2D"
    }
    extracted = []
    missing = []
    for song in songs:
        internal_name = str(song.get("internal_name") or "")
        resource_name = _key(f"songdatas/{internal_name}/jacket")
        path_id = path_by_name.get(resource_name)
        texture = textures.get(path_id) if path_id is not None else None
        if texture is None:
            missing.append((song["song_id"], internal_name, song["title"]))
            continue
        artwork = ImageOps.fit(
            texture.read().image.convert("RGB"),
            (CELL_SIZE, CELL_SIZE),
            method=Image.Resampling.LANCZOS,
        )
        extracted.append((int(song["song_id"]), artwork))

    rows = (len(extracted) + COLUMNS - 1) // COLUMNS
    atlas = Image.new("RGB", (COLUMNS * CELL_SIZE, rows * CELL_SIZE))
    index = {}
    for position, (song_id, artwork) in enumerate(extracted):
        column, row = position % COLUMNS, position // COLUMNS
        atlas.paste(artwork, (column * CELL_SIZE, row * CELL_SIZE))
        index[str(song_id)] = position

    atlas.save(ATLAS_PATH, "WEBP", quality=88, method=6)
    INDEX_PATH.write_text(
        json.dumps(
            {"cell_size": CELL_SIZE, "columns": COLUMNS, "songs": index},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    print(f"extracted {len(extracted)} jackets; missing {len(missing)}")
    for item in missing:
        print("missing", *item)


if __name__ == "__main__":
    main()
