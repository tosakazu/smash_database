#!/usr/bin/env bash
# run_region.sh — 1 地域ぶんの取り込みを 1 コマンドで回す (ダウンロード → 判定 → 検査 → commit → push)。
#
#   STARTGG_TOKEN=... bash scripts/common/run_region.sh --country-code US [--days 14]
#                                                       [--no-commit] [--no-push] [--dry-run]
#
# 地域はカントリーコードから決まる (US/CA/MX → North_America)。リポジトリのルートで実行すること。
# 日本は spsp の deploy/update_and_deploy.sh が回すので、このスクリプトは使わない。
#
# 途中で失敗したら止まる。ダウンロードは done.csv を見て再実行できるので、失敗しても次回が続きから拾う。
set -euo pipefail

COUNTRY=""; DAYS=14; DO_COMMIT=1; DO_PUSH=1; DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --country-code) COUNTRY="$2"; shift 2;;
    --days) DAYS="$2"; shift 2;;
    --no-commit) DO_COMMIT=0; DO_PUSH=0; shift;;
    --no-push) DO_PUSH=0; shift;;
    --dry-run) DRY=1; shift;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$COUNTRY" ]] || { echo "ERROR: --country-code が要る (例: --country-code US)" >&2; exit 2; }
[[ -d scripts/common && -e .git ]] || { echo "ERROR: リポジトリのルートで実行すること" >&2; exit 2; }
: "${STARTGG_TOKEN:?ERROR: STARTGG_TOKEN が未設定}"

PY="${PYTHON:-python3}"
REGION="$("$PY" -c "from scripts.common.utils import country_code2region as f; print(f('$COUNTRY').replace(' ', '_'))")"
[[ -n "$REGION" && "$REGION" != "Other" ]] || { echo "ERROR: $COUNTRY の地域が決まらない (utils.country_code2region を直す)" >&2; exit 2; }
BRANCH="data-$REGION"
START="$(date +%F)"
FINISH="$(date -d "-${DAYS} days" +%F)"
RUN() { echo "+ $*"; [[ $DRY -eq 1 ]] || "$@"; }

echo "=== $REGION ($COUNTRY): $FINISH 〜 $START ==="

echo "[1/6] ダウンロード"
RUN "$PY" -u scripts/common/download.py --country-code "$COUNTRY" --start-date "$START" --finish-date "$FINISH"

echo "[2/6] 開催予定の取得 (data/startgg/<地域>/upcoming.json)"
RUN "$PY" -u scripts/common/fetch_upcoming.py --country "$COUNTRY" --region "$REGION"

echo "[3/6] クラス bracket (地域が扱う場合のみ。扱わない地域では即終了する)"
RUN "$PY" -u scripts/common/update_class_data.py --region "$REGION" --since-days "$DAYS"

echo "[4/6] 判定 (derived.json / users_derived.jsonl)"
RUN "$PY" -u scripts/common/derive.py --region "$REGION"

echo "[5/6] 検査 (既知の不整合が急増していないか。失敗しても止めない)"
if [[ $DRY -eq 1 ]]; then
  echo "+ $PY -u scripts/common/fix/validate_data.py --region $REGION --baseline data/startgg/$REGION/validation_baseline.json"
else
  "$PY" -u scripts/common/fix/validate_data.py --region "$REGION" \
      --baseline "data/startgg/$REGION/validation_baseline.json" || echo "  WARN: 不整合が baseline より増えている (上の REGRESSION 行を見る)"
fi

echo "[6/6] commit / push"
if [[ $DO_COMMIT -eq 0 ]]; then
  echo "  → --no-commit なので何もしない (git status で差分を確認)"
  exit 0
fi
CUR="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CUR" != "$BRANCH" ]]; then
  echo "  ERROR: いま $CUR にいる。データは $BRANCH にだけ commit する (git switch $BRANCH してから再実行)" >&2
  exit 3
fi
if [[ -z "$(git status --porcelain -- "data/startgg/$REGION")" ]]; then
  echo "  → 変化なし"
  exit 0
fi
RUN git add -A -- "data/startgg/$REGION"      # 他地域とスクリプトには触らない
RUN git commit -q -m "data: $(date +%F) $REGION"
if [[ $DO_PUSH -eq 1 ]]; then
  RUN git push -q origin "$BRANCH:$BRANCH"
  echo "  → push 済 ($BRANCH)"
else
  echo "  → --no-push なので commit だけ"
fi
