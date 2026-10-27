"""Shared offline jacket atlas for score cards and equipped ``ja_`` avatars."""
from functools import lru_cache
import json
from pathlib import Path

from PIL import Image


ASSETS = Path(__file__).resolve().parent / "plugins/takumi_b40"


@lru_cache(maxsize=1)
def load_jacket_atlas():
    metadata = json.loads((ASSETS / "jacket_atlas.json").read_text(encoding="utf-8"))
    cell, columns, songs = int(metadata["cell_size"]), int(metadata["columns"]), metadata["songs"]
    if not 1 <= cell <= 512 or columns < 1 or not isinstance(songs, dict):
        raise ValueError("Invalid TAKUMI jacket atlas metadata")
    with Image.open(ASSETS / "jacket_atlas.webp") as source:
        atlas = source.convert("RGB")
    if atlas.width != columns * cell or atlas.height % cell:
        raise ValueError("Invalid TAKUMI jacket atlas dimensions")
    return atlas, songs, cell, columns


@lru_cache(maxsize=512)
def load_jacket_image(song_id):
    try:
        atlas, songs, cell, columns = load_jacket_atlas()
        position = songs[str(int(song_id))]
        if type(position) is not int or position < 0:
            return None
        left, top = position % columns * cell, position // columns * cell
        if top + cell > atlas.height:
            return None
        return atlas.crop((left, top, left + cell, top + cell))
    except (OSError, ValueError, KeyError, TypeError):
        return None


@lru_cache(maxsize=1)
def _jacket_avatar_ids():
    """Resolve game internal names through local metadata, never filesystem paths."""
    try:
        rows = json.loads((ASSETS / "song_catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, list):
        return {}
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, song_id = row.get("internal_name"), row.get("song_id")
        if isinstance(name, str) and name and type(song_id) is int and song_id > 0:
            result["ja_" + name] = song_id
    return result


def load_jacket_avatar(icon_id):
    song_id = _jacket_avatar_ids().get(icon_id)
    if song_id is None:
        return None
    jacket = load_jacket_image(song_id)
    # Return a fresh layer; player-specific rendering must not change cached art.
    return jacket.convert("RGBA") if jacket is not None else None
