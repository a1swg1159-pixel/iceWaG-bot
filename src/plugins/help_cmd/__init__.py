from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent

from src.common import at_me_only, is_main_group, random_delay


GAME_HELP = (
    "指令...自己看喵。\n"
    "/help - 查看帮助\n"
    "/sign - 每日签到 (+100💰)\n"
    "/猫猫币 - 查看余额\n"
    "/二十一点 <押注> - 二十一点\n"
    "/老虎机 <押注> - 老虎机\n"
    "/答题 - 每日答题 (+50💰)\n"
    "/答题榜 - 答题排行榜\n"
    "/uno - UNO 桌游\n"
    "——————————————\n"
    "💡 这里只能玩游戏喵。"
)

FULL_HELP = GAME_HELP.replace(
    "💡 这里只能玩游戏喵。",
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

help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True, rule=at_me_only)


@help_cmd.handle()
async def handle_help(event: GroupMessageEvent):
    await random_delay()
    if is_main_group(event.group_id):
        await help_cmd.finish(FULL_HELP)
    else:
        await help_cmd.finish(GAME_HELP)
