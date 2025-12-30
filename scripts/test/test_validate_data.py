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
            errors = validate_event_dir(event_dir)
            self.assertTrue(any("missing file matches.json" in err for err in errors))

    def test_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event_dir = Path(tmpdir)
            write_json(event_dir / "attr.json", {"place": {}})
            write_json(event_dir / "matches.json", {"data": []})
            write_json(event_dir / "seeds.json", {"data": []})
            write_json(event_dir / "standings.json", {"data": []})
            errors = validate_event_dir(event_dir)
            self.assertTrue(any("missing field 'event_id'" in err for err in errors))
            self.assertTrue(any("place" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
