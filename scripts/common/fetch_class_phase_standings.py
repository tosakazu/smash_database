#!/usr/bin/env python3
"""Fetch phase_group standings for class phases (B-class/C-class/etc).

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

from scripts.common.queries import get_phase_group_standings_query
from scripts.common._cli import add_api_args, setup_api
from scripts.common import clock
from scripts.common.utils import (
    write_json_pretty,
    fetch_data_with_retries,
    FetchError,
)

# Event dirs whose class_phases/<pid>.json changed on a --refresh-days re-fetch in this process
# (update_class_data rebuilds their <letter>_virtual/ with --force).
CHANGED_EVENT_DIRS: set = set()


def load_phases(event_dir: Path) -> dict | None:
    fp = event_dir / "phases.json"
    if not fp.exists(): return None
    try:
        return json.loads(fp.read_text())
    except Exception:
        return None


def ended_within(event_dir: Path, days: int) -> bool:
    """Whether the event ended (or started, if it has no end) within the last `days` days."""
    try:
        a = json.loads((event_dir / "attr.json").read_text())
    except Exception:
        return False
    ts = a.get("end_timestamp") or a.get("timestamp")
    return isinstance(ts, (int, float)) and ts >= clock.now_ts() - days * 86400


def standings_part(groups: list) -> list:
    """The part of a class phase file that comes from the standings query (played_user_ids is added later)."""
    return [(g.get("phase_group_id"), g.get("standings")) for g in groups]


def fetch_phase_group_standings(phase_group_id: int, per_page: int = 100, strict: bool = False) -> list[dict]:
    """Fetch all standings for a phase group (paginated). strict: raise FetchError instead of returning a partial list."""
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
            if strict:
                raise
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    add_api_args(parser, max_retries=5, retry_delay=10)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Overwrite existing class_phases/<phase_id>.json")
    parser.add_argument("--refresh-days", type=int, default=0,
                        help="Also re-fetch the existing files of events that ended within this many days, and rewrite one only "
                             "if its standings changed (a class bracket fetched while it was still running). 0 = off")
    parser.add_argument("--events-root", default=None, help="Default: data/startgg/<region>/events")
    parser.add_argument("--region", default=None, help="Region used when --events-root is omitted")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.events_root is None:
        if not args.region:
            parser.error("Either --events-root or --region is required")
        args.events_root = os.path.join("data", "startgg", args.region.replace(" ", "_"), "events")
    setup_api(args)

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
        refresh = False
        if not args.force and out_file.exists():
            if not (args.refresh_days > 0 and ended_within(event_dir, args.refresh_days)):
                n_skip += 1
                continue
            refresh = True
        groups = ph.get("phase_groups") or []
        all_standings = []  # per phase_group
        try:
            for pg in groups:
                pgid = pg.get("id")
                display = pg.get("display")
                if pgid is None: continue
                # a refresh keeps the old file unless every group came back whole
                standings = fetch_phase_group_standings(pgid, per_page=args.per_page, strict=refresh)
                all_standings.append({
                    "phase_group_id": pgid,
                    "display": display,
                    "standings": standings,
                })
        except FetchError:
            n_fail += 1
            continue
        if refresh:
            try:
                old_groups = json.loads(out_file.read_text()).get("phase_groups") or []
            except Exception:
                old_groups = []
            if standings_part(old_groups) == standings_part(all_standings) \
                    or not any(g["standings"] for g in all_standings):   # unchanged, or start.gg returned nothing
                n_skip += 1
                continue
            print(f"  ↻ event={event_id} phase={pid} '{ph.get('name')}': standings changed since the last fetch", flush=True)
            if not args.dry_run:
                CHANGED_EVENT_DIRS.add(event_dir)
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
            write_json_pretty(out_file, out)
        total_entries = sum(len(g["standings"]) for g in all_standings)
        print(f"  ✓ event={event_id} phase={pid} '{ph.get('name')}' → {len(all_standings)} groups, {total_entries} entries", flush=True)
        n_ok += 1

    print(f"\nDone. ok={n_ok} skip={n_skip} fail={n_fail}", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
