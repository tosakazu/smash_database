# GitHub Actions（想定）

## 目的
- 定期的に start.gg から最新データを取得し、`data/` を更新する。

## トリガー
- `schedule`（例: 1日1回）
- `workflow_dispatch`

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
5. `data/` をコミット or アーティファクト化

## 想定コマンド
- 全体取得
  - `python scripts/download.py --token "$STARTGG_TOKEN" --game_id "$STARTGG_GAME_ID" --country_code "$STARTGG_COUNTRY_CODE" --openai_api_key "$OPENAI_API_KEY"`
- 欠損補完
  - `python scripts/check_and_fill_missing.py --token "$STARTGG_TOKEN" --openai_api_key "$OPENAI_API_KEY"`

## 生成物の扱い
- 取得結果は `data/` 配下に保存。
- 運用方針として以下のいずれかを選ぶ想定。
  - コミットしてリポジトリへ反映
  - GitHub Actions のアーティファクトとして保存
