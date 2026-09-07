#!/usr/bin/env python3
"""Fetch event matches with phase_group_id annotation.

The original matches.json only stores phaseGroup.displayIdentifier ('E1', '1', ...) which
collides across phases within an event (e.g., '1' is reused by 9 phases in 九龍 #16).
For proper B-class participation detection, we need phase_group_id (unique).

Saves to event_dir/matches_phased.json (parallel to matches.json) with each match having:
  { winner_id, loser_id, phase_group_id, phase_group_display }

Targets events that have phases.json (= candidates for class virtual sub-tournaments).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.common.queries import get_event_sets_query
from scripts.common.utils import (
    fetch_all_nodes, set_retry_parameters, set_api_parameters,
    FetchError,
)


def load_attr(event_dir: Path) -> dict | None:
    try:
        return json.loads((event_dir / "attr.json").read_text())
    except Exception:
        return None


def load_phases(event_dir: Path) -> dict | None:
    try:
        return json.loads((event_dir / "phases.json").read_text())
    except Exception:
        return None


def get_entrant_user_map_from_seeds(event_dir: Path) -> dict[int, int]:
    """seeds.json から entrant_id → user_id を作る."""
    try:
        d = json.loads((event_dir / "seeds.json").read_text())
    except Exception:
        return {}
    items = d if isinstance(d, list) else (d.get("data") or [])
    out = {}
    for it in items:
        if not isinstance(it, dict): continue
        eid = it.get("entrant_id")
        uid = it.get("user_id")
        if eid is not None and uid is not None:
            out[int(eid)] = int(uid)
    return out


def fetch_event_sets(event_id: int, per_page: int = 50) -> list[dict] | None:
    query = get_event_sets_query()
    variables = {"eventId": event_id}
    keys = ["event", "sets"]
    try:
        return fetch_all_nodes(query, variables, keys, per_page=per_page)
    except FetchError as e:
        print(f"  fetch fail event_id={event_id}: {e}", flush=True)
        return None


def _slot_user_id(slot: dict) -> int | None:
    """slots[].entrant.participants[].user.id を直接取り出す."""
    if not slot: return None
    ent = slot.get("entrant") or {}
    parts = ent.get("participants") or []
    if not parts: return None
    u = (parts[0] or {}).get("user") or {}
    uid = u.get("id")
    return int(uid) if uid is not None else None


def extract_match_records(all_sets: list[dict]) -> list[dict]:
    """get_event_sets_query 結果から matches を抽出、phase_group_id 付き."""
    out = []
    for node in all_sets:
        if not isinstance(node, dict): continue
        slots = node.get("slots") or []
        if len(slots) != 2: continue
        slot0, slot1 = slots[0], slots[1]
        if not slot0 or not slot1: continue
        st0 = slot0.get("standing") or {}; st1 = slot1.get("standing") or {}
        score0 = ((st0.get("stats") or {}).get("score") or {}).get("value")
        score1 = ((st1.get("stats") or {}).get("score") or {}).get("value")
        if score0 is None: score0 = 0
        if score1 is None: score1 = 0
        winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot is slot0 else slot0
        wid = _slot_user_id(winner_slot)
        lid = _slot_user_id(loser_slot)
        if wid is None or lid is None: continue
        if wid == lid: continue
        dq = (score0 < 0 or score1 < 0)
        cancel = (score0 == 0 and score1 == 0)
        pg = node.get("phaseGroup") or {}
        out.append({
            "winner_id": wid,
            "loser_id": lid,
            "state": node.get("state"),
            "dq": dq,
            "cancel": cancel,
            "round": node.get("round"),
            "phase_group_id": pg.get("id"),
            "phase_group_display": pg.get("displayIdentifier"),
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=int, default=10)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--events-root",
                        default="data/startgg/Japan/events")
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    root = Path(args.events_root)
    targets = []
    for pf in root.rglob("phases.json"):
        try:
            d = json.loads(pf.read_text())
        except Exception:
            continue
        if not d.get("has_class_phases"): continue
        event_dir = pf.parent
        attr = load_attr(event_dir)
        if not attr: continue
        ev_id = attr.get("event_id")
        if ev_id is None: continue
        out_file = event_dir / "matches_phased.json"
        if not args.force and out_file.exists():
            continue
        targets.append((event_dir, int(ev_id), attr.get("tournament_name", ""), attr.get("event_name", "")))
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"Targets: {len(targets)} events", flush=True)

    n_ok = 0; n_fail = 0
    for i, (event_dir, ev_id, tname, ename) in enumerate(targets):
        if i % 5 == 0:
            print(f"  [{i}/{len(targets)}] ok={n_ok} fail={n_fail}", flush=True)
        sets = fetch_event_sets(ev_id, per_page=args.per_page)
        if sets is None:
            n_fail += 1
            continue
        matches = extract_match_records(sets)
        out_file = event_dir / "matches_phased.json"
        out_file.write_text(json.dumps(matches, ensure_ascii=False))
        print(f"  ✓ event={ev_id} '{tname}/{ename}' → {len(matches)} matches", flush=True)
        n_ok += 1

    print(f"\nDone. ok={n_ok} fail={n_fail}", flush=True)


if __name__ == "__main__":
    main()
