#!/usr/bin/env bash
# run_region.sh — run one region's ingest with a single command (meant to be called daily from cron).
#
#   bash scripts/common/run_region.sh --country-code US [--days 14] [--token-file PATH]
#        [--python PATH] [--log-dir DIR] [--no-commit] [--no-push] [--dry-run] [--check]
#
# Steps: [1] download → [2] upcoming → [3] class brackets → [4] classify (derived.json)
#       → [5] validate (detect spikes in issue counts) → [6] commit data/startgg/<region>/ and push to data-<region>
#
# The region is derived from the country code (US / CA / MX / DO → North_America). Run from the repository
# root with that region's data branch (data-<region>) checked out. On any other branch nothing is
# committed (so scripts and other regions' data are never swept in).
# Japan is run by the spsp nightly (deploy/update_and_deploy.sh), so this script is not used for it.
#
# Token:    env var STARTGG_TOKEN, else --token-file (default: ./STARTGG_TOKEN, ~/.config/smash_database/STARTGG_TOKEN).
#          The value is never written to the log or argv.
# Log:      appended to <region>_<date>.log under --log-dir (default ~/.local/log/smash_database/). Deleted after 60 days.
# Locking:  if a run for the same region is already in progress, exit without doing anything (flock).
# Failure:  stop (rc≠0) if download or classify fails. Download can resume from done.csv, so it is
#          retried from where it left off up to --download-retries (default 2) times. Validation only WARNs, never stops.
#
# A summary (tournaments/events fetched, classify updates, commit) is printed at the end.
# --check:  only verify the environment and exit (Python / deps / token / branch / GitHub / start.gg API). Use after first setup.
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
[[ -n "$COUNTRY" ]] || { echo "ERROR: --country-code is required (e.g. --country-code US)" >&2; exit 2; }
[[ -d scripts/common && -e .git ]] || { echo "ERROR: run from the repository root" >&2; exit 2; }

# ── token (value is never printed) ──
if [[ -z "${STARTGG_TOKEN:-}" ]]; then
  for f in "$TOKEN_FILE" ./STARTGG_TOKEN "$HOME/.config/smash_database/STARTGG_TOKEN"; do
    [[ -n "$f" && -s "$f" ]] && { STARTGG_TOKEN="$(tr -d '[:space:]' < "$f")"; break; }
  done
fi
if [[ -z "${STARTGG_TOKEN:-}" && $DRY -eq 0 && $CHECK -eq 0 ]]; then
  echo "ERROR: STARTGG_TOKEN not found (env var, or --token-file / ./STARTGG_TOKEN / ~/.config/smash_database/STARTGG_TOKEN)" >&2; exit 2
fi
export STARTGG_TOKEN="${STARTGG_TOKEN:-dry-run}"
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:Unverified HTTPS request}"   # keep the verify=False warnings from utils from flooding the log

# ── region, branch, dates ──
REGION="$("$PY" -c "from scripts.common.utils import country_code2region as f; print(f('$COUNTRY').replace(' ', '_'))")" || exit 2
[[ -n "$REGION" && "$REGION" != "Other" ]] || { echo "ERROR: no region for $COUNTRY (add it to country_code2region in scripts/common/utils.py)" >&2; exit 2; }
BRANCH="data-$REGION"; DATA_DIR="data/startgg/$REGION"
START="$(date +%F)"; FINISH="$(date -d "-${DAYS} days" +%F 2>/dev/null || date -v-"${DAYS}"d +%F)"

# ── --check: verify the environment only ──
if [[ $CHECK -eq 1 ]]; then
  ok=1
  say() { printf '  %-10s %s\n' "$1" "$2"; }
  pyver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || { say "NG" "python does not run: $PY"; exit 1; }
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' && say "ok" "python $pyver ($PY)" || { say "NG" "python $pyver is too old (need 3.10+)"; ok=0; }
  "$PY" -c 'import requests' 2>/dev/null && say "ok" "requests" || { say "NG" "requests missing (pip install -r requirements.txt)"; ok=0; }
  command -v flock >/dev/null && say "ok" "flock" || { say "NG" "flock missing (util-linux)"; ok=0; }
  [[ "$STARTGG_TOKEN" != "dry-run" && -n "$STARTGG_TOKEN" ]] && say "ok" "token (${#STARTGG_TOKEN} chars)" || { say "NG" "STARTGG_TOKEN not found"; ok=0; }
  say "ok" "region $REGION ← $COUNTRY (branch $BRANCH, dir $DATA_DIR)"
  cur="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"; [[ "$cur" == "$BRANCH" ]] && say "ok" "branch $cur" || { say "WARN" "branch is $cur (must be on $BRANCH to commit)"; }
  "$PY" -c "import scripts.$REGION.classify as m; print(m.CLASSIFIER_VERSION, m.TIMEZONE)" >/dev/null 2>&1 && say "ok" "scripts/$REGION/classify.py" || { say "NG" "cannot load scripts/$REGION/classify.py"; ok=0; }
  git ls-remote -q --exit-code origin "refs/heads/$BRANCH" >/dev/null 2>&1 && say "ok" "origin/$BRANCH reachable" || say "WARN" "origin/$BRANCH missing or unreachable (normal before the first push)"
  if [[ "$STARTGG_TOKEN" != "dry-run" ]]; then
    "$PY" - <<'PYEOF' && say "ok" "start.gg API" || { say "NG" "cannot reach start.gg API (token / network)"; ok=0; }
import os, sys, warnings; warnings.simplefilter("ignore")
from scripts.common.utils import set_api_parameters, set_retry_parameters, fetch_data_with_retries
set_api_parameters("https://api.start.gg/gql/alpha", os.environ["STARTGG_TOKEN"]); set_retry_parameters(1, 2)
r = fetch_data_with_retries("query { videogame(id: 1386) { name } }", {})
sys.exit(0 if ((r or {}).get("data") or {}).get("videogame") else 1)
PYEOF
  fi
  [[ $ok -eq 1 ]] && echo "→ ready" || { echo "→ not ready (see NG lines)"; exit 1; }
  exit 0
fi

# ── log (to both screen and file; process substitution is unavailable on some hosts, so re-exec self through tee) ──
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${REGION}_$(date +%F).log"
if [[ -z "${_RUN_REGION_INNER:-}" ]]; then
  find "$LOG_DIR" -name '*.log' -mtime +60 -delete 2>/dev/null
  _RUN_REGION_INNER=1 STARTGG_TOKEN="$STARTGG_TOKEN" bash "$0" "${ORIG_ARGS[@]}" 2>&1 | tee -a "$LOG"
  exit "${PIPESTATUS[0]}"
fi
# ── concurrency guard (do nothing if a run for the same region is in progress) ──
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
  echo "  events: $EV0 → $(count_events)   tournaments.jsonl: $TJ0 → $(count_lines "$DATA_DIR/tournaments.jsonl") rows   users.jsonl: $US0 → $(count_lines "$DATA_DIR/users.jsonl") rows"
  [[ -n "${COMMIT:-}" ]] && echo "  commit: $COMMIT"
  if [[ ${#FAILED[@]} -gt 0 ]]; then echo "  ⚠️  steps that failed (run continued):"; for s in "${FAILED[@]}"; do echo "     - $s"; done; fi
  exit $rc
}
trap finish EXIT

echo "═══ $REGION ($COUNTRY) start $(date '+%F %T')  window $FINISH to $START  branch=$(git rev-parse --abbrev-ref HEAD) ═══"
EV0=$(count_events); TJ0=$(count_lines "$DATA_DIR/tournaments.jsonl"); US0=$(count_lines "$DATA_DIR/users.jsonl")

step 1/6 "download (resumes from done.csv; retried up to $RETRIES times on failure)"
AWAITING=()   # pass the awaiting-resume registry if present (listed events are re-fetched every run until a winner exists)
[[ -s "$DATA_DIR/manual/awaiting_resume.json" ]] && AWAITING=(--awaiting-file "$DATA_DIR/manual/awaiting_resume.json")
ok=0
DL_OUT="$(mktemp)"   # download.py prints per-tournament FetchError lines and still exits 0; count them for the summary
for ((i = 0; i <= RETRIES; i++)); do
  if RUN "$PY" -u scripts/common/download.py --country-code "$COUNTRY" --start-date "$START" --finish-date "$FINISH" "${AWAITING[@]}" 2>&1 | tee -a "$DL_OUT"; [[ ${PIPESTATUS[0]} -eq 0 ]]; then ok=1; break; fi
  echo "  download rc≠0 (try $((i + 1))/$((RETRIES + 1)))"; sleep 30
done
DL_ERRS=$(grep -c "FetchError" "$DL_OUT" || true); rm -f "$DL_OUT"
[[ $ok -eq 1 ]] || { echo "ERROR: download failed $((RETRIES + 1)) times. Progress in done.csv is kept; next run resumes from there" >&2; exit 1; }
if [[ "$DL_ERRS" -gt 0 ]]; then
  echo "  WARN: $DL_ERRS FetchError line(s) during download (those tournaments are not marked done and are retried next run; see the 'FetchError on tournament' lines above)"
  FAILED+=("download: $DL_ERRS FetchError line(s) (those tournaments are retried next run; see the log)")
fi

step 2/6 "upcoming tournaments ($DATA_DIR/upcoming.json)"
RUN "$PY" -u scripts/common/fetch_upcoming.py --country "$COUNTRY" --region "$REGION" \
  || { echo "  WARN: fetch_upcoming failed (previous upcoming.json kept)"; FAILED+=("fetch_upcoming (upcoming list is from the previous run)"); }

step 3/6 "class brackets (only if the region handles them)"
RUN "$PY" -u scripts/common/update_class_data.py --region "$REGION" --since-days "$DAYS" \
  || { echo "  WARN: update_class_data failed (class separation deferred to next run)"; FAILED+=("update_class_data (class brackets deferred to next run)"); }

step 4/6 "classify (derived.json / users_derived.jsonl)"
RUN "$PY" -u scripts/common/derive.py --region "$REGION" \
  || { echo "ERROR: derive failed — derived.json incomplete, not committing" >&2; exit 1; }

step 5/6 "validate (check known inconsistencies have not spiked; WARN only, never stops)"
if [[ $DRY -eq 1 ]]; then
  echo "+ $PY -u scripts/common/fix/validate_data.py --region $REGION --baseline $DATA_DIR/validation_baseline.json"
else
  "$PY" -u scripts/common/fix/validate_data.py --region "$REGION" --baseline "$DATA_DIR/validation_baseline.json" \
    || { echo "  WARN: inconsistencies grew beyond baseline (see REGRESSION lines above)"; FAILED+=("validate_data (inconsistencies grew beyond baseline)"); }
fi

step 6/6 "commit / push"
if [[ $DO_COMMIT -eq 0 ]]; then echo "  → --no-commit (check the diff with git status)"; exit 0; fi
CUR="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CUR" != "$BRANCH" ]]; then
  echo "ERROR: currently on $CUR. Data is committed only on $BRANCH (git switch $BRANCH, then rerun)" >&2; exit 3
fi
if [[ -z "$(git status --porcelain -- "$DATA_DIR")" ]]; then echo "  → no changes"; exit 0; fi
RUN git add -A -- "$DATA_DIR"                      # never touch other regions or scripts
RUN git commit -q -m "data: $(date +%F) $REGION" || { echo "ERROR: commit failed" >&2; exit 1; }
COMMIT="$(git rev-parse --short HEAD)"
if [[ $DO_PUSH -eq 1 ]]; then
  RUN git push -q origin "$BRANCH:$BRANCH" && echo "  → pushed ($BRANCH $COMMIT)" \
    || { echo "  WARN: push failed (commit done; git pull --rebase origin $BRANCH and retry next run)"; FAILED+=("push (commit $COMMIT is done)"); }
else
  echo "  → --no-push (commit $COMMIT only)"
fi
