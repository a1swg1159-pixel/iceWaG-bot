import ast
import unittest
from pathlib import Path


HELP_PATH = (
    Path(__file__).parents[1] / "src" / "plugins" / "help_cmd" / "__init__.py"
)


def _constant(name: str) -> str:
    tree = ast.parse(HELP_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if name in targets:
                return ast.literal_eval(node.value)
    raise AssertionError(f"missing help constant: {name}")


class HelpVisibilityTests(unittest.TestCase):
    def test_public_help_does_not_reveal_restricted_features(self):
        public_help = _constant("PUBLIC_HELP")
        restricted_terms = (
            "答题", "UNO", "/ping", "/time", "每日运势",
            "每日天气", "每日新闻", "/roll", "/email", "主群", "专属",
            "隐藏", "受限",
        )
        for term in restricted_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, public_help)

    def test_public_help_lists_every_public_interaction(self):
        public_help = _constant("PUBLIC_HELP")
        for term in (
            "/help", "/sign", "/猫猫币", "/二十一点", "/老虎机",
            "/chu", "/mai", "/takumi", "单独 @我",
        ):
            with self.subTest(term=term):
                self.assertIn(term, public_help)


if __name__ == "__main__":
    unittest.main()
