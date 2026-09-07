# smash_database — start.gg の大会データ (スマブラ SP)

## リポジトリの構成 (2026-09-07 から)

| ブランチ | 中身 | 用途 |
|---|---|---|
| `main` | `scripts/` (取得スクリプト) と文書だけ。`data/` は gitignore | スクリプトの管理。全地域共通 |
| `Japan` | `startgg/events/Japan/**` と `startgg/Japan/{done.csv,done_events.csv,tournaments.jsonl,users.jsonl}` | 日本の大会データ。毎晩の取得分を commit / push |
| (今後) `North_America` など | `startgg/events/<地域>/**` と `startgg/<地域>/{...}` | 地域ごとに担当者が管理。パスが重ならないので merge で統合できる |

データブランチは `main` の worktree の `data/` にネストして checkout する:

```sh
git clone --single-branch --branch main git@github.com:tosakazu/smash_database.git smash_db_tournament
cd smash_db_tournament
git fetch origin Japan && git worktree add data Japan     # → data/startgg/events/Japan/... (以前と同じパス)
```

`git clone` を無指定で行うと全地域のデータ履歴も落ちてくるので `--single-branch` を付ける。

## スクリプト

- `scripts/common/` — 全地域共通: API 層 (`utils.py`)、クエリ (`queries.py`)、時計 (`clock.py`)、CLI 定型 (`_cli.py`)、
  取得本体 (`download.py`, `download_policy.py`, `redownload_matches_v2.py`)、未来大会 (`fetch_upcoming.py`)、`manual/` (手動ツール)、`fix/`
- `scripts/Japan/` — 日本固有: 下位クラス (B/C クラス) の分離 (`update_class_data.py` と子 4 本)、`manual/`
- `scripts/test/` — 単体テスト。取得工程の回帰テスト (記録・再生) は spsp リポジトリの `tests/fetch/`
- 一覧と役割は `scripts/common/README.md`

## 取得 (日本)

夜間は spsp の `deploy/update_and_deploy.sh` が呼ぶ。手で回すなら (トークンは環境変数 `STARTGG_TOKEN`):

```sh
python3 scripts/common/download.py --country_code JP --start_date 2026-09-07 --finish_date 2026-08-24 \
  --startgg_dir data/startgg/events --done_file_path data/startgg/Japan/done.csv \
  --users_file_path data/startgg/Japan/users.jsonl --tournament_file_path data/startgg/Japan/tournaments.jsonl
python3 scripts/Japan/update_class_data.py --since-days 30
```

`--done_file_path` 等を省略すると `data/startgg/<地域>/` (地域は `--country_code` から) が既定になる。

## 履歴

2026-09-07 に履歴を作り直した (それ以前の履歴と海外 4 地域のデータは持たない)。
