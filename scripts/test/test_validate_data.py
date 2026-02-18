import json
import tempfile
import unittest
from pathlib import Path

from scripts.fix.validate_data import validate_event_dir


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
