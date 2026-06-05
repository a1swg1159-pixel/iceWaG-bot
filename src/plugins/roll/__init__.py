import random

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg

from src.common import at_me_only, random_delay as rdelay


roll_cmd = on_command("roll", priority=10, block=True, rule=at_me_only)


@roll_cmd.handle()
async def handle_roll(event: MessageEvent, args=CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await rdelay()
        await roll_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n用法: /roll 选项1 选项2 ... 用空格隔开喵。"
        )

    choices = raw.split()
    if len(choices) < 2:
        await rdelay()
        await roll_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n至少给两个选项才有的选喵。"
        )

    result = random.choice(choices)
    await rdelay()
    await roll_cmd.finish(
        MessageSegment.at(event.get_user_id())
        + f"\n选到了: {result}喵。"
    )
