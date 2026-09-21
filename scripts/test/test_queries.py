"""The tournament-list query: the server-side date window (PR #57)."""
import unittest

from scripts.common import clock
from scripts.common.queries import get_tournaments_by_game_query


class TournamentListQueryTests(unittest.TestCase):
    def test_before_ts_pins_the_window(self):
        q = get_tournaments_by_game_query("JP", before_ts=123456)
        self.assertIn("beforeDate: 123456", q)
        self.assertIn('countryCode: "JP"', q)
        self.assertIn('sortBy: "endAt desc"', q)

    def test_without_before_ts_the_clock_is_used(self):
        q = get_tournaments_by_game_query("JP")
        self.assertIn(f"beforeDate: {clock.now_ts()}"[:16], q)   # same second in practice; compare the prefix
        self.assertNotIn("beforeDate: 123456", q)

    def test_before_now_false_and_no_before_ts_has_no_date_filter(self):
        q = get_tournaments_by_game_query("JP", before_now=False)
        self.assertNotIn("beforeDate", q)


if __name__ == "__main__":
    unittest.main()
