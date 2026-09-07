#!/usr/bin/env python3
"""Re-download matches.json for events with duplicate/missing data.

Reads /tmp/duplicated_events.json (output of duplicate scan), and for each event
with duplicates >= threshold, re-fetches sets via fixed fetch_all_nodes and
rewrites matches.json.

Usage:
    python3 scripts/fetch/redownload_matches.py --token "$STARTGG_TOKEN" --min-dups 5
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


def _build_entrant2user(all_nodes):
    """Build entrant_id → user_id map from slots data."""
    out = {}
    for node in all_nodes:
        if not isinstance(node, dict): continue
        for slot in (node.get("slots") or []):
            ent = slot.get("entrant") or {}
            eid = ent.get("id")
            if eid is None: continue
            parts = ent.get("participants") or []
            if not parts: continue
            u = (parts[0] or {}).get("user") or {}
            uid = u.get("id")
            if uid is not None:
                out[eid] = uid
    return out


def write_matches(all_nodes, event_dir: Path):
    """Rewrite matches.json from sets data with FULL schema (incl. details/games).
    Dedupes by node.id (start.gg set id) to handle any leftover pagination overlap.

    Winner/loser 判定は node.winnerId (entrant ID) を優先使用.
    score だけで判定すると、score 0/0 (cancel/DQ で score 取得失敗) の場合に
    常に slot1 を winner と誤判定するバグがあった (池スマ#4 メロンおじさんで発覚).
    """
    entrant2user = _build_entrant2user(all_nodes)
    json_data = {"data": []}
    seen_set_ids = set()
    for node in all_nodes:
        if not isinstance(node, dict): continue
        nid = node.get("id")
        if nid is not None:
            if nid in seen_set_ids: continue
            seen_set_ids.add(nid)
        slots = node.get("slots") or []
        if len(slots) != 2: continue
        slot0, slot1 = slots[0], slots[1]
        if not slot0 or not slot1: continue
        if not (slot0.get("entrant") and slot1.get("entrant")): continue
        st0 = slot0.get("standing") or {}; st1 = slot1.get("standing") or {}
        if st0.get("stats") is None or st1.get("stats") is None: continue
        score0 = ((st0.get("stats") or {}).get("score") or {}).get("value")
        score1 = ((st1.get("stats") or {}).get("score") or {}).get("value")
        if score0 is None: score0 = 0
        if score1 is None: score1 = 0
        # winnerId (entrant ID) を優先. fallback として score 比較を使用.
        winner_eid = node.get("winnerId")
        ent0_id = (slot0.get("entrant") or {}).get("id")
        ent1_id = (slot1.get("entrant") or {}).get("id")
        if winner_eid is not None and winner_eid in (ent0_id, ent1_id):
            winner_slot = slot0 if winner_eid == ent0_id else slot1
        else:
            # winnerId 不明 → score 比較. 同点なら確定できないので skip.
            if score0 == score1:
                continue
            winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot is slot0 else slot0
        winner_score = score0 if winner_slot is slot0 else score1
        loser_score = score1 if winner_slot is slot0 else score0
        dq = (score0 < 0 or score1 < 0)
        cancel = (score0 == 0 and score1 == 0 and winner_eid is None)
        # games / details
        details = []
        for game in (node.get("games") or []):
            if not isinstance(game, dict): continue
            details.append({
                "game_id": game.get("id"),
                "order_num": game.get("orderNum"),
                "winner_id": entrant2user.get(game.get("winnerId")),
                "entrant1_score": game.get("entrant1Score"),
                "entrant2_score": game.get("entrant2Score"),
                "stage": ((game.get("stage") or {}).get("name")),
                "selections": [
                    {
                        "user_id": entrant2user.get(((sel.get("entrant") or {}).get("id"))),
                        "selection_id": sel.get("id"),
                        "character_id": (sel.get("character") or {}).get("id"),
                        "character_name": (sel.get("character") or {}).get("name"),
                    }
                    for sel in (game.get("selections") or []) if isinstance(sel, dict)
                ],
            })
        pg = node.get("phaseGroup") or {}
        wave = pg.get("wave") or {}
        wid_ent = (winner_slot.get("entrant") or {}).get("id")
        lid_ent = (loser_slot.get("entrant") or {}).get("id")
        match_data = {
            "match_id": nid,
            "winner_id": entrant2user.get(wid_ent),
            "loser_id": entrant2user.get(lid_ent),
            "winner_score": winner_score,
            "loser_score": loser_score,
            "round_text": node.get("fullRoundText"),
            "round": node.get("round"),
            "phase": pg.get("displayIdentifier"),
            "phase_group_id": pg.get("id"),
            "wave": wave.get("identifier"),
            "dq": dq,
            "cancel": cancel,
            "state": node.get("state"),
            "details": details,
        }
        json_data["data"].append(match_data)
    (event_dir / "matches.json").write_text(json.dumps(json_data, ensure_ascii=False))
    return len(json_data["data"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=int, default=10)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--min-dups", type=int, default=5, help="Only re-fetch events with >= this many duplicates")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dup-list", default="/tmp/duplicated_events.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-list", default="/tmp/refetch_failed_events.jsonl",
                        help="Output path for failed events (one JSON per line). Append mode.")
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    with open(args.dup_list) as f:
        affected = json.load(f)
    targets = [a for a in affected if a["duplicates"] >= args.min_dups]
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"Targets: {len(targets)} events (dups >= {args.min_dups})", flush=True)

    n_ok = 0; n_fail = 0
    for i, a in enumerate(targets):
        if i % 5 == 0:
            print(f"  [{i}/{len(targets)}] ok={n_ok} fail={n_fail}", flush=True)
        ev_id = a["event_id"]
        event_dir = Path(a["path"])
        before_total = a["total_matches"]
        before_unique = a["unique_matches"]
        try:
            sets = fetch_all_nodes(
                get_event_sets_query(),
                {"eventId": ev_id},
                ["event", "sets"],
                per_page=args.per_page,
            )
        except FetchError as e:
            err_str = str(e)
            print(f"  fail event={ev_id} '{a['tournament_name']}': {err_str}", flush=True)
            n_fail += 1
            # Append to fail list (separate file for later inspection / manual retry)
            try:
                with open(args.fail_list, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "event_id": ev_id,
                        "path": str(event_dir),
                        "tournament_name": a.get("tournament_name", ""),
                        "date": a.get("date"),
                        "num_entrants": a.get("num_entrants"),
                        "error": err_str[:500],
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass
            continue
        if args.dry_run:
            print(f"  DRY event={ev_id} '{a['tournament_name']}' → fetched {len(sets)} sets (was total={before_total} unique={before_unique})", flush=True)
            n_ok += 1
            continue
        new_count = write_matches(sets, event_dir)
        delta = new_count - before_unique
        print(f"  ✓ event={ev_id} '{a['tournament_name']}' before unique={before_unique} → after={new_count} (Δ {delta:+d})", flush=True)
        n_ok += 1

    print(f"\nDone. ok={n_ok} fail={n_fail}", flush=True)


if __name__ == "__main__":
    main()
