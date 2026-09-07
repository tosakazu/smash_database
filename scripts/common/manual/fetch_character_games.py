"""Fetch character / stage selections for every set in past tournaments.

For each event under data/startgg/Japan/events/{>= --since}/**/attr.json with
num_entrants >= --min-entrants, refetch ALL sets including the `games` field
(character/stage selections) and save a sidecar `character_games.json` next to
the existing `matches.json`. The original matches.json is NOT modified.

The sidecar contains only sets that actually have at least one game with
character selections, so the file is small for tournaments where organizers
didn't record per-game data.

Phase groups are taken from the existing matches.json (phase_group_id), so this
script reuses already-known structure rather than re-fetching event phases.
If matches.json is missing for an event we fall back to fetching the event's
phases freshly.

Resumable: if `character_games.json` already exists in the event dir we skip
that event. Pass --refresh to overwrite.

Rate-limit / complexity handling is delegated to scripts.common.utils.fetch_all_nodes
which does AIMD per_page tuning and 429 / complexity backoff.

Usage:
    cd smash_db_tournament
    ../ranking_eval/.venv/bin/python3 -u scripts/fetch/fetch_character_games.py \
        --token "$(cat ../STARTGG_TOKEN)" \
        --since 2024-06-01 \
        --min-entrants 8 \
        [--limit N]  [--refresh]  [--dry-run]
"""

import argparse
import json
import sys
import time
from datetime import datetime, date
from pathlib import Path

HERE = Path(__file__).resolve()
SMASH_DB = HERE.parents[3]   # scripts/common/manual/x.py → smash_db_tournament/
sys.path.insert(0, str(SMASH_DB))

from scripts.common.queries import (
    get_phase_group_sets_with_games_query,
    get_event_phases_full_query,
)
from scripts.common.utils import (
    fetch_all_nodes,
    fetch_data_with_retries,
    set_api_parameters,
    set_retry_parameters,
    set_page_delay,
    FetchError,
)

EVENTS_DIR = SMASH_DB / "data" / "startgg" / "Japan" / "events"
SIDECAR_NAME = "character_games.json"


# ---------- helpers ----------

def iter_event_dirs(since: date):
    """Yield event_dir Path objects (one per event) under Japan/ >= since."""
    if not EVENTS_DIR.is_dir():
        return
    for year_dir in sorted(EVENTS_DIR.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = int(year_dir.name)
        if year < since.year:
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            month = int(month_dir.name)
            if year == since.year and month < since.month:
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                day = int(day_dir.name)
                try:
                    d = date(year, month, day)
                except ValueError:
                    continue
                if d < since:
                    continue
                for t_dir in day_dir.iterdir():
                    if not t_dir.is_dir():
                        continue
                    for e_dir in t_dir.iterdir():
                        if (e_dir / "attr.json").is_file():
                            yield e_dir


def read_phase_group_ids_from_matches(event_dir: Path) -> list[int]:
    p = event_dir / "matches.json"
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    pgs = set()

    def _walk(obj):
        if isinstance(obj, dict):
            if "phase_group_id" in obj and isinstance(obj["phase_group_id"], int):
                pgs.add(obj["phase_group_id"])
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(d)
    return sorted(pgs)


def fetch_event_phase_groups(event_id: int) -> list[int]:
    """Fallback: hit start.gg to enumerate phase_group_ids for the event."""
    q = get_event_phases_full_query()
    try:
        resp = fetch_data_with_retries(q, {"eventId": event_id})
    except Exception as e:
        print(f"  phase-fetch fail event_id={event_id}: {e}", flush=True)
        return []
    if not isinstance(resp, dict):
        return []
    ev = ((resp.get("data") or {}).get("event") or {})
    out = []
    for ph in (ev.get("phases") or []):
        for pg in (ph.get("phaseGroups") or {}).get("nodes") or []:
            pid = pg.get("id")
            if isinstance(pid, int):
                out.append(pid)
    return sorted(set(out))


def _slot_user_id(slot: dict) -> int | None:
    if not slot:
        return None
    ent = slot.get("entrant") or {}
    parts = ent.get("participants") or []
    if not parts:
        return None
    u = (parts[0] or {}).get("user") or {}
    uid = u.get("id")
    return int(uid) if uid is not None else None


def _selection_user_id(sel: dict) -> int | None:
    if not sel:
        return None
    ent = sel.get("entrant") or {}
    parts = ent.get("participants") or []
    if not parts:
        return None
    u = (parts[0] or {}).get("user") or {}
    uid = u.get("id")
    return int(uid) if uid is not None else None


def extract_set_record(set_node: dict) -> dict | None:
    """Convert a set node to a compact record, or None if no usable game data."""
    games = set_node.get("games") or []
    if not games:
        return None

    # Build entrant_id -> user_id from set slots (more reliable than per-selection).
    entrant2user: dict[int, int] = {}
    slots = set_node.get("slots") or []
    for slot in slots:
        ent = slot.get("entrant") or {}
        eid = ent.get("id")
        uid = _slot_user_id(slot)
        if isinstance(eid, int) and uid is not None:
            entrant2user[eid] = uid

    set_winner_uid: int | None = None
    win_eid = set_node.get("winnerId")
    if isinstance(win_eid, int):
        set_winner_uid = entrant2user.get(win_eid)

    slot_uids = [entrant2user.get((s.get("entrant") or {}).get("id")) for s in slots]

    out_games = []
    for g in games:
        if not isinstance(g, dict):
            continue
        sels = g.get("selections") or []
        out_sels = []
        for sel in sels:
            ch = (sel.get("character") or {}) if isinstance(sel, dict) else {}
            if not ch.get("id") and not ch.get("name"):
                continue
            sel_eid = ((sel.get("entrant") or {}).get("id"))
            sel_uid = entrant2user.get(sel_eid) if isinstance(sel_eid, int) else _selection_user_id(sel)
            out_sels.append({
                "user_id": sel_uid,
                "character_id": ch.get("id"),
                "character_name": ch.get("name"),
            })
        if not out_sels:
            continue
        g_win_eid = g.get("winnerId")
        out_games.append({
            "game_id": g.get("id"),
            "order_num": g.get("orderNum"),
            "winner_user_id": entrant2user.get(g_win_eid) if isinstance(g_win_eid, int) else None,
            "entrant1_score": g.get("entrant1Score"),
            "entrant2_score": g.get("entrant2Score"),
            "stage_id": (g.get("stage") or {}).get("id"),
            "stage_name": (g.get("stage") or {}).get("name"),
            "selections": out_sels,
        })

    if not out_games:
        return None

    return {
        "set_id": set_node.get("id"),
        "round": set_node.get("round"),
        "round_text": set_node.get("fullRoundText"),
        "state": set_node.get("state"),
        "slot_user_ids": slot_uids,
        "winner_user_id": set_winner_uid,
        "games": out_games,
    }


def fetch_phase_group_sets_with_games(pg_id: int, per_page: int) -> list[dict]:
    q = get_phase_group_sets_with_games_query()
    variables = {"phaseGroupId": pg_id}
    keys = ["phaseGroup", "sets"]
    try:
        return fetch_all_nodes(q, variables, keys, per_page=per_page)
    except FetchError as e:
        print(f"  pg={pg_id} FETCH FAIL: {e}", flush=True)
        return []


def fetch_event_character_games(event_id: int, phase_group_ids: list[int],
                                per_page: int, pg_sleep: float) -> dict:
    """Returns sidecar payload."""
    total_sets = 0
    matches = []
    pg_failed = []
    for pg_id in phase_group_ids:
        try:
            nodes = fetch_phase_group_sets_with_games(pg_id, per_page=per_page)
        except Exception as e:
            print(f"  pg={pg_id} EXC: {e}", flush=True)
            pg_failed.append(pg_id)
            time.sleep(pg_sleep)
            continue
        for n in nodes:
            if not isinstance(n, dict):
                continue
            total_sets += 1
            rec = extract_set_record(n)
            if rec is not None:
                matches.append(rec)
        time.sleep(pg_sleep)
    return {
        "event_id": event_id,
        "fetched_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "num_phase_groups": len(phase_group_ids),
        "num_phase_groups_failed": len(pg_failed),
        "failed_phase_group_ids": pg_failed,
        "num_sets_total": total_sets,
        "num_sets_with_character": len(matches),
        "matches": matches,
    }


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-entrants", type=int, default=8)
    ap.add_argument("--per-page", type=int, default=10,
                    help="initial perPage; fetch_all_nodes will halve on complexity error")
    ap.add_argument("--max-retries", type=int, default=20)
    ap.add_argument("--retry-delay", type=float, default=3.0)
    ap.add_argument("--page-delay", type=float, default=0.0,
                    help="sleep injected by fetch_all_nodes between pages (sec)")
    ap.add_argument("--pg-sleep", type=float, default=0.5,
                    help="sleep between phase_groups (sec)")
    ap.add_argument("--event-sleep", type=float, default=0.0,
                    help="extra sleep between events (sec)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N events (0 = no limit)")
    ap.add_argument("--refresh", action="store_true",
                    help="overwrite existing sidecar files")
    ap.add_argument("--dry-run", action="store_true",
                    help="list candidate events and exit")
    ap.add_argument("--newest-first", action="store_true",
                    help="process newest tournaments first (default: oldest first)")
    args = ap.parse_args()

    since = datetime.strptime(args.since, "%Y-%m-%d").date()

    set_api_parameters("https://api.start.gg/gql/alpha", args.token)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_page_delay(args.page_delay)

    # ---- collect candidates ----
    cands = []
    for e_dir in iter_event_dirs(since):
        try:
            a = json.loads((e_dir / "attr.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        ne = a.get("num_entrants") or 0
        if ne < args.min_entrants:
            continue
        eid = a.get("event_id")
        if not eid:
            continue
        cands.append({
            "event_id": int(eid),
            "event_dir": e_dir,
            "num_entrants": int(ne),
            "tournament_name": a.get("tournament_name", ""),
            "event_name": a.get("event_name", ""),
            "timestamp": a.get("timestamp", 0),
        })
    cands.sort(key=lambda x: x["timestamp"], reverse=args.newest_first)
    order = "newest-first" if args.newest_first else "oldest-first"
    print(f"[scan] candidate events (>= {args.min_entrants} entrants, since {args.since}, order={order}): {len(cands)}")

    if not args.refresh:
        cands = [c for c in cands if not (c["event_dir"] / SIDECAR_NAME).is_file()]
        print(f"[scan] after skipping already-fetched sidecars: {len(cands)}")

    if args.dry_run:
        from collections import Counter
        months = Counter()
        for c in cands:
            ts = c["timestamp"]
            if ts:
                d = datetime.fromtimestamp(ts)
                months[(d.year, d.month)] += 1
        print("[dry-run] per-month candidate distribution:")
        for ym in sorted(months):
            print(f"  {ym[0]}-{ym[1]:02d}: {months[ym]}")
        print("[dry-run] first 5 candidates:")
        for c in cands[:5]:
            print(f"  eid={c['event_id']} n={c['num_entrants']} "
                  f"{c['tournament_name']} / {c['event_name']}")
        return

    if not cands:
        return

    n_done = 0
    n_with_char = 0
    n_no_char = 0
    t0 = time.time()
    for i, c in enumerate(cands, 1):
        if args.limit and n_done >= args.limit:
            break
        eid = c["event_id"]
        e_dir = c["event_dir"]
        sidecar = e_dir / SIDECAR_NAME

        # Read phase_group_ids from existing matches.json; fallback to event phases query
        pg_ids = read_phase_group_ids_from_matches(e_dir)
        if not pg_ids:
            pg_ids = fetch_event_phase_groups(eid)
        if not pg_ids:
            print(f"[{i}/{len(cands)}] eid={eid} no phase_groups found — skipping", flush=True)
            continue

        payload = fetch_event_character_games(
            eid, pg_ids,
            per_page=args.per_page,
            pg_sleep=args.pg_sleep,
        )

        sidecar.write_text(
            json.dumps(payload, ensure_ascii=False, indent=None, separators=(",", ":")),
            encoding="utf-8",
        )

        n_done += 1
        if payload["num_sets_with_character"] > 0:
            n_with_char += 1
        else:
            n_no_char += 1

        elapsed = time.time() - t0
        rate = n_done / elapsed if elapsed else 0
        tag = "CHAR" if payload["num_sets_with_character"] > 0 else "----"
        print(f"[{i}/{len(cands)}] eid={eid:>8} n={c['num_entrants']:>4} {tag} "
              f"pg={len(pg_ids)} sets={payload['num_sets_total']} "
              f"with_char={payload['num_sets_with_character']}  "
              f"({c['tournament_name'][:30]} / {c['event_name'][:20]})  "
              f"rate={rate:.2f}/s t={elapsed:.0f}s", flush=True)
        if args.event_sleep:
            time.sleep(args.event_sleep)

    print()
    print(f"[done] events_processed={n_done}  with_character={n_with_char}  no_character={n_no_char}")


if __name__ == "__main__":
    main()
