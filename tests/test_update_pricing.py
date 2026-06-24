#!/usr/bin/env python3
"""Unit tests for the pure pricing logic in update_pricing.

Run with:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import update_pricing as up


class ResolveMultiplierTest(unittest.TestCase):
    # --- discountPercent path ---

    def test_discount_returns_factor_below_one(self):
        factor, label = up.resolve_multiplier({"discountPercent": 10})
        self.assertAlmostEqual(factor, 0.9)
        self.assertIn("discount=10%", label)

    def test_discount_applied_to_price(self):
        factor, _ = up.resolve_multiplier({"discountPercent": 25})
        self.assertAlmostEqual(round(100 * factor, 2), 75.0)

    def test_discount_66_67_yields_third(self):
        factor, _ = up.resolve_multiplier({"discountPercent": 66.6667})
        self.assertAlmostEqual(99 * factor, 33.0, places=2)

    def test_discount_zero_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({"discountPercent": 0})

    def test_discount_hundred_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({"discountPercent": 100})

    def test_discount_negative_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({"discountPercent": -5})

    # --- multiplier path ---

    def test_multiplier_returns_factor(self):
        factor, label = up.resolve_multiplier({"multiplier": 3})
        self.assertEqual(factor, 3)
        self.assertIn("multiplier=3", label)

    def test_multiplier_3x_applied_to_price(self):
        factor, _ = up.resolve_multiplier({"multiplier": 3})
        self.assertAlmostEqual(round(9.99 * factor, 2), 29.97)

    def test_multiplier_fractional(self):
        factor, _ = up.resolve_multiplier({"multiplier": 0.5})
        self.assertAlmostEqual(round(10 * factor, 2), 5.0)

    def test_multiplier_zero_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({"multiplier": 0})

    def test_multiplier_negative_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({"multiplier": -1})

    # --- mutual exclusivity ---

    def test_both_fields_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({"discountPercent": 10, "multiplier": 3})

    def test_neither_field_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_multiplier({})


class BestPricePointTest(unittest.TestCase):
    POINTS = [
        {"id": "a", "price": 0.99},
        {"id": "b", "price": 1.99},
        {"id": "c", "price": 2.99},
    ]

    # --- nearest (default) ---

    def test_exact_match(self):
        self.assertEqual(up.best_price_point(self.POINTS, 1.99)["id"], "b")

    def test_closest_rounds_to_nearest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 2.40)["id"], "b")
        self.assertEqual(up.best_price_point(self.POINTS, 2.60)["id"], "c")

    def test_below_lowest_picks_lowest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 0.10)["id"], "a")

    def test_above_highest_picks_highest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 99.0)["id"], "c")

    def test_single_point_always_chosen(self):
        self.assertEqual(up.best_price_point([{"id": "x", "price": 5.0}], 100.0)["id"], "x")

    # --- up (ceil to nearest tier >= target) ---

    def test_up_exact_match(self):
        self.assertEqual(up.best_price_point(self.POINTS, 1.99, "up")["id"], "b")

    def test_up_between_picks_higher(self):
        self.assertEqual(up.best_price_point(self.POINTS, 2.40, "up")["id"], "c")
        self.assertEqual(up.best_price_point(self.POINTS, 2.01, "up")["id"], "c")

    def test_up_below_lowest_picks_lowest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 0.10, "up")["id"], "a")

    def test_up_above_highest_falls_back_to_highest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 99.0, "up")["id"], "c")

    # --- down (floor to nearest tier <= target) ---

    def test_down_exact_match(self):
        self.assertEqual(up.best_price_point(self.POINTS, 1.99, "down")["id"], "b")

    def test_down_between_picks_lower(self):
        self.assertEqual(up.best_price_point(self.POINTS, 2.60, "down")["id"], "b")
        self.assertEqual(up.best_price_point(self.POINTS, 1.98, "down")["id"], "a")

    def test_down_above_highest_picks_highest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 99.0, "down")["id"], "c")

    def test_down_below_lowest_falls_back_to_lowest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 0.10, "down")["id"], "a")


class ResolveRoundingTest(unittest.TestCase):
    def test_defaults_to_nearest_when_omitted(self):
        self.assertEqual(up.resolve_rounding({}), "nearest")

    def test_each_valid_value_returned(self):
        for value in ("nearest", "up", "down"):
            self.assertEqual(up.resolve_rounding({"rounding": value}), value)

    def test_unknown_value_rejected(self):
        with self.assertRaises(ValueError):
            up.resolve_rounding({"rounding": "ceil"})


class FinalPriceSelectionTest(unittest.TestCase):
    """End-to-end choice of the final price: source × factor → nearest tier.

    Mirrors process_rule: target = round(src_price * multiplier, 2), then the
    closest available price point is chosen.
    """

    def _choose(self, rule, src_price, points, rounding="nearest"):
        factor, _ = up.resolve_multiplier(rule)
        target = round(src_price * factor, 2)
        return up.best_price_point(points, target, rounding)["price"]

    TIERS = [
        {"id": "a", "price": 4.99},
        {"id": "b", "price": 6.99},
        {"id": "c", "price": 7.99},
        {"id": "d", "price": 9.99},
        {"id": "e", "price": 19.99},
        {"id": "f", "price": 29.99},
    ]

    def test_discount_then_nearest_tier(self):
        # 9.99 * 0.30 discount = 6.993 → 6.99 tier
        self.assertEqual(self._choose({"discountPercent": 30}, 9.99, self.TIERS), 6.99)

    def test_discount_snaps_to_closest_not_floor(self):
        # 9.99 * 0.80 = 7.992 → closest is 7.99, not 6.99
        self.assertEqual(self._choose({"discountPercent": 20}, 9.99, self.TIERS), 7.99)

    def test_multiplier_then_nearest_tier(self):
        # 9.99 * 3 = 29.97 → 29.99 tier
        self.assertEqual(self._choose({"multiplier": 3}, 9.99, self.TIERS), 29.99)

    def test_multiplier_2x(self):
        # 9.99 * 2 = 19.98 → 19.99 tier
        self.assertEqual(self._choose({"multiplier": 2}, 9.99, self.TIERS), 19.99)

    def test_fractional_multiplier_rounds_target(self):
        # 9.99 * 0.5 = 4.995 → round 5.0 → closest tier 4.99
        self.assertEqual(self._choose({"multiplier": 0.5}, 9.99, self.TIERS), 4.99)

    def test_target_above_all_tiers_caps_at_top(self):
        self.assertEqual(self._choose({"multiplier": 10}, 9.99, self.TIERS), 29.99)

    def test_target_below_all_tiers_floors_at_bottom(self):
        self.assertEqual(self._choose({"discountPercent": 99}, 9.99, self.TIERS), 4.99)

    # --- same target, different rounding strategy → different tier ---

    def test_rounding_strategy_changes_chosen_tier(self):
        # 10.00 * 0.72 = 7.20, strictly between 6.99 and 7.99 tiers.
        # nearest = 6.99 (closer), up = 7.99, down = 6.99
        self.assertEqual(self._choose({"multiplier": 0.72}, 10.0, self.TIERS, "nearest"), 6.99)
        self.assertEqual(self._choose({"multiplier": 0.72}, 10.0, self.TIERS, "up"), 7.99)
        self.assertEqual(self._choose({"multiplier": 0.72}, 10.0, self.TIERS, "down"), 6.99)

    def test_up_never_below_target(self):
        # 10.00 * 2.5 = 25.00, between 19.99 and 29.99; nearest = 29.99 here,
        # but down floors to 19.99 (<= target), up ceils to 29.99 (>= target).
        self.assertEqual(self._choose({"multiplier": 2.5}, 10.0, self.TIERS, "up"), 29.99)
        self.assertEqual(self._choose({"multiplier": 2.5}, 10.0, self.TIERS, "down"), 19.99)


if __name__ == "__main__":
    unittest.main()
