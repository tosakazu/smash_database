#!/usr/bin/env python3
"""Backfill wave_start_at into existing matches.json without re-fetching matches.

Approach:
  1. Iterate every event with matches.json
  2. Query EventPhasesFull (= cheap: 1 query/event, no sets)
  3. Build wave_id → wave_start_at map for that event
  4. For each match in matches.json, fill in 'wave_start_at' if wave_id matches
  5. Save back. State file enables resume.

実行 (smash_db_tournament/ root から):
    python3 -m scripts.fetch.backfill_wave_start_at \
        --token "$STARTGG_TOKEN" \
        --root data/startgg/events/Japan \
        --state /tmp/backfill_wave_state.json

Notes:
  - phase_group_start_at は同じクエリで取れるので一緒に埋める.
  - 既に wave_start_at が入ってる match は skip.
  - 1 query/event なので 4297 events で ~70-80min 程度想定 (= rate-limit 込み).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent  # smash_db_tournament/
sys.path.insert(0, str(REPO_ROOT))

from scripts.utils import set_api_parameters, fetch_data_with_retries
from scripts.queries import get_event_phases_full_query


def fetch_wave_map(event_id):
    """Return (wave_map, pg_map) where:
      wave_map: wave_id (int) → startAt (Unix int) or None
      pg_map: phase_group_id (int) → startAt (Unix int) or None
    """
    q = get_event_phases_full_query()
    resp = fetch_data_with_retries(q, {"eventId": int(event_id)})
    if not resp or "data" not in resp:
        return {}, {}
    event = (resp.get("data") or {}).get("event") or {}
    phases = event.get("phases") or []
    wave_map = {}
    pg_map = {}
    for ph in phases:
        for pg in (ph.get("phaseGroups") or {}).get("nodes") or []:
            pg_id = pg.get("id")
            if pg_id is not None:
                pg_map[int(pg_id)] = pg.get("startAt")
            wave = pg.get("wave") or {}
            wid = wave.get("id")
            if wid is not None:
                # First-write wins; identical wave_id should have identical startAt.
                wave_map.setdefault(int(wid), wave.get("startAt"))
    return wave_map, pg_map


def patch_matches(matches_path: Path, wave_map: dict, pg_map: dict):
    """Patch matches.json in place. Return (patched_count, total_matches)."""
    try:
        d = json.loads(matches_path.read_bytes())
    except Exception:
        return 0, 0
    if not isinstance(d, dict):
        return 0, 0
    matches = d.get("data") or []
    if not isinstance(matches, list):
        return 0, 0
    patched = 0
    for m in matches:
        if not isinstance(m, dict):
            continue
        wid = m.get("wave_id")
        if wid is not None and m.get("wave_start_at") is None:
            sa = wave_map.get(int(wid))
            if sa is not None:
                m["wave_start_at"] = sa
                patched += 1
        pgid = m.get("phase_group_id")
        if pgid is not None and m.get("phase_group_start_at") is None:
            sa = pg_map.get(int(pgid))
            if sa is not None:
                m["phase_group_start_at"] = sa
                # don't double-count patched (= already counted via wave); track separately if needed
    matches_path.write_text(json.dumps(d, ensure_ascii=False))
    return patched, len(matches)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="start.gg API token")
    ap.add_argument("--root", required=True, help="events root (e.g., data/startgg/events/Japan)")
    ap.add_argument("--state", default="/tmp/backfill_wave_state.json", help="resume state file")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between API calls")
    ap.add_argument("--api_url", default="https://api.start.gg/gql/alpha")
    args = ap.parse_args()

    set_api_parameters(args.api_url, args.token)

    root = Path(args.root).resolve()
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {"done": []}
    done = set(state["done"])

    # Collect targets: event_dir → event_id
    targets = []
    for attr in root.rglob("attr.json"):
        try:
            a = json.loads(attr.read_bytes())
            eid = a.get("event_id")
            if eid is None:
                continue
            eid = int(eid)
        except Exception:
            continue
        if eid in done:
            continue
        ed = attr.parent
        if not (ed / "matches.json").exists():
            continue
        targets.append((eid, ed))

    print(f"[backfill_wave] total events: {len(targets) + len(done)}; remaining: {len(targets)}", flush=True)

    start = time.time()
    total_patched = 0
    for i, (eid, ed) in enumerate(targets):
        retries = 0
        while True:
            try:
                wave_map, pg_map = fetch_wave_map(eid)
                # Skip events with no wave/pg startAt info entirely
                if not wave_map and not pg_map:
                    done.add(eid)
                    break
                patched, total = patch_matches(ed / "matches.json", wave_map, pg_map)
                total_patched += patched
                if (i + 1) % 50 == 0 or patched > 0:
                    elapsed = time.time() - start
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    eta_min = (len(targets) - i - 1) / rate / 60 if rate > 0 else 0
                    print(
                        f"  [{i+1}/{len(targets)}] ev={eid} waves={len(wave_map)} pgs={len(pg_map)} "
                        f"patched={patched}/{total} cum_patched={total_patched} (ETA {eta_min:.1f}m)",
                        flush=True,
                    )
                done.add(eid)
                state["done"] = sorted(done)
                state_path.write_text(json.dumps(state))
                break
            except KeyboardInterrupt:
                print("\n[interrupted] state saved.", flush=True)
                state["done"] = sorted(done)
                state_path.write_text(json.dumps(state))
                sys.exit(0)
            except Exception as ex:
                msg = str(ex)[:200]
                retries += 1
                if retries > 5:
                    print(f"  [{i+1}/{len(targets)}] ev={eid} GIVE UP: {msg}", flush=True)
                    done.add(eid)
                    state["done"] = sorted(done)
                    state_path.write_text(json.dumps(state))
                    break
                wait = min(60, 5 * 2 ** retries)
                print(f"  [{i+1}/{len(targets)}] ev={eid} retry {retries} after {wait}s: {msg}", flush=True)
                time.sleep(wait)
        time.sleep(args.delay)

    print(f"\n[DONE] events_done={len(done)} total_match_patched={total_patched}", flush=True)


if __name__ == "__main__":
    main()
