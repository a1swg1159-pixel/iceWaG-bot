"""Pure-Pillow jacket effects, with a bounded cache of public artwork only.

No account data, scores, API calls or disk writes belong in this module.
"""
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
BILINEAR = getattr(Image, "Resampling", Image).BILINEAR
MAX_SURFACE_BYTES = 32 * 1024 * 1024
MAX_SURFACE_IDLE = 3600


@dataclass(frozen=True)
class CardLayout:
    width: int
    height: int
    jacket_size: int
    jacket_x: int
    jacket_y: int
    radius: int = 17


@lru_cache(maxsize=8)
def shade_layer(width, height):
    # Two one-dimensional ramps replace the preview's Python loop over every
    # pixel. Pillow combines/expands them in native code; content is identical.
    horizontal = Image.new("L", (width, 1))
    horizontal.putdata([
        round(78 + 44 * min(1, max(0, (x * 565 / width - 125) / 230)))
        for x in range(width)
    ])
    vertical = Image.new("L", (1, height))
    vertical.putdata([
        round(25 * max(0, (y * 256 / height - 170) / 86)) for y in range(height)
    ])
    layer = Image.new("RGBA", (width, height), (13, 15, 25, 255))
    layer.putalpha(ImageChops.add(horizontal.resize(layer.size), vertical.resize(layer.size)))
    return layer


@lru_cache(maxsize=8)
def jacket_feather(size):
    edge = max(1, 8 * size / 170)

    def smooth(value):
        t = min(1, max(0, value))
        return round(255 * t * t * (3 - 2 * t))

    horizontal = Image.new("L", (size, 1))
    horizontal.putdata([smooth(min(x / edge, (size - 1 - x) / (edge * 2))) for x in range(size)])
    vertical = Image.new("L", (1, size))
    vertical.putdata([smooth(min(y / edge, (size - 1 - y) / edge)) for y in range(size)])
    return ImageChops.darker(horizontal.resize((size, size)), vertical.resize((size, size)))


@lru_cache(maxsize=8)
def corner_mask(width, height, radius):
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    return mask


def build_card_art(jacket, layout):
    """Opaque darkened blur plus a sharp centre with feathered jacket edges."""
    scale = min(layout.width / 565, layout.height / 256)
    padding = max(1, round(24 * scale))
    field_size = (layout.width + padding * 2, layout.height + padding * 2)
    # The backdrop is deliberately blurred. Compute that blur at half size,
    # using RGB to avoid repeated RGBA premultiplication; the sharp jacket below
    # is never downsampled and still uses the full prepared thumbnail.
    source = Image.new("RGB", jacket.size, (28, 31, 48))
    source.paste(jacket, (0, 0), jacket.getchannel("A"))
    field = ImageOps.fit(source, tuple(max(1, round(side / 2)) for side in field_size),
                         method=LANCZOS, centering=(.5, .42))
    field = field.filter(ImageFilter.GaussianBlur(11 * scale / 2)).resize(field_size, BILINEAR)
    field = field.crop((padding, padding, layout.width + padding, layout.height + padding))
    # Transparent/missing artwork must never expose a bright canvas behind text.
    card = field.convert("RGBA")
    card.alpha_composite(shade_layer(layout.width, layout.height))

    size = layout.jacket_size
    bleed_margin = max(1, round(12 * size / 170))
    bleed = Image.new("RGBA", (size + bleed_margin * 2, size + bleed_margin * 2))
    bleed.paste(jacket, (bleed_margin, bleed_margin))
    bleed = bleed.filter(ImageFilter.GaussianBlur(6 * size / 170))
    bleed.putalpha(bleed.getchannel("A").point(lambda value: round(value * .55)))
    card.alpha_composite(bleed, (layout.jacket_x - bleed_margin, layout.jacket_y - bleed_margin))
    sharp = jacket.copy()
    sharp.putalpha(ImageChops.multiply(sharp.getchannel("A"), jacket_feather(size)))
    card.alpha_composite(sharp, (layout.jacket_x, layout.jacket_y))
    card.putalpha(corner_mask(layout.width, layout.height, layout.radius))
    return card


class CardArtCache:
    """32 MiB LRU; entries idle for an hour expire on the next cache access.

    Keys depend on thumbnail pixels and layout, not player or song IDs. New art
    cannot accidentally reuse an old jacket, and callers receive private copies
    so score digits are never retained here or leaked into a later report.
    """

    def __init__(self, max_bytes=MAX_SURFACE_BYTES, max_idle=MAX_SURFACE_IDLE, clock=None):
        self.max_bytes = max_bytes
        self.max_idle = max_idle
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self.items = OrderedDict()
        self.bytes = 0

    def _discard(self, key):
        image, _ = self.items.pop(key)
        self.bytes -= image.width * image.height * 4

    def clear(self):
        with self._lock:
            self.items.clear()
            self.bytes = 0

    def get(self, jacket, layout):
        jacket = jacket.convert("RGBA")
        if jacket.size != (layout.jacket_size, layout.jacket_size):
            jacket = ImageOps.fit(jacket, (layout.jacket_size, layout.jacket_size), method=LANCZOS)
        key = (layout, hashlib.sha256(jacket.tobytes()).digest())
        with self._lock:
            now = self.clock()
            # Entries are ordered by last access; expired ones are at the front.
            while self.items:
                oldest = next(iter(self.items))
                if now - self.items[oldest][1] < self.max_idle:
                    break
                self._discard(oldest)
            record = self.items.get(key)
            if record is not None:
                self.items[key] = (record[0], now)
                self.items.move_to_end(key)
                return record[0].copy()
        # Effect processing never holds the lock against other render requests.
        image = build_card_art(jacket, layout)
        size = image.width * image.height * 4
        if size <= self.max_bytes:
            with self._lock:
                if key in self.items:
                    self._discard(key)
                while self.items and self.bytes + size > self.max_bytes:
                    self._discard(next(iter(self.items)))
                self.items[key] = (image.copy(), self.clock())
                self.bytes += size
        return image


CARD_ART_CACHE = CardArtCache()
