import asyncio
import os
import random
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.rule import Rule


# ====== 群聊 @规则：只有 @了 bot 才响应 ======

async def _at_me_only(event: GroupMessageEvent) -> bool:
    """只响应 @了本 bot 的消息"""
    return event.is_tome()


at_me_only = Rule(_at_me_only)


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
    """从环境变量 DAILY_PUSH_GROUPS 读取推送目标群号列表"""
    raw = os.environ.get("DAILY_PUSH_GROUPS", "")
    if not raw:
        logger.warning("DAILY_PUSH_GROUPS is empty, skip daily push")
        return []
    groups = []
    for part in str(raw).split(","):
        part = part.strip()
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
