"""download_standings / download_seeds must skip entrants whose participants list is null or empty.

start.gg returns both shapes for deleted entrants: participants: None (2026-09-13 nightly, Japan) and
participants: [] (2026-09-22, US 2025-12). Either used to crash on participants[0].
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from scripts.common import download


def _entrant(eid, uid):
    return {"id": eid, "participants": [{"user": {"id": uid}, "player": {"id": uid, "gamerTag": f"p{uid}"}}]}


class EmptyParticipantsTests(unittest.TestCase):
    def test_standings_skip_null_and_empty_participants(self):
        nodes = [
            {"placement": 1, "entrant": _entrant(10, 100)},
            {"placement": 2, "entrant": {"id": 11, "participants": []}},
            {"placement": 3, "entrant": {"id": 12, "participants": None}},
            {"placement": 4, "entrant": None},
            {"placement": 5, "entrant": _entrant(13, 103)},
        ]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(download, "fetch_all_nodes", return_value=nodes):
            users, players, e2u = download.download_standings(1, d)
            self.assertEqual([u["id"] for u in users], [100, 103])
            self.assertEqual(e2u, {10: 100, 13: 103})
            rows = json.load(open(os.path.join(d, "standings.json")))["data"]
            self.assertEqual([(r["placement"], r["user_id"]) for r in rows], [(1, 100), (2, None), (5, 103)])

    def test_seeds_skip_null_and_empty_participants(self):
        seeds = [
            {"seedNum": 1, "entrant": _entrant(10, 100)},
            {"seedNum": 2, "entrant": {"id": 11, "participants": []}},
            {"seedNum": 3, "entrant": None},
        ]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(download, "fetch_phase_id", return_value=5), \
                mock.patch.object(download, "fetch_all_nodes", return_value=seeds):
            users, players, e2u = [], [], {}
            download.download_seeds(1, users, players, e2u, d)
            rows = json.load(open(os.path.join(d, "seeds.json")))["data"]
            self.assertEqual([(r["seed_num"], r["user_id"]) for r in rows], [(1, 100), (2, None)])
            self.assertEqual(e2u, {10: 100})


if __name__ == "__main__":
    unittest.main()
