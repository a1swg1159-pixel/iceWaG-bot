"""MAGiCAL maimai report rendering. Local Pillow UI; no API or account access.

Public art effects are bounded/cached; player names and rendered scores are not.
"""
from functools import lru_cache
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from src.chunithm_card_art import CARD_ART_CACHE, CardLayout
from src.chunithm_render import adaptive_card_title, ellipsis, font, text_width
from src.image_credit import append_image_credit

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "maimai_assets" / "magical"
LANCZOS = Image.Resampling.LANCZOS
W, CW, CH, GAP, LEFT, HEADER = 3000, 568, 236, 22, 40, 270
SECTION_H = 88
PROFILE_H = 220
# MAGiCAL follows the CiRCLE PLUS rating plates. Verified against SEGA's
# complete table, not the older thresholds in third-party renderers:
# https://info-maimai.sega.jp/8674/ (2026-03-10 announcement).
# Lower bound, sprite colour, attached star count.
RATING_BANDS = (
    (0, "white", 0), (1000, "blue", 0), (2000, "green", 0),
    (4000, "yellow", 0), (7000, "red", 0), (10000, "purple", 0),
    (12000, "bronze", 0), (13000, "silver", 0),
    (14000, "gold", 1), (14250, "gold", 2),
    (14500, "platinum", 1), (14750, "platinum", 2),
    (15000, "rainbow", 1), (15250, "rainbow", 2),
    (15500, "rainbow", 3), (15750, "rainbow", 4),
    (16000, "kiwami", 1), (16250, "kiwami", 2),
    (16500, "kiwami", 3), (16750, "kiwami", 4),
)
LAYOUT = CardLayout(CW, CH, 184, 22, 26)
INK, MINT, GOLD = "#123f4c", "#179c8f", "#e4c66f"
DIFFICULTIES = (
    ("BASIC", "#368d4e"), ("ADVANCED", "#a37007"),
    ("EXPERT", "#cf3e58"), ("MASTER", "#9744bc"),
    ("Re:MASTER", "#f5e9ff"),
)


@lru_cache(maxsize=60)
def rounded(size):
    try:
        return ImageFont.truetype(str(ASSETS / "MPLUSRounded1c-ExtraBold.ttf"), size)
    except OSError:
        return font(size, "song")


@lru_cache(maxsize=80)
def raw_sprite(name):
    try:
        with Image.open(ASSETS / name) as im:
            return im.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


@lru_cache(maxsize=80)
def sprite(name):
    result = raw_sprite(name)
    if result is None:
        return None
    alpha = result.getchannel("A")
    bounds = alpha.point(lambda a: 255 if a >= 24 else 0).getbbox() if name.startswith("grade_") else alpha.getbbox()
    return result.crop(bounds) if bounds else result


def fit_sprite(image, name, box, opacity=1.0, align="center"):
    x, y, width, height = box
    item = sprite(name)
    if item is None:
        return (0, 0)
    factor = min(width / item.width, height / item.height)
    item = item.resize((max(1, round(item.width * factor)), max(1, round(item.height * factor))), LANCZOS)
    if opacity < 1:
        item.putalpha(item.getchannel("A").point(lambda a: round(a * opacity)))
    x += (width - item.width) // 2 if align == "center" else width - item.width if align == "right" else 0
    y += (height - item.height) // 2
    image.alpha_composite(item, (x, y))
    return item.size


def txt(draw, xy, value, size, fill=INK, kind="ui", anchor="lt"):
    face = rounded(size) if kind == "round" else font(size, kind)
    draw.text(xy, str(value), font=face, fill=fill, anchor=anchor)


@lru_cache(maxsize=5)
def difficulty(level):
    label, color = DIFFICULTIES[level]
    face = rounded(23)
    b = face.getbbox(label, anchor="lt")
    mask = Image.new("L", (math.ceil(text_width(label, face)) + 20, b[3] + 4))
    d = ImageDraw.Draw(mask)
    d.text((16, 2), label, font=face, fill=255, anchor="lt")
    _, top, _, bottom = mask.getbbox()
    d.rounded_rectangle((2, top, 6, bottom - 1), radius=2, fill=255)
    outline = Image.new("RGBA", mask.size, "#af71d2" if level == 4 else "#fff9e8")
    outline.putalpha(mask.filter(ImageFilter.MaxFilter(3)))
    inside = Image.new("RGBA", mask.size, color)
    inside.putalpha(mask)
    outline.alpha_composite(inside)
    return outline


def grade(achievement):
    for threshold, key, factor in (
        (100.5, "sssp", 22.4), (100, "sss", 21.6), (99.5, "ssp", 21.1),
        (99, "ss", 20.8), (98, "sp", 20.3), (97, "s", 20),
        (94, "aaa", 16.8), (90, "aa", 15.2), (80, "a", 13.6),
        (75, "bbb", 12), (70, "bb", 11.2), (60, "b", 9.6),
        (50, "c", 8), (0, "d", 0),
    ):
        if achievement >= threshold:
            return key, factor


def jacket(score):
    try:
        loader = getattr(score, "load_jacket_thumbnail", None)
        image = loader(184) if callable(loader) else score.load_jacket()
        if image is not None:
            return image
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
    return Image.new("RGBA", (184, 184), (28, 31, 48, 255))


def badge_key(value, *, sync=False):
    key = str(value or "").lower().replace("+", "p")
    aliases = {"fsd": "fdx", "fsdp": "fdxp", "fdxp": "fdxp", "fdx": "fdx"}
    key = aliases.get(key, key)
    allowed = {"fs", "fsp", "fdx", "fdxp", "sync"} if sync else {"fc", "fcp", "ap", "app"}
    return key if key in allowed else ""


def score_rank(score):
    key = str(getattr(score, "rank", "")).lower().replace("+", "p")
    if key in {"d", "c", "b", "bb", "bbb", "a", "aa", "aaa", "s", "sp", "ss", "ssp", "sss", "sssp"}:
        return key
    return grade(score.achievements)[0]


def card(score, index=1):
    image = CARD_ART_CACHE.get(jacket(score), LAYOUT)
    # Keep pale jackets readable without changing the sharp jacket itself.
    shade = Image.new("RGBA", image.size, (10, 18, 26, 0))
    ramp = Image.new("L", (CW, 1))
    ramp.putdata([round(24 * min(1, max(0, (x - 184) / 120))) for x in range(CW)])
    shade.putalpha(ImageChops.multiply(ramp.resize(image.size), image.getchannel("A")))
    image.alpha_composite(shade)
    d = ImageDraw.Draw(image)
    # Fine inner line; no extra coloured panels around individual text rows.
    d.rounded_rectangle((0, 0, CW - 1, CH - 1), radius=17, outline=(229, 244, 223, 125), width=1)
    image.alpha_composite(difficulty(score.level_index if score.level_index in range(5) else 3), (224, 16))
    fit_sprite(image, f"mode_{'dx' if score.song_type == 'dx' else 'standard'}.webp", (415, 18, 53, 22))
    txt(d, (550, 17), str(score.level), 29, "#fff7df", "number", "rt")
    # Original jacket dimensions, softly feathered into its own blurred backdrop.
    details = Image.new("RGBA", image.size)
    details_draw = ImageDraw.Draw(details)
    details_draw.rounded_rectangle((31, 34, 87, 61), radius=9, fill=(15, 25, 34, 175))
    details_draw.line((226, 183, 550, 183), fill=(255, 249, 214, 65), width=1)
    image.alpha_composite(details)
    txt(d, (59, 37), f"#{index:02}", 20, "#fffae9", "display", "mt")
    lines, size = adaptive_card_title(score.song_name, 324, min_size=26, max_size=36, max_lines=2)
    face = font(size, "song")
    text_height = (len(lines) - 1) * (size + 5) + face.getbbox(lines[-1], anchor="lt")[3]
    y = 58 + max(0, (62 - text_height) // 2)
    for line in lines:
        txt(d, (226, y), line, size, "#fffdf5", "song")
        y += size + 5
    # Fixed visible grade height. Four decimal places never disappear.
    value = f"{score.achievements:.4f}"
    number_font = font(47, "number")
    number_w = text_width(value, number_font)
    assert number_w + text_width("%", font(23)) < 224, value
    txt(d, (226, 130), value, 47, "#fffbea", "number")
    txt(d, (230 + number_w, 151), "%", 22, "#f9eccb")
    key = score_rank(score)
    item = sprite(f"grade_{key}.webp")
    if item is not None:
        rank_h = 37
        item = item.resize((round(item.width * rank_h / item.height), rank_h), LANCZOS)
        image.alpha_composite(item, (550 - item.width, 136))
    else:
        txt(d, (550, 136), key.upper().replace("P", "+"), 30, "#ffdfa0", "display", "rt")
    txt(d, (226, 189), score.dx_rating, 39, "#ffdfa0", "number")
    fc = badge_key(getattr(score, "fc", ""))
    fs = badge_key(getattr(score, "fs", ""), sync=True)
    if fc:
        fit_sprite(image, f"badge_{fc}.webp", (432, 189, 48, 39))
    if fs:
        fit_sprite(image, f"badge_{fs}.webp", (493, 189, 48, 39))
    return image


def background(width, height):
    # A single vertical ramp avoids expensive per-pixel Python processing.
    strip = Image.new("RGB", (1, height))
    strip.putdata([
        (round(232 + 8 * y / height), round(249 - 6 * y / height), round(243 - 13 * y / height))
        for y in range(height)
    ])
    return strip.resize((width, height)).convert("RGBA")


@lru_cache(maxsize=10)
def rating_digit(digit):
    source = raw_sprite("profile_rating_digits.webp")
    if source is None:
        return None
    w, h = source.width // 4, source.height // 4
    return source.crop(((digit % 4) * w, (digit // 4) * h,
                        (digit % 4 + 1) * w, (digit // 4 + 1) * h))


@lru_cache(maxsize=64)
def native_sprite(name, size):
    # Read-only static UI; never modify an image returned from this cache.
    source = raw_sprite(name)
    if source is None:
        return Image.new("RGBA", size)
    return source.resize(size, LANCZOS)


def rating_style(rating):
    rating = max(0, int(rating))
    return next((color, stars) for threshold, color, stars in reversed(RATING_BANDS)
                if rating >= threshold)


def draw_rating(profile, rating, height):
    # Five native number cells. Reject impossible overflow instead of clipping
    # digits or silently showing only the final five places.
    rating = max(0, int(rating))
    if rating > 99999:
        raise ValueError("Rating exceeds the five-digit cabinet field")
    rating_h = round(height / 3)
    rating_w = round(rating_h * 664 / 130)
    x, y = height, round(height * .064)
    color, stars = rating_style(rating)
    profile.alpha_composite(native_sprite(f"profile_rating_{color}.webp", (rating_w, rating_h)), (x, y))
    if raw_sprite(f"profile_rating_{color}.webp") is None:
        fallback = ImageDraw.Draw(profile)
        fallback.rounded_rectangle((x, y, x + rating_w, y + rating_h), radius=12, fill="#263f48", outline="#d8ca95", width=2)
        txt(fallback, (x + 12, y + 24), "RATING", 26, "#ffffff", "display")
    if stars:
        profile.alpha_composite(native_sprite(f"profile_star_{stars}.webp", (round(rating_h * 72 / 130), rating_h)),
                                (x + round(rating_w * 616 / 664), y))
    digits = str(rating).rjust(5)
    digit_h = round(height * 11 / 64)
    number_x, number_y = round(height * 1.785), round(height * .15)
    for i, digit in enumerate(digits):
        if digit.isspace():
            continue
        tile = rating_digit(int(digit))
        if tile is None:
            txt(ImageDraw.Draw(profile), (number_x + round(i * digit_h * .79), number_y), digit, digit_h, "#ffe77c", "display")
            continue
        digit_w = round(tile.width * digit_h / tile.height)
        profile.alpha_composite(tile.resize((digit_w, digit_h), LANCZOS),
                                (number_x + round(i * digit_w * .89), number_y))


def draw_nickname(draw, nickname, box):
    """One shaped text run, natural kerning and one shared alphabetic baseline."""
    x, y, width, height = box
    nickname = " ".join(str(nickname).split())
    name_size = 42
    while name_size > 20 and text_width(nickname, font(name_size, "song")) > width:
        name_size -= 1
    face = font(name_size, "song")
    nickname = ellipsis(nickname, face, width)
    left, top, right, bottom = face.getbbox(nickname, anchor="ls")
    baseline = y + (height - (bottom - top)) // 2 - top
    # Per-glyph anchor='lt' incorrectly lifted lowercase glyphs. Drawing the
    # entire string retains font kerning and keeps i/c/e/W/a/G on one baseline.
    draw.text((x - left, baseline), nickname, font=face, anchor="ls", fill="#151719")


def collection_image(player, method):
    loader = getattr(player, method, None)
    if not callable(loader):
        return None
    try:
        image = loader()
        return image.convert("RGBA") if image is not None else None
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def player_panel(player):
    """Render real collections, with neutral placeholders when unavailable."""
    height, width = PROFILE_H, round(PROFILE_H * 720 / 116)
    plate = collection_image(player, "load_name_plate_image")
    profile = ImageOps.fit(plate, (width, height), method=LANCZOS) if plate is not None else background(width, height)
    draw = ImageDraw.Draw(profile)
    if plate is None:
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=9, outline="#c9b477", width=2)
    icon_size, inset = round(height * .872), round(height * .064)
    icon = collection_image(player, "load_icon")
    if icon is None:
        icon = Image.new("RGBA", (icon_size, icon_size), "#d3e9e2")
        avatar_draw = ImageDraw.Draw(icon)
        avatar_draw.ellipse((62, 27, 130, 95), fill="#81aaa0")
        avatar_draw.rounded_rectangle((28, 111, 164, 191), radius=43, fill="#81aaa0")
    else:
        icon = ImageOps.fit(icon, (icon_size, icon_size), method=LANCZOS)
    profile.alpha_composite(icon, (inset, inset))
    draw_rating(profile, player.rating, height)
    x = round(height * (1 + 1 / 64))
    y = round(height * (.064 + .333 + 1 / 32))
    name_w, name_h = round(height / 3 * 5.108 * 6 / 5), round(height * 7 / 24)
    draw.rounded_rectangle((x - 3, y - 3, x + name_w + 3, y + name_h + 3), radius=11, fill="#aaa9a7")
    draw.rounded_rectangle((x, y, x + name_w, y + name_h), radius=9, fill="#fffefe")
    draw.line((x + 10, y + name_h - 1, x + name_w - 10, y + name_h - 1), fill="#dfded8", width=2)
    draw_nickname(draw, player.name, (x + 16, y, name_w - 32, name_h))
    trophy = str(getattr(player, "trophy", "") or "")
    if trophy:
        trophy_y, trophy_h = y + name_h + 11, 35
        strip = Image.new("RGBA", (name_w + 4, trophy_h))
        sd = ImageDraw.Draw(strip)
        sd.rounded_rectangle((0, 0, strip.width - 1, strip.height - 1), radius=11, fill="#c4c3bc", outline="#9d9f96", width=1)
        sd.rounded_rectangle((2, 2, strip.width - 3, strip.height - 3), radius=9, fill="#faf9ef")
        title_size = 22
        while title_size > 15 and text_width(trophy, font(title_size, "song")) > name_w - 18:
            title_size -= 1
        trophy = ellipsis(trophy, font(title_size, "song"), name_w - 18)
        sd.text((strip.width // 2, 5), trophy, font=font(title_size, "song"), anchor="mt", fill="#333c37")
        profile.alpha_composite(strip, (x - 2, trophy_y))
    return profile


@lru_cache(maxsize=1)
def header_art():
    image = Image.new("RGBA", (W, HEADER))
    sky = sprite("sky.png")
    if sky is not None:
        scene = ImageOps.fit(sky, (1480, HEADER), method=LANCZOS, centering=(.5, .57))
        fade_x = Image.new("L", (scene.width, 1))
        fade_x.putdata([round(170 * min(1, x / 260, (scene.width - 1 - x) / 260)) for x in range(scene.width)])
        fade_y = Image.new("L", (1, HEADER))
        fade_y.putdata([round(255 * min(1, y / 30, (HEADER - 1 - y) / 70)) for y in range(HEADER)])
        mask = ImageChops.multiply(fade_x.resize(scene.size), fade_y.resize(scene.size))
        scene.putalpha(ImageChops.multiply(scene.getchannel("A"), mask))
        image.alpha_composite(scene, (1220, 0))
    fit_sprite(image, "characters.png", (1690, 4, 370, 258))
    fit_sprite(image, "logo.png", (2385, 4, 540, 257))
    ImageDraw.Draw(image).line((LEFT, HEADER - 1, W - LEFT, HEADER - 1), fill=GOLD, width=2)
    return image


def header(image, player):
    image.alpha_composite(header_art())
    image.alpha_composite(player_panel(player), (LEFT, HEADER - PROFILE_H - 12))


def section(image, y, title):
    fit_sprite(image, "section.png", (LEFT - 3, y + 2, CW + 6, 77))
    d = ImageDraw.Draw(image)
    size = 37
    while size > 24 and text_width(title, rounded(size)) > CW - 110:
        size -= 1
    title = ellipsis(title, rounded(size), CW - 110)
    txt(d, (LEFT + CW // 2, y + 22), title, size, INK, "round", "mt")


def credit(image):
    return append_image_credit(image, font(26, "ui"), footer_fill=(246, 252, 246),
                               text_fill=(44, 100, 99), rule_fill=(220, 193, 120))


def draw_grid(image, scores, y, index_offset=0):
    for i, score in enumerate(scores):
        x = LEFT + (i % 5) * (CW + GAP)
        top = y + (i // 5) * (CH + GAP)
        image.alpha_composite(card(score, i + 1 + index_offset), (x, top))


def b50(player, old, new):
    old_y = HEADER + 8
    old_cards_y = old_y + SECTION_H
    new_y = old_cards_y + 7 * CH + 6 * GAP + 24
    new_cards_y = new_y + SECTION_H
    image = background(W, new_cards_y + 3 * CH + 2 * GAP + 40)
    header(image, player)
    section(image, old_y, "BEST 35")
    draw_grid(image, old[:35], old_cards_y)
    section(image, new_y, "BEST 15")
    draw_grid(image, new[:15], new_cards_y)
    # Compact the section bars, never the original card dimensions.
    return credit(image)


def score_list(player, scores, title, *, index_offset=0):
    rows = max(1, math.ceil(len(scores) / 5))
    y = HEADER + 8 + SECTION_H
    height = y + rows * CH + max(0, rows - 1) * GAP + 40
    image = background(W, height)
    header(image, player)
    section(image, HEADER + 8, title)
    draw_grid(image, scores, y, index_offset)
    return credit(image)
