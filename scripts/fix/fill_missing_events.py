import argparse
import json
import sys
from pathlib import Path

from openai import OpenAI

import os

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import get_event_details_by_id_query
from scripts.utils import (
    FetchError,
    NoPhaseError,
    analyze_event_setting,
    fetch_data_with_retries,
    get_date_parts,
    get_event_directory,
    read_set,
    read_tournaments_jsonl,
    read_users_jsonl,
    set_api_parameters,
    set_indent_num,
    set_retry_parameters,
    write_jsonl,
)
from scripts.fetch.download import (
    download_all_set,
    download_seeds,
    download_standings,
    extend_tournament_info,
    extend_user_info,
    write_event_attributes,
)


DEFAULT_MISSING_JSON = Path("data/JJPR/missing_events/missing_events.json")
DEFAULT_DONE_EVENTS = Path("data/startgg/done_events.csv")
DEFAULT_USERS_FILE = Path("data/startgg/users.jsonl")
DEFAULT_TOURNAMENTS_FILE = Path("data/startgg/tournaments.jsonl")
DEFAULT_STARTGG_DIR = Path("data/startgg/events")
DEFAULT_EVENT_PROMPT = Path("scripts/event_analysis_prompt.txt")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch and store start.gg data for tournaments missing from the local dataset."
    )
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="start.gg GraphQL endpoint")
    parser.add_argument("--token", required=True, help="start.gg API token")
    parser.add_argument("--missing_json", type=Path, default=DEFAULT_MISSING_JSON, help="Path to missing events JSON")
    parser.add_argument("--startgg_dir", type=Path, default=DEFAULT_STARTGG_DIR, help="Base directory for event data")
    parser.add_argument("--users_file_path", type=Path, default=DEFAULT_USERS_FILE, help="Path to users.jsonl")
    parser.add_argument("--tournament_file_path", type=Path, default=DEFAULT_TOURNAMENTS_FILE, help="Path to tournaments.jsonl")
    parser.add_argument("--done_events_path", type=Path, default=DEFAULT_DONE_EVENTS, help="Path to processed event ID list")
    parser.add_argument("--event_prompt_file_path", type=Path, default=DEFAULT_EVENT_PROMPT, help="Prompt file for OpenAI labels")
    parser.add_argument("--openai_api_key", default="", help="OpenAI API key (optional)")
    parser.add_argument("--indent_num", type=int, default=2, help="Indent for saved JSON files")
    parser.add_argument("--max_retries", type=int, default=10, help="Max retries for API requests")
    parser.add_argument("--retry_delay", type=int, default=5, help="Seconds between retries")
    parser.add_argument("--overwrite_done", action="store_true", help="Force re-download even if event already processed")
    parser.add_argument("--verbose", action="store_true", help="Print detailed progress information")
    return parser.parse_args()


def load_missing_events(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing events JSON not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("missing_events", [])


def fetch_event_metadata(event_id: int):
    response = fetch_data_with_retries(get_event_details_by_id_query(), {"eventId": event_id})
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


def ensure_parent_dir(path: Path):
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def write_done_event(event_id: int, path: Path):
    ensure_parent_dir(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{event_id}\n")
        f.flush()


def rewrite_tournaments_file(tournaments: dict, tournament_file_path: Path):
    """Rewrite the tournaments.jsonl file to reflect in-memory changes."""
    entries = sorted(
        (
            {
                "tournament_id": tid,
                "name": data.get("name"),
                "events": data.get("events", []),
            }
            for tid, data in tournaments.items()
        ),
        key=lambda item: item["tournament_id"],
    )
    write_jsonl(entries, str(tournament_file_path), with_version=True)


def process_event(
    event_entry: dict,
    startgg_dir: Path,
    users,
    tournaments,
    done_events,
    users_file_path: Path,
    tournament_file_path: Path,
    done_events_path: Path,
    openai_client,
    event_prompt: str,
    overwrite_done: bool = False,
    verbose: bool = False,
):
    event_id = event_entry.get("id")
    if event_id is None:
        if verbose:
            print("Skipping entry without event ID.", file=sys.stderr)
        return False

    if not isinstance(event_id, int):
        try:
            event_id = int(event_id)
        except ValueError:
            print(f"Invalid event ID '{event_entry.get('id')}' - skipping.", file=sys.stderr)
            return False

    if event_id in done_events and not overwrite_done:
        if verbose:
            print(f"Event {event_id} already processed. Skipping.")
        return False

    event, tournament = fetch_event_metadata(event_id)
    event_name = event.get("name") or "Unknown Event"
    tournament_name = tournament.get("name") or "Unknown Tournament"
    timestamp = event.get("startAt") or tournament.get("startAt")
    if timestamp is None:
        print(f"Event {event_id} has no start timestamp. Skipping.", file=sys.stderr)
        return False

    country_code = tournament.get("countryCode") or ""
    year, month, day = get_date_parts(timestamp)
    event_dir = Path(
        get_event_directory(
            str(startgg_dir),
            country_code,
            year,
            month,
            day,
            tournament_name,
            event_name,
        )
    )
    if verbose:
        print(f"Processing event {event_id}: {event_name} ({tournament_name}) -> {event_dir}")

    user_data, player_data, entrant2user = download_standings(event_id, str(event_dir))
    num_entrants = len(user_data)

    try:
        download_seeds(event_id, user_data, player_data, entrant2user, str(event_dir))
    except NoPhaseError as exc:
        print(f"Seeds not available for event {event_id}: {exc}", file=sys.stderr)

    extend_user_info(user_data, player_data, users, str(users_file_path))
    download_all_set(event_id, entrant2user, str(event_dir))

    labels = analyze_event_setting(
        openai_client, event_prompt, tournament_name, event_name, event_id
    ) if openai_client and event_prompt else {}

    place = build_place_dict(tournament)
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
        str(event_dir),
    )

    tournament_id = tournament.get("id")
    if tournament_id is not None:
        if tournament_id not in tournaments:
            tournaments[tournament_id] = {
                "tournament_id": tournament_id,
                "name": tournament_name,
                "events": [],
            }
            new_entry = tournaments[tournament_id].copy()
            new_entry["events"] = [
                {
                    "event_id": event_id,
                    "event_name": event_name,
                    "path": str(event_dir),
                }
            ]
            extend_tournament_info(new_entry, str(tournament_file_path))
        else:
            events = tournaments[tournament_id].setdefault("events", [])
            if not any(e.get("event_id") == event_id for e in events):
                events.append(
                    {
                        "event_id": event_id,
                        "event_name": event_name,
                        "path": str(event_dir),
                    }
                )
            if verbose:
                print(f"Tournament {tournament_id} already tracked; event appended in memory.")
    else:
        print(f"Tournament ID missing for event {event_id}. Tournament info not updated.", file=sys.stderr)

    already_recorded = event_id in done_events
    done_events.add(event_id)
    if not already_recorded:
        write_done_event(event_id, done_events_path)
    return True


def main():
    args = parse_args()

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    openai_client = None
    event_prompt = None
    if args.openai_api_key:
        try:
            openai_client = OpenAI(api_key=args.openai_api_key)
            if args.event_prompt_file_path.is_file():
                with args.event_prompt_file_path.open("r", encoding="utf-8") as f:
                    event_prompt = f.read()
            else:
                print(f"Event prompt file not found: {args.event_prompt_file_path}", file=sys.stderr)
        except Exception as exc:
            print(f"Failed to initialize OpenAI client: {exc}", file=sys.stderr)
            openai_client = None

    for path in (
        args.startgg_dir,
        args.users_file_path.parent,
        args.tournament_file_path.parent,
        args.done_events_path.parent,
    ):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)

    try:
        missing_events = load_missing_events(args.missing_json)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    done_events = read_set(str(args.done_events_path), as_int=True)
    users = read_users_jsonl(str(args.users_file_path))
    tournaments = read_tournaments_jsonl(str(args.tournament_file_path))

    if args.verbose:
        print(f"Loaded {len(missing_events)} missing events.")
        print(f"Known users: {len(users)} | Known tournaments: {len(tournaments)} | Done events: {len(done_events)}")

    processed = 0
    failed = 0

    for event_entry in missing_events:
        try:
            success = process_event(
                event_entry,
                args.startgg_dir,
                users,
                tournaments,
                done_events,
                args.users_file_path,
                args.tournament_file_path,
                args.done_events_path,
                openai_client,
                event_prompt,
                overwrite_done=args.overwrite_done,
                verbose=args.verbose,
            )
            if success:
                processed += 1
        except FetchError as exc:
            failed += 1
            print(f"Fetch error for event entry {event_entry.get('id')}: {exc}", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(f"Unexpected error for event entry {event_entry.get('id')}: {exc}", file=sys.stderr)
            if args.verbose:
                import traceback

                traceback.print_exc()

    if processed > 0:
        rewrite_tournaments_file(tournaments, args.tournament_file_path)

    print(f"Completed. Processed: {processed}, Failed: {failed}, Skipped: {len(missing_events) - processed - failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
