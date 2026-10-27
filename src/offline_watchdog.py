"""Independent QQ/Bot outage monitor with TLS-only email notifications.

No NoneBot imports: this process remains usable when the Bot is stopped or
blocked. It cannot report an entire host/network failure from that same host.
Run exactly one watchdog per deployment. No mail is sent at module import.
"""
import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from http.client import HTTPException
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import smtplib
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

logger = logging.getLogger("icewag.watchdog")
STATE_PATH = Path("data/watchdog/state.json")
CHINA = timezone(timedelta(hours=8))
REASONS = {
    "online": "Bot 与 QQ 在线状态正常",
    "qq_disconnected": "QQ 的 OneBot 连接已断开，或指定账号未连接",
    "qq_offline": "NapCat 仍能响应，但报告 QQ 已离线",
    "onebot_unresponsive": "OneBot 状态接口超时或调用失败",
    "onebot_unhealthy": "OneBot 报告运行状态异常",
    "invalid_onebot_status": "OneBot 返回了无法确认在线状态的数据",
    "bot_unreachable": "Bot 健康接口不可达或超时，可能进程停止、卡住或内部网络异常",
    "health_unauthorized": "健康检查鉴权失败，请检查两端的 OneBot Token",
    "health_missing": "找不到健康检查接口，请确认 Bot 已更新且插件加载成功",
    "health_not_configured": "Bot 未配置有效的 OneBot Token，健康检查未开放",
    "invalid_health": "健康检查响应格式异常，无法确认在线状态",
}


class ConfigError(ValueError):
    pass


def _number(env, key, default, low, high):
    try:
        value = int(env.get(key, "") or default)
    except (TypeError, ValueError):
        raise ConfigError(f"{key} 必须是整数") from None
    if not low <= value <= high:
        raise ConfigError(f"{key} 必须在 {low}～{high} 之间")
    return value


def _address(value, key):
    value = value.strip()
    if not re.fullmatch(r"[^\s@<>,;\x00-\x1f]+@[^\s@<>,;\x00-\x1f]+\.[^\s@<>,;\x00-\x1f]+", value):
        raise ConfigError(f"{key} 请填写一个完整邮箱地址，不要填写显示名称或多个地址")
    return value


@dataclass(frozen=True)
class ProbeConfig:
    token: str = field(repr=False)
    url: str = "http://127.0.0.1:8081/internal/health"

    @classmethod
    def from_env(cls, env):
        token = env.get("ONEBOT_V11_ACCESS_TOKEN", "").strip()
        if not token or token == "CHANGE_ME" or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ConfigError("请先配置有效的 ONEBOT_V11_ACCESS_TOKEN")
        url = env.get("ALERT_HEALTH_URL", "") or cls.url
        parsed = urlsplit(url)
        # Tokens stay on the local host / Docker service network. Never follow
        # a redirect or proxy a health request through an external service.
        if (parsed.scheme != "http" or parsed.hostname not in ("bot", "localhost", "127.0.0.1", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != "/internal/health"):
            raise ConfigError("ALERT_HEALTH_URL 必须是本机或 bot 服务的 /internal/health 地址")
        return cls(token, url)


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    user: str = field(repr=False)
    password: str = field(repr=False)
    sender: str = field(repr=False)
    recipient: str = field(repr=False)
    name: str = "iceWaG-bot"

    @classmethod
    def from_env(cls, env):
        for key in ("ALERT_SMTP_HOST", "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD", "ALERT_EMAIL_TO"):
            if not env.get(key, "").strip():
                raise ConfigError(f"尚未配置 {key}，请在服务器 .env 中填写")
        security = (env.get("ALERT_SMTP_SECURITY", "") or "ssl").lower()
        if security not in ("ssl", "starttls"):
            raise ConfigError("ALERT_SMTP_SECURITY 只允许 ssl 或 starttls，不允许明文认证")
        port = _number(env, "ALERT_SMTP_PORT", 465 if security == "ssl" else 587, 1, 65535)
        host = env["ALERT_SMTP_HOST"].strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ConfigError("ALERT_SMTP_HOST 请只填写邮件服务器域名或 IP")
        name = (env.get("ALERT_INSTANCE_NAME", "") or "iceWaG-bot").strip()
        if len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ConfigError("ALERT_INSTANCE_NAME 过长或包含换行")
        user = env["ALERT_SMTP_USER"].strip()
        return cls(host, port, security, user, env["ALERT_SMTP_PASSWORD"],
                   _address(env.get("ALERT_EMAIL_FROM", "") or user, "ALERT_EMAIL_FROM"),
                   _address(env["ALERT_EMAIL_TO"], "ALERT_EMAIL_TO"), name)


@dataclass(frozen=True)
class Policy:
    interval: int = 30
    offline_seconds: int = 120
    recovery_seconds: int = 60
    startup_grace: int = 120
    retry_seconds: int = 300

    @classmethod
    def from_env(cls, env):
        interval = _number(env, "ALERT_CHECK_INTERVAL", 30, 10, 300)
        return cls(interval,
                   _number(env, "ALERT_OFFLINE_SECONDS", 120, interval, 86400),
                   _number(env, "ALERT_RECOVERY_SECONDS", 60, interval, 3600),
                   _number(env, "ALERT_STARTUP_GRACE", 120, 0, 3600),
                   _number(env, "ALERT_RETRY_SECONDS", 300, 60, 86400))


@dataclass
class Incident:
    failure_since: float | None = None
    recovery_since: float | None = None
    last_attempt: float | None = None
    alert_sent: bool = False
    reason: str = "online"

    def observe(self, reason, now, started, policy):
        if reason == "online":
            if not self.alert_sent:
                self.reset()
                return None
            if self.recovery_since is None:
                self.recovery_since, self.last_attempt = now, None
            if now - self.recovery_since >= policy.recovery_seconds and self.retry_due(now, policy):
                return "recovery"
            return None
        if self.recovery_since is not None:
            self.recovery_since, self.last_attempt = None, None
        self.reason = reason if reason in REASONS else "invalid_health"
        if self.failure_since is None:
            self.failure_since = now
        if (not self.alert_sent and now - started >= policy.startup_grace
                and now - self.failure_since >= policy.offline_seconds and self.retry_due(now, policy)):
            return "offline"
        return None

    def retry_due(self, now, policy):
        return self.last_attempt is None or now - self.last_attempt >= policy.retry_seconds

    def delivered(self, kind):
        if kind == "offline":
            self.alert_sent = True
        elif kind == "recovery":
            self.reset()

    def reset(self):
        self.failure_since = self.recovery_since = self.last_attempt = None
        self.alert_sent = False
        self.reason = "online"


def load_state(path, now):
    try:
        if path.stat().st_size > 16384:
            raise ValueError("oversized")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("invalid")
        state = Incident()
        for name in ("failure_since", "last_attempt"):
            value = data.get(name)
            if value is not None:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("invalid timestamp")
                setattr(state, name, min(float(value), now))
        state.alert_sent = data.get("alert_sent") is True
        state.reason = data.get("reason") if data.get("reason") in REASONS else "invalid_health"
        # A process restart is not proof of uninterrupted recovery.
        if state.alert_sent and state.failure_since is None:
            state.failure_since = now
        return state
    except FileNotFoundError:
        return Incident()
    except (OSError, ValueError, TypeError, RecursionError):
        logger.warning("监控状态无法读取；重新建立状态（不输出文件内容）")
        return Incident()


def save_state(path, state):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"version": 1, **asdict(state)}), encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        logger.error("监控状态无法保存；当前进程仍去重，但重启可能重复提醒")
        return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_health(config):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(config.url, headers={"Authorization": "Bearer " + config.token})
    response = None
    try:
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        code = response.code
        if code in (401, 403):
            return "health_unauthorized"
        if code == 404:
            return "health_missing"
        if code not in (200, 503):
            return "invalid_health"
        raw = response.read(16385)
        if len(raw) > 16384:
            return "invalid_health"
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return "invalid_health"
        reason = payload.get("reason")
        if code == 200 and payload.get("ok") is True and reason == "online":
            return "online"
        if code == 503 and payload.get("ok") is False and reason in REASONS and reason != "online":
            return reason
        return "invalid_health"
    except (URLError, OSError, TimeoutError, HTTPException):
        return "bot_unreachable"
    except (ValueError, TypeError, RecursionError):
        return "invalid_health"
    finally:
        if response is not None:
            response.close()


def stamp(value):
    return datetime.fromtimestamp(value, CHINA).strftime("%Y-%m-%d %H:%M:%S +08:00")


def notification(config, kind, state, now):
    title = {"offline": "掉线提醒", "recovery": "恢复通知", "test": "邮件测试"}[kind]
    message = EmailMessage()
    message["Subject"] = f"[{config.name}] {title}"
    message["From"], message["To"] = config.sender, config.recipient
    message["Date"] = format_datetime(datetime.fromtimestamp(now, CHINA))
    message["Message-ID"] = make_msgid()
    if kind == "test":
        body = "这是一封主动发送的测试邮件，不代表 QQ 或 Bot 掉线。\n请检查收件箱和垃圾邮件箱。"
    elif kind == "offline":
        body = (f"检测到持续异常：{REASONS[state.reason]}。\n"
                f"首次发现：{stamp(state.failure_since)}\n"
                "请查看 Bot / NapCat 日志，并在 NapCat 管理页面确认 QQ 登录状态。\n"
                "同一次异常不重复提醒；稳定恢复后会发送恢复通知。")
    else:
        recovered = state.recovery_since if state.recovery_since is not None else now
        failed = state.failure_since if state.failure_since is not None else now
        duration = max(0, int(recovered - failed))
        body = f"Bot 健康检查已恢复，QQ 在线状态已连续正常。\n本次异常约持续 {duration // 60} 分 {duration % 60} 秒。"
    message.set_content(f"{config.name}\n{body}\n\n通知时间：{stamp(now)}\n自动监控 · generated by iceWaG")
    return message


def send_mail(config, message):
    context = ssl.create_default_context()
    smtp = None
    try:
        if config.security == "ssl":
            smtp = smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context)
        else:
            smtp = smtplib.SMTP(config.host, config.port, timeout=15)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config.user, config.password)
        refused = smtp.send_message(message, from_addr=config.sender, to_addrs=[config.recipient])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        # A failed QUIT after a successful DATA is not a failed delivery.
        if smtp is not None:
            try:
                smtp.quit()
            except OSError:
                smtp.close()


def tick(state, reason, now, started, policy, mail, path=STATE_PATH):
    before = asdict(state)
    action = state.observe(reason, now, started, policy)
    if action:
        state.last_attempt = now
        save_state(path, state)
        try:
            send_mail(mail, notification(mail, action, state, now))
        except (OSError, ValueError):
            logger.error("邮件发送失败；%s 秒后再试。请检查 SMTP、授权码及出口网络（凭据不输出）", policy.retry_seconds)
        else:
            state.delivered(action)
            logger.info("%s邮件已提交给 SMTP 服务器", "掉线" if action == "offline" else "恢复")
    if asdict(state) != before:
        save_state(path, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="本机运行时显式读取 .env；Docker 使用环境变量")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只检查一次，不发送邮件")
    mode.add_argument("--test-email", action="store_true", help="主动发送一封测试邮件，不修改监控状态")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        if args.env_file:
            from dotenv import load_dotenv
            if not args.env_file.is_file():
                raise ConfigError("指定的环境配置文件不存在")
            load_dotenv(args.env_file, override=False)
        if args.test_email:
            mail = MailConfig.from_env(os.environ)
            send_mail(mail, notification(mail, "test", Incident(), time.time()))
            logger.info("测试邮件已提交给 SMTP 服务器，请检查收件箱/垃圾邮件箱")
            return 0
        probe = ProbeConfig.from_env(os.environ)
        if args.check:
            reason = check_health(probe)
            print(json.dumps({"ok": reason == "online", "reason": reason, "detail": REASONS[reason]}, ensure_ascii=False))
            return 0 if reason == "online" else 1
        mail = MailConfig.from_env(os.environ)
        policy = Policy.from_env(os.environ)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except (OSError, ValueError):
        logger.error("配置读取或测试邮件发送失败；检查配置及网络，凭据不输出")
        return 2

    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    started = time.time()
    state = load_state(STATE_PATH, started)
    last_reason = None
    logger.info("独立掉线监控已启动：间隔 %s 秒，持续异常 %s 秒后通知，恢复确认 %s 秒",
                policy.interval, policy.offline_seconds, policy.recovery_seconds)
    while not stopped.is_set():
        reason = check_health(probe)
        if stopped.is_set():
            break
        if reason != last_reason:
            logger.info("检测状态：%s", REASONS[reason])
            last_reason = reason
        tick(state, reason, time.time(), started, policy, mail)
        stopped.wait(policy.interval)
    logger.info("监控已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
