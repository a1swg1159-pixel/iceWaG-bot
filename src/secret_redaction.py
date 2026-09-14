"""Redact short-lived credentials before application logs are emitted."""

import re
from typing import Any, MutableMapping


MAIMAI_QR_SECRET = re.compile(r"SGWCMAID[A-Za-z0-9_-]{20,256}")


def redact_sensitive_text(value: str) -> str:
    return MAIMAI_QR_SECRET.sub("SGWCMAID[REDACTED]", value)


def redact_log_record(record: MutableMapping[str, Any]) -> None:
    record["message"] = redact_sensitive_text(str(record.get("message", "")))
