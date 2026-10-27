"""TAKUMI equipped collections and public achievement metadata.

No credentials or player-owned data are written here. Only public title
definitions may be cached on disk; avatar IDs are resolved through an atlas
index rather than interpolated into filesystem paths or remote URLs.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
import csv
import io
import json
import logging
from pathlib import Path
import threading
import time

import requests
from PIL import Image

from src.takumi_jackets import load_jacket_avatar

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/plugins/takumi_b40/collection_assets"
ACHIEVEMENTS_PATH = ASSETS / "achievements.json"
RUNTIME_ACHIEVEMENTS_PATH = ROOT / "data/takumi_achievements.json"
ACHIEVEMENTS_URL = (
    "https://docs.google.com/spreadsheets/d/1cYF7RUax3XuRSgufHG02Fy-GgYzYWjAv/gviz/tq"
    "?tqx=out:csv&sheet=AchieveInfoAll"
)
COLLECTION_DATA_KEYS = ("0000_PlayerData", "0500_AchievementData", "0700_IconData")
MAX_METADATA_BYTES = 1024 * 1024
MAX_RECORDS = 5000
REFRESH_SECONDS = 6 * 3600
RETRY_SECONDS = 300
_TITLE_CACHE = None
_TITLE_REFRESH_AT = 0.0
_TITLE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlayerCollections:
    icon_id: str = ""
    achievement_id: str = ""
    dan_icon_id: str = ""
    owned_icon_ids: tuple[str, ...] = ()
    achieved_title_ids: tuple[str, ...] = ()
    title: str = ""
    title_rank: int | None = None


def _identifier(value):
    if not isinstance(value, str) or len(value) > 256:
        return ""
    value = value.strip()
    return value if all(ord(c) >= 32 for c in value) else ""


def _record(data, key, expected):
    if not isinstance(data, dict):
        return expected()
    item = data.get(key)
    value = item.get("Value") if isinstance(item, dict) else None
    if isinstance(value, str):
        if len(value) > MAX_METADATA_BYTES:
            return expected()
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return expected()
    return value if isinstance(value, expected) else expected()


def parse_collections(user_data):
    """Fields verified against the game service, with malformed-data isolation."""
    player = _record(user_data, COLLECTION_DATA_KEYS[0], dict)
    icons = _record(user_data, COLLECTION_DATA_KEYS[2], list)
    titles = _record(user_data, COLLECTION_DATA_KEYS[1], list)
    icon_ids = (_identifier(row.get("IconId")) for row in icons[:MAX_RECORDS] if isinstance(row, dict))
    title_ids = (_identifier(row.get("AchievementID")) for row in titles[:MAX_RECORDS]
                 if isinstance(row, dict) and row.get("IsAchieve") is True)
    return PlayerCollections(
        icon_id=_identifier(player.get("IconID")),
        achievement_id=_identifier(player.get("AchievementID")),
        dan_icon_id=_identifier(player.get("DanIconID")),
        owned_icon_ids=tuple(dict.fromkeys(i for i in icon_ids if i)),
        achieved_title_ids=tuple(dict.fromkeys(i for i in title_ids if i)),
    )


def validate_achievements(data):
    if not isinstance(data, dict) or not 1 <= len(data) <= MAX_RECORDS:
        raise ValueError("Invalid title catalog")
    result = {}
    for identity, value in data.items():
        if not _identifier(identity) or not isinstance(value, dict):
            continue
        title, rank = value.get("name"), value.get("rank")
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            continue
        if type(rank) is not int or not 0 <= rank <= 6:
            continue
        result[identity] = {"name": " ".join(title.split()), "rank": rank}
    if not result:
        raise ValueError("Empty title catalog")
    return result


def parse_achievement_csv(content):
    if len(content.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("Oversized title catalog")
    rows = csv.reader(io.StringIO(content.lstrip("\ufeff")))
    header = next(rows, [])
    if header[:3] != ["称号ID", "称号表示名", "ランク"]:
        raise ValueError("Unexpected achievement sheet")
    result = {}
    for number, row in enumerate(rows):
        if number >= MAX_RECORDS:
            raise ValueError("Too many titles")
        if len(row) < 3 or not row[0].strip():
            continue
        try:
            rank = int(row[2])
        except ValueError:
            continue
        result[row[0].strip()] = {"name": row[1], "rank": rank}
    return validate_achievements(result)


def _read_catalog(path):
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            return {}
        return validate_achievements(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def _save_public_catalog(data):
    path = RUNTIME_ACHIEVEMENTS_PATH
    temporary = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        logger.warning("TAKUMI public title catalog could not be cached")


def load_achievements(*, allow_network=False):
    """Renderer calls remain offline; the fetch path may refresh public data."""
    global _TITLE_CACHE, _TITLE_REFRESH_AT
    with _TITLE_LOCK:
        if _TITLE_CACHE is None:
            _TITLE_CACHE = _read_catalog(RUNTIME_ACHIEVEMENTS_PATH) or _read_catalog(ACHIEVEMENTS_PATH)
        if not allow_network or time.monotonic() < _TITLE_REFRESH_AT:
            return dict(_TITLE_CACHE)
        # Timeouts/errors never prevent the account's scores from rendering.
        _TITLE_REFRESH_AT = time.monotonic() + RETRY_SECONDS
        try:
            response = requests.get(ACHIEVEMENTS_URL, timeout=(3, 6), stream=True)
            try:
                response.raise_for_status()
                content = bytearray()
                for block in response.iter_content(65536):
                    content.extend(block)
                    if len(content) > MAX_METADATA_BYTES:
                        raise ValueError("Oversized title response")
                catalog = parse_achievement_csv(content.decode("utf-8-sig"))
            finally:
                response.close()
        except (requests.RequestException, ValueError, UnicodeError):
            logger.warning("TAKUMI public title refresh failed; using local definitions")
        else:
            _TITLE_CACHE = catalog
            _TITLE_REFRESH_AT = time.monotonic() + REFRESH_SECONDS
            _save_public_catalog(catalog)
        return dict(_TITLE_CACHE)


def resolve_collections(collections, catalog=None):
    titles = catalog if catalog is not None else load_achievements()
    title = titles.get(collections.achievement_id)
    if not isinstance(title, dict):
        return replace(collections, title="", title_rank=None)
    return replace(collections, title=title["name"], title_rank=title["rank"])


@lru_cache(maxsize=1)
def _icon_atlas():
    try:
        index = json.loads((ASSETS / "icons.json").read_text(encoding="utf-8"))
        with Image.open(ASSETS / "icons.webp") as source:
            image = source.convert("RGBA")
        size, columns = int(index["cell_size"]), int(index["columns"])
        if not 1 <= size <= 512 or columns < 1 or columns * size != image.width:
            return None
        return image, index["icons"], size, columns
    except (OSError, ValueError, TypeError, KeyError):
        return None


def load_icon(icon_id):
    if not isinstance(icon_id, str):
        return None
    if icon_id.startswith("ja_"):
        return load_jacket_avatar(icon_id)
    atlas = _icon_atlas()
    if atlas is None:
        return None
    image, icons, cell, columns = atlas
    index = icons.get(icon_id)
    if type(index) is not int or index < 0:
        return None
    x, y = index % columns * cell, index // columns * cell
    if y + cell > image.height:
        return None
    # A fresh crop prevents one player's modifications entering shared artwork.
    return image.crop((x, y, x + cell, y + cell))
