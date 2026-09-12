#!/usr/bin/env python3
"""Derive class-internal placements from class_phase data and create virtual
sub-tournament files for SPSP ingestion.

For each event with class_phases/<phase_id>.json files:
1. Group class phases by class letter (B/C/D/E) via phase name pattern.
2. For each class letter, compute each participant's internal class placement
   by taking their deepest (smallest num_seeds) phase + their placement there.
3. Rank participants by (deepest_phase_num_seeds, placement_in_phase) — tied
   players get the same B-class placement (= position of first tied player).
4. Filter the event's matches.json to only matches within the corresponding
   class phase groups (via phase displayIdentifier mapping).
5. Save as virtual sub-tournament directory:
     <event_dir>/class_phases/<letter>_virtual/{attr.json, standings.json, matches.json}
   The directory is later loaded as a normal Tournament by data_loader.py.

Run from the smash_db_tournament directory.
"""
import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from scripts.common.utils import write_json_pretty, write_json_compact  # noqa: E402


# Extracting the class letter and naming are owned by the region module (main bundles it into _CLS)
_CLS = None


def _class_letter(name: str) -> str | None:
    return _CLS.class_letter(name)


def _class_index(letter: str) -> int:
    """Offset for the virtual event ID / timestamp. Determined by the order of the region's CLASS_LETTERS
    (Japan: B/C/D/E = 1/2/3/4, the same values as the old character-code-based scheme)."""
    try:
        return _CLS.CLASS_LETTERS.index(letter) + 1
    except ValueError:
        return 0


def load_class_phase_files(event_dir: Path) -> list[dict]:
    cp_dir = event_dir / "class_phases"
    if not cp_dir.is_dir(): return []
    out = []
    for fp in cp_dir.glob("*.json"):
        # Skip our own virtual outputs
        if fp.parent.name.endswith("_virtual"): continue
        if "_virtual" in fp.name: continue
        try:
            d = json.loads(fp.read_text())
            if "phase_groups" not in d: continue
            out.append(d)
        except Exception:
            continue
    return out


def derive_class_placements(
    class_phase_data: list[dict],
    played_in_class: set[int] | None = None,
) -> tuple[dict[int, int], int]:
    """Returns (uid → class internal placement) + num_entrants.

    played_in_class (DQ handling, optional):
      uid set of players who played >= 1 match in this class. When given,
      uids not in this set are treated as phantom entries and excluded. >= 1 match counts as full participation.
      None means standings-based as before.
    """
    # Per player: (smallest num_seeds, placement_in_phase) is best
    player_best = {}  # uid -> (num_seeds, placement)
    for ph in class_phase_data:
        num_seeds = ph.get("num_seeds")
        if not num_seeds: continue
        for pg in ph.get("phase_groups") or []:
            for s in pg.get("standings") or []:
                uid = s.get("user_id")
                placement = s.get("placement")
                if uid is None or placement is None: continue
                # DQ filter: exclude players with zero matches in the B-class
                if played_in_class is not None and uid not in played_in_class:
                    continue
                key = (num_seeds, placement)
                if uid not in player_best or key < player_best[uid]:
                    player_best[uid] = key
    if not player_best:
        return {}, 0
    # Sort by depth_score, assign bucket_start placement to tied groups
    sorted_p = sorted(player_best.items(), key=lambda x: x[1])
    result = {}
    pos = 1
    prev_key = None
    bucket_start = 1
    for uid, key in sorted_p:
        if key != prev_key:
            bucket_start = pos
            prev_key = key
        result[uid] = bucket_start
        pos += 1
    # num_entrants = largest phase size in this class (true entry count before filtering)
    num_entrants = max(ph.get("num_seeds", 0) or 0 for ph in class_phase_data)
    return result, num_entrants


def build_played_in_class_set(class_phase_data: list[dict]) -> set[int]:
    """Returns: uid set who have ≥1 match in ANY of the given class phases.

    Uses `played_user_ids` field embedded per phase_group in class_phases/<phase_id>.json
    (populated by fetch_class_phase_players.py). Returns an empty set if the field is absent.
    """
    result: set[int] = set()
    any_data = False
    for ph in class_phase_data:
        for pg in ph.get("phase_groups") or []:
            uids = pg.get("played_user_ids")
            if uids is None: continue
            any_data = True
            for u in uids:
                if u is not None:
                    result.add(int(u))
    return result if any_data else set()


def get_played_player_ids(event_dir: Path) -> set[int]:
    """Return the set of player_ids with >= 1 match in the parent event's matches.json.
    Used for DQ / no-show detection (= players absent from matches are also dropped from the virtual standings).
    """
    mp = event_dir / "matches.json"
    if not mp.exists(): return set()
    try:
        d = json.loads(mp.read_text())
    except Exception:
        return set()
    items = d if isinstance(d, list) else (d.get("data") or [])
    played = set()
    for m in items:
        if not isinstance(m, dict): continue
        # Same conditions as the parent event's data_loader (state==3, not DQ, not cancel)
        if m.get("state") != 3: continue
        if m.get("dq") or m.get("cancel"): continue
        wid = m.get("winner_id"); lid = m.get("loser_id")
        if wid is None or lid is None: continue
        if wid == lid: continue
        played.add(int(wid)); played.add(int(lid))
    return played


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root",
                        default=None, help="Default: data/startgg/<region>/events")
    parser.add_argument("--region", required=True, help="Select the classifier module scripts/<region>/classify.py")
    parser.add_argument("--force", action="store_true", help="Overwrite existing virtual files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    global _CLS
    from scripts.common.region import load_region_classifier, class_support
    _CLS = class_support(load_region_classifier(args.region))
    if _CLS is None:
        print(f"Region {args.region} does not handle class brackets — nothing to do", flush=True)
        return 0
    if args.events_root is None:
        args.events_root = os.path.join("data", "startgg", args.region.replace(" ", "_"), "events")

    root = Path(args.events_root)
    n_events = 0
    n_virtual = 0
    n_skip = 0
    for event_attr in root.rglob("attr.json"):
        # Skip virtual dirs
        if "_virtual" in str(event_attr): continue
        event_dir = event_attr.parent
        phases_file = event_dir / "phases.json"
        if not phases_file.exists(): continue
        try:
            phases_data = json.loads(phases_file.read_text())
        except Exception:
            continue
        if not phases_data.get("has_class_phases"): continue
        # An event whose phases are all is_class is itself a class tournament (= the B-class
        # is run as a separate event). Creating a virtual would duplicate it entirely, so skip.
        _phs = phases_data.get("phases") or []
        if _phs and all(p.get("is_class") for p in _phs):
            continue
        n_events += 1
        # Load class phase standing data
        cp_data = load_class_phase_files(event_dir)
        if not cp_data: continue
        # Group by class letter
        by_letter = defaultdict(list)
        for ph in cp_data:
            letter = _class_letter(ph.get("phase_name") or "")
            if letter:
                by_letter[letter].append(ph)
        # Load original event attr for inheritance
        try:
            ev_attr = json.loads(event_attr.read_text())
        except Exception:
            continue
        # DQ filter: keep only players with >= 1 match in matches.json (drop no-shows)
        played_uids = get_played_player_ids(event_dir)
        for letter, ph_list in by_letter.items():
            # Set of players with >= 1 match in this class (for DQ exclusion; used when played_user_ids
            # is embedded in the class phase JSON, otherwise no filter).
            played_in_class = build_played_in_class_set(ph_list)
            placements, num_ent = derive_class_placements(
                ph_list, played_in_class=(played_in_class or None)
            )
            if not placements: continue
            # Just in case: also drop players with zero matches in the whole event
            if played_uids:
                placements = {uid: p for uid, p in placements.items() if uid in played_uids}
                if not placements: continue
            virt_dir = event_dir / "class_phases" / f"{letter}_virtual"
            virt_attr = virt_dir / "attr.json"
            if not args.force and virt_attr.exists():
                n_skip += 1
                continue
            # Build standings (after DQ filter; no renumbering of placement needed — keeping bucket_start is fine)
            standings = [
                {"placement": p, "user_id": uid}
                for uid, p in sorted(placements.items(), key=lambda x: x[1])
            ]
            # Virtual tournament: empty matches (BT is handled entirely by the parent event).
            # The parent event's matches.json already includes the B-class matches, and BT learning runs there.
            # → Processing matches on the virtual side would double-count, so leave it empty.
            # When is_class_virtual=True, data_loader / build skip the DQ filter and BT learning
            # and only run TJPR (placement scoring).
            matches = []
            # Build attr (inherit from parent event)
            # Lower classes are processed after the main bracket (= they are held after the main bracket that day).
            # Shift the timestamp by +1s per letter so sorting always places them after the parent event
            # (B=+1s, C=+2s, ...). The JST date does not change.
            _ts = ev_attr.get("timestamp")
            _idx = _class_index(letter)
            _ts_virtual = (_ts + _idx) if isinstance(_ts, (int, float)) else _ts
            virt_event_id = -(int(ev_attr.get("event_id") or 0) * 10 + _idx)
            virt_attr_d = {
                "event_id": virt_event_id,  # synthetic negative ID
                "tournament_name": ev_attr.get("tournament_name", ""),
                "event_name": _CLS.class_virtual_event_name(ev_attr.get('event_name', 'Singles'), letter),
                "region": ev_attr.get("region"),
                "place": ev_attr.get("place"),
                "num_entrants": num_ent,
                "offline": ev_attr.get("offline", True),
                "status": "completed",
                "timestamp": _ts_virtual,
                "end_timestamp": ev_attr.get("end_timestamp"),
                "version": "1.0",
                "url": ev_attr.get("url"),
            }
            # The virtual flag and class letter are carried by the directory name (class_phases/<letter>_virtual).
            # attr.json holds only start.gg-derived values; derive.py writes the classification to derived.json (since 2026-09-09)
            if args.dry_run:
                print(f"DRY: would write {virt_dir.relative_to(root)} ({len(standings)} std, {len(matches)} matches)")
                n_virtual += 1
                continue
            virt_dir.mkdir(parents=True, exist_ok=True)
            write_json_pretty(virt_attr, virt_attr_d)
            write_json_compact(virt_dir / "standings.json", standings)   # bare list (data_loader accepts both)
            write_json_compact(virt_dir / "matches.json", matches)
            n_virtual += 1
    print(f"events_with_class_phases={n_events}, virtual_written={n_virtual}, skipped={n_skip}")


if __name__ == "__main__":
    sys.exit(main() or 0)
