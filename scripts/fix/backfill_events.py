#!/usr/bin/env python3
"""Re-download existing events to backfill new schema fields."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.fetch.download import (
    download_all_set,
    download_seeds,
    download_standings,
    extend_tournament_info,
    extend_user_info,
    write_event_attributes,
)
from scripts.queries import get_event_details_by_id_query
from scripts.utils import (
    FetchError,
    NoPhaseError,
    fetch_data_with_retries,
    get_date_parts,
    get_event_directory,
    read_tournaments_jsonl,
    read_users_jsonl,
    set_api_parameters,
    set_indent_num,
    set_retry_parameters,
    write_jsonl,
)


def parse_date(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def load_event_ids(path: Path) -> list[int]:
    event_ids = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event_ids.append(int(line))
    return event_ids


def fetch_event_details(event_id: int) -> tuple[dict, dict]:
    response = fetch_data_with_retries(
        get_event_details_by_id_query(),
        {"eventId": event_id},
    )
    if "data" not in response or response["data"] is None or "event" not in response["data"]:
        raise FetchError(f"Malformed response for event {event_id}: {response}")
    event = response["data"]["event"]
    if event is None:
        raise FetchError(f"Event {event_id} not found.")
    tournament = event.get("tournament")
    if tournament is None:
        raise FetchError(f"Tournament information missing for event {event_id}.")
    return event, tournament


def build_place_dict(tournament: dict) -> dict:
    return {
        "country_code": tournament.get("countryCode"),
        "city": tournament.get("city"),
        "lat": tournament.get("lat"),
        "lng": tournament.get("lng"),
        "venue_name": tournament.get("venueName"),
        "timezone": tournament.get("timezone"),
        "postal_code": tournament.get("postalCode"),
        "venue_address": tournament.get("venueAddress"),
        "maps_place_id": tournament.get("mapsPlaceId"),
    }


def should_process(timestamp: int, since_ts: int | None, until_ts: int | None) -> bool:
    if since_ts is not None and timestamp < since_ts:
        return False
    if until_ts is not None and timestamp > until_ts:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill existing events by re-downloading data."
    )
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="API URL")
    parser.add_argument("--token", required=True, help="start.gg API token")
    parser.add_argument("--events_root", default="data/startgg/events", help="Events root directory")
    parser.add_argument("--users_file_path", default="data/startgg/users.jsonl", help="Path to users.jsonl")
    parser.add_argument("--tournament_file_path", default="data/startgg/tournaments.jsonl", help="Path to tournaments.jsonl")
    parser.add_argument("--event_ids_file", default="", help="Optional file with event IDs (one per line)")
    parser.add_argument("--since", default="", help="Process events from this date (YYYY-MM-DD)")
    parser.add_argument("--until", default="", help="Process events up to this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of events processed")
    parser.add_argument("--indent_num", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--max_retries", type=int, default=10, help="Maximum retries for API requests")
    parser.add_argument("--retry_delay", type=int, default=5, help="Delay between retries in seconds")
    args = parser.parse_args()

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    users = read_users_jsonl(args.users_file_path)
    tournaments = read_tournaments_jsonl(args.tournament_file_path)
    rewrite_tournaments = False

    since_ts = parse_date(args.since) if args.since else None
    until_ts = parse_date(args.until) if args.until else None

    event_entries: list[dict] = []
    if args.event_ids_file:
        event_ids = load_event_ids(Path(args.event_ids_file))
        for event_id in event_ids:
            event_entries.append({"event_id": event_id})
    else:
        for tournament in tournaments.values():
            for event in tournament.get("events", []):
                event_entries.append(event)

    processed = 0
    for entry in event_entries:
        if args.limit and processed >= args.limit:
            break
        event_id = entry.get("event_id")
        if event_id is None:
            continue
        try:
            event_id = int(event_id)
        except ValueError:
            continue

        try:
            event, tournament = fetch_event_details(event_id)
        except FetchError as exc:
            print(exc, file=sys.stderr)
            continue

        event_name = event.get("name") or "Unknown Event"
        tournament_name = tournament.get("name") or "Unknown Tournament"
        timestamp = event.get("startAt") or tournament.get("startAt")
        if timestamp is None:
            print(f"Event {event_id} has no timestamp. Skipping.", file=sys.stderr)
            continue

        if not should_process(timestamp, since_ts, until_ts):
            continue

        country_code = tournament.get("countryCode") or ""
        year, month, day = get_date_parts(timestamp)
        event_dir = entry.get("path")
        if not event_dir:
            event_dir = get_event_directory(
                args.events_root,
                country_code,
                year,
                month,
                day,
                tournament_name,
                event_name,
            )

        os.makedirs(event_dir, exist_ok=True)

        user_data, player_data, entrant2user = download_standings(event_id, event_dir)
        num_entrants = len(user_data)
        try:
            download_seeds(event_id, user_data, player_data, entrant2user, event_dir)
        except NoPhaseError as exc:
            print(f"Seeds not available for event {event_id}: {exc}", file=sys.stderr)
        extend_user_info(user_data, player_data, users, args.users_file_path)
        download_all_set(event_id, entrant2user, event_dir)

        place = build_place_dict(tournament)
        labels = {}
        write_event_attributes(
            num_entrants,
            event_id,
            event_name,
            tournament_name,
            timestamp,
            place,
            tournament.get("url"),
            labels,
            event.get("isOnline"),
            event_dir,
        )

        tournament_id = tournament.get("id")
        if tournament_id is not None:
            tournament_id = int(tournament_id)
            if tournament_id not in tournaments:
                tournaments[tournament_id] = {
                    "tournament_id": tournament_id,
                    "name": tournament_name,
                    "events": [],
                }
                rewrite_tournaments = True
            if not any(e.get("event_id") == event_id for e in tournaments[tournament_id]["events"]):
                tournaments[tournament_id]["events"].append(
                    {"event_id": event_id, "event_name": event_name, "path": event_dir}
                )
                rewrite_tournaments = True
        processed += 1

    if rewrite_tournaments:
        write_jsonl(list(tournaments.values()), args.tournament_file_path, with_version=True)

    print(f"Backfill complete. Processed {processed} events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
