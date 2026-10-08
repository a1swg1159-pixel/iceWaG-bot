"""Chunithm score-image renderers for B30, B50, and related commands."""

import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from PIL import Image, ImageDraw

from src import chunithm_render as chu_theme
from src.chunithm_assets import ARTWORK_CACHE, ASSETS_BASE_URLS, DOWNLOAD_POOL
from src.score_level_query import (
    ConstantRangeQuery, LevelQuery, matches_level_query,
    parse_constant_range_query, parse_level_query,
)


BASE_URL = "https://maimai.lxns.net"

# Image dimensions are owned by the shared CHUNITHM theme.
JACKET_SIZE = 185
COLOR_GOLD = chu_theme.PURPLE

# ====== 评级映射 ======
RANK_MAPPING = {
    "sp": "S+", "ssp": "SS+", "sssp": "SSS+",
    "d": "D", "c": "C", "b": "B", "bb": "BB", "bbb": "BBB",
    "a": "A", "aa": "AA", "aaa": "AAA",
    "s": "S", "ss": "SS", "sss": "SSS",
}
ALL_SONGS_CACHE: Dict[int, Dict[str, Any]] = {}
ALL_SONGS_LAST_ATTEMPT: Optional[float] = None
SONG_CACHE_RETRY_SECONDS = 300


def get_font(size: int):
    return chu_theme.font(size, "cjk")


def create_b30_style_image(
    player: "Player", scores: List["Score"], b30_rating: float,
    save_path: Path, bg_image_path: Optional[Path] = None,
) -> Path:
    prefetch_report_assets(player, scores[:30])
    sections = [chu_theme.ScoreSection("", scores[:30])]
    image = chu_theme.render_score_sheet(
        player, sections, "BEST 30", rating=b30_rating,
    )
    return chu_theme.save_report(image, save_path)


# API / data models

def build_headers(credential: str) -> Dict[str, str]:
    if not credential.lower().startswith("bearer "):
        raise ValueError("LXNS score APIs require an OAuth Bearer token")
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/",
        "Authorization": credential,
    }


def _player_api_url(suffix: str = "") -> str:
    return f"{BASE_URL}/api/v0/user/chunithm/player{suffix}"


def fetch_all_songs_data() -> bool:
    global ALL_SONGS_CACHE, ALL_SONGS_LAST_ATTEMPT
    if ALL_SONGS_CACHE:
        return True
    now = time.monotonic()
    if (ALL_SONGS_LAST_ATTEMPT is not None and
            now - ALL_SONGS_LAST_ATTEMPT < SONG_CACHE_RETRY_SECONDS):
        return False
    ALL_SONGS_LAST_ATTEMPT = now
    try:
        resp = requests.get(
            f"{BASE_URL}/api/v0/chunithm/song/list",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15, verify=False)
        resp.raise_for_status()
        payload = resp.json()
        songs = payload.get("songs", []) if isinstance(payload, dict) else []
        if not songs and isinstance(payload, dict):
            data = payload.get("data", {})
            songs = data.get("songs", []) if isinstance(data, dict) else []
        ALL_SONGS_CACHE = {
            int(song["id"]): song for song in songs
            if isinstance(song, dict) and "id" in song
        }
        return bool(ALL_SONGS_CACHE)
    except Exception:
        return False


def _resolve_song_level_value(
    song_id: int, level_index: int, fallback: str,
) -> Tuple[str, bool]:
    """Return (value, is_precise_constant) using the current LXNS song table."""
    if not ALL_SONGS_CACHE and not fetch_all_songs_data():
        return str(fallback or "?"), False
    song = ALL_SONGS_CACHE.get(song_id)
    if not song:
        return str(fallback or "?"), False
    difficulties = song.get("difficulties", [])
    if not isinstance(difficulties, list):
        return str(fallback or "?"), False
    for chart in difficulties:
        if not isinstance(chart, dict) or chart.get("difficulty") != level_index:
            continue
        level_value = chart.get("level_value")
        if isinstance(level_value, (int, float)):
            return f"{float(level_value):.1f}", True
        break
    return str(fallback or "?"), False


def get_song_level_value(song_id: int, level_index: int, fallback: str) -> str:
    return _resolve_song_level_value(song_id, level_index, fallback)[0]


class Player:
    def __init__(self, data: Dict[str, Any]):
        pd = data.get("data", data) if isinstance(data, dict) else {}
        self.name: str = pd.get("name", "Unknown")
        self.rating: float = pd.get("rating", 0.0)
        self.rating_floor = math.floor(self.rating * 100) / 100
        # Keep absent profile fields absent: never invent a level or an emblem.
        level = pd.get("level")
        self.level = level if type(level) is int and level >= 0 else None
        reborn = pd.get("reborn_count")
        self.reborn_count = reborn if type(reborn) is int and reborn >= 0 else None
        emblem = pd.get("class_emblem")
        self.class_emblem = {
            key: value for key, value in (emblem.items() if isinstance(emblem, dict) else [])
            if key in {"base", "medal"} and type(value) is int and 0 <= value <= 6
        }
        character = pd.get("character")
        self.character_id: Optional[int] = (
            character.get("id") if isinstance(character, dict) else
            character if isinstance(character, int) else None
        )
        self.character_name: str = (
            str(character.get("name", "")) if isinstance(character, dict) else ""
        )
        self.character_image: Optional[Image.Image] = None
        trophy = pd.get("trophy")
        self.trophy_id: Optional[int] = (
            trophy.get("id") if isinstance(trophy, dict) else
            trophy if isinstance(trophy, int) else None
        )
        self.trophy_name: str = (
            str(trophy.get("name", "")) if isinstance(trophy, dict) else ""
        )
        self.trophy_color: str = (
            str(trophy.get("color", "normal")).lower()
            if isinstance(trophy, dict) else "normal"
        )
        self.trophy_image: Optional[Image.Image] = None
        map_icon = pd.get("map_icon")
        self.map_icon_id: Optional[int] = (
            map_icon.get("id") if isinstance(map_icon, dict) else
            map_icon if isinstance(map_icon, int) else None
        )
        self.map_icon_image: Optional[Image.Image] = None
        name_plate = pd.get("name_plate")
        self.name_plate_id: Optional[int] = (
            name_plate.get("id") if isinstance(name_plate, dict) else
            name_plate if isinstance(name_plate, int) else None
        )
        self.name_plate_image: Optional[Image.Image] = None
        # Remember misses as well as successes within a report/request.
        self._collection_images: Dict[Tuple[str, int], Optional[Image.Image]] = {}

    def _load_collection_image(
        self, collection_type: str, collection_id: Optional[int], timeout: int = 5,
    ) -> Optional[Image.Image]:
        if (collection_type not in {"character", "trophy", "icon", "plate"}
                or not isinstance(collection_id, int) or isinstance(collection_id, bool)
                or collection_id < 0):
            return None
        key = (collection_type, collection_id)
        if key in self._collection_images:
            return self._collection_images[key]
        image = ARTWORK_CACHE.get(collection_type, collection_id, timeout=timeout)
        self._collection_images[key] = image
        return image

    def load_character_image(self) -> Optional[Image.Image]:
        if self.character_image is None:
            self.character_image = self._load_collection_image(
                "character", self.character_id
            )
        return self.character_image

    def load_trophy_image(self) -> Optional[Image.Image]:
        if self.trophy_image is None and self.trophy_color == "image":
            self.trophy_image = self._load_collection_image("trophy", self.trophy_id)
        return self.trophy_image

    def load_name_plate_image(self) -> Optional[Image.Image]:
        if self.name_plate_image is None:
            self.name_plate_image = self._load_collection_image("plate", self.name_plate_id)
        return self.name_plate_image

    def load_map_icon_image(self) -> Optional[Image.Image]:
        if self.map_icon_image is not None:
            return self.map_icon_image
        self.map_icon_image = self._load_collection_image("icon", self.map_icon_id)
        return self.map_icon_image


class Score:
    def __init__(self, data: Dict[str, Any]):
        self.id: int = data.get("id", 0)
        self.song_name: str = data.get("song_name", "Unknown")
        self.raw_level: str = data.get("level", "Unknown")
        self.level_index: int = data.get("level_index", -1)
        self.score: int = data.get("score", 0)
        self.rating: float = data.get("rating", 0.0)
        self.rating_floor = math.floor(self.rating * 100) / 100
        raw_rank = data.get("rank", "Unknown").lower()
        self.rank: str = RANK_MAPPING.get(raw_rank, raw_rank.upper())
        self.origin_id: int = data.get("origin_id", self.id)
        self.jacket_image: Optional[Image.Image] = None
        self.final_level, self.level_is_constant = _resolve_song_level_value(
            song_id=self.origin_id if self.level_index == 5 else self.id,
            level_index=self.level_index, fallback=self.raw_level,
        )

    def load_jacket_image(self) -> Image.Image:
        if self.jacket_image:
            return self.jacket_image
        jid = self.origin_id if self.level_index == 5 else self.id
        image = ARTWORK_CACHE.get("jacket", jid)
        if image is not None:
            self.jacket_image = image
            return image
        img = Image.new("RGBA", (JACKET_SIZE, JACKET_SIZE), (28, 31, 48, 255))
        d = ImageDraw.Draw(img)
        abbr = self.song_name[:2] if len(
            self.song_name) >= 2 else self.song_name
        f = get_font(20)
        bb = d.textbbox((0, 0), abbr, font=f)
        d.text(((JACKET_SIZE - bb[2]) // 2, (JACKET_SIZE - bb[3]) // 2),
               abbr, fill=COLOR_GOLD, font=f)
        self.jacket_image = img
        return img


def _prefetch_avatar(player: Player) -> None:
    if player.load_map_icon_image() is None:
        player.load_character_image()


def prefetch_report_assets(player: Player, scores: Sequence[Score]) -> None:
    """Warm only artwork the report needs, using a process-wide six-worker pool.

    Offline/demo objects keep their own loaders; production Score objects retain
    downloaded images for drawing, without keeping every player's data globally.
    """
    futures = []
    if isinstance(player, Player):
        futures.extend(DOWNLOAD_POOL.submit(loader) for loader in (
            player.load_name_plate_image, player.load_trophy_image,
            lambda: _prefetch_avatar(player),
        ))
    seen = set()
    for score in scores:
        if isinstance(score, Score) and id(score) not in seen:
            seen.add(id(score))
            futures.append(DOWNLOAD_POOL.submit(score.load_jacket_image))
    for future in futures:
        future.result()


def get_player_info(credential: str) -> Optional[Player]:
    try:
        resp = requests.get(
            _player_api_url(), headers=build_headers(credential), timeout=10,
        )
        resp.raise_for_status()
        return Player(resp.json())
    except Exception:
        return None


def get_player_scores(
    credential: str,
) -> Optional[List[Score]]:
    try:
        resp = requests.get(
            _player_api_url("/scores"), headers=build_headers(credential), timeout=15,
        )
        resp.raise_for_status()
        return [Score(item) for item in resp.json().get("data", [])[:30]]
    except Exception:
        return None


CLEANUP_MAX_AGE_SECONDS = 3600


def cleanup_old_images(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    now = time.time()
    for f in output_dir.iterdir():
        if f.is_file() and f.suffix == ".png":
            try:
                if now - f.stat().st_mtime > CLEANUP_MAX_AGE_SECONDS:
                    f.unlink()
            except Exception:
                pass


def generate_b30_image(
    credential: str, output_dir: Path, user_id: str,
) -> Optional[Path]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(credential)
    scores = get_player_scores(credential)
    if not player or not scores:
        return None
    total = sum(s.rating_floor for s in scores)
    b30 = math.floor(total * 100 / len(scores)) / 100
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"b30_{user_id}_{int(b30 * 100):04d}.png"
    return create_b30_style_image(player, scores, b30, save, bg)


# ===================================================================
#  推分
# ===================================================================

PUSH_W, PUSH_H = chu_theme.SINGLE_WIDTH, chu_theme.SINGLE_HEIGHT


def create_push_score_image(
    player: Player, score: Score, save_path: Path,
    bg_image_path: Optional[Path] = None, title: str = "随机推分",
) -> Path:
    prefetch_report_assets(player, [score])
    image = chu_theme.render_single_sheet(player, score, title)
    return chu_theme.save_report(image, save_path)

def get_all_player_scores(
    credential: str,
) -> Optional[List[Score]]:
    try:
        resp = requests.get(
            _player_api_url("/scores"),
            headers=build_headers(credential), timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("data", [])
        # Some LXNS score rows omit RT; the score itself is still usable.
        return [Score(item) for item in raw if "score" in item]
    except Exception:
        return None


def generate_push_score_image(
    credential: str, output_dir: Path, user_id: str,
) -> Optional[Tuple[Path, Score]]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(credential)
    scores = get_all_player_scores(credential)
    if not player or not scores:
        return None
    score = random.choice(scores)
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"push_{user_id}_{score.id}.png"
    return create_push_score_image(player, score, save, bg), score


# ===================================================================
#  B50
# ===================================================================

B50_W = chu_theme.SHEET_WIDTH
SCORE_LIST_PAGE_SIZE = 50

LEVEL_INDEX_FALLBACK = {
    "Basic": 0, "Advanced": 1, "Expert": 2, "Master": 3, "Ultima": 4,
    "basic": 0, "advanced": 1, "expert": 2, "master": 3, "ultima": 4,
    "BAS": 0, "ADV": 1, "EXP": 2, "MAS": 3, "ULT": 4,
}


def _infer_level_index(data: Dict[str, Any]) -> int:
    if "level_index" in data:
        return data["level_index"]
    label = data.get("level_label", "")
    if label in LEVEL_INDEX_FALLBACK:
        return LEVEL_INDEX_FALLBACK[label]
    raw = str(data.get("level", ""))
    if "+" in raw:
        try:
            if float(raw.replace("+", "")) >= 13:
                return 4
        except Exception:
            pass
    try:
        v = float(raw)
        if v <= 5:
            return 0
        if v <= 8:
            return 1
        if v <= 12:
            return 2
        return 3
    except Exception:
        return 3


def get_player_bests(
    credential: str,
) -> Optional[Dict[str, List[Score]]]:
    try:
        resp = requests.get(
            _player_api_url("/bests"),
            headers=build_headers(credential), timeout=15,
        )
        resp.raise_for_status()
        inner = resp.json().get("data", resp.json()) if isinstance(resp.json(), dict) else {}
        result: Dict[str, List[Score]] = {}
        for key in ("bests", "new_bests"):
            raw = inner.get(key, []) or []
            patched = []
            for item in raw:
                if "level_index" not in item:
                    item = dict(item)
                    item["level_index"] = _infer_level_index(item)
                patched.append(Score(item))
            result[key] = patched
        return result
    except Exception:
        return None


def create_b50_style_image(
    player: Player, old_scores: List[Score], new_scores: List[Score],
    b50_rating: float, save_path: Path, bg_image_path: Optional[Path] = None,
) -> Path:
    prefetch_report_assets(player, list(old_scores[:30]) + list(new_scores[:20]))
    sections = [
        chu_theme.ScoreSection("OLD BEST 30", old_scores[:30]),
        chu_theme.ScoreSection("NEW BEST 20", new_scores[:20], chu_theme.CYAN),
    ]
    image = chu_theme.render_score_sheet(
        player, sections, "BEST 50", rating=b50_rating,
    )
    return chu_theme.save_report(image, save_path)


def generate_b50_image(
    credential: str, output_dir: Path, user_id: str,
) -> Optional[Path]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(credential)
    bests = get_player_bests(credential)
    if not player or not bests:
        return None
    old = bests.get("bests", [])[:30]
    new = bests.get("new_bests", [])[:20]
    if not old and not new:
        return None
    # The player endpoint is authoritative for the displayed total Rating.
    # Re-averaging score rows can drift when the account has fewer than 50
    # records or when the game changes its Rating composition rules.
    b50 = player.rating_floor
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"b50_{user_id}_{int(b50 * 100):04d}.png"
    return create_b50_style_image(player, old, new, b50, save, bg)


def _chunithm_constant(score: Score) -> Optional[float]:
    if not score.level_is_constant:
        return None
    try:
        return float(score.final_level)
    except (TypeError, ValueError):
        return None


def _chunithm_score_matches(score: Score, query: LevelQuery) -> bool:
    return matches_level_query(
        query,
        display_level=score.raw_level,
        constant=_chunithm_constant(score),
        plus_threshold=0.5,
    )


def create_chunithm_score_list_images(
    player: Player,
    scores: List[Score],
    query: LevelQuery,
    output_dir: Path,
    user_id: str,
) -> List[Path]:
    if not scores:
        return []
    page_count = math.ceil(len(scores) / SCORE_LIST_PAGE_SIZE)
    timestamp = time.time_ns()
    paths = []
    for page_index in range(page_count):
        start = page_index * SCORE_LIST_PAGE_SIZE
        page_scores = scores[start:start + SCORE_LIST_PAGE_SIZE]
        prefetch_report_assets(player, page_scores)
        sections = [chu_theme.ScoreSection(
            f"{page_index + 1} / {page_count}" if page_count > 1 else "",
            page_scores, chu_theme.CYAN, start,
        )]
        image = chu_theme.render_score_sheet(
            player, sections, "SCORE LIST", label=query.label,
        )
        path = output_dir / (
            f"chu_score_{user_id}_{query.label.replace('+', 'p')}_"
            f"{timestamp}_{page_index + 1}.png"
        )
        paths.append(chu_theme.save_report(image, path))
    return paths


def generate_chunithm_score_list_images(
    credential: str, output_dir: Path, user_id: str, query_text: str,
) -> Optional[Tuple[List[Path], int]]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    query = parse_level_query(query_text)
    fetch_all_songs_data()
    player = get_player_info(credential)
    scores = get_all_player_scores(credential)
    if not player or scores is None:
        return None
    matched = [score for score in scores if _chunithm_score_matches(score, query)]
    matched.sort(
        key=lambda score: (
            _chunithm_constant(score) or 0.0,
            score.score,
            score.rating_floor,
        ),
        reverse=True,
    )
    if not matched:
        return [], 0
    return create_chunithm_score_list_images(
        player, matched, query, output_dir, user_id,
    ), len(matched)


# ===================================================================
#  装福
# ===================================================================

def generate_fu_image(
    credential: str, output_dir: Path, user_id: str,
) -> Optional[Tuple[Path, Score]]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(credential)
    scores = get_all_player_scores(credential)
    if not player or not scores:
        return None
    above = [s for s in scores if s.rating_floor > player.rating_floor]
    if not above:
        return None
    score = random.choice(above)
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"fu_{user_id}_{score.id}.png"
    return create_push_score_image(player, score, save, bg, title="装福"), score


# ===================================================================
#  Personal fitted constants
# ===================================================================

FIT_W = chu_theme.FIT_WIDTH
FIT_MAX_HEIGHT = min(28000, 50_000_000 // FIT_W - 56)


@dataclass(frozen=True)
class FitConstEntry:
    song_id: int
    title: str
    difficulty: int
    constant: float
    play: Score

    @property
    def fitted(self) -> float:
        return fit_chunithm_constant(self.constant, self.play.score)


def fit_chunithm_constant(constant: float, score: int) -> float:
    """Personal difficulty estimate; lower score implies a harder chart.

    The reference workbook has small per-chart adjustments, mostly within a
    few tenths. This monotone piecewise curve is deliberately conservative at
    SSS/SSS+ scores and capped for scores well below that band. It is not an
    official chart constant or a replacement for CHUNITHM rating.
    """
    if not math.isfinite(constant) or not 1 <= constant <= 20:
        raise ValueError("谱面定数无效")
    score = max(0, min(1_010_000, int(score)))
    anchors = (
        (0, 0.90), (975_000, 0.65), (990_000, 0.48),
        (1_000_000, 0.32), (1_005_000, 0.20),
        (1_007_000, 0.10), (1_008_500, 0.00),
        (1_009_500, -0.10), (1_010_000, -0.16),
    )
    for (left_score, left_delta), (right_score, right_delta) in zip(
        anchors, anchors[1:]
    ):
        if score <= right_score:
            portion = (score - left_score) / (right_score - left_score)
            adjusted = constant + left_delta + portion * (right_delta - left_delta)
            return round(max(1.0, min(20.0, adjusted)), 2)
    return round(max(1.0, min(20.0, constant - 0.16)), 2)


def build_fitconst_entries(
    songs: Sequence[Dict[str, Any]], scores: Sequence[Score],
    query: ConstantRangeQuery,
) -> List[FitConstEntry]:
    bests: Dict[Tuple[int, int], Score] = {}
    for score in scores:
        identity = (score.origin_id if score.level_index == 5 else score.id,
                    score.level_index)
        if identity not in bests or score.score > bests[identity].score:
            bests[identity] = score

    entries: List[FitConstEntry] = []
    seen = set()
    for song in songs:
        if not isinstance(song, dict):
            continue
        try:
            song_id = int(song["id"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(song.get("title") or song.get("name") or f"SONG {song_id}")
        for chart in song.get("difficulties", []):
            if not isinstance(chart, dict):
                continue
            try:
                difficulty = int(chart["difficulty"])
                constant = float(chart["level_value"])
            except (KeyError, TypeError, ValueError):
                continue
            tenth = round(constant * 10)
            identity = (song_id, difficulty)
            if (not math.isfinite(constant) or tenth < query.lower
                    or tenth > query.upper or identity in seen
                    or identity not in bests or bests[identity].score <= 0):
                continue
            seen.add(identity)
            entries.append(FitConstEntry(
                song_id, title, difficulty, constant, bests[identity],
            ))
    return entries


def _sorted_fit_entries(entries: Sequence[FitConstEntry]) -> List[FitConstEntry]:
    return sorted(entries, key=lambda entry: (
        -entry.fitted,
        -entry.constant,
        entry.title.casefold(), entry.difficulty,
    ))


def create_chunithm_fitconst_image(
    player: Player, entries: Sequence[FitConstEntry],
    query: ConstantRangeQuery, output_dir: Path, user_id: str,
) -> Path:
    ordered = _sorted_fit_entries(entries)
    if chu_theme.fit_sheet_height(len(ordered)) > FIT_MAX_HEIGHT:
        raise ValueError("范围内谱面太多，单张图片会超出安全尺寸，请缩小定数范围")
    prefetch_report_assets(player, [entry.play for entry in ordered])
    image = chu_theme.render_fit_sheet(player, ordered, query.label)
    safe_user = "".join(c for c in str(user_id) if c.isalnum()) or "user"
    path = output_dir / f"chu_fitconst_{safe_user}_{time.time_ns()}.png"
    return chu_theme.save_report(image, path)


def generate_chunithm_fitconst_image(
    credential: str, output_dir: Path, user_id: str, query_text: str,
) -> Optional[Tuple[Optional[Path], int]]:
    query = parse_constant_range_query(query_text)
    cleanup_old_images(output_dir)
    if not fetch_all_songs_data():
        return None
    player = get_player_info(credential)
    scores = get_all_player_scores(credential)
    if player is None or scores is None:
        return None
    entries = build_fitconst_entries(list(ALL_SONGS_CACHE.values()), scores, query)
    if not entries:
        return (None, 0)
    path = create_chunithm_fitconst_image(
        player, entries, query, output_dir, user_id,
    )
    return path, len(entries)
