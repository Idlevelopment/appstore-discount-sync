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

    def test_exact_match(self):
        self.assertEqual(up.best_price_point(self.POINTS, 1.99)["id"], "b")

    def test_closest_rounds_to_nearest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 2.40)["id"], "b")
        self.assertEqual(up.best_price_point(self.POINTS, 2.60)["id"], "c")

    def test_below_lowest_picks_lowest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 0.10)["id"], "a")

    def test_above_highest_picks_highest(self):
        self.assertEqual(up.best_price_point(self.POINTS, 99.0)["id"], "c")


if __name__ == "__main__":
    unittest.main()
