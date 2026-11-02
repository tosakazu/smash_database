"""backfill_world: after pass 1 a region is walked once more (tournaments left out of done.csv)."""
import unittest

from scripts.common import backfill_world as bw


class PassTests(unittest.TestCase):
    def test_start_next_pass(self):
        prog = {"done_months": ["2026-09", "2026-10"], "partial": {"2026-08": ["FR"]},
                "failures": {"2026-07/FR": 1}, "gave_up": ["2019-01/DE"]}
        bw.start_next_pass(prog)
        self.assertEqual(prog["pass"], 2)
        self.assertEqual(prog["done_months_pass1"], ["2026-10", "2026-09"])
        self.assertEqual((prog["done_months"], prog["partial"], prog["failures"]), ([], {}, {}))
        self.assertEqual(prog["gave_up"], ["2019-01/DE"])   # kept: listed to redo by hand

    def test_two_passes(self):
        self.assertEqual(bw.PASSES, 2)


if __name__ == "__main__":
    unittest.main()
