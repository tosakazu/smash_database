#!/usr/bin/env python3
"""Recompute global_round / global_top_x / global_bracket_label of existing matches.json files.

For fixes to the global bracket position calculation (redownload_matches_v2.compute_phase_global_rounds /
compute_global_top_x) without re-downloading. Uses only what matches.json and standings.json hold.

Safety: an event is rewritten only if recomputing with the *previous* phase order reproduces its current
values exactly (so the only difference is the fix). Events that do not reproduce are listed and left as is.

    python scripts/common/fix/relabel_global_brackets.py data/startgg/Japan/events [--write]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.common import redownload_matches_v2 as R  # noqa: E402

KEYS = ("global_round", "global_top_x", "global_bracket_label")


def _phase_info(m):
    return {"id": m.get("phase_id"), "name": m.get("phase_name") or "", "numSeeds": m.get("phase_num_seeds"),
            "bracketType": m.get("phase_bracket_type"), "phaseOrder": m.get("phase_order")}


def recompute(md: dict, event_dir: Path, progress_key=None) -> list[tuple]:
    """(global_round, global_top_x, global_bracket_label) per match, in matches.json order."""
    ms = md.get("data") or []
    fake = [({"round": m.get("round")}, _phase_info(m), {}) for m in ms]
    cap = md.get("bracket_capacity")
    if cap is None:
        cap = R.effective_bracket_capacity(R._phase_max_numseeds(fake))
    saved = R._phase_progress_key
    if progress_key is not None:
        R._phase_progress_key = progress_key
    try:
        pgi, _ = R.compute_phase_global_rounds(fake)
    finally:
        R._phase_progress_key = saved
    pl = R._load_placements_map(event_dir)
    return [tuple(R.compute_global_top_x(m.get("round"), _phase_info(m), pgi, cap, pl, m.get("loser_id"))) for m in ms]


def _old_key(by_phase, pid):   # the order before the fix (phaseOrder only)
    return (by_phase[pid]["phase_order"], pid)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path, help="events directory (e.g. data/startgg/Japan/events)")
    ap.add_argument("--write", action="store_true", help="rewrite the files (default: report only)")
    args = ap.parse_args(argv)
    n_events = n_changed = n_matches = 0
    skipped = []
    for f in sorted(args.root.glob("**/matches.json")):
        try:
            raw = f.read_text(encoding="utf-8")
            md = json.loads(raw)
        except Exception:
            continue
        if not isinstance(md, dict) or not md.get("data"):
            continue
        n_events += 1
        ms = md["data"]
        cur = [tuple(m.get(k) for k in KEYS) for m in ms]
        new = recompute(md, f.parent)
        if new == cur:
            continue
        if recompute(md, f.parent, _old_key) != cur:
            skipped.append(str(f.relative_to(args.root)))
            continue
        n_changed += 1
        n_matches += sum(1 for a, b in zip(cur, new) if a != b)
        if args.write:
            for m, g in zip(ms, new):
                m.update(zip(KEYS, g))
            indent = 2 if raw.startswith("{\n") else None
            f.write_text(json.dumps(md, ensure_ascii=False, indent=indent) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    print(f"events {n_events}, changed {n_changed} ({n_matches} matches){' (written)' if args.write else ''}, "
          f"skipped (current values do not reproduce) {len(skipped)}")
    for s in skipped[:50]:
        print("  skip", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
