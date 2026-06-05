import json
import random
from datetime import date
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

from src.common import no_at_others, random_delay


# ========== 运势分段 ==========

FORTUNE_TABLE = [
    (0, 0,   "💀 大凶",   "今天别出门了...在家躲着喵。"),
    (1, 15,  "🌧 凶",     "运气不太好呢...忍忍就过去了喵。"),
    (16, 40, "☁ 末吉",   "普普通通的一天...别期待太多喵。"),
    (41, 65, "🌤 小吉",   "还算不错吧...但别得意喵。"),
    (66, 85, "🌈 中吉",   "哼...运气意外地还行喵。"),
    (86, 99, "☀ 大吉",   "今天运气很好呢...才不是因为你喵。"),
    (100, 100, "🎉 运势爆棚", "今天你说什么都对!!! 蹭蹭好运喵!"),
]


def get_fortune(score: int):
    for lo, hi, label, desc in FORTUNE_TABLE:
        if lo <= score <= hi:
            return label, desc
    return "❓ 未知", "...喵。"


# ========== 本地持久化 ==========

FORTUNE_DATA_FILE = Path("data") / "fortune_data.json"


def load_fortunes() -> dict:
    if not FORTUNE_DATA_FILE.exists():
        return {}
    try:
        return json.loads(FORTUNE_DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_fortunes(data: dict) -> None:
    FORTUNE_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    FORTUNE_DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def get_today_fortune(user_id: str) -> int:
    today = date.today().isoformat()
    key = f"{user_id}_{today}"

    data = load_fortunes()

    if key in data:
        return data[key]

    fresh_data = {k: v for k, v in data.items() if k.endswith(f"_{today}")}
    score = random.randint(0, 100)
    fresh_data[key] = score
    save_fortunes(fresh_data)
    return score


# ========== 指令 ==========

fortune_cmd = on_command("每日运势", priority=10, block=True, rule=no_at_others)


@fortune_cmd.handle()
async def handle_fortune(event: MessageEvent):
    score = get_today_fortune(event.get_user_id())
    label, desc = get_fortune(score)

    fill = score // 10
    bar = "█" * fill + "░" * (10 - fill)

    text = (
        f"✨ 今日运势: {score} {label}\n"
        f"[{bar}]\n"
        f"{desc}"
    )

    await random_delay()
    await fortune_cmd.finish(
        MessageSegment.at(event.get_user_id())
        + "\n"
        + text
    )
