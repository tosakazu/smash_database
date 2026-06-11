#!/usr/bin/env python3
"""Fetch the set of player_ids who played ≥1 match in each class phase_group.

Scope: only class phase_groups (B-class / C-class / ...) — much smaller than all event sets,
avoiding complexity throttling.

Output: append to event_dir/class_phases/<phase_id>.json a `played_user_ids` field per
phase_group.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import get_phase_group_sets_minimal_query
from scripts.utils import (
    fetch_data_with_retries, set_retry_parameters, set_api_parameters,
    FetchError,
)


def fetch_phase_group_played_uids(phase_group_id: int, per_page: int = 50) -> set[int]:
    """Returns uid set of players with ≥1 valid (completed, non-DQ, non-cancel) match in the phase_group."""
    page = 1
    played = set()
    while True:
        try:
            resp = fetch_data_with_retries(
                get_phase_group_sets_minimal_query(),
                {"phaseGroupId": phase_group_id, "page": page, "perPage": per_page},
            )
        except FetchError as e:
            print(f"    fetch fail pg={phase_group_id} page={page}: {e}", flush=True)
            break
        pg = (resp.get("data") or {}).get("phaseGroup")
        if not pg: break
        nodes = ((pg.get("sets") or {}).get("nodes")) or []
        if not nodes: break
        for node in nodes:
            if node.get("state") != 3: continue
            slots = node.get("slots") or []
            if len(slots) != 2: continue
            slot0, slot1 = slots[0], slots[1]
            st0 = slot0.get("standing") or {}; st1 = slot1.get("standing") or {}
            score0 = ((st0.get("stats") or {}).get("score") or {}).get("value")
            score1 = ((st1.get("stats") or {}).get("score") or {}).get("value")
            if score0 is None: score0 = 0
            if score1 is None: score1 = 0
            if score0 < 0 or score1 < 0: continue  # DQ
            if score0 == 0 and score1 == 0: continue  # cancel
            uids = []
            for s in (slot0, slot1):
                ent = s.get("entrant") or {}
                parts = ent.get("participants") or []
                if not parts:
                    uids.append(None); continue
                u = (parts[0] or {}).get("user") or {}
                uids.append(u.get("id"))
            if uids[0] is None or uids[1] is None: continue
            if uids[0] == uids[1]: continue
            played.add(int(uids[0])); played.add(int(uids[1]))
        page_info = (pg.get("sets") or {}).get("pageInfo") or {}
        total_pages = page_info.get("totalPages") or 1
        if page >= total_pages: break
        page += 1
    return played


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--max_retries", type=int, default=5)
    parser.add_argument("--retry_delay", type=int, default=10)
    parser.add_argument("--per_page", type=int, default=40)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing played_user_ids in phase_group entries")
    parser.add_argument("--events_root",
                        default="data/startgg/events/Japan")
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    root = Path(args.events_root)
    targets = []
    for pf in root.rglob("phases.json"):
        try:
            phd = json.loads(pf.read_text())
        except Exception:
            continue
        if not phd.get("has_class_phases"): continue
        event_dir = pf.parent
        cp_dir = event_dir / "class_phases"
        if not cp_dir.is_dir(): continue
        # 各 class phase の JSON file
        for cp in cp_dir.glob("*.json"):
            if "_virtual" in cp.name: continue
            try:
                cpd = json.loads(cp.read_text())
            except Exception:
                continue
            # Skip if already has played_user_ids for all phase_groups
            pgs = cpd.get("phase_groups") or []
            if not args.force and pgs and all("played_user_ids" in pg for pg in pgs):
                continue
            targets.append((cp, cpd))
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"Targets: {len(targets)} class phase files", flush=True)

    n_ok = 0; n_fail = 0
    for i, (cp_path, cpd) in enumerate(targets):
        if i % 5 == 0:
            print(f"  [{i}/{len(targets)}] ok={n_ok} fail={n_fail}", flush=True)
        try:
            pgs = cpd.get("phase_groups") or []
            for pg in pgs:
                pgid = pg.get("phase_group_id")
                if pgid is None: continue
                if not args.force and "played_user_ids" in pg: continue
                uids = fetch_phase_group_played_uids(int(pgid), per_page=args.per_page)
                pg["played_user_ids"] = sorted(uids)
            cp_path.write_text(json.dumps(cpd, ensure_ascii=False, indent=2))
            n_ok += 1
            phase_name = cpd.get("phase_name", "?")
            total_played = sum(len(pg.get("played_user_ids") or []) for pg in pgs)
            print(f"  ✓ phase={cpd.get('phase_id')} '{phase_name}' → {total_played} played uids", flush=True)
        except Exception as e:
            print(f"  fail {cp_path}: {e}", flush=True)
            n_fail += 1

    print(f"\nDone. ok={n_ok} fail={n_fail}", flush=True)


if __name__ == "__main__":
    main()
