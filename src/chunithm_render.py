"""MATE-inspired score posters, rendered locally with no network or account access."""

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from src.chunithm_card_art import CARD_ART_CACHE, CardLayout
from src.image_credit import append_image_credit


LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
ASSETS = Path(__file__).resolve().parents[1] / "data" / "b30_assets"
INK = (37, 34, 31)
MUTED = (109, 98, 85)
PAPER = (250, 247, 239)
WHITE = (255, 254, 250)
YELLOW = (255, 222, 48)
CORAL = (255, 115, 102)
MINT = (158, 223, 203)
BORDER = (232, 226, 214)
# Stable colour names used by the command wrappers.
CYAN = CORAL
PURPLE = INK
DIFFICULTIES = {
    0: ("BASIC", (42, 135, 93)),
    1: ("ADVANCED", (153, 99, 13)),
    2: ("EXPERT", (204, 61, 76)),
    3: ("MASTER", (137, 68, 162)),
    4: ("ULTIMA", (43, 39, 45)),
    5: ("WORLD'S END", (32, 111, 139)),
}
# MATE arcade result-screen assets; provenance is in mate/sources.json.
# Missing/corrupt assets fall back to text, never to NET web badges.
RANK_ASSETS = {
    rank: f"rank_{rank.lower().replace('+', 'p')}.webp"
    for rank in (
        "D", "C", "B", "BB", "BBB", "A", "AA", "AAA",
        "S", "S+", "SS", "SS+", "SSS", "SSS+",
    )
}
COLUMNS = 5
MARGIN = 44
GAP = 24
CARD_WIDTH = 565
CARD_HEIGHT = 256
CARD_JACKET_SIZE = 170
CARD_JACKET_X = 18
CARD_JACKET_Y = 43
SHEET_WIDTH = MARGIN * 2 + COLUMNS * CARD_WIDTH + GAP * (COLUMNS - 1)
HEADER_HEIGHT = 278
BEST_HEADER_HEIGHT = 270
BEST_LOGO_SIZE = (550, 250)
SECTION_HEIGHT = 74
FIT_COLUMNS = COLUMNS
FIT_CARD_WIDTH = 850
FIT_CARD_HEIGHT = 236
FIT_WIDTH = MARGIN * 2 + FIT_COLUMNS * FIT_CARD_WIDTH + GAP * (FIT_COLUMNS - 1)
FIT_HEADER_HEIGHT = HEADER_HEIGHT + 82
SINGLE_WIDTH = 1800
SINGLE_HEIGHT = 1120
RANK_HEIGHT = 36
SINGLE_RANK_HEIGHT = 76
CARD_INK = (255, 252, 244)
CARD_MUTED = (227, 229, 237)
DIFFICULTY_INK = {
    0: (40, 116, 57), 1: (143, 98, 8), 2: (179, 48, 73),
    3: (137, 57, 170), 4: (21, 20, 25), 5: (32, 111, 139),
}
SCORE_CARD_LAYOUT = CardLayout(CARD_WIDTH, CARD_HEIGHT, CARD_JACKET_SIZE, CARD_JACKET_X, CARD_JACKET_Y)
FIT_CARD_LAYOUT = CardLayout(FIT_CARD_WIDTH, FIT_CARD_HEIGHT, 188, 18, 22)
SINGLE_CARD_LAYOUT = CardLayout(SINGLE_WIDTH - MARGIN * 2, 758, 584, 24, 31, 22)
PROFILE_PLATE_WIDTH = 660
PROFILE_PLATE_HEIGHT = round(PROFILE_PLATE_WIDTH * 228 / 576)
PROFILE_TOP = 8
# Native 576x228 plate coordinates, excluding the asset's transparent padding.
# Trophy and information/portrait frames share one inset right edge.
PROFILE_LEFT = 144
PROFILE_RIGHT = 556
PROFILE_INFO_TOP = 102
PROFILE_INFO_BOTTOM = 194
PROFILE_AVATAR_LEFT = PROFILE_RIGHT - (PROFILE_INFO_BOTTOM - PROFILE_INFO_TOP)

# MATE's website uses Noto Sans JP. Outfit and Rajdhani are deliberately
# chosen open-source companions, not claimed to be SEGA's in-game fonts.
FONT_FILES = {
    "display": "Outfit-ExtraBold.ttf",
    "ui": "Outfit-SemiBold.ttf",
    "number": "Rajdhani-Bold.ttf",
    "song": "NotoSansCJKjp-Bold.otf",
    "cjk": "NotoSansCJKjp-Bold.otf",
}


@lru_cache(maxsize=128)
def font(size: int, kind: str = "ui"):
    names = {
        "display": ("BarlowCondensed-ExtraBold.ttf", "section.ttf"),
        "ui": ("BarlowCondensed-SemiBold.ttf", "section.ttf"),
        "number": ("BarlowCondensed-ExtraBold.ttf", "section.ttf"),
        "song": ("FOT_NewRodin_Pro_EB.otf", "font.ttf"),
        "cjk": ("font.ttf", "FOT_NewRodin_Pro_EB.otf"),
    }[kind]
    candidates = [ASSETS / "mate" / "fonts" / FONT_FILES[kind]] + [
        ASSETS / "font" / name for name in names
    ] + [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text_width(value: str, face) -> float:
    if hasattr(face, "getlength"):
        return face.getlength(str(value))
    left, _, right, _ = face.getbbox(str(value))
    return right - left


def ellipsis(value: str, face, width: int) -> str:
    value = " ".join(str(value).split())
    if text_width(value, face) <= width:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if text_width(value[:middle] + "...", face) <= width:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip() + "..."


def title_lines(value: str, face, width: int, max_lines: int = 3) -> list[str]:
    """Wrap words/CJK, reserving three real lines before truncating a title."""
    remaining = " ".join(str(value).split())
    lines = []
    while remaining and len(lines) < max_lines:
        if len(lines) == max_lines - 1:
            lines.append(ellipsis(remaining, face, width))
            break
        if text_width(remaining, face) <= width:
            lines.append(remaining)
            break
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if text_width(remaining[:middle], face) <= width:
                low = middle
            else:
                high = middle - 1
        stop = low
        word = remaining.rfind(" ", 0, stop + 1)
        if word >= stop // 2 and word > 0:
            stop = word
        lines.append(remaining[:stop].strip())
        remaining = remaining[stop:].strip()
    return lines or [""]


@lru_cache(maxsize=512)
def card_title(value: str, width: int, size: int) -> tuple:
    return tuple(title_lines(value, font(size, "song"), width, max_lines=2))


@lru_cache(maxsize=512)
def adaptive_card_title(value, width, min_size=28, max_size=38, max_lines=2):
    value = " ".join(str(value).split())
    if text_width(value, font(min_size, "song")) > width:
        return tuple(title_lines(value, font(min_size, "song"), width, max_lines)), min_size
    low, high = min_size, max_size
    while low < high:
        middle = (low + high + 1) // 2
        if text_width(value, font(middle, "song")) <= width:
            low = middle
        else:
            high = middle - 1
    return (value,), low


def draw_adaptive_title(draw, value, box, *, min_size=28, max_size=38, max_lines=2):
    x, y, width, height = box
    lines, size = adaptive_card_title(value, width, min_size, max_size, max_lines)
    face = font(size, "song")
    bounds = face.getbbox(lines[-1] or " ")
    line_step = size + 5
    ink_height = (len(lines) - 1) * line_step + bounds[3] - bounds[1]
    top = y + max(0, (height - ink_height) // 2)
    for row, line in enumerate(lines):
        text(draw, (x, top + row * line_step), line, size, kind="song", fill=CARD_INK)


def text(draw, xy, value, size, *, kind="ui", fill=INK, anchor="lt"):
    draw.text(xy, str(value), font=font(size, kind), fill=fill, anchor=anchor)


@lru_cache(maxsize=2)
def background_tile(width: int):
    try:
        with Image.open(ASSETS / "mate" / "background.jpg") as source:
            tile = source.convert("RGB").resize(
                (width, round(source.height * width / source.width)), LANCZOS,
            )
            return Image.blend(tile, Image.new("RGB", tile.size, PAPER), .57)
    except (OSError, ValueError):
        return None


def canvas(width: int, height: int, *, header_height=HEADER_HEIGHT) -> Image.Image:
    image = Image.new("RGB", (width, height), PAPER)
    background = background_tile(width)
    if background is not None:
        reflected = ImageOps.flip(background)
        for index, y in enumerate(range(0, height, background.height)):
            image.paste(background if index % 2 == 0 else reflected, (0, y))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, header_height-1), fill=WHITE)
    draw.line((MARGIN, header_height - 1, width - MARGIN, header_height - 1),
              fill=BORDER, width=1)
    return image


def panel(draw, box, *, fill=WHITE, outline=BORDER, radius=16):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)


@lru_cache(maxsize=4)
def ui_art(name):
    try:
        with Image.open(ASSETS / "mate" / "ui" / name) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=16)
def card_surface(width, height, level):
    """A quiet MATE paper panel; official accents never sit behind score digits."""
    card = Image.new("RGBA", (width, height), (*WHITE, 255))
    color = DIFFICULTIES.get(level, DIFFICULTIES[3])[1]
    draw = ImageDraw.Draw(card)
    for y in range(52):
        blend = .065 * (1-y/52)
        fill = tuple(round(WHITE[i]*(1-blend) + color[i]*blend) for i in range(3))
        draw.line((0, y, width-1, y), fill=(*fill, 255))
    accents = ui_art("accents.webp")
    if accents is not None:
        accent = ImageOps.contain(accents, (130, 80), method=LANCZOS)
        accent.putalpha(accent.getchannel("A").point(lambda a: round(a*.20)))
        card.alpha_composite(accent, (width-accent.width-12, -22))
    dots = ui_art("dots.png")
    if dots is not None:
        dots = ImageOps.fit(dots, (150, 48), method=LANCZOS)
        dots.putalpha(dots.getchannel("A").point(lambda a: round(a*.45)))
        card.alpha_composite(dots, (width-166, 0))
    mask = Image.new("L", card.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width-1, height-1), radius=16, fill=255)
    card.putalpha(mask)
    return card


def paste_card(image, x, y, width, height, level):
    sprite = card_surface(width, height, level)
    image.paste(sprite, (x, y), sprite)


@lru_cache(maxsize=32)
def section_tab(value, accent):
    width = math.ceil(text_width(value, font(38, "display"))) + 62
    sprite = Image.new("RGBA", (width, 58))
    draw = ImageDraw.Draw(sprite)
    draw.rounded_rectangle((0, 0, width-1, 57), radius=21, fill=(255, 252, 229, 255))
    draw.ellipse((15, 24, 24, 33), fill=accent)
    text(draw, (35, 10), value, 38, kind="display")
    return sprite


def draw_section_tab(image, xy, value, accent=INK):
    sprite = section_tab(value, accent)
    image.paste(sprite, xy, sprite)


@lru_cache(maxsize=1)
def logo():
    try:
        with Image.open(ASSETS / "mate" / "logo.webp") as source:
            mark = source.convert("RGBA")
            bounds = mark.getchannel("A").getbbox()
            return mark.crop(bounds) if bounds else mark
    except (OSError, ValueError):
        return None


def draw_wordmark(image, x, y, *, size=(345, 150)):
    mark = logo()
    if mark is not None:
        mark = ImageOps.contain(mark, size, method=LANCZOS)
        image.paste(mark, (x + (size[0] - mark.width) // 2,
                           y + (size[1] - mark.height) // 2), mark)
        return
    fallback = Image.new("RGBA", (345, 150))
    draw = ImageDraw.Draw(fallback)
    text(draw, (22, 5), "CHUNITHM", 36, kind="number")
    draw.text((15, 42), "Mate", font=font(110, "number"), fill=YELLOW,
              stroke_width=9, stroke_fill=CORAL, anchor="lt")
    draw.text((15, 42), "Mate", font=font(110, "number"), fill=YELLOW,
              stroke_width=2, stroke_fill=WHITE, anchor="lt")
    fallback = ImageOps.contain(fallback, size, method=LANCZOS)
    image.paste(fallback, (x + (size[0]-fallback.width)//2,
                           y + (size[1]-fallback.height)//2), fallback)


@lru_cache(maxsize=4)
def best_header_art(width, height=BEST_HEADER_HEIGHT):
    """Official MATE busts on a locally composed, clipped horizontal banner."""
    portraits = []
    for name, crop in (("m_5", (170, 60, 855, 485)),
                       ("m_4", (200, 55, 850, 480)),
                       ("m_6", (230, 85, 850, 510))):
        try:
            with Image.open(ASSETS / "mate" / "header" / f"{name}.webp") as source:
                portraits.append(source.convert("RGBA").crop(crop))
        except (OSError, ValueError):
            portraits.append(None)
    if not any(portrait is not None for portrait in portraits):
        return None
    banner = Image.new("RGBA", (width, height), (*WHITE, 255))
    backdrop = background_tile(width)
    if backdrop is not None:
        banner.alpha_composite(Image.blend(
            ImageOps.fit(backdrop, banner.size, method=LANCZOS),
            Image.new("RGB", banner.size, WHITE), .32,
        ).convert("RGBA"))
    slot_width = width / 3
    for index, portrait in enumerate(portraits):
        if portrait is None:
            continue
        # All heads stay in view; only the lower torso meets the banner edge.
        scale = min((height+80)/portrait.height, (slot_width+20)/portrait.width)
        portrait = portrait.resize((round(portrait.width*scale), round(portrait.height*scale)), LANCZOS)
        x = round(slot_width*(index+.5) - portrait.width/2)
        banner.alpha_composite(portrait, (x, 0))
    fade = Image.new("L", (width, 1))
    fade.putdata([round(255*min(1, x/64, (width-1-x)/64)) for x in range(width)])
    banner.putalpha(ImageChops.multiply(banner.getchannel("A"), fade.resize(banner.size)))
    return banner


def collection_image(player, method, *, trim=True):
    loader = getattr(player, method, None)
    if not callable(loader):
        return None
    try:
        source = loader()
        if source is None:
            return None
        sprite = source.convert("RGBA")
        bounds = sprite.getchannel("A").getbbox()
        return (sprite.crop(bounds) if trim else sprite) if bounds else None
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=32)
def profile_asset(relative_path):
    try:
        with Image.open(ASSETS / "mate" / relative_path) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def profile_rating_tier(rating):
    for ceiling, tier in ((4, "green"), (7, "orange"), (10, "red"),
                          (12, "purple"), (13.25, "bronze"), (14.5, "silver"),
                          (15.25, "gold"), (16, "platinum"), (17, "rainbow")):
        if rating < ceiling:
            return tier
    return "kiwami"


@lru_cache(maxsize=16)
def profile_rating_map(tier):
    tint = {"green": "#70e921", "orange": "#fba725",
            "red": "#f84744", "purple": "#bb52eb"}.get(tier)
    sprite = profile_asset(f"profile_rating/{'white' if tint else tier}.webp")
    if sprite is not None and tint:
        colored = ImageOps.colorize(sprite.convert("L"), "black", tint)
        colored.putalpha(sprite.getchannel("A"))
        return colored
    return sprite


@lru_cache(maxsize=64)
def profile_font(size, bold=False):
    if not bold:
        try:
            # Optional light face for explicit callers; player labels use bold.
            return ImageFont.truetype(str(ASSETS / "font" / "font.ttf"), size)
        except OSError:
            pass
    return font(size, "cjk")


@lru_cache(maxsize=512)
def profile_regular_supports(value):
    face = profile_font(24)
    missing = bytes(face.getmask("\U0010ffff"))
    return all(bytes(face.getmask(char)) != missing for char in set(value)
               if not char.isspace())


def profile_label(image, xy, value, size, width, *, full_width=False, bold=False):
    """Draw on the 2x native player panel; preserve arcade-like full-width names."""
    value = str(value)
    if full_width:
        value = "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~"
                        else "\u3000" if c == " " else c for c in value)
    # M+ 1p omits many simplified Chinese glyphs; use the bundled full CJK face
    # for the entire label so nicknames never mix weights or display tofu boxes.
    bold = bold or not profile_regular_supports(value)
    # Name/trophy rows have hard bounds; extreme API input cannot cover the icon.
    while size > 18 and text_width(value, profile_font(size * 2, bold)) > width * 2:
        size -= 1
    face = profile_font(size * 2, bold)
    value = ellipsis(value, face, width * 2)
    ImageDraw.Draw(image).text((round(xy[0] * 2), round(xy[1] * 2)), value,
                              font=face, fill=(0, 0, 0), anchor="lm")


def _profile_paste(image, sprite, box, *, contain=False):
    if sprite is None:
        return
    x, y, w, h = [round(v * 2) for v in box]
    if contain:
        sprite = ImageOps.contain(sprite, (w, h), method=LANCZOS)
        x, y = x + (w - sprite.width) // 2, y + (h - sprite.height) // 2
    else:
        sprite = sprite.resize((w, h), LANCZOS)
    image.alpha_composite(sprite, (x, y))


def _profile_rating(image, rating):
    if not isinstance(rating, (int, float)) or not math.isfinite(rating) or rating < 0:
        return
    number_map = profile_rating_map(profile_rating_tier(rating))
    if number_map is None:
        profile_label(image, (154, 179), f"RATING {rating:.2f}", 19, 201, bold=True)
        return
    w, h = number_map.width // 4, number_map.height // 4
    _profile_paste(image, number_map.crop((0, 3*h, 3*w, 4*h)), (154, 168, 72, 24))
    x = 247
    for digit in f"{min(rating, 99.99):.2f}":
        index = 10 if digit == "." else int(digit)
        left, top = (index % 4) * w, (index // 4) * h
        _profile_paste(image, number_map.crop((left, top, left+w, top+h)), (x, 164, 28, 28))
        x += 10 if digit == "." else 21


def _profile_trophy(image, player):
    width = PROFILE_RIGHT - PROFILE_LEFT
    trophy_box = (PROFILE_LEFT, 54, width, 40)
    trophy = collection_image(player, "load_trophy_image")
    if trophy is not None:
        _profile_paste(image, trophy, trophy_box, contain=True)
        return
    name = str(getattr(player, "trophy_name", "") or "").strip()
    if not name:
        return
    color = str(getattr(player, "trophy_color", "normal")).lower()
    tint = {"copper": (220, 166, 122), "bronze": (220, 166, 122),
            "silver": (192, 213, 229), "gold": (239, 193, 76),
            "platinum": (211, 220, 191), "platina": (211, 220, 191),
            "rainbow": (217, 183, 228)}.get(color, (218, 216, 213))
    # Native silver capsule: thin beveled edge and fine diagonal lower sheen.
    capsule = Image.new("RGBA", (width * 2, 80))
    right = capsule.width - 1
    draw = ImageDraw.Draw(capsule)
    for y in range(80):
        blend = .48 * (1 - y / 79)
        fill = tuple(round(c + (255-c) * blend) for c in tint)
        draw.line((0, y, right, y), fill=(*fill, 255))
    for x in range(-80, capsule.width, 6):
        draw.line((x, 79, x+26, 53), fill=(255, 255, 255, 105), width=1)
    mask = Image.new("L", capsule.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, right, 79), radius=13, fill=255)
    capsule.putalpha(mask)
    draw.rounded_rectangle((1, 1, right-1, 78), radius=13, outline=(177, 177, 177), width=2)
    draw.rounded_rectangle((4, 4, right-4, 75), radius=10, outline=(255, 255, 255), width=3)
    _profile_paste(image, capsule, trophy_box)
    size = 28
    # Both nickname and primary trophy are deliberately bold, including Latin.
    bold = True
    text_width_limit = width - 24
    while size > 18 and text_width(name, profile_font(size * 2, bold)) > text_width_limit * 2:
        size -= 1
    name = ellipsis(name, profile_font(size * 2, bold), text_width_limit * 2)
    title_width = text_width(name, profile_font(size * 2, bold)) / 2
    profile_label(image, ((PROFILE_LEFT + PROFILE_RIGHT - title_width)/2, 74),
                  name, size, text_width_limit, bold=bold)


def render_profile(player):
    """576x228 native plate coordinates, measured against SEGA's MATE UI.

    Keep structural transparency in plate/icon files. Cropping their alpha
    margins moves equipped art relative to the arcade's text and portrait slots.
    Frames are reconstructed, not claimed to be extracted game textures.
    """
    image = Image.new("RGBA", (1152, 456))
    draw = ImageDraw.Draw(image)
    plate = collection_image(player, "load_name_plate_image", trim=False)
    if plate is not None:
        _profile_paste(image, plate, (0, 0, 576, 228), contain=True)
    else:
        for y in range(206 * 2):
            gray = 230 + round(19 * y / 411)
            draw.line((132, y, 1151, y), fill=(gray, gray, gray, 255))
        draw.rounded_rectangle((133, 1, 1150, 411), radius=4,
                               outline=(255, 255, 255), width=3)

    _profile_trophy(image, player)

    # Draw body contents on their own layer and crop to the common frame.
    # Separate absolute borders used to protrude into the plate's alpha padding.
    content = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(content)
    left, top = PROFILE_LEFT * 2, PROFILE_INFO_TOP * 2
    right, bottom = PROFILE_RIGHT * 2, PROFILE_INFO_BOTTOM * 2
    divider = PROFILE_AVATAR_LEFT * 2
    draw.rectangle((left, top, divider-1, bottom-1), fill=(249, 249, 249, 220))
    draw.line((left+2, 323, divider-2, 323), fill=(158, 158, 158), width=2)
    draw.line((left+2, 325, divider-2, 325), fill=(255, 255, 255), width=2)

    avatar_size = PROFILE_INFO_BOTTOM - PROFILE_INFO_TOP - 4
    avatar_box = (PROFILE_AVATAR_LEFT+2, PROFILE_INFO_TOP+2, avatar_size, avatar_size)
    avatar_back = Image.new("RGBA", (avatar_size*2, avatar_size*2), (246, 246, 246, 255))
    stripes = ImageDraw.Draw(avatar_back)
    for x in range(-avatar_back.width, avatar_back.width, 7):
        stripes.line((x, avatar_back.height-1, x+avatar_back.width, 0),
                     fill=(220, 220, 220), width=2)
    _profile_paste(content, avatar_back, avatar_box)
    avatar = collection_image(player, "load_map_icon_image", trim=False)
    if avatar is None:
        avatar = collection_image(player, "load_character_image")
    _profile_paste(content, avatar, avatar_box, contain=True)
    draw.line((divider, top, divider, bottom-1), fill=(255, 255, 255), width=3)

    level = getattr(player, "level", None)
    if type(level) is int and level >= 0:
        profile_label(content, (148, 146), "Lv.", 22, 36, bold=True)
        profile_label(content, (184, 139), str(level), 37, 51, bold=True)
    reborn = getattr(player, "reborn_count", None)
    if type(reborn) is int and reborn > 0:
        _profile_paste(content, profile_asset("profile/reborn_star.webp"), (147, 104, 24, 24), contain=True)
        profile_label(content, (152, 115), str(reborn), 12, 18, bold=True)
    profile_label(content, (240, 135), getattr(player, "name", "PLAYER") or "PLAYER",
                  39, PROFILE_AVATAR_LEFT - 248, full_width=True, bold=True)
    _profile_rating(content, getattr(player, "rating_floor", None))

    emblem = getattr(player, "class_emblem", None)
    if isinstance(emblem, dict):
        for kind, box in (("base", (359, 170, 98, 21)), ("medal", (391, 157, 34, 34))):
            value = emblem.get(kind)
            if type(value) is int and 1 <= value <= 6:
                _profile_paste(content, profile_asset(f"profile/class_{kind}/{value}.webp"), box, contain=True)
    # Pillow rectangle endpoints are inclusive; inset the last pixel so no
    # outline can extend beyond the same half-open box used for sprite fitting.
    draw.rectangle((left, top, right-1, bottom-1), outline=(255, 255, 255), width=3)
    image.alpha_composite(content.crop((left, top, right, bottom)), (left, top))
    return image.resize((PROFILE_PLATE_WIDTH, PROFILE_PLATE_HEIGHT), LANCZOS)


def draw_profile(image, player, *, bottom=None):
    profile = render_profile(player)
    y = PROFILE_TOP
    if bottom is not None:
        bounds = profile.getchannel("A").getbbox()
        if bounds:
            # Keep internal asset coordinates; discard only trailing transparent
            # rows for placement, so the visible plate meets the content below.
            y = bottom - bounds[3]
    if image.mode == "RGBA":
        image.alpha_composite(profile, (MARGIN, y))
    else:
        image.paste(profile, (MARGIN, y), profile)


def draw_header(image: Image.Image, player, title: str, *, label: str = "", rating=None):
    draw = ImageDraw.Draw(image)
    width = image.width
    # Keep the wrapper argument for compatibility, but omit report-title slogans.
    best = title in {"BEST 30", "BEST 50"}
    if best:
        logo_width, logo_height = BEST_LOGO_SIZE
        left = MARGIN + PROFILE_PLATE_WIDTH + 28
        right = width - MARGIN - logo_width - 28
        if right > left:
            artwork = best_header_art(right-left)
            if artwork is not None:
                image.paste(artwork, (left, 0), artwork)
        draw_profile(image, player, bottom=BEST_HEADER_HEIGHT)
        draw_wordmark(image, width-MARGIN-logo_width,
                      (BEST_HEADER_HEIGHT-logo_height)//2, size=BEST_LOGO_SIZE)
    else:
        draw_profile(image, player)
        draw_wordmark(image, width - MARGIN - 345, 12)
    if label:
        label_right = width - MARGIN - 430
        text(draw, (label_right, 72), label, 48, kind="number", fill=MUTED, anchor="rt")
    # Player rating belongs inside the arcade profile, not a duplicate right badge.
    # Keep `rating` in the signature for older report wrappers; it may be a B30
    # average and must not replace the account's actual rating in the player panel.


def difficulty(draw, xy, level: int, *, size=28):
    label, color = DIFFICULTIES.get(level, DIFFICULTIES[3])
    x, y = xy
    width = math.ceil(text_width(label, font(size))) + 15
    draw.rounded_rectangle((x, y + 4, x + 4, y + size - 1), radius=2, fill=color)
    text(draw, (x + 15, y + 2), label, size, fill=color)
    return width


@lru_cache(maxsize=24)
def difficulty_label_masks(level, size=23):
    label, _ = DIFFICULTIES.get(level, DIFFICULTIES[3])
    face = font(size)
    bounds = face.getbbox(label, anchor="lt")
    stroke = max(1, round(size / 23))
    width = math.ceil(text_width(label, face)) + 15 + stroke * 2
    height = max(1, bounds[3]) + stroke * 2
    letters = Image.new("L", (width, height))
    ImageDraw.Draw(letters).text((stroke + 15, stroke), label, font=face, anchor="lt", fill=255)
    _, top, _, bottom = letters.getbbox()
    marker = Image.new("L", letters.size)
    ImageDraw.Draw(marker).rounded_rectangle((stroke, top, stroke + 4, bottom - 1), radius=2, fill=255)
    return letters, marker, stroke


@lru_cache(maxsize=24)
def difficulty_label_sprite(level, size=23):
    letters, marker, stroke = difficulty_label_masks(level, size)
    mask = ImageChops.lighter(letters, marker)
    sprite = Image.new("RGBA", mask.size, (255, 247, 230, 255))
    sprite.putalpha(mask.filter(ImageFilter.MaxFilter(stroke * 2 + 1)))
    ink = Image.new("RGBA", mask.size, (*DIFFICULTY_INK.get(level, DIFFICULTY_INK[3]), 255))
    ink.putalpha(mask)
    sprite.alpha_composite(ink)
    return sprite


def draw_difficulty_label(image, xy, level, *, size=23):
    sprite = difficulty_label_sprite(level, size)
    stroke = max(1, round(size / 23))
    pos = (xy[0] - stroke, xy[1] - stroke)
    if image.mode == "RGBA":
        image.alpha_composite(sprite, pos)
    else:
        image.paste(sprite, pos, sprite)
    return sprite.width - stroke * 2


def normalize_rank(rank):
    value = str(rank).strip().upper()
    return {"SP": "S+", "SSP": "SS+", "SSSP": "SSS+"}.get(value, value)


def _scale_rank(sprite, height):
    """Use ink height, never a per-grade width cap that shrinks SSS+."""
    height = max(1, round(height))
    width = max(1, round(sprite.width * height / sprite.height))
    return sprite.resize((width, height), LANCZOS)


@lru_cache(maxsize=64)
def rank_sprite(rank, size=RANK_HEIGHT):
    """Return proportional artwork with an identical visible height per size."""
    filename = RANK_ASSETS.get(normalize_rank(rank))
    if filename is None:
        return None
    try:
        with Image.open(ASSETS / "mate" / "result_ranks" / filename) as source:
            sprite = source.convert("RGBA")
            bounds = sprite.getchannel("A").getbbox()
            if not bounds:
                return None
            sprite = sprite.crop(bounds)
            return _scale_rank(sprite, size)
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=64)
def rank_text_sprite(rank, size=RANK_HEIGHT, fill=INK):
    value = normalize_rank(rank) or "?"
    face = font(size, "number")
    left, top, right, bottom = face.getbbox(value)
    sprite = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(sprite).text((-left, -top), value, font=face, fill=fill)
    bounds = sprite.getchannel("A").getbbox()
    return _scale_rank(sprite.crop(bounds) if bounds else sprite, size)


def rank_width(rank, size=RANK_HEIGHT):
    sprite = rank_sprite(rank, size)
    return (sprite if sprite is not None else rank_text_sprite(rank, size)).width


def draw_rank(image, right, top, rank, *, size=RANK_HEIGHT, fallback_fill=INK):
    rank = normalize_rank(rank)
    width = rank_width(rank, size)
    left = right - width
    sprite = rank_sprite(rank, size)
    if sprite is None:
        sprite = rank_text_sprite(rank, size, fallback_fill)
    position = (int(left), int(top))
    if image.mode == "RGBA":
        image.alpha_composite(sprite, position)
    else:
        image.paste(sprite, position, sprite)
    return left


def draw_jacket(image, score, box):
    x, y, size = box
    thumbnail = getattr(score, "load_jacket_thumbnail", None)
    if callable(thumbnail):
        jacket = thumbnail(size)
    else:
        jacket = ImageOps.fit(score.load_jacket_image().convert("RGBA"), (size, size), method=LANCZOS)
    image.paste(jacket, (x, y), jacket)


def jacket_card(score, layout):
    thumbnail = getattr(score, "load_jacket_thumbnail", None)
    try:
        if callable(thumbnail):
            jacket = thumbnail(layout.jacket_size)
        else:
            jacket = score.load_jacket_image()
        if jacket is None:
            raise ValueError("Missing jacket")
        return CARD_ART_CACHE.get(jacket, layout)
    except (OSError, ValueError, Image.DecompressionBombError):
        return CARD_ART_CACHE.get(Image.new("RGBA", (layout.jacket_size,) * 2, (28, 31, 48, 255)), layout)


def paste_jacket_card(image, card, x, y):
    if image.mode == "RGBA":
        image.alpha_composite(card, (x, y))
    else:
        image.paste(card, (x, y), card)


def draw_arrow(draw, x, y, length=48, *, fill=MUTED):
    draw.line((x, y, x + length, y), fill=fill, width=2)
    draw.line((x + length - 9, y - 7, x + length, y, x + length - 9, y + 7), fill=fill, width=2)


def card_separator(card, start, end):
    layer = Image.new("RGBA", card.size)
    ImageDraw.Draw(layer).line((*start, *end), fill=(255, 252, 242, 65), width=1)
    card.alpha_composite(layer)


def draw_score_card(image, score, x, y, index):
    card = jacket_card(score, SCORE_CARD_LAYOUT)
    draw = ImageDraw.Draw(card)
    right, tx = CARD_WIDTH, 207
    text(draw, (20, 12), f"{index+1:02d}", 23, kind="number", fill=CARD_INK)
    draw_difficulty_label(card, (tx, 18), score.level_index)
    draw_adaptive_title(draw, score.song_name, (tx, 65, right - tx - 18, 72))
    draw_rank(card, right - 18, 156, score.rank, fallback_fill=CARD_INK)
    number = f"{score.score:,}"
    size = 54
    # Reserve the widest rank so the score's type size doesn't change by grade.
    number_width = right - 18 - max(rank_width(r) for r in RANK_ASSETS) - tx - 12
    while size > 36 and text_width(number, font(size, "number")) > number_width:
        size -= 1
    text(draw, (tx, 155), number, size, kind="number", fill=CARD_INK)
    card_separator(card, (tx, 210), (right - 18, 210))
    text(draw, (20, 222), str(score.final_level), 32, kind="number", fill=CARD_MUTED)
    text(draw, (right - 23, 218), f"R {score.rating_floor:.2f}", 33,
         kind="number", fill=CARD_INK, anchor="rt")
    paste_jacket_card(image, card, x, y)


@dataclass(frozen=True)
class ScoreSection:
    title: str
    scores: Sequence
    accent: tuple = INK
    start_index: int = 0


def section_height(section: ScoreSection) -> int:
    rows = max(1, math.ceil(len(section.scores) / COLUMNS))
    return (SECTION_HEIGHT if section.title else 0) + rows * CARD_HEIGHT + (rows - 1) * GAP + 32


def score_sheet_height(sections: Sequence[ScoreSection], *, best=False) -> int:
    content_top = BEST_HEADER_HEIGHT if best else HEADER_HEIGHT + 20
    return content_top + sum(section_height(s) for s in sections) + 12


def render_score_sheet(player, sections: Sequence[ScoreSection], title: str,
                       *, label="", rating=None) -> Image.Image:
    best = title in {"BEST 30", "BEST 50"}
    image = canvas(SHEET_WIDTH, score_sheet_height(sections, best=best),
                   header_height=BEST_HEADER_HEIGHT if best else HEADER_HEIGHT)
    draw_header(image, player, title, label=label, rating=rating)
    draw = ImageDraw.Draw(image)
    y = BEST_HEADER_HEIGHT if best else HEADER_HEIGHT + 20
    for section in sections:
        heading = SECTION_HEIGHT if section.title else 0
        if section.title:
            draw_section_tab(image, (MARGIN, y + 4), section.title, section.accent)
        for i, score in enumerate(section.scores):
            row, col = divmod(i, COLUMNS)
            draw_score_card(image, score, MARGIN + col * (CARD_WIDTH + GAP),
                            y + heading + row * (CARD_HEIGHT + GAP), section.start_index + i)
        if not section.scores:
            text(draw, (MARGIN + 24, y + heading + 40), "暂无成绩", 40, kind="cjk", fill=MUTED)
        y += section_height(section)
    return image


def draw_fit_card(image, entry, x, y):
    card = jacket_card(entry.play, FIT_CARD_LAYOUT)
    draw = ImageDraw.Draw(card)
    right, tx = FIT_CARD_WIDTH, 228
    draw_adaptive_title(draw, entry.title, (tx, 22, FIT_CARD_WIDTH - 252, 78), min_size=34, max_size=44)
    draw_difficulty_label(card, (tx, 110), entry.difficulty, size=25)
    card_separator(card, (tx, 148), (right - 24, 148))
    number = f"{entry.play.score:,}"
    text(draw, (tx + 2, 172), number, 45, kind="number", fill=CARD_INK)
    grade_left = tx + math.ceil(text_width(number, font(45, "number"))) + 18
    draw_rank(card, grade_left + rank_width(entry.play.rank), 171, entry.play.rank, fallback_fill=CARD_INK)
    text(draw, (right - 280, 177), f"{entry.constant:.1f}", 38, kind="number", fill=CARD_MUTED)
    draw_arrow(draw, right - 201, 196, length=30, fill=CARD_MUTED)
    text(draw, (right - 25, 160), f"{entry.fitted:.2f}", 64, kind="number", fill=CARD_INK, anchor="rt")
    paste_jacket_card(image, card, x, y)


def fit_sheet_height(count: int) -> int:
    rows = max(1, math.ceil(count / FIT_COLUMNS))
    return FIT_HEADER_HEIGHT + rows * FIT_CARD_HEIGHT + (rows - 1) * GAP + 38


def render_fit_sheet(player, entries: Sequence, label: str) -> Image.Image:
    image = canvas(FIT_WIDTH, fit_sheet_height(len(entries)))
    draw_header(image, player, "FIT CONSTANT", label=label)
    draw = ImageDraw.Draw(image)
    sssp = sum(e.play.rank == "SSS+" for e in entries)
    draw_section_tab(image, (MARGIN, HEADER_HEIGHT + 12), f"{len(entries)} CHARTS   /   SSS+ {sssp}")
    for i, entry in enumerate(entries):
        row, col = divmod(i, FIT_COLUMNS)
        draw_fit_card(image, entry, MARGIN + col * (FIT_CARD_WIDTH + GAP),
                      FIT_HEADER_HEIGHT + row * (FIT_CARD_HEIGHT + GAP))
    if not entries:
        text(draw, (MARGIN + 24, FIT_HEADER_HEIGHT + 40), "暂无成绩", 40, kind="cjk", fill=MUTED)
    return image


def render_single_sheet(player, score, title: str) -> Image.Image:
    image = canvas(SINGLE_WIDTH, SINGLE_HEIGHT)
    draw_header(image, player, title)
    card = jacket_card(score, SINGLE_CARD_LAYOUT)
    draw = ImageDraw.Draw(card)
    right, tx = card.width, 714 - MARGIN
    draw_adaptive_title(draw, score.song_name, (tx, 33, right - tx - 32, 186),
                        min_size=49, max_size=58, max_lines=3)
    badge = draw_difficulty_label(card, (tx, 246), score.level_index, size=35)
    text(draw, (tx + badge + 28, 245), str(score.final_level), 43, kind="number", fill=CARD_INK)
    text(draw, (tx, 359), f"{score.score:,}", 142, kind="number", fill=CARD_INK)
    score_end = tx + text_width(f"{score.score:,}", font(142, "number"))
    draw_rank(card, min(right - 32, score_end + 44 + rank_width(score.rank, SINGLE_RANK_HEIGHT)),
              373, score.rank, size=SINGLE_RANK_HEIGHT, fallback_fill=CARD_INK)
    card_separator(card, (26, 652), (right - 30, 652))
    text(draw, (26, 681), "RATING", 43, fill=CARD_MUTED)
    text(draw, (right - 30, 669), f"{score.rating_floor:.2f}", 78, kind="number", fill=CARD_INK, anchor="rt")
    paste_jacket_card(image, card, MARGIN, 314)
    return image


def save_report(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    credited = append_image_credit(image, font(28), footer_fill=WHITE,
                                    text_fill=MUTED, rule_fill=YELLOW)
    # PNG remains lossless; avoid the expensive maximum-compression optimizer.
    credited.save(path, format="PNG", optimize=False, compress_level=3)
    return path
