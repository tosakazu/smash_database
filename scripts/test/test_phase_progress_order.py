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

    def test_missing_num_seeds_keeps_phase_order(self):
        a = {"id": 1, "name": "A", "numSeeds": None, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 1}
        b = {"id": 2, "name": "B", "numSeeds": 64, "bracketType": "DOUBLE_ELIMINATION", "phaseOrder": 2}
        info, _ = R.compute_phase_global_rounds(sets(a, {1: 4, 2: 2}) + sets(b, {1: 32, 2: 16}))
        self.assertEqual((info[1]["global_round_offset"], info[2]["global_round_offset"]), (0, 2))


if __name__ == "__main__":
    unittest.main()
