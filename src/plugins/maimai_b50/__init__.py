import asyncio
import os
from pathlib import Path
from typing import Optional, Tuple

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg

from src.common import at_me_only, random_delay
from src.plugins.chunithm_b30.oauth import (
    create_authorization_url,
    exchange_authorization_code,
    get_access_token,
    has_oauth_binding,
    remove_oauth_binding,
)
from .core import generate_maimai_b50_image, get_maimai_player


OUTPUT_DIR = Path("data") / "b30_outputs"


def _config_value(name: str) -> str:
    value = getattr(get_driver().config, name.lower(), None)
    if value in (None, ""):
        value = os.environ.get(name, "")
    return str(value or "").strip()


def _oauth_config() -> Tuple[str, str]:
    return (
        _config_value("LXNS_OAUTH_CLIENT_ID"),
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


mai_cmd = on_command(
    "mai", priority=10, block=True, rule=at_me_only,
)


@mai_cmd.handle()
async def handle_mai(event: MessageEvent, args=CommandArg()):
    await random_delay()
    raw = args.extract_plain_text().strip()
    if not raw or raw == "help":
        await mai_cmd.finish(
            MessageSegment.at(event.get_user_id())
            + "\n舞萌 DX 指令：\n"
            "/mai bind - 获取 LXNS OAuth 授权链接\n"
            "/mai bind <授权码> - 完成绑定\n"
            "/mai unbind - 解除共享的 LXNS 授权\n"
            "/mai b50 - 生成舞萌 DX Best 50\n"
            "——————————————\n"
            "💡 与 /chu 共用 OAuth，请在私聊中完成授权。"
        )

    parts = raw.split(maxsplit=1)
    sub = parts[0].lower()
    sub_args = parts[1] if len(parts) > 1 else ""
    user_id = event.get_user_id()

    if sub == "bind":
        if event.message_type != "private":
            await mai_cmd.finish(
                MessageSegment.at(user_id)
                + "\nOAuth 授权绑定请私聊 Bot 发送 /mai bind。"
            )
        client_id, client_secret = _oauth_config()
        if not client_id:
            await mai_cmd.finish(
                MessageSegment.at(user_id)
                + "\nBot 尚未配置 LXNS OAuth 应用 ID，请联系管理员。"
            )
        code = sub_args.strip()
        if not code:
            url = await asyncio.to_thread(
                create_authorization_url, user_id, client_id
            )
            await mai_cmd.finish(
                MessageSegment.at(user_id)
                + "\n请打开下面的 LXNS 授权链接：\n"
                + url
                + "\n授权完成后发送：/mai bind <授权码>"
            )
        credential, error = await asyncio.to_thread(
            exchange_authorization_code,
            user_id,
            code,
            client_id,
            client_secret,
        )
        if not credential:
            await mai_cmd.finish(
                MessageSegment.at(user_id) + f"\nOAuth 绑定失败：{error}"
            )
        player = await asyncio.to_thread(get_maimai_player, credential)
        if not player:
            remove_oauth_binding(user_id)
            await mai_cmd.finish(
                MessageSegment.at(user_id)
                + "\n授权成功，但无法读取舞萌 DX 数据。请确认已同步成绩并授权 read_player。"
            )
        await mai_cmd.finish(
            MessageSegment.at(user_id)
            + f"\nOAuth 绑定完成，已关联舞萌玩家：{player.name}。"
        )

    if sub == "unbind":
        if remove_oauth_binding(user_id):
            await mai_cmd.finish(
                MessageSegment.at(user_id)
                + "\n已解除 LXNS OAuth；/mai 与 /chu 都需要重新授权。"
            )
        await mai_cmd.finish(MessageSegment.at(user_id) + "\n你还没有绑定。")

    if sub != "b50":
        await mai_cmd.finish(
            MessageSegment.at(user_id)
            + f"\n没有 {sub} 这个指令。发送 /mai 查看可用子命令。"
        )

    credential, error = await _resolve_credential(user_id)
    if not credential:
        await mai_cmd.finish(
            MessageSegment.at(user_id)
            + f"\n无法读取授权：{error}。请先发送 /mai bind。"
        )

    await mai_cmd.send("舞萌 DX B50 生成中...稍等喵。")
    image_path = await asyncio.to_thread(
        generate_maimai_b50_image, credential, OUTPUT_DIR, user_id
    )
    if not image_path:
        await mai_cmd.finish(
            MessageSegment.at(user_id)
            + "\n数据获取失败，请重新同步 LXNS 成绩或使用 /mai bind 重新授权。"
        )
    await mai_cmd.finish(
        MessageSegment.at(user_id)
        + "\n"
        + MessageSegment.image(image_path.resolve().as_uri())
        + "\n舞萌 DX B50 生成完成。"
    )
