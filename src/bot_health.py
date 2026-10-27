"""Authenticated, read-only health probe for the independent mail watchdog.

An established WebSocket is not proof of a live QQ session. Query OneBot's
get_status and require online=True (NapCat's documented status API).
No message content, account identifiers or credentials enter the response.
"""
import asyncio
import secrets
import time

HEALTH_PATH = "/internal/health"


def result(reason):
    return {"version": 1, "ok": reason == "online", "reason": reason}


async def probe_bots(bots, bot_id="", timeout=5):
    selected = [bots[bot_id]] if bot_id and bot_id in bots else []
    if not bot_id:
        selected = list(bots.values())[:8]
    if not selected:
        return result("qq_disconnected")

    async def check(bot):
        try:
            status = await asyncio.wait_for(bot.get_status(), timeout=timeout)
        except Exception:
            # API exception text may contain credentials or account data.
            return "onebot_unresponsive"
        if not isinstance(status, dict) or type(status.get("online")) is not bool:
            return "invalid_onebot_status"
        if not status["online"]:
            return "qq_offline"
        if status.get("good") is False:
            return "onebot_unhealthy"
        return "online"

    reasons = await asyncio.gather(*(check(bot) for bot in selected))
    return result(next((reason for reason in reasons if reason != "online"), "online"))


def install_health_route(app, bots_provider, token, bot_id=""):
    """Install on the existing FastAPI app; never create another listener."""
    from fastapi import Header
    from fastapi.responses import JSONResponse

    lock = asyncio.Lock()
    cached = None
    checked_at = 0.0
    token = token.get_secret_value() if hasattr(token, "get_secret_value") else str(token or "")
    usable = bool(token) and token != "CHANGE_ME"
    # Older deployments don't have BOT_QQ; in that case inspect connected bots.
    bot_id = str(bot_id or "").strip()
    bot_id = bot_id if bot_id.isdecimal() else ""

    async def health(authorization: str = Header(default="")):
        nonlocal cached, checked_at
        headers = {"Cache-Control": "no-store"}
        if not usable:
            return JSONResponse(result("health_not_configured"), status_code=503, headers=headers)
        if len(authorization) > 4096 or not secrets.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")
        ):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401, headers=headers)
        async with lock:
            if cached is None or time.monotonic() - checked_at >= 5:
                cached = await probe_bots(dict(bots_provider()), bot_id)
                checked_at = time.monotonic()
            return JSONResponse(cached, status_code=200 if cached["ok"] else 503, headers=headers)

    app.add_api_route(HEALTH_PATH, health, methods=["GET"], include_in_schema=False)
