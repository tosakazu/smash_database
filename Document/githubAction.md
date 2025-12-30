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
