#!/usr/bin/env python3
"""Recompute global_round / global_top_x / global_bracket_label of existing matches.json files.

For fixes to the global bracket position calculation (redownload_matches_v2.compute_phase_global_rounds /
compute_global_top_x) without re-downloading. Uses only what matches.json and standings.json hold.

Current fix (2026-10-27): the LB round direction (start.gg's LB final has the largest |round|; the old code counted
round=-1 as the LB final). Affects class brackets and main LB sets without the loser's placement, plus the
phase-internal losers_top / bracket_label. (The previous fix, the phase progression order, was applied 2026-10-27
to data-Japan e3698caaa0b.)

Safety: an event is rewritten only if recomputing with the *previous* code (legacy_losers) reproduces its current
values exactly (so the only difference is the fix). Events that do not reproduce are listed and left as is.
The labels the ranking build orders by stay as they are: legacy_global_top_x / legacy_global_bracket_label keep
the existing legacy values, or the current values where there were none, and are written only where they differ
from the new labels (redownload_matches_v2.legacy_order_fields: used until the next season).

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
LEGACY_KEYS = ("legacy_global_top_x", "legacy_global_bracket_label")


def _phase_info(m):
    return {"id": m.get("phase_id"), "name": m.get("phase_name") or "", "numSeeds": m.get("phase_num_seeds"),
            "bracketType": m.get("phase_bracket_type"), "phaseOrder": m.get("phase_order")}


def recompute(md: dict, event_dir: Path, progress_key=None, legacy_losers=False) -> list[tuple]:
    """(global_round, global_top_x, global_bracket_label) per match, in matches.json order."""
    ms = md.get("data") or []
    fake = [({"round": m.get("round")}, _phase_info(m), {}) for m in ms]
    cap = md.get("bracket_capacity")
    if cap is None:
        cap = R.effective_bracket_capacity(R._phase_max_numseeds(fake))
    pgi, _ = R.compute_phase_global_rounds(fake, progress_key)
    pl = R._load_placements_map(event_dir)
    return [tuple(R.compute_global_top_x(m.get("round"), _phase_info(m), pgi, cap, pl, m.get("loser_id"),
                                         legacy_losers=legacy_losers)) for m in ms]


def local_losers(ms: list, legacy: bool) -> list[tuple]:
    """(losers_top, bracket_label) of the LB sets (phase-internal; None for other sets)."""
    lmax = {}
    for m in ms:
        r = m.get("round")
        if r is not None and r < 0:
            k = m.get("phase_group_id")
            lmax[k] = max(lmax.get(k, 0), -r)
    out = []
    for m in ms:
        r = m.get("round")
        if r is None or r >= 0:
            out.append(None)
            continue
        x = R.losers_top_x_legacy(r) if legacy else R.losers_top_x(r, lmax.get(m.get("phase_group_id")))
        out.append((x, f"Losers TOP {x}" if x is not None else None))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path, help="events directory (e.g. data/startgg/Japan/events)")
    ap.add_argument("--write", action="store_true", help="rewrite the files (default: report only)")
    args = ap.parse_args(argv)
    n_events = n_changed = n_matches = n_local = 0
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
        cur_local = [(m.get("losers_top"), m.get("bracket_label")) if l is not None else None
                     for m, l in zip(ms, local_losers(ms, True))]
        new = recompute(md, f.parent)
        new_local = local_losers(ms, False)
        if new == cur and new_local == cur_local:
            continue
        if recompute(md, f.parent, legacy_losers=True) != cur or local_losers(ms, True) != cur_local:
            skipped.append(str(f.relative_to(args.root)))
            continue
        n_changed += 1
        n_matches += sum(1 for a, b in zip(cur, new) if a != b)
        n_local += sum(1 for a, b in zip(cur_local, new_local) if a != b)
        if args.write:
            for m, g, c, loc in zip(ms, new, cur, new_local):
                order = (m.get(LEGACY_KEYS[0]), m.get(LEGACY_KEYS[1])) if LEGACY_KEYS[1] in m else c[1:]
                m.update(zip(KEYS, g))
                for k in LEGACY_KEYS:
                    m.pop(k, None)
                if order != g[1:]:
                    m.update(zip(LEGACY_KEYS, order))
                if loc is not None:
                    m["losers_top"], m["bracket_label"] = loc
            indent = 2 if raw.startswith("{\n") else None
            f.write_text(json.dumps(md, ensure_ascii=False, indent=indent) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    print(f"events {n_events}, changed {n_changed} ({n_matches} matches, phase-internal {n_local})"
          f"{' (written)' if args.write else ''}, "
          f"skipped (current values do not reproduce) {len(skipped)}")
    for s in skipped[:50]:
        print("  skip", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
