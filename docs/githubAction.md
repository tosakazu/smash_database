# GitHub Actions（想定）

## 目的
- 定期的に start.gg から最新データを取得し、`data/` を更新する。

## トリガー
- `schedule`（毎日 03:00 UTC）
- `workflow_dispatch`（手動バックフィル）
- `schedule`（毎月1日 04:00 UTC: データ全体チェック）

## 使うシークレット/環境変数
- `STARTGG_TOKEN`（必須）
- `OPENAI_API_KEY`（任意）
- `STARTGG_GAME_ID`（任意 / 既定: 1386）
- `STARTGG_COUNTRY_CODE`（任意）

## 想定ステップ
1. Checkout
2. Python セットアップ
3. 依存インストール（必要なら）
4. データ取得
5. `data/` をコミット

## 実行コマンド（現在のワークフロー）
- 定期取得（日本限定 / 直近2日）
  - `python scripts/fetch/download.py --token "$STARTGG_TOKEN" --country_code "JP" --finish_date "$(date -u -d '2 days ago' +%F)"`
- 手動バックフィル（期間指定）
  - `python scripts/fetch/download.py --token "$STARTGG_TOKEN" --country_code "<CODE>" --finish_date "YYYY-MM-DD"`
- ユーザー情報更新
  - `python scripts/fetch/refresh_users.py --token "$STARTGG_TOKEN"`
- データ検証
  - `python scripts/fix/validate_data.py`
- テスト
  - `python -m unittest scripts.test.test_validate_data`

## 生成物の扱い
- 取得結果は `data/` 配下に保存し、ワークフロー内でコミットして反映する。

## ワークフロー定義
- `.github/workflows/data_update.yml`
- `.github/workflows/data_backfill.yml`
- `.github/workflows/data_monthly_check.yml`

## 現状の挙動（2026-02-19 時点）

結論:
- 3ワークフローとも「定義ファイルは正しく存在」している。
- ただし、ローカル擬似実行では「完全に問題なし」とは言えない。
- 特に API 通信系（start.gg 取得）は、実行環境のネットワーク制限で検証不能だった。

### 1) `data_update.yml`（毎日 + 手動）

対象ステップ:
- `refresh_users.py`（start.gg API）
- `download.py`（start.gg API）
- `validate_data.py`
- `unittest`

ローカル結果:
- `python -m unittest scripts.test.test_validate_data` は **成功**（3/3）。
- `python scripts/fix/validate_data.py` は **失敗**（`Validation failed: 5969 issues found.`）。
- `download.py` はトークン指定で実行したが、`api.start.gg` の名前解決に失敗（DNS）し、通信検証不可。

判定:
- ワークフローの検証/テスト部分は動く。
- API 取得部分は、このローカル環境では未確認（ネットワーク要因）。
- 現在のデータ品質では `validate_data.py` で落ちるため、現状のままではジョブ失敗の可能性が高い。

### 2) `data_backfill.yml`（手動）

対象ステップ:
- `download.py`（start.gg API）
- `validate_data.py`
- `unittest`

ローカル結果:
- `unittest` は **成功**。
- `validate_data.py` は **失敗**（上記と同様）。
- `download.py` は **DNSエラーで通信検証不可**。

判定:
- バックフィル定義自体は正しい。
- ただし、API 到達性とデータ品質の2点がボトルネック。

### 3) `data_monthly_check.yml`（毎月 + 手動）

対象ステップ:
- `validate_data.py`
- `check_events_in_tournaments.py --apply`

ローカル結果:
- `python scripts/fix/check_events_in_tournaments.py --dry-run` は **未登録イベント6件を検出**して終了コード1。
- `validate_data.py` は **失敗**（多数の欠落/閾値超過）。

判定:
- 月次チェックが「問題を検知する」という意味では期待どおり動作。
- 現在のデータ状態では fail する。

## 問題点

1. API 通信検証が環境依存
- ローカル擬似実行で `api.start.gg` への DNS 解決に失敗し、取得系の真の動作確認ができない。

2. データ検証エラーが大量
- `validate_data.py` が 5969 件の問題を報告（`matches.json` 欠落、ID 欠損率超過など）。
- この状態では `data_update` / `data_backfill` / `data_monthly_check` のいずれも失敗しうる。

3. `tournaments.jsonl` との不整合
- `check_events_in_tournaments.py --dry-run` で未登録イベント6件を検出。
- `--apply` で自動修正可能だが、API問い合わせが必要なケースがある。
