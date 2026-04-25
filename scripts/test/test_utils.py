import unittest
from unittest.mock import patch

from scripts.utils import fetch_all_nodes, set_request_timeout


class FetchAllNodesTests(unittest.TestCase):
    def setUp(self):
        set_request_timeout(60)

    @patch("scripts.utils.time.sleep", return_value=None)
    @patch("scripts.utils.fetch_data_with_retries")
    def test_fetch_all_nodes_stops_at_total_pages(self, mock_fetch, _mock_sleep):
        pages = []
        responses = [
            {
                "data": {
                    "event": {
                        "sets": {
                            "pageInfo": {"totalPages": 2},
                            "nodes": [{"id": 1}, {"id": 2}],
                        }
                    }
                }
            },
            {
                "data": {
                    "event": {
                        "sets": {
                            "pageInfo": {"totalPages": 2},
                            "nodes": [{"id": 3}],
                        }
                    }
                }
            },
        ]

        def side_effect(_query, variables):
            pages.append(variables["page"])
            return responses[len(pages) - 1]

        mock_fetch.side_effect = side_effect

        nodes = fetch_all_nodes("query", {"eventId": 1}, ["event", "sets"], per_page=50)

        self.assertEqual(nodes, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(pages, [1, 2])

    @patch("scripts.utils.time.sleep", return_value=None)
    @patch("scripts.utils.fetch_data_with_retries")
    def test_fetch_all_nodes_falls_back_to_empty_page_stop_without_page_info(self, mock_fetch, _mock_sleep):
        mock_fetch.side_effect = [
            {"data": {"event": {"standings": {"nodes": [{"id": 1}]}}}},
            {"data": {"event": {"standings": {"nodes": []}}}},
        ]

        nodes = fetch_all_nodes("query", {"eventId": 1}, ["event", "standings"], per_page=50)

        self.assertEqual(nodes, [{"id": 1}])
        self.assertEqual(mock_fetch.call_count, 2)


class FetchDataWithRetriesTests(unittest.TestCase):
    @patch("scripts.utils.requests.post")
    def test_fetch_data_with_retries_passes_timeout(self, mock_post):
        from scripts.utils import fetch_data_with_retries

        set_request_timeout(12)
        mock_post.return_value.text = '{"data": {"ok": true}}'
        mock_post.return_value.raise_for_status.return_value = None

        payload = fetch_data_with_retries("query", {"eventId": 1})

        self.assertEqual(payload, {"data": {"ok": True}})
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 12)


if __name__ == "__main__":
    unittest.main()
