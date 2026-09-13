"""Shared LXNS OAuth 2.0 + PKCE support for score commands."""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests


AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
PUBLIC_CLIENT_ID = "9a6be364-2c97-4773-89ec-8c7d04fcbd41"
DEFAULT_SCOPE = "read_user_profile read_player write_player"
TOKENS_FILE = Path("data") / "b30_oauth_tokens.json"
PENDING_FILE = Path("data") / "b30_oauth_pending.json"
PENDING_TTL_SECONDS = 15 * 60
_STORE_LOCK = threading.Lock()


def _read_json(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _clean_pending(data: Dict[str, Dict[str, Any]]) -> None:
    cutoff = time.time() - PENDING_TTL_SECONDS
    for user_id in list(data):
        if float(data[user_id].get("created_at", 0)) < cutoff:
            data.pop(user_id, None)


def create_authorization_url(
    user_id: str, client_id: str, redirect_uri: str = OOB_REDIRECT_URI,
) -> str:
    """Create a one-user authorization URL and persist its PKCE verifier."""
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    pending_item = {
        "state": state,
        "code_verifier": verifier,
        "created_at": time.time(),
        "redirect_uri": redirect_uri,
    }
    with _STORE_LOCK:
        pending = _read_json(PENDING_FILE)
        _clean_pending(pending)
        pending[str(user_id)] = pending_item
        _write_json(PENDING_FILE, pending)

    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": DEFAULT_SCOPE,
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def _token_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}"
    return str(
        payload.get("error_description")
        or payload.get("message")
        or payload.get("error")
        or f"HTTP {response.status_code}"
    )


def _save_token_response(
    user_id: str, payload: Dict[str, Any], fallback_scope: str = DEFAULT_SCOPE,
) -> None:
    expires_in = max(60, int(payload.get("expires_in", 900)))
    item = {
        "access_token": str(payload["access_token"]),
        "refresh_token": str(payload["refresh_token"]),
        "token_type": str(payload.get("token_type", "Bearer")),
        "scope": str(payload.get("scope") or fallback_scope),
        "expires_at": time.time() + expires_in,
    }
    tokens = _read_json(TOKENS_FILE)
    tokens[str(user_id)] = item
    _write_json(TOKENS_FILE, tokens)


def exchange_authorization_code(
    user_id: str, code: str, client_id: str,
    client_secret: str = "", redirect_uri: str = OOB_REDIRECT_URI,
) -> Tuple[Optional[str], str]:
    """Exchange the displayed/returned code and store the rotating token pair."""
    with _STORE_LOCK:
        pending = _read_json(PENDING_FILE)
        _clean_pending(pending)
        item = pending.get(str(user_id))
        if not item:
            _write_json(PENDING_FILE, pending)
            return None, "授权请求已过期，请重新发送 /chu bind"

        payload = {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "client_id": client_id,
            "code_verifier": str(item["code_verifier"]),
            # OAuth requires this to exactly match the authorization request.
            "redirect_uri": str(item.get("redirect_uri", redirect_uri)),
        }
        if client_secret:
            payload["client_secret"] = client_secret

        try:
            response = requests.post(TOKEN_URL, data=payload, timeout=15)
        except Exception:
            return None, "连接 LXNS 失败，请稍后重试"
        if response.status_code >= 400:
            return None, _token_error(response)
        try:
            token_data = response.json()
            access_token = str(token_data["access_token"])
            if not token_data.get("refresh_token"):
                raise KeyError("refresh_token")
        except Exception:
            return None, "LXNS 返回的令牌数据不完整"

        # Per OAuth 2.0 the token response may omit scope when it is identical
        # to the authorization request.
        scope = str(token_data.get("scope") or DEFAULT_SCOPE)
        token_data["scope"] = scope
        granted_scopes = set(scope.split())
        missing_scopes = {
            required for required in ("read_player", "write_player")
            if required not in granted_scopes
        }
        if missing_scopes:
            return None, "授权缺少 " + "、".join(sorted(missing_scopes)) + " 权限"
        _save_token_response(str(user_id), token_data)
        pending.pop(str(user_id), None)
        _write_json(PENDING_FILE, pending)
        return f"Bearer {access_token}", ""


def get_access_token(
    user_id: str, client_id: str, client_secret: str = "",
) -> Tuple[Optional[str], str]:
    """Return a valid Authorization value, rotating the refresh token if needed."""
    with _STORE_LOCK:
        tokens = _read_json(TOKENS_FILE)
        item = tokens.get(str(user_id))
        if not item:
            return None, "尚未授权"

        access_token = str(item.get("access_token", ""))
        if access_token and float(item.get("expires_at", 0)) > time.time() + 60:
            return f"Bearer {access_token}", ""

        refresh_token = str(item.get("refresh_token", ""))
        if not refresh_token:
            return None, "授权已失效，请重新绑定"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret:
            payload["client_secret"] = client_secret
        try:
            response = requests.post(TOKEN_URL, data=payload, timeout=15)
        except Exception:
            return None, "连接 LXNS 失败，请稍后重试"
        if response.status_code >= 400:
            return None, _token_error(response)
        try:
            token_data = response.json()
            if not token_data.get("access_token") or not token_data.get("refresh_token"):
                raise KeyError("token")
        except Exception:
            return None, "LXNS 返回的刷新数据不完整"
        _save_token_response(
            str(user_id), token_data, str(item.get("scope") or DEFAULT_SCOPE)
        )
        return f"Bearer {token_data['access_token']}", ""


def has_oauth_binding(user_id: str) -> bool:
    with _STORE_LOCK:
        return str(user_id) in _read_json(TOKENS_FILE)


def has_oauth_scope(user_id: str, scope: str) -> bool:
    """Return whether a stored user grant contains the requested OAuth scope."""
    with _STORE_LOCK:
        item = _read_json(TOKENS_FILE).get(str(user_id), {})
        return scope in str(item.get("scope", "")).split()


def remove_oauth_binding(user_id: str) -> bool:
    removed = False
    with _STORE_LOCK:
        tokens = _read_json(TOKENS_FILE)
        if tokens.pop(str(user_id), None) is not None:
            _write_json(TOKENS_FILE, tokens)
            removed = True
        pending = _read_json(PENDING_FILE)
        if pending.pop(str(user_id), None) is not None:
            _write_json(PENDING_FILE, pending)
    return removed
