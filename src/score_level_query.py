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
