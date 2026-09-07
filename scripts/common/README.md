# scripts/ — start.gg からのデータ取得

## 夜間パイプライン (spsp の deploy/update_and_deploy.sh が呼ぶもの)

| スクリプト | 役割 |
|---|---|
| `fetch/download.py` | 取得窓の大会を列挙し、event ごとに standings / seeds / matches (試合と games) / attr を `data/startgg/events/...` に保存。users.jsonl / tournaments.jsonl / done.csv を更新 |
| `fetch/download_policy.py` | download.py の「取るか・飛ばすか・done にするか」の判定だけ (副作用なし。spsp の tests/fetch/test_dl_policy.py に判定表) |
| `fetch/fetch_upcoming.py` | 未開催大会の一覧 → upcoming.json |
| `fetch/update_class_data.py` | 下位クラス (B/C クラス) の分離: `fetch_event_phases` → `fetch_class_phase_standings` → `fetch_class_phase_players` → `build_class_virtual_tournaments` を同じプロセスで順に呼ぶ |
| `fetch/_cli.py` | `--token --url --max_retries --retry_delay` の共通定義と API 層の設定 (token は環境変数 `STARTGG_TOKEN`) |
| `clock.py` | 「今」の唯一の取得口 (テストで固定できる) |
| `utils.py` | API 層 (`fetch_data_with_retries` = HTTP の唯一の場所、`fetch_all_nodes` = ページング) と JSON/JSONL の読み書き |
| `queries.py` | GraphQL クエリ文字列 |
| `fetch/redownload_matches_v2.py` | 試合の取得と `matches.json` の書式 (download.py が関数を使う。単体実行は手動の取り直し用) |

出力の書式 (キー順・indent・`version` の有無) は下流 (spsp の build/data_loader.py) が読むので変えない。
書き出しは `utils.write_json` (indent 2 + version) / `write_json_pretty` / `write_json_compact` / `write_matches_v2` の 4 種。

## テスト (spsp リポジトリ側 tests/fetch/)

- `dl_golden.py`: API 応答を記録した cassette を再生し、出力ツリーが記録時とバイト一致することを確かめる (ネット不要)
- `test_dl_pure.py` / `test_dl_policy.py` / `test_api_layer.py` / `test_event_window.py` / `test_awaiting_resume.py`: 純関数と API 層の異常系

## 手動ツール (パイプラインは使わない)

`fetch/download_specific_event.py` (1 大会だけ取る), `fetch/refresh_users.py` (users.jsonl の更新), `fetch/refresh_top_ranked.py`,
`fetch/refetch_incomplete_events.py`, `fetch/redownload_matches.py` (v1), `fetch/rescan_lower_class.py`, `fetch/fetch_event_matches_phased.py`,
`fetch/backfill_end_at.py`, `fetch/backfill_wave_start_at.py`, `fetch/fetch_character_games.py` / `merge_character_games.py` (キャラ使用の一括 backfill),
`storeJson.py`。互いに import しないので、消しても壊れるのは本人だけ。

## 注意

- 本番 (ConoHa) は git HEAD ではなく working tree で動いていた時期がある。リファクタの基準は 2026-09-07 の working tree の snapshot commit。
- `download.py` は失敗した event を CWD の `failed_events.log` に追記する (置き場の変更は運用と要相談)。
