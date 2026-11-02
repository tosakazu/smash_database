"""fetch_class_phase_standings --refresh-days: a class bracket fetched while it was still running is fetched again
after the event and rewritten only when start.gg's standings changed (2026-11-02, 琵琶湖事変＃5 B-class)."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.common import fetch_class_phase_standings as f  # noqa: E402
from scripts.common.utils import FetchError  # noqa: E402

RUNNING = [{"placement": 2, "entrant_id": 1, "entrant_name": "a", "user_id": 11},
           {"placement": 2, "entrant_id": 2, "entrant_name": "b", "user_id": 22}]
FINAL = [{"placement": 1, "entrant_id": 2, "entrant_name": "b", "user_id": 22},
         {"placement": 2, "entrant_id": 1, "entrant_name": "a", "user_id": 11}]


def make_event(root: Path, ended_days_ago: float, standings) -> Path:
    ed = root / "2026" / "11" / "01" / "T" / "E"
    (ed / "class_phases").mkdir(parents=True)
    end = int(time.time() - ended_days_ago * 86400)
    (ed / "attr.json").write_text(json.dumps({"event_id": 5, "timestamp": end - 3600, "end_timestamp": end}))
    (ed / "phases.json").write_text(json.dumps({"event_id": 5, "event_name": "E", "has_class_phases": True, "phases": [
        {"id": 77, "name": "Bクラス", "order": 2, "is_class": True, "phase_groups": [{"id": 900, "display": "1"}]}]}))
    (ed / "class_phases" / "77.json").write_text(json.dumps({"phase_id": 77, "phase_groups": [
        {"phase_group_id": 900, "display": "1", "standings": standings, "played_user_ids": [11, 22]}]}))
    return ed


class RefreshTests(unittest.TestCase):
    def run_main(self, root, fetched, refresh_days=7):
        f.CHANGED_EVENT_DIRS.clear()
        with mock.patch.object(f, "setup_api"), mock.patch.object(f, "fetch_phase_group_standings", side_effect=fetched) as m:
            f.main(["--events-root", str(root), "--refresh-days", str(refresh_days)])
        return m

    def test_running_bracket_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = make_event(Path(tmp), 1, RUNNING)
            self.run_main(tmp, lambda *a, **k: FINAL)
            d = json.loads((ed / "class_phases" / "77.json").read_text())
            self.assertEqual(d["phase_groups"][0]["standings"], FINAL)
            self.assertNotIn("played_user_ids", d["phase_groups"][0])   # fetch_class_phase_players fills it again
            self.assertEqual(f.CHANGED_EVENT_DIRS, {ed})

    def test_unchanged_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = make_event(Path(tmp), 1, FINAL)
            before = (ed / "class_phases" / "77.json").read_text()
            self.run_main(tmp, lambda *a, **k: FINAL)
            self.assertEqual((ed / "class_phases" / "77.json").read_text(), before)
            self.assertEqual(f.CHANGED_EVENT_DIRS, set())

    def test_old_event_and_refresh_off_are_skipped(self):
        for days_ago, refresh in ((30, 7), (1, 0)):
            with tempfile.TemporaryDirectory() as tmp:
                make_event(Path(tmp), days_ago, RUNNING)
                m = self.run_main(tmp, lambda *a, **k: FINAL, refresh_days=refresh)
                m.assert_not_called()

    def test_failed_or_empty_refresh_keeps_the_old_file(self):
        def boom(*a, **k):
            raise FetchError("x")
        for fetched in (boom, lambda *a, **k: []):
            with tempfile.TemporaryDirectory() as tmp:
                ed = make_event(Path(tmp), 1, RUNNING)
                before = (ed / "class_phases" / "77.json").read_text()
                self.run_main(tmp, fetched)
                self.assertEqual((ed / "class_phases" / "77.json").read_text(), before)
                self.assertEqual(f.CHANGED_EVENT_DIRS, set())


if __name__ == "__main__":
    unittest.main()
