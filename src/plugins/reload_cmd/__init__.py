"""管理员热重启 - 仅限特定用户，不在帮助中显示"""
import json
import os
import sys
from pathlib import Path

from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

from src.common import random_delay

ADMIN_USER = getattr(get_driver().config, "admin_qq", "")
# 在 .env 中设置 ADMIN_QQ=你的QQ号
RELOAD_FLAG = Path("data") / ".reload_flag"


# ====== 启动时检测是否热重启完成 ======

driver = get_driver()


@driver.on_bot_connect
async def on_reload_complete(bot: Bot):
    """如果是从 /reload 重启的，上线后发消息通知"""
    if RELOAD_FLAG.exists():
        try:
            data = json.loads(RELOAD_FLAG.read_text(encoding="utf-8"))
            target_type = data.get("type")
            target_id = data.get("id")
            if target_type == "group":
                await bot.send_group_msg(
                    group_id=int(target_id),
                    message="重启完成喵。"
                )
            elif target_type == "private":
                await bot.send_private_msg(
                    user_id=int(target_id),
                    message="重启完成喵。"
                )
        except Exception:
            pass
        try:
            RELOAD_FLAG.unlink()
        except Exception:
            pass


# ====== /reload 命令 ======

reload_cmd = on_command("reload", priority=10, block=True)


@reload_cmd.handle()
async def handle_reload(event: MessageEvent, bot: Bot):
    await random_delay()

    user_id = event.get_user_id()
    if user_id != ADMIN_USER:
        await reload_cmd.finish(
            MessageSegment.at(user_id) + "\n你没有权限喵。"
        )

    # 写标记文件，重启后通知
    target_type = event.message_type
    target_id = event.group_id if target_type == "group" else event.user_id
    RELOAD_FLAG.parent.mkdir(parents=True, exist_ok=True)
    RELOAD_FLAG.write_text(
        json.dumps({"type": target_type, "id": str(target_id)}),
        encoding="utf-8",
    )

    await reload_cmd.send(
        MessageSegment.at(user_id) + "\n正在重启喵..."
    )

    python = sys.executable
    script = sys.argv[0]
    os.execv(python, [python, script])
