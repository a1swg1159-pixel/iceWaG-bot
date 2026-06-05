from nonebot import on_command

from src.common import no_at_others, random_delay


ping_cmd = on_command("ping", priority=10, block=True, rule=no_at_others)


@ping_cmd.handle()
async def handle_ping():
    await random_delay()
    await ping_cmd.finish("🏓 pong... 在呢喵。")
