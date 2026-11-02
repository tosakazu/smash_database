"""Post-completion refresh: re-fetch once at each checkpoint after the end (download_policy.recent_refresh_due).

Until 2026-11-03 the last fetch time was the mtime of attr.json; a fresh checkout (GitHub Actions) sets it to "now",
so the refresh never fired. fetched_at in attr.json replaces it.
"""
import json
import os
import tempfile
import unittest

from scripts.common import download, download_policy as P

H = 3600
END = 1_800_000_000
CP = [24 * H, 72 * H]


def due(fetched_at, now):
    return P.recent_refresh_due(END, fetched_at, now, CP)


class RecentRefreshDueTests(unittest.TestCase):
    def test_before_the_first_checkpoint(self):
        self.assertFalse(due(END + H, END + 23 * H))

    def test_each_checkpoint_once(self):
        self.assertTrue(due(END + H, END + 24 * H))
        self.assertFalse(due(END + 24 * H + 60, END + 30 * H))     # fetched after the 24 h checkpoint
        self.assertTrue(due(END + 25 * H, END + 72 * H))
        self.assertFalse(due(END + 73 * H, END + 80 * H))

    def test_missed_checkpoint_is_caught_up_within_the_slack(self):
        self.assertTrue(due(END + H, END + 90 * H))
        self.assertFalse(due(END + H, END + 72 * H + P.REFRESH_SLACK_SEC + 1))

    def test_no_fetched_at_counts_as_fetched_at_the_end(self):
        self.assertFalse(due(None, END + 10 * H))
        self.assertTrue(due(None, END + 24 * H))

    def test_no_end(self):
        self.assertFalse(P.recent_refresh_due(None, None, END, CP))


class EventNeedsRecentRefreshTests(unittest.TestCase):
    def _dir(self, attr):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "attr.json"), "w") as f:
            json.dump(attr, f)
        return d

    def test_ignores_file_mtime(self):
        # the file was just written (fresh checkout), but fetched_at says it was fetched before the 24 h checkpoint
        d = self._dir({"end_timestamp": END, "fetched_at": END + H})
        self.assertTrue(download._event_needs_recent_refresh(d, END, END + 25 * H))

    def test_uses_the_corrected_end_when_later(self):
        # start.gg endAt one day before the real end (corrected from set times in attr.json)
        d = self._dir({"end_timestamp": END + 24 * H, "fetched_at": END + 25 * H})
        self.assertFalse(download._event_needs_recent_refresh(d, END, END + 30 * H))
        self.assertTrue(download._event_needs_recent_refresh(d, END, END + 48 * H))


if __name__ == "__main__":
    unittest.main()
