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


def validate_event_dir(event_dir: Path) -> List[str]:
    errors: List[str] = []
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

        context = f"{event_dir}/{filename}"
        if filename == "attr.json":
            validate_attr(payload, context, errors)
        else:
            validate_list_container(payload, context, errors)

    return errors


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
    args = parser.parse_args()

    events_root = Path(args.events_root)
    errors: List[str] = []

    if not events_root.exists():
        errors.append(f"{events_root}: events root not found")
    else:
        for event_dir in iter_event_dirs(events_root):
            errors.extend(validate_event_dir(event_dir))

    validate_tournaments_file(Path(args.tournaments_file), errors)

    if errors:
        for error in errors:
            print(error)
        print(f"Validation failed: {len(errors)} issues found.")
        return 1

    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
