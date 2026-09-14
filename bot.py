import nonebot
from nonebot import get_asgi, get_driver
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.log import logger

from src.secret_redaction import redact_log_record


nonebot.init()
logger.configure(patcher=redact_log_record)

driver = get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

app = get_asgi()


if __name__ == "__main__":
    nonebot.run()
