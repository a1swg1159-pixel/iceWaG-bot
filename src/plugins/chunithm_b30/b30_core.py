"""Chunithm B30/B50 图片生成 —— 玻璃拟态 + 景深 + 霓虹发光 游戏UI风格"""

import io
import math
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont


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


# ===================================================================
#  数字贴图拼合分数
# ===================================================================

def _draw_score_digits(canvas: Image.Image, score_str: str, x: int, y: int, target_h: int) -> int:
    """用 AchieveNum 贴图拼出分数，返回总宽度"""
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
        h = int(img.height * scale)
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

def _draw_rank_badge(draw: ImageDraw.ImageDraw, x: int, y: int, rank: str) -> None:
    """纯文字评级，右上角右对齐"""
    font = get_song_font(FZ_BADGE + 8)  # 放大补偿去掉的徽章底
    tw = draw.textbbox((0, 0), rank, font=font)[2]
    draw.text((x - tw, y - 2), rank, fill=COLOR_WHITE, font=font)


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
    # 曲绘右下角序号（半透明深底+白字）
    rank_num = f"{idx + 1:02d}"
    rn_f = get_font(24)
    rn_tw = draw.textbbox((0, 0), rank_num, font=rn_f)[2]
    rn_th = draw.textbbox((0, 0), rank_num, font=rn_f)[3]
    rn_pad = 6
    rn_x = card_x + JACKET_X + JACKET_SIZE - rn_tw - rn_pad * 2
    rn_y = jy + JACKET_SIZE - rn_th - rn_pad * 2
    _draw_rrect_small(draw, (rn_x, rn_y, rn_x + rn_tw + rn_pad * 2, rn_y + rn_th + rn_pad * 2),
                      radius=4, fill=(0, 0, 0, 160), outline=None)
    draw.text((rn_x + rn_pad, rn_y + rn_pad), rank_num,
              fill=(255, 255, 255, 220), font=rn_f)

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

    # rating 粗体，紧贴胶囊
    rat_f = get_song_font(FZ_CARD_RATING - 12)
    rat_text = f"{score.rating_floor:.2f}"
    rat_h = draw.textbbox((0, 0), rat_text, font=rat_f)[3]
    rat_gap = 5
    rat_x = lv_cap_x2 + rat_gap
    text_baseline = y1 + lv_h
    draw.text((rat_x, text_baseline - rat_h), rat_text,
              fill=(210, 215, 235), font=rat_f)

    _draw_rank_badge(draw, text_right, y1, score.rank)

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

    # ---- Row 3: 分数 (数字贴图) ----
    y3 = sep2_y + 16
    score_str = f"{score.score:,}"
    # 根据文字区宽度反算数字高度，预留边距
    max_text_w = text_right - text_x + 8
    _load_digits()
    d0 = _DIGIT_IMAGES.get("0")
    if d0:
        # 估算: 每位数字宽=高*80/120, 逗号宽=高*73/120, 间距=高*2/120
        n_digits = sum(1 for c in score_str if c.isdigit())
        n_commas = score_str.count(",")
        # max_text_w ≈ digit_h/120*(80*n_digits + 73*n_commas + 2*(n_digits+n_commas-1))
        # digit_h ≈ max_text_w * 120 / (80*n + 73*m + 2*(n+m-1))
        denom = 80 * n_digits + 73 * n_commas + \
            max(0, 2 * (n_digits + n_commas - 1))
        digit_h = min(int(max_text_w * 120 / denom) if denom > 0 else 56, 56)
    else:
        digit_h = 48
    _draw_score_digits(canvas, score_str, text_x - 2, y3, digit_h)


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
    c_title = COLOR_GOLD if light_text else (80, 60, 20)
    shadow_c = (0, 0, 0) if light_text else (200, 200, 200)

    # 玻璃背景
    bar = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    _draw_rrect(bd, (0, 10, bar_w, bar_h - 10), radius=22,
                fill=COLOR_TOP_BAR_BG, outline=(255, 255, 255, 20), width=2)
    canvas.paste(bar, (0, 0), bar)

    # 左侧：姓名 Rating 左右并排，垂直居中
    nf = get_font(fz_player)
    n_w = draw.textbbox((0, 0), name, font=nf)[2]
    n_h = draw.textbbox((0, 0), name, font=nf)[3]
    n_y_center = (bar_h - n_h) // 2
    _draw_text_with_shadow(draw, (name_x, n_y_center), name, nf, c_name, shadow_color=shadow_c)

    # Rating
    rat_h_actual = rat_h
    rat_x = name_x + n_w + 32
    rat_y = (bar_h - rat_h_actual) // 2
    drawn_w = _draw_rating_digits(canvas, val, rat_x, rat_y, rat_h_actual)
    if drawn_w == 0:
        rtf = get_font(rat_h_actual)
        rth = draw.textbbox((0, 0), f"{val:.2f}", font=rtf)[3]
        draw.text((rat_x, (bar_h - rth) // 2), f"{val:.2f}", fill=c_rtg, font=rtf)

    # 右侧标题
    tf = get_section_font(title_fz)
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
    """游戏 UI 风格章节标题：Russo One 粗体 + 底部装饰线"""
    draw = ImageDraw.Draw(canvas)
    font = get_section_font(FZ_SECTION + 6)
    tw = draw.textbbox((0, 0), text, font=font)[2]
    th = draw.textbbox((0, 0), text, font=font)[3]

    # 文字 + 投影
    _draw_text_with_shadow(draw, (x, y), text, font, COLOR_GOLD)

    # 底部装饰条：浅金渐变线
    line_y = y + th + 10
    draw.line([x, line_y, x + tw, line_y], fill=(255, 200, 30, 80), width=3)
    draw.line([x, line_y + 3, x + tw, line_y + 3],
              fill=(255, 200, 30, 40), width=1)


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

    _prepare_background(canvas)
    _draw_top_bar(canvas, player.name, "BEST 30", b30_rating)

    card_start_y = TOP_BAR_HEIGHT + 20
    for row in range(CARD_ROWS):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(scores):
                continue
            cx = 20 + col * (CARD_WIDTH + CARD_GAP_X)
            cy = card_start_y + row * (CARD_HEIGHT + CARD_GAP_Y)
            _draw_card(canvas, scores[idx], cx, cy, idx)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = canvas.convert("RGB")
    canvas.save(save_path, quality=95)
    return save_path


# ===================================================================
#  API / 数据模型
# ===================================================================

def build_headers(token: str) -> Dict[str, str]:
    return {
        "X-User-Token": token,
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/",
    }


def fetch_all_songs_data() -> bool:
    global ALL_SONGS_CACHE
    if ALL_SONGS_CACHE:
        return True
    try:
        resp = requests.get(
            "https://www.diving-fish.com/api/chunithmprober/music_data",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15, verify=False)
        resp.raise_for_status()
        ALL_SONGS_CACHE = {s["id"]: s for s in resp.json()}
        return True
    except Exception:
        return False


def get_song_level_value(song_id: int, level_index: int, fallback: str) -> str:
    if not ALL_SONGS_CACHE and not fetch_all_songs_data():
        return format_level_value(fallback)
    song = ALL_SONGS_CACHE.get(song_id)
    if not song:
        return format_level_value(fallback)
    ds = song.get("ds", [])
    if not isinstance(ds, list) or level_index >= len(ds):
        return format_level_value(fallback)
    return format_level_value(str(ds[level_index]))


class Player:
    def __init__(self, data: Dict[str, Any]):
        pd = data.get("data", data) if isinstance(data, dict) else {}
        self.name: str = pd.get("name", "Unknown")
        self.rating: float = pd.get("rating", 0.0)
        self.rating_floor = math.floor(self.rating * 100) / 100


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
        self.final_level = get_song_level_value(
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


def get_player_info(token: str) -> Optional[Player]:
    try:
        resp = requests.get(f"{BASE_URL}/api/v0/user/chunithm/player",
                            headers=build_headers(token), timeout=10)
        resp.raise_for_status()
        return Player(resp.json())
    except Exception:
        return None


def get_player_scores(token: str) -> Optional[List[Score]]:
    try:
        resp = requests.get(f"{BASE_URL}/api/v0/user/chunithm/player/scores",
                            headers=build_headers(token), timeout=15)
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


def generate_b30_image(token: str, output_dir: Path, user_id: str) -> Optional[Path]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(token)
    scores = get_player_scores(token)
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
PUSH_JACKET = 490


def create_push_score_image(
    player: Player, score: Score, save_path: Path,
    bg_image_path: Optional[Path] = None, title: str = "随机推分",
) -> Path:
    """单曲推分/装福 —— 与 B30 卡片一致的玻璃拟态风格"""
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (PUSH_W, PUSH_H), Image.LANCZOS).convert("RGBA")
        except Exception:
            canvas = Image.new("RGBA", (PUSH_W, PUSH_H), (18, 20, 35, 255))
    else:
        canvas = Image.new("RGBA", (PUSH_W, PUSH_H), (18, 20, 35, 255))

    draw = ImageDraw.Draw(canvas)

    # 统一顶栏 (暗色文字)
    _draw_top_bar(canvas, player.name, title, player.rating,
                  bar_w=PUSH_W, bar_h=PUSH_TOP, name_x=40,
                  fz_player=40, rat_h=44, title_fz=42, light_text=False)

    # ---- 曲绘 + 霓虹发光框 ----
    _, _, border = get_card_colors(score.level_index)
    jacket = score.load_jacket_image()
    j_lg = jacket.resize((PUSH_JACKET, PUSH_JACKET), Image.LANCZOS)
    jx = 40
    jy = PUSH_TOP + (PUSH_H - PUSH_TOP - PUSH_JACKET) // 2
    canvas.paste(j_lg, (jx, jy))
    # 多层发光
    for off in range(6, 0, -1):
        a = max(15, 130 - off * 20)
        draw.rectangle(
            [jx - off, jy - off, jx + PUSH_JACKET + off, jy + PUSH_JACKET + off],
            outline=border[:3] + (a,), width=3,
        )
    draw.rectangle([jx - 1, jy - 1, jx + PUSH_JACKET + 1, jy + PUSH_JACKET + 1],
                   outline=border[:3] + (180,), width=3)

    # ---- 右侧信息 ----
    tx = jx + PUSH_JACKET + 60
    ty = PUSH_TOP + 40
    text_right = PUSH_W - 40

    # 暗色文字 (浅底)，全部粗体
    DARK_TEXT = (30, 32, 45)
    DARK_SUBTLE = (75, 80, 95)
    DARK_ACCENT = (60, 40, 15)
    DARK_LINE = (30, 32, 45, 45)

    # Track name — 粗体大字
    sn_f = get_song_font(48)
    sn = truncate_text_by_width(score.song_name, sn_f, text_right - tx)
    _draw_text_with_shadow(draw, (tx, ty), sn, sn_f, DARK_TEXT, shadow_color=(210, 210, 215))
    ty += 84

    # Level + Rating 同行 (粗体)
    lv_f = get_song_font(38)
    draw.text((tx, ty), f"Level {score.final_level}", fill=DARK_SUBTLE, font=lv_f)
    rt_f = get_song_font(38)
    rt_text = f"Rating {score.rank}"
    rt_w = draw.textbbox((0, 0), rt_text, font=rt_f)[2]
    draw.text((text_right - rt_w, ty), rt_text, fill=DARK_ACCENT, font=rt_f)
    ty += 72

    # 分割线
    draw.line([tx, ty, text_right, ty], fill=DARK_LINE, width=2)
    ty += 28

    # Score — 数字贴图
    score_str = f"{score.score:,}"
    # 计算贴图高度适配可用宽度
    _load_digits()
    d0 = _DIGIT_IMAGES.get("0")
    if d0:
        n_digits = sum(1 for c in score_str if c.isdigit())
        n_commas = score_str.count(",")
        denom = 80 * n_digits + 73 * n_commas + max(0, 2 * (n_digits + n_commas - 1))
        avail_w = text_right - tx
        digit_h = min(int(avail_w * 120 / denom) if denom > 0 else 72, 80)
    else:
        digit_h = 56
    _draw_score_digits(canvas, score_str, tx, ty, digit_h)
    ty += digit_h + 44

    # Chart Rating — 粗体
    cr_f = get_song_font(34)
    cr_text = f"Chart Rating: {score.rating_floor:.2f}"
    draw.text((tx, ty), cr_text, fill=DARK_SUBTLE, font=cr_f)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = canvas.convert("RGB")
    canvas.save(save_path, quality=95)
    return save_path


def get_all_player_scores(token: str) -> Optional[List[Score]]:
    try:
        resp = requests.get(f"{BASE_URL}/api/v0/user/chunithm/player/scores",
                            headers=build_headers(token), timeout=15)
        resp.raise_for_status()
        return [Score(item) for item in resp.json().get("data", [])]
    except Exception:
        return None


def generate_push_score_image(
    token: str, output_dir: Path, user_id: str,
) -> Optional[Tuple[Path, Score]]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(token)
    scores = get_all_player_scores(token)
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

B50_W, B50_H = 3000, 3220

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


def get_player_bests(token: str) -> Optional[Dict[str, List[Score]]]:
    try:
        resp = requests.get(f"{BASE_URL}/api/v0/user/chunithm/player/bests",
                            headers=build_headers(token), timeout=15)
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

    _prepare_background(canvas)
    _draw_top_bar(canvas, player.name, "BEST 50", b50_rating)

    # 旧曲
    sy = TOP_BAR_HEIGHT + 20
    _draw_section_title(canvas, 24, sy, "OLD BEST 30")
    csy = sy + 68
    for row in range(6):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(old_scores):
                break
            cx = 20 + col * (CARD_WIDTH + CARD_GAP_X)
            cy = csy + row * (CARD_HEIGHT + CARD_GAP_Y)
            _draw_card(canvas, old_scores[idx], cx, cy, idx)

    # 新曲
    sy = csy + 6 * (CARD_HEIGHT + CARD_GAP_Y) + 24
    _draw_section_title(canvas, 24, sy, "NEW BEST 20")
    csy = sy + 68
    for row in range(4):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(new_scores):
                break
            cx = 20 + col * (CARD_WIDTH + CARD_GAP_X)
            cy = csy + row * (CARD_HEIGHT + CARD_GAP_Y)
            _draw_card(canvas, new_scores[idx], cx, cy, idx)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = canvas.convert("RGB")
    canvas.save(save_path, quality=95)
    return save_path


def generate_b50_image(token: str, output_dir: Path, user_id: str) -> Optional[Path]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(token)
    bests = get_player_bests(token)
    if not player or not bests:
        return None
    old = bests.get("bests", [])
    new = bests.get("new_bests", [])
    if not old and not new:
        return None
    all_s = old + new
    total = sum(s.rating_floor for s in all_s)
    b50 = math.floor(total * 100 / len(all_s)) / 100 if all_s else 0.0
    bg = Path("data") / "b30_assets" / "bg.png"
    save = output_dir / f"b50_{user_id}_{int(b50 * 100):04d}.png"
    return create_b50_style_image(player, old, new, b50, save, bg)


# ===================================================================
#  装福
# ===================================================================

def generate_fu_image(
    token: str, output_dir: Path, user_id: str,
) -> Optional[Tuple[Path, Score]]:
    requests.packages.urllib3.disable_warnings()
    cleanup_old_images(output_dir)
    fetch_all_songs_data()
    player = get_player_info(token)
    scores = get_all_player_scores(token)
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
