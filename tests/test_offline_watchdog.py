import io
import json
from http.client import IncompleteRead
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from src import offline_watchdog as w


ENV = {
    "ONEBOT_V11_ACCESS_TOKEN": "test-secret",
    "ALERT_SMTP_HOST": "smtp.example.test",
    "ALERT_SMTP_USER": "sender@example.test",
    "ALERT_SMTP_PASSWORD": "secret-password",
    "ALERT_EMAIL_TO": "owner@example.test",
}


class ConfigTests(unittest.TestCase):
    def test_defaults_and_secret_repr(self):
        mail = w.MailConfig.from_env(ENV)
        self.assertEqual((mail.port, mail.security), (465, "ssl"))
        self.assertNotIn("secret-password", repr(mail))
        self.assertNotIn("owner@", repr(mail))
        self.assertNotIn("test-secret", repr(w.ProbeConfig.from_env(ENV)))
        self.assertEqual(w.Policy.from_env({}), w.Policy())

    def test_tls_and_no_header_injection(self):
        for update in ({"ALERT_SMTP_SECURITY": "none"}, {"ALERT_SMTP_PORT": "bad"},
                       {"ALERT_EMAIL_TO": "a@b.test\r\nBcc: x@y.test"},
                       {"ALERT_EMAIL_TO": "a@b.test,c@d.test"},
                       {"ALERT_INSTANCE_NAME": "name\nheader"}, {"ALERT_SMTP_PASSWORD": ""}):
            with self.assertRaises(w.ConfigError):
                w.MailConfig.from_env({**ENV, **update})
        mail = w.MailConfig.from_env({**ENV, "ALERT_SMTP_SECURITY": "starttls"})
        self.assertEqual(mail.port, 587)

    def test_probe_tokens_remain_on_local_network(self):
        for url in ("http://example.test/internal/health", "http://bot.evil.test/internal/health",
                    "http://user@bot:8081/internal/health", "http://bot/internal/health?token=x"):
            with self.assertRaises(w.ConfigError):
                w.ProbeConfig.from_env({**ENV, "ALERT_HEALTH_URL": url})
        for token in ("", "CHANGE_ME", "test\nsecret"):
            with self.assertRaises(w.ConfigError):
                w.ProbeConfig.from_env({**ENV, "ONEBOT_V11_ACCESS_TOKEN": token})


class StateTests(unittest.TestCase):
    def setUp(self):
        self.policy = w.Policy()
        self.state = w.Incident()

    def observe(self, reason, now):
        return self.state.observe(reason, now, 0, self.policy)

    def test_initial_normal_state_is_silent(self):
        for now in (0, 30, 120, 10000):
            self.assertIsNone(self.observe("online", now))

    def test_brief_disconnect_has_no_offline_or_recovery_mail(self):
        for reason, now in (("qq_offline", 0), ("qq_offline", 90), ("online", 100), ("online", 300)):
            self.assertIsNone(self.observe(reason, now))
        self.assertEqual(self.state, w.Incident())

    def test_sustained_offline_sent_only_once(self):
        self.observe("qq_disconnected", 0)
        self.assertIsNone(self.observe("qq_disconnected", 119))
        self.assertEqual(self.observe("qq_disconnected", 120), "offline")
        self.state.delivered("offline")
        for now in (150, 500, 100000):
            self.assertIsNone(self.observe("bot_unreachable", now))

    def test_startup_grace_is_respected(self):
        self.state.failure_since = 0
        self.assertIsNone(self.state.observe("qq_offline", 1000, 999, self.policy))

    def test_recovery_requires_continuous_health(self):
        self.state.failure_since, self.state.alert_sent = 0, True
        self.assertIsNone(self.observe("online", 200))
        self.assertIsNone(self.observe("qq_offline", 230))
        self.assertIsNone(self.observe("online", 260))
        self.assertIsNone(self.observe("online", 290))
        self.assertEqual(self.observe("online", 320), "recovery")
        self.state.delivered("recovery")
        self.assertEqual(self.state, w.Incident())

    def test_failed_mail_retries_after_backoff(self):
        self.state.failure_since = 0
        self.assertEqual(self.observe("qq_offline", 120), "offline")
        self.state.last_attempt = 120
        self.assertIsNone(self.observe("qq_offline", 419))
        self.assertEqual(self.observe("qq_offline", 420), "offline")

    def test_new_incident_after_confirmed_recovery(self):
        self.state.failure_since, self.state.alert_sent = 0, True
        self.observe("online", 200)
        self.assertEqual(self.observe("online", 260), "recovery")
        self.state.delivered("recovery")
        self.observe("qq_offline", 300)
        self.assertEqual(self.observe("qq_offline", 420), "offline")

    def test_persistent_dedup_and_restart_recovery_confirmation(self):
        self.state.failure_since, self.state.alert_sent = 0, True
        self.state.recovery_since = 100
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"state.json"
            self.assertTrue(w.save_state(path, self.state))
            restored = w.load_state(path, 500)
            self.assertTrue(restored.alert_sent)
            self.assertIsNone(restored.recovery_since)
            self.assertIsNone(restored.observe("qq_offline", 500, 0, self.policy))
            self.assertNotIn("secret", path.read_text())
            path.write_text('{"version":1,"failure_since":NaN}')
            self.assertEqual(w.load_state(path, 500), w.Incident())


class HTTPTests(unittest.TestCase):
    def check(self, code, payload):
        response = Mock(code=code)
        response.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(w, "build_opener", return_value=opener) as build:
            result = w.check_health(w.ProbeConfig.from_env(ENV))
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], w.NoRedirect)
        response.close.assert_called_once()
        self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer test-secret")
        return result

    def test_true_online_requires_exact_health_contract(self):
        self.assertEqual(self.check(200, {"version":1,"ok":True,"reason":"online"}), "online")
        for payload in ({"ok":True}, {"version":1,"ok":"true","reason":"online"}, []):
            self.assertEqual(self.check(200, payload), "invalid_health")

    def test_error_classification(self):
        for code, reason in ((401,"health_unauthorized"), (403,"health_unauthorized"),
                             (404,"health_missing"), (302,"invalid_health")):
            self.assertEqual(self.check(code, {}), reason)
        self.assertEqual(self.check(503, {"version":1,"ok":False,"reason":"qq_offline"}), "qq_offline")

    def test_malformed_or_oversized_responses(self):
        self.assertEqual(self.check(200, b"not-json"), "invalid_health")
        self.assertEqual(self.check(200, b"x"*16385), "invalid_health")

    def test_http_error_body_is_read_and_closed(self):
        error = HTTPError("http://bot:8081/internal/health", 503, "down", {},
                          io.BytesIO(b'{"version":1,"ok":false,"reason":"qq_disconnected"}'))
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(w, "build_opener", return_value=opener):
            self.assertEqual(w.check_health(w.ProbeConfig.from_env(ENV)), "qq_disconnected")

    def test_timeouts_are_not_treated_as_online(self):
        opener = Mock()
        opener.open.side_effect = URLError("private-url-data")
        with patch.object(w, "build_opener", return_value=opener):
            self.assertEqual(w.check_health(w.ProbeConfig.from_env(ENV)), "bot_unreachable")

    def test_interrupted_response_does_not_crash_watchdog(self):
        response = Mock(code=200)
        response.read.side_effect = IncompleteRead(b"partial")
        opener = Mock()
        opener.open.return_value = response
        with patch.object(w, "build_opener", return_value=opener):
            self.assertEqual(w.check_health(w.ProbeConfig.from_env(ENV)), "bot_unreachable")
        response.close.assert_called_once()


class MailTests(unittest.TestCase):
    def setUp(self):
        self.config = w.MailConfig.from_env(ENV)

    def test_ssl_mail_uses_verified_tls_and_no_secrets_in_message(self):
        state = w.Incident(failure_since=100, reason="qq_offline")
        message = w.notification(self.config, "offline", state, 220)
        smtp = Mock()
        smtp.send_message.return_value = {}
        with patch.object(w.smtplib, "SMTP_SSL", return_value=smtp) as connect:
            w.send_mail(self.config, message)
        context = connect.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        smtp.login.assert_called_once_with("sender@example.test", "secret-password")
        self.assertNotIn("secret-password", message.as_string())
        self.assertIn("QQ 已离线", message.get_content())

    def test_starttls_happens_before_login(self):
        config = w.MailConfig.from_env({**ENV, "ALERT_SMTP_SECURITY":"starttls"})
        smtp = Mock()
        smtp.send_message.return_value = {}
        with patch.object(w.smtplib, "SMTP", return_value=smtp):
            w.send_mail(config, w.notification(config, "test", w.Incident(), 0))
        names = [call[0] for call in smtp.method_calls]
        self.assertLess(names.index("starttls"), names.index("login"))

    def test_recovery_duration_uses_detection_times(self):
        state = w.Incident(failure_since=0, recovery_since=200, alert_sent=True)
        message = w.notification(self.config, "recovery", state, 260)
        self.assertIn("3 分 20 秒", message.get_content())

    def test_quit_failure_does_not_duplicate_delivered_message(self):
        smtp = Mock()
        smtp.send_message.return_value = {}
        smtp.quit.side_effect = smtplib.SMTPServerDisconnected("closed")
        with patch.object(w.smtplib, "SMTP_SSL", return_value=smtp):
            w.send_mail(self.config, w.notification(self.config, "test", w.Incident(), 0))
        smtp.close.assert_called_once()

    def test_complete_incident_sequence_and_restart(self):
        state = w.Incident()
        policy = w.Policy()
        with tempfile.TemporaryDirectory() as directory, patch.object(w, "send_mail") as mail:
            path = Path(directory)/"state.json"
            for now in (0,30,60,90,120,150):
                w.tick(state, "qq_offline", now, 0, policy, self.config, path)
            state = w.load_state(path, 170)
            w.tick(state, "qq_offline", 180, 170, policy, self.config, path)
            for now in (200,230,260,290):
                w.tick(state, "online", now, 170, policy, self.config, path)
            self.assertEqual(mail.call_count, 2)
            subjects = [str(call.args[1]["Subject"]) for call in mail.call_args_list]
            self.assertIn("掉线提醒", subjects[0])
            self.assertIn("恢复通知", subjects[1])
            self.assertEqual(state, w.Incident())

    def test_failed_email_is_not_marked_delivered_or_logged_with_secrets(self):
        state = w.Incident(failure_since=0)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(w, "send_mail", side_effect=smtplib.SMTPAuthenticationError(535,b'secret-password')) as mail, \
             self.assertLogs(w.logger, level="ERROR") as logs:
            path = Path(directory)/"state.json"
            for now in (120,150,419,420):
                w.tick(state, "qq_offline", now, 0, w.Policy(), self.config, path)
            self.assertEqual(mail.call_count, 2)
            self.assertFalse(state.alert_sent)
            self.assertNotIn("secret-password", str(logs.output))

    def test_check_mode_never_sends_email(self):
        with patch.dict(w.os.environ, ENV, clear=True), patch.object(w, "check_health", return_value="online"), \
             patch.object(w, "send_mail") as mail, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(w.main(["--check"]), 0)
        mail.assert_not_called()

    def test_config_failure_does_not_print_supplied_secrets(self):
        with patch.dict(w.os.environ, {**ENV, "ALERT_SMTP_SECURITY": "secret-password"}, clear=True), \
             self.assertLogs(w.logger, level="ERROR") as logs:
            self.assertEqual(w.main(["--test-email"]), 2)
        self.assertNotIn("secret-password", str(logs.output))


class DeploymentTests(unittest.TestCase):
    def test_optional_compose_is_separate_and_minimally_privileged(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        overlay = yaml.safe_load((root/"docker-compose.alerts.yml").read_text(encoding="utf-8"))
        self.assertEqual(set(overlay["services"]), {"watchdog"})
        service = overlay["services"]["watchdog"]
        self.assertEqual(service["command"], ["python", "-m", "src.offline_watchdog"])
        self.assertNotIn("ports", service)
        self.assertNotIn("container_name", service)
        self.assertNotIn("env_file", service)
        self.assertEqual(service["volumes"], ["./data/watchdog:/app/data/watchdog"])
        self.assertNotIn("depends_on", service)
        self.assertFalse(any("LXNS" in key for key in service["environment"]))
        self.assertIn("src/", (root/"Dockerfile").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
