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
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from src.image_credit import append_image_credit
from src.score_level_query import LevelQuery, matches_level_query, parse_level_query


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
SCORE_LIST_PAGE_SIZE = 40

INK = (11, 20, 30)
INK_2 = (22, 33, 44)
PAPER = (242, 241, 234)
PAPER_2 = (205, 210, 208)
CYAN = (18, 199, 207)
CYAN_DARK = (43, 116, 126)
MAGENTA = (202, 64, 122)
MUTED = (124, 139, 149)

DIFFICULTY_META = {
    "NORMAL": ("NOR", (35, 151, 205)),
    "HARD": ("HAR", (225, 156, 39)),
    "MASTER": ("MAS", (164, 67, 194)),
    "INSANITY": ("INS", (74, 84, 101)),
    "RAVAGE": ("RAV", (220, 57, 69)),
}

# Best 40 editorial system. The score-list renderer intentionally keeps its
# denser contact-sheet geometry; B40 gets a distinct hierarchy instead of
# repeating forty equal cards.
B40_BG = (8, 14, 22)
B40_SURFACE = (16, 24, 35)
B40_SURFACE_RAISED = (22, 32, 44)
B40_TEXT = (238, 234, 224)
B40_TEXT_SOFT = (194, 197, 192)
B40_MUTED = (105, 117, 126)
B40_ACCENT = (91, 176, 179)
B40_AMBER = (229, 173, 76)
B40_RULE = (43, 53, 64)
B40_MARGIN = 64
B40_GAP = 24
B40_COLUMN_W = 555


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
    display_level: str = ""


@dataclass(frozen=True)
class B40Result:
    scores: Tuple[BestScore, ...]
    rating: float
    source_score_count: int
    matched_chart_count: int
    unmatched_row_count: int
    fetched_at: float
    all_scores: Tuple[BestScore, ...] = ()

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
            display_level=row.level,
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
    matched.sort(
        key=lambda item: (item.constant, item.score, item.single_rating),
        reverse=True,
    )
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
        all_scores=tuple(matched),
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
    canvas_h = canvas.height
    # Restrained blue-black field: enough depth for an arcade display without
    # competing with forty information-dense score cards.
    for y in range(canvas_h):
        blend = y / max(1, canvas_h - 1)
        color = (
            round(9 + 3 * blend),
            round(18 + 5 * blend),
            round(28 + 7 * blend),
            255,
        )
        draw.line((0, y, CANVAS_W, y), fill=color)
    draw.rectangle((0, 0, 12, canvas_h), fill=CYAN_DARK)
    draw.line((MARGIN, PANEL_Y - 12, CANVAS_W - MARGIN, PANEL_Y - 12),
              fill=(45, 65, 78), width=2)


def _draw_header(
    canvas: Image.Image, player_name: str, result: B40Result,
) -> None:
    draw = ImageDraw.Draw(canvas)
    panel = (40, 24, CANVAS_W - 40, 246)
    draw.rectangle(panel, fill=(13, 24, 36))
    draw.rectangle((panel[0], panel[1], panel[0] + 6, panel[3]), fill=CYAN)
    draw.rectangle((panel[0], panel[3] - 5, panel[0] + 178, panel[3]), fill=CYAN)
    draw.rectangle((panel[0] + 178, panel[3] - 5,
                    panel[0] + 254, panel[3]), fill=MAGENTA)

    draw.text((70, 43), "TAKUMI³  /  PLAYER PERFORMANCE",
              fill=CYAN, font=ui_font(25))
    name_font = bold_font(58)
    name = _truncate(draw, player_name or "PLAYER", name_font, 960)
    draw.text((68, 84), name, fill=PAPER, font=name_font)
    status = (
        f"MATCHED {result.matched_chart_count:03d}"
        f"   /   ACCOUNT {result.source_score_count:03d}"
    )
    draw.text((70, 184), status, fill=MUTED, font=ui_font(23))

    draw.line((1125, 48, 1125, 218), fill=(52, 68, 81), width=2)
    draw.text((1170, 44), "RATING", fill=MUTED, font=ui_font(24))
    rating_text = f"{result.rating:.3f}"
    rating_font = number_font(106)
    draw.text((1165, 78), rating_text, fill=_rating_color(result.rating),
              font=rating_font)

    completeness = "COMPLETE" if result.is_complete else "PARTIAL"
    completeness_color = CYAN if result.is_complete else (244, 178, 65)
    draw.line((1985, 48, 1985, 218), fill=(52, 68, 81), width=2)
    draw.text((2030, 44), "BEST 40", fill=completeness_color,
              font=ui_font(24))
    draw.text((2027, 83), completeness, fill=PAPER, font=number_font(49))
    draw.text((2030, 157),
              f"UNMATCHED {result.unmatched_row_count:03d}",
              fill=MUTED, font=ui_font(22))

    badge = "B40"
    badge_font = number_font(96)
    badge_w = draw.textbbox((0, 0), badge, font=badge_font)[2]
    draw.text((panel[2] - badge_w - 56, 72), badge,
              fill=(77, 94, 106), font=badge_font)


def _draw_score_list_header(
    canvas: Image.Image,
    player_name: str,
    result: B40Result,
    query: LevelQuery,
    total: int,
    page: int,
    page_count: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    panel = (40, 24, CANVAS_W - 40, 246)
    draw.rectangle(panel, fill=(13, 24, 36))
    draw.rectangle((panel[0], panel[1], panel[0] + 6, panel[3]), fill=CYAN)
    draw.rectangle((panel[0], panel[3] - 5, panel[0] + 178, panel[3]), fill=CYAN)
    draw.rectangle((panel[0] + 178, panel[3] - 5,
                    panel[0] + 254, panel[3]), fill=MAGENTA)

    draw.text((70, 43), "TAKUMI³  /  SCORE ARCHIVE",
              fill=CYAN, font=ui_font(25))
    name_font = bold_font(58)
    name = _truncate(draw, player_name or "PLAYER", name_font, 960)
    draw.text((68, 84), name, fill=PAPER, font=name_font)
    status = (
        f"PLAYED {total:03d}   /   PAGE {page:02d}/{page_count:02d}"
        f"   /   ACCOUNT {result.source_score_count:03d}"
    )
    draw.text((70, 184), status, fill=MUTED, font=ui_font(23))

    draw.line((1125, 48, 1125, 218), fill=(52, 68, 81), width=2)
    draw.text((1170, 44), "B40 RATING", fill=MUTED, font=ui_font(24))
    rating_text = f"{result.rating:.3f}"
    draw.text((1165, 78), rating_text, fill=_rating_color(result.rating),
              font=number_font(106))

    draw.line((1985, 48, 1985, 218), fill=(52, 68, 81), width=2)
    draw.text((2030, 44), "LEVEL QUERY", fill=CYAN, font=ui_font(24))
    query_text = f"Lv.{query.label}"
    query_font = number_font(72)
    while (
        query_font.size > 48
        and draw.textbbox((0, 0), query_text, font=query_font)[2] > 430
    ):
        query_font = number_font(query_font.size - 2)
    draw.text((2027, 79), query_text, fill=PAPER, font=query_font)
    kind = "EXACT CONSTANT" if query.is_constant else "DISPLAY LEVEL"
    draw.text((2030, 174), kind, fill=MUTED, font=ui_font(21))

    badge = "LIST"
    badge_font = number_font(78)
    badge_w = draw.textbbox((0, 0), badge, font=badge_font)[2]
    draw.text((panel[2] - badge_w - 56, 84), badge,
              fill=(77, 94, 106), font=badge_font)


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
    draw.rounded_rectangle(
        (left, top_y, right_x, top_y + 44), radius=3,
        fill=fill, outline=(47, 56, 63), width=2,
    )
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((left + (width - tw) // 2 - bbox[0],
               top_y + (44 - th) // 2 - bbox[1] - 1),
              rank, fill=text_fill, font=font)
    return left


def _draw_card(
    canvas: Image.Image, score: BestScore, x: int, y: int, index: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    diff_label, accent = DIFFICULTY_META.get(
        score.difficulty, (score.difficulty[:3], MUTED))
    draw.rectangle(
        (x, y, x + CARD_W, y + CARD_H),
        fill=PAPER, outline=(79, 91, 98), width=1,
    )
    draw.rectangle((x, y, x + 6, y + CARD_H), fill=accent)

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
        outline=(54, 64, 72), width=2,
    )

    index_text = f"{index + 1:02d}"
    index_font = number_font(29)
    draw.rectangle(
        (jacket_x - 2, jacket_y - 2, jacket_x + 49, jacket_y + 33), fill=INK
    )
    draw.text((jacket_x + 7, jacket_y - 3), index_text, fill=PAPER, font=index_font)

    const_font = number_font(31)
    const_text = f"{score.constant:.1f}"
    cw = draw.textbbox((0, 0), const_text, font=const_font)[2]
    draw.rectangle((x + 22, y + 165, x + 138, y + 215), fill=INK_2)
    draw.text((x + 80 - cw // 2, y + 170), const_text, fill=PAPER, font=const_font)

    text_x = x + 160
    text_right = x + CARD_W - 17
    meta_font = ui_font(22)
    draw.rectangle((text_x, y + 17, text_x + 4, y + 43), fill=accent)
    draw.text((text_x + 13, y + 14), diff_label, fill=(65, 76, 84),
              font=meta_font)

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
    draw.text((text_x, y + 52), title, fill=INK, font=title_font)

    rank_left = _draw_rank_badge(draw, text_right, y + 112, score.rank)
    score_text = f"{score.score:,}"
    score_font = number_font(46)
    available = rank_left - text_x - 10
    while score_font.size > 30 and draw.textbbox((0, 0), score_text, font=score_font)[2] > available:
        score_font = number_font(score_font.size - 2)
    draw.text((text_x, y + 112), score_text, fill=INK, font=score_font)

    draw.line((text_x, y + 176, text_right, y + 176),
              fill=(181, 186, 184), width=2)
    draw.text((text_x, y + 188), "CHART RATING", fill=(104, 116, 123),
              font=ui_font(19))
    rate_text = f"{score.single_rating:.3f}"
    rate_font = number_font(34)
    rw = draw.textbbox((0, 0), rate_text, font=rate_font)[2]
    draw.text((text_right - rw, y + 180), rate_text, fill=INK, font=rate_font)


def _b40_jacket(score: BestScore, size: int, saturation: float,
                brightness: float) -> Image.Image:
    jacket = load_jacket_image(score.song_id)
    if jacket is None:
        fallback = Image.new("RGB", (size, size), B40_SURFACE_RAISED)
        fallback_draw = ImageDraw.Draw(fallback)
        fallback_draw.text(
            (size // 2, size // 2), "NO ART", fill=B40_MUTED,
            font=ui_font(max(16, size // 12)), anchor="mm",
        )
        return fallback
    jacket = jacket.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")
    jacket = ImageEnhance.Color(jacket).enhance(saturation)
    jacket = ImageEnhance.Brightness(jacket).enhance(brightness)
    return jacket


def _b40_title_lines(
    draw: ImageDraw.ImageDraw, title: str, font: ImageFont.ImageFont,
    width: int, max_lines: int = 2,
) -> List[str]:
    """Wrap a display title without squeezing its type size."""
    remaining = str(title or "").strip()
    lines: List[str] = []
    while remaining and len(lines) < max_lines:
        if draw.textbbox((0, 0), remaining, font=font)[2] <= width:
            lines.append(remaining)
            remaining = ""
            break
        cut = 0
        for index in range(1, len(remaining) + 1):
            if draw.textbbox((0, 0), remaining[:index], font=font)[2] > width:
                break
            cut = index
        cut = max(1, cut)
        if " " in remaining[:cut]:
            word_cut = remaining.rfind(" ", 0, cut + 1)
            if word_cut > cut // 2:
                cut = word_cut
        lines.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining and lines:
        lines[-1] = _truncate(draw, lines[-1] + " " + remaining, font, width)
    return lines or [""]


def _b40_rank_color(rank: str) -> Tuple[int, int, int]:
    return B40_AMBER if rank == "S+" else B40_TEXT_SOFT


def _draw_b40_background(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, canvas.width, canvas.height), fill=B40_BG)
    # One quiet registration mark is enough to retain the game's technical
    # character; it is not repeated around every component.
    draw.rectangle((B40_MARGIN, 40, B40_MARGIN + 72, 44), fill=B40_ACCENT)


def _draw_b40_hero(
    canvas: Image.Image, player_name: str, result: B40Result,
) -> None:
    draw = ImageDraw.Draw(canvas)
    scores = result.scores
    average = (
        sum(item.single_rating for item in scores) / len(scores)
        if scores else 0.0
    )

    draw.text((B40_MARGIN, 60), "TAKUMI³  /  PLAYER REPORT",
              fill=B40_ACCENT, font=ui_font(24))
    name_font = text_font(68)
    name = _truncate(draw, player_name or "PLAYER", name_font, 960)
    draw.text((B40_MARGIN, 104), name, fill=B40_TEXT, font=name_font)
    draw.text(
        (B40_MARGIN, 204),
        f"MATCHED {result.matched_chart_count:03d}   /   "
        f"ACCOUNT {result.source_score_count:03d}",
        fill=B40_MUTED, font=ui_font(24),
    )
    draw.text((B40_MARGIN, 250), "DIRECT ACCOUNT PERFORMANCE SNAPSHOT",
              fill=(73, 86, 96), font=ui_font(20))

    rating_x = 1184
    draw.text((rating_x, 56), "RATING", fill=B40_MUTED, font=ui_font(25))
    rating_text = f"{result.rating:.3f}"
    draw.text((rating_x - 8, 76), rating_text, fill=B40_TEXT,
              font=number_font(180))
    draw.text((rating_x, 264),
              f"CALCULATED FROM {len(scores):02d} RATED CHARTS",
              fill=B40_ACCENT, font=ui_font(21))

    right_x = 2260
    completeness = "COMPLETE" if result.is_complete else "PARTIAL"
    draw.text((right_x, 60), "BEST 40 STATUS", fill=B40_ACCENT,
              font=ui_font(24))
    draw.text((right_x, 104), completeness, fill=B40_TEXT,
              font=number_font(52))
    draw.text((right_x, 166), f"{len(scores):02d} / 40",
              fill=B40_TEXT_SOFT, font=number_font(68))
    draw.text((right_x, 252), f"AVERAGE  {average:.3f}",
              fill=B40_MUTED, font=ui_font(23))

    draw.line((B40_MARGIN, 328, CANVAS_W - B40_MARGIN, 328),
              fill=B40_RULE, width=2)


def _draw_score_archive_hero(
    canvas: Image.Image,
    player_name: str,
    result: B40Result,
    query: LevelQuery,
    total: int,
    page: int,
    page_count: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.text((B40_MARGIN, 60), "TAKUMI³  /  SCORE ARCHIVE",
              fill=B40_ACCENT, font=ui_font(24))
    name_font = text_font(62)
    name = _truncate(draw, player_name or "PLAYER", name_font, 960)
    draw.text((B40_MARGIN, 108), name, fill=B40_TEXT, font=name_font)
    draw.text(
        (B40_MARGIN, 204),
        f"PLAYED {total:03d}   /   ACCOUNT {result.source_score_count:03d}",
        fill=B40_MUTED, font=ui_font(24),
    )
    draw.text((B40_MARGIN, 250), "DIRECT ACCOUNT SCORE SNAPSHOT",
              fill=(73, 86, 96), font=ui_font(20))

    query_x = 1184
    draw.text((query_x, 56), "LEVEL QUERY", fill=B40_MUTED,
              font=ui_font(25))
    query_text = f"Lv.{query.label}"
    query_font = number_font(156)
    while (
        query_font.size > 104
        and draw.textbbox((0, 0), query_text, font=query_font)[2] > 820
    ):
        query_font = number_font(query_font.size - 4)
    draw.text((query_x - 8, 88), query_text, fill=B40_TEXT,
              font=query_font)
    query_kind = "EXACT CHART CONSTANT" if query.is_constant else "DISPLAY LEVEL"
    draw.text((query_x, 264), query_kind, fill=B40_ACCENT,
              font=ui_font(21))

    right_x = 2260
    draw.text((right_x, 60), "ARCHIVE STATUS", fill=B40_ACCENT,
              font=ui_font(24))
    draw.text((right_x, 104), f"{total:02d} RESULTS", fill=B40_TEXT,
              font=number_font(52))
    draw.text((right_x, 174), f"PAGE {page:02d} / {page_count:02d}",
              fill=B40_TEXT_SOFT, font=number_font(42))
    draw.text((right_x, 252), f"B40 RATING  {result.rating:.3f}",
              fill=B40_MUTED, font=ui_font(23))

    draw.line((B40_MARGIN, 328, CANVAS_W - B40_MARGIN, 328),
              fill=B40_RULE, width=2)


def _draw_b40_section_label(
    draw: ImageDraw.ImageDraw, y: int, number: str, title: str,
    descriptor: str,
) -> None:
    draw.text((B40_MARGIN, y), number, fill=B40_ACCENT, font=number_font(27))
    draw.text((B40_MARGIN + 54, y - 1), title, fill=B40_TEXT,
              font=text_font(31))
    title_w = draw.textbbox((0, 0), title, font=text_font(31))[2]
    draw.text((B40_MARGIN + 78 + title_w, y + 6), descriptor,
              fill=B40_MUTED, font=ui_font(19))


def _draw_b40_top_one(
    canvas: Image.Image, score: BestScore, x: int, y: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    width, height = 1040, 448
    draw.rectangle((x, y, x + width, y + height), fill=B40_SURFACE_RAISED)
    draw.rectangle((x, y, x + 8, y + height), fill=B40_ACCENT)

    jacket_size = 384
    canvas.paste(_b40_jacket(score, jacket_size, 1.0, 0.92), (x + 32, y + 32))

    text_x = x + 456
    text_right = x + width - 32
    draw.text((text_x, y + 32), "01  /  TOP SCORE", fill=B40_ACCENT,
              font=ui_font(22))
    title_font = text_font(47)
    title_lines = _b40_title_lines(
        draw, score.title, title_font, text_right - text_x, 2
    )
    for line_index, line in enumerate(title_lines):
        draw.text((text_x, y + 76 + line_index * 58), line,
                  fill=B40_TEXT, font=title_font)

    draw.text((text_x, y + 202), f"{score.score:,}", fill=B40_TEXT,
              font=number_font(84))
    draw.text((text_x, y + 302), "CHART RATING", fill=B40_MUTED,
              font=ui_font(20))
    draw.text((text_x, y + 326), f"{score.single_rating:.3f}",
              fill=B40_TEXT_SOFT, font=number_font(50))

    diff_label = DIFFICULTY_META.get(score.difficulty, (score.difficulty[:3],))[0]
    metadata = f"{diff_label}   /   CONST {score.constant:.1f}"
    draw.text((text_x, y + 402), metadata, fill=B40_MUTED,
              font=ui_font(21))
    rank_font = number_font(38)
    rank_w = draw.textbbox((0, 0), score.rank, font=rank_font)[2]
    draw.text((text_right - rank_w, y + 394), score.rank,
              fill=_b40_rank_color(score.rank), font=rank_font)


def _draw_b40_top_secondary(
    canvas: Image.Image, score: BestScore, x: int, y: int, index: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    width, height = 892, 212
    draw.rectangle((x, y, x + width, y + height), fill=B40_SURFACE)
    jacket_size = 164
    canvas.paste(_b40_jacket(score, jacket_size, 0.68, 0.76),
                 (x + 24, y + 24))

    text_x = x + 220
    text_right = x + width - 24
    draw.text((text_x, y + 18), f"{index + 1:02d}", fill=B40_ACCENT,
              font=number_font(25))
    diff_label = DIFFICULTY_META.get(score.difficulty, (score.difficulty[:3],))[0]
    draw.text((text_x + 48, y + 22), diff_label, fill=B40_MUTED,
              font=ui_font(18))
    title = _truncate(draw, score.title, text_font(34), text_right - text_x)
    draw.text((text_x, y + 52), title, fill=B40_TEXT, font=text_font(34))
    draw.text((text_x, y + 96), f"{score.score:,}", fill=B40_TEXT,
              font=number_font(49))
    draw.text((text_x, y + 158), "RT", fill=B40_MUTED, font=ui_font(17))
    draw.text((text_x + 34, y + 151), f"{score.single_rating:.3f}",
              fill=B40_TEXT_SOFT, font=number_font(28))
    const_text = f"CONST {score.constant:.1f}   /"
    rank_font = ui_font(18)
    rank_w = draw.textbbox((0, 0), score.rank, font=rank_font)[2]
    const_w = draw.textbbox((0, 0), const_text, font=rank_font)[2]
    rank_x = text_right - rank_w
    draw.text((rank_x - const_w - 8, y + 160), const_text,
              fill=B40_MUTED, font=rank_font)
    draw.text((rank_x, y + 160), score.rank,
              fill=_b40_rank_color(score.rank), font=rank_font)


def _draw_b40_overview(
    canvas: Image.Image, scores: Sequence[BestScore], y: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    values = sorted((item.single_rating for item in scores), reverse=True)
    peak = values[0] if values else 0.0
    floor = values[-1] if values else 0.0
    if not values:
        median = 0.0
    elif len(values) % 2:
        median = values[len(values) // 2]
    else:
        center = len(values) // 2
        median = (values[center - 1] + values[center]) / 2
    s_plus = sum(item.rank == "S+" for item in scores)

    draw.rectangle((B40_MARGIN, y, CANVAS_W - B40_MARGIN, y + 96),
                   fill=B40_SURFACE)
    draw.text((B40_MARGIN + 24, y + 20), "B40 OVERVIEW", fill=B40_ACCENT,
              font=ui_font(21))
    draw.text((B40_MARGIN + 24, y + 48), "PERFORMANCE DEPTH",
              fill=B40_MUTED, font=ui_font(18))

    metrics = (
        ("PEAK", f"{peak:.3f}"),
        ("MEDIAN", f"{median:.3f}"),
        ("FLOOR", f"{floor:.3f}"),
        ("S+ SCORES", f"{s_plus:02d}"),
    )
    start_x = 900
    step = 490
    for offset, (label, value) in enumerate(metrics):
        metric_x = start_x + offset * step
        draw.text((metric_x, y + 18), label, fill=B40_MUTED,
                  font=ui_font(18))
        draw.text((metric_x, y + 40), value,
                  fill=B40_AMBER if label == "S+ SCORES" else B40_TEXT_SOFT,
                  font=number_font(34))


def _draw_b40_standard(
    canvas: Image.Image, score: BestScore, x: int, y: int, index: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    width, height = B40_COLUMN_W, 184
    draw.rectangle((x, y, x + width, y + height), fill=B40_SURFACE)
    jacket_size = 136
    canvas.paste(_b40_jacket(score, jacket_size, 0.52, 0.72),
                 (x + 16, y + 24))

    text_x = x + 176
    right = x + width - 16
    diff_label = DIFFICULTY_META.get(score.difficulty, (score.difficulty[:3],))[0]
    draw.text((text_x, y + 16), f"{index + 1:02d}  /  {diff_label}",
              fill=B40_MUTED, font=ui_font(18))
    title = _truncate(draw, score.title, text_font(28), right - text_x)
    draw.text((text_x, y + 44), title, fill=B40_TEXT, font=text_font(28))
    draw.text((text_x, y + 80), f"{score.score:,}", fill=B40_TEXT,
              font=number_font(43))
    draw.text((text_x, y + 140), f"RT {score.single_rating:.3f}",
              fill=B40_TEXT_SOFT, font=ui_font(21))
    rank_font = ui_font(20)
    rank_w = draw.textbbox((0, 0), score.rank, font=rank_font)[2]
    const_text = f"{score.constant:.1f}  /"
    const_w = draw.textbbox((0, 0), const_text, font=rank_font)[2]
    rank_x = right - rank_w
    draw.text((rank_x - const_w - 8, y + 142), const_text,
              fill=B40_MUTED, font=rank_font)
    draw.text((rank_x, y + 142), score.rank,
              fill=_b40_rank_color(score.rank), font=rank_font)


def _draw_b40_compact(
    canvas: Image.Image, score: BestScore, x: int, y: int, index: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    width, height = B40_COLUMN_W, 144
    jacket_size = 88
    canvas.paste(_b40_jacket(score, jacket_size, 0.18, 0.58),
                 (x, y + 16))

    text_x = x + 112
    right = x + width
    title = _truncate(draw, score.title, text_font(24), right - text_x)
    draw.text((text_x, y + 12), title, fill=B40_TEXT_SOFT,
              font=text_font(24))
    draw.text((text_x, y + 46), f"{score.score:,}", fill=B40_TEXT,
              font=number_font(36))
    diff_label = DIFFICULTY_META.get(score.difficulty, (score.difficulty[:3],))[0]
    draw.text((text_x, y + 94),
              f"{index + 1:02d}  /  {diff_label} {score.constant:.1f}",
              fill=B40_MUTED, font=ui_font(17))
    rating_font = ui_font(18)
    rank_w = draw.textbbox((0, 0), score.rank, font=rating_font)[2]
    rating_text = f"RT {score.single_rating:.3f}  /"
    rating_w = draw.textbbox((0, 0), rating_text, font=rating_font)[2]
    rank_x = right - rank_w
    draw.text((rank_x - rating_w - 8, y + 94), rating_text,
              fill=B40_MUTED, font=rating_font)
    draw.text((rank_x, y + 94), score.rank,
              fill=_b40_rank_color(score.rank), font=rating_font)
    draw.line((text_x, y + height - 8, right, y + height - 8),
              fill=B40_RULE, width=1)


def render_b40_image(
    result: B40Result, player_name: str, output_dir: Path, qq_user_id: str,
) -> Path:
    cleanup_old_images(output_dir)
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), B40_BG)
    _draw_b40_background(canvas)
    _draw_b40_hero(canvas, player_name, result)
    draw = ImageDraw.Draw(canvas)
    scores = list(result.scores[:40])

    _draw_b40_section_label(
        draw, 372, "01", "TOP PERFORMANCE",
        "THE FIVE HIGHEST-CONTRIBUTING CHARTS",
    )
    if scores:
        _draw_b40_top_one(canvas, scores[0], B40_MARGIN, 424)
    secondary_positions = (
        (1128, 424), (2044, 424), (1128, 660), (2044, 660),
    )
    for index, score in enumerate(scores[1:5], start=1):
        x, y = secondary_positions[index - 1]
        _draw_b40_top_secondary(canvas, score, x, y, index)

    _draw_b40_overview(canvas, scores, 904)

    _draw_b40_section_label(
        draw, 1032, "02", "STRONGEST SET",
        "RANKS 06–15  /  STANDARD DETAIL",
    )
    for local_index, score in enumerate(scores[5:15]):
        row, column = divmod(local_index, 5)
        x = B40_MARGIN + column * (B40_COLUMN_W + B40_GAP)
        y = 1080 + row * (184 + B40_GAP)
        _draw_b40_standard(canvas, score, x, y, local_index + 5)

    _draw_b40_section_label(
        draw, 1512, "03", "PERFORMANCE DEPTH",
        "RANKS 16–40  /  COMPACT VIEW",
    )
    for local_index, score in enumerate(scores[15:40]):
        row, column = divmod(local_index, 5)
        x = B40_MARGIN + column * (B40_COLUMN_W + B40_GAP)
        y = 1560 + row * 156
        _draw_b40_compact(canvas, score, x, y, local_index + 15)

    footer = (
        "TAKUMI³ BEST 40   /   DIRECT ACCOUNT DATA   /   "
        "OFFICIAL SONG IDS + COMMUNITY CHART CONSTANTS"
    )
    footer_font = ui_font(18)
    fw = draw.textbbox((0, 0), footer, font=footer_font)[2]
    draw.text(((CANVAS_W - fw) // 2, CANVAS_H - 34), footer,
              fill=B40_MUTED, font=footer_font)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_user = "".join(char for char in str(qq_user_id) if char.isalnum()) or "user"
    path = output_dir / f"takumi_b40_{safe_user}_{int(result.fetched_at)}.png"
    canvas = append_image_credit(
        canvas, ui_font(22), footer_fill=B40_BG,
        text_fill=B40_TEXT_SOFT, rule_fill=B40_ACCENT,
    )
    canvas.save(path, format="PNG", optimize=True)
    return path


def generate_b40_image(
    custom_id: str, player_name: str, output_dir: Path,
    qq_user_id: str, force_refresh: bool = False,
) -> Tuple[Path, B40Result]:
    result = get_b40(custom_id, force_refresh=force_refresh)
    return render_b40_image(result, player_name, output_dir, qq_user_id), result


def _takumi_score_matches(score: BestScore, query: LevelQuery) -> bool:
    return matches_level_query(
        query,
        display_level=score.display_level,
        constant=score.constant,
        plus_threshold=0.5,
    )


def render_score_list_images(
    result: B40Result,
    scores: Sequence[BestScore],
    query: LevelQuery,
    player_name: str,
    output_dir: Path,
    qq_user_id: str,
) -> List[Path]:
    """Render TAKUMI³ level-query results as paginated score sheets."""
    cleanup_old_images(output_dir)
    page_count = max(1, math.ceil(len(scores) / SCORE_LIST_PAGE_SIZE))
    timestamp = time.time_ns()
    safe_user = "".join(
        char for char in str(qq_user_id) if char.isalnum()
    ) or "user"
    safe_level = query.label.replace("+", "p").replace(".", "d")
    paths: List[Path] = []

    for page_index in range(page_count):
        page_scores = scores[
            page_index * SCORE_LIST_PAGE_SIZE:
            (page_index + 1) * SCORE_LIST_PAGE_SIZE
        ]
        featured = page_scores[:5]
        compact = page_scores[5:]
        compact_rows = math.ceil(len(compact) / 5) if compact else 0
        content_bottom = 608
        if compact_rows:
            content_bottom = (
                704 + compact_rows * 144 + max(0, compact_rows - 1) * 12
            )
        canvas_h = max(900, content_bottom + 96)
        canvas = Image.new("RGB", (CANVAS_W, canvas_h), B40_BG)
        _draw_b40_background(canvas)
        _draw_score_archive_hero(
            canvas,
            player_name,
            result,
            query,
            len(scores),
            page_index + 1,
            page_count,
        )

        draw = ImageDraw.Draw(canvas)
        _draw_b40_section_label(
            draw, 372, "01", "LEADING RESULTS",
            "HIGHEST CONSTANT / SCORE ORDER",
        )
        average = (
            sum(item.single_rating for item in scores) / len(scores)
            if scores else 0.0
        )
        stat_text = f"AVG CHART RT  {average:.3f}"
        stat_font = ui_font(22)
        stat_w = draw.textbbox((0, 0), stat_text, font=stat_font)[2]
        draw.text((CANVAS_W - B40_MARGIN - stat_w, 378), stat_text,
                  fill=B40_MUTED, font=stat_font)

        global_offset = page_index * SCORE_LIST_PAGE_SIZE
        for local_index, score in enumerate(featured):
            x = B40_MARGIN + local_index * (B40_COLUMN_W + B40_GAP)
            _draw_b40_standard(
                canvas, score, x, 424, global_offset + local_index,
            )

        if compact:
            _draw_b40_section_label(
                draw, 656, "02", "FULL ARCHIVE",
                "COMPACT DETAIL",
            )
            for local_index, score in enumerate(compact):
                row, column = divmod(local_index, 5)
                x = B40_MARGIN + column * (B40_COLUMN_W + B40_GAP)
                y = 704 + row * 156
                _draw_b40_compact(
                    canvas, score, x, y,
                    global_offset + len(featured) + local_index,
                )

        footer = (
            "TAKUMI³ SCORE ARCHIVE   /   DIRECT ACCOUNT DATA   /   "
            "OFFICIAL SONG IDS + COMMUNITY CHART CONSTANTS"
        )
        footer_font = ui_font(18)
        footer_w = draw.textbbox((0, 0), footer, font=footer_font)[2]
        draw.text(
            ((CANVAS_W - footer_w) // 2, canvas_h - 34), footer,
            fill=B40_MUTED, font=footer_font,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / (
            f"takumi_score_{safe_user}_{safe_level}_{timestamp}_"
            f"{page_index + 1}.png"
        )
        credited = append_image_credit(
            canvas, ui_font(22), footer_fill=B40_BG,
            text_fill=B40_TEXT_SOFT, rule_fill=B40_ACCENT,
        )
        credited.save(path, format="PNG", optimize=True)
        paths.append(path)

    return paths


def generate_score_list_images(
    custom_id: str,
    player_name: str,
    output_dir: Path,
    qq_user_id: str,
    query_text: str,
    force_refresh: bool = False,
) -> Tuple[List[Path], int]:
    query = parse_level_query(query_text)
    result = get_b40(custom_id, force_refresh=force_refresh)
    # ``all_scores`` contains every matched played chart, including scores
    # below the Rating threshold. The fallback keeps hot-reload compatibility
    # with an object cached by an older plugin version.
    source_scores = result.all_scores or result.scores
    matched = [
        score for score in source_scores if _takumi_score_matches(score, query)
    ]
    matched.sort(
        key=lambda item: (item.constant, item.score, item.single_rating),
        reverse=True,
    )
    if not matched:
        return [], 0
    return (
        render_score_list_images(
            result, matched, query, player_name, output_dir, qq_user_id,
        ),
        len(matched),
    )
