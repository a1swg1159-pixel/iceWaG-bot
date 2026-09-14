import unittest

from src.score_level_query import (
    LevelQueryError,
    display_level_from_constant,
    matches_level_query,
    parse_level_query,
)


class ScoreLevelQueryTests(unittest.TestCase):
    def test_integer_and_plus_are_display_buckets(self):
        level_15 = parse_level_query("15")
        level_14_plus = parse_level_query("14+")
        self.assertEqual((level_15.kind, level_15.label), ("display", "15"))
        self.assertEqual((level_14_plus.kind, level_14_plus.label), ("display", "14+"))

    def test_decimal_is_an_exact_constant(self):
        query = parse_level_query("15.1")
        self.assertTrue(query.is_constant)
        self.assertEqual(query.value, 15.1)
        self.assertTrue(matches_level_query(
            query, display_level="15", constant=15.1, plus_threshold=0.7,
        ))
        self.assertFalse(matches_level_query(
            query, display_level="15", constant=15.0, plus_threshold=0.7,
        ))

    def test_integer_does_not_mean_exact_point_zero(self):
        query = parse_level_query("15")
        self.assertTrue(matches_level_query(
            query, display_level="15", constant=15.2, plus_threshold=0.7,
        ))
        self.assertTrue(matches_level_query(
            query, display_level="", constant=15.6, plus_threshold=0.7,
        ))
        self.assertFalse(matches_level_query(
            query, display_level="", constant=15.7, plus_threshold=0.7,
        ))

    def test_game_specific_plus_threshold_fallback(self):
        self.assertEqual(display_level_from_constant(14.5, 0.5), "14+")
        self.assertEqual(display_level_from_constant(14.5, 0.7), "14")
        self.assertEqual(display_level_from_constant(14.7, 0.7), "14+")

    def test_common_input_variants_and_invalid_values(self):
        self.assertEqual(parse_level_query("Lv.14＋").label, "14+")
        for value in ("", "abc", "14.25", "0", "21"):
            with self.subTest(value=value), self.assertRaises(LevelQueryError):
                parse_level_query(value)

    def test_every_game_level_and_one_decimal_constant_uses_same_rules(self):
        for base in range(1, 16):
            with self.subTest(query=str(base)):
                query = parse_level_query(str(base))
                self.assertEqual((query.kind, query.label), ("display", str(base)))
            with self.subTest(query=f"{base}+"):
                query = parse_level_query(f"{base}+")
                self.assertEqual((query.kind, query.label), ("display", f"{base}+"))
            for decimal in range(10):
                value = f"{base}.{decimal}"
                with self.subTest(query=value):
                    query = parse_level_query(value)
                    self.assertEqual((query.kind, query.label), ("constant", value))


if __name__ == "__main__":
    unittest.main()
