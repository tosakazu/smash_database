"""challonge_classes: Challonge lower-class brackets → class_phases/<letter>_virtual (no network)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts.common import challonge_classes as cc
from scripts.common import region

CLS = SimpleNamespace(CLASS_LETTERS=["B", "C", "D", "E"],
                      class_virtual_event_name=lambda name, letter: f"{name} / {letter}クラス",
                      class_letter=lambda name: name[0] if name[1:].startswith("クラス") else None)


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
                               "points_by_participant": [{"participant_id": a, "scores": [2 if a == winner else 0]},
                                                         {"participant_id": b, "scores": [2 if b == winner else 0]}]}}
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

    def test_class_matches_have_the_start_gg_row_keys(self):
        rows = cc.class_matches_from(parsed_response(), "B")
        startgg_keys = {"match_id", "winner_id", "loser_id", "winner_score", "loser_score", "round_text", "round", "phase",
                        "phase_id", "phase_name", "phase_order", "phase_num_seeds", "phase_bracket_type", "phase_top_n",
                        "bracket_label", "winners_top", "losers_top", "global_round", "global_top_x",
                        "global_bracket_label", "phase_group_id", "phase_group_start_at", "wave_id", "wave",
                        "wave_start_at", "dq", "cancel", "state", "started_at", "completed_at", "details"}
        self.assertEqual(set(rows[0]) - {"source"}, startgg_keys)
        self.assertEqual(rows[0]["source"], "challonge")

    def test_bracket_labels_follow_the_start_gg_shape(self):
        # the real 6-player double elimination checked on Challonge: winners 1..3, GF + reset 4, losers -1..-3
        def m(mid, r): return {"challonge_id": mid, "round": r}
        parsed = {"participants": [{}] * 6, "tournament_type": "double elimination",
                  "matches": [m(1, 1), m(2, 2), m(3, 3), m(4, 4), m(5, 4), m(6, -1), m(7, -2), m(8, -3)]}
        lab = cc._bracket_labels(parsed, "C")
        self.assertEqual(lab[1], (None, 1, 8, "C-Winners TOP 8"))
        self.assertEqual(lab[3], (None, 3, 2, "C-Winners TOP 2"))
        self.assertEqual(lab[4], ("Grand Final", 4, 2, "C-Winners TOP 2"))
        self.assertEqual(lab[5], ("Grand Final Reset", 4, 2, "C-Winners TOP 2"))
        self.assertEqual([lab[i][2] for i in (6, 7, 8)], [8, 6, 4])      # losers side: TOP X shrinks round by round
        self.assertTrue(all(lab[i][3].startswith("C-Losers TOP") for i in (6, 7, 8)))

    def test_nocount_misc(self):
        self.assertEqual(cc.startgg_user_id("startgg:42:nocount"), 42)
        self.assertTrue(cc.is_nocount("startgg:42:nocount"))
        self.assertTrue(cc.is_nocount(" startgg:42:nocount "))
        self.assertFalse(cc.is_nocount("startgg:42"))
        self.assertFalse(cc.is_nocount(None))
        self.assertIsNone(cc.startgg_user_id("startgg:42:other"))   # unknown suffix: not tied (left out with a warning)

    def nocount_response(self):
        """Dave (1004, rank 3) was added by the TO without playing the main event (:nocount). Sets: Alice beat Dave,
        Bob beat Carol, and (added here) Dave beat Carol in the losers bracket."""
        tour, parts, matches = challonge_response()
        parts[3]["attributes"]["misc"] = "startgg:1004:nocount"
        parts[2]["attributes"]["misc"] = "startgg:1003"            # Carol is a regular player here
        matches.append({"id": "4", "type": "match", "attributes": {
            "state": "complete", "round": -1, "scores": "2 - 1", "winner_id": 14,
            "points_by_participant": [{"participant_id": 14, "scores": [2]}, {"participant_id": 13, "scores": [1]}]}})
        return cc.parse_challonge(tour, parts, matches)

    def test_nocount_players_are_left_out_of_spsp(self):
        parsed = self.nocount_response()
        self.assertEqual(parsed["nocount_uids"], [1004])
        rows, left, dups = cc.standings_from(parsed)
        # Dave is not in the standings; the others keep Challonge's placements (no re-ranking)
        self.assertEqual(rows, [{"placement": 1, "user_id": 1001}, {"placement": 2, "user_id": 1002},
                                {"placement": 3, "user_id": 1003}])
        self.assertEqual(left, [])                                 # not a "left out with a warning" case
        cm = cc.class_matches_from(parsed, "B")
        # Alice beat Dave and Dave beat Carol: both sets involve Dave, so neither side learns them
        self.assertEqual([(r["winner_id"], r["loser_id"]) for r in cm], [(1002, 1003)])

    def test_dq_and_forfeit_like_start_gg(self):
        """A real Challonge bracket (fixtures/challonge_dq_v21.json): DQ / forfeit sets carry dq: true, as start.gg DQs do."""
        fx = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "challonge_dq_v21.json")))
        parsed = cc.parse_challonge(fx["tournament"], fx["participants"], fx["matches"])
        by_id = {m["challonge_id"]: m for m in parsed["matches"]}
        ident = {int(m["id"]): m["attributes"]["identifier"] for m in fx["matches"]}
        dq = {ident[i]: m["dq"] for i, m in by_id.items()}
        self.assertEqual(dq["A"], True)     # "0 - -1": a negative score (start.gg's DQ is -1 too)
        self.assertEqual(dq["B"], True)     # "0 - 0"
        self.assertEqual(dq["D"], True)     # forfeit: participant removed after the start, no scores
        self.assertEqual(dq["C"], False)    # a normal 2-1
        self.assertFalse(any(v for k, v in dq.items() if k not in ("A", "B", "D")))
        rows = {r["match_id"]: r for r in cc.class_matches_from(parsed, "B")}
        self.assertEqual(sum(1 for r in rows.values() if r["dq"]), 3)
        a = next(r for i, r in rows.items() if ident[i] == "A")
        self.assertEqual((a["winner_score"], a["loser_score"]), (0, -1))
        removed = next(p for p in parsed["participants"] if not p["active"])
        self.assertEqual(removed["final_rank"], 5)  # Challonge still ranks the forfeiting player

    def test_dq_rules(self):
        def one(w_scores, l_scores, loser_active=True):
            tour, parts, matches = challonge_response()
            parts[3]["attributes"]["states"] = {"active": loser_active}
            matches[0]["attributes"]["points_by_participant"] = [{"participant_id": 11, "scores": w_scores},
                                                                 {"participant_id": 14, "scores": l_scores}]
            return cc.parse_challonge(tour, parts, matches)["matches"][0]["dq"]
        self.assertFalse(one([2], [1]))
        self.assertFalse(one([2], [0]))
        self.assertTrue(one([0], [-1]))
        self.assertTrue(one([-1], [0]))
        self.assertTrue(one([0], [0]))
        self.assertTrue(one([], []))
        self.assertTrue(one([2], [1], loser_active=False))

    # ── awaiting_review: every set reported, not finalized (no final_rank) → rank from the bracket ──
    @staticmethod
    def bracket(ttype, sets, n):
        """sets = [(round, winner pid, loser pid)], participants 1..n."""
        return {"tournament_type": ttype, "participants": [{"challonge_id": i} for i in range(1, n + 1)],
                "matches": [{"challonge_id": 100 + k, "round": r, "winner_pid": w, "loser_pid": l}
                            for k, (r, w, l) in enumerate(sets)]}

    def test_rank_single_elimination(self):
        # 6 players (byes for 1 and 2): R1 3>6, 4>5; R2 1>4, 2>3; final 1>2
        b = self.bracket("single elimination", [(1, 3, 6), (1, 4, 5), (2, 1, 4), (2, 2, 3), (3, 1, 2)], 6)
        self.assertEqual(cc.placements_from_bracket(b), {1: 1, 2: 2, 3: 3, 4: 3, 5: 5, 6: 5})

    def test_rank_single_elimination_third_place_match(self):
        b = self.bracket("single elimination", [(1, 1, 4), (1, 2, 3), (2, 1, 2), (0, 4, 3)], 4)
        self.assertEqual(cc.placements_from_bracket(b), {1: 1, 2: 2, 4: 3, 3: 4})

    def test_rank_double_elimination_with_reset(self):
        # 4 players: W1 1>4, 2>3; W2 1>2; L1 3>4; L2 3>2; GF 3>1, reset 1>3
        b = self.bracket("double elimination",
                         [(1, 1, 4), (1, 2, 3), (2, 1, 2), (-1, 3, 4), (-2, 3, 2), (3, 3, 1), (3, 1, 3)], 4)
        self.assertEqual(cc.placements_from_bracket(b), {1: 1, 3: 2, 2: 3, 4: 4})

    def test_rank_refuses_what_it_cannot_rank(self):
        with self.assertRaises(ValueError):
            cc.placements_from_bracket(self.bracket("round robin", [(1, 1, 2)], 2))
        with self.assertRaises(ValueError):          # player 3 has no result
            cc.placements_from_bracket(self.bracket("single elimination", [(1, 1, 2)], 3))

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
        with open(os.path.join(self.parent, "standings.json"), "w") as f:     # the main event's entrants
            json.dump({"data": [{"placement": i, "user_id": u} for i, u in enumerate([1001, 1002, 1003, 1004], 1)]}, f)
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
                        # the parent event of these tests is from 1970: without this every bracket would be closed at once
                        mock.patch.object(cc, "close_due", lambda attr, now: False),
                        mock.patch.dict(os.environ, {"CHALLONGE_CLIENT_ID": "i", "CHALLONGE_CLIENT_SECRET": "s"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.old)
        self.tmp.cleanup()

    def run_fetch(self, response):
        with mock.patch.object(cc, "fetch_challonge", lambda cid, token, full=False: response):
            return cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])

    def test_writes_the_virtual_event_when_complete(self):
        self.assertEqual(self.run_fetch(parsed_response()), 0)
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        attr = json.load(open(os.path.join(vdir, "attr.json")))
        self.assertEqual(attr["event_id"], -7771)
        self.assertEqual(json.load(open(os.path.join(vdir, "matches.json"))), [])
        self.assertEqual(len(json.load(open(os.path.join(vdir, "standings.json")))), 3)
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["spsp_class_id"], 5)
        cm = json.load(open(os.path.join(vdir, "class_matches.json")))
        self.assertEqual(cm["replaces_phase_group_ids"], [])
        self.assertEqual([(r["winner_id"], r["loser_id"], r["state"], r["phase_bracket_type"]) for r in cm["data"]],
                         [(1001, 1004, 3, "SINGLE_ELIMINATION")])     # Carol's set (no start.gg id) is left out
        self.assertEqual(open("done.txt").read(), "5\n")

    def test_nocount_written_bracket(self):
        tour, parts, matches = challonge_response()
        parts[3]["attributes"]["misc"] = "startgg:1004:nocount"
        self.run_fetch(cc.parse_challonge(tour, parts, matches))
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        self.assertEqual(json.load(open(os.path.join(vdir, "attr.json")))["num_entrants"], 3)   # Dave not counted
        self.assertNotIn(1004, [r["user_id"] for r in json.load(open(os.path.join(vdir, "standings.json")))])
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["nocount_uids"], [1004])
        cm = json.load(open(os.path.join(vdir, "class_matches.json")))["data"]
        self.assertFalse(any(1004 in (r["winner_id"], r["loser_id"]) for r in cm))

    def awaiting(self, incomplete=0):
        tour, parts, matches = challonge_response()
        tour["data"]["attributes"]["state"] = "awaiting_review"
        for p in parts:
            p["attributes"]["final_rank"] = None
        matches = [m for m in matches if m["attributes"]["state"] == "complete"]
        # a full 4-player bracket: R1 Alice>Dave, Bob>Carol; final Alice>Bob
        matches.append({"id": "9", "type": "match", "attributes": {"state": "complete", "round": 2, "scores": "2 - 1",
            "winner_id": 11, "points_by_participant": [{"participant_id": 11, "scores": [2]}, {"participant_id": 12, "scores": [1]}]}})
        parsed = cc.parse_challonge(tour, parts, matches)
        parsed["n_incomplete"] = incomplete
        return parsed

    def test_awaiting_review_is_ranked_from_the_bracket(self):
        self.run_fetch(self.awaiting())
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        st = json.load(open(os.path.join(vdir, "standings.json")))
        self.assertEqual(st, [{"placement": 1, "user_id": 1001}, {"placement": 2, "user_id": 1002},
                              {"placement": 3, "user_id": 1004}])           # Carol has no start.gg id
        self.assertTrue(json.load(open(os.path.join(vdir, "challonge.json")))["placements_computed"])
        self.assertEqual(open("done.txt").read(), "5\n")

    def test_awaiting_review_with_sets_left_waits(self):
        self.run_fetch(self.awaiting(incomplete=1))
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))
        self.assertEqual(open("done.txt").read(), "")

    def test_awaiting_review_that_cannot_be_ranked_is_skipped_not_fatal(self):
        parsed = self.awaiting()
        parsed["tournament_type"] = "swiss"
        self.assertEqual(self.run_fetch(parsed), 0)
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))
        self.assertEqual(open("done.txt").read(), "")

    def test_participants_who_did_not_enter_the_main_event_are_left_out(self):
        tour, parts, matches = challonge_response()
        parts[3]["attributes"]["misc"] = "startgg:5555"          # Dave: a start.gg player who was not in the main event
        self.run_fetch(cc.parse_challonge(tour, parts, matches))
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        st = json.load(open(os.path.join(vdir, "standings.json")))
        self.assertEqual([r["user_id"] for r in st], [1001, 1002])
        cm = json.load(open(os.path.join(vdir, "class_matches.json")))["data"]
        self.assertFalse(any(5555 in (r["winner_id"], r["loser_id"]) for r in cm))   # Alice beat Dave: not learned
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["not_in_main_uids"], [5555])
        self.assertEqual(json.load(open(os.path.join(vdir, "attr.json")))["num_entrants"], 3)

    def test_seeds_also_count_as_entrants(self):
        with open(os.path.join(self.parent, "seeds.json"), "w") as f:
            json.dump([{"seed": 9, "user_id": 5555}], f)
        tour, parts, matches = challonge_response()
        parts[3]["attributes"]["misc"] = "startgg:5555"
        self.run_fetch(cc.parse_challonge(tour, parts, matches))
        st = json.load(open(os.path.join(self.parent, "class_phases", "B_virtual", "standings.json")))
        self.assertIn(5555, [r["user_id"] for r in st])

    def test_in_progress_is_left_for_later(self):
        self.run_fetch(parsed_response(state="underway"))
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))
        self.assertEqual(open("done.txt").read(), "")

    # ── one class bracket per (main event, class): the one that progressed ──
    def startgg_class(self, sets, virtual=True):
        """A start.gg B class inside the parent: phase 'Bクラス' (phase group 900) with `sets` completed sets."""
        with open(os.path.join(self.parent, "phases.json"), "w") as f:
            json.dump({"phases": [{"name": "本戦", "is_class": False, "phase_groups": [{"id": 800}]},
                                  {"name": "Bクラス", "is_class": True, "phase_groups": [{"id": 900}]}]}, f)
        rows = [{"state": 3, "phase_group_id": 800, "winner_id": 1, "loser_id": 2}] + \
               [{"state": 3, "phase_group_id": 900, "winner_id": 3, "loser_id": 4} for _ in range(sets)]
        with open(os.path.join(self.parent, "matches.json"), "w") as f:
            json.dump({"data": rows}, f)
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        if virtual:
            os.makedirs(vdir)
            with open(os.path.join(vdir, "attr.json"), "w") as f:
                f.write('{"startgg": true}')
        return vdir

    def test_start_gg_with_more_progress_stays_and_challonge_is_excluded(self):
        vdir = self.startgg_class(sets=5)                     # Challonge has 2 completed sets
        self.run_fetch(parsed_response())
        self.assertFalse(os.path.exists(os.path.join(vdir, "challonge.json")))
        self.assertEqual(open(os.path.join(vdir, "attr.json")).read(), '{"startgg": true}')
        self.assertEqual(open("done.txt").read(), "5\n")      # marked done (excluded) so it leaves the waitlist

    def test_empty_start_gg_class_is_replaced(self):
        vdir = self.startgg_class(sets=0)
        self.run_fetch(parsed_response())
        self.assertTrue(os.path.exists(os.path.join(vdir, "challonge.json")))
        cm = json.load(open(os.path.join(vdir, "class_matches.json")))
        self.assertEqual(cm["replaces_phase_group_ids"], [900])   # listed even while empty: later sets are not learned twice
        self.assertEqual(open("done.txt").read(), "5\n")

    def test_start_gg_with_less_progress_is_replaced(self):
        vdir = self.startgg_class(sets=1, virtual=False)      # start.gg class with 1 set, virtual not built yet
        self.run_fetch(parsed_response())
        cm = json.load(open(os.path.join(vdir, "class_matches.json")))
        self.assertEqual(cm["replaces_phase_group_ids"], [900])
        self.assertEqual(len(cm["data"]), 1)

    def test_tie_keeps_what_is_there(self):
        vdir = self.startgg_class(sets=2)                     # same number of completed sets as the Challonge one
        self.run_fetch(parsed_response())
        self.assertFalse(os.path.exists(os.path.join(vdir, "challonge.json")))
        self.assertEqual(open("done.txt").read(), "5\n")

    def test_two_challonge_brackets_for_one_class_take_the_further_one(self):
        with open("waitlist.json", "w") as f:
            json.dump({"ok": True, "items": [
                {"id": 5, "parent_event_id": 777, "class_letter": "B", "name": "a", "challonge_id": 555, "challonge_url": "u"},
                {"id": 7, "parent_event_id": 777, "class_letter": "B", "name": "b", "challonge_id": 557, "challonge_url": "u"}]}, f)
        big = parsed_response()
        small = parsed_response(); small["matches"] = small["matches"][:1]; small["id"] = 557
        with mock.patch.object(cc, "fetch_challonge", lambda cid, token, full=False: big if cid == 557 else small):
            cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])
        vdir = os.path.join(self.parent, "class_phases", "B_virtual")
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["spsp_class_id"], 7)
        self.assertEqual(sorted(open("done.txt").read().split()), ["5", "7"])   # both leave the waitlist

    def test_decided_once_does_not_flip(self):
        vdir = self.startgg_class(sets=0)
        self.run_fetch(parsed_response())                     # Challonge #5 replaces the empty start.gg class
        first = {f: open(os.path.join(vdir, f)).read() for f in sorted(os.listdir(vdir))}
        with open(os.path.join(self.parent, "matches.json"), "w") as f:
            json.dump({"data": [{"state": 3, "phase_group_id": 900, "winner_id": 3, "loser_id": 4}] * 9}, f)   # start.gg fills up later (a re-fetch)
        with open("waitlist.json", "w") as f:                 # ... and #5 is done, so the waitlist no longer has it
            json.dump({"ok": True, "items": []}, f)
        self.run_fetch(parsed_response())
        self.assertEqual({f: open(os.path.join(vdir, f)).read() for f in sorted(os.listdir(vdir))}, first)
        # a later Challonge bracket for the same class with no more sets than the ingested one is excluded
        with open("waitlist.json", "w") as f:
            json.dump({"ok": True, "items": [
                {"id": 8, "parent_event_id": 777, "class_letter": "B", "name": "c", "challonge_id": 558, "challonge_url": "u"}]}, f)
        self.run_fetch(parsed_response())
        self.assertEqual(json.load(open(os.path.join(vdir, "challonge.json")))["spsp_class_id"], 5)
        self.assertEqual(open("done.txt").read(), "8\n")

    def test_deleted_on_challonge_is_excluded(self):
        import requests
        resp = mock.Mock(status_code=404)
        def gone(cid, token, full=False):
            raise requests.HTTPError("404", response=resp)
        with mock.patch.object(cc, "fetch_challonge", gone):
            cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])
        self.assertEqual(open("done.txt").read(), "5\n")
        self.assertFalse(os.path.exists(os.path.join(self.parent, "class_phases")))

    def test_rate_limited_stops_asking_in_this_run(self):
        import requests
        with open(os.path.join(self.parent, "attr.json")) as f:
            attr = json.load(f)
        with open("data/startgg/Japan/tournaments.jsonl", "w") as f:
            f.write(json.dumps({"tournament_id": 1, "name": "T", "events": [
                {"event_id": 777, "event_name": "Singles", "path": self.parent}]}) + "\n")
        with open("waitlist.json", "w") as f:
            json.dump({"ok": True, "items": [
                {"id": n, "parent_event_id": 777, "class_letter": "B", "name": "T Bクラス", "challonge_id": 500 + n,
                 "challonge_url": "https://challonge.com/x"} for n in (5, 6, 7)]}, f)
        self.assertEqual(attr["event_id"], 777)
        asked = []
        def limited(cid, token, full=False):
            asked.append(cid)
            raise requests.HTTPError("429", response=mock.Mock(status_code=429, headers={"Retry-After": "3600", "Set-Cookie": "x"}))
        out = io.StringIO()
        with mock.patch.object(cc, "fetch_challonge", limited), contextlib.redirect_stdout(out):
            rc = cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt"])
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [505])                      # the 2nd and 3rd brackets are not asked
        self.assertIn("HTTP 429; Retry-After=3600", out.getvalue())
        self.assertNotIn("Set-Cookie", out.getvalue())
        self.assertEqual(open("done.txt").read(), "")

    def test_challonge_failures_skip_only_that_bracket(self):
        import requests
        for exc in (requests.Timeout("t"), requests.HTTPError("503"), KeyError("data")):
            def boom(cid, token, full=False, exc=exc):
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


class RequestSavingTests(unittest.TestCase):
    """The Challonge API allows few requests: a bracket that is not ready costs one request, an old one is asked rarely."""

    def fetch(self, state):
        calls = []
        def get(url, **kw):
            calls.append(url.split("/v2.1")[1])
            body = ({"data": {"id": "9", "type": "tournament", "attributes": {"state": state, "name": "n"}}}
                    if url.endswith("/tournaments/9.json") else {"data": []})
            return mock.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None)
        with mock.patch.object(cc.requests, "get", get):
            parsed = cc.fetch_challonge(9, "t")
        return parsed, calls

    def test_not_ready_reads_the_tournament_only(self):
        for state in ("pending", "underway", "group_stages_underway"):
            parsed, calls = self.fetch(state)
            self.assertEqual(calls, ["/application/tournaments/9.json"])
            self.assertEqual(parsed["state"], state)
            self.assertEqual(parsed["matches"], [])

    def test_ready_reads_participants_and_matches(self):
        for state in ("complete", "awaiting_review"):
            _, calls = self.fetch(state)
            self.assertEqual(calls, ["/application/tournaments/9.json", "/application/tournaments/9/participants.json",
                                     "/application/tournaments/9/matches.json"])

    def test_closing_reads_everything_whatever_the_state(self):
        calls = []
        def get(url, **kw):
            calls.append(url.split("/v2.1")[1])
            body = ({"data": {"id": "9", "type": "tournament", "attributes": {"state": "underway", "name": "n"}}}
                    if url.endswith("/tournaments/9.json") else {"data": []})
            return mock.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None)
        with mock.patch.object(cc.requests, "get", get):
            cc.fetch_challonge(9, "t", full=True)
        self.assertEqual(len(calls), 3)

    def test_close_due(self):
        end = 1_800_000_000
        attr = {"timestamp": end - 6 * 3600, "end_timestamp": end}
        self.assertFalse(cc.close_due(attr, end + 5 * 3600))
        self.assertTrue(cc.close_due(attr, end + 6 * 3600))
        self.assertTrue(cc.close_due({"timestamp": end}, end + 7 * 3600))      # no end time: from the start
        self.assertFalse(cc.close_due({}, end))                                # no date known: never closed

    def test_placements_unfinished(self):
        b = ParseTests.bracket
        # single elimination, 6 players, final not played: 1 and 2 still in
        self.assertEqual(cc.placements_unfinished(b("single elimination", [(1, 3, 6), (1, 4, 5), (2, 1, 4), (2, 2, 3)], 6)),
                         {3: 3, 4: 3, 5: 5, 6: 5})
        # played unevenly (one semifinal missing): the players out are never placed better than they can end
        self.assertEqual(cc.placements_unfinished(b("single elimination", [(1, 3, 6), (1, 4, 5), (2, 1, 4)], 6)),
                         {4: 4, 5: 5, 6: 5})
        # double elimination, 4 players, grand final not played: 4 and 2 are out (two losses), 1 and 3 still in
        self.assertEqual(cc.placements_unfinished(
            b("double elimination", [(1, 1, 4), (1, 2, 3), (2, 1, 2), (-1, 3, 4), (-2, 3, 2)], 4)), {2: 3, 4: 4})
        self.assertEqual(cc.placements_unfinished(b("single elimination", [], 4)), {})
        with self.assertRaises(ValueError):
            cc.placements_unfinished(b("round robin", [(1, 1, 2)], 3))

    def run_fetch(self, parsed_by_full, hours_after_end):
        """One waiting bracket (#3, parent event ended at `end`); returns (asked [full flags], done ids, output, challonge.json)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd(); os.chdir(tmp)
            try:
                parent = "data/startgg/Japan/events/2026/10/20/T/Singles"
                os.makedirs(parent)
                end = 1_800_000_000
                with open(os.path.join(parent, "attr.json"), "w") as f:
                    json.dump({"event_id": 777, "event_name": "Singles", "tournament_name": "T", "timestamp": end - 3600,
                               "end_timestamp": end}, f)
                with open(os.path.join(parent, "standings.json"), "w") as f:
                    json.dump({"data": [{"placement": i, "user_id": 1000 + i} for i in range(1, 5)]}, f)
                with open("data/startgg/Japan/tournaments.jsonl", "w") as f:
                    f.write(json.dumps({"tournament_id": 1, "name": "T",
                                        "events": [{"event_id": 777, "event_name": "Singles", "path": parent}]}) + "\n")
                with open("waitlist.json", "w") as f:
                    json.dump({"ok": True, "items": [{"id": 3, "parent_event_id": 777, "class_letter": "B", "name": "T B",
                                                      "challonge_id": 555, "challonge_url": "https://challonge.com/x"}]}, f)
                asked = []
                def fake(cid, token, full=False):
                    asked.append(full)
                    return json.loads(json.dumps(parsed_by_full[full]))
                out = io.StringIO()
                with mock.patch.object(region, "load_region_classifier", lambda r: None), \
                        mock.patch.object(region, "class_support", lambda clf: CLS), \
                        mock.patch.object(cc, "challonge_token", lambda cid, sec: "t"), \
                        mock.patch.object(cc, "fetch_challonge", fake), \
                        mock.patch.dict(os.environ, {"CHALLONGE_CLIENT_ID": "i", "CHALLONGE_CLIENT_SECRET": "s"}), \
                        contextlib.redirect_stdout(out):
                    cc.main(["fetch", "--region", "Japan", "--waitlist-file", "waitlist.json", "--done-out", "done.txt",
                             "--now", str(end + hours_after_end * 3600)])
                done = open("done.txt").read().split()
                src = os.path.join(parent, "class_phases", "B_virtual", "challonge.json")
                return asked, done, out.getvalue(), (json.load(open(src)) if os.path.exists(src) else None)
            finally:
                os.chdir(old)

    @staticmethod
    def underway(sets):
        """4 players (start.gg users 1001..1004), single elimination, state underway."""
        parts = [{"challonge_id": i, "name": f"p{i}", "misc": f"startgg:{1000 + i}", "user_id": 1000 + i, "nocount": False,
                  "final_rank": None, "seed": i, "active": True} for i in range(1, 5)]
        ms = [{"challonge_id": 100 + k, "round": r, "scores": "2-0", "winner_user_id": 1000 + w, "loser_user_id": 1000 + l,
               "winner_score": 2, "loser_score": 0, "dq": False, "winner_pid": w, "loser_pid": l, "involves_nocount": False}
              for k, (r, w, l) in enumerate(sets)]
        return {"id": 555, "url": "u", "name": "n", "state": "underway", "tournament_type": "single elimination",
                "participants": parts, "matches": ms, "n_incomplete": 3 - len(ms), "nocount_uids": []}

    def test_unfinished_bracket_waits_until_the_main_event_is_hours_old(self):
        light = {"id": 555, "state": "underway", "participants": [], "matches": [], "n_incomplete": 0}
        asked, done, out, src = self.run_fetch({False: light}, 3)
        self.assertEqual((asked, done, src), ([False], [], None))
        self.assertIn("wait #3", out)

    def test_unfinished_bracket_is_closed_as_it_stands(self):
        # the final was never reported: both semifinal losers are 3rd, the finalists get no placement, the sets are kept
        asked, done, out, src = self.run_fetch({True: self.underway([(1, 1, 4), (1, 2, 3)])}, 7)
        self.assertEqual((asked, done), ([True], ["3"]))
        self.assertTrue(src["closed_unfinished"])
        self.assertEqual({p["user_id"]: p["final_rank"] for p in src["participants"]}, {1001: None, 1002: None, 1003: 3, 1004: 3})
        self.assertEqual(len(src["matches"]), 2)
        self.assertIn("taken as it stands", out)

    def test_bracket_never_played_is_dropped_when_closed(self):
        asked, done, out, src = self.run_fetch({True: self.underway([])}, 7)
        self.assertEqual((asked, done, src), ([True], ["3"], None))
        self.assertIn("EXCLUDE #3", out)


if __name__ == "__main__":
    unittest.main()
