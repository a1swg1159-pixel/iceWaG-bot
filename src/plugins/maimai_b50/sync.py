"""One-shot maimai arcade QR import into the bound LXNS account."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, List, Mapping

import httpx


LXNS_SCORES_URL = "https://maimai.lxns.net/api/v0/user/maimai/player/scores"
QR_PATTERN = re.compile(r"SGWCMAID[A-Za-z0-9_-]{20,256}")
UPLOAD_BATCH_SIZE = 500


class MaimaiQrSyncError(RuntimeError):
    """A safe, user-facing failure raised by the QR sync pipeline."""


@dataclass(frozen=True)
class MaimaiQrSyncResult:
    fetched: int
    uploaded: int


def normalize_qr_string(value: str) -> str:
    """Validate a scanner result without retaining or echoing it."""
    qr = value.strip()
    if not QR_PATTERN.fullmatch(qr):
        raise MaimaiQrSyncError(
            "二维码字符串格式不正确；请发送以 SGWCMAID 开头的完整字符串"
        )
    return qr


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _enum_name(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.name.lower()
    return str(value).lower()


def score_to_lxns_payload(score: Any) -> Mapping[str, Any]:
    """Convert a maimai.py Score to LXNS' documented personal API shape."""
    song_id = int(score.id)
    level_index = int(_enum_value(score.level_index))
    song_type = str(_enum_value(score.type))
    achievements = float(score.achievements)
    dx_score = int(score.dx_score or 0)
    if song_id <= 0 or level_index < 0 or not song_type:
        raise ValueError("invalid score identity")
    return {
        "id": song_id,
        "type": song_type,
        "level_index": level_index,
        "achievements": achievements,
        "fc": _enum_name(score.fc),
        "fs": _enum_name(score.fs),
        "dx_score": dx_score,
    }


async def _fetch_arcade_scores(qr: str, http_proxy: str | None) -> List[Any]:
    try:
        from maimai_py import ArcadeProvider, MaimaiClient
    except (ImportError, OSError) as exc:
        raise MaimaiQrSyncError(
            "服务器未安装可用的 maimai-py/maimai-ffi 二维码组件"
        ) from exc

    try:
        client = MaimaiClient()
        identifier = await client.qrcode(qr, http_proxy=http_proxy)
        result = await client.scores(
            identifier, provider=ArcadeProvider(http_proxy=http_proxy)
        )
        return list(result.scores)
    except Exception as exc:
        # Never include the exception text: upstream errors may contain request data.
        raise MaimaiQrSyncError(
            "机台二维码已失效、不可用，或暂时无法连接华立成绩服务"
        ) from exc


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        return str(
            payload.get("message")
            or payload.get("error_description")
            or payload.get("error")
            or f"HTTP {response.status_code}"
        )
    return f"HTTP {response.status_code}"


async def _upload_scores(
    credential: str, scores: Iterable[Mapping[str, Any]]
) -> int:
    items = list(scores)
    uploaded = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for offset in range(0, len(items), UPLOAD_BATCH_SIZE):
            batch = items[offset:offset + UPLOAD_BATCH_SIZE]
            try:
                response = await client.post(
                    LXNS_SCORES_URL,
                    headers={"Authorization": credential},
                    json={"scores": batch},
                )
            except httpx.HTTPError as exc:
                raise MaimaiQrSyncError("连接 LXNS 写入接口失败，请稍后重试") from exc
            if response.is_error:
                raise MaimaiQrSyncError(
                    f"LXNS 拒绝写入：{_response_error(response)}"
                )
            try:
                payload = response.json()
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("success") is False:
                raise MaimaiQrSyncError(
                    f"LXNS 拒绝写入：{_response_error(response)}"
                )
            uploaded += len(batch)
    return uploaded


async def sync_maimai_qrcode_to_lxns(
    qr_value: str, credential: str, http_proxy: str | None = None,
) -> MaimaiQrSyncResult:
    """Read current arcade scores from one QR and write them to LXNS."""
    qr = normalize_qr_string(qr_value)
    raw_scores = await _fetch_arcade_scores(qr, http_proxy)
    payloads: List[Mapping[str, Any]] = []
    for score in raw_scores:
        try:
            payloads.append(score_to_lxns_payload(score))
        except (AttributeError, TypeError, ValueError):
            continue
    if not payloads:
        raise MaimaiQrSyncError("二维码解析成功，但没有取得可上传的舞萌成绩")
    uploaded = await _upload_scores(credential, payloads)
    return MaimaiQrSyncResult(fetched=len(raw_scores), uploaded=uploaded)
