"""download.py --quick (download-japan-quick): the decisions in download_policy, no network."""
import unittest

from scripts.common import download_policy as P

DAY = 86400
NOW = 1_800_000_000
FINISH = NOW - DAY           # the workflow passes "24 hours ago"
START = NOW + DAY            # today 23:59:59 or later
COMPLETED, ACTIVE = 3, 2


def act(state=COMPLETED, end=NOW - 3600, start=NOW - 7200, done=False, start_date=START):
    return P.quick_tournament_action(state, end, start, FINISH, start_date, done)


class QuickTournamentActionTests(unittest.TestCase):
    def test_completed_within_the_window_is_fetched(self):
        self.assertEqual(act(), (None, None))

    def test_not_completed_is_skipped_even_after_endat(self):
        self.assertEqual(act(state=ACTIVE)[0], "skip")
        self.assertEqual(act(state=None)[0], "skip")

    def test_done_is_never_refetched(self):
        # the full mode re-fetches done tournaments that ended within 3 days; --quick does not
        self.assertEqual(act(done=True), ("skip", "already downloaded"))

    def test_stops_at_the_first_tournament_older_than_the_window(self):
        # also when it is done (the full mode only stops at a tournament that is not done)
        self.assertEqual(act(end=FINISH - 1)[0], "stop")
        self.assertEqual(act(end=FINISH - 1, done=True)[0], "stop")
        self.assertEqual(act(end=FINISH - 1, state=ACTIVE)[0], "stop")

    def test_window_boundary(self):
        self.assertEqual(act(end=FINISH), (None, None))

    def test_no_endat_older_than_the_window_is_skipped_not_stopped(self):
        # no guaranteed position in the "endAt desc" listing
        self.assertEqual(act(end=None, start=FINISH - 1)[0], "skip")
        self.assertEqual(act(end=None, start=NOW - 60), (None, None))

    def test_newer_than_start_date_is_skipped(self):
        self.assertEqual(act(start=START + 1, end=START + 3600)[0], "skip")
        self.assertEqual(act(start_date=None), (None, None))


class QuickEventActionTests(unittest.TestCase):
    def test_present_files_are_not_refetched(self):
        self.assertEqual(P.quick_event_action(True)[0], "skip")
        self.assertEqual(P.quick_event_action(False)[0], "download")


if __name__ == "__main__":
    unittest.main()
