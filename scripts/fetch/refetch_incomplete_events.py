#!/usr/bin/env python3
"""順位表/試合データが不完全なイベントを一括再取得する.

対象 (ローカルデータから自動検出):
  A. standings に place 1 (優勝者) が無いイベント (= 大会途中スナップショット疑い)
  B. standings の 25%+ が試合ゼロのイベント (= matches 部分欠損疑い, nent>=16)

各イベントについて standings + matches を start.gg から取り直す.
- 取得失敗 (FetchError) や新データが明らかに退化している場合は元ファイルを温存
- 元ファイルは --backup-dir に退避してから上書き

使い方:
  python3 scripts/fetch/refetch_incomplete_events.py --token <T> [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import set_api_parameters, set_retry_parameters, FetchError  # noqa: E402
from scripts.fetch.download import download_standings, extend_user_info, _flush_dirty_users  # noqa: E402
from scripts.utils import read_users_jsonl  # noqa: E402
from scripts.fetch.redownload_matches_v2 import refetch_event, API_DELAY_SEC  # noqa: E402

EVENTS_ROOT = ROOT / "data" / "startgg" / "events" / "Japan"
USERS_PATH = ROOT / "data" / "startgg" / "users.jsonl"


def detect_targets():
    """(eid, event_dir, reason, date_str, name) のリストを返す."""
    targets = []
    for attr_path in EVENTS_ROOT.rglob("attr.json"):
        try:
            d = json.loads(attr_path.read_bytes())
            eid = int(d.get("event_id"))
        except Exception:
            continue
        if eid < 0:
            continue
        ne = d.get("num_entrants") or 0
        if ne < 8:
            continue
        try:
            sd = json.loads((attr_path.parent / "standings.json").read_bytes())
        except Exception:
            continue
        rows = sd.get("data", sd)
        if not isinstance(rows, list) or not rows:
            continue
        places = [p for p in (r.get("placement") for r in rows) if isinstance(p, int)]
        uids = {r.get("user_id") for r in rows if r.get("user_id") is not None}
        reason = None
        if places and min(places) != 1:
            reason = "no_champion"
        elif ne >= 16:
            try:
                md = json.loads((attr_path.parent / "matches.json").read_bytes())
                ms = md.get("data", md)
            except Exception:
                ms = []
            played = set()
            for m in ms:
                played.add(m.get("winner_id")); played.add(m.get("loser_id"))
            if uids and len(uids - played) / len(uids) >= 0.25:
                reason = "matchless_25pct"
        if reason:
            ts = d.get("timestamp") or 0
            date = dt.datetime.fromtimestamp(ts).date().isoformat() if ts else "?"
            targets.append((eid, attr_path.parent, reason, date,
                            (d.get("tournament_name") or "")[:40]))
    targets.sort(key=lambda x: x[3])
    return targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--url", default="https://api.start.gg/gql/alpha")
    ap.add_argument("--backup-dir", type=Path,
                    default=Path("/tmp/refetch_incomplete_backup"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    set_retry_parameters(5, 10)
    set_api_parameters(args.url, args.token)

    targets = detect_targets()
    if args.limit:
        targets = targets[: args.limit]
    print(f"targets: {len(targets)}", flush=True)
    for t in targets:
        print(f"  {t[3]} eid={t[0]} [{t[2]}] {t[4]}", flush=True)
    if args.dry_run:
        return

    users = read_users_jsonl(str(USERS_PATH))
    dirty_user_ids: set = set()

    n_std_changed = n_match_changed = n_fail = n_unchanged = 0
    for i, (eid, event_dir, reason, date, name) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {date} eid={eid} {name} ({reason})", flush=True)
        bdir = args.backup_dir / str(eid)
        bdir.mkdir(parents=True, exist_ok=True)
        old_std = (event_dir / "standings.json").read_bytes() if (event_dir / "standings.json").exists() else None
        old_mat = (event_dir / "matches.json").read_bytes() if (event_dir / "matches.json").exists() else None
        if old_std: (bdir / "standings.json").write_bytes(old_std)
        if old_mat: (bdir / "matches.json").write_bytes(old_mat)

        # ── standings ──
        try:
            user_data, player_data, _ = download_standings(eid, str(event_dir))
            new_rows = json.loads((event_dir / "standings.json").read_bytes()).get("data") or []
            old_rows = (json.loads(old_std).get("data") or []) if old_std else []
            if old_std and len(new_rows) < max(8, len(old_rows) // 2):
                # 明らかな退化 (event リセット等) → 復元
                (event_dir / "standings.json").write_bytes(old_std)
                print("   standings: DEGRADED → restored", flush=True)
            elif json.dumps(new_rows, sort_keys=True) != json.dumps(old_rows, sort_keys=True):
                n_std_changed += 1
                extend_user_info(user_data, player_data, users, str(USERS_PATH),
                                 dirty_user_ids=dirty_user_ids)
                print(f"   standings: CHANGED ({len(old_rows)} → {len(new_rows)} rows)", flush=True)
            else:
                print("   standings: unchanged", flush=True)
        except FetchError as e:
            print(f"   standings: FETCH FAIL ({str(e)[:120]})", flush=True)
            if old_std:
                (event_dir / "standings.json").write_bytes(old_std)
            n_fail += 1
            continue
        time.sleep(API_DELAY_SEC)

        # ── matches ──
        try:
            old_cnt = len((json.loads(old_mat).get("data") or [])) if old_mat else 0
            new_cnt, _ = refetch_event(eid, event_dir, per_page=50)
            if new_cnt != old_cnt:
                n_match_changed += 1
                print(f"   matches: {old_cnt} → {new_cnt}", flush=True)
            else:
                print("   matches: unchanged", flush=True)
        except FetchError as e:
            print(f"   matches: FETCH FAIL ({str(e)[:120]})", flush=True)
            if old_mat:
                (event_dir / "matches.json").write_bytes(old_mat)
            n_fail += 1
        time.sleep(API_DELAY_SEC)
        if not (n_std_changed and n_match_changed):
            n_unchanged += 0  # 集計は最後にまとめて出す

    _flush_dirty_users(users, dirty_user_ids, str(USERS_PATH))
    print(f"\nDone. standings_changed={n_std_changed} matches_changed={n_match_changed} "
          f"fail={n_fail} / total={len(targets)}", flush=True)
    print(f"backup: {args.backup_dir}", flush=True)


if __name__ == "__main__":
    main()
