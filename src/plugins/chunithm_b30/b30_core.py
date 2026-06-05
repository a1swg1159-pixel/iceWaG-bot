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

CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 770
TOP_BAR_HEIGHT = 70
CARD_COLUMNS = 5
CARD_ROWS = 6
CARD_WIDTH = 220
CARD_HEIGHT = 100
CARD_GAP_X = 15
CARD_GAP_Y = 10
JACKET_SIZE = 60
SONG_NAME_MAX_WIDTH = CARD_WIDTH - JACKET_SIZE - 25
CARD_CORNER_RADIUS = 10

FONT_PATH = "simhei.ttf"
FONT_SIZE_NICKNAME = 20
FONT_SIZE_B30_RATING = 18
FONT_SIZE_CARD_RANK = 20
FONT_SIZE_CARD_NAME = 14
FONT_SIZE_CARD_SCORE = 24
FONT_SIZE_CARD_RATING = 14
FONT_SIZE_CARD_LEVEL = 15

COLOR_BG = (25, 28, 38)
COLOR_TOP_BAR = (34, 39, 51)
COLOR_CARD_BG_ULTIMA = (35, 35, 35)        # 黑谱
COLOR_CARD_BG_MASTER = (150, 50, 240)      # 紫谱 深紫
COLOR_CARD_BG_EXPERT = (120, 20, 20)
COLOR_CARD_BG_DEFAULT = (34, 39, 51)
COLOR_CARD_BORDER_ULTIMA = (253, 58, 58)   # 黑谱红边
COLOR_CARD_BORDER_MASTER = (215, 150, 255) # 紫谱浅紫边
COLOR_CARD_BORDER_EXPERT = (244, 67, 54)
COLOR_CARD_BORDER_DEFAULT = (52, 152, 219)

COLOR_TEXT_WHITE = (255, 255, 255)
COLOR_TEXT_YELLOW = (241, 196, 15)

RANK_MAPPING = {
    "sp": "S+",
    "ssp": "SS+",
    "sssp": "SSS+",
    "d": "D",
    "c": "C",
    "b": "B",
    "bb": "BB",
    "bbb": "BBB",
    "a": "A",
    "aa": "AA",
    "aaa": "AAA",
    "s": "S",
    "ss": "SS",
    "sss": "SSS",
}

ALL_SONGS_CACHE: Dict[int, Dict[str, Any]] = {}


def build_headers(token: str) -> Dict[str, str]:
    return {
        "X-User-Token": token,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/",
    }


def truncate_text_by_width(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    ellipsis: str = "...",
) -> str:
    text_width = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), text, font=font
    )[2]
    if text_width <= max_width:
        return text

    ellipsis_width = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), ellipsis, font=font
    )[2]
    available_width = max_width - ellipsis_width

    truncated_text = ""
    for char in text:
        test_text = truncated_text + char
        test_width = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
            (0, 0), test_text, font=font
        )[2]
        if test_width > available_width:
            break
        truncated_text = test_text

    return truncated_text + ellipsis


def get_font(size: int) -> ImageFont.FreeTypeFont:
    font_paths = [
        FONT_PATH,
        "msyh.ttc",
        "simkai.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        # Linux CJK fonts
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default(size=size)


def format_level_value(level_str: str) -> str:
    if not level_str or level_str == "Unknown":
        return "Unknown"

    try:
        clean_level = "".join(
            filter(lambda c: c.isdigit() or c == ".", str(level_str))
        )
        if not clean_level:
            return str(level_str)

        level_num = float(clean_level)
        return f"{level_num:.1f}"
    except Exception:
        return str(level_str)


def get_card_colors_by_level(level_index: int) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    if level_index >= 4:
        return COLOR_CARD_BG_ULTIMA, COLOR_CARD_BORDER_ULTIMA
    if level_index == 3:
        return COLOR_CARD_BG_MASTER, COLOR_CARD_BORDER_MASTER
    if level_index == 2:
        return COLOR_CARD_BG_EXPERT, COLOR_CARD_BORDER_EXPERT
    return COLOR_CARD_BG_DEFAULT, COLOR_CARD_BORDER_DEFAULT


def draw_rounded_rectangle(
    draw: ImageDraw.ImageDraw,
    xy,
    radius: int,
    fill=None,
    outline=None,
    width: int = 1,
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
        draw.line([x1 + radius, y1, x2 - radius, y1],
                  fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2],
                  fill=outline, width=width)
        draw.line([x1, y1 + radius, x1, y2 - radius],
                  fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius],
                  fill=outline, width=width)
        draw.arc([x1, y1, x1 + 2 * radius, y1 + 2 * radius],
                 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y1, x2, y1 + 2 * radius],
                 270, 0, fill=outline, width=width)
        draw.arc([x1, y2 - 2 * radius, x1 + 2 * radius, y2],
                 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2 * radius, y2 - 2 * radius, x2, y2],
                 0, 90, fill=outline, width=width)


def fetch_all_songs_data() -> bool:
    global ALL_SONGS_CACHE
    if ALL_SONGS_CACHE:
        return True

    music_data_url = "https://www.diving-fish.com/api/chunithmprober/music_data"
    try:
        resp = requests.get(
            music_data_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        songs = resp.json()
        ALL_SONGS_CACHE = {song["id"]: song for song in songs}
        return True
    except Exception:
        return False


def get_song_level_value(song_id: int, level_index: int, fallback_level: str) -> str:
    if not ALL_SONGS_CACHE and not fetch_all_songs_data():
        return format_level_value(fallback_level)

    song = ALL_SONGS_CACHE.get(song_id)
    if not song:
        return format_level_value(fallback_level)

    ds = song.get("ds", [])
    if not isinstance(ds, list) or level_index >= len(ds):
        return format_level_value(fallback_level)

    level_value = ds[level_index]
    return format_level_value(str(level_value))


class Player:
    def __init__(self, data: Dict[str, Any]):
        player_data = data.get("data", data) if isinstance(data, dict) else {}
        self.name: str = player_data.get("name", "Unknown")
        self.rating: float = player_data.get("rating", 0.0)
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
            level_index=self.level_index,
            fallback_level=self.raw_level,
        )

    def load_jacket_image(self) -> Image.Image:
        if self.jacket_image:
            return self.jacket_image

        jacket_id = self.origin_id if self.level_index == 5 else self.id
        jacket_urls = []
        for base_url in ASSETS_BASE_URLS:
            jacket_urls.extend(
                [
                    f"{base_url}/chunithm/jacket/{jacket_id}.png",
                    f"{base_url}/chunithm/jacket/{jacket_id}.jpg",
                    f"{base_url}/chunithm/jacket/{jacket_id:04d}.png",
                ]
            )

        img_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Referer": f"{BASE_URL}/",
            "Accept": "image/png,image/jpeg,image/webp,*/*",
        }

        for url in jacket_urls:
            try:
                resp = requests.get(
                    url, headers=img_headers, timeout=5, allow_redirects=True, verify=False
                )
                if resp.status_code == 200 and len(resp.content) > 100:
                    try:
                        img = Image.open(io.BytesIO(resp.content)).resize(
                            (JACKET_SIZE, JACKET_SIZE), Image.LANCZOS
                        )
                        self.jacket_image = img
                        return img
                    except Exception:
                        continue
            except Exception:
                continue

        default_img = Image.new(
            "RGB", (JACKET_SIZE, JACKET_SIZE), color=(44, 62, 80))
        draw = ImageDraw.Draw(default_img)
        name_abbr = self.song_name[:2] if len(
            self.song_name) >= 2 else self.song_name
        font = get_font(20)
        bbox = draw.textbbox((0, 0), name_abbr, font=font)
        draw.text(
            ((JACKET_SIZE - bbox[2]) / 2, (JACKET_SIZE - bbox[3]) / 2),
            name_abbr,
            fill=COLOR_TEXT_YELLOW,
            font=font,
        )
        self.jacket_image = default_img
        return default_img


def create_b30_style_image(
    player: Player,
    scores: List[Score],
    b30_rating: float,
    save_path: Path,
    bg_image_path: Optional[Path] = None,
) -> Path:
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (CANVAS_WIDTH, CANVAS_HEIGHT), Image.LANCZOS).convert("RGB")
        except Exception:
            canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), color=COLOR_BG)
    else:
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), color=COLOR_BG)

    draw = ImageDraw.Draw(canvas)

    # 顶部栏 - 实色
    draw.rectangle([0, 0, CANVAS_WIDTH, TOP_BAR_HEIGHT], fill=COLOR_TOP_BAR)

    # 玩家名
    name_font = get_font(FONT_SIZE_NICKNAME)
    draw.text((20, 10), f"名称: {player.name}",
              fill=COLOR_TEXT_WHITE, font=name_font)

    # Rating
    rating_font = get_font(FONT_SIZE_B30_RATING)
    b30_text = f"B30 评分: {b30_rating:.2f}"
    draw.text((20, 40), b30_text, fill=COLOR_TEXT_WHITE, font=rating_font)

    # 右上角标题
    title_text = "BEST 30"
    title_font = get_font(FONT_SIZE_B30_RATING)
    draw.text(
        (CANVAS_WIDTH - 100, (TOP_BAR_HEIGHT - FONT_SIZE_B30_RATING) / 2),
        title_text,
        fill=COLOR_TEXT_WHITE,
        font=title_font,
    )

    # 卡片
    card_start_y = TOP_BAR_HEIGHT + 20
    for row in range(CARD_ROWS):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(scores):
                continue
            card_x = 20 + col * (CARD_WIDTH + CARD_GAP_X)
            card_y = card_start_y + row * (CARD_HEIGHT + CARD_GAP_Y)
            _draw_card(draw, canvas, scores[idx], card_x, card_y, idx)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(save_path, quality=95)
    return save_path


def get_player_info(token: str) -> Optional[Player]:
    url = f"{BASE_URL}/api/v0/user/chunithm/player"
    try:
        resp = requests.get(url, headers=build_headers(token), timeout=10)
        resp.raise_for_status()
        return Player(resp.json())
    except Exception:
        return None


def get_player_scores(token: str) -> Optional[List[Score]]:
    url = f"{BASE_URL}/api/v0/user/chunithm/player/scores"
    try:
        resp = requests.get(url, headers=build_headers(token), timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [Score(item) for item in data[:30]]
    except Exception:
        return None


CLEANUP_MAX_AGE_SECONDS = 3600  # 1 小时后清理


def cleanup_old_images(output_dir: Path) -> None:
    """清理超过 CLEANUP_MAX_AGE_SECONDS 的旧图片"""
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

    total_rating_floor = sum(score.rating_floor for score in scores)
    b30_rating = math.floor(total_rating_floor * 100 / len(scores)) / 100

    bg_image_path = Path("data") / "b30_assets" / "bg.png"
    filename = f"b30_{user_id}_{int(b30_rating * 100):04d}.png"
    save_path = output_dir / filename
    return create_b30_style_image(player, scores, b30_rating, save_path, bg_image_path)


# ========== 推分功能 ==========

PUSH_CANVAS_WIDTH = 700
PUSH_CANVAS_HEIGHT = 280
PUSH_TOP_BAR_HEIGHT = 50
PUSH_JACKET_SIZE = 180
PUSH_TEXT_LEFT = 240
PUSH_LINE_HEIGHT = 32


def get_all_player_scores(token: str) -> Optional[List[Score]]:
    """获取玩家的所有成绩（不限于前30）"""
    url = f"{BASE_URL}/api/v0/user/chunithm/player/scores"
    try:
        resp = requests.get(url, headers=build_headers(token), timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [Score(item) for item in data]
    except Exception:
        return None


def create_push_score_image(
    player: Player,
    score: Score,
    save_path: Path,
    bg_image_path: Optional[Path] = None,
    title: str = "随机推分",
) -> Path:
    """生成单曲推分图片"""
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (PUSH_CANVAS_WIDTH, PUSH_CANVAS_HEIGHT), Image.LANCZOS).convert("RGB")
        except Exception:
            canvas = Image.new("RGB", (PUSH_CANVAS_WIDTH, PUSH_CANVAS_HEIGHT), color=COLOR_BG)
    else:
        canvas = Image.new("RGB", (PUSH_CANVAS_WIDTH, PUSH_CANVAS_HEIGHT), color=COLOR_BG)

    draw = ImageDraw.Draw(canvas)

    # 顶部栏 - 实色
    draw.rectangle(
        [0, 0, PUSH_CANVAS_WIDTH, PUSH_TOP_BAR_HEIGHT], fill=COLOR_TOP_BAR)

    title_font = get_font(22)
    draw.text((20, (PUSH_TOP_BAR_HEIGHT - 22) / 2),
              title, fill=COLOR_TEXT_YELLOW, font=title_font)

    player_text = f"玩家: {player.name}"
    player_font = get_font(16)
    player_bbox = draw.textbbox((0, 0), player_text, font=player_font)
    draw.text(
        (PUSH_CANVAS_WIDTH - player_bbox[2] - 20,
         (PUSH_TOP_BAR_HEIGHT - player_bbox[3]) / 2),
        player_text, fill=(200, 200, 210), font=player_font,
    )

    # 曲绘
    _, card_border_color = get_card_colors_by_level(score.level_index)
    jacket = score.load_jacket_image()
    jacket_large = jacket.resize(
        (PUSH_JACKET_SIZE, PUSH_JACKET_SIZE), Image.LANCZOS)
    jacket_y = PUSH_TOP_BAR_HEIGHT + \
        (PUSH_CANVAS_HEIGHT - PUSH_TOP_BAR_HEIGHT - PUSH_JACKET_SIZE) // 2
    canvas.paste(jacket_large, (30, jacket_y))

    # 曲绘边框
    draw.rectangle(
        [28, jacket_y - 2, 32 + PUSH_JACKET_SIZE, jacket_y + PUSH_JACKET_SIZE + 2],
        outline=card_border_color, width=2,
    )

    # 右侧信息 - 深色字体
    text_y = PUSH_TOP_BAR_HEIGHT + 20
    label_font = get_font(18)
    value_font = get_font(18)

    def draw_line(label: str, value: str, y: int) -> int:
        draw.text((PUSH_TEXT_LEFT, y), f"{label}: ",
                  fill=(60, 65, 80), font=label_font)
        lw = draw.textbbox((0, 0), f"{label}: ", font=label_font)[2]
        draw.text((PUSH_TEXT_LEFT + lw, y), value,
                  fill=(20, 25, 40), font=value_font)
        return y + PUSH_LINE_HEIGHT

    text_y = draw_line("歌曲", score.song_name, text_y)
    text_y = draw_line("定数", score.final_level, text_y)
    text_y = draw_line("分数", f"{score.score:,}", text_y)
    text_y = draw_line("评级", score.rank, text_y)

    rating_label = "评分"
    rating_value = f"{score.rating_floor:.2f}"
    draw.text((PUSH_TEXT_LEFT, text_y), f"{rating_label}: ",
              fill=(60, 65, 80), font=label_font)
    rlw = draw.textbbox((0, 0), f"{rating_label}: ", font=label_font)[2]
    draw.text((PUSH_TEXT_LEFT + rlw, text_y), rating_value,
              fill=(180, 100, 0), font=value_font)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(save_path, quality=95)
    return save_path


def generate_push_score_image(
    token: str,
    output_dir: Path,
    user_id: str,
) -> Optional[Tuple[Path, Score]]:
    """随机抽取一首有成绩的歌曲，生成推分图片"""
    requests.packages.urllib3.disable_warnings()

    cleanup_old_images(output_dir)
    fetch_all_songs_data()

    player = get_player_info(token)
    scores = get_all_player_scores(token)
    if not player or not scores:
        return None

    score = random.choice(scores)
    score.load_jacket_image()

    bg_image_path = Path("data") / "b30_assets" / "bg.png"
    filename = f"push_{user_id}_{score.id}.png"
    save_path = output_dir / filename
    image_path = create_push_score_image(
        player, score, save_path, bg_image_path)
    return image_path, score


# ========== B50 功能 ==========

B50_CANVAS_WIDTH = 1200
B50_CANVAS_HEIGHT = 1260

LEVEL_INDEX_FALLBACK = {
    "Basic": 0, "Advanced": 1, "Expert": 2, "Master": 3, "Ultima": 4,
    "basic": 0, "advanced": 1, "expert": 2, "master": 3, "ultima": 4,
    "BAS": 0, "ADV": 1, "EXP": 2, "MAS": 3, "ULT": 4,
}


def _infer_level_index(score_data: Dict[str, Any]) -> int:
    """如果 API 未返回 level_index，从 level_label 推断"""
    if "level_index" in score_data:
        return score_data["level_index"]
    label = score_data.get("level_label", "")
    if label in LEVEL_INDEX_FALLBACK:
        return LEVEL_INDEX_FALLBACK[label]
    # 从 level 字符串推断
    raw = str(score_data.get("level", ""))
    if "+" in raw:
        try:
            if float(raw.replace("+", "")) >= 13:
                return 4  # Ultima
        except Exception:
            pass
    if raw == "?" or raw == "Unknown":
        return 3
    try:
        if float(raw) <= 5:
            return 0
        if float(raw) <= 8:
            return 1
        if float(raw) <= 12:
            return 2
        return 3
    except Exception:
        return 3


def get_player_bests(token: str) -> Optional[Dict[str, List[Score]]]:
    """调用 /api/v0/user/chunithm/player/bests 获取 Rating 构成"""
    url = f"{BASE_URL}/api/v0/user/chunithm/player/bests"
    try:
        resp = requests.get(url, headers=build_headers(token), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    inner = data.get("data", data) if isinstance(data, dict) else {}
    result: Dict[str, List[Score]] = {}
    for key in ("bests", "new_bests"):
        raw_list = inner.get(key, []) or []
        patched = []
        for item in raw_list:
            if "level_index" not in item:
                item = dict(item)
                item["level_index"] = _infer_level_index(item)
            patched.append(Score(item))
        result[key] = patched
    return result


def _draw_card(draw: ImageDraw.ImageDraw, canvas: Image.Image,
               score: Score, card_x: int, card_y: int, idx: int) -> None:
    """绘制单张成绩卡片"""
    card_bg_color, card_border_color = get_card_colors_by_level(score.level_index)
    # 红边（Expert=2 / Ultima>=4）加粗
    border_w = 2 if score.level_index == 2 or score.level_index >= 4 else 1

    draw_rounded_rectangle(
        draw=draw,
        xy=(card_x, card_y, card_x + CARD_WIDTH, card_y + CARD_HEIGHT),
        radius=CARD_CORNER_RADIUS,
        fill=card_bg_color,
        outline=card_border_color,
        width=border_w,
    )

    jacket = score.load_jacket_image()
    canvas.paste(jacket, (card_x + 5, card_y + 35))

    rank_text = f"{idx + 1:02d}"
    draw.text((card_x + 5, card_y + 5), rank_text,
              fill=COLOR_TEXT_WHITE, font=get_font(FONT_SIZE_CARD_RANK))

    song_name = truncate_text_by_width(
        text=score.song_name,
        font=get_font(FONT_SIZE_CARD_NAME),
        max_width=SONG_NAME_MAX_WIDTH,
    )
    draw.text((card_x + 70, card_y + 35), song_name,
              fill=COLOR_TEXT_WHITE, font=get_font(FONT_SIZE_CARD_NAME))

    draw.text((card_x + 70, card_y + 10), score.final_level,
              fill=COLOR_TEXT_WHITE, font=get_font(FONT_SIZE_CARD_LEVEL))

    draw.text((card_x + 70, card_y + 53), f"{score.score:,}",
              fill=COLOR_TEXT_WHITE, font=get_font(FONT_SIZE_CARD_SCORE))

    rating_text = f"评分: {score.rating_floor:.2f}"
    draw.text((card_x + 70, card_y + 80), rating_text,
              fill=COLOR_TEXT_WHITE, font=get_font(FONT_SIZE_CARD_RATING))

    draw.text((card_x + CARD_WIDTH - 45, card_y + 10), score.rank,
              fill=COLOR_TEXT_WHITE, font=get_font(FONT_SIZE_CARD_RANK))


def create_b50_style_image(
    player: Player,
    old_scores: List[Score],
    new_scores: List[Score],
    b50_rating: float,
    save_path: Path,
    bg_image_path: Optional[Path] = None,
) -> Path:
    """生成 B50 图片（旧曲 30 + 新曲 20）"""
    if bg_image_path and bg_image_path.exists():
        try:
            canvas = Image.open(bg_image_path).resize(
                (B50_CANVAS_WIDTH, B50_CANVAS_HEIGHT), Image.LANCZOS).convert("RGB")
        except Exception:
            canvas = Image.new("RGB", (B50_CANVAS_WIDTH, B50_CANVAS_HEIGHT), color=COLOR_BG)
    else:
        canvas = Image.new("RGB", (B50_CANVAS_WIDTH, B50_CANVAS_HEIGHT), color=COLOR_BG)

    draw = ImageDraw.Draw(canvas)

    # 顶部栏 - 实色
    draw.rectangle(
        [0, 0, B50_CANVAS_WIDTH, TOP_BAR_HEIGHT], fill=COLOR_TOP_BAR)

    name_font = get_font(FONT_SIZE_NICKNAME)
    draw.text((20, 10), f"名称: {player.name}",
              fill=COLOR_TEXT_WHITE, font=name_font)

    rating_font = get_font(FONT_SIZE_B30_RATING)
    draw.text((20, 40), f"B50 评分: {b50_rating:.2f}",
              fill=COLOR_TEXT_WHITE, font=rating_font)

    title_text = "BEST 50"
    title_font = get_font(FONT_SIZE_B30_RATING)
    draw.text(
        (B50_CANVAS_WIDTH - 100, (TOP_BAR_HEIGHT - FONT_SIZE_B30_RATING) / 2),
        title_text, fill=COLOR_TEXT_WHITE, font=title_font,
    )

    # 旧曲 BEST 30
    section_font = get_font(18)
    section_y = TOP_BAR_HEIGHT + 10
    draw.text((20, section_y), "旧曲 BEST 30",
              fill=COLOR_TEXT_YELLOW, font=section_font)

    card_start_y = section_y + 28
    for row in range(6):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(old_scores):
                break
            card_x = 20 + col * (CARD_WIDTH + CARD_GAP_X)
            card_y = card_start_y + row * (CARD_HEIGHT + CARD_GAP_Y)
            _draw_card(draw, canvas, old_scores[idx], card_x, card_y, idx)

    # 新曲 BEST 20
    section_y = card_start_y + 6 * (CARD_HEIGHT + CARD_GAP_Y) + 15
    draw.text((20, section_y), "新曲 BEST 20",
              fill=COLOR_TEXT_YELLOW, font=section_font)

    card_start_y = section_y + 28
    for row in range(4):
        for col in range(CARD_COLUMNS):
            idx = row * CARD_COLUMNS + col
            if idx >= len(new_scores):
                break
            card_x = 20 + col * (CARD_WIDTH + CARD_GAP_X)
            card_y = card_start_y + row * (CARD_HEIGHT + CARD_GAP_Y)
            _draw_card(draw, canvas, new_scores[idx], card_x, card_y, idx)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(save_path, quality=95)
    return save_path


def generate_b50_image(token: str, output_dir: Path, user_id: str) -> Optional[Path]:
    """调用 /bests API 获取 Rating 构成并生成 B50 图片"""
    requests.packages.urllib3.disable_warnings()

    cleanup_old_images(output_dir)
    fetch_all_songs_data()

    player = get_player_info(token)
    bests_data = get_player_bests(token)
    if not player or not bests_data:
        return None

    old_scores = bests_data.get("bests", [])
    new_scores = bests_data.get("new_bests", [])
    if not old_scores and not new_scores:
        return None

    all_top = old_scores + new_scores
    total_rating = sum(s.rating_floor for s in all_top)
    score_count = len(all_top)
    b50_rating = math.floor(total_rating * 100 / score_count) / 100 if score_count else 0.0

    bg_image_path = Path("data") / "b30_assets" / "bg.png"
    filename = f"b50_{user_id}_{int(b50_rating * 100):04d}.png"
    save_path = output_dir / filename
    return create_b50_style_image(player, old_scores, new_scores, b50_rating, save_path, bg_image_path)


# ========== 福 功能 ==========

def generate_fu_image(
    token: str,
    output_dir: Path,
    user_id: str,
) -> Optional[Tuple[Path, Score]]:
    """随机选一首单曲 rating 高于玩家实际 rating 的歌"""
    requests.packages.urllib3.disable_warnings()

    cleanup_old_images(output_dir)
    fetch_all_songs_data()

    player = get_player_info(token)
    scores = get_all_player_scores(token)
    if not player or not scores:
        return None

    # 筛选单曲 rating 高于玩家实际 rating 的
    above = [s for s in scores if s.rating_floor > player.rating_floor]
    if not above:
        return None

    score = random.choice(above)
    score.load_jacket_image()

    bg_image_path = Path("data") / "b30_assets" / "bg.png"
    filename = f"fu_{user_id}_{score.id}.png"
    save_path = output_dir / filename
    image_path = create_push_score_image(
        player, score, save_path, bg_image_path, title="装福")
    return image_path, score
