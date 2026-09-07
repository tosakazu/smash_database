#!/usr/bin/env python3
"""Validate downloaded event data for missing files or schema mismatches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


REQUIRED_EVENT_FILES = ("attr.json", "matches.json", "seeds.json", "standings.json")

ATTR_REQUIRED_FIELDS = (
    "event_id",
    "tournament_name",
    "event_name",
    "timestamp",
    "region",
    "num_entrants",
    "offline",
    "url",
    "place",
    "labels",
    "status",
)
PLACE_REQUIRED_FIELDS = (
    "country_code",
    "city",
    "lat",
    "lng",
    "venue_name",
    "timezone",
    "postal_code",
    "venue_address",
    "maps_place_id",
)

LIST_CONTAINER_FIELDS = ("data",)
MAX_MISSING_USER_RATIO_ERROR = 0.2
MAX_MISSING_MATCH_ID_RATIO_ERROR = 0.1
MAX_MISMATCHED_MATCH_ID_RATIO_ERROR = 0.05
MIN_MATCH_COUNT_FOR_MISMATCH_ERROR = 5


def load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_required_fields(obj: Dict, fields: tuple, context: str, errors: List[str]) -> None:
    for field in fields:
        if field not in obj:
            errors.append(f"{context}: missing field '{field}'")


def validate_attr(data: Dict, context: str, errors: List[str]) -> None:
    validate_required_fields(data, ATTR_REQUIRED_FIELDS, context, errors)
    place = data.get("place")
    if isinstance(place, dict):
        validate_required_fields(place, PLACE_REQUIRED_FIELDS, f"{context}.place", errors)
    elif place is None:
        errors.append(f"{context}: place is missing")
    else:
        errors.append(f"{context}: place is not an object")


def validate_list_container(data: Dict, context: str, errors: List[str]) -> None:
    validate_required_fields(data, LIST_CONTAINER_FIELDS, context, errors)
    if "data" in data and not isinstance(data["data"], list):
        errors.append(f"{context}: data is not a list")


def validate_event_dir(event_dir: Path) -> tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    payloads: Dict[str, Dict] = {}

    for filename in REQUIRED_EVENT_FILES:
        path = event_dir / filename
        if not path.exists():
            errors.append(f"{event_dir}: missing file {filename}")
            continue
        try:
            payload = load_json(path)
        except json.JSONDecodeError as exc:
            errors.append(f"{event_dir}: invalid JSON in {filename} ({exc})")
            continue

        payloads[filename] = payload
        context = f"{event_dir}/{filename}"
        if filename == "attr.json":
            validate_attr(payload, context, errors)
        else:
            validate_list_container(payload, context, errors)

    attr = payloads.get("attr.json")
    num_entrants = None
    if isinstance(attr, dict):
        num_entrants = attr.get("num_entrants")

    standings = payloads.get("standings.json", {}).get("data", [])
    seeds = payloads.get("seeds.json", {}).get("data", [])
    matches = payloads.get("matches.json", {}).get("data", [])

    # When num_entrants is unknown, treat empty standings/seeds as an error as well.
    # This keeps validation strict for partially populated attr.json.
    should_have_entries = not (isinstance(num_entrants, int) and num_entrants == 0)
    if should_have_entries:
        if isinstance(standings, list) and len(standings) == 0:
            errors.append(f"{event_dir}: standings.json is empty but num_entrants > 0")
        if isinstance(seeds, list) and len(seeds) == 0:
            errors.append(f"{event_dir}: seeds.json is empty but num_entrants > 0")

    if isinstance(standings, list):
        missing_users = sum(1 for item in standings if item.get("user_id") is None)
        if standings:
            ratio = missing_users / len(standings)
        else:
            ratio = 0
        if missing_users:
            if ratio > MAX_MISSING_USER_RATIO_ERROR:
                errors.append(
                    f"{event_dir}: standings missing user_id ratio {ratio:.1%} exceeds threshold"
                )
            else:
                warnings.append(
                    f"{event_dir}: standings missing user_id for {missing_users} entries"
                )

    if isinstance(matches, list):
        missing_winners = sum(1 for item in matches if item.get("winner_id") is None)
        missing_losers = sum(1 for item in matches if item.get("loser_id") is None)
        missing_total = missing_winners + missing_losers
        if missing_total and matches:
            ratio = missing_total / (len(matches) * 2)
            if ratio > MAX_MISSING_MATCH_ID_RATIO_ERROR:
                errors.append(
                    f"{event_dir}: matches missing winner/loser ratio {ratio:.1%} exceeds threshold"
                )
            else:
                warnings.append(
                    f"{event_dir}: matches missing winner/loser IDs "
                    f"(winner={missing_winners}, loser={missing_losers})"
                )
        if isinstance(standings, list) and standings and len(matches) == 0:
            warnings.append(f"{event_dir}: matches.json is empty but standings exist")

        if isinstance(standings, list):
            standing_ids = {item.get("user_id") for item in standings if item.get("user_id") is not None}
            if standing_ids:
                missing_in_standings = 0
                for item in matches:
                    winner_id = item.get("winner_id")
                    loser_id = item.get("loser_id")
                    if winner_id is not None and winner_id not in standing_ids:
                        missing_in_standings += 1
                    if loser_id is not None and loser_id not in standing_ids:
                        missing_in_standings += 1
                if missing_in_standings:
                    ratio = missing_in_standings / (len(matches) * 2)
                    if (
                        len(matches) >= MIN_MATCH_COUNT_FOR_MISMATCH_ERROR
                        and ratio > MAX_MISMATCHED_MATCH_ID_RATIO_ERROR
                    ):
                        errors.append(
                            f"{event_dir}: match IDs not in standings ratio {ratio:.1%} exceeds threshold"
                        )
                    else:
                        warnings.append(
                            f"{event_dir}: match IDs not in standings ({missing_in_standings} entries)"
                        )

    return errors, warnings


def iter_event_dirs(events_root: Path):
    for path in events_root.rglob("attr.json"):
        yield path.parent


def validate_tournaments_file(tournaments_file: Path, errors: List[str]) -> None:
    if not tournaments_file.exists():
        errors.append(f"{tournaments_file}: file not found")
        return
    with tournaments_file.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{tournaments_file}:{line_no}: invalid JSON ({exc})")
                continue
            events = record.get("events") or []
            if not isinstance(events, list):
                errors.append(f"{tournaments_file}:{line_no}: events is not a list")
                continue
            for event in events:
                path = event.get("path")
                if not path:
                    errors.append(f"{tournaments_file}:{line_no}: event missing path")
                    continue
                event_dir = Path(path)
                if not event_dir.exists():
                    errors.append(f"{tournaments_file}:{line_no}: missing event dir {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate downloaded start.gg data directories and schema."
    )
    parser.add_argument(
        "--events_root",
        default="data/startgg/events",
        help="Root directory containing event data.",
    )
    parser.add_argument(
        "--tournaments_file",
        default="data/startgg/tournaments.jsonl",
        help="tournaments.jsonl to validate paths.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors.",
    )
    args = parser.parse_args()

    events_root = Path(args.events_root)
    errors: List[str] = []
    warnings: List[str] = []

    if not events_root.exists():
        errors.append(f"{events_root}: events root not found")
    else:
        for event_dir in iter_event_dirs(events_root):
            event_errors, event_warnings = validate_event_dir(event_dir)
            errors.extend(event_errors)
            warnings.extend(event_warnings)

    validate_tournaments_file(Path(args.tournaments_file), errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"Validation failed: {len(errors)} issues found.")
        return 1

    if warnings:
        for warning in warnings:
            print(f"WARN: {warning}")
        if args.strict:
            print(f"Validation failed: {len(warnings)} warnings found (strict mode).")
            return 1

    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
