# GitHub Actions

## 概要
- GitHub Actions による更新結果は、PR を作らず `chore-update` ブランチへ直接 commit / push する。
- 大会データの取得状況は `docs/chore-tornament/README.md` に日付単位で記録する。
- 記録対象の日付範囲は `2018-12-29` から当日まで。

## ワークフロー

### `update_tournament.yml`
- 定義ファイル: `.github/workflows/update_tournament.yml`
- 実行タイミング:
  - `schedule`: 毎日 `18:00 UTC` = 毎日 `03:00 JST`
  - `workflow_dispatch`
- 実行内容:
  - `scripts/fetch/download.py` を日本 (`JP`) 向けに当日・前日分で実行
  - `python -m unittest scripts.test.test_validate_data` を実行
  - `scripts/fix/update_chore_tournament_log.py` で `docs/chore-tornament/` を更新
  - 差分があれば `chore-update` ブランチへ直接 push

### `update_user.yml`
- 定義ファイル: `.github/workflows/update_user.yml`
- 実行タイミング:
  - `schedule`: 毎日 `18:05 UTC` = 毎日 `03:05 JST`
  - `workflow_dispatch`
- 実行内容:
  - `scripts/fetch/refresh_users.py --max_users 300` を実行
  - 差分があれば `chore-update` ブランチへ直接 push

### `data_backfill.yml`
- 定義ファイル: `.github/workflows/data_backfill.yml`
- 実行タイミング:
  - `workflow_dispatch`
- 入力:
  - `start_date`
  - `end_date`
  - `country_code`
- 実行内容:
  - 指定期間で `scripts/fetch/download.py` を実行
  - `python -m unittest scripts.test.test_validate_data` を実行
  - 指定期間を `scripts/fix/update_chore_tournament_log.py` に記録
  - 差分があれば `chore-update` ブランチへ直接 push

### `data_monthly_check.yml`
- 定義ファイル: `.github/workflows/data_monthly_check.yml`
- 実行タイミング:
  - `schedule`: 毎日 `18:10 UTC` = 毎日 `03:10 JST`
  - `workflow_dispatch`
- 実行内容:
  - `scripts/fix/check_events_in_tournaments.py --apply` を実行
  - `scripts/fix/update_chore_tournament_log.py` を実行して記録表を再生成
  - 差分があれば `chore-update` ブランチへ直接 push
  - `check_events_in_tournaments.py` が失敗した場合は workflow 全体も失敗にする

## 手動実行と定期実行の挙動

- `update_tournament.yml`
  - `schedule` の場合: 毎日 `03:00 JST` に起動し、その日の JST 日付と前日の JST 日付を対象に大会データ取得を行う。
  - `workflow_dispatch` の場合: 実行した時点ですぐ起動し、同じく実行日の JST 日付と前日の JST 日付を対象に大会データ取得を行う。

- `update_user.yml`
  - `schedule` の場合: 毎日 `03:05 JST` に起動し、`users_refresh_cursor.txt` を使ってユーザー更新を継続する。
  - `workflow_dispatch` の場合: 実行した時点ですぐ起動し、同じ処理をその場で実行する。

- `data_backfill.yml`
  - `schedule` はない。
  - `workflow_dispatch` の場合のみ起動し、指定した `start_date` から `end_date` の範囲を取得して、その範囲を `docs/chore-tornament` に記録する。

- `data_monthly_check.yml`
  - `schedule` の場合: 毎日 `03:10 JST` に起動し、`tournaments.jsonl` の補正と `docs/chore-tornament` の再生成を行う。
  - `workflow_dispatch` の場合: 実行した時点ですぐ起動し、同じ補正処理と再生成をその場で実行する。

- 共通挙動
  - どの workflow も最初に `chore-update` ブランチへ checkout し、差分がある場合のみそのブランチへ commit / push する。
  - PR は作成しない。
  - `schedule` は GitHub Actions の仕様上、デフォルトブランチ上の workflow 定義を元に起動される。

## `docs/chore-tornament`

### 生成ファイル
- `docs/chore-tornament/README.md`
  - `2018-12-29` から当日までを 1 日 1 行の Markdown テーブルで出力する。
  - `data/startgg/events/Japan/YYYY/MM/DD` のフォルダ有無を `Folder Exists` に記録する。
  - GitHub Actions がその日付を取得対象として処理した場合、`Checked By GitHub Actions` / `Last Checked At (JST)` / `Workflow` を更新する。
- `docs/chore-tornament/checked_dates.json`
  - Markdown 生成用の記録データを保持する。

### 更新スクリプト
- `scripts/fix/update_chore_tournament_log.py`
  - `--mark-start` と `--mark-end` で、GitHub Actions が確認した日付範囲を記録する。
  - 指定がない場合は、既存記録を維持したままテーブルだけ再生成する。

## 実際に使うシークレット
- `STARTGG_TOKEN`
