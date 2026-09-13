import asyncio
import os
from pathlib import Path
from typing import Optional, Tuple

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg

from src.common import at_me_only, random_delay
from .b30_core import (
    generate_b30_image, generate_b50_image, generate_fu_image,
    generate_push_score_image, get_player_info,
)
from .oauth import (
    create_authorization_url, exchange_authorization_code, get_access_token,
    has_oauth_binding, remove_oauth_binding, PUBLIC_CLIENT_ID,
)


DATA_DIR = Path("data")
OUTPUT_DIR = DATA_DIR / "b30_outputs"


def _config_value(name: str) -> str:
    value = getattr(get_driver().config, name.lower(), None)
    if value in (None, ""):
        value = os.environ.get(name, "")
    return str(value or "").strip()


def _oauth_config() -> Tuple[str, str]:
    return (
        _config_value("LXNS_OAUTH_CLIENT_ID") or PUBLIC_CLIENT_ID,
        _config_value("LXNS_OAUTH_CLIENT_SECRET"),
    )


async def _resolve_credential(user_id: str) -> Tuple[Optional[str], str]:
    client_id, client_secret = _oauth_config()
    if not has_oauth_binding(user_id):
        return None, "尚未进行 OAuth 授权"
    if not client_id:
        return None, "Bot 未配置 LXNS_OAUTH_CLIENT_ID"
    return await asyncio.to_thread(
        get_access_token, user_id, client_id, client_secret
    )


chu_cmd = on_command(
    "chu", priority=10, block=True, rule=at_me_only,
)


@chu_cmd.handle()
async def handle_chu(event: MessageEvent, args=CommandArg()):
    await random_delay()
    raw = args.extract_plain_text().strip()
    if not raw:
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n中二节奏指令...自己看喵。\n"
            "/chu bind - 获取 LXNS OAuth 授权链接\n"
            "/chu bind <授权码> - 完成绑定\n"
            "/chu unbind - 解除绑定\n"
            "/chu b30 - 生成 B30 图片\n"
            "/chu b50 - 生成 B50 图片\n"
            "/chu 推分 - 随机抽一首歌\n"
            "/chu 装福 - 随机抽一首上分曲\n"
            "——————————————\n"
            "💡 成绩查询仅支持 OAuth，请在私聊中完成授权。"
        )

    parts = raw.split(maxsplit=1)
    sub = parts[0]
    sub_args = parts[1] if len(parts) > 1 else ""

    user_id = event.get_user_id()
    if sub == "bind":
        if event.message_type != "private":
            await chu_cmd.finish(
                MessageSegment.at(user_id)
                + "\nOAuth 授权绑定请私聊 Bot 发送 /chu bind。"
            )
        client_id, client_secret = _oauth_config()
        if not client_id:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\nBot 尚未配置 LXNS OAuth 应用 ID，请联系管理员。"
            )
        code = sub_args.strip()
        if not code:
            url = await asyncio.to_thread(
                create_authorization_url, user_id, client_id
            )
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n请打开下面的 LXNS 授权链接，授权读取和更新玩家数据：\n"
                + url
                + "\n授权完成后，把页面显示的授权码发给我：/chu bind <授权码>"
            )
        credential, error = await asyncio.to_thread(
            exchange_authorization_code, user_id, code, client_id,
            client_secret,
        )
        if not credential:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + f"\nOAuth 绑定失败：{error}"
            )
        player = await asyncio.to_thread(get_player_info, credential)
        if not player:
            remove_oauth_binding(user_id)
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n授权成功，但无法读取中二节奏玩家数据。请确认已同步成绩并授权 read_player。"
            )
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + f"\nOAuth 绑定完成，已关联玩家：{player.name}。"
        )

    if sub == "unbind":
        removed = remove_oauth_binding(user_id)
        if removed:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id()) + "\n解绑了...下次记得再来喵。")
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id()) + "\n你还没绑定呢...真麻烦喵。")

    credential, auth_error = await _resolve_credential(user_id)
    if not credential:
        await chu_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + f"\n无法读取授权：{auth_error}。请先发送 /chu bind。"
        )

    if sub == "b30":
        await chu_cmd.send("B30 图片生成中...等着喵。")
        await asyncio.sleep(1.5)
        image_path = await asyncio.to_thread(
            generate_b30_image, credential, OUTPUT_DIR, user_id
        )
        if not image_path:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n数据获取失败，请重新同步 LXNS 成绩或使用 /chu bind 重新授权。"
            )
        msg = (
            MessageSegment.at(event.get_user_id())
            + "\n" + MessageSegment.image(image_path.resolve().as_uri())
            + "\nB30 生成完了喵。"
        )
        await chu_cmd.finish(msg)

    if sub == "b50":
        await chu_cmd.send("B50 图片生成中...等着喵。")
        image_path = await asyncio.to_thread(
            generate_b50_image, credential, OUTPUT_DIR, user_id
        )
        if not image_path:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n数据获取失败，请重新同步 LXNS 成绩或使用 /chu bind 重新授权。"
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
            generate_push_score_image, credential, OUTPUT_DIR, user_id)
        if not result:
            await chu_cmd.finish(
                MessageSegment.at(event.get_user_id())
                + "\n数据获取失败，请重新同步 LXNS 成绩或使用 /chu bind 重新授权。"
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
            generate_fu_image, credential, OUTPUT_DIR, user_id)
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
