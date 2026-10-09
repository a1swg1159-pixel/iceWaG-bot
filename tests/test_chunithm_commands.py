"""Exercise the real handler without importing NoneBot or starting the bot."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from src.score_level_query import parse_constant_range_query, parse_level_query


PLUGIN = Path(__file__).parents[1] / "src/plugins/chunithm_b30/__init__.py"


class Finished(Exception):
    pass


class ChunithmCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(PLUGIN.read_text(encoding="utf-8"))
        handler = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_chu")
        handler.decorator_list = []
        pause = next(node for node in tree.body if isinstance(node, ast.Assign) and
                     any(isinstance(target, ast.Name) and target.id == "PAUSED_COMMANDS" for target in node.targets))
        self.scope = {
            "MessageEvent": object, "CommandArg": lambda: None,
            "MessageSegment": SimpleNamespace(at=lambda user: "@"+user),
            "random_delay": AsyncMock(), "_resolve_credential": AsyncMock(return_value=(None, "not bound")),
            "parse_level_query": parse_level_query, "parse_constant_range_query": parse_constant_range_query,
            "chu_cmd": SimpleNamespace(finish=AsyncMock(side_effect=Finished), send=AsyncMock()),
            "generate_fu_image": Mock(), "generate_push_score_image": Mock(),
        }
        exec(compile(ast.Module(body=[pause, handler], type_ignores=[]), str(PLUGIN), "exec"), self.scope)

    async def invoke(self, text, message_type="private"):
        event = SimpleNamespace(get_user_id=lambda: "test", message_type=message_type)
        with self.assertRaises(Finished):
            await self.scope["handle_chu"](event, SimpleNamespace(extract_plain_text=lambda: text))

    async def test_paused_commands_stop_before_authorization_network_or_rendering(self):
        for message_type in ("group", "private"):
            for command in ("装福", "推分"):
                await self.invoke(command, message_type)
        self.scope["_resolve_credential"].assert_not_awaited()
        self.scope["random_delay"].assert_not_awaited()
        self.scope["generate_fu_image"].assert_not_called()
        self.scope["generate_push_score_image"].assert_not_called()
        self.assertIn("暂时停用", self.scope["chu_cmd"].finish.call_args.args[0])

    async def test_image_queries_have_no_artificial_delay(self):
        for command in ("b30", "b50", "score 14", "fitconst 14"):
            await self.invoke(command)
        self.assertEqual(self.scope["_resolve_credential"].await_count, 4)
        self.scope["random_delay"].assert_not_awaited()

    async def test_command_help_hides_paused_commands(self):
        await self.invoke("")
        help_text = self.scope["chu_cmd"].finish.call_args.args[0]
        for term in ("装福", "推分"):
            self.assertNotIn(term, help_text)
        self.assertIn("/chu fitconst", help_text)
        self.scope["random_delay"].assert_awaited_once()

    async def test_pause_can_be_removed_without_restoring_deleted_handlers(self):
        self.scope["PAUSED_COMMANDS"] = frozenset()
        await self.invoke("推分")
        self.scope["_resolve_credential"].assert_awaited_once()
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn('if sub == "装福":', source)
        self.assertIn('if sub == "推分":', source)
        self.assertIn('generate_fu_image, credential', source)
        self.assertIn('generate_push_score_image, credential', source)
