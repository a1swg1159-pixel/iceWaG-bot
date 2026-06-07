"""小游戏插件 —— 二十一点 / 老虎机 + 猫猫币系统"""

import json
import random
from datetime import date
from pathlib import Path
from typing import Dict, Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg

from src.common import at_me_only, random_delay as rdelay

DATA_DIR = Path("data")
_bj_states: Dict[str, dict] = {}

COIN_FILE = DATA_DIR / "coins.json"
INITIAL_COINS = 500
DAILY_BONUS = 100

# ====== 老虎机 ======
SLOT_EMOJIS = ["🍒", "🍋", "🍊", "🔔", "💎", "⭐", "🍀", "7️⃣"]
SLOT_PAYOUT = {
    3: 15,   # 三个相同
    2: 2,    # 两个相同
    1: 0,    # 不中
}

# ====== 二十一点 ======
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


# ====== 货币读写 ======

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


def get_user(data: dict, uid: str) -> dict:
    if uid not in data:
        data[uid] = {"balance": INITIAL_COINS, "total_earned": 0, "daily_sign": ""}
    return data[uid]


def ensure_balance(uid: str, amount: int) -> str | None:
    """检查余额是否足够，不足返回错误消息"""
    data = load_coins()
    user = get_user(data, uid)
    if user["balance"] < amount:
        return f"猫猫币不够喵...你有 {user['balance']}💰，要下 {amount}💰 做梦呢。"
    return None


def add_coins(uid: str, amount: int) -> int:
    """加币，返回新余额"""
    data = load_coins()
    user = get_user(data, uid)
    user["balance"] += amount
    if amount > 0:
        user["total_earned"] = user.get("total_earned", 0) + amount
    save_coins(data)
    return user["balance"]


# ====== 货币指令 ======

coin_cmd = on_command("coin", aliases={"猫猫币", "balance", "余额"}, priority=10, block=True, rule=at_me_only)


@coin_cmd.handle()
async def handle_coin(event: GroupMessageEvent):
    uid = event.get_user_id()
    data = load_coins()
    user = get_user(data, uid)
    save_coins(data)
    await rdelay()
    await coin_cmd.finish(
        MessageSegment.at(uid)
        + f"\n💰 余额: {user['balance']} 猫猫币"
        + f"\n📊 累计赚取: {user.get('total_earned', 0)} 猫猫币"
        + f"\n——————————————"
        + f"\n/sign - 每日签到 (+{DAILY_BONUS}💰)"
        + f"\n/二十一点 <押注> - 二十一点"
        + f"\n/老虎机 <押注> - 老虎机"
        + f"\n...输了可别哭喵。"
    )


sign_cmd = on_command("sign", aliases={"签到"}, priority=10, block=True, rule=at_me_only)


@sign_cmd.handle()
async def handle_sign(event: GroupMessageEvent):
    uid = event.get_user_id()
    today = date.today().isoformat()
    data = load_coins()
    user = get_user(data, uid)
    if user.get("daily_sign") == today:
        await rdelay()
        await sign_cmd.finish(
            MessageSegment.at(uid) + "\n今天已经签过了...贪心也要有个限度喵。"
        )
    user["daily_sign"] = today
    user["balance"] += DAILY_BONUS
    save_coins(data)
    await rdelay()
    await sign_cmd.finish(
        MessageSegment.at(uid)
        + f"\n签到成功 +{DAILY_BONUS}💰  |  余额: {user['balance']}💰"
        + f"\n拿去买糖吃...不对，是给你玩游戏用的喵。"
    )


# ====== 二十一点 ======

def _card_value(rank: str) -> int:
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def _hand_value(hand: list) -> int:
    total = sum(_card_value(r) for _, r in hand)
    aces = sum(1 for _, r in hand if r == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def _hand_str(hand: list) -> str:
    return " ".join(f"{s}{r}" for s, r in hand)


def _deal() -> Tuple[str, str]:
    return random.choice(SUITS), random.choice(RANKS)


bj_cmd = on_command("二十一点", aliases={"bj", "21"}, priority=10, block=True, rule=at_me_only)


@bj_cmd.handle()
async def handle_bj(event: GroupMessageEvent, args=CommandArg()):
    uid = event.get_user_id()
    raw = args.extract_plain_text().strip()
    try:
        bet = int(raw)
    except ValueError:
        await rdelay()
        await bj_cmd.finish(MessageSegment.at(uid) + "\n用法: /二十一点 <押注金额>喵。比如 /二十一点 100")
    if bet <= 0:
        await rdelay()
        await bj_cmd.finish(MessageSegment.at(uid) + "\n押注金额必须是正数...你在想什么喵。")

    err = ensure_balance(uid, bet)
    if err:
        await rdelay()
        await bj_cmd.finish(MessageSegment.at(uid) + "\n" + err)

    # 发牌
    player = [_deal(), _deal()]
    dealer = [_deal(), _deal()]
    p_val = _hand_value(player)
    d_val = _hand_value(dealer)

    # 黑杰克
    if p_val == 21:
        win = int(bet * 2.5)
        add_coins(uid, win)
        await rdelay()
        await bj_cmd.finish(
            MessageSegment.at(uid)
            + f"\n🃏 你的牌: {_hand_str(player)}  ({p_val})"
            + f"\n🃏 庄家牌: {_hand_str(dealer)}  ({d_val})"
            + f"\n💥 黑杰克！碰巧而已... +{win}💰"
        )
        return

    # 存入全局 state
    state = {
        "uid": uid,
        "bet": bet,
        "player": player,
        "dealer": dealer,
    }
    _bj_states[uid] = state

    await rdelay()
    await bj_cmd.send(
        MessageSegment.at(uid)
        + f"\n🃏 你的牌: {_hand_str(player)}  ({p_val})"
        + f"\n🃏 庄家: {dealer[0][0]}{dealer[0][1]}  ??"
        + f"\n/要牌 还是 /停牌 ？"
        + f"\n...快点决定喵。"
    )


# 要牌/停牌用 on_message
hit_cmd = on_command("要牌", aliases={"hit"}, priority=10, block=True, rule=at_me_only)


@hit_cmd.handle()
async def handle_hit(event: GroupMessageEvent, bot: Bot):
    uid = event.get_user_id()
    state = _bj_states.get(uid)

    if not state:
        await rdelay()
        await hit_cmd.finish(MessageSegment.at(uid) + "\n你没有在进行中的游戏喵...先 /二十一点 <押注> 开始一局。")

    bet = state["bet"]
    player = state["player"]
    dealer = state["dealer"]

    player.append(_deal())
    p_val = _hand_value(player)

    if p_val > 21:
        # 爆了
        add_coins(uid, -bet)
        _bj_states.pop(uid, None)
        await rdelay()
        await hit_cmd.finish(
            MessageSegment.at(uid)
            + f"\n🃏 你的牌: {_hand_str(player)}  ({p_val})"
            + f"\n💀 爆了！{bet}💰 没收喵...太贪心了吧。"
        )

    if p_val == 21:
        _bj_states.pop(uid, None)
        await rdelay()
        await hit_cmd.send(
            MessageSegment.at(uid)
            + f"\n🃏 你的牌: {_hand_str(player)}  ({p_val})"
            + f"\n刚好 21！...算你走运，轮到庄家了喵。"
        )
        await _dealer_play(bot, uid, event.group_id, bet, player, dealer)
        return

    await rdelay()
    await hit_cmd.send(
        MessageSegment.at(uid)
        + f"\n🃏 你的牌: {_hand_str(player)}  ({p_val})"
        + f"\n还要吗？ /要牌 或 /停牌"
    )


stand_cmd = on_command("停牌", aliases={"stand"}, priority=10, block=True, rule=at_me_only)


@stand_cmd.handle()
async def handle_stand(event: GroupMessageEvent, bot: Bot):
    uid = event.get_user_id()
    state = _bj_states.get(uid)

    if not state:
        await rdelay()
        await stand_cmd.finish(MessageSegment.at(uid) + "\n你没有在进行中的游戏喵。")

    bet = state["bet"]
    player = state["player"]
    dealer = state["dealer"]
    _bj_states.pop(uid, None)

    await rdelay()
    await stand_cmd.send(
        MessageSegment.at(uid)
        + f"\n停牌了...怂了喵。轮到庄家。"
    )
    await _dealer_play(bot, uid, event.group_id, bet, player, dealer)


async def _dealer_play(bot: Bot, uid: str, group_id: int, bet: int, player: list, dealer: list):
    while _hand_value(dealer) < 17:
        dealer.append(_deal())

    d_val = _hand_value(dealer)
    p_val = _hand_value(player)

    if d_val > 21:
        win = bet * 2
        add_coins(uid, win)
        msg = f"\n🃏 庄家: {_hand_str(dealer)}  ({d_val}) — 爆了！\n🎉 +{win}💰 ...只是运气喵。"
    elif d_val > p_val:
        add_coins(uid, -bet)
        msg = f"\n🃏 庄家: {_hand_str(dealer)}  ({d_val})\n😼 庄家赢了...{bet}💰 没收喵。下次带够再来。"
    elif d_val < p_val:
        win = bet * 2
        add_coins(uid, win)
        msg = f"\n🃏 庄家: {_hand_str(dealer)}  ({d_val})\n🎉 +{win}💰 ...偶尔赢一次别得意喵。"
    else:
        add_coins(uid, bet)
        msg = f"\n🃏 庄家: {_hand_str(dealer)}  ({d_val})\n🤝 平局！退你 {bet}💰 ...白玩一场喵。"

    await bot.send_group_msg(group_id=group_id, message=MessageSegment.at(uid) + msg)


# ====== 老虎机 ======

slot_cmd = on_command("老虎机", aliases={"slot"}, priority=10, block=True, rule=at_me_only)


@slot_cmd.handle()
async def handle_slot(event: GroupMessageEvent, args=CommandArg()):
    uid = event.get_user_id()
    raw = args.extract_plain_text().strip()
    try:
        bet = int(raw)
    except ValueError:
        await rdelay()
        await slot_cmd.finish(MessageSegment.at(uid) + "\n用法: /老虎机 <押注金额>喵。比如 /老虎机 50")
    if bet <= 0:
        await rdelay()
        await slot_cmd.finish(MessageSegment.at(uid) + "\n别以为下 0 币就能白嫖...想得美喵。")

    err = ensure_balance(uid, bet)
    if err:
        await rdelay()
        await slot_cmd.finish(MessageSegment.at(uid) + "\n" + err)

    # 扣钱
    add_coins(uid, -bet)

    # 转三列
    r1 = random.choice(SLOT_EMOJIS)
    r2 = random.choice(SLOT_EMOJIS)
    r3 = random.choice(SLOT_EMOJIS)

    # 算奖
    if r1 == r2 == r3:
        matches = 3
    elif r1 == r2 or r2 == r3 or r1 == r3:
        matches = 2
    else:
        matches = 1

    multiplier = SLOT_PAYOUT.get(matches, 0)
    win = bet * multiplier
    if win > 0:
        add_coins(uid, win)

    data = load_coins()
    user = get_user(data, uid)

    # 不同结果的冷淡/嘲讽回复
    if matches == 3:
        reactions = [
            "三连！...肯定是机器坏了喵。",
            "全中了...你是不是作弊了。",
            "这都能中...运气没地方花了吗。",
        ]
    elif matches == 2:
        reactions = [
            "两连...勉强回本喵。",
            "差一点就三连了...可惜呢，不是在夸你。",
            "还行...继续？还是见好就收？",
        ]
    else:
        reactions = [
            "全空...意料之中喵。",
            "三个不沾...你今天的运势真差。",
            "哈...白给了。还要继续？",
        ]

    line = f" {r1}  |  {r2}  |  {r3} "
    await rdelay()
    await slot_cmd.finish(
        MessageSegment.at(uid)
        + f"\n╔══════════╗"
        + f"\n║{line}║"
        + f"\n╚══════════╝"
        + f"\n{random.choice(reactions)}"
        + (f"\n{'🎉' if win > 0 else '💸'} {'+' if win > 0 else '-'}{win if win > 0 else bet}💰  |  余额: {user['balance']}💰")
    )
