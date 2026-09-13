"""答题插件 —— Open Trivia DB 题库 + 积分榜 + 每日推送"""

import json
import random
from datetime import date
from pathlib import Path

import httpx
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.rule import Rule

from src.common import at_me_only, main_group_only, random_delay as rdelay
from src.common import scheduler, get_target_groups

DATA_DIR = Path("data")
QUIZ_FILE = DATA_DIR / "quiz.json"
COIN_FILE = DATA_DIR / "coins.json"
QUIZ_REWARD = 50

TRIVIA_API = "https://opentdb.com/api.php?amount=1&type=multiple"


# ====== 数据读写 ======

def load_quiz() -> dict:
    if not QUIZ_FILE.exists():
        return {}
    try:
        return json.loads(QUIZ_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_quiz(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QUIZ_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_coins() -> dict:
    if not COIN_FILE.exists():
        return {}
    try:
        return json.loads(COIN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_coins(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COIN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_quiz_reward(uid: str) -> int:
    data = load_coins()
    user = data.get(uid, {"balance": 500})
    data[uid] = user
    user["balance"] = user.get("balance", 500) + QUIZ_REWARD
    save_coins(data)
    return user["balance"]


# ====== 获取题目 ======

async def fetch_question() -> dict | None:
    """从 Open Trivia DB 获取一道英文题"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(TRIVIA_API)
            if resp.status_code != 200:
                return None
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None
            r = results[0]
            import html
            question = html.unescape(r["question"])
            correct = html.unescape(r["correct_answer"])
            incorrect = [html.unescape(a) for a in r["incorrect_answers"]]
            category = html.unescape(r.get("category", "General"))

            options = incorrect + [correct]
            random.shuffle(options)

            return {
                "question": question,
                "options": options,
                "correct": correct,
                "category": category,
            }
    except Exception as e:
        logger.warning(f"Quiz fetch error: {e}")
        return None


def format_question(q: dict) -> str:
    labels = ["A", "B", "C", "D"]
    opts = "\n".join(f"  {labels[i]}. {q['options'][i]}" for i in range(len(q["options"])))
    return (
        f"📝 今日答题 · {q.get('category', '综合')}\n"
        f"——————————————\n"
        f"{q['question']}\n\n"
        f"{opts}\n"
        f"——————————————\n"
        f"回复 /答题 A/B/C/D 作答，第一个答对 +{QUIZ_REWARD}💰 喵。"
    )


# ====== 指令 ======

quiz_cmd = on_command(
    "答题", priority=10, block=True, rule=main_group_only & at_me_only,
)

LABELS = ["A", "B", "C", "D"]


def _get_label_index(text: str) -> int | None:
    t = text.strip().upper()
    if t in LABELS:
        return LABELS.index(t)
    return None


@quiz_cmd.handle()
async def handle_quiz(event: GroupMessageEvent, bot, args=CommandArg()):
    uid = event.get_user_id()
    raw = args.extract_plain_text().strip()

    # ---- /答题榜 ----
    if raw == "榜" or raw == "排行榜":
        data = load_quiz()
        scores = data.get("scores", {})
        if not scores:
            await rdelay()
            await quiz_cmd.finish(MessageSegment.at(uid) + "\n还没有人答对过题目喵...你来做第一个？")

        # 获取本群成员
        try:
            member_list = await bot.get_group_member_list(group_id=event.group_id)
        except Exception:
            member_list = []
        group_uids = {str(m["user_id"]) for m in member_list}

        # 只显示群内成员，用群昵称
        ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        lines = ["🏆 答题排行榜"]
        rank = 0
        for uid_str, score in ranking:
            if uid_str not in group_uids:
                continue
            rank += 1
            if rank > 10:
                break
            # 取群昵称
            name = next((m.get("card") or m.get("nickname", uid_str) for m in member_list if str(m["user_id"]) == uid_str), uid_str)
            lines.append(f"  {rank}. {name}  —  {score} 题")
        if rank == 0:
            lines.append("  本群还没有人上榜喵...")
        lines.append("——————————————")
        lines.append("/答题 挑战今日题目喵。")
        await rdelay()
        await quiz_cmd.finish(MessageSegment.at(uid) + "\n" + "\n".join(lines))

    # ---- /答题 A/B/C/D（作答）----
    label_idx = _get_label_index(raw)
    if label_idx is not None:
        data = load_quiz()
        today = date.today().isoformat()
        q = data.get("current")
        if not q or q.get("date") != today:
            await rdelay()
            await quiz_cmd.finish(MessageSegment.at(uid) + "\n今天还没有题目喵...发 /答题 获取。")
        if q.get("answered"):
            winner = q.get("winner", "?")
            await rdelay()
            await quiz_cmd.finish(
                MessageSegment.at(uid)
                + f"\n今天已经有人答对了喵...答案是 {q['correct']}，被 QQ:{winner} 抢了。明天再来！"
            )

        # 检查答案
        correct_label = None
        for i, opt in enumerate(q["options"]):
            if opt == q["correct"]:
                correct_label = LABELS[i]
                break

        if label_idx == (LABELS.index(correct_label) if correct_label else -1):
            q["answered"] = True
            q["winner"] = uid
            data["scores"] = data.get("scores", {})
            data["scores"][uid] = data["scores"].get(uid, 0) + 1
            save_quiz(data)
            balance = add_quiz_reward(uid)
            await rdelay()
            await quiz_cmd.finish(
                MessageSegment.at(uid)
                + f"\n🎉 答对了！+{QUIZ_REWARD}💰  |  余额: {balance}💰"
                + f"\n...碰巧而已喵。\n"
                + f"\n" + format_question(q)
                + f"\n✅ 正确答案: {correct_label}. {q['correct']}"
            )
        else:
            await rdelay()
            await quiz_cmd.finish(
                MessageSegment.at(uid)
                + f"\n❌ 错了...再想想喵。"
            )

    # ---- /答题（获取/查看今日题目）----
    today = date.today().isoformat()
    data = load_quiz()
    existing = data.get("current")

    # 今天已有未答题目 → 直接显示
    if existing and existing.get("date") == today and not existing.get("answered"):
        await rdelay()
        await quiz_cmd.finish(MessageSegment.at(uid) + "\n" + format_question(existing))
        return

    # 今天已有人答对 → 显示题目但不给奖励
    if existing and existing.get("date") == today and existing.get("answered"):
        winner = existing.get("winner", "?")
        await rdelay()
        await quiz_cmd.finish(
            MessageSegment.at(uid)
            + f"\n今天的题目已经被 QQ:{winner} 抢了喵...来晚了。\n"
            + f"\n" + format_question(existing)
            + f"\n⚠ 已结束，答对也不给钱喵。"
        )
        return

    # 今天还没题目 → 取一道
    q = await fetch_question()
    if not q:
        await rdelay()
        await quiz_cmd.finish(MessageSegment.at(uid) + "\n题目获取失败...等会儿再试喵。")

    q["date"] = today
    q["answered"] = False
    q["winner"] = None
    data["current"] = q
    save_quiz(data)

    await rdelay()
    await quiz_cmd.finish(MessageSegment.at(uid) + "\n" + format_question(q))


# ====== 快捷作答：直接发 A/B/C/D（不带 /答题） ======

async def _bare_answer_check(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip().upper()
    return text in LABELS


bare_answer = on_message(
    Rule(_bare_answer_check) & main_group_only & at_me_only,
    priority=20,
    block=False,
)


@bare_answer.handle()
async def handle_bare_answer(event: GroupMessageEvent):
    uid = event.get_user_id()
    raw = event.get_plaintext().strip().upper()
    label_idx = _get_label_index(raw)
    if label_idx is None:
        return

    data = load_quiz()
    today = date.today().isoformat()
    q = data.get("current")
    if not q or q.get("date") != today:
        return
    if q.get("answered"):
        await rdelay()
        await bare_answer.finish(MessageSegment.at(uid) + "\n别人已经答对了喵...来晚了。")
        return

    correct_label = None
    for i, opt in enumerate(q["options"]):
        if opt == q["correct"]:
            correct_label = LABELS[i]
            break

    if label_idx == (LABELS.index(correct_label) if correct_label else -1):
        q["answered"] = True
        q["winner"] = uid
        data["scores"] = data.get("scores", {})
        data["scores"][uid] = data["scores"].get(uid, 0) + 1
        save_quiz(data)
        balance = add_quiz_reward(uid)
        await rdelay()
        await bare_answer.finish(
            MessageSegment.at(uid)
            + f"\n🎉 答对了！+{QUIZ_REWARD}💰  |  余额: {balance}💰"
            + f"\n...碰巧而已喵。\n"
            + f"\n" + format_question(q)
            + f"\n✅ 正确答案: {correct_label}. {q['correct']}"
        )


# ====== 每日定时推送 (8:02，在新闻之后) ======

@scheduler.scheduled_job("cron", hour=8, minute=2, misfire_grace_time=300)
async def push_daily_quiz():
    q = await fetch_question()
    if not q:
        logger.warning("Quiz push failed: cannot fetch question")
        return

    today = date.today().isoformat()
    data = load_quiz()
    q["date"] = today
    q["answered"] = False
    q["winner"] = None
    data["current"] = q
    save_quiz(data)

    msg = format_question(q)
    groups = get_target_groups()
    if not groups:
        return

    from nonebot import get_bots
    bots = get_bots()
    if not bots:
        return
    bot = list(bots.values())[0]

    for gid in groups:
        try:
            await bot.send_group_msg(group_id=gid, message=msg)
        except Exception as e:
            logger.warning(f"Quiz push failed for group {gid}: {e}")

    logger.info(f"Quiz pushed to {len(groups)} groups")
