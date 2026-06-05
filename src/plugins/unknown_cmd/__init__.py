from nonebot import on_message
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.rule import Rule

from src.common import random_delay


async def is_unknown_command(event: MessageEvent) -> bool:
    """检查消息是否为未被匹配的指令（以 / 开头）"""
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    if not text:
        return False

    # 以 / 或 ／ 开头，且不是只有 /
    if text.startswith("/") or text.startswith("／"):
        return len(text) > 1
    return False


unknown_cmd = on_message(
    Rule(is_unknown_command),
    priority=99,   # 最低优先级，确保其他指令先匹配
    block=False,   # 不阻塞，万一有后续处理器
)


@unknown_cmd.handle()
async def handle_unknown(event: MessageEvent):
    await random_delay()
    await unknown_cmd.finish(
        MessageSegment.at(event.get_user_id())
        + "\n哼...没有这个指令。发送 /help 自己看喵。"
    )
