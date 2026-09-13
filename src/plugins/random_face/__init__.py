import io
import random
from pathlib import Path

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.rule import Rule

from src.common import at_me_only, random_delay as rdelay


# ====== 音击（ONGEKI）图源 ======
# 1) 官方素材图：已扒到本地 data/ongeki_official/（来自萌娘共享的音击分类，91 张）
# 2) 同人插画：lolicon API 用 tag=オンゲキ（日文）过滤，返回 i.pixiv.re 反代，国内可直连
LOLICON_API = "https://api.lolicon.app/setu/v2"
ONGEKI_TAG = "オンゲキ"
OFFICIAL_DIR = Path("data") / "ongeki_official"

FALLBACK_FACE_IDS = [14, 21, 23, 24, 27, 33, 36, 49, 53, 60, 74, 75, 76, 78, 79]
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 1600


def _to_jpeg(data: bytes) -> bytes:
    """统一转成 JPEG（处理 EXIF 旋转 + 等比缩小），规避格式兼容问题。"""
    try:
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        width, height = img.size
        longest = max(width, height)
        if longest > MAX_DIMENSION:
            scale = MAX_DIMENSION / longest
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"Image re-encode failed: {e}; returning original bytes")
        return data


async def _download_image(client: httpx.AsyncClient, url: str) -> bytes | None:
    """下载图片字节，校验大小，失败返回 None。"""
    try:
        resp = await client.get(url)
        if resp.status_code == 200 and resp.content and len(resp.content) > 100:
            if len(resp.content) > MAX_IMAGE_BYTES:
                logger.warning(f"Image too large ({len(resp.content)} bytes), skip: {url}")
                return None
            return resp.content
    except Exception as e:
        logger.warning(f"Download failed: {url} -> {e}")
    return None


def _random_official_image() -> bytes | None:
    """从本地扒好的音击官方图里随机抽一张，返回图片字节。"""
    if not OFFICIAL_DIR.exists():
        return None
    files = sorted(OFFICIAL_DIR.glob("*.jpg"))
    if not files:
        return None
    f = random.choice(files)
    try:
        data = f.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            logger.warning(f"Official image too large, skip: {f.name}")
            return None
        return data
    except Exception as e:
        logger.warning(f"Read official image failed: {f.name} -> {e}")
        return None


async def _lolicon_image(client: httpx.AsyncClient, tag: str) -> bytes | None:
    """从 lolicon（pixiv）按 tag 取一张图，返回 JPEG 字节。"""
    try:
        resp = await client.get(
            LOLICON_API,
            params={"tag": tag, "num": 1, "size": "regular", "r18": 0},
        )
        if resp.status_code != 200:
            logger.warning(f"lolicon tag={tag} returned {resp.status_code}")
            return None
        data = resp.json().get("data") or []
        if not data:
            logger.warning(f"lolicon tag={tag} no result")
            return None
        url = data[0].get("urls", {}).get("regular")
        if not url:
            return None
        content = await _download_image(client, url)
        if content:
            return _to_jpeg(content)
    except Exception as e:
        logger.warning(f"lolicon tag={tag} failed: {e}")
    return None


async def fetch_ongeki_image() -> bytes | None:
    """官方图 与 同人插画 各取一张，随机返回其一。"""
    official = _random_official_image()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    ) as client:
        doujin = await _lolicon_image(client, ONGEKI_TAG)

    candidates = [x for x in (official, doujin) if x]
    return random.choice(candidates) if candidates else None


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
    data = await fetch_ongeki_image()
    if data:
        await rdelay()
        await random_face.finish(
            MessageSegment.image(file=data) + MessageSegment.text(" 音击！")
        )

    face_id = random.choice(FALLBACK_FACE_IDS)
    await rdelay()
    await random_face.finish(MessageSegment.face(face_id))
