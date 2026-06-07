"""每日新闻插件 —— 今日头条热榜，支持主动查询与每日 8:01 定时推送"""

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger

from src.common import at_me_only, main_group_only, push_to_groups, random_delay as rdelay
from src.common import scheduler


# ====== 配置 ======

NEWS_API = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
NEWS_COUNT = 5  # 每次推送的新闻条数


# ====== API 调用 ======

async def fetch_toutiao_hot(count: int = NEWS_COUNT) -> str | None:
    """获取今日头条热榜，返回带链接的格式化文本；失败返回 None"""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(NEWS_API, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Toutiao hot returned {resp.status_code}")
                return None

            data = resp.json()
            items = data.get("data", [])
            if not items:
                return None

            top = items[:count]
            lines = ["📰 今日热榜...可不是特意帮你找的喵。"]
            for i, item in enumerate(top):
                title = item.get("Title", "无标题")
                cid = item.get("ClusterIdStr", "")

                # 热度格式化（HotValue 可能是字符串）
                try:
                    hot = int(item.get("HotValue", 0))
                except (ValueError, TypeError):
                    hot = 0
                if hot >= 100_000_000:
                    hot_str = f"{hot / 100_000_000:.1f}亿"
                elif hot >= 10_000:
                    hot_str = f"{hot / 10_000:.0f}万"
                else:
                    hot_str = str(hot)

                link = f"https://www.toutiao.com/trending/{cid}" if cid else ""
                lines.append(f"{i + 1}. 🔥{hot_str} {title}  {link}")

            return "\n".join(lines)

    except Exception as e:
        logger.warning(f"News fetch error: {e}")
        return None


# ====== 指令 ======

news_cmd = on_command("每日新闻", priority=10, block=True, rule=main_group_only & at_me_only)


@news_cmd.handle()
async def handle_news(event: MessageEvent):
    text = await fetch_toutiao_hot()
    await rdelay()
    if text:
        await news_cmd.finish(
            MessageSegment.at(event.get_user_id()) + "\n" + text
        )
    else:
        await news_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n新闻获取失败...又不是什么重要的事喵。"
        )


# ====== 定时推送 ======

@scheduler.scheduled_job("cron", hour=8, minute=1, misfire_grace_time=300)
async def push_daily_news():
    """每天 8:01 推送新闻到所有目标群（比天气晚 1 分钟）"""
    text = await fetch_toutiao_hot()
    if not text:
        text = "📰 今日新闻获取失败了喵...反正看了也不会变帅。"
    sent = await push_to_groups(text)
    logger.info(f"News pushed to {len(sent)} groups")
