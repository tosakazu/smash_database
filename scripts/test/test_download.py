import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.fetch.download import (
    build_match_dedupe_key,
    dedupe_set_nodes,
    download_all_tournaments,
    fetch_all_sets,
    should_skip_tournament,
    write_matches,
)
from scripts.utils import FetchError


class DownloadTests(unittest.TestCase):
    @patch("scripts.fetch.download.fetch_all_nodes")
    def test_fetch_all_sets_retries_with_smaller_page_size_when_duplicate_ids_found(self, mock_fetch_all_nodes):
        mock_fetch_all_nodes.side_effect = [
            [{"id": 1}, {"id": 1}, {"id": 2}],
            [{"id": 1}, {"id": 2}, {"id": 3}],
        ]

        sets_data = fetch_all_sets(1308799)

        self.assertEqual(sets_data, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(mock_fetch_all_nodes.call_count, 2)
        self.assertEqual(mock_fetch_all_nodes.call_args_list[0].kwargs["per_page"], 50)
        self.assertEqual(mock_fetch_all_nodes.call_args_list[1].kwargs["per_page"], 25)

    def test_dedupe_set_nodes_removes_duplicate_set_ids_preserving_order(self):
        self.assertEqual(
            dedupe_set_nodes([{"id": 10}, {"id": 10}, {"id": 20}, {"name": "no-id"}]),
            [{"id": 10}, {"id": 20}, {"name": "no-id"}],
        )

    @patch("scripts.fetch.download.fetch_all_nodes")
    def test_fetch_all_sets_retries_with_smaller_page_size_when_query_too_complex(self, mock_fetch_all_nodes):
        mock_fetch_all_nodes.side_effect = [
            FetchError("query complexity is too high"),
            [{"id": 1}, {"id": 2}],
        ]

        sets_data = fetch_all_sets(1308799)

        self.assertEqual(sets_data, [{"id": 1}, {"id": 2}])
        self.assertEqual(mock_fetch_all_nodes.call_count, 2)
        self.assertEqual(mock_fetch_all_nodes.call_args_list[0].kwargs["per_page"], 50)
        self.assertEqual(mock_fetch_all_nodes.call_args_list[1].kwargs["per_page"], 25)

    def test_build_match_dedupe_key_ignores_details(self):
        base = {
            "winner_id": 1,
            "loser_id": 2,
            "winner_score": 2,
            "loser_score": 1,
            "round_text": "Winners Round 1",
            "round": 1,
            "phase": "B1200",
            "wave": "B",
            "dq": False,
            "cancel": False,
            "state": 3,
            "details": [{"game_id": 10}],
        }
        variant = dict(base)
        variant["details"] = [{"game_id": 11}]
        self.assertEqual(build_match_dedupe_key(base), build_match_dedupe_key(variant))

    def test_write_matches_dedupes_semantically_identical_matches(self):
        node = {
            "id": 101,
            "slots": [
                {
                    "entrant": {"id": 11},
                    "standing": {"stats": {"score": {"value": 2}}},
                },
                {
                    "entrant": {"id": 22},
                    "standing": {"stats": {"score": {"value": 0}}},
                },
            ],
            "games": None,
            "phaseGroup": {"displayIdentifier": "B1200", "wave": {"identifier": "B"}},
            "fullRoundText": "Winners Round 1",
            "round": 1,
            "state": 3,
        }
        duplicate_with_different_id = dict(node)
        duplicate_with_different_id["id"] = 202

        with tempfile.TemporaryDirectory() as tmpdir:
            write_matches([node, duplicate_with_different_id], {11: 2716511, 22: 2962327}, tmpdir)
            with open(f"{tmpdir}/matches.json", encoding="utf-8") as fh:
                payload = json.load(fh)

        self.assertEqual(len(payload["data"]), 1)

    def test_should_skip_tournament_when_done_and_complete(self):
        tournaments = {
            1: {
                "events": [
                    {"path": "event-dir"},
                ]
            }
        }

        with patch("scripts.fetch.download.event_files_complete", return_value=True):
            self.assertTrue(should_skip_tournament(1, tournaments, {1}, force_refresh=False))

    def test_should_not_skip_tournament_when_force_refresh_enabled(self):
        tournaments = {
            1: {
                "events": [
                    {"path": "event-dir"},
                ]
            }
        }

        with patch("scripts.fetch.download.event_files_complete", return_value=True):
            self.assertFalse(should_skip_tournament(1, tournaments, {1}, force_refresh=True))

    def test_should_not_skip_tournament_when_missing_files(self):
        tournaments = {
            1: {
                "events": [
                    {"path": "event-dir"},
                ]
            }
        }

        with patch("scripts.fetch.download.event_files_complete", return_value=False):
            self.assertFalse(should_skip_tournament(1, tournaments, {1}, force_refresh=False))

    @patch("scripts.fetch.download.read_set", return_value=set())
    @patch("scripts.fetch.download.read_users_jsonl", return_value={})
    @patch("scripts.fetch.download.read_tournaments_jsonl", return_value={})
    @patch("scripts.fetch.download.fetch_latest_tournaments_by_game")
    @patch("scripts.fetch.download.fetch_event_ids_from_tournament")
    @patch("scripts.fetch.download.download_all_set")
    @patch("scripts.fetch.download.download_standings")
    @patch("scripts.fetch.download.download_seeds")
    @patch("scripts.fetch.download.extend_user_info")
    @patch("scripts.fetch.download.extend_tournament_info")
    @patch("scripts.fetch.download.write_done_tournaments")
    def test_download_all_tournaments_matches_only_skips_heavy_fetches(
        self,
        mock_write_done,
        mock_extend_tournament,
        mock_extend_user_info,
        mock_download_seeds,
        mock_download_standings,
        mock_download_all_set,
        mock_fetch_event_ids,
        mock_fetch_tournaments,
        _mock_read_tournaments,
        _mock_read_users,
        _mock_read_set,
    ):
        mock_fetch_tournaments.return_value = (
            [
                {
                    "id": 1,
                    "name": "Test Tournament",
                    "startAt": 1714780800,
                    "endAt": 1714784400,
                    "countryCode": "JP",
                    "city": "Tokyo",
                    "lat": None,
                    "lng": None,
                    "venueName": None,
                    "timezone": "Asia/Tokyo",
                    "postalCode": None,
                    "venueAddress": None,
                    "mapsPlaceId": None,
                    "url": "https://example.com",
                }
            ],
            1,
        )
        mock_fetch_event_ids.return_value = [(10, "Singles", False)]

        with tempfile.TemporaryDirectory() as tmpdir:
            event_dir = f"{tmpdir}/Japan/2024/05/04/Test Tournament/Singles"
            os.makedirs(event_dir, exist_ok=True)
            download_all_tournaments(
                "1386",
                "JP",
                datetime(2024, 5, 4, 23, 59, 59),
                datetime(2024, 5, 4, 0, 0, 0),
                f"{tmpdir}",
                f"{tmpdir}/done.csv",
                f"{tmpdir}/users.jsonl",
                f"{tmpdir}/tournaments.jsonl",
                force_refresh=True,
                matches_only=True,
            )

        mock_download_all_set.assert_called_once_with(10, {}, event_dir)
        mock_download_standings.assert_not_called()
        mock_download_seeds.assert_not_called()
        mock_extend_user_info.assert_not_called()
        mock_extend_tournament.assert_not_called()
        mock_write_done.assert_not_called()


if __name__ == "__main__":
    unittest.main()
