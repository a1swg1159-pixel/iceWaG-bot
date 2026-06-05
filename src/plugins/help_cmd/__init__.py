from nonebot import on_command

from src.common import no_at_others, random_delay


HELP_TEXT = (
    "指令...自己看喵。\n"
    "/help - 查看帮助\n"
    "/ping - 连接测试\n"
    "/time - 查看当前时间\n"
    "/每日运势 - 查看今日运势\n"
    "/每日天气 - 查看今日天气\n"
    "/每日新闻 - 今日头条热榜\n"
    "/roll <选项...> - 随机选一个\n"
    "/chu - 中二节奏相关指令\n"
    "/email - 邮件通知（请私聊使用）\n"
    "——————————————\n"
    "💡 戳我的话...可不会理你喵。"
)

help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True, rule=no_at_others)


@help_cmd.handle()
async def handle_help():
    await random_delay()
    await help_cmd.finish(HELP_TEXT)
