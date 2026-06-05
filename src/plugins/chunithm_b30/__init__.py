import asyncio
import json
from pathlib import Path
from typing import Dict

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg

from src.common import random_delay
from .b30_core import generate_b30_image, generate_b50_image, generate_fu_image, generate_push_score_image


DATA_DIR = Path("data")
TOKENS_FILE = DATA_DIR / "b30_tokens.json"
OUTPUT_DIR = DATA_DIR / "b30_outputs"


def load_tokens() -> Dict[str, str]:
    if not TOKENS_FILE.exists():
        return {}
    try:
        return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_tokens(data: Dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(
        json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8"
    )


chu_cmd = on_command("chu", priority=10, block=True)


@chu_cmd.handle()
async def handle_chu(event: MessageEvent, args=CommandArg()):
    await random_delay()
    raw = args.extract_plain_text().strip()
    if not raw:
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n中二节奏指令...自己看喵。\n"
            "/chu b30bind <token> - 绑定 Token\n"
            "/chu b30unbind - 解除绑定\n"
            "/chu b30 - 生成 B30 图片\n"
            "/chu b50 - 生成 B50 图片\n"
            "/chu 推分 - 随机抽一首歌\n"
            "/chu 装福 - 随机抽一首上分曲\n"
            "——————————————\n"
            "💡 需要先绑定 Token 才能用喵。"
        )

    parts = raw.split(maxsplit=1)
    sub = parts[0]
    sub_args = parts[1] if len(parts) > 1 else ""

    user_id = event.get_user_id()
    tokens = load_tokens()

    if sub == "b30bind":
        token = sub_args.strip()
        if not token:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n用法: /chu b30bind <token>喵。"
            )
        tokens[user_id] = token
        save_tokens(tokens)
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id()) + "\n绑好了...别弄丢了喵。")

    if sub == "b30unbind":
        if user_id in tokens:
            tokens.pop(user_id)
            save_tokens(tokens)
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id()) + "\n解绑了...下次记得再来喵。")
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id()) + "\n你还没绑定呢...真麻烦喵。")

    # 以下需要 Token
    token = tokens.get(user_id)
    if not token:
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n还没绑定 Token... /chu b30bind <token>，自己弄喵。"
        )

    if sub == "b30":
        await chu_cmd.send("B30 图片生成中...等着喵。")
        await asyncio.sleep(1.5)
        image_path = await asyncio.to_thread(generate_b30_image, token, OUTPUT_DIR, user_id)
        if not image_path:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n数据获取失败...Token 是不是有问题？喵。"
            )
        msg = (
            MessageSegment.at(event.get_user_id())
            + "\n" + MessageSegment.image(image_path.resolve().as_uri())
            + "\nB30 生成完了喵。"
        )
        await chu_cmd.finish(msg)

    if sub == "b50":
        await chu_cmd.send("B50 图片生成中...等着喵。")
        image_path = await asyncio.to_thread(generate_b50_image, token, OUTPUT_DIR, user_id)
        if not image_path:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n数据获取失败...Token 是不是有问题？喵。"
            )
        msg = (
            MessageSegment.at(event.get_user_id())
            + "\n" + MessageSegment.image(image_path.resolve().as_uri())
            + "\nB50 生成完了喵。"
        )
        await chu_cmd.finish(msg)

    if sub == "推分":
        await chu_cmd.send("随机抽歌中...别催喵。")
        result = await asyncio.to_thread(
            generate_push_score_image, token, OUTPUT_DIR, user_id)
        if not result:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n数据获取失败...Token 是不是有问题？喵。"
            )
        image_path, score = result
        msg = (
            MessageSegment.at(event.get_user_id())
            + "\n" + MessageSegment.image(image_path.resolve().as_uri())
            + f"\n抽到了: {score.song_name}  |  定数: {score.final_level}  |  分数: {score.score:,}  |  评级: {score.rank}喵。"
        )
        await chu_cmd.finish(msg)

    if sub == "装福":
        await chu_cmd.send("找高分曲中...等着喵。")
        result = await asyncio.to_thread(
            generate_fu_image, token, OUTPUT_DIR, user_id)
        if not result:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n没有找到高于当前 Rating 的曲目喵...你已经很强了。"
            )
        image_path, score = result
        msg = (
            MessageSegment.at(event.get_user_id())
            + "\n" + MessageSegment.image(image_path.resolve().as_uri())
            + f"\n这首单曲 RT {score.rating_floor:.2f} > 你的 RT，很厉害喵！\n"
            + f"🎵 {score.song_name}  |  定数: {score.final_level}  |  分数: {score.score:,}  |  评级: {score.rank}"
        )
        await chu_cmd.finish(msg)

    await chu_cmd.finish(
        MessageSegment.at(event.get_user_id())
        + f"\n没有 {sub} 这个指令喵。发送 /chu 查看可用子命令。"
    )
