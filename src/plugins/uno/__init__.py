"""UNO 桌游插件 —— 经典变色牌 + 群友互坑"""

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from nonebot import get_bots, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from src.common import random_delay as rdelay

DATA_FILE = Path("data") / "uno_games.json"

# ====== 牌 ======
COLORS = ["🔴", "🟡", "🟢", "🔵"]
NUMBERS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
ACTIONS = ["⏭️", "🔄", "➕2"]
WILDS = ["🌈", "🌈4️⃣"]

# 中文/emoji → 内部表示映射
_COLOR_MAP = {"红": "🔴", "黄": "🟡", "绿": "🟢", "蓝": "🔵", "🔴": "🔴", "🟡": "🟡", "🟢": "🟢", "🔵": "🔵"}
_NUM_MAP = {str(i): NUMBERS[i] for i in range(10)}
_NUM_MAP.update({"零": "0️⃣", "一": "1️⃣", "二": "2️⃣", "三": "3️⃣", "四": "4️⃣",
                  "五": "5️⃣", "六": "6️⃣", "七": "7️⃣", "八": "8️⃣", "九": "9️⃣",
                  "0": "0️⃣", "1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣",
                  "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣"})
_ACTION_MAP = {"跳过": "⏭️", "反转": "🔄", "+2": "➕2", "➕2": "➕2", "⏭️": "⏭️", "🔄": "🔄"}
_WILD_MAP = {"调色盘": "🌈", "🌈": "🌈", "万能": "🌈", "+4": "🌈4️⃣", "万能+4": "🌈4️⃣", "🌈4️⃣": "🌈4️⃣", "万能4": "🌈4️⃣"}


def _parse_card(raw: str):
    """解析汉字输入为内部卡牌 (color_emoji, symbol_emoji) 或 None"""
    text = raw.strip()
    if not text:
        return None
    # 万能
    if text in _WILD_MAP:
        return ("🌈", _WILD_MAP[text])
    # 动作牌: 红 反转 / 反转
    for cn, an in _ACTION_MAP.items():
        if text.endswith(cn):
            prefix = text[:-len(cn)]
            if prefix in _COLOR_MAP:
                return (_COLOR_MAP[prefix], an)
            if not prefix:
                # 无颜色默认匹配当前颜色（调用方处理）
                return (None, an)
            break
    # 数字牌: 黄 2 / 红 5
    if len(text) >= 2:
        for cn, nn in _NUM_MAP.items():
            if text.endswith(cn):
                prefix = text[:-len(cn)]
                if prefix in _COLOR_MAP:
                    return (_COLOR_MAP[prefix], nn)
                break
    # 纯 emoji 直传
    if text in ("🌈", "🌈4️⃣"):
        return ("🌈", text)
    for c in ("🔴", "🟡", "🟢", "🔵"):
        for s in NUMBERS + ACTIONS:
            if text == c + s:
                return (c, s)
    return None


def _new_deck() -> list:
    deck = []
    for c in COLORS:
        deck.append((c, "0️⃣"))
        for n in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]:
            deck.append((c, n))
            deck.append((c, n))
        for a in ACTIONS:
            deck.append((c, a))
            deck.append((c, a))
    for _ in range(4):
        deck.append(("🌈", "🌈"))
        deck.append(("🌈", "🌈4️⃣"))
    random.shuffle(deck)
    return deck


def _card_str(card) -> str:
    c, n = card
    # 颜色中文
    c_cn = {"🔴": "红", "🟡": "黄", "🟢": "绿", "🔵": "蓝"}.get(c, c)
    # 符号中文
    n_cn = {"⏭️": "跳过", "🔄": "反转", "➕2": "+2", "🌈": "调色盘", "🌈4️⃣": "+4"}
    for emoji, idx in _NUM_MAP.items():
        if idx == n:
            n_cn[idx] = emoji  # 数字保留 emoji
            break
    sym = n_cn.get(n, n)
    if c == "🌈":
        return sym
    return f"{c_cn}{sym}"


def _card_match(top, card) -> bool:
    """检查 card 是否能出在 top 上"""
    tc, tn = top
    cc, cn = card
    if cc == "🌈":
        return True  # Wild 万能
    if tc == "🌈":
        return True  # 上一张是万能，任意
    return cc == tc or cn == tn


def _calc_scores(hands: dict) -> dict:
    """计算每人剩余牌分数（数字=面值, Skip/Rev/+2=20, Wild=50, Wild+4=50）"""
    scores = {}
    for uid, hand in hands.items():
        s = 0
        for _, n in hand:
            if n in NUMBERS:
                s += NUMBERS.index(n)
            elif n in ("⏭️", "🔄", "➕2"):
                s += 20
            else:
                s += 50
        scores[uid] = s
    return scores


# ====== 帮助 ======

UNO_HELP = (
    "🃏 UNO 规则...自己看喵。\n"
    "——————————————\n"
    "/uno 开始 - 加入/开始游戏\n"
    "/uno 手牌 - 重新查看手牌\n"
    "/uno 查询 @某人 - 看他剩几张牌\n"
    "/uno 结束 - 强制结束（仅房主）\n"
    "/出 <牌> - 出牌，如 /出 黄2、/出 反转\n"
    "/过 - 摸一张牌\n"
    "——————————————\n"
    "剩一张时在群里发 UNO 喊牌，不喊罚 2 张！\n"
    "规则：出同色/同数字/同符号 | 调色盘/+4万能\n"
    "⏭️=跳过  🔄=反转  ➕2/+4可叠加\n"
    "先出完赢，其他人按手中牌扣分喵。"
)


# ====== 指令 ======

uno_cmd = on_command("uno", aliases={"UNO"}, priority=10, block=True)

# 检测群聊中纯文本 UNO（喊牌）不需要 @bot
async def _is_uno_call(event: GroupMessageEvent) -> bool:
    return event.get_plaintext().strip().upper() == "UNO"

uno_call = on_message(Rule(_is_uno_call), priority=30, block=True)


@uno_call.handle()
async def handle_uno_call(event: GroupMessageEvent):
    uid = event.get_user_id()
    gid = str(event.group_id)
    games = _load_games()
    game = games.get(gid)
    if game and game.get("status") == "playing" and game.get("uno_pending") == uid:
        game["uno_pending"] = None
        _save_games(games)
        await uno_call.finish(MessageSegment.at(uid) + "\nUNO！记住了喵。")


def _load_games() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_games(data: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _send_hands(bot: Bot, group_id: int, game: dict):
    """私聊每个玩家发最新手牌"""
    hands = game.get("hands", game.get("players", {}))
    for uid, hand in hands.items():
        cards = "  ".join(_card_str(c) for c in hand)
        msg = (
            f"🃏 你的手牌 ({len(hand)} 张)：\n"
            f"{cards}\n"
            f"——————————————\n"
            f"当前桌面: {_card_str(game['top'])}\n"
            f"轮到: {_player_name(game, game['turn'])}\n"
            f"——————————————\n"
            f"/出 <牌> 出牌 | /过 摸牌"
        )
        try:
            await bot.send_private_msg(user_id=int(uid), group_id=group_id, message=msg)
        except Exception:
            pass


def _player_name(game: dict, uid: str) -> str:
    return game.get("names", {}).get(uid, uid)


def _next_player(game: dict) -> str:
    order = game["order"]
    idx = order.index(game["turn"])
    step = 1 if game.get("direction", 1) == 1 else -1
    return order[(idx + step) % len(order)]


def _next_player_skip(game: dict, skip: int) -> str:
    """跳过 skip 个人"""
    order = game["order"]
    idx = order.index(game["turn"])
    step = skip * (1 if game.get("direction", 1) == 1 else -1)
    return order[(idx + step) % len(order)]


# 单独注册 /出 和 /过 (群聊+私聊)
from nonebot.adapters.onebot.v11 import PrivateMessageEvent

play_cmd = on_command("出", priority=10, block=True)
pass_cmd = on_command("过", priority=10, block=True)


def _find_user_game(uid: str) -> tuple | None:
    """查找用户所在的 UNO 游戏，返回 (gid, game) 或 None"""
    games = _load_games()
    for gid, game in games.items():
        if game.get("status") != "playing":
            continue
        if uid in game.get("hands", game.get("players", {})):
            return gid, game
    return None


@play_cmd.handle()
async def handle_play(event, bot: Bot, args=CommandArg()):
    uid = event.get_user_id()
    card = args.extract_plain_text().strip()
    if not card:
        return

    if isinstance(event, PrivateMessageEvent):
        result = _find_user_game(uid)
        if not result:
            await play_cmd.finish("你没有在进行中的 UNO 游戏喵。")
            return
        gid, game = result
        await _handle_play_card(event, bot, uid, gid, {gid: game}, card)
    else:
        await _handle_play_card(event, bot, uid, str(event.group_id), _load_games(), card)


@pass_cmd.handle()
async def handle_pass_cmd(event, bot: Bot):
    uid = event.get_user_id()

    if isinstance(event, PrivateMessageEvent):
        result = _find_user_game(uid)
        if not result:
            await pass_cmd.finish("你没有在进行中的 UNO 游戏喵。")
            return
        gid, game = result
        await _handle_pass(event, bot, uid, gid, {gid: game})
    else:
        await _handle_pass(event, bot, uid, str(event.group_id), _load_games())


@uno_cmd.handle()
async def handle_uno(event: GroupMessageEvent, bot: Bot, args=CommandArg()):
    uid = event.get_user_id()
    gid = str(event.group_id)
    raw = args.extract_plain_text().strip()
    games = _load_games()

    # ---- /uno 手牌 ----
    if raw == "手牌" or raw == "hand":
        game = games.get(gid)
        if not game or game.get("status") != "playing":
            await rdelay()
            await uno_cmd.finish(MessageSegment.at(uid) + "\n当前没有进行中的 UNO 喵。")
            return
        # 清除 UNO 罚牌标记
        if game.get("uno_pending") == uid:
            game["uno_pending"] = None
        hands = game.get("hands", game.get("players", {}))
        if uid in hands:
            hand = hands[uid]
            cards = "  ".join(_card_str(c) for c in hand)
            try:
                await bot.send_private_msg(
                    user_id=int(uid), group_id=event.group_id,
                    message=(
                        f"🃏 你的手牌 ({len(hand)} 张)：\n{cards}\n"
                        f"——————————————\n"
                        f"桌面: {_card_str(game['top'])}  轮到: {_player_name(game, game['turn'])}"
                    )
                )
            except Exception:
                pass
            await rdelay()
            await uno_cmd.finish(MessageSegment.at(uid) + "\n手牌已私聊喵。")
        return

    # ---- /uno 查询 @某人 ----
    if raw == "查询" or raw == "查看" or raw.startswith("查询") or raw.startswith("查看"):
        game = games.get(gid)
        if not game or game.get("status") != "playing":
            await rdelay()
            await uno_cmd.finish(MessageSegment.at(uid) + "\n当前没有进行中的 UNO 喵。")
            return
        # 找被 @ 的人
        target = uid
        for seg in event.get_message():
            if seg.type == "at":
                target = str(seg.data.get("qq", uid))
                break
        hands = game.get("hands", game.get("players", {}))
        count = len(hands.get(target, []))
        await rdelay()
        await uno_cmd.finish(
            MessageSegment.at(target)
            + f"\n🃏 剩余 {count} 张牌"
            + ("...快赢了喵！" if count <= 2 else "")
        )
        return

    # ---- /uno 开始（加入/开局）----
    if raw == "开始":
        game = games.get(gid)

        # 无游戏或已结束 → 创建
        if not game or game.get("status") not in ("waiting",):
            game = {
                "host": uid, "players": {uid: []},
                "names": {uid: event.sender.card or event.sender.nickname or uid},
                "status": "waiting",
            }
            games[gid] = game
            _save_games(games)
            await rdelay()
            await uno_cmd.finish(
                f"🃏 UNO 房间已创建！{_player_name(game, uid)} 是房主。\n"
                f"其他人 /uno 开始 加入，人齐了房主再 /uno 开始。"
            )
            return

        # 等待中，不是房主 → 加入
        if uid != game["host"]:
            if uid in game["players"]:
                await rdelay()
                await uno_cmd.finish(MessageSegment.at(uid) + "\n你已经加入了喵。")
            game["players"][uid] = []
            game["names"] = game.get("names", {})
            game["names"][uid] = event.sender.card or event.sender.nickname or uid
            _save_games(games)
            await rdelay()
            await uno_cmd.finish(
                f"🃏 {_player_name(game, uid)} 加入了！"
                f"  ({len(game['players'])} 人，房主 /uno 开始)"
            )
            return

        # 等待中，是房主 → 开局
        players = game["players"]
        if len(players) < 2:
            await rdelay()
            await uno_cmd.finish(MessageSegment.at(uid) + "\n至少 2 个人喵。")

        deck = _new_deck()
        hands = {p: [deck.pop() for _ in range(7)] for p in players}
        top = deck.pop()
        while top[0] == "🌈":
            deck.insert(random.randint(0, len(deck)), top)
            top = deck.pop()
        order = list(players.keys())
        random.shuffle(order)
        game["deck"] = deck
        game["hands"] = hands
        game["top"] = top
        game["current_color"] = top[0]
        game["order"] = order
        game["turn"] = order[0]
        game["direction"] = 1
        game["status"] = "playing"
        game["players"] = hands
        game["last_action"] = datetime.now(timezone.utc).isoformat()
        games[gid] = game
        _save_games(games)

        await _send_hands(bot, int(gid), game)
        names = " → ".join(_player_name(game, p) for p in order)
        await rdelay()
        await uno_cmd.finish(
            f"🃏 UNO 开始！\n"
            f"顺序: {names}\n"
            f"桌面: {_card_str(top)}\n"
            f"轮到: {_player_name(game, game['turn'])}"
        )

    # ---- /uno 结束 ----
    if raw == "结束":
        game = games.get(gid)
        if not game:
            return
        if uid != game.get("host"):
            await rdelay()
            await uno_cmd.finish(MessageSegment.at(uid) + "\n只有房主能结束喵。")
        del games[gid]
        _save_games(games)
        await rdelay()
        await uno_cmd.finish("UNO 结束...没尽兴吗喵。")

    # ---- /uno（帮助）----
    if raw == "":
        await rdelay()
        await uno_cmd.finish(UNO_HELP)
        return


def _check_uno_penalty(gid: str, game: dict, bot: Bot):
    """检查上一位剩 1 张的玩家是否喊了 UNO，没喊罚 2 张"""
    pending = game.get("uno_pending")
    if not pending:
        return
    now = datetime.now(timezone.utc).timestamp()
    if pending != game.get("turn") and now > game.get("uno_deadline", 0):
        hand = game["hands"].get(pending, [])
        for _ in range(2):
            if game["deck"]:
                hand.append(game["deck"].pop())
        game["uno_pending"] = None
        game["uno_deadline"] = 0
        try:
            bot.send_group_msg(
                group_id=int(gid),
                message=MessageSegment.at(pending) + f"\n⚠ 没喊 UNO！罚 2 张牌喵。现在是 {_player_name(game, game['turn'])} 的回合。"
            )
        except Exception:
            pass


async def _handle_play_card(event, bot: Bot, uid: str, gid: str, games: dict, raw: str):
    game = games.get(gid)
    if not game or game.get("status") != "playing":
        return

    # UNO 罚牌检查
    _check_uno_penalty(gid, game, bot)

    if uid != game["turn"]:
        await bot.send_group_msg(group_id=int(gid), message=MessageSegment.at(uid) + f"\n没轮到你喵...现在是 {_player_name(game, game['turn'])}。")
        return

    hand = game["hands"].get(uid, [])
    if not hand:
        return

    # 分离颜色参数: /出 调色盘 红 或 /出 +4 蓝
    parts = raw.split()
    card_raw = parts[0]
    chosen_color_raw = parts[1] if len(parts) > 1 else ""

    # 解析出牌
    parsed = _parse_card(card_raw)
    if parsed is None:
        await bot.send_group_msg(group_id=int(gid), message=MessageSegment.at(uid) + f"\n不认识 {card_raw} 喵...试试 /出 黄5 或 /出 反转。")
        return

    p_color, p_sym = parsed
    # 动作牌/万能牌补颜色
    if p_color is None:
        p_color = game.get("current_color", "🔴")

    played = None
    for i, card in enumerate(hand):
        if card[0] == "🌈" and p_sym in WILDS:
            if card[1] == p_sym:
                played = i
                break
        elif card[0] == p_color and card[1] == p_sym:
            played = i
            break
    if played is None:
        await bot.send_group_msg(group_id=int(gid), message=MessageSegment.at(uid) + f"\n手牌没有 {raw} 喵...检查一下拼写。")
        return

    card = hand[played]
    if not _card_match(game["top"], card):
        await bot.send_group_msg(group_id=int(gid), message=MessageSegment.at(uid) + "\n这张牌对不上桌面喵...换一张或者 /过 摸牌。")
        return

    hand.pop(played)
    game["top"] = card

    # 万能牌选色：优先用参数指定的颜色
    if card[0] == "🌈":
        if chosen_color_raw in _COLOR_MAP:
            game["current_color"] = _COLOR_MAP[chosen_color_raw]
        else:
            color_count = {}
            for c, _ in hand:
                if c != "🌈":
                    color_count[c] = color_count.get(c, 0) + 1
            game["current_color"] = max(color_count, key=color_count.get) if color_count else random.choice(COLORS)
    else:
        game["current_color"] = card[0]

    # 赢了？
    if len(hand) == 0:
        scores = _calc_scores(game["hands"])
        winner_name = _player_name(game, uid)
        lines = [f"🎉 {winner_name} 赢了！"]
        for p, s in scores.items():
            if p != uid:
                lines.append(f"  {_player_name(game, p)}: -{s} 分")
        lines.append("其他人按手中残牌扣分喵。")
        del games[gid]
        _save_games(games)
        await bot.send_group_msg(group_id=int(gid), message="\n".join(lines))
        return

    # UNO 罚牌标记（剩 1 张，10 秒内喊 /uno）
    if len(hand) == 1:
        game["uno_pending"] = uid
        game["uno_deadline"] = (datetime.now(timezone.utc).timestamp() + 10)

    # 动作牌效果（叠加规则）
    accum = game.get("pending_draw", 0)
    next_p = _next_player(game)

    if card[1] == "⏭️":
        game["pending_draw"] = 0
        game["turn"] = _next_player_skip(game, 2)

    elif card[1] == "🔄":
        game["pending_draw"] = 0
        game["direction"] = -game.get("direction", 1)
        game["turn"] = _next_player(game)

    elif card[1] == "➕2":
        # +2可叠+2，+4后不能叠+2
        if game["top"][1] in ("➕2", "🌈4️⃣"):
            accum += 2
        else:
            accum = 2
        game["pending_draw"] = accum
        game["turn"] = next_p

    elif card[1] == "🌈4️⃣":
        # +4可叠+4和+2
        if game["top"][1] in ("➕2", "🌈4️⃣"):
            accum += 4
        else:
            accum = 4
        game["pending_draw"] = accum
        game["turn"] = next_p

    else:
        # 普通牌 / 调色盘：如有累积罚牌，发给当前玩家
        if accum > 0:
            for _ in range(accum):
                if game["deck"]:
                    game["hands"][uid].append(game["deck"].pop())
            game["pending_draw"] = 0
        game["turn"] = next_p

    game["last_action"] = datetime.now(timezone.utc).isoformat()
    games[gid] = game
    _save_games(games)

    # 通知群
    action_msg = ""
    if card[1] == "⏭️":
        action_msg = f" → 跳过下家"
    elif card[1] == "➕2":
        action_msg = f" → 下家面临 {game.get('pending_draw', 2)} 张罚牌（可叠+2/+4）"
    elif card[1] == "🌈4️⃣":
        action_msg = f" → 色:{game['current_color']} | 下家面临 {game.get('pending_draw', 4)} 张罚牌"
    elif card[1] == "🌈":
        action_msg = f" → 色:{game['current_color']}"

    await _send_hands(bot, int(gid), game)
    await bot.send_group_msg(
        group_id=int(gid),
        message=(
            f"🃏 {_player_name(game, uid)} 出了 {_card_str(card)}"
            + action_msg
            + f"\n轮到: {_player_name(game, game['turn'])}"
        )
    )


async def _handle_pass(event, bot: Bot, uid: str, gid: str, games: dict):
    game = games.get(gid)

    # UNO 罚牌检查
    _check_uno_penalty(gid, game, bot)
    if not game or game.get("status") != "playing":
        return
    if uid != game["turn"]:
        await bot.send_group_msg(group_id=int(gid), message=MessageSegment.at(uid) + f"\n没轮到你喵...现在是 {_player_name(game, game['turn'])}。")
        return

    # 摸一张 / 罚牌
    accum = game.get("pending_draw", 0)
    if accum > 0:
        for _ in range(accum):
            if game["deck"]:
                game["hands"][uid].append(game["deck"].pop())
        game["pending_draw"] = 0
        draw_msg = f"认命摸了 {accum} 张罚牌"
    else:
        if game["deck"]:
            game["hands"][uid].append(game["deck"].pop())
        draw_msg = "摸牌跳过"

    game["turn"] = _next_player(game)
    game["last_action"] = datetime.now(timezone.utc).isoformat()
    games[gid] = game
    _save_games(games)

    await _send_hands(bot, int(gid), game)
    await bot.send_group_msg(
        group_id=int(gid),
        message=(
            f"🃏 {_player_name(game, uid)} {draw_msg}"
            f"\n轮到: {_player_name(game, game['turn'])}"
        )
    )
    return

    # 没牌了
    await bot.send_group_msg(group_id=int(gid), message="牌堆空了...这局作废喵。")
