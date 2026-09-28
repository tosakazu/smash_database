"""scripts/common/overseas.py: who counts as overseas (country outside the region, hand-kept lists, residents)."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.common import overseas

CLF = SimpleNamespace(RESIDENT_WINDOW_DAYS=730, RESIDENT_MIN_TOURNAMENTS=3, RESIDENT_MIN_MONTHS=2)
COUNTRY = SimpleNamespace(is_overseas_country=lambda c: bool(c) and c != "Japan")


def _event(root: Path, name: str, date: str, uids, is_1on1=True, offline=True, sub=""):
    d = root / "events" / date.replace("-", "/") / name / "Singles" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / "derived.json").write_text(json.dumps({"is_1on1": is_1on1, "is_offline": offline, "calendar": {"date": date}}))
    (d / "standings.json").write_text(json.dumps({"data": [{"placement": i + 1, "user_id": u} for i, u in enumerate(uids)]}))


class OverseasStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = Path(self.tmp.name)
        users = [{"user_id": 1, "country": "Japan"}, {"user_id": 2, "country": "France"},
                 {"user_id": 3, "country": "France"}, {"user_id": 4, "country": "United States"},
                 {"user_id": 6, "country": "France"}]
        (r / "users.jsonl").write_text("".join(json.dumps(u) + "\n" for u in users))
        (r / "manual").mkdir()
        (r / "manual" / "overseas_manual.json").write_text(json.dumps({"uids": [5, 2], "home_uids": [4]}))
        # 7 was merged into 3 (so 3 has three events)
        (r / "manual" / "user_merges.json").write_text(json.dumps({"merges": [{"old": 7, "new": 3}]}))
        _event(r, "A", "2026-01-10", [1, 2, 3])
        _event(r, "B", "2026-03-10", [1, 3])
        _event(r, "C", "2026-05-10", [1, 7])
        _event(r, "C", "2026-05-10", [2, 2, 2], sub="class_phases/B_virtual")   # class bracket: not counted
        _event(r, "D", "2026-06-10", [2], is_1on1=False)                        # not 1on1: not counted
        _event(r, "E", "2026-07-10", [2], offline=False)                         # online: not counted
        _event(r, "F", "2020-01-10", [6, 6])                                      # outside the window

    def tearDown(self):
        self.tmp.cleanup()

    def test_status(self):
        written, st = overseas.write_overseas_status(CLF, COUNTRY, self.root, self.root / "events", dt.date(2026, 9, 28))
        self.assertTrue(written)
        self.assertEqual(st["by_country"], [2, 6])     # 3 is a resident (3 events over 3 months via the merge), 4 is home
        self.assertEqual(st["manual"], [5])            # 2 has a country, so only 5 (no registration) comes from the list
        self.assertEqual(st["resident"], [3])
        self.assertEqual(st["home"], [4])
        again, _ = overseas.write_overseas_status(CLF, COUNTRY, self.root, self.root / "events", dt.date(2026, 9, 28))
        self.assertFalse(again)                        # unchanged content is not rewritten

    def test_merge_chain(self):
        (self.root / "manual" / "user_merges.json").write_text(json.dumps({"merges": [{"old": 8, "new": 9}, {"old": 9, "new": 10}]}))
        self.assertEqual(overseas.load_merges(self.root / "manual"), {8: 10, 9: 10})


if __name__ == "__main__":
    unittest.main()
