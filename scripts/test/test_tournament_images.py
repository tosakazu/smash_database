"""tournament_images.jsonl: the icon index written by download.py and the backfill."""
import os
import tempfile
import unittest

from scripts.common import tournament_images as ti
from scripts.common.queries import get_tournaments_by_game_query

URL = "https://images.start.gg/images/tournament/1/image-abc.png"


class TournamentImagesTests(unittest.TestCase):
    def test_listing_query_asks_for_the_profile_image(self):
        self.assertIn('images(type: "profile")', get_tournaments_by_game_query("JP", before_ts=1))

    def test_first_image_or_null(self):
        self.assertEqual(ti.from_api([{"url": URL, "width": 400, "height": 400}]),
                         {"url": URL, "width": 400, "height": 400})
        none = {"url": None, "width": None, "height": None}
        self.assertEqual(ti.from_api([]), none)
        self.assertEqual(ti.from_api(None), none)

    def test_update_reports_changes_only(self):
        index = {}
        self.assertTrue(ti.update(index, "5", [{"url": URL, "width": 1, "height": 1}]))
        self.assertFalse(ti.update(index, 5, [{"url": URL, "width": 1, "height": 1}]))
        self.assertTrue(ti.update(index, 5, []))            # icon removed on start.gg
        self.assertIsNone(index[5]["url"])

    def test_save_load_round_trip_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            path = ti.images_path_for(os.path.join(d, "tournaments.jsonl"))
            self.assertEqual(os.path.basename(path), "tournament_images.jsonl")
            self.assertEqual(ti.load(path), {})
            index = {}
            ti.update(index, 20, [{"url": URL, "width": 2, "height": 2}])
            ti.update(index, 3, [])
            ti.save(index, path)
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertTrue(lines[0].startswith('{"tournament_id": 3,'))
            self.assertEqual(ti.load(path), index)


if __name__ == "__main__":
    unittest.main()
