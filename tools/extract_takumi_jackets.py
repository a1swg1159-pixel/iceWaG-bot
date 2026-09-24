"""Build compact TAKUMI³ image atlases from an official Android APK.

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
RANK_ATLAS_PATH = ROOT / "src" / "plugins" / "takumi_b40" / "rank_atlas.webp"
RANK_INDEX_PATH = ROOT / "src" / "plugins" / "takumi_b40" / "rank_atlas.json"
CELL_SIZE = 160
COLUMNS = 18
RANK_CELL_SIZE = 512
RANK_COLUMNS = 5
RANK_RESOURCE_PATHS = {
    "rankimages/ranksplus": "S+",
    "rankimages/ranks": "S",
    "rankimages/rankaaa": "AAA",
    "rankimages/rankaa": "AA",
    "rankimages/ranka": "A",
    "rankimages/rankbbb": "BBB",
    "rankimages/rankbb": "BB",
    "rankimages/rankb": "B",
    "rankimages/rankc": "C",
    "rankimages/rankn": "N",
}


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
    rank_textures = {
        item.path_id: item
        for item in environment.objects
        if item.assets_file.name == "sharedassets0.assets"
        and item.type.name == "Texture2D"
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

    rank_path_ids = {}
    for name, pointer in container:
        normalized = _key(name)
        if normalized in RANK_RESOURCE_PATHS:
            rank_path_ids.setdefault(normalized, []).append(int(pointer["m_PathID"]))
    rank_images = []
    missing_ranks = []
    for resource_path, rank in RANK_RESOURCE_PATHS.items():
        texture = next(
            (
                rank_textures[path_id]
                for path_id in rank_path_ids.get(resource_path, [])
                if path_id in rank_textures
            ),
            None,
        )
        if texture is None:
            missing_ranks.append(rank)
            continue
        rank_images.append((rank, texture.read().image.convert("RGBA")))
    if missing_ranks:
        raise RuntimeError(
            "missing TAKUMI rank textures: " + ", ".join(missing_ranks)
        )

    rows = (len(extracted) + COLUMNS - 1) // COLUMNS
    atlas = Image.new("RGB", (COLUMNS * CELL_SIZE, rows * CELL_SIZE))
    index = {}
    for position, (song_id, artwork) in enumerate(extracted):
        column, row = position % COLUMNS, position // COLUMNS
        atlas.paste(artwork, (column * CELL_SIZE, row * CELL_SIZE))
        index[str(song_id)] = position

    rank_rows = (len(rank_images) + RANK_COLUMNS - 1) // RANK_COLUMNS
    rank_atlas = Image.new(
        "RGBA", (RANK_COLUMNS * RANK_CELL_SIZE, rank_rows * RANK_CELL_SIZE),
        (0, 0, 0, 0),
    )
    rank_index = {}
    for position, (rank, artwork) in enumerate(rank_images):
        column, row = position % RANK_COLUMNS, position // RANK_COLUMNS
        rank_atlas.paste(
            ImageOps.fit(
                artwork, (RANK_CELL_SIZE, RANK_CELL_SIZE),
                method=Image.Resampling.LANCZOS,
            ),
            (column * RANK_CELL_SIZE, row * RANK_CELL_SIZE),
        )
        rank_index[rank] = position

    atlas_temporary = ATLAS_PATH.with_suffix(".webp.tmp")
    index_temporary = INDEX_PATH.with_suffix(".json.tmp")
    rank_atlas_temporary = RANK_ATLAS_PATH.with_suffix(".webp.tmp")
    rank_index_temporary = RANK_INDEX_PATH.with_suffix(".json.tmp")
    atlas.save(atlas_temporary, "WEBP", quality=88, method=6)
    index_temporary.write_text(
        json.dumps(
            {"cell_size": CELL_SIZE, "columns": COLUMNS, "songs": index},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    rank_atlas.save(
        rank_atlas_temporary, "WEBP", lossless=True, method=6,
    )
    rank_index_temporary.write_text(
        json.dumps(
            {
                "cell_size": RANK_CELL_SIZE,
                "columns": RANK_COLUMNS,
                "ranks": rank_index,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    atlas_temporary.replace(ATLAS_PATH)
    index_temporary.replace(INDEX_PATH)
    rank_atlas_temporary.replace(RANK_ATLAS_PATH)
    rank_index_temporary.replace(RANK_INDEX_PATH)
    print(f"extracted {len(extracted)} jackets; missing {len(missing)}")
    print(f"extracted {len(rank_images)} rank textures; missing 0")
    for item in missing:
        print("missing", *item)


if __name__ == "__main__":
    main()
