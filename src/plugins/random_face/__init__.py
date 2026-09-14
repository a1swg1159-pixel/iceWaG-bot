from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.rule import Rule

from src.common import at_me_only, random_delay as rdelay
from .local_comics import choose_local_comic


async def only_at_bot(event: GroupMessageEvent) -> bool:
    if not event.is_tome():
        return False

    msg = event.get_message()
    for seg in msg:
        if seg.type == "at":
            continue
        if seg.type == "text" and seg.data.get("text", "").strip() == "":
            continue
        return False
    return True


random_face = on_message(Rule(only_at_bot) & at_me_only, priority=20, block=False)


@random_face.handle()
async def handle_random_face():
    comic = choose_local_comic()
    await rdelay()
    if comic is None:
        await random_face.finish("本地音击漫画目录里暂时没有可发送的图片。")

    try:
        data = comic.read_bytes()
    except OSError as exc:
        logger.warning(f"Read local Ongeki comic failed: {comic.name} -> {exc}")
        await random_face.finish("这张本地音击漫画读取失败了。")

    await random_face.finish(MessageSegment.image(file=data))
