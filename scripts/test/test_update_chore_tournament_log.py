import json
import tempfile
import unittest
from pathlib import Path

from scripts.fix.update_chore_tournament_log import main


class UpdateChoreTournamentLogTest(unittest.TestCase):
    def test_generates_markdown_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            docs_dir = root / "docs/chore-tornament"
            events_root = root / "data/startgg/events/Japan"
            (events_root / "2018/12/29").mkdir(parents=True)
            (events_root / "2018/12/30").mkdir(parents=True)

            import sys

            original_argv = sys.argv
            sys.argv = [
                "update_chore_tournament_log.py",
                "--docs-dir",
                str(docs_dir),
                "--events-root",
                str(events_root),
                "--mark-start",
                "2018-12-29",
                "--mark-end",
                "2018-12-30",
                "--workflow",
                "update_tournament",
                "--checked-at",
                "2026-04-10T00:00:00+00:00",
            ]
            try:
                main()
            finally:
                sys.argv = original_argv

            metadata = json.loads((docs_dir / "checked_dates.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["2018-12-29"]["workflow"], "update_tournament")
            self.assertEqual(metadata["2018-12-30"]["workflow"], "update_tournament")

            markdown = (docs_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("| 2018-12-29 | yes | yes |", markdown)
            self.assertIn("| 2018-12-30 | yes | yes |", markdown)


if __name__ == "__main__":
    unittest.main()
