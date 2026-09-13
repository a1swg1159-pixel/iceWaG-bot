"""NoneBot command surface for TAKUMI³ Best 40."""

import asyncio
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from src.common import at_me_only, random_delay

from .bindings import get_binding, remove_binding, set_binding
from .core import (
    TakumiError,
    TakumiNoScoresError,
    generate_b40_image,
    get_b40,
    link_account,
    unlink_account,
)


OUTPUT_DIR = Path("data") / "takumi_outputs"


def _display_name(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    if sender is not None:
        card = str(getattr(sender, "card", "") or "").strip()
        nickname = str(getattr(sender, "nickname", "") or "").strip()
        if card:
            return card
        if nickname:
            return nickname
    return str(event.get_user_id())


takumi_cmd = on_command(
    "takumi", aliases={"t3"}, priority=10, block=True,
    rule=at_me_only,
)


@takumi_cmd.handle()
async def handle_takumi(event: MessageEvent, args=CommandArg()):
    await random_delay()
    raw = args.extract_plain_text().strip()
    user_id = event.get_user_id()

    if not raw or raw.lower() == "help":
        await takumi_cmd.finish(
            MessageSegment.at(user_id)
            + "\nTAKUMI³ 指令：\n"
            "/takumi bind <登录邮箱> <密码> - 仅限私聊，一次性授权\n"
            "/takumi b40 - 直接查询并生成 Best 40 图片\n"
            "/takumi unbind - 撤销授权并解除绑定\n"
            "——————————————\n"
            "🔒 密码只用于本次登录，插件不会写入绑定文件；"
            "仅保存随机生成的可撤销登录凭据。"
        )

    parts = raw.split(maxsplit=2)
    sub = parts[0].lower()

    if sub == "bind":
        if event.message_type != "private":
            await takumi_cmd.finish(
                MessageSegment.at(user_id)
                + "\n为防止账号密码泄露，绑定只能私聊 Bot。请撤回含密码的群消息。"
            )
        if len(parts) != 3:
            await takumi_cmd.finish(
                "用法：/takumi bind <TAKUMI³ 登录邮箱> <密码>\n"
                "密码只用于这一次授权，插件不会写入绑定文件。"
            )

        email, password = parts[1], parts[2]
        previous = await asyncio.to_thread(get_binding, user_id)
        try:
            account = await asyncio.to_thread(link_account, email, password)
            binding = await asyncio.to_thread(
                set_binding,
                user_id,
                account.custom_id,
                account.playfab_id,
                account.display_name,
            )
        except TakumiError as exc:
            await takumi_cmd.finish(f"绑定失败：{exc}")
        except Exception:
            logger.exception("TAKUMI³ account binding failed")
            await takumi_cmd.finish("绑定时发生错误，请稍后再试。")

        # Rebinding must not leave the previous bot credential active. Failure
        # here is harmless to the new binding and is logged for maintenance.
        if previous and previous.custom_id != binding.custom_id:
            try:
                await asyncio.to_thread(unlink_account, previous.custom_id)
            except TakumiError:
                logger.warning("Failed to revoke previous TAKUMI³ binding")

        try:
            result = await asyncio.to_thread(get_b40, binding.custom_id, True)
        except TakumiNoScoresError:
            await takumi_cmd.finish(
                "账号已绑定，但这个账号还没有可用的普通模式成绩。"
            )
        except TakumiError as exc:
            await takumi_cmd.finish(
                "账号已绑定，但首次读取成绩失败："
                f"{exc}\n稍后可直接发送 /takumi b40 重试。"
            )

        completeness = (
            "完整 B40"
            if result.is_complete
            else f"{len(result.scores)}/40 个有效定数谱面"
        )
        name = account.display_name or "TAKUMI³ 玩家"
        await takumi_cmd.finish(
            f"绑定完成：{name}\n"
            f"已读取 {result.source_score_count} 条普通模式成绩，当前可生成{completeness}。"
        )

    if sub == "unbind":
        binding = await asyncio.to_thread(get_binding, user_id)
        if not binding:
            await takumi_cmd.finish("你还没有绑定 TAKUMI³ 账号。")
        revoke_failed = False
        try:
            await asyncio.to_thread(unlink_account, binding.custom_id)
        except TakumiError:
            revoke_failed = True
            logger.warning("Failed to revoke TAKUMI³ CustomID during unbind")
        await asyncio.to_thread(remove_binding, user_id)
        suffix = "（远端撤销暂时失败，本地凭据已删除）" if revoke_failed else ""
        await takumi_cmd.finish(f"TAKUMI³ 账号已解绑。{suffix}")

    if sub != "b40":
        await takumi_cmd.finish(
            MessageSegment.at(user_id)
            + f"\n没有 {sub} 这个指令。发送 /takumi 查看用法。"
        )

    binding = await asyncio.to_thread(get_binding, user_id)
    if not binding:
        await takumi_cmd.finish(
            MessageSegment.at(user_id)
            + "\n还没有绑定，请私聊 Bot 发送："
            "/takumi bind <登录邮箱> <密码>"
        )

    await takumi_cmd.send("TAKUMI³ B40 生成中...稍等喵。")
    try:
        image_path, result = await asyncio.to_thread(
            generate_b40_image,
            binding.custom_id,
            binding.display_name or _display_name(event),
            OUTPUT_DIR,
            user_id,
        )
    except TakumiError as exc:
        await takumi_cmd.finish(MessageSegment.at(user_id) + f"\n生成失败：{exc}")
    except Exception:
        logger.exception("TAKUMI³ B40 image generation failed")
        await takumi_cmd.finish(
            MessageSegment.at(user_id) + "\n生成图片时发生错误，请稍后再试。"
        )

    note = ""
    if not result.is_complete:
        note = (
            f"\n⚠️ 当前只匹配到 {len(result.scores)}/40 个"
            "有精确定数的有效谱面，Rating 可能偏低。"
        )
    await takumi_cmd.finish(
        MessageSegment.at(user_id)
        + "\n"
        + MessageSegment.image(image_path.resolve().as_uri())
        + f"\nTAKUMI³ B40：{result.rating:.3f}{note}"
    )
