#!/usr/bin/env python3
"""Fetch phase_group standings for class phases (Bクラス/Cクラス/etc).

For events with `has_class_phases: true` (from phases.json), iterate through each
class phase's phase_groups and pull standings. Save raw per-phase-group standings
under event_dir/class_phases/<phase_id>.json.

The raw data can later be analyzed to derive B-class internal placement (1st, 2nd,
3-4th tied, etc.) for SPSP scoring.

Usage:
    python3 scripts/fetch/fetch_class_phase_standings.py --token "$STARTGG_TOKEN"
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import get_phase_group_standings_query
from scripts.utils import (
    fetch_data_with_retries,
    set_retry_parameters, set_api_parameters,
    FetchError,
)


def load_phases(event_dir: Path) -> dict | None:
    fp = event_dir / "phases.json"
    if not fp.exists(): return None
    try:
        return json.loads(fp.read_text())
    except Exception:
        return None


def fetch_phase_group_standings(phase_group_id: int, per_page: int = 100) -> list[dict]:
    """Fetch all standings for a phase group (paginated)."""
    page = 1
    out = []
    while True:
        try:
            resp = fetch_data_with_retries(
                get_phase_group_standings_query(),
                {"phaseGroupId": phase_group_id, "page": page, "perPage": per_page},
            )
        except FetchError as e:
            print(f"    fetch fail pg={phase_group_id} page={page}: {e}", flush=True)
            break
        pg = (resp.get("data") or {}).get("phaseGroup")
        if not pg: break
        nodes = ((pg.get("standings") or {}).get("nodes")) or []
        if not nodes: break
        for n in nodes:
            place = n.get("placement")
            ent = n.get("entrant") or {}
            ent_id = ent.get("id")
            ent_name = ent.get("name")
            parts = ent.get("participants") or []
            uid = None
            if parts:
                u = (parts[0] or {}).get("user") or {}
                uid = u.get("id")
            out.append({
                "placement": place,
                "entrant_id": ent_id,
                "entrant_name": ent_name,
                "user_id": uid,
            })
        page_info = (pg.get("standings") or {}).get("pageInfo") or {}
        total_pages = page_info.get("totalPages") or 1
        if page >= total_pages: break
        page += 1
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--max_retries", type=int, default=5)
    parser.add_argument("--retry_delay", type=int, default=10)
    parser.add_argument("--per_page", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Overwrite existing class_phases/<phase_id>.json")
    parser.add_argument("--events_root", default="data/startgg/events/Japan")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    root = Path(args.events_root)
    # Discover all phases.json with class phases
    targets = []
    for pf in root.rglob("phases.json"):
        try:
            d = json.loads(pf.read_text())
        except Exception:
            continue
        if not d.get("has_class_phases"): continue
        event_dir = pf.parent
        for ph in d.get("phases") or []:
            if not ph.get("is_class"): continue
            pid = ph.get("id")
            if pid is None: continue
            targets.append((event_dir, d.get("event_id"), d.get("event_name"), ph))
    print(f"Targets: {len(targets)} class phases across {len(set(t[0] for t in targets))} events", flush=True)
    if args.limit > 0:
        targets = targets[: args.limit]

    n_ok = 0
    n_skip = 0
    n_fail = 0
    for i, (event_dir, event_id, event_name, ph) in enumerate(targets):
        if i % 10 == 0:
            print(f"  [{i}/{len(targets)}] ok={n_ok} skip={n_skip} fail={n_fail}", flush=True)
        pid = ph["id"]
        out_dir = event_dir / "class_phases"
        out_file = out_dir / f"{pid}.json"
        if not args.force and out_file.exists():
            n_skip += 1
            continue
        groups = ph.get("phase_groups") or []
        all_standings = []  # per phase_group
        for pg in groups:
            pgid = pg.get("id")
            display = pg.get("display")
            if pgid is None: continue
            standings = fetch_phase_group_standings(pgid, per_page=args.per_page)
            all_standings.append({
                "phase_group_id": pgid,
                "display": display,
                "standings": standings,
            })
        out = {
            "event_id": event_id,
            "event_name": event_name,
            "phase_id": pid,
            "phase_name": ph.get("name"),
            "phase_order": ph.get("order"),
            "bracket_type": ph.get("bracket_type"),
            "num_seeds": ph.get("num_seeds"),
            "phase_groups": all_standings,
        }
        if not args.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        total_entries = sum(len(g["standings"]) for g in all_standings)
        print(f"  ✓ event={event_id} phase={pid} '{ph.get('name')}' → {len(all_standings)} groups, {total_entries} entries", flush=True)
        n_ok += 1

    print(f"\nDone. ok={n_ok} skip={n_skip} fail={n_fail}", flush=True)


if __name__ == "__main__":
    main()
