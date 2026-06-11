#!/usr/bin/env python3
"""Fetch event phase metadata and save to event_dir/phases.json.

For events where lower-class brackets (B-class / C-class etc) exist as PHASES
within a single Singles event (e.g., 篝火#15), we need the phase structure to
properly clip standings at the main-bracket cutoff.

Usage:
    python3 scripts/fetch/fetch_event_phases.py --token "$STARTGG_TOKEN" \\
        --since 2023-01-01 --min-entrants 200
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import get_event_phases_named_query
from scripts.utils import (
    read_tournaments_jsonl, fetch_data_with_retries,
    set_retry_parameters, set_api_parameters,
    FetchError,
)


def load_attr(event_path: str) -> dict | None:
    try:
        with open(os.path.join(event_path, "attr.json"), "rb") as f:
            return json.loads(f.read())
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--since", default="2023-01-01")
    parser.add_argument("--min-entrants", type=int, default=200,
                        help="Only fetch events with at least this many entrants (class brackets typically exist only in large events)")
    parser.add_argument("--region", default="Japan")
    parser.add_argument("--tournament_file_path", default="data/startgg/tournaments.jsonl")
    parser.add_argument("--max_retries", type=int, default=5)
    parser.add_argument("--retry_delay", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Overwrite existing phases.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--event-dirs-file", default=None,
                        help="JSON file with a list of event dir paths to target explicitly "
                             "(bypasses since/min-entrants filters)")
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    targets = []
    if args.event_dirs_file:
        with open(args.event_dirs_file) as f:
            dirs = json.load(f)
        for ep in dirs:
            if not os.path.isdir(ep):
                continue
            attr = load_attr(ep)
            if not attr:
                continue
            phases_path = os.path.join(ep, "phases.json")
            if not args.force and os.path.exists(phases_path):
                continue
            event_id = attr.get("event_id")
            if event_id is None:
                continue
            targets.append((attr.get("timestamp") or 0, event_id, ep,
                            attr.get("tournament_name", ""), attr.get("event_name", ""),
                            attr.get("num_entrants", 0) or 0))
    else:
        cutoff_ts = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp())
        print(f"Loading tournaments.jsonl ...", flush=True)
        tournaments = read_tournaments_jsonl(args.tournament_file_path)

        # Build target list: events from Japan tournaments post-cutoff with >= min-entrants
        for tid, entry in tournaments.items():
            for ev in entry.get("events") or []:
                ep = ev.get("path", "")
                if not ep or not os.path.isdir(ep):
                    continue
                attr = load_attr(ep)
                if not attr:
                    continue
                ts = attr.get("timestamp")
                if ts is None or ts < cutoff_ts:
                    continue
                region = (attr.get("region") or "")
                if args.region and region != args.region:
                    continue
                nent = attr.get("num_entrants", 0) or 0
                if nent < args.min_entrants:
                    continue
                phases_path = os.path.join(ep, "phases.json")
                if not args.force and os.path.exists(phases_path):
                    continue
                event_id = attr.get("event_id")
                if event_id is None:
                    continue
                targets.append((ts, event_id, ep, attr.get("tournament_name", ""), attr.get("event_name", ""), nent))
    targets.sort()
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"Targets: {len(targets)} events to fetch phases (min_entrants={args.min_entrants})", flush=True)

    n_ok = 0
    n_class = 0
    n_fail = 0
    for i, (ts, event_id, ep, tname, ename, nent) in enumerate(targets):
        if i % 50 == 0:
            print(f"  [{i}/{len(targets)}] ok={n_ok} class={n_class} fail={n_fail}", flush=True)
        try:
            resp = fetch_data_with_retries(get_event_phases_named_query(), {"eventId": event_id})
        except FetchError as e:
            print(f"  fetch fail event_id={event_id} '{ename}': {e}", flush=True)
            n_fail += 1
            continue
        ev_data = (resp.get("data") or {}).get("event")
        if not ev_data:
            n_fail += 1
            continue
        phases = ev_data.get("phases") or []
        # Detect class phases
        import re
        CLASS_PAT = re.compile(r'[BCDEＢＣＤＥ]\s*[-_\s]*[Cc]lass|[BCDEＢＣＤＥ]\s*クラス', re.IGNORECASE)
        has_class = any(CLASS_PAT.search(p.get("name") or "") for p in phases)
        if has_class:
            n_class += 1
        # Save phases.json
        out = {
            "event_id": event_id,
            "event_name": ev_data.get("name"),
            "has_class_phases": has_class,
            "phases": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "order": p.get("phaseOrder"),
                    "bracket_type": p.get("bracketType"),
                    "num_seeds": p.get("numSeeds"),
                    "is_class": bool(CLASS_PAT.search(p.get("name") or "")),
                    "phase_groups": [
                        {"id": pg.get("id"), "display": pg.get("displayIdentifier")}
                        for pg in ((p.get("phaseGroups") or {}).get("nodes") or [])
                    ],
                }
                for p in phases
            ],
        }
        if not args.dry_run:
            with open(os.path.join(ep, "phases.json"), "w") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
        n_ok += 1
        if has_class:
            class_phase_names = [p.get("name") for p in phases if CLASS_PAT.search(p.get("name") or "")]
            print(f"  CLASS event_id={event_id} '{tname} / {ename}' ({nent} ent): {class_phase_names}", flush=True)

    print(f"\nDone. ok={n_ok} class={n_class} fail={n_fail}", flush=True)


if __name__ == "__main__":
    main()
