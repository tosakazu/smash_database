#!/usr/bin/env python3
"""Update driver for class-bracket data (separate lower-tier brackets inside a tournament).

Detects events from the last N days where matches.json has a class phase but
phases.json does not mark it is_class, and runs them through a 4-stage pipeline:
  1. fetch_event_phases.py --event-dirs-file ...   (creates phases.json)
  2. fetch_class_phase_standings.py               (class_phases/<pid>.json)
  3. fetch_class_phase_players.py                 (appends played_user_ids)
  4. build_class_virtual_tournaments.py           (creates <letter>_virtual/)

Stages 2-4 are idempotent (existing output is skipped), so a full scan is cheap.
Background: phases.json originally came only from manual backfill of tournaments with 200+ entrants,
leaving class brackets unseparated in 501 small/mid-size tournaments (found 2026-06-11).

Class names and their existence vary by region (Japan = B/C/D/E classes, NA = Amateur etc.), so
name detection belongs to the region module (scripts/<region>/classify.py). Regions without a declaration do nothing.

Usage (from the smash_db_tournament directory):
  STARTGG_TOKEN=... python3 scripts/common/update_class_data.py --region Japan [--since-days 30]
"""
from __future__ import annotations

import os
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from scripts.common import clock  # noqa: E402
from scripts.common.region import load_region_classifier, class_support  # noqa: E402


def events_root_for(region: str) -> Path:
    return ROOT / "data" / "startgg" / region.replace(" ", "_") / "events"


def detect_unseparated(since_days: int, events_root: Path, is_class_name) -> list[str]:
    """Return event dirs from the last since_days days whose class phases are not marked.
    is_class_name: the region's function that says whether a phase name is a class bracket."""
    events_root = Path(events_root)
    cutoff = clock.today() - dt.timedelta(days=since_days)
    out = []
    for ydir in sorted(events_root.iterdir()):
        if not ydir.is_dir() or not ydir.name.isdigit():
            continue
        for mdir in sorted(ydir.iterdir()):
            if not mdir.is_dir() or not mdir.name.isdigit():
                continue
            for ddir in sorted(mdir.iterdir()):
                if not ddir.is_dir() or not ddir.name.isdigit():
                    continue
                try:
                    d = dt.date(int(ydir.name), int(mdir.name), int(ddir.name))
                except ValueError:
                    continue
                if d < cutoff:
                    continue
                for mp in ddir.rglob("matches.json"):
                    ed = mp.parent
                    if "_virtual" in str(ed):
                        continue
                    try:
                        md = json.loads(mp.read_bytes())
                    except Exception:
                        continue
                    ms = md.get("data", md) if isinstance(md, dict) else md
                    if not isinstance(ms, list):
                        continue
                    class_phases = {m.get("phase_name") for m in ms
                                    if isinstance(m, dict) and m.get("phase_name")
                                    and is_class_name(m.get("phase_name"))}
                    if not class_phases:
                        continue
                    marked = set()
                    pj = ed / "phases.json"
                    if pj.exists():
                        try:
                            pd_ = json.loads(pj.read_bytes())
                            marked = {p.get("name") for p in (pd_.get("phases") or [])
                                      if p.get("is_class")}
                        except Exception:
                            pass
                    if class_phases - marked:
                        out.append(str(ed))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.environ.get("STARTGG_TOKEN"), help="start.gg API token (default: env var STARTGG_TOKEN)")
    ap.add_argument("--region", required=True, help="selects the classifier module scripts/<region>/classify.py")
    ap.add_argument("--since-days", type=int, default=30)
    ap.add_argument("--events-root", default=None, help="default data/startgg/<region>/events (a temp dir in tests)")
    args = ap.parse_args(argv)
    clf = load_region_classifier(args.region)
    cls = class_support(clf)
    if cls is None:
        print(f"[update_class_data] region {args.region} does not handle class brackets (no declaration in classify.py) — nothing to do", flush=True)
        return 0
    if args.events_root is None:
        args.events_root = str(events_root_for(args.region))
    if not args.token:
        raise SystemExit("ERROR: --token or env var STARTGG_TOKEN is required (prefer env; keep the value out of argv)")
    os.environ["STARTGG_TOKEN"] = args.token   # children (main of fetch_*.py) read it from env (kept out of argv)
    events_root = Path(args.events_root)

    # Previously the 4 children were launched as subprocesses with the target list passed via a file in the system tmp (left behind, never cleaned).
    # Now they are called in-process in the same order. Each main sets up its own API layer (token from env).
    from scripts.common import (fetch_event_phases, fetch_class_phase_standings, fetch_class_phase_players,
                                build_class_virtual_tournaments)
    dirs = detect_unseparated(args.since_days, events_root, cls.is_unseparated_class_phase)
    print(f"[update_class_data] unseparated class events (last {args.since_days}d): {len(dirs)}", flush=True)
    rc = 0
    if dirs:
        print("+ fetch_event_phases ...", flush=True)
        rc |= fetch_event_phases.main(["--event-dirs-file", "-", "--region", args.region], event_dirs=dirs) or 0
    # Stages 2-4 are idempotent (existing output skipped), so run them over everything each time
    print("+ fetch_class_phase_standings ...", flush=True)
    rc |= fetch_class_phase_standings.main(["--events-root", str(events_root), "--region", args.region]) or 0
    print("+ fetch_class_phase_players ...", flush=True)
    rc |= fetch_class_phase_players.main(["--events-root", str(events_root), "--region", args.region]) or 0
    print("+ build_class_virtual_tournaments ...", flush=True)
    rc |= build_class_virtual_tournaments.main(["--events-root", str(events_root), "--region", args.region]) or 0
    print(f"[update_class_data] done rc={rc}", flush=True)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
