"""Health route only; notification delivery lives outside the Bot process."""
from nonebot import get_app, get_bots, get_driver

from src.bot_health import install_health_route

config = get_driver().config
install_health_route(
    get_app(), get_bots,
    getattr(config, "onebot_v11_access_token", ""),
    getattr(config, "bot_qq", ""),
)
