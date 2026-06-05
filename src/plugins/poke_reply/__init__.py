from nonebot import on_notice
from nonebot.adapters.onebot.v11 import MessageSegment, PokeNotifyEvent

from src.common import random_delay


poke = on_notice()


@poke.handle()
async def handle_poke(event: PokeNotifyEvent):
    if event.is_tome():
        await random_delay()
        await poke.finish(MessageSegment.face(14) + " 烦死了...再戳就不理你了喵！")
