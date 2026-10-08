"""Parsing and matching for rhythm-game displayed levels and chart constants."""

from dataclasses import dataclass
import math
import re
from typing import Optional, Union


_DISPLAY_LEVEL_RE = re.compile(r"^(\d{1,2})(\+)?$")
_CONSTANT_RE = re.compile(r"^(\d{1,2})\.(\d)$")


class LevelQueryError(ValueError):
    """Raised when a score-list level query is not supported."""


@dataclass(frozen=True)
class ConstantRangeQuery:
    lower: int
    upper: int

    @property
    def label(self) -> str:
        if self.lower == self.upper:
            return f"{self.lower / 10:.1f}"
        return f"{self.lower / 10:.1f}–{self.upper / 10:.1f}"


def parse_constant_range_query(value: str) -> ConstantRangeQuery:
    """Parse inclusive tenths using the same buckets as /takumi const."""
    raw = str(value or "").strip().replace("．", ".").replace("＋", "+")
    raw = re.sub(r"^lv\.?\s*", "", raw, flags=re.IGNORECASE)
    endpoint = r"(\d{1,2}\.\d|\d{1,2}\+?)"
    match = re.fullmatch(
        rf"\s*{endpoint}(?:\s*(?:-|－|~|～|至|到)\s*{endpoint})?\s*",
        raw,
    )
    if not match:
        raise LevelQueryError("格式应为 14、14.2、14+ 或 14.2-14.8")

    def bounds(text: str) -> tuple[int, int]:
        if text.endswith("+"):
            base = int(text[:-1]) * 10
            return base + 5, base + 9
        if "." in text:
            whole, fraction = text.split(".")
            tenth = int(whole) * 10 + int(fraction)
            return tenth, tenth
        base = int(text) * 10
        return base, min(base + 9, 200)

    left = bounds(match.group(1))
    right = bounds(match.group(2)) if match.group(2) else left
    if left[0] < 10 or right[1] > 200 or left[0] > right[1]:
        raise LevelQueryError("定数范围需从小到大，且在 1.0～20.0 之间")
    return ConstantRangeQuery(left[0], right[1])


@dataclass(frozen=True)
class LevelQuery:
    raw: str
    label: str
    kind: str
    value: float

    @property
    def is_constant(self) -> bool:
        return self.kind == "constant"


def parse_level_query(value: str) -> LevelQuery:
    """Parse ``15``/``14+`` as displayed levels and ``15.1`` as a constant."""
    raw = str(value or "").strip().replace("＋", "+")
    lowered = raw.lower()
    if lowered.startswith("lv."):
        raw = raw[3:].strip()
    elif lowered.startswith("lv"):
        raw = raw[2:].strip()

    constant_match = _CONSTANT_RE.fullmatch(raw)
    if constant_match:
        number = float(raw)
        if not 1.0 <= number <= 20.0:
            raise LevelQueryError("定数需要在 1.0～20.0 之间")
        return LevelQuery(value, f"{number:.1f}", "constant", number)

    display_match = _DISPLAY_LEVEL_RE.fullmatch(raw)
    if display_match:
        number = int(display_match.group(1))
        if not 1 <= number <= 20:
            raise LevelQueryError("等级需要在 1～20 之间")
        plus = bool(display_match.group(2))
        label = f"{number}{'+' if plus else ''}"
        return LevelQuery(value, label, "display", float(number))

    raise LevelQueryError("格式应为 15、15.1 或 14+")


def display_level_from_constant(value: float, plus_threshold: float) -> str:
    """Derive a displayed level only when the song table did not provide one."""
    base = math.floor(value + 1e-8)
    fraction = value - base
    return f"{base}+" if fraction + 1e-8 >= plus_threshold else str(base)


def _display_label(value: object) -> Optional[str]:
    text = str(value or "").strip().replace("＋", "+")
    match = _DISPLAY_LEVEL_RE.fullmatch(text)
    if not match:
        return None
    number = int(match.group(1))
    return f"{number}{'+' if match.group(2) else ''}"


def _constant_value(value: Union[str, float, int, None]) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def matches_level_query(
    query: LevelQuery,
    *,
    display_level: object,
    constant: Union[str, float, int, None],
    plus_threshold: float,
) -> bool:
    """Match a query without treating integer display buckets as exact constants."""
    precise = _constant_value(constant)
    if query.is_constant:
        return precise is not None and abs(precise - query.value) < 0.001

    displayed = _display_label(display_level)
    if displayed is None and precise is not None:
        displayed = display_level_from_constant(precise, plus_threshold)
    return displayed == query.label
