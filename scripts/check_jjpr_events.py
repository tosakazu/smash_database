import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


DEFAULT_JJPR_JSON = Path("data/JJPR/check/JJPREvents.json")
DEFAULT_EVENTS_ROOT = Path("data/startgg/events")
DEFAULT_MISSING_DIR = Path("data/JJPR/missing_events")


REPLACEMENTS = (
    (" ", "_"),
    ("/", "-"),
    ("／", "-"),
)


def normalize_name(name: str) -> str:
    normalized = name
    for src, dest in REPLACEMENTS:
        normalized = normalized.replace(src, dest)
    return normalized.casefold()


def build_tournament_index(events_root: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = defaultdict(list)
    for day_dir in events_root.glob("*/*/*/*"):
        if not day_dir.is_dir():
            continue
        for tournament_dir in day_dir.iterdir():
            if not tournament_dir.is_dir():
                continue
            key = normalize_name(tournament_dir.name)
            index[key].append(tournament_dir)
    return index


def load_target_events(json_path: Path) -> list[dict]:
    with json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("targetEvents", [])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that each tournamentName in JJPREvents.json appears in the "
            "startgg events directory structure."
        )
    )
    parser.add_argument(
        "--jjpr_json",
        type=Path,
        default=DEFAULT_JJPR_JSON,
        help=f"Path to JJPREvents.json (default: {DEFAULT_JJPR_JSON})",
    )
    parser.add_argument(
        "--events_root",
        type=Path,
        default=DEFAULT_EVENTS_ROOT,
        help=f"Root directory for event data (default: {DEFAULT_EVENTS_ROOT})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print matching directory paths for successful checks.",
    )
    parser.add_argument(
        "--missing_dir",
        type=Path,
        default=DEFAULT_MISSING_DIR,
        help=f"Directory to save details about missing tournaments (default: {DEFAULT_MISSING_DIR})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.jjpr_json.is_file():
        print(f"JJPREvents file not found: {args.jjpr_json}", file=sys.stderr)
        raise SystemExit(1)
    if not args.events_root.is_dir():
        print(f"Events root directory not found: {args.events_root}", file=sys.stderr)
        raise SystemExit(1)

    target_events = load_target_events(args.jjpr_json)
    index = build_tournament_index(args.events_root)

    missing: list[dict] = []
    for event in target_events:
        tournament_name = event.get("tournamentName")
        event_id = event.get("id")
        if not tournament_name:
            event_record = dict(event)
            event_record["missing_reason"] = "tournamentName missing"
            missing.append(event_record)
            continue

        normalized = normalize_name(tournament_name)
        matches = index.get(normalized, [])
        if matches:
            if args.verbose:
                match_paths = ", ".join(str(p) for p in matches)
                print(f"[OK] {tournament_name} (event_id={event_id}): {match_paths}")
        else:
            event_record = dict(event)
            event_record["missing_reason"] = "no matching directory"
            missing.append(event_record)

    if missing:
        args.missing_dir.mkdir(parents=True, exist_ok=True)
        output_path = args.missing_dir / "missing_events.json"
        payload = {
            "source": str(args.jjpr_json),
            "events_root": str(args.events_root),
            "missing_events": missing,
        }
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("\nMissing tournament directories:", file=sys.stderr)
        for entry in missing:
            print(
                f"  - event_id={entry.get('id')}, "
                f"tournamentName='{entry.get('tournamentName')}': "
                f"{entry.get('missing_reason')}",
                file=sys.stderr,
            )
        print(f"\nMissing event details saved to {output_path}", file=sys.stderr)
        raise SystemExit(1)

    print(f"All {len(target_events)} tournaments found under {args.events_root}.")


if __name__ == "__main__":
    main()
