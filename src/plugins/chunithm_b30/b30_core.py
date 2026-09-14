"""Chunithm score-image renderers for B30, B50, and related commands."""

import io
import math
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from src.image_credit import append_image_credit
from src.score_level_query import LevelQuery, matches_level_query, parse_level_query


BASE_URL = "https://maimai.lxns.net"
ASSETS_BASE_URLS = [
    "https://assets2.lxns.net",
    "https://assets.lxns.net",
    "https://static.maimai.lxns.net",
]

# ====== 画布 (2.5x ~3000px) ======
CANVAS_WIDTH = 3000
CANVAS_HEIGHT = 1950
TOP_BAR_HEIGHT = 190

# ====== 卡片网格 ======
CARD_COLUMNS = 5
CARD_ROWS = 6
CARD_WIDTH = 560
CARD_HEIGHT = 250
CARD_GAP_X = 30
CARD_GAP_Y = 30
CARD_CORNER_RADIUS = 32

# ====== 卡片内部 ======
JACKET_SIZE = 185
JACKET_X = 24

# ====== 玻璃卡片色彩 (RGBA) ======
GLASS_BG_ULTIMA = (42, 16, 20, 210)
GLASS_BG_MASTER = (52, 20, 84, 205)
GLASS_BG_EXPERT = (68, 16, 20, 205)
GLASS_BG_DEFAULT = (26, 28, 44, 200)

# ====== 发光描边 ======
GLOW_COLOR_ULTIMA = (255, 70, 70, 130)
BORDER_COLOR_ULTIMA = (255, 55, 55, 245)
GLOW_COLOR_MASTER = (175, 95, 255, 130)
BORDER_COLOR_MASTER = (165, 85, 240, 245)
GLOW_COLOR_EXPERT = (245, 75, 65, 120)
BORDER_COLOR_EXPERT = (235, 65, 55, 240)
GLOW_COLOR_DEFAULT = (75, 155, 240, 110)
BORDER_COLOR_DEFAULT = (55, 140, 225, 235)

# ====== 文字色彩 ======
COLOR_WHITE = (255, 255, 255)
COLOR_GOLD = (255, 210, 0)
COLOR_SUBTLE = (170, 175, 195)
COLOR_TOP_BAR_BG = (16, 18, 30, 220)

# ====== 字体路径 ======
SONG_FONT_PATH = "data/b30_assets/font/FOT_NewRodin_Pro_EB.otf"  # 曲名专用
SECTION_FONT_PATH = "data/b30_assets/font/section.ttf"        # 章节标题专用
B50_UI_FONT_PATH = "data/b30_assets/font/BarlowCondensed-SemiBold.ttf"
B50_NUMBER_FONT_PATH = "data/b30_assets/font/BarlowCondensed-ExtraBold.ttf"

# ====== 字号 (2.5x) ======
FZ_PLAYER = 62
FZ_RATING = 44
FZ_TITLE = 50
FZ_SECTION = 42
FZ_RANK_NUM = 44
FZ_SONG = 34
FZ_SCORE = 64
FZ_LEVEL = 36
FZ_CARD_RATING = 32
FZ_BADGE = 30

# ====== 评级映射 ======
RANK_MAPPING = {
    "sp": "S+", "ssp": "SS+", "sssp": "SSS+",
    "d": "D", "c": "C", "b": "B", "bb": "BB", "bbb": "BBB",
    "a": "A", "aa": "AA", "aaa": "AAA",
    "s": "S", "ss": "SS", "sss": "SSS",
}

RANK_BADGE = {
    "SSS+": ((255, 215, 0), (180, 140, 0)),
    "SS+":  ((220, 195, 255), (135, 95, 200)),
    "S+":   ((255, 215, 175), (195, 130, 50)),
    "SSS":  ((255, 195, 0), (155, 115, 0)),
    "SS":   ((195, 175, 240), (115, 75, 180)),
    "S":    ((225, 195, 140), (150, 110, 50)),
}

ALL_SONGS_CACHE: Dict[int, Dict[str, Any]] = {}
ALL_SONGS_LAST_ATTEMPT: Optional[float] = None
SONG_CACHE_RETRY_SECONDS = 300

# ====== 数字贴图缓存 ======
_DIGIT_IMAGES: Dict[str, Image.Image] = {}
_RATING_DIGITS: Dict[str, Dict[str, Image.Image]] = {}

ASSETS_DIR = Path("data") / "b30_assets"


def _load_digits() -> None:
    if _DIGIT_IMAGES:
        return
    num_dir = ASSETS_DIR / "AchieveNum"
    for name in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "comma"):
        p = num_dir / f"{name}.png"
        if p.exists():
            _DIGIT_IMAGES[name] = Image.open(p).convert("RGBA")


def _load_rating_digits() -> None:
    if _RATING_DIGITS:
        return
    for tier in ("gold", "rainbow", "ex_rainbow"):
        tier_dir = ASSETS_DIR / "RatingNum" / tier
        if not tier_dir.exists():
            continue
        _RATING_DIGITS[tier] = {}
        for name in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "dot"):
            p = tier_dir / f"{name}.png"
            if p.exists():
                _RATING_DIGITS[tier][name] = Image.open(p).convert("RGBA")


def _draw_rating_digits(canvas: Image.Image, rating: float, x: int, y: int, target_h: int) -> int:
    """用 RatingNum 贴图拼出 rating 数值，失败时回退文字，返回总宽度"""
    _load_rating_digits()
    if rating >= 17:
        tier = "ex_rainbow"
    elif rating >= 16:
        tier = "rainbow"
    else:
        tier = "gold"
    digits = _RATING_DIGITS.get(tier, _RATING_DIGITS.get("gold", {}))
    if not digits or "0" not in digits:
        return 0

    orig_h = digits["0"].height
    scale = target_h / orig_h
    text = f"{rating:.2f}"
    total_w = 0

    for ch in text:
        key = "dot" if ch == "." else ch
        img = digits.get(key)
        if img is None:
            continue
        w = int(img.width * scale)
        h = int(img.height * scale)
        if w > 0 and h > 0:
            scaled = img.resize((w, h), Image.LANCZOS)
            canvas.paste(scaled, (x + total_w, y), scaled)
            total_w += w + int(scale)

    # 回退：贴图加载失败时用金色文字
    if total_w == 0:
        draw = ImageDraw.Draw(canvas)
        rf = get_section_font(target_h)
        draw.text((x, y), f"{rating:.2f}", fill=COLOR_GOLD, font=rf)
        total_w = draw.textbbox((0, 0), f"{rating:.2f}", font=rf)[2]

    return total_w


# ===================================================================
#  字体
# ===================================================================

def _try_load_font(path: str, size: int) -> Optional[ImageFont.FreeTypeFont]:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return None


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """通用 CJK 字体"""
    for p in [
        "data/b30_assets/font/font.ttf",
        "simhei.ttf", "msyh.ttc", "simkai.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        f = _try_load_font(p, size)
        if f:
            return f
    return ImageFont.load_default(size=size)


def get_song_font(size: int) -> ImageFont.FreeTypeFont:
    """曲名专用字体 (New Rodin EB，回退到通用字体)"""
    f = _try_load_font(SONG_FONT_PATH, size)
    if f:
        return f
    return get_font(size)


def get_section_font(size: int) -> ImageFont.FreeTypeFont:
    """章节标题字体 (Russo One，回退到通用字体)"""
    f = _try_load_font(SECTION_FONT_PATH, size)
    if f:
        return f
    return get_font(size)


def get_b50_ui_font(size: int) -> ImageFont.FreeTypeFont:
    """Compact Latin UI face; falls back to the bundled display font."""
    for path in (
        B50_UI_FONT_PATH,
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf",
        "C:/Windows/Fonts/arialbd.ttf", "arialbd.ttf", SECTION_FONT_PATH,
    ):
        font = _try_load_font(path, size)
        if font:
            return font
    return get_section_font(size)


def get_b50_number_font(size: int) -> ImageFont.FreeTypeFont:
    """Tabular-looking face for scores and rating values."""
    for path in (
        B50_NUMBER_FONT_PATH,
        "C:/Windows/Fonts/bahnschrift.ttf", "bahnschrift.ttf", SECTION_FONT_PATH,
    ):
        font = _try_load_font(path, size)
        if font:
            return font
    return get_section_font(size)


# ===================================================================
#  数字贴图拼合分数
# ===================================================================

def _draw_score_digits(canvas: Image.Image, score_str: str, x: int, y: int, target_h: int,
                       v_stretch: float = 1.0) -> int:
    """用 AchieveNum 贴图拼出分数，返回总宽度。
       v_stretch: 纵向拉伸倍率（只拉高不拉宽）。"""
    _load_digits()
    orig_h = 120  # 原始贴图高度
    scale = target_h / orig_h
    total_w = 0

    for ch in score_str:
        if ch == ",":
            img = _DIGIT_IMAGES.get("comma")
        elif ch in _DIGIT_IMAGES:
            img = _DIGIT_IMAGES[ch]
        else:
            continue

        if img is None:
            continue

        w = int(img.width * scale)
        h = int(img.height * scale * v_stretch)
        if w > 0 and h > 0:
            scaled = img.resize((w, h), Image.LANCZOS)
            canvas.paste(scaled, (x + total_w, y), scaled)
            total_w += w + int(2 * scale)  # 字符间距

    return total_w


# ===================================================================
#  文字工具
# ===================================================================

def truncate_text_by_width(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, ellipsis: str = "...",
) -> str:
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tw = dummy.textbbox((0, 0), text, font=font)[2]
    if tw <= max_width:
        return text
    ew = dummy.textbbox((0, 0), ellipsis, font=font)[2]
    avail = max_width - ew
    result = ""
    for c in text:
        if dummy.textbbox((0, 0), result + c, font=font)[2] > avail:
            break
        result += c
    return result + ellipsis


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str,
    font: ImageFont.FreeTypeFont, fill: Tuple[int, int, int],
    shadow_color: Tuple[int, int, int] = (0, 0, 0),
    shadow_offset: int = 1,
) -> None:
    """带投影的文字 —— 先画暗色偏移再画本体，保证玻璃上可读性"""
    x, y = xy
    draw.text((x + shadow_offset, y + shadow_offset),
              text, fill=shadow_color, font=font)
    draw.text((x, y + shadow_offset), text, fill=shadow_color, font=font)
    draw.text((x + shadow_offset, y), text, fill=shadow_color, font=font)
    draw.text((x, y), text, fill=fill, font=font)


# ===================================================================
#  难度 / 颜色
# ===================================================================

def format_level_value(level_str: str) -> str:
    if not level_str or level_str == "Unknown":
        return "Unknown"
    try:
        clean = "".join(filter(lambda c: c.isdigit()
                        or c == ".", str(level_str)))
        return f"{float(clean):.1f}" if clean else str(level_str)
    except Exception:
        return str(level_str)


def get_card_colors(level_index: int) -> Tuple[Tuple, Tuple, Tuple]:
    if level_index >= 4:
        return GLASS_BG_ULTIMA, GLOW_COLOR_ULTIMA, BORDER_COLOR_ULTIMA
    if level_index == 3:
        return GLASS_BG_MASTER, GLOW_COLOR_MASTER, BORDER_COLOR_MASTER
    if level_index == 2:
        return GLASS_BG_EXPERT, GLOW_COLOR_EXPERT, BORDER_COLOR_EXPERT
    return GLASS_BG_DEFAULT, GLOW_COLOR_DEFAULT, BORDER_COLOR_DEFAULT


# ===================================================================
#  圆角矩形
# ===================================================================

def _draw_rrect(
    draw: ImageDraw.ImageDraw, xy: Tuple, radius: int,
    fill=None, outline=None, width: int = 1,
) -> None:
    x1, y1, x2, y2 = xy
    if fill:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.pieslice([x1, y1, x1 + 2 * radius, y1 +
                      2 * radius], 180, 270, fill=fill)
        draw.pieslice([x2 - 2 * radius, y1, x2, y1 +
                      2 * radius], 270, 0, fill=fill)
        draw.pieslice([x1, y2 - 2 * radius, x1 + 2 *
                      radius, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - 2 * radius, y2 - 2 *
                      radius, x2, y2], 0, 90, fill=fill)
    if outline and width > 0:
        draw.arc([x1, y1, x1 + 2 * radius, y1 + 2 * radius],
                 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y1, x2, y1 + 2 * radius],
                 270, 0, fill=outline, width=width)
        draw.arc([x1, y2 - 2 * radius, x1 + 2 * radius, y2],
                 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y2 - 2 * radius, x2, y2],
                 0, 90, fill=outline, width=width)
        draw.line([x1 + radius, y1, x2 - radius, y1],
                  fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2],
                  fill=outline, width=width)
        draw.line([x1, y1 + radius, x1, y2 - radius],
                  fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius],
                  fill=outline, width=width)


def _draw_rrect_small(
    draw: ImageDraw.ImageDraw, xy: Tuple, radius: int,
    fill=None, outline=None, width: int = 1,
) -> None:
    """小元素用（rank badge 等）"""
    x1, y1, x2, y2 = xy
    if fill:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.pieslice([x1, y1, x1 + 2 * radius, y1 +
                      2 * radius], 180, 270, fill=fill)
        draw.pieslice([x2 - 2 * radius, y1, x2, y1 +
                      2 * radius], 270, 0, fill=fill)
        draw.pieslice([x1, y2 - 2 * radius, x1 + 2 *
                      radius, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - 2 * radius, y2 - 2 *
                      radius, x2, y2], 0, 90, fill=fill)
    if outline:
        draw.arc([x1, y1, x1 + 2 * radius, y1 + 2 * radius],
                 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y1, x2, y1 + 2 * radius],
                 270, 0, fill=outline, width=width)
        draw.arc([x1, y2 - 2 * radius, x1 + 2 * radius, y2],
                 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y2 - 2 * radius, x2, y2],
                 0, 90, fill=outline, width=width)
        draw.line([x1 + radius, y1, x2 - radius, y1],
                  fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2],
                  fill=outline, width=width)
        draw.line([x1, y1 + radius, x1, y2 - radius],
                  fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius],
                  fill=outline, width=width)


# ===================================================================
#  玻璃卡片 Surface（带多层霓虹发光边框）
# ===================================================================

def _make_glass_card(bg_color: Tuple, glow_color: Tuple, border_color: Tuple) -> Image.Image:
    w, h = CARD_WIDTH, CARD_HEIGHT
    r = CARD_CORNER_RADIUS
    surf = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(surf)

    # 多层外发光 — 模拟 neon glow
    for i in range(4, 0, -1):
        alpha = max(20, glow_color[3] - i * 25)
        g = glow_color[:3] + (alpha,)
        _draw_rrect(d, (4 - i, 4 - i, w + 4 + i, h + 4 + i),
                    r + i, fill=None, outline=g, width=2)

    # 实色描边
    _draw_rrect(d, (4, 4, w + 4, h + 4), r + 1,
                fill=None, outline=border_color, width=2)
    # 玻璃填充
    _draw_rrect(d, (5, 5, w + 3, h + 3), r, fill=bg_color, outline=None)

    return surf


# ===================================================================
#  评级徽章
# ===================================================================

def _draw_rank_badge(draw: ImageDraw.ImageDraw, x: int, center_y: int, rank: str) -> None:
    """纯文字评级，右对齐，垂直居中于 center_y"""
    font = get_song_font(FZ_BADGE + 8)  # 放大补偿去掉的徽章底
    tw = draw.textbbox((0, 0), rank, font=font)[2]
    th = draw.textbbox((0, 0), rank, font=font)[3]
    draw.text((x - tw, center_y - th // 2), rank, fill=COLOR_WHITE, font=font)


# ===================================================================
#  单卡片绘制
# ===================================================================

def _draw_card(
    canvas: Image.Image, score: "Score", card_x: int, card_y: int, idx: int
) -> None:
    glass_bg, glow, border = get_card_colors(score.level_index)

    # 玻璃 Surface
    card_surf = _make_glass_card(glass_bg, glow, border)
    canvas.paste(card_surf, (card_x - 4, card_y - 4), card_surf)

    draw = ImageDraw.Draw(canvas)

    # ---- 曲绘 (左侧，垂直居中) ----
    jacket = score.load_jacket_image()
    j_lg = jacket.resize((JACKET_SIZE, JACKET_SIZE), Image.LANCZOS)
    jy = card_y + (CARD_HEIGHT - JACKET_SIZE) // 2
    canvas.paste(j_lg, (card_x + JACKET_X, jy))
    # 曲绘光边框
    draw.rectangle(
        [card_x + JACKET_X - 2, jy - 2,
         card_x + JACKET_X + JACKET_SIZE + 2, jy + JACKET_SIZE + 2],
        outline=border[:3] + (100,), width=3,
    )
    # 曲绘右下角序号 — 毛玻璃半透明标识
    rank_num = f"{idx + 1:02d}"
    rn_f = get_song_font(28)
    rn_tw = draw.textbbox((0, 0), rank_num, font=rn_f)[2]
    rn_th = draw.textbbox((0, 0), rank_num, font=rn_f)[3]
    rn_pad_x = 10
    rn_pad_y = 6
    rn_margin = 4
    badge_w = rn_tw + rn_pad_x * 2
    badge_h = rn_th + rn_pad_y * 2
    rn_x = card_x + JACKET_X + JACKET_SIZE - badge_w - rn_margin
    rn_y = jy + JACKET_SIZE - badge_h - rn_margin

    # 毛玻璃 badge surface
    glass_badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    gb_d = ImageDraw.Draw(glass_badge)
    # 半透黑底 — 微透曲绘
    gb_d.rectangle((0, 0, badge_w, badge_h), fill=(0, 0, 0, 140))
    # 底部稀有度色条
    accent_h = 4
    gb_d.rectangle((0, badge_h - accent_h, badge_w, badge_h),
                   fill=border[:3] + (160,))
    # 白字
    gb_d.text((rn_pad_x, rn_pad_y), rank_num,
              fill=(255, 255, 255, 235), font=rn_f)
    canvas.paste(glass_badge, (rn_x, rn_y), glass_badge)

    # ---- 右侧文字区 ----
    text_x = card_x + JACKET_X + JACKET_SIZE + 32
    text_right = card_x + CARD_WIDTH - 28

    # ---- Row 1: 定数 [胶囊]  Rating  [评级] ----
    y1 = card_y + 24
    # 极小粗体
    lv_f = get_song_font(FZ_CARD_RATING - 12)
    lv_text = score.final_level
    lv_w = int(draw.textlength(lv_text, font=lv_f))
    lv_h = draw.textbbox((0, 0), lv_text, font=lv_f)[3]

    # 定数胶囊
    lv_pad_x, lv_pad_y = 8, 4
    lv_cap_w = lv_w + lv_pad_x * 2
    lv_cap_h = lv_h + lv_pad_y * 2
    lv_cap_x1 = text_x - lv_pad_x
    lv_cap_y1 = y1 + (lv_h - lv_cap_h) // 2 + lv_pad_y // 2
    lv_cap_x2 = lv_cap_x1 + lv_cap_w
    lv_cap_y2 = lv_cap_y1 + lv_cap_h
    _draw_rrect_small(draw, (lv_cap_x1, lv_cap_y1, lv_cap_x2, lv_cap_y2),
                      radius=8, fill=(160, 205, 255, 60), outline=(160, 205, 255, 120), width=1)
    draw.text((text_x, y1), lv_text, fill=(20, 22, 35), font=lv_f)

    # 定数胶囊中心轴（统一对齐基准）
    lv_center_y = lv_cap_y1 + lv_cap_h // 2

    # rating 粗体，紧贴胶囊，垂直居中于定数轴
    rat_f = get_song_font(FZ_CARD_RATING - 12)
    rat_text = f"{score.rating_floor:.2f}"
    rat_h = draw.textbbox((0, 0), rat_text, font=rat_f)[3]
    rat_gap = 5
    rat_x = lv_cap_x2 + rat_gap
    draw.text((rat_x, lv_center_y - rat_h // 2), rat_text,
              fill=(210, 215, 235), font=rat_f)

    # 评级标识，垂直居中于定数轴
    _draw_rank_badge(draw, text_right, lv_center_y, score.rank)

    # ---- 分割线 ----
    sep_y = y1 + lv_f.size + 18
    draw.line([text_x, sep_y, text_right, sep_y],
              fill=(255, 255, 255, 45), width=2)

    # ---- Row 2: 歌曲名 ----
    y2 = sep_y + 16
    sf = get_song_font(FZ_SONG)
    song = truncate_text_by_width(score.song_name, sf, text_right - text_x)
    _draw_text_with_shadow(draw, (text_x, y2), song, sf, COLOR_WHITE)

    # ---- 分割线 ----
    sep2_y = y2 + sf.size + 16
    draw.line([text_x, sep2_y, text_right, sep2_y],
              fill=(255, 255, 255, 35), width=2)

    # ---- Row 3: 分数 (粗体，自适应不溢出) ----
    y3 = sep2_y + 16
    score_str = f"{score.score:,}"
    score_fz = FZ_SCORE
    sc_f = get_song_font(score_fz)
    sw = draw.textbbox((0, 0), score_str, font=sc_f)[2]
    avail_w = text_right - text_x
    while sw > avail_w and score_fz > 42:
        score_fz -= 2
        sc_f = get_song_font(score_fz)
        sw = draw.textbbox((0, 0), score_str, font=sc_f)[2]
    _draw_text_with_shadow(draw, (text_x, y3), score_str, sc_f, COLOR_WHITE)


# ===================================================================
#  顶部栏
# ===================================================================

def _draw_top_bar(canvas: Image.Image, name: str, label: str, val: float,
                  bar_w: int = CANVAS_WIDTH, bar_h: int = TOP_BAR_HEIGHT,
                  name_x: int = 48, fz_player: int = FZ_PLAYER,
                  rat_h: int = 68, title_fz: int = FZ_TITLE + 8,
                  light_text: bool = True) -> None:
    """游戏 UI 风格顶部栏：姓名 + Rating + 右侧标题。
       light_text=True 用白色/金色(深底)，False 用深色(浅底)。"""
    draw = ImageDraw.Draw(canvas)
    c_name = COLOR_WHITE if light_text else (30, 32, 45)
    c_rtg = COLOR_GOLD if light_text else (80, 60, 20)
    c_title = COLOR_GOLD  # 标题始终金色
    shadow_c = (0, 0, 0) if light_text else (200, 200, 200)

    # 玻璃背景
    bar = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    _draw_rrect(bd, (0, 10, bar_w, bar_h - 10), radius=22,
                fill=COLOR_TOP_BAR_BG, outline=(255, 255, 255, 20), width=2)
    canvas.paste(bar, (0, 0), bar)

    # 左侧：昵称: XXX  Rating: XX.XX
    lbl_f = get_song_font(fz_player)  # 标签粗体与昵称同字号
    nf = get_song_font(fz_player)  # 昵称用 New Rodin 游戏粗体

    # 计算共同基线：取两字体最大高度居中
    lbl_n = "昵称: "
    lw_n = draw.textbbox((0, 0), lbl_n, font=lbl_f)[2]
    lh_n = draw.textbbox((0, 0), lbl_n, font=lbl_f)[3]
    nh = draw.textbbox((0, 0), name, font=nf)[3]
    max_h = max(lh_n, nh)
    lbl_y = (bar_h - max_h) // 2 + (max_h - lh_n) // 2
    name_y = (bar_h - max_h) // 2 + (max_h - nh) // 2

    draw.text((name_x, lbl_y), lbl_n, fill=c_rtg, font=lbl_f)
    # 实际昵称
    _draw_text_with_shadow(draw, (name_x + lw_n, name_y), name, nf, c_name, shadow_color=shadow_c)

    # Rating 数字贴图
    rat_h_actual = rat_h
    rat_label = "  Rating: "
    rl_f = get_song_font(fz_player)  # Rating 标签粗体
    rl_w = draw.textbbox((0, 0), rat_label, font=rl_f)[2]
    name_w = draw.textbbox((0, 0), name, font=nf)[2]
    rat_x = name_x + lw_n + name_w + 40
    rat_lbl_x = rat_x
    rat_y = (bar_h - rat_h_actual) // 2
    draw.text((rat_lbl_x, rat_y - 4), rat_label, fill=c_rtg, font=rl_f)
    drawn_w = _draw_rating_digits(canvas, val, rat_lbl_x + rl_w, rat_y, rat_h_actual)
    if drawn_w == 0:
        rtf = get_font(rat_h_actual)
        rth = draw.textbbox((0, 0), f"{val:.2f}", font=rtf)[3]
        draw.text((rat_lbl_x + rl_w, (bar_h - rth) // 2), f"{val:.2f}", fill=c_rtg, font=rtf)

    # 右侧标题 (粗体，兼容中英文)
    tf = get_song_font(title_fz)
    tw = draw.textbbox((0, 0), label, font=tf)[2]
    th = draw.textbbox((0, 0), label, font=tf)[3]
    pad_x = 36
    pad_y = 22
    bx1 = bar_w - tw - pad_x * 2 - 24
    bx2 = bar_w - 24
    by1 = (bar_h - th - pad_y * 2) // 2
    by2 = by1 + th + pad_y * 2

    _draw_rrect_small(draw, (bx1, by1, bx2, by2), radius=18,
                      fill=(30, 32, 50, 220), outline=c_rtg + (180,), width=2)
    _draw_rrect_small(draw, (bx1 + 3, by1 + 3, bx2 - 3, by2 - 3), radius=15,
                      fill=None, outline=c_rtg + (60,), width=1)
    draw.text((bx1 + pad_x, by1 + pad_y), label, fill=c_title, font=tf)

    # 底部霓虹线
    for i in range(3):
        a = 60 - i * 18
        draw.line([20, bar_h - 3 - i, bar_w - 20, bar_h - 3 - i],
                  fill=c_rtg + (max(10, a),), width=1)


# ===================================================================
#  章节标题
# ===================================================================

def _draw_section_title(canvas: Image.Image, x: int, y: int, text: str) -> None:
    """章节标题：暗色调 + 底部装饰线"""
    draw = ImageDraw.Draw(canvas)
    font = get_section_font(FZ_SECTION + 6)
    tw = draw.textbbox((0, 0), text, font=font)[2]
    th = draw.textbbox((0, 0), text, font=font)[3]

    # 文字 + 投影
    _draw_text_with_shadow(draw, (x, y), text, font, (160, 155, 145))

    # 底部装饰条：暗色装饰线
    line_y = y + th + 10
    draw.line([x, line_y, x + tw, line_y], fill=(180, 175, 165, 60), width=3)
    draw.line([x, line_y + 3, x + tw, line_y + 3],
              fill=(180, 175, 165, 30), width=1)


# ===================================================================
#  背景处理：高斯模糊 + 暗化 = 景深效果
# ===================================================================

def _prepare_background(canvas: Image.Image) -> None:
    """保留原始背景，不做任何处理"""
    pass


# ===================================================================
#  B30 主图
# ===================================================================

def create_b30_style_image(
    player: "Player", scores: List["Score"], b30_rating: float,
    save_path: Path, bg_image_path: Optional[Path] = None,
) -> Path:
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (CANVAS_WIDTH, CANVAS_HEIGHT), Image.LANCZOS
            ).convert("RGBA")
        except Exception:
            canvas = Image.new(
                "RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (18, 20, 35, 255))
    else:
        canvas = Image.new(
            "RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (18, 20, 35, 255))

    # B30 deliberately reuses the B50 contact-sheet design so both commands
    # have the same masthead, paper texture, score cards, and reading order.
    _prepare_b50_background(canvas)
    _draw_b50_header(
        canvas, player, b30_rating, b30_rating, 0.0,
        report_title="BEST 30", summary_title="B30",
        old_label="BEST / 30", new_label="",
    )

    panel_y = B50_HEADER_H + 10
    panel_h = B50_SECTION_HEADER_H + 6 * B50_CARD_H + 5 * B50_CARD_GAP_Y + 24
    _draw_b50_section_panel(
        canvas, panel_y, panel_h, "BEST 30", len(scores), b30_rating,
        B50_OLD_ACCENT,
    )
    card_start_y = panel_y + B50_SECTION_HEADER_H
    for idx, score in enumerate(scores[:30]):
        row, col = divmod(idx, CARD_COLUMNS)
        cx = B50_CARD_START_X + col * (B50_CARD_W + B50_CARD_GAP_X)
        cy = card_start_y + row * (B50_CARD_H + B50_CARD_GAP_Y)
        _draw_b50_card(canvas, score, cx, cy, idx)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = append_image_credit(canvas, get_b50_ui_font(22))
    canvas.save(save_path, quality=95)
    return save_path


# ===================================================================
#  API / 数据模型
# ===================================================================

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

    def _load_collection_image(
        self, collection_type: str, collection_id: Optional[int], timeout: int = 5,
    ) -> Optional[Image.Image]:
        if collection_id is None:
            return None
        headers = {
            "User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/",
            "Accept": "image/png,image/jpeg,image/webp,*/*",
        }
        for base in ASSETS_BASE_URLS:
            try:
                response = requests.get(
                    f"{base}/chunithm/{collection_type}/{collection_id}.png",
                    headers=headers, timeout=timeout, allow_redirects=True, verify=False,
                )
                if response.status_code != 200 or len(response.content) <= 100:
                    continue
                return Image.open(io.BytesIO(response.content)).convert("RGBA")
            except Exception:
                continue
        return None

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
        urls = []
        for base in ASSETS_BASE_URLS:
            urls.extend([
                f"{base}/chunithm/jacket/{jid}.png",
                f"{base}/chunithm/jacket/{jid}.jpg",
                f"{base}/chunithm/jacket/{jid:04d}.png",
            ])
        hdrs = {"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/",
                "Accept": "image/png,image/jpeg,image/webp,*/*"}
        for url in urls:
            try:
                resp = requests.get(
                    url, headers=hdrs, timeout=5, allow_redirects=True, verify=False)
                if resp.status_code == 200 and len(resp.content) > 100:
                    try:
                        img = Image.open(io.BytesIO(resp.content)).resize(
                            (JACKET_SIZE, JACKET_SIZE), Image.LANCZOS)
                        self.jacket_image = img
                        return img
                    except Exception:
                        continue
            except Exception:
                continue
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

PUSH_W, PUSH_H = 1800, 780
PUSH_TOP = 160
PUSH_JACKET = 510


def create_push_score_image(
    player: Player, score: Score, save_path: Path,
    bg_image_path: Optional[Path] = None, title: str = "随机推分",
) -> Path:
    """Render a single target in the same print-sheet style as B30/B50."""
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (PUSH_W, PUSH_H), Image.LANCZOS).convert("RGBA")
        except Exception:
            canvas = Image.new("RGBA", (PUSH_W, PUSH_H), (18, 20, 35, 255))
    else:
        canvas = Image.new("RGBA", (PUSH_W, PUSH_H), (18, 20, 35, 255))

    _prepare_b50_background(canvas)
    draw = ImageDraw.Draw(canvas)
    paper = (238, 235, 225)
    ink = B50_TEXT
    difficulty, accent = B50_DIFFICULTY_META.get(
        max(0, min(5, score.level_index)), B50_DIFFICULTY_META[3],
    )

    # Masthead: same flat ink block and asymmetric accent bars as B30/B50.
    header = (24, 18, PUSH_W - 24, PUSH_TOP + 14)
    draw.rectangle(header, fill=ink)
    draw.rectangle((header[0], header[1], header[0] + 12, header[3]), fill=accent)
    draw.rectangle((header[0], header[3] - 10, 1180, header[3]), fill=B50_OLD_ACCENT)
    draw.rectangle((1180, header[3] - 10, header[2], header[3]), fill=B50_NEW_ACCENT)
    draw.text((50, 34), "CHUNITHM  /  SINGLE CHART", fill=B50_NEW_ACCENT,
              font=get_b50_ui_font(24))
    player_font = get_song_font(48)
    player_name = truncate_text_by_width(player.name, player_font, 850)
    draw.text((48, 70), player_name, fill=paper, font=player_font)

    draw.rectangle((1010, 36, 1018, 145), fill=B50_OLD_ACCENT)
    draw.text((1050, 31), "RATING", fill=(151, 154, 158),
              font=get_b50_ui_font(23))
    draw.text((1045, 61), f"{player.rating_floor:.2f}", fill=paper,
              font=get_b50_number_font(67))

    label_panel = (1470, 32, PUSH_W - 40, 150)
    draw.rectangle(label_panel, fill=paper)
    label_font = get_song_font(42)
    label = truncate_text_by_width(title, label_font, label_panel[2] - label_panel[0] - 36)
    label_box = draw.textbbox((0, 0), label, font=label_font)
    label_x = label_panel[0] + (label_panel[2] - label_panel[0] - label_box[2]) // 2
    label_y = label_panel[1] + (label_panel[3] - label_panel[1] - label_box[3]) // 2
    draw.text((label_x, label_y), label, fill=ink, font=label_font)

    # Main single-chart panel.
    panel = (24, PUSH_TOP + 30, PUSH_W - 24, PUSH_H - 24)
    draw.rectangle(panel, fill=(222, 218, 207), outline=ink, width=4)

    jacket_x, jacket_y, jacket_size = 44, PUSH_TOP + 50, PUSH_JACKET
    jacket = ImageOps.fit(
        score.load_jacket_image().convert("RGBA"),
        (jacket_size, jacket_size),
        method=Image.LANCZOS,
    )
    canvas.paste(jacket, (jacket_x, jacket_y), jacket)
    draw.rectangle(
        (jacket_x - 2, jacket_y - 2,
         jacket_x + jacket_size + 2, jacket_y + jacket_size + 2),
        outline=ink,
        width=4,
    )
    draw.rectangle(
        (jacket_x, jacket_y + jacket_size - 14,
         jacket_x + jacket_size, jacket_y + jacket_size),
        fill=accent,
    )

    text_x = 596
    text_right = PUSH_W - 48
    badge_width = 138
    draw.rectangle((text_x, 214, text_x + badge_width, 258), fill=accent)
    diff_font = get_b50_ui_font(26)
    diff_box = draw.textbbox((0, 0), difficulty, font=diff_font)
    draw.text(
        (text_x + (badge_width - diff_box[2]) // 2, 219),
        difficulty,
        fill=paper if score.level_index != 4 else ink,
        font=diff_font,
    )
    const_source = "OFFICIAL" if score.level_is_constant else "EST."
    draw.text((text_x + badge_width + 18, 220),
              f"CHART {score.final_level}  /  {const_source}",
              fill=B50_MUTED, font=get_b50_ui_font(25))

    title_font = get_song_font(50)
    title_lines = _b50_title_lines(score.song_name, title_font, text_right - text_x)
    for line_index, line in enumerate(title_lines):
        draw.text((text_x, 284 + line_index * 58), line, fill=ink, font=title_font)

    score_rule_y = 416 if len(title_lines) > 1 else 384
    draw.line((text_x, score_rule_y, text_right, score_rule_y), fill=ink, width=3)
    draw.text((text_x, score_rule_y + 18), "PLAY SCORE", fill=B50_MUTED,
              font=get_b50_ui_font(25))
    score_text = f"{score.score:,}"
    score_font = get_b50_number_font(96)
    rank_left = _draw_b50_rank_badge(canvas, text_right, score_rule_y + 62, score.rank)
    while (
        score_font.size > 66
        and draw.textbbox((0, 0), score_text, font=score_font)[2]
        > rank_left - text_x - 24
    ):
        score_font = get_b50_number_font(score_font.size - 2)
    draw.text((text_x, score_rule_y + 47), score_text, fill=ink, font=score_font)

    rating_top = 620
    draw.rectangle((text_x, rating_top, text_right, 714), fill=ink)
    draw.rectangle((text_x, rating_top, text_x + 12, 714), fill=accent)
    draw.text((text_x + 34, rating_top + 30), "CHART RT", fill=paper,
              font=get_b50_ui_font(27))
    rating_text = f"{score.rating_floor:.2f}"
    rating_font = get_b50_number_font(59)
    rating_width = draw.textbbox((0, 0), rating_text, font=rating_font)[2]
    draw.text((text_right - rating_width - 28, rating_top + 13), rating_text,
              fill=paper, font=rating_font)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = append_image_credit(canvas, get_b50_ui_font(22))
    canvas.save(save_path, quality=95)
    return save_path


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
        # The developer endpoint may return SimpleScore rows without numeric score/RT.
        complete = [item for item in raw if "score" in item and "rating" in item]
        if complete:
            return [Score(item) for item in complete]
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
    score.load_jacket_image()
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"push_{user_id}_{score.id}.png"
    return create_push_score_image(player, score, save, bg), score


# ===================================================================
#  B50
# ===================================================================

B50_W, B50_H = 3000, 3160

B50_MARGIN = 24
B50_HEADER_H = 270
B50_CARD_START_X = 40
B50_CARD_W = 568
B50_CARD_H = 236
B50_CARD_GAP_X = 22
B50_CARD_GAP_Y = 22
B50_SECTION_HEADER_H = 112
SCORE_LIST_PAGE_SIZE = 50
B50_OLD_ACCENT = (239, 79, 45)
B50_NEW_ACCENT = (16, 177, 191)
B50_TEXT = (20, 21, 23)
B50_MUTED = (102, 101, 96)
B50_CARD_SURFACES: Dict[int, Image.Image] = {}

B50_DIFFICULTY_META = {
    0: ("BAS", (21, 128, 77)),
    1: ("ADV", (174, 107, 0)),
    2: ("EXP", (211, 47, 55)),
    3: ("MAS", (111, 57, 159)),
    4: ("ULT", (20, 21, 23)),
    5: ("WE", (0, 119, 143)),
}


def _prepare_b50_background(canvas: Image.Image) -> None:
    """Lay down a flat, two-ink zine texture with no gradients or glow."""
    source = canvas.copy().convert("RGB")
    paper = (232, 228, 216, 255)
    width, height = canvas.size
    canvas.paste(paper, (0, 0, width, height))

    # The bundled game background only contributes a barely visible paper
    # texture. It is intentionally not legible as a second background image.
    source = ImageEnhance.Color(source).enhance(0.0)
    source = ImageEnhance.Contrast(source).enhance(0.72).convert("RGBA")
    source.putalpha(10)
    canvas.alpha_composite(source)

    draw = ImageDraw.Draw(canvas)
    ink = (20, 21, 23)
    # Printer's crop marks and sparse halftone corners make the sheet feel
    # authored and physical without competing with fifty jacket artworks.
    for x, y in ((18, 18), (width - 18, 18), (18, height - 18),
                 (width - 18, height - 18)):
        sx = 1 if x < width // 2 else -1
        sy = 1 if y < height // 2 else -1
        draw.line((x, y, x + sx * 70, y), fill=ink, width=3)
        draw.line((x, y, x, y + sy * 70), fill=ink, width=3)
    for row in range(7):
        for col in range(15 - row):
            x = width - 38 - col * 22
            y = height - 38 - row * 22
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(117, 113, 104))


def _make_b50_character_avatar(source: Image.Image, size: int) -> Image.Image:
    """Turn the transparent full-body character art into a readable bust avatar."""
    source = source.convert("RGBA")
    alpha_bbox = source.getchannel("A").getbbox()
    if alpha_bbox:
        source = source.crop(alpha_bbox)
    return ImageOps.fit(
        source, (size, size), method=Image.LANCZOS,
        centering=(0.5, 0.24),
    )


def _draw_b50_trophy(
    canvas: Image.Image, player: "Player", x: int, y: int, max_width: int,
) -> None:
    """Render the equipped title as either its image asset or a colored title strip."""
    d = ImageDraw.Draw(canvas)
    ink = (20, 21, 23)
    paper = (238, 235, 225)
    trophy_image = player.load_trophy_image()
    if trophy_image is not None:
        image = trophy_image.copy().convert("RGBA")
        bbox = image.getchannel("A").getbbox()
        if bbox:
            image = image.crop(bbox)
        # Image trophies already contain their official frame and lettering.
        # Preserve that artwork instead of wrapping it in the report's own badge.
        image.thumbnail((max_width, 48), Image.LANCZOS)
        canvas.paste(image, (x, y), image)
        return

    title = player.trophy_name.strip()
    if not title:
        return
    font = get_font(28)
    title = truncate_text_by_width(title, font, max_width - 28)
    text_box = d.textbbox((0, 0), title, font=font)
    width = min(max_width, text_box[2] - text_box[0] + 28)
    height = 42
    color = player.trophy_color
    palette = {
        "normal": ((224, 222, 214), ink),
        "copper": ((186, 117, 72), paper),
        "silver": ((188, 196, 205), ink),
        "gold": ((224, 167, 47), ink),
        "platinum": ((205, 218, 220), ink),
    }
    if color == "rainbow":
        stops = (
            (239, 105, 91), (247, 184, 64), (225, 218, 112),
            (70, 185, 151), (67, 157, 207), (151, 105, 205),
        )
        for offset in range(width):
            scaled = offset * (len(stops) - 1) / max(1, width - 1)
            index = min(int(scaled), len(stops) - 2)
            blend = scaled - index
            fill = tuple(
                round(stops[index][channel] * (1 - blend) +
                      stops[index + 1][channel] * blend)
                for channel in range(3)
            )
            d.line((x + offset, y, x + offset, y + height), fill=fill)
        text_color = ink
    else:
        fill, text_color = palette.get(color, palette["normal"])
        d.rectangle((x, y, x + width, y + height), fill=fill)
    d.rectangle((x, y, x + width, y + height), outline=paper, width=2)
    d.text((x + 13, y + 2), title, font=font, fill=text_color)


def _draw_b50_header(
    canvas: Image.Image, player: "Player", rating: float,
    old_average: float, new_average: float,
    report_title: str = "BEST 50", summary_title: str = "B50",
    old_label: str = "OLD / 30", new_label: str = "NEW / 20",
) -> None:
    """Draw a flat, asymmetric masthead inspired by a printed track list."""
    d = ImageDraw.Draw(canvas)
    panel = (B50_MARGIN, 18, B50_W - B50_MARGIN, 250)
    ink = (20, 21, 23)
    paper = (238, 235, 225)
    d.rectangle(panel, fill=ink)
    d.rectangle((panel[0], panel[1], panel[0] + 14, panel[3]), fill=B50_NEW_ACCENT)
    d.rectangle((panel[0], panel[3] - 12, 1750, panel[3]), fill=B50_OLD_ACCENT)
    d.rectangle((1750, panel[3] - 12, panel[2], panel[3]), fill=B50_NEW_ACCENT)

    character = player.load_character_image()
    player_x = 252 if character is not None else 62
    if character is not None:
        icon_size = 158
        icon_x, icon_y = 62, 48
        d.rectangle((icon_x - 5, icon_y - 5,
                     icon_x + icon_size + 4, icon_y + icon_size + 4), fill=paper)
        avatar = _make_b50_character_avatar(character, icon_size)
        canvas.paste(avatar, (icon_x, icon_y), avatar)
        d.rectangle((icon_x - 5, icon_y - 5, icon_x + icon_size + 4, icon_y + icon_size + 4),
                    outline=paper, width=4)
        d.rectangle((icon_x - 10, icon_y + icon_size + 8,
                     icon_x + icon_size + 10, icon_y + icon_size + 14), fill=B50_OLD_ACCENT)

    d.text((player_x + 2, 38), f"CHUNITHM  /  {report_title}", fill=B50_NEW_ACCENT,
           font=get_b50_ui_font(29))
    player_font = get_song_font(63)
    player_name = truncate_text_by_width(player.name, player_font, 1160 - player_x)
    d.text((player_x, 78), player_name, fill=paper, font=player_font)
    _draw_b50_trophy(canvas, player, player_x, 169, 1160 - player_x)

    d.rectangle((1192, 36, 1200, 216), fill=B50_OLD_ACCENT)
    d.text((1250, 34), "RATING", fill=(151, 154, 158), font=get_b50_ui_font(26))
    rating_text = f"{rating:.2f}"
    d.text((1242, 72), rating_text, fill=paper, font=get_b50_number_font(116))

    summary_font = get_b50_ui_font(26)
    d.text((1800, 38), old_label, fill=B50_OLD_ACCENT, font=summary_font)
    d.text((1800, 78), f"{old_average:.2f}", fill=paper,
           font=get_b50_number_font(53))
    if new_label:
        d.text((2135, 38), new_label, fill=B50_NEW_ACCENT, font=summary_font)
        d.text((2135, 78), f"{new_average:.2f}", fill=paper,
               font=get_b50_number_font(53))

    d.rectangle((2510, 18, panel[2], 238), fill=paper)
    d.text((2548, 46), summary_title, fill=ink, font=get_section_font(120))


def _b50_card_surface(level_index: int) -> Image.Image:
    key = max(0, min(5, level_index))
    if key in B50_CARD_SURFACES:
        return B50_CARD_SURFACES[key]
    _, accent = B50_DIFFICULTY_META.get(key, B50_DIFFICULTY_META[3])
    surf = Image.new("RGBA", (B50_CARD_W, B50_CARD_H), (238, 235, 225, 255))
    d = ImageDraw.Draw(surf)
    d.rectangle((0, 0, B50_CARD_W - 1, B50_CARD_H - 1),
                outline=(20, 21, 23), width=3)
    d.rectangle((0, 0, 9, B50_CARD_H), fill=accent)
    d.rectangle((9, 0, B50_CARD_W, 6), fill=(20, 21, 23))
    B50_CARD_SURFACES[key] = surf
    return surf


def _b50_title_lines(
    text: str, font: ImageFont.FreeTypeFont, max_width: int,
) -> List[str]:
    """Fit a song title into at most two lines, preferring natural spaces."""
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


def _draw_b50_rank_badge(
    canvas: Image.Image, right_x: int, top_y: int, rank: str,
) -> int:
    """Draw a large, arcade-style result badge and return its left edge."""
    rank = rank.upper()
    font = get_b50_number_font(37)
    probe = ImageDraw.Draw(canvas)
    text_box = probe.textbbox((0, 0), rank, font=font, stroke_width=1)
    badge_w = max(88, text_box[2] - text_box[0] + 36)
    badge_h = 49
    left_x = right_x - badge_w

    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)

    if rank == "SSS+":
        # The top result gets the game's characteristic iridescent treatment.
        stops = (
            (250, 105, 95), (255, 196, 74), (245, 239, 126),
            (99, 215, 177), (94, 187, 240), (176, 125, 229),
        )
        for x in range(badge_w):
            scaled = x * (len(stops) - 1) / max(1, badge_w - 1)
            index = min(int(scaled), len(stops) - 2)
            blend = scaled - index
            color = tuple(
                round(stops[index][channel] * (1 - blend) +
                      stops[index + 1][channel] * blend)
                for channel in range(3)
            )
            bd.line((x, 0, x, badge_h - 1), fill=(*color, 255))
        text_fill = (15, 16, 19)
        text_stroke = (244, 240, 226)
    else:
        top_color, bottom_color, text_fill = {
            "SSS": ((255, 238, 159), (226, 158, 34), (42, 29, 5)),
            "SS+": ((250, 253, 255), (164, 183, 205), (25, 32, 43)),
            "SS": ((231, 235, 241), (143, 151, 165), (24, 27, 33)),
            "S+": ((255, 224, 176), (215, 130, 56), (48, 26, 8)),
            "S": ((235, 191, 125), (163, 99, 42), (45, 24, 9)),
        }.get(rank, ((214, 216, 217), (126, 130, 134), (20, 21, 23)))
        for y in range(badge_h):
            blend = y / max(1, badge_h - 1)
            color = tuple(
                round(top_color[channel] * (1 - blend) +
                      bottom_color[channel] * blend)
                for channel in range(3)
            )
            bd.line((0, y, badge_w - 1, y), fill=(*color, 255))
        text_stroke = (255, 250, 232)

    # Heavy keyline and offset shadow keep the badge legible at message scale.
    probe.rectangle((left_x + 4, top_y + 4, right_x + 4, top_y + badge_h + 4),
                    fill=(20, 21, 23))
    canvas.alpha_composite(badge, (left_x, top_y))
    probe.rectangle((left_x, top_y, right_x, top_y + badge_h),
                    outline=B50_TEXT, width=3)
    probe.line((left_x + 5, top_y + 5, right_x - 5, top_y + 5),
               fill=(255, 255, 255), width=2)

    tw = text_box[2] - text_box[0]
    th = text_box[3] - text_box[1]
    text_x = left_x + (badge_w - tw) // 2 - text_box[0]
    text_y = top_y + (badge_h - th) // 2 - text_box[1] - 1
    probe.text((text_x, text_y), rank, font=font, fill=text_fill,
               stroke_width=1, stroke_fill=text_stroke)
    return left_x


def _draw_b50_card(
    canvas: Image.Image, score: "Score", card_x: int, card_y: int, idx: int,
) -> None:
    """Draw one compact score card with a consistent reading order."""
    level_key = max(0, min(5, score.level_index))
    diff_label, accent = B50_DIFFICULTY_META.get(level_key, B50_DIFFICULTY_META[3])
    surface = _b50_card_surface(level_key)
    canvas.paste(surface, (card_x, card_y), surface)
    draw = ImageDraw.Draw(canvas)

    jacket_size = 184
    jacket_x = card_x + 22
    jacket_y = card_y + 26
    jacket = score.load_jacket_image().resize((jacket_size, jacket_size), Image.LANCZOS)
    canvas.paste(jacket, (jacket_x, jacket_y))
    draw.rectangle((jacket_x - 3, jacket_y - 3,
                    jacket_x + jacket_size + 2, jacket_y + jacket_size + 2),
                   outline=B50_TEXT, width=3)

    index_text = f"{idx + 1:02d}"
    index_font = get_b50_number_font(27)
    iw = draw.textbbox((0, 0), index_text, font=index_font)[2]
    draw.rectangle((jacket_x - 3, jacket_y - 3,
                    jacket_x + iw + 29, jacket_y + 39), fill=B50_TEXT)
    draw.text((jacket_x + 11, jacket_y + 1), index_text,
              fill=(238, 235, 225), font=index_font)

    text_x = card_x + 226
    text_right = card_x + B50_CARD_W - 18
    meta_font = get_b50_ui_font(23)
    diff_w = draw.textbbox((0, 0), diff_label, font=meta_font)[2]
    diff_box_w = diff_w + 18
    draw.rectangle((text_x, card_y + 12, text_x + diff_box_w, card_y + 47), fill=accent)
    draw.text((text_x + 9, card_y + 15), diff_label,
              fill=(238, 235, 225), font=meta_font)
    level_label = "定数" if getattr(score, "level_is_constant", True) else "Lv."
    const_text = f"{level_label}  {score.final_level}"
    const_x = text_x + diff_box_w + 13
    const_max_w = text_right - const_x - 10
    const_font = get_song_font(22)
    if draw.textbbox((0, 0), const_text, font=const_font)[2] > const_max_w:
        # The numeric value matters more than its label when horizontal room is tight.
        const_text = str(score.final_level)
    draw.text((const_x, card_y + 15), const_text,
              fill=B50_TEXT, font=const_font)

    title_max_w = text_right - text_x
    title_font = get_song_font(27)
    title_lines = _b50_title_lines(score.song_name, title_font, title_max_w)
    for line_index, line in enumerate(title_lines):
        draw.text((text_x, card_y + 57 + line_index * 31), line,
                  font=title_font, fill=B50_TEXT)

    rank_left = _draw_b50_rank_badge(canvas, text_right, card_y + 121, score.rank)
    score_font = get_b50_number_font(50)
    score_text = f"{score.score:,}"
    score_max_w = rank_left - text_x - 12
    while draw.textbbox((0, 0), score_text, font=score_font)[2] > score_max_w:
        score_font = get_b50_number_font(score_font.size - 2)
    score_y = card_y + (124 if len(title_lines) > 1 else 117)
    draw.text((text_x, score_y), score_text, font=score_font, fill=B50_TEXT)

    rating_y = card_y + 178
    draw.rectangle((text_x, rating_y, text_right, card_y + 225), fill=B50_TEXT)
    rt_label_font = get_b50_ui_font(22)
    draw.text((text_x + 11, rating_y + 9), "RT",
              fill=(238, 235, 225), font=rt_label_font)
    rt_text = f"{score.rating_floor:.2f}"
    rt_font = get_b50_number_font(35)
    rt_w = draw.textbbox((0, 0), rt_text, font=rt_font)[2]
    draw.text((text_right - rt_w - 10, rating_y + 3), rt_text,
              fill=(238, 235, 225), font=rt_font)


def _draw_b50_section_panel(
    canvas: Image.Image, y: int, height: int, title: str,
    count: int, average: float, accent: Tuple[int, int, int],
) -> None:
    """Draw a hard-edged section header and contact-sheet frame."""
    draw = ImageDraw.Draw(canvas)
    paper = (238, 235, 225)
    draw.rectangle((B50_MARGIN, y, B50_W - B50_MARGIN, y + height),
                   fill=(222, 218, 207), outline=B50_TEXT, width=4)
    draw.rectangle((B50_MARGIN, y, B50_W - B50_MARGIN, y + 96), fill=B50_TEXT)
    draw.rectangle((B50_MARGIN, y, B50_MARGIN + 354, y + 96), fill=accent)
    title_font = get_section_font(44)
    draw.text((B50_MARGIN + 28, y + 20), title, font=title_font, fill=B50_TEXT)
    stat_font = get_b50_ui_font(28)
    stat_text = f"AVG.  {average:.2f}    /    {count:02d}"
    stat_w = draw.textbbox((0, 0), stat_text, font=stat_font)[2]
    draw.text((B50_W - B50_MARGIN - 28 - stat_w, y + 33), stat_text,
              fill=paper, font=stat_font)

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
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (B50_W, B50_H), Image.LANCZOS).convert("RGBA")
        except Exception:
            canvas = Image.new("RGBA", (B50_W, B50_H), (18, 20, 35, 255))
    else:
        canvas = Image.new("RGBA", (B50_W, B50_H), (18, 20, 35, 255))

    old_average = sum(s.rating_floor for s in old_scores) / len(old_scores) if old_scores else 0.0
    new_average = sum(s.rating_floor for s in new_scores) / len(new_scores) if new_scores else 0.0

    _prepare_b50_background(canvas)
    _draw_b50_header(canvas, player, b50_rating, old_average, new_average)

    old_panel_y = B50_HEADER_H + 10
    old_panel_h = B50_SECTION_HEADER_H + 6 * B50_CARD_H + 5 * B50_CARD_GAP_Y + 24
    _draw_b50_section_panel(
        canvas, old_panel_y, old_panel_h,
        "OLD 30", len(old_scores), old_average, B50_OLD_ACCENT,
    )
    csy = old_panel_y + B50_SECTION_HEADER_H
    for row in range(6):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(old_scores):
                break
            cx = B50_CARD_START_X + col * (B50_CARD_W + B50_CARD_GAP_X)
            cy = csy + row * (B50_CARD_H + B50_CARD_GAP_Y)
            _draw_b50_card(canvas, old_scores[idx], cx, cy, idx)

    new_panel_y = old_panel_y + old_panel_h + 20
    new_panel_h = B50_SECTION_HEADER_H + 4 * B50_CARD_H + 3 * B50_CARD_GAP_Y + 24
    _draw_b50_section_panel(
        canvas, new_panel_y, new_panel_h,
        "NEW 20", len(new_scores), new_average, B50_NEW_ACCENT,
    )
    csy = new_panel_y + B50_SECTION_HEADER_H
    for row in range(4):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(new_scores):
                break
            cx = B50_CARD_START_X + col * (B50_CARD_W + B50_CARD_GAP_X)
            cy = csy + row * (B50_CARD_H + B50_CARD_GAP_Y)
            _draw_b50_card(canvas, new_scores[idx], cx, cy, idx)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = append_image_credit(canvas, get_b50_ui_font(22))
    canvas.save(save_path, quality=95)
    return save_path


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
    """Render matched records as one or more B50-style contact sheets."""
    page_count = max(1, math.ceil(len(scores) / SCORE_LIST_PAGE_SIZE))
    timestamp = time.time_ns()
    paths: List[Path] = []
    background = Path("data") / "b30_assets" / "bg.png"
    average = sum(score.rating_floor for score in scores) / len(scores)

    for page_index in range(page_count):
        page_scores = scores[
            page_index * SCORE_LIST_PAGE_SIZE:(page_index + 1) * SCORE_LIST_PAGE_SIZE
        ]
        rows = max(1, math.ceil(len(page_scores) / CARD_COLUMNS))
        panel_y = B50_HEADER_H + 10
        panel_h = (
            B50_SECTION_HEADER_H + rows * B50_CARD_H
            + max(0, rows - 1) * B50_CARD_GAP_Y + 24
        )
        canvas_h = panel_y + panel_h + 24
        if background.exists():
            try:
                canvas = Image.open(background).resize(
                    (B50_W, canvas_h), Image.LANCZOS
                ).convert("RGBA")
            except Exception:
                canvas = Image.new("RGBA", (B50_W, canvas_h), (18, 20, 35, 255))
        else:
            canvas = Image.new("RGBA", (B50_W, canvas_h), (18, 20, 35, 255))

        _prepare_b50_background(canvas)
        _draw_b50_header(
            canvas, player, player.rating_floor, average, 0.0,
            report_title="SCORE LIST", summary_title=query.label,
            old_label="AVG RT", new_label="",
        )
        _draw_b50_section_panel(
            canvas, panel_y, panel_h,
            f"LEVEL {query.label}  {page_index + 1}/{page_count}",
            len(scores), average, B50_NEW_ACCENT,
        )
        cards_y = panel_y + B50_SECTION_HEADER_H
        for local_index, score in enumerate(page_scores):
            row, column = divmod(local_index, CARD_COLUMNS)
            x = B50_CARD_START_X + column * (B50_CARD_W + B50_CARD_GAP_X)
            y = cards_y + row * (B50_CARD_H + B50_CARD_GAP_Y)
            global_index = page_index * SCORE_LIST_PAGE_SIZE + local_index
            _draw_b50_card(canvas, score, x, y, global_index)

        path = output_dir / (
            f"chu_score_{user_id}_{query.label.replace('+', 'p')}_"
            f"{timestamp}_{page_index + 1}.png"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas = append_image_credit(canvas, get_b50_ui_font(22))
        canvas.save(path, quality=95)
        paths.append(path)
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
    score.load_jacket_image()
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"fu_{user_id}_{score.id}.png"
    return create_push_score_image(player, score, save, bg, title="装福"), score
