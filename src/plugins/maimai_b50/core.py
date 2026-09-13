"""LXNS OAuth-backed maimai DX Best 50 renderer."""

import io
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

from src.image_credit import append_image_credit

BASE_URL = "https://maimai.lxns.net"
ASSET_BASES = ("https://assets2.lxns.net", "https://assets.lxns.net")
PLAYER_URL = f"{BASE_URL}/api/v0/user/maimai/player"
BESTS_URL = f"{PLAYER_URL}/bests"
SONGS_URL = f"{BASE_URL}/api/v0/maimai/song/list"

CANVAS_W, CANVAS_H = 3000, 3160
MARGIN = 24
HEADER_H = 270
SECTION_HEADER_H = 112
CARD_W, CARD_H = 568, 236
CARD_GAP_X, CARD_GAP_Y = 22, 22
CARD_START_X = 40
PAPER = (238, 235, 225)
INK = (20, 21, 23)
MUTED = (151, 154, 158)
OLD_ACCENT = (239, 79, 45)
NEW_ACCENT = (16, 177, 191)

DIFFICULTIES = {
    0: ("BAS", (67, 181, 91)),
    1: ("ADV", (236, 184, 42)),
    2: ("EXP", (224, 62, 65)),
    3: ("MAS", (147, 72, 190)),
    4: ("Re:M", (228, 222, 236)),
}

RANKS = {
    "d": "D", "c": "C", "b": "B", "bb": "BB", "bbb": "BBB",
    "a": "A", "aa": "AA", "aaa": "AAA", "s": "S", "sp": "S+",
    "ss": "SS", "ssp": "SS+", "sss": "SSS", "sssp": "SSS+",
}

_SONG_CACHE: Dict[int, Dict[str, Any]] = {}


def _font(paths: Tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def get_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/font.ttf",
        "C:/Windows/Fonts/msyh.ttc", "msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ), size)


def get_song_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/FOT_NewRodin_Pro_EB.otf",
        "data/b30_assets/font/font.ttf",
        "C:/Windows/Fonts/msyhbd.ttc", "msyhbd.ttc",
        "/usr/share/opentype/noto/NotoSansCJK-Bold.ttc",
    ), size)


def get_b50_ui_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/BarlowCondensed-SemiBold.ttf",
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf",
        "C:/Windows/Fonts/arialbd.ttf", "arialbd.ttf",
    ), size)


def get_b50_number_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/BarlowCondensed-ExtraBold.ttf",
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf",
    ), size)


def get_section_font(size: int) -> ImageFont.FreeTypeFont:
    return _font((
        "data/b30_assets/font/section.ttf",
        "data/b30_assets/font/BarlowCondensed-ExtraBold.ttf",
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf",
    ), size)


def truncate_text_by_width(
    value: str, font: ImageFont.FreeTypeFont, max_width: int,
) -> str:
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value
    suffix = "..."
    available = max_width - draw.textbbox((0, 0), suffix, font=font)[2]
    result = ""
    for character in value:
        if draw.textbbox((0, 0), result + character, font=font)[2] > available:
            break
        result += character
    return result + suffix


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


class MaimaiPlayer:
    def __init__(self, payload: Dict[str, Any]):
        data = _unwrap(payload)
        self.name = str(data.get("name", "Unknown"))
        self.rating = int(data.get("rating", 0) or 0)
        trophy = data.get("trophy") or {}
        self.trophy = str(trophy.get("name", "")) if isinstance(trophy, dict) else ""
        icon = data.get("icon") or {}
        self.icon_id = icon.get("id") if isinstance(icon, dict) else icon
        self.icon_image: Optional[Image.Image] = None

    def load_icon(self) -> Optional[Image.Image]:
        if self.icon_image is not None or self.icon_id is None:
            return self.icon_image
        for base in ASSET_BASES:
            try:
                response = requests.get(
                    f"{base}/maimai/icon/{self.icon_id}.png",
                    timeout=5,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/"},
                )
                if response.status_code == 200 and len(response.content) > 100:
                    self.icon_image = Image.open(io.BytesIO(response.content)).convert("RGBA")
                    return self.icon_image
            except Exception:
                continue
        return None


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

    def load_jacket(self) -> Image.Image:
        if self.jacket_image is not None:
            return self.jacket_image
        cache_dir = Path("data") / "maimai_assets" / "jacket"
        cache_path = cache_dir / f"{self.id}.png"
        if cache_path.exists():
            try:
                self.jacket_image = Image.open(cache_path).convert("RGB")
                return self.jacket_image
            except Exception:
                pass
        for base in ASSET_BASES:
            try:
                response = requests.get(
                    f"{base}/maimai/jacket/{self.id}.png",
                    timeout=5,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/"},
                )
                if response.status_code == 200 and len(response.content) > 100:
                    image = Image.open(io.BytesIO(response.content)).convert("RGB")
                    try:
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        image.save(cache_path)
                    except OSError:
                        pass
                    self.jacket_image = image
                    return image
            except Exception:
                continue
        image = Image.new("RGB", (180, 180), (39, 42, 50))
        draw = ImageDraw.Draw(image)
        font = get_b50_ui_font(25)
        label = str(self.id)
        box = draw.textbbox((0, 0), label, font=font)
        draw.text(((180 - box[2]) // 2, (180 - box[3]) // 2), label, fill=PAPER, font=font)
        self.jacket_image = image
        return image


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


def _draw_background(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, CANVAS_W, CANVAS_H), fill=PAPER)
    for x, y in ((18, 18), (CANVAS_W - 18, 18), (18, CANVAS_H - 18),
                 (CANVAS_W - 18, CANVAS_H - 18)):
        sx = 1 if x < CANVAS_W // 2 else -1
        sy = 1 if y < CANVAS_H // 2 else -1
        draw.line((x, y, x + sx * 70, y), fill=INK, width=3)
        draw.line((x, y, x, y + sy * 70), fill=INK, width=3)
    for row in range(7):
        for col in range(15 - row):
            x = CANVAS_W - 38 - col * 22
            y = CANVAS_H - 38 - row * 22
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(117, 113, 104))


def _draw_header(
    canvas: Image.Image, player: MaimaiPlayer, b35: int, b15: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    panel = (MARGIN, 18, CANVAS_W - MARGIN, 250)
    draw.rectangle(panel, fill=INK)
    draw.rectangle((panel[0], panel[1], panel[0] + 14, panel[3]), fill=NEW_ACCENT)
    draw.rectangle((panel[0], panel[3] - 12, 1750, panel[3]), fill=OLD_ACCENT)
    draw.rectangle((1750, panel[3] - 12, panel[2], panel[3]), fill=NEW_ACCENT)

    icon_size = 158
    icon_x, icon_y = 62, 48
    icon = player.load_icon()
    player_x = 252 if icon is not None else 62
    if icon is not None:
        icon = icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
        draw.rectangle((icon_x - 5, icon_y - 5,
                        icon_x + icon_size + 4, icon_y + icon_size + 4), fill=PAPER)
        canvas.paste(icon, (icon_x, icon_y), icon if icon.mode == "RGBA" else None)
        draw.rectangle((icon_x - 5, icon_y - 5,
                        icon_x + icon_size + 4, icon_y + icon_size + 4), outline=PAPER, width=4)
        draw.rectangle((icon_x - 10, icon_y + icon_size + 8,
                        icon_x + icon_size + 10, icon_y + icon_size + 14), fill=OLD_ACCENT)

    draw.text((player_x + 2, 38), "MAIMAI DX  /  BEST 50", fill=NEW_ACCENT,
              font=get_b50_ui_font(29))
    player_font = get_song_font(63)
    player_name = truncate_text_by_width(player.name, player_font, 1160 - player_x)
    draw.text((player_x, 78), player_name, fill=PAPER, font=player_font)
    if player.trophy:
        trophy = truncate_text_by_width(player.trophy, get_font(28), 870)
        draw.text((player_x, 169), trophy, fill=(205, 207, 209), font=get_font(28))

    draw.rectangle((1192, 36, 1200, 216), fill=OLD_ACCENT)
    draw.text((1250, 34), "DX RATING", fill=MUTED, font=get_b50_ui_font(26))
    draw.text((1242, 72), str(player.rating), fill=PAPER, font=get_b50_number_font(100))

    draw.text((1800, 38), "OLD / 35", fill=OLD_ACCENT, font=get_b50_ui_font(26))
    draw.text((1800, 78), str(b35), fill=PAPER, font=get_b50_number_font(53))
    draw.text((2135, 38), "NEW / 15", fill=NEW_ACCENT, font=get_b50_ui_font(26))
    draw.text((2135, 78), str(b15), fill=PAPER, font=get_b50_number_font(53))

    draw.rectangle((2510, 18, panel[2], 238), fill=PAPER)
    draw.text((2548, 46), "B50", fill=INK, font=get_section_font(120))


def _draw_section(
    canvas: Image.Image, y: int, height: int, title: str, count: int,
    total: int, accent: Tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((MARGIN, y, CANVAS_W - MARGIN, y + height),
                   fill=(222, 218, 207), outline=INK, width=4)
    draw.rectangle((MARGIN, y, CANVAS_W - MARGIN, y + 96), fill=INK)
    draw.rectangle((MARGIN, y, MARGIN + 400, y + 96), fill=accent)
    draw.text((MARGIN + 28, y + 20), title, fill=INK, font=get_section_font(44))
    stats = f"TOTAL  {total}    /    {count:02d}"
    font = get_b50_ui_font(28)
    width = draw.textbbox((0, 0), stats, font=font)[2]
    draw.text((CANVAS_W - MARGIN - 28 - width, y + 33), stats, fill=PAPER, font=font)


def _title_lines(
    text: str, font: ImageFont.FreeTypeFont, max_width: int,
) -> List[str]:
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if probe.textbbox((0, 0), text, font=font)[2] <= max_width:
        return [text]
    split_at = 1
    for index in range(1, len(text) + 1):
        if probe.textbbox((0, 0), text[:index], font=font)[2] > max_width:
            break
        split_at = index
    natural = max(text.rfind(" ", 0, split_at + 1), text.rfind("-", 0, split_at + 1))
    if natural >= max(1, split_at // 2):
        split_at = natural + (1 if text[natural] == "-" else 0)
    first = text[:split_at].rstrip()
    remaining = text[split_at:].lstrip()
    second = truncate_text_by_width(remaining, font, max_width) if remaining else ""
    return [first] + ([second] if second else [])


def _draw_rank_badge(canvas: Image.Image, right: int, top: int, rank: str) -> int:
    draw = ImageDraw.Draw(canvas)
    rank = rank.upper()
    font = get_b50_number_font(37)
    box = draw.textbbox((0, 0), rank, font=font, stroke_width=1)
    width = max(88, box[2] - box[0] + 36)
    height = 49
    left = right - width
    draw.rectangle((left + 4, top + 4, right + 4, top + height + 4), fill=INK)
    if rank == "SSS+":
        stops = (
            (250, 105, 95), (255, 196, 74), (245, 239, 126),
            (99, 215, 177), (94, 187, 240), (176, 125, 229),
        )
        for offset in range(width):
            scaled = offset * (len(stops) - 1) / max(1, width - 1)
            index = min(int(scaled), len(stops) - 2)
            blend = scaled - index
            color = tuple(round(stops[index][c] * (1 - blend) + stops[index + 1][c] * blend) for c in range(3))
            draw.line((left + offset, top, left + offset, top + height), fill=color)
        text_fill = INK
    else:
        top_color, bottom_color, text_fill = {
            "SSS": ((255, 238, 159), (226, 158, 34), (42, 29, 5)),
            "SS+": ((250, 253, 255), (164, 183, 205), (25, 32, 43)),
            "SS": ((231, 235, 241), (143, 151, 165), (24, 27, 33)),
            "S+": ((255, 224, 176), (215, 130, 56), (48, 26, 8)),
            "S": ((235, 191, 125), (163, 99, 42), (45, 24, 9)),
        }.get(rank, ((214, 216, 217), (126, 130, 134), INK))
        for offset in range(height):
            blend = offset / max(1, height - 1)
            color = tuple(round(top_color[c] * (1 - blend) + bottom_color[c] * blend) for c in range(3))
            draw.line((left, top + offset, right, top + offset), fill=color)
    draw.rectangle((left, top, right, top + height), outline=INK, width=3)
    draw.line((left + 5, top + 5, right - 5, top + 5), fill=(255, 255, 255), width=2)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text((left + (width - tw) // 2 - box[0], top + (height - th) // 2 - box[1] - 1),
              rank, font=font, fill=text_fill, stroke_width=1, stroke_fill=PAPER)
    return left


def _draw_card(canvas: Image.Image, score: MaimaiScore, x: int, y: int, index: int) -> None:
    draw = ImageDraw.Draw(canvas)
    label, accent = DIFFICULTIES.get(score.level_index, DIFFICULTIES[3])
    draw.rectangle((x, y, x + CARD_W - 1, y + CARD_H - 1), fill=PAPER, outline=INK, width=3)
    draw.rectangle((x, y, x + 9, y + CARD_H), fill=accent)
    draw.rectangle((x + 9, y, x + CARD_W, y + 6), fill=INK)

    jacket_size = 184
    jacket_x, jacket_y = x + 22, y + 26
    jacket = score.load_jacket().resize((jacket_size, jacket_size), Image.Resampling.LANCZOS)
    canvas.paste(jacket, (jacket_x, jacket_y))
    draw.rectangle((jacket_x - 3, jacket_y - 3,
                    jacket_x + jacket_size + 2, jacket_y + jacket_size + 2), outline=INK, width=3)
    index_text = f"{index + 1:02d}"
    index_font = get_b50_number_font(27)
    index_w = draw.textbbox((0, 0), index_text, font=index_font)[2]
    draw.rectangle((jacket_x - 3, jacket_y - 3, jacket_x + index_w + 29, jacket_y + 39), fill=INK)
    draw.text((jacket_x + 11, jacket_y + 1), index_text, fill=PAPER, font=index_font)

    tx = x + 226
    right = x + CARD_W - 18
    meta_font = get_b50_ui_font(23)
    diff_width = draw.textbbox((0, 0), label, font=meta_font)[2] + 18
    draw.rectangle((tx, y + 12, tx + diff_width, y + 47), fill=accent)
    diff_text_color = INK if score.level_index == 4 else PAPER
    draw.text((tx + 9, y + 15), label, fill=diff_text_color, font=meta_font)
    type_text = "DX" if score.song_type == "dx" else "STD"
    meta_text = f"{type_text}  定数 {score.level}"
    meta_text = truncate_text_by_width(meta_text, get_song_font(22), right - tx - diff_width - 13)
    draw.text((tx + diff_width + 13, y + 15), meta_text, fill=INK, font=get_song_font(22))

    title_font = get_song_font(27)
    lines = _title_lines(score.song_name, title_font, right - tx)
    for line_index, line in enumerate(lines):
        draw.text((tx, y + 57 + line_index * 31), line, fill=INK, font=title_font)

    rank_left = _draw_rank_badge(canvas, right, y + 121, score.rank)
    achievement = f"{score.achievements:.4f}%"
    achievement_font = get_b50_number_font(43)
    available = rank_left - tx - 12
    while draw.textbbox((0, 0), achievement, font=achievement_font)[2] > available and achievement_font.size > 30:
        achievement_font = get_b50_number_font(achievement_font.size - 2)
    achievement_y = y + (124 if len(lines) > 1 else 117)
    draw.text((tx, achievement_y), achievement, fill=INK, font=achievement_font)

    draw.rectangle((tx, y + 178, right, y + 225), fill=INK)
    draw.text((tx + 11, y + 187), "DX RT", fill=PAPER, font=get_b50_ui_font(22))
    value = str(score.dx_rating)
    value_font = get_b50_number_font(35)
    value_w = draw.textbbox((0, 0), value, font=value_font)[2]
    draw.text((right - value_w - 10, y + 181), value, fill=(255, 255, 255), font=value_font)


def create_maimai_b50_image(
    player: MaimaiPlayer,
    standard: List[MaimaiScore],
    current: List[MaimaiScore],
    standard_total: int,
    dx_total: int,
    save_path: Path,
) -> Path:
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), PAPER)
    _draw_background(canvas)
    _draw_header(canvas, player, standard_total, dx_total)

    old_y = HEADER_H + 20
    old_h = SECTION_HEADER_H + 7 * CARD_H + 6 * CARD_GAP_Y + 24
    _draw_section(canvas, old_y, old_h, "OLD  /  BEST 35", len(standard), standard_total, OLD_ACCENT)
    start_y = old_y + SECTION_HEADER_H
    for index, score in enumerate(standard[:35]):
        row, column = divmod(index, 5)
        x = CARD_START_X + column * (CARD_W + CARD_GAP_X)
        y = start_y + row * (CARD_H + CARD_GAP_Y)
        _draw_card(canvas, score, x, y, index)

    new_y = old_y + old_h + 20
    new_h = SECTION_HEADER_H + 3 * CARD_H + 2 * CARD_GAP_Y + 24
    _draw_section(canvas, new_y, new_h, "NEW  /  BEST 15", len(current), dx_total, NEW_ACCENT)
    start_y = new_y + SECTION_HEADER_H
    for index, score in enumerate(current[:15]):
        row, column = divmod(index, 5)
        x = CARD_START_X + column * (CARD_W + CARD_GAP_X)
        y = start_y + row * (CARD_H + CARD_GAP_Y)
        _draw_card(canvas, score, x, y, index)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = append_image_credit(canvas, get_b50_ui_font(22))
    canvas.save(save_path, quality=95)
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
