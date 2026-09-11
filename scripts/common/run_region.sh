#!/usr/bin/env bash
# run_region.sh — 1 地域ぶんの取り込みを 1 コマンドで回す (cron から毎日呼ぶ想定)。
#
#   bash scripts/common/run_region.sh --country-code US [--days 14] [--token-file PATH]
#        [--python PATH] [--log-dir DIR] [--no-commit] [--no-push] [--dry-run] [--check]
#
# 手順: [1] ダウンロード → [2] 開催予定 → [3] クラス bracket → [4] 判定 (derived.json)
#       → [5] 検査 (件数の急増検知) → [6] data/startgg/<地域>/ を commit して data-<地域> へ push
#
# 地域はカントリーコードから決まる (US / CA / MX / DO → North_America)。リポジトリのルートで、
# その地域のデータブランチ (data-<地域>) を checkout した状態で実行する。他のブランチにいると
# commit しない (スクリプトや他地域のデータを巻き込まないため)。
# 日本は spsp 側の nightly (deploy/update_and_deploy.sh) が回すので、このスクリプトは使わない。
#
# トークン: 環境変数 STARTGG_TOKEN、無ければ --token-file (既定: ./STARTGG_TOKEN、~/.config/smash_database/STARTGG_TOKEN)。
#          値はログにも argv にも出さない。
# ログ:     --log-dir (既定 ~/.local/log/smash_database/) に <地域>_<日付>.log を追記。60 日で消す。
# 多重起動: 同じ地域の実行が走っていれば何もせず終わる (flock)。
# 失敗:     ダウンロードと判定が失敗したら止める (rc≠0)。ダウンロードは done.csv で再開できるので、
#          --download-retries (既定 2) 回まで続きからやり直す。検査は WARN だけで止めない。
#
# 出力の最後に要約 (取得した大会/イベント数、判定の更新数、commit) を出す。
# --check:  環境の確認だけして終わる (Python / 依存 / トークン / ブランチ / GitHub / start.gg API)。初回セットアップ後に。
set -uo pipefail

ORIG_ARGS=("$@")
COUNTRY=""; DAYS=14; DO_COMMIT=1; DO_PUSH=1; DRY=0; CHECK=0; TOKEN_FILE=""; PY="${PYTHON:-python3}"
LOG_DIR="${SMASH_DB_LOG_DIR:-$HOME/.local/log/smash_database}"; RETRIES=2
while [[ $# -gt 0 ]]; do
  case "$1" in
    --country-code) COUNTRY="$2"; shift 2;;
    --days) DAYS="$2"; shift 2;;
    --token-file) TOKEN_FILE="$2"; shift 2;;
    --python) PY="$2"; shift 2;;
    --log-dir) LOG_DIR="$2"; shift 2;;
    --download-retries) RETRIES="$2"; shift 2;;
    --no-commit) DO_COMMIT=0; DO_PUSH=0; shift;;
    --no-push) DO_PUSH=0; shift;;
    --dry-run) DRY=1; shift;;
    --check) CHECK=1; shift;;
    -h|--help) sed -n '2,24p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$COUNTRY" ]] || { echo "ERROR: --country-code が要る (例: --country-code US)" >&2; exit 2; }
[[ -d scripts/common && -e .git ]] || { echo "ERROR: リポジトリのルートで実行すること" >&2; exit 2; }

# ── トークン (値は出さない) ──
if [[ -z "${STARTGG_TOKEN:-}" ]]; then
  for f in "$TOKEN_FILE" ./STARTGG_TOKEN "$HOME/.config/smash_database/STARTGG_TOKEN"; do
    [[ -n "$f" && -s "$f" ]] && { STARTGG_TOKEN="$(tr -d '[:space:]' < "$f")"; break; }
  done
fi
if [[ -z "${STARTGG_TOKEN:-}" && $DRY -eq 0 && $CHECK -eq 0 ]]; then
  echo "ERROR: STARTGG_TOKEN が無い (環境変数か --token-file / ./STARTGG_TOKEN / ~/.config/smash_database/STARTGG_TOKEN)" >&2; exit 2
fi
export STARTGG_TOKEN="${STARTGG_TOKEN:-dry-run}"
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:Unverified HTTPS request}"   # utils の verify=False の警告でログが埋まるのを防ぐ

# ── 地域・ブランチ・日付 ──
REGION="$("$PY" -c "from scripts.common.utils import country_code2region as f; print(f('$COUNTRY').replace(' ', '_'))")" || exit 2
[[ -n "$REGION" && "$REGION" != "Other" ]] || { echo "ERROR: $COUNTRY の地域が決まらない (scripts/common/utils.py の country_code2region に足す)" >&2; exit 2; }
BRANCH="data-$REGION"; DATA_DIR="data/startgg/$REGION"
START="$(date +%F)"; FINISH="$(date -d "-${DAYS} days" +%F 2>/dev/null || date -v-"${DAYS}"d +%F)"

# ── --check: 環境の確認だけ ──
if [[ $CHECK -eq 1 ]]; then
  ok=1
  say() { printf '  %-10s %s\n' "$1" "$2"; }
  pyver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || { say "NG" "python が動かない: $PY"; exit 1; }
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' && say "ok" "python $pyver ($PY)" || { say "NG" "python $pyver は古い (3.10 以上)"; ok=0; }
  "$PY" -c 'import requests' 2>/dev/null && say "ok" "requests" || { say "NG" "requests が無い (pip install -r requirements.txt)"; ok=0; }
  command -v flock >/dev/null && say "ok" "flock" || { say "NG" "flock が無い (util-linux)"; ok=0; }
  [[ "$STARTGG_TOKEN" != "dry-run" && -n "$STARTGG_TOKEN" ]] && say "ok" "token (${#STARTGG_TOKEN} 文字)" || { say "NG" "STARTGG_TOKEN が見つからない"; ok=0; }
  say "ok" "region $REGION ← $COUNTRY (branch $BRANCH, dir $DATA_DIR)"
  cur="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"; [[ "$cur" == "$BRANCH" ]] && say "ok" "branch $cur" || { say "WARN" "branch は $cur (commit するには $BRANCH にいること)"; }
  "$PY" -c "import scripts.$REGION.classify as m; print(m.CLASSIFIER_VERSION, m.TIMEZONE)" >/dev/null 2>&1 && say "ok" "scripts/$REGION/classify.py" || { say "NG" "scripts/$REGION/classify.py が読めない"; ok=0; }
  git ls-remote -q --exit-code origin "refs/heads/$BRANCH" >/dev/null 2>&1 && say "ok" "origin/$BRANCH に到達できる" || say "WARN" "origin/$BRANCH が無いか到達できない (初回 push 前なら正常)"
  if [[ "$STARTGG_TOKEN" != "dry-run" ]]; then
    "$PY" - <<'PYEOF' && say "ok" "start.gg API" || { say "NG" "start.gg API に届かない (トークン / ネットワーク)"; ok=0; }
import os, sys, warnings; warnings.simplefilter("ignore")
from scripts.common.utils import set_api_parameters, set_retry_parameters, fetch_data_with_retries
set_api_parameters("https://api.start.gg/gql/alpha", os.environ["STARTGG_TOKEN"]); set_retry_parameters(1, 2)
r = fetch_data_with_retries("query { videogame(id: 1386) { name } }", {})
sys.exit(0 if ((r or {}).get("data") or {}).get("videogame") else 1)
PYEOF
  fi
  [[ $ok -eq 1 ]] && echo "→ 準備できている" || { echo "→ NG がある"; exit 1; }
  exit 0
fi

# ── ログ (画面とファイルの両方へ。プロセス置換が使えない環境があるので自分を tee 越しに呼び直す) ──
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${REGION}_$(date +%F).log"
if [[ -z "${_RUN_REGION_INNER:-}" ]]; then
  find "$LOG_DIR" -name '*.log' -mtime +60 -delete 2>/dev/null
  _RUN_REGION_INNER=1 STARTGG_TOKEN="$STARTGG_TOKEN" bash "$0" "${ORIG_ARGS[@]}" 2>&1 | tee -a "$LOG"
  exit "${PIPESTATUS[0]}"
fi
# ── 多重起動ガード (同じ地域の実行が走っていれば何もしない) ──
exec 9>"$LOG_DIR/.${REGION}.lock"
if ! flock -n 9; then echo "$(date '+%F %T') $REGION: already running — skip"; exit 0; fi

FAILED=()
T0=$(date +%s)
step() { echo; echo "[$1] $(date '+%H:%M:%S') $2"; }
RUN() { echo "+ $*"; [[ $DRY -eq 1 ]] || "$@"; }
count_events() { find "$DATA_DIR/events" -name attr.json 2>/dev/null | wc -l | tr -d ' '; }
count_lines() { [[ -f "$1" ]] && wc -l < "$1" | tr -d ' ' || echo 0; }
finish() {
  rc=$?
  echo
  echo "═══ $REGION ($COUNTRY) $(date '+%F %T')  rc=$rc  $(( $(date +%s) - T0 ))s ═══"
  echo "  events: $EV0 → $(count_events)   tournaments.jsonl: $TJ0 → $(count_lines "$DATA_DIR/tournaments.jsonl") 行   users.jsonl: $US0 → $(count_lines "$DATA_DIR/users.jsonl") 行"
  [[ -n "${COMMIT:-}" ]] && echo "  commit: $COMMIT"
  if [[ ${#FAILED[@]} -gt 0 ]]; then echo "  ⚠️  失敗したが続行したステップ:"; for s in "${FAILED[@]}"; do echo "     - $s"; done; fi
  exit $rc
}
trap finish EXIT

echo "═══ $REGION ($COUNTRY) 開始 $(date '+%F %T')  窓 $FINISH 〜 $START  branch=$(git rev-parse --abbrev-ref HEAD) ═══"
EV0=$(count_events); TJ0=$(count_lines "$DATA_DIR/tournaments.jsonl"); US0=$(count_lines "$DATA_DIR/users.jsonl")

step 1/6 "ダウンロード (done.csv を見て続きから。失敗したら $RETRIES 回までやり直す)"
AWAITING=()   # 再開待ちの登録簿があれば渡す (載っている event は優勝者が出るまで毎回取り直す)
[[ -s "$DATA_DIR/manual/awaiting_resume.json" ]] && AWAITING=(--awaiting-file "$DATA_DIR/manual/awaiting_resume.json")
ok=0
for ((i = 0; i <= RETRIES; i++)); do
  if RUN "$PY" -u scripts/common/download.py --country-code "$COUNTRY" --start-date "$START" --finish-date "$FINISH" "${AWAITING[@]}"; then ok=1; break; fi
  echo "  download rc≠0 (try $((i + 1))/$((RETRIES + 1)))"; sleep 30
done
[[ $ok -eq 1 ]] || { echo "ERROR: ダウンロードが $((RETRIES + 1)) 回失敗。done.csv の分は残っているので次回続きから" >&2; exit 1; }

step 2/6 "開催予定 ($DATA_DIR/upcoming.json)"
RUN "$PY" -u scripts/common/fetch_upcoming.py --country "$COUNTRY" --region "$REGION" \
  || { echo "  WARN: fetch_upcoming failed (前回分のまま)"; FAILED+=("fetch_upcoming (開催予定は前回分)"); }

step 3/6 "クラス bracket (地域が扱う場合のみ)"
RUN "$PY" -u scripts/common/update_class_data.py --region "$REGION" --since-days "$DAYS" \
  || { echo "  WARN: update_class_data failed (クラス分離は次回)"; FAILED+=("update_class_data (クラス bracket は次回)"); }

step 4/6 "判定 (derived.json / users_derived.jsonl)"
RUN "$PY" -u scripts/common/derive.py --region "$REGION" \
  || { echo "ERROR: derive failed — derived.json が揃わないので commit しない" >&2; exit 1; }

step 5/6 "検査 (既知の不整合が急増していないか。WARN だけで止めない)"
if [[ $DRY -eq 1 ]]; then
  echo "+ $PY -u scripts/common/fix/validate_data.py --region $REGION --baseline $DATA_DIR/validation_baseline.json"
else
  "$PY" -u scripts/common/fix/validate_data.py --region "$REGION" --baseline "$DATA_DIR/validation_baseline.json" \
    || { echo "  WARN: 不整合が baseline より増えている (上の REGRESSION 行を見る)"; FAILED+=("validate_data (不整合が baseline より増加)"); }
fi

step 6/6 "commit / push"
if [[ $DO_COMMIT -eq 0 ]]; then echo "  → --no-commit (git status で差分を確認)"; exit 0; fi
CUR="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CUR" != "$BRANCH" ]]; then
  echo "ERROR: いま $CUR にいる。データは $BRANCH にだけ commit する (git switch $BRANCH してから再実行)" >&2; exit 3
fi
if [[ -z "$(git status --porcelain -- "$DATA_DIR")" ]]; then echo "  → 変化なし"; exit 0; fi
RUN git add -A -- "$DATA_DIR"                      # 他地域とスクリプトには触らない
RUN git commit -q -m "data: $(date +%F) $REGION" || { echo "ERROR: commit failed" >&2; exit 1; }
COMMIT="$(git rev-parse --short HEAD)"
if [[ $DO_PUSH -eq 1 ]]; then
  RUN git push -q origin "$BRANCH:$BRANCH" && echo "  → push 済 ($BRANCH $COMMIT)" \
    || { echo "  WARN: push failed (commit は済み。git pull --rebase origin $BRANCH して次回)"; FAILED+=("push (commit $COMMIT は済み)"); }
else
  echo "  → --no-push (commit $COMMIT のみ)"
fi
