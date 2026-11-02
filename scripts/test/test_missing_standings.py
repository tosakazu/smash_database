"""An event with no standings connection at all must read as empty, not as a fetch failure.

start.gg answers `event { standings: null }` for an event the organizer created and never ran
(state CREATED) and for one that finished with no results entered (COMPLETED). There is no error
in the response, so fetch_all_nodes used to raise FetchError forever: the tournament never
reached done.csv and was re-downloaded every night. 213 North America events were in that state
on 2026-11-01 — 147 CREATED, 21 COMPLETED, 5 ACTIVE — blocking 97 tournaments.

A connection that disappears partway through pagination is still a fault: that one must raise,
or a truncated page would be read as "that was all of them".
"""
import unittest
from unittest import mock

from scripts.common import utils
from scripts.common.utils import FetchError, fetch_all_nodes


def _page(nodes, total, total_pages):
    return {"data": {"event": {"standings": {"nodes": nodes, "pageInfo": {"total": total, "totalPages": total_pages}}}}}


class MissingStandingsTests(unittest.TestCase):
    def test_null_connection_is_empty_when_allowed(self):
        with mock.patch.object(utils, "fetch_data_with_retries", return_value={"data": {"event": {"standings": None}}}):
            self.assertEqual(
                fetch_all_nodes("q", {"eventId": 1}, ["event", "standings"], per_page=4, missing_ok=True),
                [],
            )

    def test_null_connection_still_raises_by_default(self):
        with mock.patch.object(utils, "fetch_data_with_retries", return_value={"data": {"event": {"standings": None}}}):
            with self.assertRaises(FetchError):
                fetch_all_nodes("q", {"eventId": 1}, ["event", "standings"], per_page=4)

    def test_connection_vanishing_mid_pagination_raises_even_when_allowed(self):
        pages = [
            _page([{"placement": i} for i in range(1, 5)], 8, 2),
            {"data": {"event": {"standings": None}}},
        ]
        with mock.patch.object(utils, "fetch_data_with_retries", side_effect=pages):
            with self.assertRaises(FetchError):
                fetch_all_nodes("q", {"eventId": 1}, ["event", "standings"], per_page=4, missing_ok=True)

    def test_a_real_event_is_unaffected(self):
        pages = [
            _page([{"placement": i} for i in range(1, 5)], 6, 2),
            _page([{"placement": 5}, {"placement": 6}], 6, 2),
        ]
        with mock.patch.object(utils, "fetch_data_with_retries", side_effect=pages):
            got = fetch_all_nodes("q", {"eventId": 1}, ["event", "standings"], per_page=4, missing_ok=True)
        self.assertEqual([n["placement"] for n in got], [1, 2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
