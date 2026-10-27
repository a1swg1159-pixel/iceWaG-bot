import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bot_health import HEALTH_PATH, install_health_route, probe_bots


def bot(**status):
    return SimpleNamespace(get_status=AsyncMock(return_value=status))


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_connection_and_wrong_account(self):
        self.assertEqual((await probe_bots({}))["reason"], "qq_disconnected")
        self.assertEqual((await probe_bots({"1": bot(online=True)}, "2"))["reason"], "qq_disconnected")

    async def test_connected_does_not_mean_qq_online(self):
        for status, reason in (({"online": True, "good": True}, "online"),
                               ({"online": False, "good": True}, "qq_offline"),
                               ({"online": True, "good": False}, "onebot_unhealthy"),
                               ({"online": "true"}, "invalid_onebot_status"),
                               ({}, "invalid_onebot_status")):
            self.assertEqual((await probe_bots({"1": bot(**status)}))["reason"], reason)

    async def test_timeout_and_exception_do_not_leak(self):
        async def hang():
            await asyncio.sleep(100)
        dummy = SimpleNamespace(get_status=hang)
        self.assertEqual((await probe_bots({"1": dummy}, timeout=.005))["reason"], "onebot_unresponsive")
        dummy = SimpleNamespace(get_status=AsyncMock(side_effect=ValueError("private-token-data")))
        self.assertNotIn("private", str(await probe_bots({"1": dummy})))

    async def test_configured_account_is_selected(self):
        other, selected = bot(online=False), bot(online=True)
        self.assertTrue((await probe_bots({"1": other, "2": selected}, "2"))["ok"])
        other.get_status.assert_not_called()
        selected.get_status.assert_awaited_once()

    async def test_multiple_connections_require_all_healthy(self):
        result = await probe_bots({"1": bot(online=True), "2": bot(online=False)})
        self.assertFalse(result["ok"])


class RouteTests(unittest.TestCase):
    def client(self, bots, token="test-token", target=""):
        app = FastAPI()
        install_health_route(app, lambda: bots, token, target)
        return TestClient(app)

    def test_authentication_is_required_before_probing(self):
        dummy = bot(online=True)
        with self.client({"123": dummy}) as client:
            for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "x"*5000}):
                self.assertEqual(client.get(HEALTH_PATH, headers=header).status_code, 401)
        dummy.get_status.assert_not_called()

    def test_health_response_and_short_cache(self):
        dummy = bot(online=True, good=True)
        with self.client({"123": dummy}) as client:
            for _ in range(2):
                response = client.get(HEALTH_PATH, headers={"Authorization": "Bearer test-token"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["ok"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("123", response.text)
                self.assertNotIn("test-token", response.text)
        dummy.get_status.assert_awaited_once()

    def test_offline_is_503(self):
        with self.client({"123": bot(online=False)}) as client:
            response = client.get(HEALTH_PATH, headers={"Authorization": "Bearer test-token"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["reason"], "qq_offline")

    def test_missing_token_fails_closed(self):
        dummy = bot(online=True)
        for token in ("", "CHANGE_ME"):
            with self.client({"123": dummy}, token) as client:
                self.assertEqual(client.get(HEALTH_PATH).status_code, 503)
        dummy.get_status.assert_not_called()

    def test_actual_nonebot_plugin_registration_without_server(self):
        import nonebot
        # Isolated driver init only: no production .env, no server, no schedulers.
        nonebot.init(_env_file=None, driver="~fastapi", onebot_v11_access_token="test-token")
        from nonebot.adapters.onebot.v11 import Adapter
        nonebot.get_driver().register_adapter(Adapter)
        plugin = nonebot.load_plugin("src.plugins.health_monitor")
        self.assertIsNotNone(plugin)
        with TestClient(nonebot.get_app()) as client:
            response = client.get(HEALTH_PATH, headers={"Authorization": "Bearer test-token"})
            self.assertEqual(response.json()["reason"], "qq_disconnected")


if __name__ == "__main__":
    unittest.main()
