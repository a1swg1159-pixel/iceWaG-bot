"""Atomic local store for QQ -> TAKUMI³ bot login credentials."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional


BINDINGS_FILE = Path("data") / "takumi_bindings.json"
_LOCK = threading.Lock()


@dataclass(frozen=True)
class TakumiBinding:
    """A revocable PlayFab CustomID issued specifically for this bot."""

    custom_id: str
    playfab_id: str = ""
    display_name: str = ""


def _read_document() -> Dict[str, object]:
    if not BINDINGS_FILE.exists():
        return {}
    try:
        value = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _parse_binding(item: object) -> Optional[TakumiBinding]:
    # The previous format stored a public Score Tool ID as a bare string. It
    # cannot authenticate to PlayFab. Preserve such entries until their owner
    # rebinds, but never treat one as a credential.
    if not isinstance(item, dict):
        return None
    custom_id = str(item.get("custom_id") or "").strip()
    if not custom_id:
        return None
    return TakumiBinding(
        custom_id=custom_id,
        playfab_id=str(item.get("playfab_id") or "").strip(),
        display_name=str(item.get("display_name") or "").strip(),
    )


def _write(value: Dict[str, object]) -> None:
    BINDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = BINDINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(BINDINGS_FILE)


def get_binding(qq_user_id: str) -> Optional[TakumiBinding]:
    with _LOCK:
        return _parse_binding(_read_document().get(str(qq_user_id)))


def set_binding(
    qq_user_id: str,
    custom_id: str,
    playfab_id: str = "",
    display_name: str = "",
) -> TakumiBinding:
    binding = TakumiBinding(
        custom_id=str(custom_id).strip(),
        playfab_id=str(playfab_id).strip(),
        display_name=str(display_name).strip(),
    )
    if not binding.custom_id:
        raise ValueError("custom_id must not be empty")
    with _LOCK:
        value = _read_document()
        value[str(qq_user_id)] = asdict(binding)
        _write(value)
    return binding


def remove_binding(qq_user_id: str) -> Optional[TakumiBinding]:
    with _LOCK:
        value = _read_document()
        raw = value.pop(str(qq_user_id), None)
        if raw is not None:
            _write(value)
        return _parse_binding(raw)
