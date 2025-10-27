#!/usr/bin/env python3
"""Clean up tournaments.jsonl by removing entries that lack downloaded data."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TOURNAMENTS = Path("data/startgg/tournaments.jsonl")
DEFAULT_REQUIRED_FILES = ("attr.json", "matches.json", "standings.json", "seeds.json")
JSON_VERSION = "1.0"


@dataclass
class EventCheckResult:
    event: dict
    ok: bool
    reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove tournaments/events from tournaments.jsonl whose data files are missing."
    )
    parser.add_argument(
        "--tournaments-file",
        type=Path,
        default=DEFAULT_TOURNAMENTS,
        help=f"Path to tournaments.jsonl (default: {DEFAULT_TOURNAMENTS})",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root used to resolve event paths (default: current working directory).",
    )
    parser.add_argument(
        "--required-file",
        dest="required_files",
        action="append",
        help=(
            "File required to exist under each event directory. "
            "Can be specified multiple times. "
            f"Defaults to: {', '.join(DEFAULT_REQUIRED_FILES)}"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report missing events without rewriting tournaments.jsonl.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print matching events as well as removals.",
    )
    return parser.parse_args()


def normalise_path(raw_path: str, repo_root: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    return path


def check_event(event: dict, repo_root: Path, required_files: tuple[str, ...]) -> EventCheckResult:
    raw_path = event.get("path")
    if not raw_path:
        return EventCheckResult(event=event, ok=False, reason="event path missing")

    event_dir = normalise_path(raw_path, repo_root)
    if not event_dir.is_dir():
        return EventCheckResult(event=event, ok=False, reason=f"missing directory: {event_dir}")

    missing = [name for name in required_files if not (event_dir / name).is_file()]
    if missing:
        return EventCheckResult(
            event=event,
            ok=False,
            reason=f"missing files: {', '.join(missing)} within {event_dir}",
        )

    return EventCheckResult(event=event, ok=True)


def build_required_files(args: argparse.Namespace) -> tuple[str, ...]:
    if not args.required_files:
        return tuple(DEFAULT_REQUIRED_FILES)
    # Remove duplicates while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for item in args.required_files:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return tuple(ordered)


def clean_tournaments(
    tournaments: list[dict], repo_root: Path, required_files: tuple[str, ...], verbose: bool
) -> tuple[list[dict], list[str]]:
    cleaned: list[dict] = []
    report_lines: list[str] = []

    for entry in tournaments:
        events = entry.get("events", [])
        if not isinstance(events, list):
            report_lines.append(
                f"Skipped tournament {entry.get('tournament_id')} (events is not a list)."
            )
            continue

        kept_events: list[dict] = []
        removed_events: list[EventCheckResult] = []

        for event in events:
            result = check_event(event, repo_root, required_files)
            if result.ok:
                kept_events.append(event)
                if verbose:
                    report_lines.append(
                        f"[OK] tournament_id={entry.get('tournament_id')} "
                        f"event_id={event.get('event_id')} path={event.get('path')}"
                    )
            else:
                removed_events.append(result)
                report_lines.append(
                    f"[REMOVE] tournament_id={entry.get('tournament_id')} "
                    f"event_id={event.get('event_id')} reason={result.reason}"
                )

        if kept_events:
            new_entry = {k: v for k, v in entry.items() if k != "events"}
            new_entry["events"] = kept_events
            cleaned.append(new_entry)
        elif removed_events:
            report_lines.append(
                f"[DROP] tournament_id={entry.get('tournament_id')} removed entirely "
                "because no valid events remain."
            )

    return cleaned, report_lines


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_jsonl(records: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            record = dict(record)
            record["version"] = JSON_VERSION
            json.dump(record, handle, ensure_ascii=False)
            handle.write("\n")


def main() -> None:
    args = parse_args()
    tournaments_file = args.tournaments_file
    if not tournaments_file.is_file():
        raise SystemExit(f"{tournaments_file} does not exist.")

    repo_root = args.repo_root.resolve()
    required_files = build_required_files(args)

    tournaments = read_jsonl(tournaments_file)
    cleaned, report_lines = clean_tournaments(tournaments, repo_root, required_files, args.verbose)

    if report_lines:
        print("\n".join(report_lines))
    else:
        print("No missing tournaments detected.")

    if args.dry_run:
        print("Dry run mode enabled; tournaments.jsonl not modified.")
        return

    if cleaned == tournaments:
        print("No changes written; tournaments.jsonl already consistent.")
        return

    write_jsonl(cleaned, tournaments_file)
    print(f"Updated {tournaments_file} with {len(cleaned)} valid tournaments remaining.")


if __name__ == "__main__":
    main()
