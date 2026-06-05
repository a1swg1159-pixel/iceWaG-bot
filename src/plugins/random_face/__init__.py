import random

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.rule import Rule

from src.common import random_delay as rdelay


# 猫娘图片 API 源（按优先级排列，先国内可达、后备用）
NEKO_APIS = [
    {
        "url": "https://nekos.best/api/v2/neko",
        "parser": lambda d: (
            d.get("results", [{}])[0].get("url") if d.get("results") else None
        ),
    },
    {
        "url": "https://api.waifu.pics/sfw/neko",
        "parser": lambda d: d.get("url"),
    },
]


# QQ 内置表情（API 全部挂掉时的兜底）
FALLBACK_FACE_IDS = [14, 21, 23, 24, 27, 33, 36, 49, 53, 60, 74, 75, 76, 78, 79]


async def fetch_random_neko_url() -> str | None:
    """按优先级逐个尝试 API 源获取一张猫娘图片 URL，全部失败返回 None。"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        for api in NEKO_APIS:
            try:
                resp = await client.get(api["url"])
                if resp.status_code == 200:
                    url = api["parser"](resp.json())
                    if url:
                        return url
            except Exception:
                continue
    return None


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


random_face = on_message(Rule(only_at_bot), priority=20, block=False)


@random_face.handle()
async def handle_random_face():
    # 优先使用联网获取的猫娘表情包
    url = await fetch_random_neko_url()
    if url:
        await rdelay()
        await random_face.finish(
            MessageSegment.image(file=url) + MessageSegment.text(" 喵？")
        )

    # 兜底：API 全挂了就用 QQ 内置表情
    face_id = random.choice(FALLBACK_FACE_IDS)
    await rdelay()
    await random_face.finish(MessageSegment.face(face_id))
