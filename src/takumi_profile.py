"""Offline TAKUMI nameplate renderer; only blank frames/art are cached.

Geometry follows the official result-screen reference. Equipped icon/title
values are supplied explicitly, never inferred from rating or a demo account.
"""
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from src.chunithm_render import ellipsis, font, text_width
from src.takumi_collections import ASSETS, PlayerCollections, load_icon

PROFILE_SIZE = (960, 218)
PROFILE_OUTPUT_SIZE = (1080, 245)
LANCZOS = Image.Resampling.LANCZOS


@lru_cache(maxsize=40)
def bold(size):
    for path in (
        ASSETS.parents[3] / "data/b30_assets/mate/fonts/NotoSansCJKjp-Bold.otf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
    ):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return font(size, "song")


@lru_cache(maxsize=40)
def tech(size):
    try:
        return ImageFont.truetype(str(ASSETS / "fonts/Audiowide-Regular.ttf"), size)
    except OSError:
        return font(size, "ui")


def _text(draw, xy, value, face, *, centered=False, stroke=0):
    left, top, right, bottom = draw.textbbox((0, 0), value, font=face)
    x, y = xy
    if centered:
        x -= (right-left)/2
        y -= (bottom-top)/2
    draw.text((round(x-left), round(y-top)), value, font=face, fill="#f4fbff",
              stroke_width=stroke, stroke_fill="#062632")


@lru_cache(maxsize=1)
def profile_frame():
    base = Image.new("RGBA", PROFILE_SIZE)
    draw = ImageDraw.Draw(base)
    edge = Image.new("RGBA", PROFILE_SIZE)
    lines = ImageDraw.Draw(edge)
    avatar = [(13, 108), (62, 13), (242, 13), (290, 108), (242, 203), (62, 203)]
    name = [(267, 13), (843, 13), (890, 107), (314, 107)]
    halo = Image.new("RGBA", PROFILE_SIZE)
    halo_draw = ImageDraw.Draw(halo)
    rim = Image.new("L", PROFILE_SIZE)
    rim_draw = ImageDraw.Draw(rim)
    for polygon in (avatar, name):
        draw.polygon(polygon, fill=(0, 42, 58, 242))
        lines.line(polygon + polygon[:1], fill=(0, 224, 249, 200), width=3, joint="curve")
        halo_draw.line(polygon + polygon[:1], fill=(0, 219, 248, 145), width=12, joint="curve")
        rim_draw.polygon(polygon, fill=255)
    base.alpha_composite(halo.filter(ImageFilter.GaussianBlur(7)))
    inner = ImageChops.subtract(rim, rim.filter(ImageFilter.MinFilter(19)))
    inner = ImageChops.multiply(inner.filter(ImageFilter.GaussianBlur(4)), rim)
    sheen = Image.new("RGBA", PROFILE_SIZE, (0, 187, 218, 255))
    sheen.putalpha(inner.point(lambda value: round(value * .6)))
    base.alpha_composite(sheen)
    base.alpha_composite(edge.filter(ImageFilter.GaussianBlur(2)))
    base.alpha_composite(edge)
    draw = ImageDraw.Draw(base)
    draw.line([(684, 14), (731, 106)], fill=(26, 185, 206, 210), width=2)
    draw.rectangle((69, 25, 236, 191), outline=(35, 136, 161, 220), width=2)
    for points in ([(63, 49), (63, 21), (103, 21)],
                   [(202, 21), (244, 21), (244, 49)],
                   [(63, 168), (63, 194), (104, 194)],
                   [(202, 194), (244, 194), (244, 168)]):
        draw.line(points, fill=(16, 108, 131, 210), width=2)
    draw.line([(31, 76), (14, 109), (32, 145)], fill="#16d9ee", width=5)
    draw.line([(267, 75), (285, 109), (268, 145)], fill="#13bcd0", width=4)
    for offset, alpha in ((0, 220), (18, 140), (34, 85)):
        draw.polygon([(310 + offset, 130), (318 + offset, 150),
                      (291 + offset, 203), (273 + offset, 203)], fill=(0, 195, 218, alpha))
    draw.line([(867, 13), (941, 13), (941, 203)], fill=(21, 171, 190, 180), width=2)
    return base


@lru_cache(maxsize=7)
def _title_plate(rank):
    if type(rank) is not int or not 0 <= rank <= 6:
        return None
    try:
        with Image.open(ASSETS / f"title_{rank}.png") as source:
            # The menu asset has different corner cuts. Reuse its inner
            # texture, clipped to the actual result-screen parallelogram;
            # otherwise its right edge crosses the outline into an X.
            texture = source.convert("RGBA").crop((32, 0, source.width-32, source.height))
            plate = texture.resize((620, 76), LANCZOS)
            mask = Image.new("L", plate.size)
            ImageDraw.Draw(mask).polygon([(0, 0), (583, 0), (619, 75), (35, 75)], fill=255)
            plate.putalpha(ImageChops.multiply(plate.getchannel("A"), mask))
            return plate
    except OSError:
        return None


def _title_lines(title, face, width):
    lines, current = [], ""
    for char in title:
        if current and text_width(current + char, face) > width:
            lines.append(current)
            current = char
        else:
            current += char
    return lines + ([current] if current else [])


def player_panel(player_name, rating, collections=None):
    collections = collections or PlayerCollections()
    image = profile_frame().copy()
    icon = load_icon(collections.icon_id)
    if icon is not None:
        image.alpha_composite(ImageOps.contain(icon, (150, 150), LANCZOS), (78, 34))
    draw = ImageDraw.Draw(image)
    banner = [(322, 127), (905, 127), (941, 203), (357, 203)]
    draw.polygon(banner, fill="#084052")
    plate = _title_plate(collections.title_rank) if collections.title else None
    if plate is not None:
        image.alpha_composite(plate, (322, 127))
    draw.line(banner + banner[:1], fill="#13c2d9", width=1)
    name_face = bold(45)
    name = ellipsis(player_name or "PLAYER", name_face, 354)
    _text(draw, (334, 43), name, name_face)
    _text(draw, (719, 27), "RATING", tech(14))
    rating_text = f"{rating:.3f}" if rating is not None else "—"
    _text(draw, (855-text_width(rating_text, tech(27)), 58), rating_text, tech(27))
    if collections.title:
        face_fn = tech if collections.title.isascii() else bold
        size = 32
        while size > 24 and text_width(collections.title, face_fn(size)) > 538:
            size -= 1
        face = face_fn(size)
        lines = _title_lines(collections.title, face, 538)
        # Long equipped titles get two readable lines, not tiny explanation text.
        lines = lines[:1] + [ellipsis("".join(lines[1:]), face, 538)] if len(lines) > 2 else lines
        for n, line in enumerate(lines):
            _text(draw, (635, 165+(n-(len(lines)-1)/2)*29), line, face, centered=True, stroke=1)
    return image.resize(PROFILE_OUTPUT_SIZE, LANCZOS)
