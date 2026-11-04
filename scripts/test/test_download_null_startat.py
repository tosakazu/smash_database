"""A tournament listed without startAt is skipped instead of aborting the whole listing.

backfill-world 2026-11-03/04: Oceania 2019-04 / 2019-08 AU crashed in datetime.fromtimestamp(None) three times and
were given up, so the remaining tournaments of those months were never fetched.
"""
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from scripts.common import download


def _tour(tid, start):
    return {"id": tid, "name": f"T{tid}", "state": 3, "images": [], "startAt": start, "endAt": start,
            "countryCode": "AU", "city": None, "lat": None, "lng": None, "venueName": None, "timezone": None,
            "postalCode": None, "venueAddress": None, "mapsPlaceId": None, "url": f"/tournament/t{tid}"}


class NullStartAtTests(unittest.TestCase):
    def test_null_startat_is_skipped(self):
        d = tempfile.mkdtemp()
        done, users, tours = (os.path.join(d, n) for n in ("done.csv", "users.jsonl", "tournaments.jsonl"))
        for f in (done, users, tours):
            open(f, "w").close()
        start = datetime(2019, 9, 1)
        finish = datetime(2019, 8, 1)
        listing = [_tour(1, None)]
        with mock.patch.object(download, "fetch_latest_tournaments_by_game", return_value=(listing, 1)), \
                mock.patch.object(download, "fetch_event_ids_from_tournament") as events:
            download.download_all_tournaments(1386, "AU", start, finish, d, done, users, tours)
        events.assert_not_called()
        self.assertEqual(open(done).read(), "")


if __name__ == "__main__":
    unittest.main()
