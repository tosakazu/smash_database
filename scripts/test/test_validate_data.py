import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.fix.validate_data import validate_event_dir


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


class ValidateDataTests(unittest.TestCase):
    def test_missing_required_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event_dir = Path(tmpdir)
            write_json(event_dir / "attr.json", {"place": {}})
            errors, warnings = validate_event_dir(event_dir)
            self.assertTrue(any("missing file matches.json" in err for err in errors))
            self.assertEqual(warnings, [])

    def test_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event_dir = Path(tmpdir)
            write_json(event_dir / "attr.json", {"place": {}})
            write_json(event_dir / "matches.json", {"data": []})
            write_json(event_dir / "seeds.json", {"data": []})
            write_json(event_dir / "standings.json", {"data": []})
            errors, warnings = validate_event_dir(event_dir)
            self.assertTrue(any("missing field 'event_id'" in err for err in errors))
            self.assertTrue(any("place" in err for err in errors))
            self.assertTrue(any("standings.json is empty" in err for err in errors))
            self.assertEqual(warnings, [])

    def test_match_ids_not_in_standings_warn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event_dir = Path(tmpdir)
            write_json(
                event_dir / "attr.json",
                {
                    "event_id": 1,
                    "tournament_name": "T",
                    "event_name": "E",
                    "timestamp": 0,
                    "region": "Japan",
                    "num_entrants": 2,
                    "offline": True,
                    "url": "u",
                    "place": {
                        "country_code": "JP",
                        "city": "c",
                        "lat": 0,
                        "lng": 0,
                        "venue_name": "v",
                        "timezone": "t",
                        "postal_code": "p",
                        "venue_address": "a",
                        "maps_place_id": "m",
                    },
                    "labels": {},
                    "status": "completed",
                },
            )
            write_json(event_dir / "standings.json", {"data": [{"placement": 1, "user_id": 10}]})
            write_json(event_dir / "seeds.json", {"data": [{"seed_num": 1, "user_id": 10}]})
            write_json(
                event_dir / "matches.json",
                {"data": [{"winner_id": 99, "loser_id": 10, "winner_score": 2, "loser_score": 1}]},
            )
            errors, warnings = validate_event_dir(event_dir)
            self.assertEqual(errors, [])
            self.assertTrue(any("match IDs not in standings" in warn for warn in warnings))


if __name__ == "__main__":
    unittest.main()


class SummaryBaselineTests(unittest.TestCase):
    """--summary / --baseline が使う種別分けと基準比較。"""

    def test_categorize(self):
        from scripts.common.fix.validate_data import categorize

        cases = {
            "path/to/event: missing file matches.json": "missing_file:matches.json",
            "path: matches missing winner/loser ratio 33.3% exceeds threshold": "sets_missing_winner_loser",
            "path: standings missing user_id ratio 50.0% exceeds threshold": "standings_missing_user_id",
            "path: match IDs not in standings ratio 6.0% exceeds threshold": "set_ids_not_in_standings",
            "path/attr.json: missing field 'event_id'": "missing_field:event_id",
            "tournaments.jsonl:12: missing event dir data/x": "index_points_at_missing_dir",
            "何にも当てはまらないメッセージ": "other",
        }
        for message, label in cases.items():
            with self.subTest(message=message):
                self.assertEqual(categorize(message), label)

    def test_summarize_counts_by_category(self):
        from scripts.common.fix.validate_data import summarize

        counts = summarize([
            "a: missing file matches.json",
            "b: missing file matches.json",
            "c: missing file seeds.json",
        ])
        self.assertEqual(counts, {"missing_file:matches.json": 2, "missing_file:seeds.json": 1})

    def test_baseline_tolerates_small_growth_and_reports_jumps(self):
        from scripts.common.fix.validate_data import compare_with_baseline, write_baseline

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_file = Path(tmpdir) / "validation_baseline.json"
            # 基準が無ければ作るだけで、増加としては報告しない
            self.assertEqual(compare_with_baseline({"x": 5}, baseline_file, 10), [])
            write_baseline({"x": 5}, baseline_file)

            self.assertEqual(compare_with_baseline({"x": 15}, baseline_file, 10), [])  # 許容幅ちょうど
            regressions = compare_with_baseline({"x": 16}, baseline_file, 10)
            self.assertEqual(len(regressions), 1)
            self.assertIn("5 → 16", regressions[0])
            # 基準に無い種別が出てきたら 0 からの増加として見る
            self.assertEqual(compare_with_baseline({"y": 3}, baseline_file, 10), [])
            self.assertEqual(len(compare_with_baseline({"y": 30}, baseline_file, 10)), 1)
