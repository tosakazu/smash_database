# Fix / 不完全な点メモ

- `scripts/fetch/download_specific_event.py` は既存トーナメントにイベントを追加する際、`tournaments.jsonl` に反映されない（コメントにも記載あり）。
- `scripts/fetch/download_specific_event.py` の先頭コメントに「get_event_details_by_slug_query を追加する必要がある」とあるが、現状は `get_event_details_by_tournament_query` を使用しておりコメントが古い。
- `scripts/utils.py` の `fetch_data_with_retries()` は `variables` を `json.dumps()` して送信しているため、APIが変数をオブジェクトとして要求する場合に互換性の懸念がある。
