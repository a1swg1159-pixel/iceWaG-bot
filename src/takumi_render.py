"""Approved TAKUMI official-style sheets, migrated from the offline design study.

Rendering is local-only. The supplied core is used solely for local atlas
lookups, rating/medal helpers and grouping; this module never fetches accounts.
Static artwork is cached; player names, equipment and scores are not.
"""
from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from src.chunithm_card_art import CARD_ART_CACHE, CardLayout
from src.chunithm_render import adaptive_card_title, font, text_width
from src.image_credit import append_image_credit
from src.takumi_profile import player_panel, tech

ASSETS = Path(__file__).parent / "plugins/takumi_b40/theme_assets"
LANCZOS = Image.Resampling.LANCZOS
W, CW, CH, GAP_X, GAP_Y, MARGIN = 3000, 555, 228, 24, 16, 64
COLS, START_Y, RANK_H = 5, 374, 43
CCW, CCH, CCOLS, CGAP_X, CGAP_Y, GROUP_H = 350, 120, 8, 10, 8, 80
WHITE, SOFT, CYAN, DARK = "#f4fbff", "#acceda", "#51eaff", "#07131d"
DIFFICULTIES = {
    "NORMAL": "#69d2ff", "HARD": "#ffd575", "MASTER": "#dda5ff",
    "INSANITY": "#e9edf4", "RAVAGE": "#ff8ca7",
}
LAYOUT = CardLayout(CW, CH, 164, 16, 32, 12)
CONST_LAYOUT = CardLayout(CCW, CCH, 90, 10, 15, 9)

@dataclass(frozen=True)
class Sample:
    song_id: int
    title: str
    difficulty: str
    constant: float
    score: int | None
    medal: int | None = None
    single_rating: float = 0.0


@lru_cache(maxsize=6)
def asset(name):
    try:
        with Image.open(ASSETS / name) as source:
            return source.convert("RGBA")
    except OSError:
        return Image.new("RGBA", (100, 100))


class TakumiRenderer:
    def __init__(self, core):
        self.core = core

    def cut_polygon(self, box, cut=14):
        x, y, r, b = box
        return [(x + cut, y), (r - cut, y), (r, y + cut),
                (r, b - cut), (r - cut, b), (x + cut, b),
                (x, b - cut), (x, y + cut)]


    @lru_cache(maxsize=10)
    def cut_mask(self, width, height, cut):
        mask = Image.new("L", (width, height))
        ImageDraw.Draw(mask).polygon(self.cut_polygon((0, 0, width - 1, height - 1), cut), fill=255)
        return mask


    def panel(self, draw, box, cut=14, fill=None, outline="#356276", width=1):
        points = self.cut_polygon(box, cut)
        if fill is not None:
            draw.polygon(points, fill=fill)
        if outline is not None:
            draw.line(points + points[:1], fill=outline, width=width, joint="curve")


    def top_text(self, draw, xy, text, face, fill=WHITE, align="left", stroke=0):
        """Place the visible ink, not the font's ascender padding, at the given y."""
        text = str(text)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
        x, y = xy
        if align == "right":
            x -= right - left
        elif align == "center":
            x -= (right - left) / 2
        draw.text((round(x - left), round(y - top)), text, font=face, fill=fill,
                  stroke_width=stroke, stroke_fill="#07131d")
        return (round(x), y, round(x + right - left), y + bottom - top)


    def credit(self, image):
        return append_image_credit(image, font(23, "ui"), footer_fill=(7, 19, 29),
                                   text_fill=(171, 205, 214), rule_fill=(27, 78, 96))


    def background(self, width, height):
        image = Image.new("RGBA", (width, height), DARK)
        # Keep the official pattern at a fixed spatial scale: a long const sheet
        # doesn't stretch the texture or change the width of any content.
        backdrop, reverse = self._background_tiles(width)
        for row, y in enumerate(range(0, height, backdrop.height)):
            # Mirrored continuation avoids a bright horizontal restart seam on
            # long const sheets. The pattern's scale is still independent of length.
            image.alpha_composite(reverse if row % 2 else backdrop, (0, y))
        return image

    @lru_cache(maxsize=2)
    def _background_tiles(self, width):
        backdrop = asset("scifi_bg.png")
        backdrop = backdrop.resize((width, round(width * backdrop.height / backdrop.width)), LANCZOS)
        backdrop.alpha_composite(Image.new("RGBA", backdrop.size, (3, 11, 18, 168)))
        return backdrop, ImageOps.flip(backdrop)

    @lru_cache(maxsize=1)
    def _header_art(self):
        return (asset("ras.png").resize((595, 595), LANCZOS),
                ImageOps.contain(asset("logo.png"), (790, 244), LANCZOS))


    def header(self, image, player_name, rating, label, collections=None):
        strip = Image.new("RGBA", (W, 290))
        strip.alpha_composite(Image.new("RGBA", strip.size, (0, 13, 24, 80)))
        # Official RA-S artwork is decoration, not a claimed equipped collection.
        character, logo = self._header_art()
        strip.alpha_composite(character, (1270, 7))
        strip.alpha_composite(logo, (W - MARGIN - logo.width, 22))
        image.alpha_composite(strip, (0, 0))
        draw = ImageDraw.Draw(image)
        image.alpha_composite(player_panel(player_name, rating, collections), (MARGIN - 13, 34))
        # A single short navigation tab, with no additional summary-box row.
        tab_w = max(330, math.ceil(text_width(label, tech(32))) + 90)
        self.panel(draw, (MARGIN, 300, MARGIN + tab_w, 350), 18,
              fill="#073947", outline="#41c5d9", width=2)
        self.top_text(draw, (MARGIN + 32, 311), label, tech(32))
        draw.line([(MARGIN + tab_w + 18, 350), (W - MARGIN, 350)], fill="#225e71", width=2)


    def jacket(self, song_id, size):
        source = self.core.load_jacket_image(song_id)
        if source is None:
            source = Image.new("RGBA", (size, size), "#102637")
            draw = ImageDraw.Draw(source)
            self.panel(draw, (6, 6, size - 7, size - 7), 12, outline="#418497", width=2)
            self.top_text(draw, (size / 2, size / 2 - 15), "T3", tech(28), CYAN, "center")
        return ImageOps.fit(source.convert("RGBA"), (size, size), LANCZOS)


    @lru_cache(maxsize=32)
    def rank_image(self, rank, height=RANK_H):
        source = self.core.load_rank_image(rank)
        if source is None:
            face = tech(height)
            box = face.getbbox(rank)
            source = Image.new("RGBA", (box[2] - box[0] + 4, box[3] - box[1] + 4))
            self.top_text(ImageDraw.Draw(source), (2, 2), rank, face)
        # All ranks share the exact same visible height, including multi-letter
        # grades. Trim transparent atlas padding before scaling, never squeeze.
        source = source.crop(source.getchannel("A").getbbox())
        return source.resize((round(source.width * height / source.height), height), LANCZOS)


    def draw_rank(self, image, rank, right, top, height=RANK_H):
        badge = self.rank_image(rank, height)
        image.alpha_composite(badge, (right - badge.width, top))


    @lru_cache(maxsize=2)
    def text_shade(self, compact):
        layout = CONST_LAYOUT if compact else LAYOUT
        start = layout.jacket_x + layout.jacket_size - 9
        ramp = Image.new("L", (layout.width, 1))
        ramp.putdata([round(80 * min(1, max(0, (x - start) / (layout.width * .23))))
                      for x in range(layout.width)])
        layer = Image.new("RGBA", (layout.width, layout.height), (2, 22, 34, 255))
        layer.putalpha(ramp.resize(layer.size))
        return layer


    def card(self, sample, index=1, compact=False):
        layout = CONST_LAYOUT if compact else LAYOUT
        tile = CARD_ART_CACHE.get(self.jacket(sample.song_id, layout.jacket_size), layout)
        tile.alpha_composite(self.text_shade(compact))
        tile.putalpha(ImageChops.multiply(tile.getchannel("A"),
                                         self.cut_mask(layout.width, layout.height, 12)))
        draw = ImageDraw.Draw(tile)
        self.panel(draw, (0, 0, layout.width - 1, layout.height - 1), 12,
              outline="#446778", width=1)
        tone = DIFFICULTIES.get(sample.difficulty, CYAN)
        if compact:
            self.draw_compact(tile, draw, sample, index, tone)
        else:
            self.panel(draw, (198, 15, 370, 44), 7, fill=(4, 15, 25, 170), outline=tone)
            self.top_text(draw, (284, 23), sample.difficulty, tech(18), tone, "center")
            self.top_text(draw, (CW - 18, 18), f"{sample.constant:.1f}", tech(27), WHITE, "right")
            lines, size = adaptive_card_title(sample.title, CW - 216, 27, 35)
            title_y = 59 if len(lines) == 2 else 67
            for line_i, line in enumerate(lines):
                self.top_text(draw, (198, title_y + line_i * 33), line, font(size, "song"), stroke=1)
            if sample.score is None:
                self.top_text(draw, (198, 132), "UNPLAYED", tech(26), SOFT)
            else:
                self.top_text(draw, (198, 129), f"{sample.score:,}", tech(40), stroke=2)
                self.top_text(draw, (198, 185), f"{sample.single_rating:.2f}", tech(26), CYAN)
                self.draw_rank(tile, self.core.score_rank(sample.score), CW - 20, 174)
                if sample.medal in (4, 5):
                    mark = "AJ" if sample.medal == 5 else "FC"
                    self.top_text(draw, (333, 190), mark, tech(20),
                             "#ffe49b" if sample.medal == 5 else "#7effe0")
            # Numeric index stays on the artwork, never competes with the title.
            self.panel(draw, (16, 16, 63, 42), 6, fill="#071b28", outline="#528899")
            self.top_text(draw, (39, 21), f"{index:02}", tech(18), SOFT, "center")
        return tile


    def draw_compact(self, tile, draw, sample, index, tone):
        # Abbreviations conserve room without shrinking the regular B40 cards.
        label = self.core.DIFFICULTY_META.get(sample.difficulty, (sample.difficulty[:3],))[0]
        self.top_text(draw, (113, 9), label, tech(14), tone)
        self.top_text(draw, (CCW - 12, 8), f"{index:02}", tech(14), SOFT, "right")
        if sample.score is not None and sample.medal in (4, 5):
            self.top_text(draw, (CCW - 48, 8), "AJ" if sample.medal == 5 else "FC", tech(14),
                     "#ffe49b" if sample.medal == 5 else "#7effe0", "right")
        lines, size = adaptive_card_title(sample.title, CCW - 126, 21, 26)
        for i, line in enumerate(lines):
            self.top_text(draw, (113, 31 + i * 24), line, font(size, "song"), stroke=1)
        if sample.score is None:
            self.top_text(draw, (113, 89), "UNPLAYED", tech(19), SOFT)
        else:
            self.top_text(draw, (113, 88), f"{sample.score:,}", tech(22), stroke=1)
            self.draw_rank(tile, self.core.score_rank(sample.score), CCW - 12, 85, height=26)


    def score_sheet(self, samples, rating, label, name="PLAYER", collections=None, *, min_height=0, index_offset=0):
        rows = max(1, math.ceil(len(samples) / COLS))
        height = START_Y + rows * CH + (rows - 1) * GAP_Y + 40
        image = self.background(W, max(min_height, height))
        self.header(image, name, rating, label, collections)
        for i, sample in enumerate(samples):
            image.alpha_composite(self.card(sample, i + 1 + index_offset),
                                  (MARGIN + i % COLS * (CW + GAP_X),
                                   START_Y + i // COLS * (CH + GAP_Y)))
        return self.credit(image)


    def const_height(self, groups):
        return START_Y + sum(GROUP_H + math.ceil(len(rows) / CCOLS) * (CCH + CGAP_Y)
                             + 16 for _, rows in groups) + 24


    def const_sheet(self, entries, query, rating, name="PLAYER", collections=None):
        groups = self.core._const_groups(entries, query)
        height = self.const_height(groups)
        if height > self.core.CONST_MAX_IMAGE_HEIGHT:
            raise self.core.TakumiChartDataError("定数范围太大，单张长图会超出安全尺寸；请缩小查询范围。")
        image = self.background(W, height)
        self.header(image, name, rating, f"CONST  {query.label}", collections)
        draw = ImageDraw.Draw(image)
        y = START_Y
        for tenth, group in groups:
            stats = self.core.const_stats(group)
            self.panel(draw, (MARGIN, y, MARGIN + 175, y + 58), 14,
                  fill="#073e4d", outline="#42d3e9", width=2)
            self.top_text(draw, (MARGIN + 87, y + 11), f"{tenth / 10:.1f}", tech(35), WHITE, "center")
            # These are the requested per-tenth statistics, not inferred clears.
            values = [("PLAYED", f"{stats.played}/{stats.total}", SOFT),
                      ("S+", stats.s_plus, WHITE), ("S", stats.s, WHITE),
                      ("AAA", stats.aaa, WHITE), ("FC", stats.fc, "#7effe0"),
                      ("AJ", stats.aj, "#ffe49b")]
            x = MARGIN + 217
            for label, count, color in values:
                self.top_text(draw, (x, y + 21), label, tech(21), SOFT)
                x += math.ceil(text_width(label, tech(21))) + 17
                self.top_text(draw, (x, y + 17), count, tech(28), color)
                x += math.ceil(text_width(str(count), tech(28))) + 62
            draw.line([(min(x + 8, W - MARGIN - 8), y + 58),
                       (W - MARGIN, y + 58)], fill="#245b70", width=2)
            y += GROUP_H
            for i, entry in enumerate(group):
                sample = Sample(entry.song_id, entry.title, entry.difficulty,
                                entry.constant, entry.score, entry.medal)
                image.alpha_composite(self.card(sample, i + 1, compact=True),
                                      (MARGIN + i % CCOLS * (CCW + CGAP_X),
                                       y + i // CCOLS * (CCH + CGAP_Y)))
            y += math.ceil(len(group) / CCOLS) * (CCH + CGAP_Y) + 16
        return self.credit(image)



