"""LXNS OAuth-backed maimai DX Best 50 renderer."""

import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageOps

from src import maimai_render as mai_theme
from src.maimai_assets import ARTWORK_CACHE, DOWNLOAD_POOL
from src.score_level_query import LevelQuery, matches_level_query, parse_level_query

BASE_URL = "https://maimai.lxns.net"
PLAYER_URL = f"{BASE_URL}/api/v0/user/maimai/player"
BESTS_URL = f"{PLAYER_URL}/bests"
SCORES_URL = f"{PLAYER_URL}/scores"
SONGS_URL = f"{BASE_URL}/api/v0/maimai/song/list"

CANVAS_W, CANVAS_H = mai_theme.W, 3054
HEADER_H, SECTION_HEADER_H = mai_theme.HEADER, mai_theme.SECTION_H
CARD_W, CARD_H = mai_theme.CW, mai_theme.CH
CARD_GAP_X = CARD_GAP_Y = mai_theme.GAP
CARD_START_X = mai_theme.LEFT
SCORE_LIST_PAGE_SIZE = 50

RANKS = {
    "d": "D", "c": "C", "b": "B", "bb": "BB", "bbb": "BBB",
    "a": "A", "aa": "AA", "aaa": "AAA", "s": "S", "sp": "S+",
    "ss": "SS", "ssp": "SS+", "sss": "SSS", "sssp": "SSS+",
}

_SONG_CACHE: Dict[int, Dict[str, Any]] = {}


def cleanup_old_images(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    now = time.time()
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg"):
            try:
                if now - path.stat().st_mtime > 3600:
                    path.unlink()
            except OSError:
                pass


def _headers(credential: str) -> Dict[str, str]:
    if not credential.lower().startswith("bearer "):
        raise ValueError("maimai score APIs require an OAuth Bearer token")
    return {
        "Authorization": credential,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{BASE_URL}/",
    }


def _unwrap(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {}


def _song_id(value: Any) -> int:
    try:
        result = int(value)
        return result if result >= 100000 else result % 10000
    except (TypeError, ValueError):
        return 0


def _load_song_catalog() -> Dict[int, Dict[str, Any]]:
    global _SONG_CACHE
    if _SONG_CACHE:
        return _SONG_CACHE
    try:
        response = requests.get(
            SONGS_URL,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        songs = payload.get("songs", []) if isinstance(payload, dict) else []
        if not songs and isinstance(payload, dict):
            songs = _unwrap(payload).get("songs", [])
        _SONG_CACHE = {
            _song_id(song.get("id")): song
            for song in songs
            if isinstance(song, dict) and song.get("id") is not None
        }
    except Exception:
        pass
    return _SONG_CACHE


def _chart_metadata(
    song: Dict[str, Any], song_type: str, level_index: int,
) -> Dict[str, Any]:
    difficulties = song.get("difficulties", {})
    if isinstance(difficulties, dict):
        keys = ("dx", "DX") if song_type == "dx" else ("standard", "standard_legacy", "SD")
        charts: Any = []
        for key in keys:
            if isinstance(difficulties.get(key), list):
                charts = difficulties[key]
                break
    elif isinstance(difficulties, list):
        charts = difficulties
    else:
        charts = []
    for position, chart in enumerate(charts):
        if not isinstance(chart, dict):
            continue
        difficulty = chart.get("difficulty", chart.get("level_index", position))
        try:
            if int(difficulty) == level_index:
                return chart
        except (TypeError, ValueError):
            if position == level_index:
                return chart
    return {}


def _calculate_dx_rating(level_value: float, achievements: float) -> int:
    thresholds = (
        (100.5, 22.4), (100.0, 21.6), (99.5, 21.1), (99.0, 20.8),
        (98.0, 20.3), (97.0, 20.0), (94.0, 16.8), (90.0, 15.2),
        (80.0, 13.6), (75.0, 12.0), (70.0, 11.2), (60.0, 9.6),
        (50.0, 8.0), (0.0, 0.0),
    )
    coefficient = next(value for floor, value in thresholds if achievements >= floor)
    return math.floor(level_value * min(achievements, 100.5) * coefficient / 100)


def _collection_id(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        value = value.get("id")
    try:
        result = int(value)
        return result if 0 <= result <= 10**10 else None
    except (ValueError, TypeError, OverflowError):
        return None


class MaimaiPlayer:
    def __init__(self, payload: Dict[str, Any]):
        data = _unwrap(payload)
        self.name = str(data.get("name", "Unknown"))
        self.rating = int(data.get("rating", 0) or 0)
        trophy = data.get("trophy") or {}
        self.trophy = str(trophy.get("name") or "") if isinstance(trophy, dict) else ""
        self.icon_id = _collection_id(data.get("icon"))
        self.name_plate_id = _collection_id(data.get("name_plate"))
        self.icon_image: Optional[Image.Image] = None
        self.name_plate_image: Optional[Image.Image] = None
        self._icon_loaded = False
        self._plate_loaded = False

    def load_icon(self) -> Optional[Image.Image]:
        if self.icon_image is None and not self._icon_loaded:
            self.icon_image = ARTWORK_CACHE.get("icon", self.icon_id)
            self._icon_loaded = True
        return self.icon_image

    def load_name_plate_image(self) -> Optional[Image.Image]:
        if self.name_plate_image is None and not self._plate_loaded:
            self.name_plate_image = ARTWORK_CACHE.get("plate", self.name_plate_id)
            self._plate_loaded = True
        return self.name_plate_image


class MaimaiScore:
    def __init__(self, data: Dict[str, Any], catalog: Dict[int, Dict[str, Any]]):
        self.id = _song_id(data.get("id", data.get("song_id", 0)))
        self.song_type = str(data.get("type", data.get("song_type", "standard"))).lower()
        self.song_type = "dx" if self.song_type in ("dx", "deluxe") else "standard"
        self.level_index = int(data.get("level_index", data.get("difficulty", 3)) or 0)
        song = catalog.get(self.id, {})
        chart = _chart_metadata(song, self.song_type, self.level_index)
        self.song_name = str(
            data.get("song_name") or data.get("title") or song.get("title") or f"TRACK {self.id}"
        )
        raw_level = (
            data.get("level_value") or data.get("constant") or data.get("ds")
            or chart.get("level_value") or chart.get("constant") or chart.get("ds")
        )
        self.display_level = str(data.get("level") or chart.get("level") or "").strip()
        try:
            self.level_value = float(raw_level)
            self.level = f"{self.level_value:.1f}"
        except (TypeError, ValueError):
            self.level_value = 0.0
            self.level = str(data.get("level") or chart.get("level") or "?")
        self.achievements = float(data.get("achievements", 0) or 0)
        rank_key = str(data.get("rate", data.get("rank", ""))).lower()
        self.rank = RANKS.get(rank_key, rank_key.upper() or "-")
        raw_rating = data.get("dx_rating", data.get("rating"))
        try:
            self.dx_rating = int(raw_rating)
        except (TypeError, ValueError):
            self.dx_rating = _calculate_dx_rating(self.level_value, self.achievements)
        self.fc = str(data.get("fc") or "").upper()
        self.fs = str(data.get("fs") or "").upper()
        self.jacket_image: Optional[Image.Image] = None
        self._jacket_thumbnails: Dict[int, Image.Image] = {}

    def load_jacket(self) -> Image.Image:
        if self.jacket_image is None:
            self.jacket_image = ARTWORK_CACHE.get("jacket", self.id)
        if self.jacket_image is None:
            # A local neutral placeholder is not stored as successful artwork.
            image = Image.new("RGBA", (184, 184), (28, 31, 48, 255))
            ImageDraw.Draw(image).text((92, 92), str(self.id),
                                      font=mai_theme.font(25, "ui"),
                                      fill="#ffdfa0", anchor="mm")
            self.jacket_image = image
        return self.jacket_image

    def load_jacket_thumbnail(self, size: int) -> Image.Image:
        if size not in self._jacket_thumbnails:
            image = None
            if self.jacket_image is None:
                image = ARTWORK_CACHE.thumbnail("jacket", self.id, size)
            if image is None:
                image = ImageOps.fit(self.load_jacket(), (size, size),
                                     method=mai_theme.LANCZOS)
            self._jacket_thumbnails[size] = image
        return self._jacket_thumbnails[size]


def prefetch_report_assets(player: MaimaiPlayer, scores: List[MaimaiScore]) -> None:
    """Warm public artwork concurrently; never cache player data or OAuth."""
    futures = []
    if isinstance(player, MaimaiPlayer):
        futures.extend(DOWNLOAD_POOL.submit(loader) for loader in (
            player.load_icon, player.load_name_plate_image,
        ))
    seen = set()
    for score in scores:
        if isinstance(score, MaimaiScore) and id(score) not in seen:
            seen.add(id(score))
            futures.append(DOWNLOAD_POOL.submit(score.load_jacket_thumbnail, 184))
    for future in futures:
        future.result()


def get_maimai_player(credential: str) -> Optional[MaimaiPlayer]:
    try:
        response = requests.get(PLAYER_URL, headers=_headers(credential), timeout=12)
        response.raise_for_status()
        return MaimaiPlayer(response.json())
    except Exception:
        return None


def get_maimai_bests(
    credential: str,
) -> Optional[Tuple[List[MaimaiScore], List[MaimaiScore], int, int]]:
    try:
        response = requests.get(BESTS_URL, headers=_headers(credential), timeout=18)
        response.raise_for_status()
        data = _unwrap(response.json())
        catalog = _load_song_catalog()
        standard = [MaimaiScore(item, catalog) for item in (data.get("standard") or [])[:35]]
        current = [MaimaiScore(item, catalog) for item in (data.get("dx") or [])[:15]]
        standard_total = int(data.get("standard_total") or sum(x.dx_rating for x in standard))
        dx_total = int(data.get("dx_total") or sum(x.dx_rating for x in current))
        return standard, current, standard_total, dx_total
    except Exception:
        return None


def get_all_maimai_scores(credential: str) -> Optional[List[MaimaiScore]]:
    """Fetch the authorized player's complete best-score cache from LXNS."""
    try:
        response = requests.get(SCORES_URL, headers=_headers(credential), timeout=25)
        response.raise_for_status()
        payload: Any = response.json()
        rows: Any = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(rows, dict):
            rows = rows.get("scores", [])
        if not isinstance(rows, list):
            return None
        catalog = _load_song_catalog()
        return [MaimaiScore(item, catalog) for item in rows if isinstance(item, dict)]
    except Exception:
        return None


def create_maimai_b50_image(
    player: MaimaiPlayer,
    standard: List[MaimaiScore],
    current: List[MaimaiScore],
    standard_total: int,
    dx_total: int,
    save_path: Path,
) -> Path:
    # Keep total arguments for API compatibility; the approved UI omits stats.
    prefetch_report_assets(player, list(standard[:35]) + list(current[:15]))
    canvas = mai_theme.b50(player, standard, current)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(save_path, compress_level=3)
    return save_path


def generate_maimai_b50_image(
    credential: str, output_dir: Path, user_id: str,
) -> Optional[Path]:
    cleanup_old_images(output_dir)
    player = get_maimai_player(credential)
    bests = get_maimai_bests(credential)
    if not player or not bests:
        return None
    standard, current, standard_total, dx_total = bests
    if not standard and not current:
        return None
    save_path = output_dir / f"mai_b50_{user_id}_{int(time.time())}.png"
    return create_maimai_b50_image(
        player, standard, current, standard_total, dx_total, save_path
    )


def _maimai_score_matches(score: MaimaiScore, query: LevelQuery) -> bool:
    return matches_level_query(
        query,
        display_level=score.display_level,
        constant=score.level_value if score.level_value > 0 else None,
        plus_threshold=0.7,
    )


def create_maimai_score_list_images(
    player: MaimaiPlayer,
    scores: List[MaimaiScore],
    query: LevelQuery,
    output_dir: Path,
    user_id: str,
) -> List[Path]:
    """Render matched records as one or more 50-chart MAGiCAL sheets."""
    page_count = max(1, math.ceil(len(scores) / SCORE_LIST_PAGE_SIZE))
    timestamp = time.time_ns()
    paths: List[Path] = []
    for page_index in range(page_count):
        offset = page_index * SCORE_LIST_PAGE_SIZE
        page_scores = scores[offset:offset + SCORE_LIST_PAGE_SIZE]
        prefetch_report_assets(player, page_scores)
        title = f"Lv. {query.label}"
        if page_count > 1:
            title += f"  {page_index + 1}/{page_count}"
        canvas = mai_theme.score_list(player, page_scores, title, index_offset=offset)
        path = output_dir / (
            f"mai_score_{user_id}_{query.label.replace('+', 'p')}_"
            f"{timestamp}_{page_index + 1}.png"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(path, compress_level=3)
        paths.append(path)
    return paths


def generate_maimai_score_list_images(
    credential: str, output_dir: Path, user_id: str, query_text: str,
) -> Optional[Tuple[List[Path], int]]:
    cleanup_old_images(output_dir)
    query = parse_level_query(query_text)
    player = get_maimai_player(credential)
    scores = get_all_maimai_scores(credential)
    if not player or scores is None:
        return None
    matched = [score for score in scores if _maimai_score_matches(score, query)]
    matched.sort(
        key=lambda score: (score.level_value, score.achievements, score.dx_rating),
        reverse=True,
    )
    if not matched:
        return [], 0
    return create_maimai_score_list_images(
        player, matched, query, output_dir, user_id,
    ), len(matched)
