"""An event saved with nothing in it is not marked done until start.gg reports it COMPLETED (download_policy.empty_event_action).

Funasuma 2026-11-01: the organizer set endAt to 10/31 23:59, so the 11/01 03:05 run fetched it before it was held
(0 entrants, 0 matches) and marked it done; it was never fetched again.
"""
import json
import os
import tempfile
import unittest

from scripts.common import download_policy as P
from scripts.common.download import _event_is_empty

DAY = 86400
NOW = 1_800_000_000


class EmptyEventActionTests(unittest.TestCase):
    def test_not_completed_within_the_window_waits(self):
        for state in ("CREATED", "ACTIVE", None):
            self.assertEqual(P.empty_event_action(state, NOW - DAY, NOW)[0], "wait")

    def test_endat_in_the_future_still_waits(self):
        self.assertEqual(P.empty_event_action("ACTIVE", NOW + DAY, NOW)[0], "wait")

    def test_completed_is_fetched(self):
        self.assertEqual(P.empty_event_action("COMPLETED", NOW - DAY, NOW)[0], "download")
        # also after the window: the state says it is final
        self.assertEqual(P.empty_event_action("COMPLETED", NOW - 30 * DAY, NOW)[0], "download")

    def test_after_the_window_falls_back_to_the_usual_rules(self):
        # never held (organizer left it CREATED): marked done as before, no more requests
        self.assertEqual(P.empty_event_action("CREATED", NOW - P.RETRY_WINDOW_SEC, NOW), (None, None))
        self.assertEqual(P.empty_event_action("CREATED", None, NOW), (None, None))


class EventIsEmptyTests(unittest.TestCase):
    def _dir(self, standings, matches):
        d = tempfile.mkdtemp()
        for name, rows in (("standings.json", standings), ("matches.json", matches)):
            if rows is not None:
                with open(os.path.join(d, name), "w") as f:
                    json.dump({"data": rows}, f)
        return d

    def test_empty(self):
        self.assertTrue(_event_is_empty(self._dir([], [])))

    def test_standings_or_matches_present(self):
        self.assertFalse(_event_is_empty(self._dir([{"placement": 1}], [])))
        self.assertFalse(_event_is_empty(self._dir([], [{"id": 1}])))

    def test_unreadable_is_not_empty(self):
        self.assertFalse(_event_is_empty(self._dir([], None)))


if __name__ == "__main__":
    unittest.main()
