"""每日天气插件 —— 支持主动查询与每日 8:00 定时推送"""

import httpx
from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger

from src.common import at_me_only, main_group_only, push_to_groups, random_delay as rdelay
from src.common import scheduler

# ====== 配置 ======

driver = get_driver()
WEATHER_CITY = getattr(driver.config, "weather_city", "北京")
WEATHER_API = "https://wttr.in/{}?format=j1"

# 天气图标映射
WEATHER_ICONS = {
    "sunny": "☀️", "clear": "🌙",
    "partly cloudy": "⛅", "cloudy": "☁️", "overcast": "☁️",
    "mist": "🌫️", "fog": "🌫️", "freezing fog": "🌫️",
    "patchy rain possible": "🌦️", "patchy rain nearby": "🌦️",
    "light rain": "🌧️", "moderate rain": "🌧️", "heavy rain": "🌧️",
    "light drizzle": "🌦️", "patchy light drizzle": "🌦️",
    "thunderstorm": "⛈️", "thundery outbreaks possible": "⛈️",
    "light snow": "🌨️", "moderate snow": "🌨️", "heavy snow": "🌨️",
    "blizzard": "❄️", "blowing snow": "❄️",
    "ice pellets": "🧊", "light sleet": "🌨️",
    "windy": "💨",
}


def _icon(desc: str) -> str:
    """根据天气描述文字返回对应图标"""
    if not desc:
        return "🌈"
    d = desc.strip().lower()
    for key, icon in WEATHER_ICONS.items():
        if key in d:
            return icon
    return "🌈"


# ====== API 调用 ======

async def fetch_weather(city: str) -> str | None:
    """从 wttr.in 获取指定城市全天天气预报，成功返回格式化文本，失败返回 None"""
    url = WEATHER_API.format(city)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"wttr.in returned {resp.status_code}")
                return None

            data = resp.json()

            # 今日概况
            today = (data.get("weather") or [{}])[0]
            high = today.get("maxtempC", "?")
            low = today.get("mintempC", "?")
            date = today.get("date", "")

            # 时段预报：取 8h / 12h / 16h / 20h 代表上午/中午/下午/晚上
            hours_data = today.get("hourly") or []
            target_hours = {"8": "🌅 上午", "12": "☀️ 中午", "16": "🌤 下午", "20": "🌙 晚上"}
            period_forecasts = []
            for h in hours_data:
                hour_str = str(int(h.get("time", "0")) // 100)  # "0800" → "8"
                if hour_str in target_hours:
                    temp_c = h.get("tempC", "?")
                    desc = (h.get("weatherDesc") or [{}])[0].get("value", "未知")
                    rain = h.get("chanceofrain", "0")
                    icon = _icon(desc)
                    period_forecasts.append(
                        f"{target_hours[hour_str]}: {icon} {desc}  {temp_c}°C  🌧{rain}%"
                    )

            lines = [
                f"📅  {city} 全天预报 ({date})...自己看喵。",
                f"📊 最高 {high}°C / 最低 {low}°C",
                "——————————————",
            ]
            if period_forecasts:
                lines.extend(period_forecasts)
            else:
                # 降级：用当前天气
                cur = (data.get("current_condition") or [{}])[0]
                temp = cur.get("temp_C", "?")
                desc = (cur.get("weatherDesc") or [{}])[0].get("value", "未知")
                lines.append(f"当前: {_icon(desc)} {desc}  {temp}°C")

            lines.append("——————————————")
            lines.append("数据来自 wttr.in...信不信随你喵。")
            return "\n".join(lines)

    except Exception as e:
        logger.warning(f"Weather fetch error: {e}")
        return None


# ====== 指令 ======

weather_cmd = on_command("每日天气", priority=10, block=True, rule=main_group_only & at_me_only)


@weather_cmd.handle()
async def handle_weather(event: MessageEvent):
    city = str(WEATHER_CITY)
    text = await fetch_weather(city)
    await rdelay()
    if text:
        await weather_cmd.finish(
            MessageSegment.at(event.get_user_id()) + "\n" + text
        )
    else:
        await weather_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + f"\n天气获取失败...别催了喵。"
        )


# ====== 定时推送 ======

@scheduler.scheduled_job("cron", hour=8, minute=0, misfire_grace_time=300)
async def push_daily_weather():
    """每天 8:00 推送天气到所有目标群"""
    city = str(WEATHER_CITY)
    text = await fetch_weather(city)
    if not text:
        text = f"☁  {city}今日天气获取失败了喵...反正也不是什么重要的东西。"
    text = "哼...既然起来了就自己看天气喵。\n\n" + text
    sent = await push_to_groups(text)
    logger.info(f"Weather pushed to {len(sent)} groups")
