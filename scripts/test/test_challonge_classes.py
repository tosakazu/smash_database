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
    """(tournament, participants, matches) in the Challonge API v2.1 shape (as checked against the real API)."""
    def part(pid, name, misc, rank, seed):
        return {"id": str(pid), "type": "participant",
                "attributes": {"name": name, "misc": misc, "final_rank": rank, "seed": seed}}
    parts = [part(11, "Alice (1a2b3c4d)", "startgg:1001", 1, 2), part(12, "Bob (5e6f7a8b)", "startgg:1002", 2, 1),
             part(13, "Carol", None, 3, 3), part(14, "Dave (99999999)", " startgg:1004 ", 3, 4)]
    def match(mid, st, winner, a, b):
        return {"id": str(mid), "type": "match",
                "attributes": {"state": st, "round": 1, "scores": "2 - 0", "winner_id": winner,
                               "points_by_participant": [{"participant_id": a, "scores": []},
                                                         {"participant_id": b, "scores": []}]}}
    matches = [match(1, "complete", 11, 11, 14), match(2, "complete", 12, 13, 12), match(3, "open", None, 11, 12)]
    tour = {"data": {"id": "555", "type": "tournament",
                     "attributes": {"name": "篝火#15 Bクラス", "state": state, "tournament_type": "single elimination",
                                    "full_challonge_url": "https://challonge.com/ja/test555"}}}
    return tour, parts, matches


def parsed_response(state="complete"):
    return cc.parse_challonge(*challonge_response(state))


class ParseTests(unittest.TestCase):
    def test_misc(self):
        self.assertEqual(cc.startgg_user_id("startgg:42"), 42)
        self.assertEqual(cc.startgg_user_id(" startgg:42 "), 42)
        self.assertIsNone(cc.startgg_user_id(None))
        self.assertIsNone(cc.startgg_user_id("startgg:abc"))

    def test_standings_leave_out_untied(self):
        parsed = parsed_response()
        rows, left, dups = cc.standings_from(parsed)
        self.assertEqual(dups, [])
        self.assertEqual(rows, [{"placement": 1, "user_id": 1001}, {"placement": 2, "user_id": 1002},
                                {"placement": 3, "user_id": 1004}])
        self.assertEqual(left, ["Carol"])
        self.assertEqual(len(parsed["matches"]), 2)          # the open match is not kept
        self.assertEqual((parsed["matches"][0]["winner_user_id"], parsed["matches"][0]["loser_user_id"]), (1001, 1004))
        self.assertEqual(parsed["matches"][1]["winner_user_id"], 1002)
        self.assertIsNone(parsed["matches"][1]["loser_user_id"])   # Carol has no start.gg id
        self.assertEqual((parsed["id"], parsed["state"]), (555, "complete"))

    def test_duplicate_misc_keeps_the_better_placement(self):
        tour, parts, matches = challonge_response()
        parts[3]["attributes"]["misc"] = "startgg:1001"      # Dave carries Alice's id too (rank 3)
        rows, left, dups = cc.standings_from(cc.parse_challonge(tour, parts, matches))
        self.assertEqual(rows, [{"placement": 1, "user_id": 1001}, {"placement": 2, "user_id": 1002}])
        self.assertEqual(dups, [1001])

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
                        mock.patch.object(cc, "challonge_token", lambda cid, sec: "t"),
                        mock.patch.dict(os.environ, {"CHALLONGE_CLIENT_ID": "i", "CHALLONGE_CLIENT_SECRET": "s"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.old)
        self.tmp.cleanup()

    def run_fetch(self, response):
        with mock.patch.object(cc, "fetch_challonge", lambda cid, token: response):
            return cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])

    def test_writes_the_virtual_event_when_complete(self):
        self.assertEqual(self.run_fetch(parsed_response()), 0)
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        attr = json.load(open(os.path.join(vdir, "attr.json")))
        self.assertEqual(attr["event_id"], -7771)
        self.assertEqual(json.load(open(os.path.join(vdir, "matches.json"))), [])
        self.assertEqual(len(json.load(open(os.path.join(vdir, "standings.json")))), 3)
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["spsp_class_id"], 5)
        self.assertEqual(open("done.txt").read(), "5\n")

    def test_in_progress_is_left_for_later(self):
        self.run_fetch(parsed_response(state="underway"))
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))
        self.assertEqual(open("done.txt").read(), "")

    def test_start_gg_class_bracket_wins(self):
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "attr.json"), "w") as f:
            f.write("{}")
        self.run_fetch(parsed_response())
        self.assertFalse(os.path.exists(os.path.join(vdir, "challonge.json")))
        self.assertEqual(open(os.path.join(vdir, "attr.json")).read(), "{}")

    def test_challonge_failures_skip_only_that_bracket(self):
        import requests
        for exc in (requests.Timeout("t"), requests.HTTPError("503"), KeyError("data")):
            def boom(cid, token, exc=exc):
                raise exc
            with mock.patch.object(cc, "fetch_challonge", boom):
                rc = cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])
            self.assertEqual(rc, 0)
            self.assertEqual(open("done.txt").read(), "")
            self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))

    def test_without_api_key_nothing_happens(self):
        with mock.patch.dict(os.environ, {"CHALLONGE_CLIENT_SECRET": ""}):
            self.assertEqual(self.run_fetch(parsed_response()), 0)
        self.assertFalse(os.path.exists("done.txt"))


    def run_mark_done(self, status_by_id):
        class Resp:
            ok = True
            status_code = 200
            def __init__(self, body): self.body = body
            def json(self): return self.body
        def post(url, data, headers, timeout):
            i = json.loads(data)["id"]
            return Resp({"ok": True, "id": i, "status": status_by_id[i]})
        with mock.patch.dict(os.environ, {"SPSP_CLASS_DONE_KEY": "k"}), mock.patch.object(cc.requests, "post", post):
            return cc.main(["mark-done", "--region", "Japan", "--done-in", "done.txt"])

    def test_mark_done_keeps_the_data(self):
        self.run_fetch(parsed_response())
        self.assertEqual(self.run_mark_done({5: "done"}), 0)
        self.assertTrue(os.path.exists(os.path.join(self.parent, "class_phases", "B_virtual", "attr.json")))

    def test_deleted_meanwhile_removes_what_was_ingested(self):
        self.run_fetch(parsed_response())
        self.assertEqual(self.run_mark_done({5: "deleted"}), 0)
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases", "B_virtual")))


if __name__ == "__main__":
    unittest.main()
