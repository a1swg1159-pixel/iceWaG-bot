from nonebot import on_command

from src.common import at_me_only, main_group_only, random_delay


ping_cmd = on_command("ping", priority=10, block=True, rule=main_group_only & at_me_only)


@ping_cmd.handle()
async def handle_ping():
    await random_delay()
    await ping_cmd.finish("🏓 pong... 在呢喵。")
