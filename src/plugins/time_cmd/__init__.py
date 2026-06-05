"""当前时间插件 —— 用于测试容器时区配置是否正确"""

from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

from src.common import at_me_only, random_delay as rdelay


time_cmd = on_command("time", priority=10, block=True, rule=at_me_only)


@time_cmd.handle()
async def handle_time(event: MessageEvent):
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now.strftime("%A")
    week_map = {
        "Monday": "一", "Tuesday": "二", "Wednesday": "三",
        "Thursday": "四", "Friday": "五", "Saturday": "六", "Sunday": "日",
    }
    weekday_cn = week_map.get(weekday, weekday)

    await rdelay()
    await time_cmd.finish(
        MessageSegment.at(event.get_user_id())
        + f"\n{ts}  星期{weekday_cn}。这点小事也要问我喵..."
    )
