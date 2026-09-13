import asyncio
import os
import random
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.log import logger
from nonebot.rule import Rule


# ====== 主群判断 ======

def is_main_group(group_id: int) -> bool:
    """检查群是否在 DAILY_PUSH_GROUPS 中"""
    return group_id in get_target_groups()


# ====== 群聊 @规则：只有 @了 bot 才响应（私聊不受限） ======

async def _at_me_only(event: MessageEvent) -> bool:
    """私聊直接通过，群聊只有 @了本 bot 才响应"""
    if event.message_type == "private":
        return True
    return event.is_tome()


at_me_only = Rule(_at_me_only)


async def _main_group_only(event: GroupMessageEvent) -> bool:
    """只允许主群（DAILY_PUSH_GROUPS）使用功能"""
    return is_main_group(event.group_id)


main_group_only = Rule(_main_group_only)


# ====== 随机延迟（防风控） ======

async def random_delay(min_s: float = 0.8, max_s: float = 2.5) -> None:
    """短随机延迟，降低风控风险"""
    await asyncio.sleep(random.uniform(min_s, max_s))


# ====== 共享调度器 ======

scheduler = AsyncIOScheduler()

driver = get_driver()


@driver.on_startup
async def _start_scheduler():
    scheduler.start()
    logger.info("Scheduler started")


@driver.on_shutdown
async def _shutdown_scheduler():
    scheduler.shutdown()
    logger.info("Scheduler stopped")


# ====== 定时推送辅助 ======

def get_target_groups() -> List[int]:
    """从 NoneBot 配置读取主群/每日推送目标群号列表。"""
    # NoneBot reads dotenv values into driver.config without exporting them to
    # os.environ. Reading only os.environ made a correctly configured .env look
    # empty, so main_group_only rejected every group.
    raw = getattr(get_driver().config, "daily_push_groups", None)
    if raw in (None, ""):
        raw = os.environ.get("DAILY_PUSH_GROUPS", "")
    if not raw:
        logger.warning("DAILY_PUSH_GROUPS is empty, skip daily push")
        return []

    if isinstance(raw, (list, tuple, set)):
        parts = raw
    else:
        parts = str(raw).split(",")

    groups = []
    for part in parts:
        part = str(part).strip()
        if part.isdigit():
            groups.append(int(part))
    return groups


async def push_to_groups(message: str) -> List[int]:
    """向所有 DAILY_PUSH_GROUPS 群发送消息，返回成功发送的群号列表"""
    groups = get_target_groups()
    if not groups:
        return []

    bots = get_bots()
    if not bots:
        logger.warning("No bot connected, skip push")
        return []

    bot: Bot = list(bots.values())[0]
    success = []
    for gid in groups:
        try:
            await bot.send_group_msg(group_id=gid, message=message)
            success.append(gid)
        except Exception as e:
            logger.warning(f"Failed to push to group {gid}: {e}")
    return success
