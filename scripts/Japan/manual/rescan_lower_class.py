#!/usr/bin/env python3
"""Re-scan known tournaments for missed events (下位クラス bracket 等).

After the queries.py / download.py fix that removes the videogameId filter and
accepts lower-class brackets even when their videogame tag is missing, we need
to re-run fetch_event_ids_from_tournament for each known Japan tournament
post-COVID (2023-01-01〜) and download any new events that appear.

Usage:
    python3 scripts/fetch/rescan_lower_class.py --token "$STARTGG_TOKEN" \\
        --since 2023-01-01 --region Japan
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.common.queries import get_tournament_events_query
from scripts.common.utils import (
    country_code2region, get_date_parts, get_event_directory,
    read_users_jsonl, read_set, read_tournaments_jsonl,
    write_jsonl, set_indent_num,
    fetch_data_with_retries,
    set_retry_parameters, set_api_parameters,
    FetchError, NoPhaseError,
)
from scripts.common.download import (
    download_standings, download_seeds, download_all_set,
    extend_user_info, write_event_attributes,
    fetch_event_ids_from_tournament,
)


def load_event_attr(event_path: str) -> dict | None:
    try:
        with open(os.path.join(event_path, "attr.json"), "rb") as f:
            return json.loads(f.read())
    except Exception:
        return None


def tournament_date_and_place(entry):
    """既存 entry の最初の event の attr.json から (timestamp, place, url, country_code, end_timestamp) を読む."""
    for ev in entry.get("events") or []:
        attr = load_event_attr(ev.get("path", ""))
        if not attr:
            continue
        ts = attr.get("timestamp")
        place = attr.get("place")
        url = attr.get("url")
        cc = (place or {}).get("country_code", "")
        end_ts = attr.get("end_timestamp")
        is_online = not attr.get("offline", True)
        if ts and place:
            return ts, place, url, cc, end_ts, is_online
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--since", default="2023-01-01", help="YYYY-MM-DD")
    parser.add_argument("--region", default="Japan")
    parser.add_argument("--game_id", default="1386")
    parser.add_argument("--startgg_dir", default="data/startgg/events")
    parser.add_argument("--users_file_path", default="data/startgg/users.jsonl")
    parser.add_argument("--tournament_file_path", default="data/startgg/tournaments.jsonl")
    parser.add_argument("--max_retries", type=int, default=5)
    parser.add_argument("--retry_delay", type=int, default=10)
    parser.add_argument("--indent_num", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="0 = all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    cutoff_ts = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp())
    print(f"Loading tournaments.jsonl ...", flush=True)
    tournaments = read_tournaments_jsonl(args.tournament_file_path)
    users = read_users_jsonl(args.users_file_path)
    print(f"  {len(tournaments)} tournaments, {len(users)} users", flush=True)

    # Filter targets: at least one event with date >= cutoff AND region matches
    targets = []
    for tid, entry in tournaments.items():
        meta = tournament_date_and_place(entry)
        if not meta:
            continue
        ts, place, url, cc, end_ts, is_online = meta
        if ts < cutoff_ts:
            continue
        region = country_code2region(cc) if cc else None
        if args.region and region != args.region:
            continue
        targets.append((tid, entry, ts, place, url, cc, end_ts, is_online))
    targets.sort(key=lambda x: x[2])
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"Targets: {len(targets)} tournaments since {args.since} region={args.region}", flush=True)

    n_checked = 0
    n_new_events = 0
    n_skip_dl_fail = 0
    rewrite = False
    for tid, entry, ts, place, url, cc, end_ts, is_online in targets:
        n_checked += 1
        if n_checked % 50 == 0:
            print(f"  [{n_checked}/{len(targets)}] checked, new_events={n_new_events}", flush=True)
        try:
            events_info = fetch_event_ids_from_tournament(tid, args.game_id)
        except FetchError as e:
            print(f"  fetch fail tid={tid} ({entry.get('name')}): {e}", flush=True)
            continue
        existing_eids = {ev.get("event_id") for ev in entry.get("events") or []}
        new_events = [(eid, ename, ionl) for (eid, ename, ionl) in events_info if eid not in existing_eids]
        if not new_events:
            continue
        # Found new events for this tournament. Download them.
        tournament_name = entry.get("name", "")
        print(f"  NEW: tid={tid} ({tournament_name}): {[en for _, en, _ in new_events]}", flush=True)
        if args.dry_run:
            n_new_events += len(new_events)
            continue
        year, month, day = get_date_parts(ts)
        for event_id, event_name, ionl in new_events:
            try:
                event_dir = get_event_directory(args.startgg_dir, cc, year, month, day, tournament_name, event_name)
                os.makedirs(event_dir, exist_ok=True)
                user_data, player_data, entrant2user = download_standings(event_id, event_dir)
                num_entrants = len(user_data)
                try:
                    download_seeds(event_id, user_data, player_data, entrant2user, event_dir)
                except NoPhaseError:
                    print(f"    no phase for event_id={event_id} '{event_name}', skipping", flush=True)
                    # cleanup
                    if os.path.isdir(event_dir):
                        shutil.rmtree(event_dir, ignore_errors=True)
                    n_skip_dl_fail += 1
                    continue
                extend_user_info(user_data, player_data, users, args.users_file_path)
                download_all_set(event_id, entrant2user, event_dir)
                labels = {}
                write_event_attributes(num_entrants, event_id, event_name, tournament_name, ts, place, url, labels, ionl, event_dir, end_timestamp=end_ts)
                # update tournaments entry
                entry.setdefault("events", []).append({
                    "event_id": event_id,
                    "event_name": event_name,
                    "path": event_dir,
                })
                rewrite = True
                n_new_events += 1
                print(f"    ✓ downloaded event_id={event_id} '{event_name}' ({num_entrants} entrants)", flush=True)
            except FetchError as e:
                print(f"    fetch fail event_id={event_id}: {e}", flush=True)
                n_skip_dl_fail += 1
                continue

    if rewrite and not args.dry_run:
        print(f"Rewriting tournaments.jsonl ...", flush=True)
        write_jsonl(list(tournaments.values()), args.tournament_file_path, with_version=True)

    print(f"\nDone. Checked {n_checked} tournaments, found {n_new_events} new events, {n_skip_dl_fail} failed.", flush=True)


if __name__ == "__main__":
    main()
