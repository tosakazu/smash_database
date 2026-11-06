"""redownload_matches_v2: main phases are joined in progression order (numSeeds descending), not phaseOrder."""
import unittest

from scripts.common import redownload_matches_v2 as R


def sets(phase, rounds):
    """(set_node, phase_info, pg_info) for a DE phase: {round: number of sets}."""
    return [({"round": r}, phase, {}) for r, n in rounds.items() for _ in range(n)]


class PhaseProgressOrderTest(unittest.TestCase):
    def test_top8_created_before_top96(self):
        # start.gg phaseOrder: Bracket 1, TOP8 2, TOP96 3 — but players go Bracket → TOP96 → TOP8 (第74回スマバト)
        bracket = {"id": 1, "name": "Bracket", "numSeeds": 256, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 1}
        top8 = {"id": 2, "name": "TOP8", "numSeeds": 8, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 2}
        top96 = {"id": 3, "name": "TOP96", "numSeeds": 96, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 3}
        data = sets(bracket, {1: 128, 2: 64, 3: 32}) + sets(top8, {1: 2, 2: 1, 3: 1}) + sets(top96, {1: 16, 2: 8, 3: 4})
        info, _ = R.compute_phase_global_rounds(data)
        self.assertEqual([info[p]["global_round_offset"] for p in (1, 3, 2)], [0, 3, 6])
        cap = R.effective_bracket_capacity(256)
        label = lambda ph, r: R.compute_global_top_x(r, ph, info, cap, {}, None)[2]
        self.assertEqual(label(top96, 1), "Winners TOP 32")
        self.assertEqual(label(top8, 1), "Winners TOP 4")
        # the old order (phaseOrder) is kept for the ranking build, only where it differs
        legacy, _ = R.compute_phase_global_rounds(data, R._legacy_phase_key)
        old = lambda ph, r: R.legacy_order_fields(r, ph, legacy, cap, {}, None, *R.compute_global_top_x(r, ph, info, cap, {}, None)[1:])
        self.assertEqual(old(top96, 1), {"legacy_global_top_x": 4, "legacy_global_bracket_label": "Winners TOP 4"})
        self.assertEqual(old(bracket, 1), {})

    def test_missing_num_seeds_keeps_phase_order(self):
        a = {"id": 1, "name": "A", "numSeeds": None, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 1}
        b = {"id": 2, "name": "B", "numSeeds": 64, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 2}
        info, _ = R.compute_phase_global_rounds(sets(a, {1: 4, 2: 2}) + sets(b, {1: 32, 2: 16}))
        self.assertEqual((info[1]["global_round_offset"], info[2]["global_round_offset"]), (0, 2))

    def test_later_phase_top_n_from_its_own_size(self):
        # マエスマ GRAND WARS#3: pools TOP384 cut at the WB QF, a parallel bracket ("H class" was not a class before B〜J, so joined as a main
        # phase), then TOP96 → TOP8. Accumulating rounds pushed TOP96 and TOP8 to TOP 2; each later phase now uses its
        # own WB size (2 x the sets of its first effective round)
        pools = {"id": 1, "name": "★TOP384", "numSeeds": 374, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 1}
        h = {"id": 2, "name": "Side bracket (TOP384)", "numSeeds": 118, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 2}
        top96 = {"id": 3, "name": "★TOP96", "numSeeds": 96, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 3}
        top8 = {"id": 4, "name": "★TOP8", "numSeeds": 8, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 4}
        data = (sets(pools, {1: 118, 2: 128, 3: 64, 4: 32}) + sets(h, {1: 54, 2: 32, 3: 16, 4: 8, 5: 4, 6: 2, 7: 1})
                + sets(top96, {1: 16}) + sets(top8, {1: 2, 2: 1}))
        cap = R.effective_bracket_capacity(374)
        info, _ = R.compute_phase_global_rounds(data)
        label = lambda ph, r: R.compute_global_top_x(r, ph, info, cap, {}, None)[2]
        self.assertEqual(label(pools, 2), "Winners TOP 256")       # the first phase is unchanged
        self.assertEqual(label(top96, 1), "Winners TOP 32")
        self.assertEqual(label(top8, 1), "Winners TOP 4")
        self.assertEqual(label(top8, 2), "Winners TOP 2")
        old_info, _ = R.compute_phase_global_rounds(data, own_size=False)
        self.assertEqual(R.compute_global_top_x(1, top8, old_info, cap, {}, None)[2], "Winners TOP 2")   # the old (collapsed) label
        # the ranking build keeps ordering by the old labels (legacy_global_*)
        legacy, _ = R.compute_phase_global_rounds(data, R._legacy_phase_key, own_size=False)
        new = R.compute_global_top_x(1, top96, info, cap, {}, None)
        self.assertEqual(R.legacy_order_fields(1, top96, legacy, cap, {}, None, *new[1:])["legacy_global_top_x"], 2)


class LosersRoundDirectionTest(unittest.TestCase):
    """start.gg numbers LB rounds so that the LB final has the largest |round| (19 entrants: -4 … -10)."""

    def test_class_bracket_counts_from_the_lb_final(self):
        b = {"id": 5, "name": "Bクラス", "numSeeds": 16, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 2}
        main = {"id": 1, "name": "Main", "numSeeds": 32, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 1}
        data = sets(main, {1: 16, 2: 8}) + sets(b, {1: 8, 2: 4, 3: 2, 4: 1, -3: 4, -4: 4, -5: 2, -6: 1})
        info, _ = R.compute_phase_global_rounds(data)
        label = lambda r: R.compute_global_top_x(r, b, info, 32, {}, None)[2]
        self.assertEqual([label(r) for r in (-3, -4, -5, -6)],
                         ["B-Losers TOP 8", "B-Losers TOP 6", "B-Losers TOP 4", "B-Losers TOP 3"])
        # the ranking build keeps ordering by the old labels (round -6 was counted as far from the final)
        legacy, _ = R.compute_phase_global_rounds(data, R._legacy_phase_key)
        old = R.legacy_order_fields(-6, b, legacy, 32, {}, None, *R.compute_global_top_x(-6, b, info, 32, {}, None)[1:])
        self.assertEqual(old, {"legacy_global_top_x": 16, "legacy_global_bracket_label": "B-Losers TOP 16"})

    def test_main_fallback_without_placement(self):
        top8 = {"id": 2, "name": "TOP8", "numSeeds": 8, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 2}
        pools = {"id": 1, "name": "Pools", "numSeeds": 64, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 1}
        data = sets(pools, {1: 32, 2: 16, 3: 8, -1: 16, -2: 8, -3: 4}) + sets(top8, {1: 4, 2: 2, 3: 1, -1: 2, -2: 2, -3: 1, -4: 1})
        info, _ = R.compute_phase_global_rounds(data)
        top = lambda ph, r: R.compute_global_top_x(r, ph, info, 64, {}, None)[1]
        self.assertEqual([top(top8, r) for r in (-1, -2, -3, -4)], [8, 6, 4, 3])
        self.assertEqual(top(pools, -3), 12)   # 8 players go on to TOP8 → the pool LB final loser is 9th-12th
        # with the loser's placement, the placement wins (unchanged)
        self.assertEqual(R.compute_global_top_x(-4, top8, info, 64, {7: 5}, 7)[1], 6)

    def test_phase_local_losers_top(self):
        self.assertEqual([R.losers_top_x(r, 10) for r in (-4, -10)], [24, 3])
        self.assertEqual(R.losers_top_x(-1, None), R.losers_top_x_legacy(-1))

class ClassLetterTest(unittest.TestCase):
    def test_b_to_j(self):
        # マエスマ GRAND WARS: F/G/H class are lower classes (2026-11-06). A class / S class are not
        for name, want in [("F class (TOP192)", "F"), ("H class (TOP384)", "H"), ("Ｇクラス", "G"), ("B-class", "B"),
                           ("Aクラストーナメント", None), ("Sクラス", None), ("K class", None)]:
            self.assertEqual(R._get_class_letter(name), want, name)


if __name__ == "__main__":
    unittest.main()

