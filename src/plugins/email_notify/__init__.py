"""邮件通知插件 —— 绑定邮箱，定时检查未读新邮件，群+私聊通知"""

import email as email_parser
import imaplib
import json
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg

from src.common import scheduler

DATA_DIR = Path("data")
BINDINGS_FILE = DATA_DIR / "email_bindings.json"

# IMAP 服务器自动匹配
IMAP_SERVERS = {
    "qq.com": ("imap.qq.com", 993),
    "foxmail.com": ("imap.qq.com", 993),
    "163.com": ("imap.163.com", 993),
    "126.com": ("imap.126.com", 993),
    "yeah.net": ("imap.yeah.net", 993),
    "gmail.com": ("imap.gmail.com", 993),
    "outlook.com": ("imap.outlook.com", 993),
    "hotmail.com": ("imap.outlook.com", 993),
    "mails.tsinghua.edu.cn": ("mails.tsinghua.edu.cn", 993),
}

CHECK_INTERVAL_MINUTES = 5


# ====== 数据读写 ======

def _domain(email_addr: str) -> str:
    return email_addr.rsplit("@", 1)[-1].lower()


def load_bindings() -> dict:
    if not BINDINGS_FILE.exists():
        return {}
    try:
        return json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_bindings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BINDINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ====== IMAP 工具 ======

def _decode_mime_words(raw: str) -> str:
    parts = decode_header(raw or "")
    result = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                result += text.decode(charset or "utf-8", errors="replace")
            except Exception:
                result += text.decode("utf-8", errors="replace")
        else:
            result += text
    return result


def connect_imap(email_addr: str, auth_code: str) -> imaplib.IMAP4_SSL | None:
    domain = _domain(email_addr)
    server_info = IMAP_SERVERS.get(domain)
    if not server_info:
        return None
    host, port = server_info
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=15)
        conn.login(email_addr, auth_code)
        return conn
    except Exception as e:
        logger.warning(f"IMAP login failed for {email_addr}: {e}")
        return None


def fetch_unseen_emails(email_addr: str, auth_code: str, since_date: str) -> list[dict]:
    """获取指定日期之后的未读邮件，查完后标记为已读"""
    conn = connect_imap(email_addr, auth_code)
    if not conn:
        return []

    try:
        sel_status, _ = conn.select("INBOX")
        if sel_status != "OK":
            return []

        # 未读 + 时间过滤: 只查上次检查之后到达的邮件
        search_criteria = f'(UNSEEN SINCE "{since_date}")'
        status, data = conn.uid("SEARCH", None, search_criteria)
        if status != "OK":
            return []

        uid_strs = data[0].split()
        if not uid_strs:
            return []

        uids = [int(uid) for uid in uid_strs]
        emails = []

        for uid in uids:
            status, msg_data = conn.uid("FETCH", str(uid).encode(), "(BODY.PEEK[HEADER])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")

            msg = email_parser.message_from_string(raw)
            subject = _decode_mime_words(msg.get("Subject", "无主题"))
            sender = _decode_mime_words(msg.get("From", "未知发件人"))
            date_str = msg.get("Date", "")

            emails.append({
                "uid": uid,
                "subject": subject,
                "sender": sender,
                "date": date_str,
            })

        # 标记为已读，确保下次不会重复推送
        for uid in uids:
            try:
                conn.uid("STORE", str(uid).encode(), "+FLAGS", "(\\Seen)")
            except Exception:
                pass

        return emails

    except Exception as e:
        logger.warning(f"Error fetching emails for {email_addr}: {e}")
        return []
    finally:
        try:
            conn.logout()
        except Exception:
            pass



# ====== 绑定指令（仅私聊） ======

email_cmd = on_command("email", priority=10, block=True)


@email_cmd.handle()
async def handle_email(event: MessageEvent, args=CommandArg()):
    if event.message_type != "private":
        await email_cmd.finish("绑定邮箱这种事...私聊我才告诉你喵。")

    raw = args.extract_plain_text().strip()
    if not raw:
        await email_cmd.finish(
            "/email bind <地址> <授权码> - 绑定邮箱\n"
            "/email unbind <地址> - 解绑邮箱\n"
            "/email list - 查看已绑定的邮箱\n"
            "——————————————\n"
            "💡 QQ邮箱/163邮箱需要用授权码，不是密码喵。"
        )

    parts = raw.split(maxsplit=2)
    sub = parts[0]
    bindings = load_bindings()
    user_id = event.get_user_id()

    # ---- bind ----
    if sub == "bind":
        if len(parts) < 3:
            await email_cmd.finish("用法: /email bind <地址> <授权码>喵。\n💡 授权码在邮箱设置里获取，不是登录密码。")
        addr = parts[1].strip()
        code = parts[2].strip()

        domain = _domain(addr)
        if domain not in IMAP_SERVERS:
            supported = "、".join(sorted(IMAP_SERVERS.keys()))
            await email_cmd.finish(f"不支持的邮箱提供商喵...目前只支持: {supported}")

        # 测试连接
        await email_cmd.send("正在连接邮箱验证...等着喵。")
        conn = connect_imap(addr, code)
        if not conn:
            await email_cmd.finish("连接失败...地址或授权码不对喵。")
        try:
            sel_status, _ = conn.select("INBOX")
            if sel_status != "OK":
                await email_cmd.finish("无法访问收件箱...这个邮箱是不是没有开启 IMAP 服务喵？")
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        if user_id not in bindings:
            bindings[user_id] = {}
        bindings[user_id][addr] = {
            "auth_code": code,
            "last_check_time": datetime.now(timezone.utc).isoformat(),
        }
        save_bindings(bindings)
        await email_cmd.finish(f"绑好了...{addr}，新邮件会通知你喵。")

    # ---- unbind ----
    if sub == "unbind":
        if len(parts) < 2:
            await email_cmd.finish("用法: /email unbind <地址>喵。")
        addr = parts[1].strip()
        if user_id not in bindings or addr not in bindings[user_id]:
            await email_cmd.finish(f"你还没绑定 {addr} 喵。")
        del bindings[user_id][addr]
        if not bindings[user_id]:
            del bindings[user_id]
        save_bindings(bindings)
        await email_cmd.finish(f"解绑了...{addr} 不会再通知了喵。")

    # ---- list ----
    if sub == "list":
        if user_id not in bindings or not bindings[user_id]:
            await email_cmd.finish("你还没绑定任何邮箱喵。")
        lines = ["你绑定的邮箱...自己看喵。"]
        for addr in bindings[user_id]:
            lines.append(f"  📧 {addr}")
        lines.append("——————————————")
        lines.append("💡 /email unbind <地址> 可以解绑喵。")
        await email_cmd.finish("\n".join(lines))

    await email_cmd.finish(f"没有 {sub} 这个子指令喵。发送 /email 查看用法。")


# ====== 定时检查 ======

@scheduler.scheduled_job("interval", minutes=CHECK_INTERVAL_MINUTES, misfire_grace_time=120)
async def check_emails():
    """每 30 分钟检查所有绑定邮箱的未读邮件"""
    bindings = load_bindings()
    if not bindings:
        return

    from nonebot import get_bots

    bots = get_bots()
    if not bots:
        return
    bot: Bot = list(bots.values())[0]

    for user_id_str, email_dict in bindings.items():
        user_id = int(user_id_str)

        for addr, info in email_dict.items():
            auth_code = info.get("auth_code", "")
            last_check = info.get("last_check_time", "2020-01-01T00:00:00+00:00")
            if not auth_code:
                continue

            try:
                since_date = datetime.fromisoformat(last_check).strftime("%d-%b-%Y")
            except Exception:
                since_date = "01-Jan-2020"

            emails = fetch_unseen_emails(addr, auth_code, since_date)

            # 更新检查时间
            now_ts = datetime.now(timezone.utc).isoformat()
            bindings[user_id_str][addr]["last_check_time"] = now_ts
            save_bindings(bindings)

            if emails:
                count = len(emails)
                logger.info(f"New email: {count} for {addr}")

                # ---- 私聊通知 ----
                lines = [f"📧 {addr} 新邮件通知"]
                lines.append("——————————————")
                for mail in emails:
                    lines.append(f"📨 {mail['subject']}")
                    lines.append(f"   发件人: {mail['sender']}")
                    lines.append(f"   时间: {mail['date']}")
                    lines.append("")
                lines.append("哼...才不是特意通知你的喵。")

                private_msg = "\n".join(lines)
                try:
                    await bot.send_private_msg(user_id=user_id, message=private_msg)
                except Exception as e:
                    logger.warning(f"Failed to send private notif to {user_id}: {e}")
