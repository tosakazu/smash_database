"""challonge_classes: Challonge lower-class brackets → class_phases/<letter>_virtual (no network)."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts.common import challonge_classes as cc
from scripts.common import region

CLS = SimpleNamespace(CLASS_LETTERS=["B", "C", "D", "E"],
                      class_virtual_event_name=lambda name, letter: f"{name} / {letter}クラス")


def challonge_response(state="complete"):
    parts = [
        {"participant": {"id": 11, "name": "Alice (1a2b3c4d)", "misc": "startgg:1001", "final_rank": 1, "seed": 2}},
        {"participant": {"id": 12, "name": "Bob (5e6f7a8b)", "misc": "startgg:1002", "final_rank": 2, "seed": 1}},
        {"participant": {"id": 13, "name": "Carol", "misc": None, "final_rank": 3, "seed": 3}},
        {"participant": {"id": 14, "name": "Dave (99999999)", "misc": " startgg:1004 ", "final_rank": 3, "seed": 4}},
    ]
    matches = [
        {"match": {"id": 1, "state": "complete", "round": 1, "winner_id": 11, "loser_id": 14, "scores_csv": "2-0"}},
        {"match": {"id": 2, "state": "complete", "round": 1, "winner_id": 12, "loser_id": 13, "scores_csv": "2-1"}},
        {"match": {"id": 3, "state": "open", "round": 2, "winner_id": None, "loser_id": None, "scores_csv": ""}},
    ]
    return {"tournament": {"id": 555, "name": "篝火#15 Bクラス", "state": state, "tournament_type": "single elimination",
                           "full_challonge_url": "https://challonge.com/test555",
                           "participants": parts, "matches": matches}}


class ParseTests(unittest.TestCase):
    def test_misc(self):
        self.assertEqual(cc.startgg_user_id("startgg:42"), 42)
        self.assertEqual(cc.startgg_user_id(" startgg:42 "), 42)
        self.assertIsNone(cc.startgg_user_id(None))
        self.assertIsNone(cc.startgg_user_id("startgg:abc"))

    def test_standings_leave_out_untied(self):
        parsed = cc.parse_challonge(challonge_response())
        rows, left = cc.standings_from(parsed)
        self.assertEqual(rows, [{"placement": 1, "user_id": 1001}, {"placement": 2, "user_id": 1002},
                                {"placement": 3, "user_id": 1004}])
        self.assertEqual(left, ["Carol"])
        self.assertEqual(len(parsed["matches"]), 2)          # the open match is not kept
        self.assertEqual(parsed["matches"][0]["winner_user_id"], 1001)
        self.assertIsNone(parsed["matches"][1]["loser_user_id"])   # Carol has no start.gg id

    def test_attr_matches_start_gg_virtuals(self):
        parent = {"event_id": 462532, "event_name": "Singles", "tournament_name": "T", "timestamp": 100,
                  "end_timestamp": 200, "offline": True, "region": "Japan", "place": {"city": "x"}, "url": "/tournament/t"}
        a = cc.virtual_attr(parent, "C", CLS.CLASS_LETTERS, CLS.class_virtual_event_name, 4)
        self.assertEqual(a["event_id"], -4625322)
        self.assertEqual(a["timestamp"], 102)
        self.assertEqual(a["event_name"], "Singles / Cクラス")
        self.assertEqual(a["num_entrants"], 4)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)
        self.parent = "data/startgg/Japan/events/2026/10/01/T/Singles"
        os.makedirs(self.parent)
        with open(os.path.join(self.parent, "attr.json"), "w") as f:
            json.dump({"event_id": 777, "event_name": "Singles", "tournament_name": "T", "timestamp": 1000}, f)
        with open("data/startgg/Japan/tournaments.jsonl", "w") as f:
            f.write(json.dumps({"tournament_id": 1, "name": "T",
                                "events": [{"event_id": 777, "event_name": "Singles", "path": self.parent}]}) + "\n")
        with open("waitlist.json", "w") as f:
            json.dump({"ok": True, "items": [
                {"id": 5, "parent_event_id": 777, "class_letter": "B", "name": "T Bクラス", "challonge_id": 555,
                 "challonge_url": "https://challonge.com/test555"},
                {"id": 6, "parent_event_id": 888, "class_letter": "C", "name": "not downloaded", "challonge_id": 556,
                 "challonge_url": "https://challonge.com/test556"}]}, f)
        self.patches = [mock.patch.object(region, "load_region_classifier", lambda r: None),
                        mock.patch.object(region, "class_support", lambda clf: CLS),
                        mock.patch.dict(os.environ, {"CHALLONGE_API_KEY": "k"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.old)
        self.tmp.cleanup()

    def run_fetch(self, response):
        with mock.patch.object(cc, "fetch_challonge", lambda cid, key: response):
            return cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])

    def test_writes_the_virtual_event_when_complete(self):
        self.assertEqual(self.run_fetch(challonge_response()), 0)
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        attr = json.load(open(os.path.join(vdir, "attr.json")))
        self.assertEqual(attr["event_id"], -7771)
        self.assertEqual(json.load(open(os.path.join(vdir, "matches.json"))), [])
        self.assertEqual(len(json.load(open(os.path.join(vdir, "standings.json")))), 3)
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["spsp_class_id"], 5)
        self.assertEqual(open("done.txt").read(), "5\n")

    def test_in_progress_is_left_for_later(self):
        self.run_fetch(challonge_response(state="underway"))
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))
        self.assertEqual(open("done.txt").read(), "")

    def test_start_gg_class_bracket_wins(self):
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "attr.json"), "w") as f:
            f.write("{}")
        self.run_fetch(challonge_response())
        self.assertFalse(os.path.exists(os.path.join(vdir, "challonge.json")))
        self.assertEqual(open(os.path.join(vdir, "attr.json")).read(), "{}")

    def test_without_api_key_nothing_happens(self):
        with mock.patch.dict(os.environ, {"CHALLONGE_API_KEY": ""}):
            self.assertEqual(self.run_fetch(challonge_response()), 0)
        self.assertFalse(os.path.exists("done.txt"))


if __name__ == "__main__":
    unittest.main()
