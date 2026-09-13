"""Direct TAKUMI³ account fetcher, rating calculator, and Best 40 renderer."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

from src.image_credit import append_image_credit


PLAYFAB_TITLE_ID = "8372D"
PLAYFAB_BASE_URL = f"https://{PLAYFAB_TITLE_ID}.playfabapi.com/Client"
SONG_CATALOG_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1cYF7RUax3XuRSgufHG02Fy-GgYzYWjAv/gviz/tq"
    "?tqx=out:csv&sheet=SongsInfo"
)
CHARTS_PATH = Path(__file__).with_name("charts.json")
SONG_CATALOG_PATH = Path(__file__).with_name("song_catalog.json")
JACKET_ATLAS_PATH = Path(__file__).with_name("jacket_atlas.webp")
JACKET_INDEX_PATH = Path(__file__).with_name("jacket_atlas.json")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 512 * 1024
MAX_SCORE_ROWS = 5000
MAX_TITLE_LENGTH = 200
MAX_DIFFICULTY_LENGTH = 20
MAX_LEVEL_LENGTH = 10
SCORE_CACHE_SECONDS = 60
CATALOG_CACHE_SECONDS = 6 * 3600
OUTPUT_MAX_AGE_SECONDS = 3600

REGULAR_SCORE_KEYS = {
    0: "0200_SongScore_Regular_Normal",
    1: "0201_SongScore_Regular_Hard",
    2: "0202_SongScore_Regular_Master",
    3: "0203_SongScore_Regular_Insanity",
}

CANVAS_W = 3000
CANVAS_H = 2500
MARGIN = 24
HEADER_H = 270
PANEL_Y = 290
SECTION_H = 100
CARD_START_X = 40
CARD_W = 568
CARD_H = 236
CARD_GAP_X = 22
CARD_GAP_Y = 22
CARD_COLUMNS = 5

INK = (14, 24, 38)
INK_2 = (21, 40, 59)
PAPER = (239, 238, 229)
PAPER_2 = (222, 224, 218)
CYAN = (0, 216, 224)
CYAN_DARK = (0, 128, 150)
MAGENTA = (214, 61, 132)
MUTED = (126, 145, 157)

DIFFICULTY_META = {
    "NORMAL": ("NOR", (35, 151, 205)),
    "HARD": ("HAR", (225, 156, 39)),
    "MASTER": ("MAS", (164, 67, 194)),
    "INSANITY": ("INS", (74, 84, 101)),
    "RAVAGE": ("RAV", (220, 57, 69)),
}


class TakumiError(RuntimeError):
    """Base class for user-facing TAKUMI errors."""


class TakumiFetchError(TakumiError):
    """The score service could not be reached or returned invalid data."""


class TakumiNoScoresError(TakumiError):
    """The authenticated account did not return score rows."""


class TakumiChartDataError(TakumiError):
    """The bundled chart constant table is missing or malformed."""


class TakumiAuthError(TakumiError):
    """The supplied TAKUMI³ account credentials were rejected."""


@dataclass(frozen=True)
class PlayFabSession:
    session_ticket: str
    playfab_id: str
    display_name: str = ""


@dataclass(frozen=True)
class AccountLink:
    custom_id: str
    playfab_id: str
    display_name: str


@dataclass(frozen=True)
class CatalogChart:
    song_id: int
    title: str
    difficulty: str
    level: str


@dataclass(frozen=True)
class GameScore:
    song_id: int
    title: str
    difficulty: str
    level: str
    score: int


@dataclass(frozen=True)
class BestScore:
    chart_id: str
    title: str
    difficulty: str
    constant: float
    score: int
    contribution: float
    single_rating: float
    rank: str
    song_id: int = 0


@dataclass(frozen=True)
class B40Result:
    scores: Tuple[BestScore, ...]
    rating: float
    source_score_count: int
    matched_chart_count: int
    unmatched_row_count: int
    fetched_at: float

    @property
    def is_complete(self) -> bool:
        return len(self.scores) >= 40


_CACHE_LOCK = threading.Lock()
_RESULT_CACHE: Dict[str, Tuple[float, B40Result]] = {}
_CATALOG_CACHE: Optional[Tuple[float, Tuple[CatalogChart, ...]]] = None


def validate_email(value: str) -> str:
    email = str(value or "").strip()
    if not email or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
        raise TakumiAuthError("邮箱格式不正确。")
    return email


def validate_password(value: str) -> str:
    password = str(value or "")
    if not password or len(password) > 256 or any(ord(char) < 32 for char in password):
        raise TakumiAuthError("密码格式不正确。")
    return password


def _normalize_field(value: str) -> str:
    return (
        str(value or "").strip()
        .replace("~", "～")
        .replace("\\", "＼")
        .replace(",", "，")
    )


def _playfab_call(endpoint: str, payload: dict, session_ticket: str = "") -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "TakumiB40Bot/2.0",
    }
    if session_ticket:
        headers["X-Authorization"] = session_ticket
    try:
        response = requests.post(
            f"{PLAYFAB_BASE_URL}/{endpoint}", json=payload, headers=headers,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise TakumiFetchError("连接 TAKUMI³ 账号服务器失败，请稍后重试。") from exc
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise TakumiFetchError("TAKUMI³ 账号服务器返回的数据异常（过大）。")
    try:
        body = response.json()
    except ValueError as exc:
        raise TakumiFetchError("TAKUMI³ 账号服务器返回了无法解析的数据。") from exc
    if not isinstance(body, dict):
        raise TakumiFetchError("TAKUMI³ 账号服务器返回的数据格式异常。")
    if response.status_code >= 400 or body.get("error"):
        error = str(body.get("error") or "")
        message = str(body.get("errorMessage") or "")
        auth_errors = {
            "InvalidEmailOrPassword", "InvalidEmailAddress", "InvalidParams",
            "AccountNotFound", "NotAuthenticated", "InvalidUsernameOrPassword",
        }
        if error in auth_errors and endpoint.startswith("LoginWith"):
            raise TakumiAuthError("邮箱或密码错误，或账号尚未完成注册。")
        detail = error or message or f"HTTP {response.status_code}"
        raise TakumiFetchError(f"TAKUMI³ 账号服务器拒绝了请求（{detail}）。")
    data = body.get("data")
    if not isinstance(data, dict):
        raise TakumiFetchError("TAKUMI³ 账号服务器没有返回有效数据。")
    return data


def _profile_name(data: dict) -> str:
    info = data.get("InfoResultPayload")
    profile = info.get("PlayerProfile") if isinstance(info, dict) else None
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("DisplayName") or "").strip()[:80]


def login_with_email(email: str, password: str) -> PlayFabSession:
    data = _playfab_call("LoginWithEmailAddress", {
        "TitleId": PLAYFAB_TITLE_ID,
        "Email": validate_email(email),
        "Password": validate_password(password),
        "InfoRequestParameters": {"GetPlayerProfile": True},
    })
    ticket = str(data.get("SessionTicket") or "")
    playfab_id = str(data.get("PlayFabId") or "")
    if not ticket or not playfab_id:
        raise TakumiFetchError("登录成功，但账号服务器没有返回完整会话。")
    return PlayFabSession(ticket, playfab_id, _profile_name(data))


def login_with_custom_id(custom_id: str) -> PlayFabSession:
    value = str(custom_id or "").strip()
    if not value or len(value) > 100:
        raise TakumiAuthError("本地绑定信息已损坏，请重新绑定。")
    data = _playfab_call("LoginWithCustomID", {
        "TitleId": PLAYFAB_TITLE_ID,
        "CustomId": value,
        "CreateAccount": False,
        "InfoRequestParameters": {"GetPlayerProfile": True},
    })
    ticket = str(data.get("SessionTicket") or "")
    playfab_id = str(data.get("PlayFabId") or "")
    if not ticket or not playfab_id:
        raise TakumiFetchError("TAKUMI³ 绑定登录没有返回完整会话，请重新绑定。")
    return PlayFabSession(ticket, playfab_id, _profile_name(data))


def link_account(email: str, password: str) -> AccountLink:
    """Log in once, link a random bot credential, and discard the password."""
    session = login_with_email(email, password)
    for _ in range(3):
        custom_id = "t3bot_" + secrets.token_urlsafe(32)
        try:
            _playfab_call(
                "LinkCustomID", {"CustomId": custom_id, "ForceLink": False},
                session.session_ticket,
            )
        except TakumiFetchError as exc:
            if "LinkedIdentifierAlreadyClaimed" in str(exc):
                continue
            raise
        return AccountLink(custom_id, session.playfab_id, session.display_name)
    raise TakumiFetchError("生成绑定凭据失败，请重试。")


def unlink_account(custom_id: str) -> None:
    session = login_with_custom_id(custom_id)
    _playfab_call(
        "UnlinkCustomID", {"CustomId": custom_id}, session.session_ticket
    )


def fetch_user_data(session_ticket: str) -> dict:
    data = _playfab_call(
        "GetUserData", {"Keys": list(REGULAR_SCORE_KEYS.values())},
        session_ticket,
    )
    user_data = data.get("Data")
    return user_data if isinstance(user_data, dict) else {}


@lru_cache(maxsize=1)
def load_chart_table() -> Tuple[dict, ...]:
    try:
        payload = json.loads(CHARTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TakumiChartDataError("TAKUMI 谱面定数表读取失败。") from exc
    if not isinstance(payload, list) or not payload:
        raise TakumiChartDataError("TAKUMI 谱面定数表为空。")
    return tuple(item for item in payload if isinstance(item, dict))


def _visible_level_from_sheet(value: str) -> Optional[str]:
    try:
        encoded = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if encoded < 10 or encoded >= 9999:
        return None
    return _visible_level(encoded / 10)


def _catalog_from_rows(rows: Sequence[dict]) -> Tuple[CatalogChart, ...]:
    charts: List[CatalogChart] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            song_id = int(row["song_id"])
        except (KeyError, TypeError, ValueError):
            continue
        title = _normalize_field(str(row.get("title") or ""))
        levels = row.get("levels")
        if not title or not isinstance(levels, list) or len(levels) < 4:
            continue
        fourth = (
            "RAVAGE" if "rav" in str(row.get("special") or "").lower()
            else "INSANITY"
        )
        for index, difficulty in enumerate(("NORMAL", "HARD", "MASTER", fourth)):
            level = _visible_level_from_sheet(levels[index])
            if level:
                charts.append(CatalogChart(song_id, title, difficulty, level))
    if not charts:
        raise TakumiChartDataError("TAKUMI³ 歌曲 ID 表为空。")
    return tuple(charts)


def _catalog_from_csv(content: str) -> Tuple[CatalogChart, ...]:
    try:
        rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
    except csv.Error as exc:
        raise TakumiChartDataError("TAKUMI³ 在线歌曲 ID 表格式异常。") from exc
    payload = []
    for row in rows[1:1001]:
        if len(row) < 18:
            continue
        try:
            song_id = int(row[15])
        except (TypeError, ValueError):
            continue
        payload.append({
            "song_id": song_id,
            "title": row[17] or row[0],
            "levels": row[7:11],
            "special": row[11],
        })
    return _catalog_from_rows(payload)


def _load_bundled_catalog() -> Tuple[CatalogChart, ...]:
    try:
        payload = json.loads(SONG_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TakumiChartDataError("TAKUMI³ 本地歌曲 ID 表读取失败。") from exc
    if not isinstance(payload, list):
        raise TakumiChartDataError("TAKUMI³ 本地歌曲 ID 表格式异常。")
    return _catalog_from_rows(payload)


def load_song_catalog(force_refresh: bool = False) -> Tuple[CatalogChart, ...]:
    """Read the live catalog used by the game, with a bundled offline fallback."""
    global _CATALOG_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force_refresh and _CATALOG_CACHE
            and now - _CATALOG_CACHE[0] < CATALOG_CACHE_SECONDS
        ):
            return _CATALOG_CACHE[1]
    catalog: Optional[Tuple[CatalogChart, ...]] = None
    try:
        response = requests.get(
            SONG_CATALOG_URL,
            headers={"Accept": "text/csv", "User-Agent": "TakumiB40Bot/2.0"},
            timeout=15,
        )
        response.raise_for_status()
        if len(response.content) <= MAX_CATALOG_BYTES:
            catalog = _catalog_from_csv(
                response.content.decode("utf-8-sig", errors="strict")
            )
    except (requests.RequestException, UnicodeError, TakumiChartDataError):
        catalog = None
    if catalog is None:
        catalog = _load_bundled_catalog()
    with _CACHE_LOCK:
        _CATALOG_CACHE = (time.monotonic(), catalog)
    return catalog


def _unwrap_score_payload(value: object) -> List[dict]:
    current = value
    for _ in range(4):
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except json.JSONDecodeError:
                return []
            continue
        if isinstance(current, list):
            return [item for item in current if isinstance(item, dict)]
        if isinstance(current, dict):
            for key in ("Value", "value", "Data", "data", "Items", "items"):
                if key in current:
                    current = current[key]
                    break
            else:
                # Some serializers wrap the list in the PlayFab data key itself.
                nested = next(
                    (item for item in current.values() if isinstance(item, list)), None
                )
                if nested is None:
                    return []
                current = nested
            continue
        return []
    return []


def parse_playfab_scores(user_data: dict) -> Dict[Tuple[int, int], int]:
    """Parse the four regular-play score shards stored by the game."""
    if not isinstance(user_data, dict):
        raise TakumiFetchError("账号成绩数据格式异常。")
    best: Dict[Tuple[int, int], int] = {}
    row_count = 0
    for difficulty_index, key in REGULAR_SCORE_KEYS.items():
        record = user_data.get(key)
        for item in _unwrap_score_payload(record):
            lowered = {str(name).lower(): value for name, value in item.items()}
            try:
                song_id = int(lowered["songid"])
                score = int(lowered["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if song_id < 0 or not 0 <= score <= 1_010_000:
                continue
            row_count += 1
            if row_count > MAX_SCORE_ROWS:
                raise TakumiFetchError("账号成绩条目数量异常。")
            identity = (song_id, difficulty_index)
            best[identity] = max(score, best.get(identity, -1))
    if not best:
        raise TakumiNoScoresError("账号中没有可用的普通模式成绩。")
    return best


def resolve_game_scores(
    raw_scores: Dict[Tuple[int, int], int],
    catalog: Sequence[CatalogChart],
) -> Tuple[List[GameScore], int]:
    difficulty_indexes = {
        "NORMAL": 0, "HARD": 1, "MASTER": 2, "INSANITY": 3, "RAVAGE": 3,
    }
    by_identity = {
        (item.song_id, difficulty_indexes[item.difficulty]): item
        for item in catalog if item.difficulty in difficulty_indexes
    }
    resolved: List[GameScore] = []
    unknown = 0
    for (song_id, difficulty_index), score in raw_scores.items():
        item = by_identity.get((song_id, difficulty_index))
        if item is None:
            unknown += 1
            continue
        resolved.append(GameScore(
            song_id, item.title, item.difficulty, item.level, score
        ))
    return resolved, unknown


def score_rank(score: int) -> str:
    if score >= 995_000:
        return "S+"
    if score >= 990_000:
        return "S"
    if score >= 970_000:
        return "AAA"
    if score >= 950_000:
        return "AA"
    if score >= 900_000:
        return "A"
    if score >= 850_000:
        return "BBB"
    if score >= 800_000:
        return "BB"
    if score >= 700_000:
        return "B"
    return "C"


def song_contribution(score: int, chart_constant: float) -> float:
    """Return the unrounded contribution used in the overall rating sum."""
    # Special modes can display 1,010,000, but the normal rating formula caps
    # at the 1,000,000 theoretical value (bonus 2.1).
    value = max(0, min(int(score), 1_000_000))
    constant = float(chart_constant)
    if value <= 800_000:
        return 0.0
    if value < 970_000:
        return (constant * ((value - 800_000) / 170_000)) / 34
    if value < 990_000:
        bonus = (value - 970_000) / 20_000
    elif value < 995_000:
        bonus = 1 + (value - 990_000) / 10_000
    elif value < 999_000:
        bonus = 1.5 + (value - 995_000) / 8_000
    else:
        bonus = 2 + (value - 999_000) / 10_000
    return (constant + bonus) / 34


def _visible_level(constant: float) -> str:
    base = math.floor(constant)
    return f"{base}+" if constant - base >= 0.5 else str(base)


def match_scores(
    game_scores: Sequence[GameScore], charts: Optional[Sequence[dict]] = None,
) -> Tuple[List[BestScore], int]:
    """Match official song IDs to the bundled precise chart constants."""
    chart_rows = tuple(charts) if charts is not None else load_chart_table()
    grouped: Dict[Tuple[str, str, str], List[GameScore]] = {}
    for row in sorted(game_scores, key=lambda item: item.song_id):
        grouped.setdefault((row.title, row.difficulty, row.level), []).append(row)

    matched_rows = set()
    processed_chart_ids = set()
    matched: List[BestScore] = []
    for chart in chart_rows:
        try:
            chart_id = str(chart["chart_id"])
            title = _normalize_field(str(chart["title"]))
            difficulty = str(chart["difficulty"]).strip().upper()
            constant = float(chart["const_value"])
        except (KeyError, TypeError, ValueError):
            continue
        if chart_id in processed_chart_ids:
            continue
        processed_chart_ids.add(chart_id)

        config = chart.get("match_config")
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                config = None
        if isinstance(config, dict):
            source_title = _normalize_field(str(config.get("csv_title", title)))
            # Legacy match_config fields describe the former CSV export, where
            # RAVAGE was labelled INSANITY. Official account data can identify
            # the fourth chart through the song catalog, so use its real name.
            source_difficulty = difficulty
            source_level = str(config.get("csv_level", _visible_level(constant)))
            try:
                order = max(0, int(config.get("order", 0)))
            except (TypeError, ValueError):
                order = 0
        else:
            source_title = title
            source_difficulty = difficulty
            source_level = _visible_level(constant)
            order = 0

        candidates = grouped.get((source_title, source_difficulty, source_level), [])
        # The only legacy order=1 entry distinguished two OMG fourth charts
        # that the CSV called INSANITY. They are separate INSANITY/RAVAGE
        # groups in the official catalog, so each group starts at zero.
        if order >= len(candidates) and len(candidates) == 1:
            order = 0
        if order >= len(candidates):
            continue
        row = candidates[order]
        contribution = song_contribution(row.score, constant)
        matched_rows.add((row.song_id, row.difficulty))
        matched.append(BestScore(
            chart_id=chart_id,
            title=title,
            difficulty=difficulty,
            constant=constant,
            score=row.score,
            contribution=contribution,
            single_rating=contribution * 40,
            rank=score_rank(row.score),
            song_id=row.song_id,
        ))

    # A chart should be unique, but keep the higher score defensively if a
    # future table contains aliases that resolve to the same chart ID.
    best_by_chart: Dict[str, BestScore] = {}
    for item in matched:
        previous = best_by_chart.get(item.chart_id)
        if previous is None or item.score > previous.score:
            best_by_chart[item.chart_id] = item
    return list(best_by_chart.values()), len(game_scores) - len(matched_rows)


def build_b40_from_user_data(
    user_data: dict,
    catalog: Optional[Sequence[CatalogChart]] = None,
    charts: Optional[Sequence[dict]] = None,
    fetched_at: Optional[float] = None,
) -> B40Result:
    raw_scores = parse_playfab_scores(user_data)
    game_scores, unknown = resolve_game_scores(
        raw_scores, catalog if catalog is not None else load_song_catalog()
    )
    matched, unmatched = match_scores(game_scores, charts)
    rated = [item for item in matched if item.contribution > 0]
    rated.sort(key=lambda item: (item.contribution, item.score), reverse=True)
    top40 = tuple(rated[:40])
    return B40Result(
        scores=top40,
        rating=sum(item.contribution for item in top40),
        source_score_count=len(raw_scores),
        matched_chart_count=len(matched),
        unmatched_row_count=max(0, unmatched + unknown),
        fetched_at=fetched_at if fetched_at is not None else time.time(),
    )


def get_b40(custom_id: str, force_refresh: bool = False) -> B40Result:
    binding_secret = str(custom_id or "").strip()
    if not binding_secret:
        raise TakumiAuthError("本地绑定信息已损坏，请重新绑定。")
    now = time.monotonic()
    if not force_refresh:
        with _CACHE_LOCK:
            cached = _RESULT_CACHE.get(binding_secret)
            if cached and now - cached[0] < SCORE_CACHE_SECONDS:
                return cached[1]
    session = login_with_custom_id(binding_secret)
    result = build_b40_from_user_data(fetch_user_data(session.session_ticket))
    with _CACHE_LOCK:
        _RESULT_CACHE[binding_secret] = (time.monotonic(), result)
    return result


def cleanup_old_images(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    cutoff = time.time() - OUTPUT_MAX_AGE_SECONDS
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix.lower() == ".png":
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass


def _font(paths: Iterable[str], size: int) -> ImageFont.FreeTypeFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def ui_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/BarlowCondensed-SemiBold.ttf",
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf",
        "/usr/share/truetype/dejavu/DejaVuSans.ttf",
    ), size)


def number_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/BarlowCondensed-ExtraBold.ttf",
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf",
        "/usr/share/truetype/dejavu/DejaVuSans-Bold.ttf",
    ), size)


def text_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/font.ttf",
        "C:/Windows/Fonts/msyh.ttc", "msyh.ttc",
        "/usr/share/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/truetype/dejavu/DejaVuSans.ttf",
    ), size)


def bold_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "C:/Windows/Fonts/msyhbd.ttc", "msyhbd.ttc",
        "/usr/share/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/truetype/wqy/wqy-microhei.ttc",
        "data/b30_assets/font/font.ttf",
        "data/b30_assets/font/FOT_NewRodin_Pro_EB.otf",
        "/usr/share/truetype/dejavu/DejaVuSans-Bold.ttf",
    ), size)


@lru_cache(maxsize=1)
def _load_jacket_atlas() -> Tuple[Image.Image, dict, int, int]:
    try:
        metadata = json.loads(JACKET_INDEX_PATH.read_text(encoding="utf-8"))
        cell_size = int(metadata["cell_size"])
        columns = int(metadata["columns"])
        songs = metadata["songs"]
        if cell_size <= 0 or columns <= 0 or not isinstance(songs, dict):
            raise ValueError
        with Image.open(JACKET_ATLAS_PATH) as source:
            atlas = source.convert("RGB")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TakumiChartDataError("TAKUMI³ 本地曲绘图集读取失败。") from exc
    return atlas, songs, cell_size, columns


@lru_cache(maxsize=512)
def load_jacket_image(song_id: int) -> Optional[Image.Image]:
    """Return one square jacket from the compact bundled atlas."""
    try:
        atlas, songs, cell_size, columns = _load_jacket_atlas()
        position = int(songs[str(int(song_id))])
    except (TakumiChartDataError, KeyError, TypeError, ValueError):
        return None
    column, row = position % columns, position // columns
    left, top = column * cell_size, row * cell_size
    if left + cell_size > atlas.width or top + cell_size > atlas.height:
        return None
    return atlas.crop((left, top, left + cell_size, top + cell_size))


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= width:
        return text
    suffix = "..."
    available = width - draw.textbbox((0, 0), suffix, font=font)[2]
    value = ""
    for char in text:
        if draw.textbbox((0, 0), value + char, font=font)[2] > available:
            break
        value += char
    return value.rstrip() + suffix


def _draw_cube(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int,
    line: Tuple[int, int, int] = CYAN,
) -> None:
    half = size // 2
    quarter = size // 4
    top = (cx, cy - half)
    left = (cx - half, cy - quarter)
    center = (cx, cy)
    right = (cx + half, cy - quarter)
    bottom = (cx, cy + half)
    left_lower = (cx - half, cy + quarter)
    right_lower = (cx + half, cy + quarter)
    draw.polygon((top, right, center, left), fill=(22, 72, 88), outline=line)
    draw.polygon((left, center, bottom, left_lower),
                 fill=(14, 42, 64), outline=line)
    draw.polygon((center, right, right_lower, bottom),
                 fill=(18, 53, 75), outline=line)
    for start, end in ((top, left), (top, right), (left, center),
                       (right, center), (center, bottom),
                       (left_lower, bottom), (right_lower, bottom)):
        draw.line((start, end), fill=line, width=max(2, size // 28))


def _rating_color(rating: float) -> Tuple[int, int, int]:
    if rating >= 19:
        return (250, 211, 77)
    if rating >= 18:
        return (192, 132, 252)
    if rating >= 16:
        return (248, 113, 113)
    if rating >= 14:
        return (251, 146, 60)
    if rating >= 12:
        return (250, 204, 21)
    if rating >= 10:
        return (74, 222, 128)
    if rating >= 5:
        return (34, 211, 238)
    return PAPER


def _draw_background(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    # Deep-blue vertical blend.
    for y in range(CANVAS_H):
        blend = y / max(1, CANVAS_H - 1)
        color = (
            round(7 + 7 * blend),
            round(19 + 9 * blend),
            round(34 + 14 * blend),
            255,
        )
        draw.line((0, y, CANVAS_W, y), fill=color)

    # TAKUMI³-like moving lane perspective.
    vanish_x, vanish_y = 2130, 110
    for bottom_x in range(-400, CANVAS_W + 500, 180):
        draw.line((vanish_x, vanish_y, bottom_x, CANVAS_H),
                  fill=(0, 145, 165, 30), width=2)
    for y in range(320, CANVAS_H, 116):
        draw.line((0, y, CANVAS_W, y), fill=(0, 216, 224, 24), width=2)
    for offset in range(7):
        draw.line((0, PANEL_Y - 10 - offset, CANVAS_W, PANEL_Y - 10 - offset),
                  fill=(*CYAN, max(12, 76 - offset * 10)), width=1)


def _draw_header(
    canvas: Image.Image, player_name: str, result: B40Result,
) -> None:
    draw = ImageDraw.Draw(canvas)
    panel = (MARGIN, 18, CANVAS_W - MARGIN, 250)
    draw.rectangle(panel, fill=INK)
    draw.rectangle((panel[0], panel[1], panel[0] + 14, panel[3]), fill=CYAN)
    draw.rectangle((panel[0], panel[3] - 12, 1780, panel[3]), fill=MAGENTA)
    draw.rectangle((1780, panel[3] - 12, panel[2], panel[3]), fill=CYAN)

    draw.text((62, 38), "TAKUMI³  /  PERFORMANCE ARCHIVE",
              fill=CYAN, font=ui_font(29))
    name_font = bold_font(62)
    name = _truncate(draw, player_name or "PLAYER", name_font, 1020)
    draw.text((60, 80), name, fill=PAPER, font=name_font)
    status = (
        f"TOP {len(result.scores):02d}  •  MATCHED {result.matched_chart_count:03d}"
        f" / ACCOUNT {result.source_score_count:03d}"
    )
    draw.text((64, 176), status, fill=MUTED, font=ui_font(25))

    draw.rectangle((1135, 36, 1143, 216), fill=MAGENTA)
    draw.text((1190, 35), "RATING", fill=MUTED, font=ui_font(27))
    rating_text = f"{result.rating:.3f}"
    rating_font = number_font(112)
    draw.text((1182, 70), rating_text, fill=_rating_color(result.rating),
              font=rating_font)

    completeness = "COMPLETE" if result.is_complete else "PARTIAL"
    completeness_color = CYAN if result.is_complete else (244, 178, 65)
    draw.text((2050, 38), "BEST 40 DATA", fill=completeness_color,
              font=ui_font(27))
    draw.text((2050, 78), completeness, fill=PAPER, font=number_font(51))
    draw.text((2050, 143), "UNMATCHED", fill=MUTED, font=ui_font(23))
    draw.text((2225, 137), f"{result.unmatched_row_count:03d}",
              fill=PAPER, font=number_font(37))

    # Cubic logo block replaces the character art used by the CHUNITHM sheet.
    logo_left = 2510
    draw.rectangle((logo_left, 18, panel[2], 238), fill=PAPER)
    _draw_cube(draw, 2685, 123, 118, line=CYAN_DARK)
    draw.text((2790, 55), "B", fill=INK, font=number_font(96))
    draw.text((2850, 104), "40", fill=INK, font=number_font(67))


def _draw_rank_badge(
    draw: ImageDraw.ImageDraw, right_x: int, top_y: int, rank: str,
) -> int:
    palette = {
        "S+": ((239, 182, 76), INK),
        "S": ((208, 139, 63), PAPER),
        "AAA": ((193, 203, 211), INK),
        "AA": ((164, 174, 184), INK),
        "A": ((121, 137, 151), PAPER),
    }
    fill, text_fill = palette.get(rank, ((87, 101, 116), PAPER))
    font = number_font(34)
    bbox = draw.textbbox((0, 0), rank, font=font)
    width = max(78, bbox[2] - bbox[0] + 26)
    left = right_x - width
    draw.rectangle((left + 4, top_y + 4, right_x + 4, top_y + 47 + 4), fill=INK)
    draw.rectangle((left, top_y, right_x, top_y + 47), fill=fill, outline=INK, width=3)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((left + (width - tw) // 2 - bbox[0],
               top_y + (47 - th) // 2 - bbox[1] - 1),
              rank, fill=text_fill, font=font)
    return left


def _draw_card(
    canvas: Image.Image, score: BestScore, x: int, y: int, index: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    diff_label, accent = DIFFICULTY_META.get(
        score.difficulty, (score.difficulty[:3], MUTED))
    draw.rectangle((x + 5, y + 6, x + CARD_W + 5, y + CARD_H + 6), fill=(2, 9, 16))
    draw.rectangle((x, y, x + CARD_W, y + CARD_H), fill=PAPER, outline=CYAN_DARK, width=3)
    draw.rectangle((x, y, x + 9, y + CARD_H), fill=accent)
    draw.rectangle((x + 9, y, x + CARD_W, y + 6), fill=INK)

    jacket_x, jacket_y, jacket_size = x + 18, y + 20, 128
    jacket = load_jacket_image(score.song_id)
    if jacket is not None:
        jacket = jacket.resize(
            (jacket_size, jacket_size), Image.Resampling.LANCZOS
        )
        canvas.paste(jacket, (jacket_x, jacket_y))
    else:
        draw.rectangle(
            (jacket_x, jacket_y, jacket_x + jacket_size, jacket_y + jacket_size),
            fill=INK_2,
        )
        _draw_cube(
            draw, jacket_x + jacket_size // 2, jacket_y + jacket_size // 2,
            72, line=accent,
        )
    draw.rectangle(
        (jacket_x - 3, jacket_y - 3,
         jacket_x + jacket_size + 2, jacket_y + jacket_size + 2),
        outline=INK, width=3,
    )

    index_text = f"{index + 1:02d}"
    index_font = number_font(29)
    draw.rectangle(
        (jacket_x - 3, jacket_y - 3, jacket_x + 51, jacket_y + 35), fill=INK
    )
    draw.text((jacket_x + 7, jacket_y - 2), index_text, fill=PAPER, font=index_font)

    const_font = number_font(31)
    const_text = f"{score.constant:.1f}"
    cw = draw.textbbox((0, 0), const_text, font=const_font)[2]
    draw.rectangle((x + 22, y + 165, x + 138, y + 215), fill=INK_2)
    draw.text((x + 80 - cw // 2, y + 170), const_text, fill=PAPER, font=const_font)

    text_x = x + 160
    text_right = x + CARD_W - 17
    meta_font = ui_font(22)
    diff_width = draw.textbbox((0, 0), diff_label, font=meta_font)[2] + 18
    draw.rectangle((text_x, y + 14, text_x + diff_width, y + 47), fill=accent)
    draw.text((text_x + 9, y + 15), diff_label, fill=PAPER, font=meta_font)

    title_size = 33
    title_font = bold_font(title_size)
    while (
        title_size > 24
        and draw.textbbox((0, 0), score.title, font=title_font)[2]
        > text_right - text_x
    ):
        title_size -= 1
        title_font = bold_font(title_size)
    title = _truncate(draw, score.title, title_font, text_right - text_x)
    draw.text((text_x, y + 54), title, fill=INK, font=title_font)

    rank_left = _draw_rank_badge(draw, text_right, y + 111, score.rank)
    score_text = f"{score.score:,}"
    score_font = number_font(46)
    available = rank_left - text_x - 10
    while score_font.size > 30 and draw.textbbox((0, 0), score_text, font=score_font)[2] > available:
        score_font = number_font(score_font.size - 2)
    draw.text((text_x, y + 113), score_text, fill=INK, font=score_font)

    draw.rectangle((text_x, y + 177, text_right, y + 224), fill=INK)
    draw.text((text_x + 10, y + 187), "CHART RT", fill=MUTED, font=ui_font(20))
    rate_text = f"{score.single_rating:.3f}"
    rate_font = number_font(34)
    rw = draw.textbbox((0, 0), rate_text, font=rate_font)[2]
    draw.text((text_right - rw - 10, y + 179), rate_text, fill=PAPER, font=rate_font)


def render_b40_image(
    result: B40Result, player_name: str, output_dir: Path, qq_user_id: str,
) -> Path:
    cleanup_old_images(output_dir)
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), INK + (255,))
    _draw_background(canvas)
    _draw_header(canvas, player_name, result)

    draw = ImageDraw.Draw(canvas)
    panel_bottom = CANVAS_H - 54
    draw.rectangle((MARGIN, PANEL_Y, CANVAS_W - MARGIN, panel_bottom),
                   fill=(9, 25, 39, 238), outline=CYAN_DARK, width=4)
    draw.rectangle((MARGIN, PANEL_Y, CANVAS_W - MARGIN, PANEL_Y + 86), fill=INK)
    draw.rectangle((MARGIN, PANEL_Y, MARGIN + 380, PANEL_Y + 86), fill=CYAN)
    draw.text((MARGIN + 28, PANEL_Y + 15), "RATE TARGETS",
              fill=INK, font=number_font(43))
    average = (
        sum(item.single_rating for item in result.scores) / 40
        if result.scores else 0.0
    )
    stat_text = f"B40 AVG. {average:.3f}    /    {len(result.scores):02d} CHARTS"
    stat_font = ui_font(29)
    stat_w = draw.textbbox((0, 0), stat_text, font=stat_font)[2]
    draw.text((CANVAS_W - MARGIN - stat_w - 28, PANEL_Y + 25), stat_text,
              fill=PAPER, font=stat_font)

    card_start_y = PANEL_Y + SECTION_H
    for index, score in enumerate(result.scores[:40]):
        row, column = divmod(index, CARD_COLUMNS)
        x = CARD_START_X + column * (CARD_W + CARD_GAP_X)
        y = card_start_y + row * (CARD_H + CARD_GAP_Y)
        _draw_card(canvas, score, x, y, index)

    footer = (
        "TAKUMI³ BEST 40  •  SCORE DATA: DIRECT ACCOUNT  •  "
        "OFFICIAL SONG IDS + COMMUNITY CHART CONSTANTS"
    )
    footer_font = ui_font(19)
    fw = draw.textbbox((0, 0), footer, font=footer_font)[2]
    draw.text(((CANVAS_W - fw) // 2, CANVAS_H - 40), footer,
              fill=MUTED, font=footer_font)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_user = "".join(char for char in str(qq_user_id) if char.isalnum()) or "user"
    path = output_dir / f"takumi_b40_{safe_user}_{int(result.fetched_at)}.png"
    canvas = append_image_credit(canvas, ui_font(22))
    canvas.save(path, format="PNG", optimize=True)
    return path


def generate_b40_image(
    custom_id: str, player_name: str, output_dir: Path,
    qq_user_id: str, force_refresh: bool = False,
) -> Tuple[Path, B40Result]:
    result = get_b40(custom_id, force_refresh=force_refresh)
    return render_b40_image(result, player_name, output_dir, qq_user_id), result
