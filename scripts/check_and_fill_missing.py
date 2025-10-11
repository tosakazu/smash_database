import argparse
import json
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    OpenAI = None

from check_jjpr_events import (
    DEFAULT_EVENTS_ROOT as CHECK_DEFAULT_EVENTS_ROOT,
    DEFAULT_JJPR_JSON as CHECK_DEFAULT_JJPR_JSON,
    DEFAULT_MISSING_DIR as CHECK_DEFAULT_MISSING_DIR,
    build_tournament_index,
    load_target_events,
    normalize_name,
)
from fill_missing_events import (
    DEFAULT_DONE_EVENTS,
    DEFAULT_EVENT_PROMPT,
    DEFAULT_STARTGG_DIR,
    DEFAULT_TOURNAMENTS_FILE,
    DEFAULT_USERS_FILE,
    process_event,
    rewrite_tournaments_file,
)
from utils import (
    read_set,
    read_tournaments_jsonl,
    read_users_jsonl,
    set_api_parameters,
    set_indent_num,
    set_retry_parameters,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the JJPR event presence check and, if needed, fetch missing events automatically."
        )
    )
    parser.add_argument("--token", required=True, help="start.gg API token")
    parser.add_argument(
        "--jjpr_json",
        type=Path,
        default=CHECK_DEFAULT_JJPR_JSON,
        help=f"Path to JJPREvents.json (default: {CHECK_DEFAULT_JJPR_JSON})",
    )
    parser.add_argument(
        "--events_root",
        type=Path,
        default=CHECK_DEFAULT_EVENTS_ROOT,
        help=f"Root directory where events are stored (default: {CHECK_DEFAULT_EVENTS_ROOT})",
    )
    parser.add_argument(
        "--missing_dir",
        type=Path,
        default=CHECK_DEFAULT_MISSING_DIR,
        help=(
            "Directory used to store missing_events.json reports "
            f"(default: {CHECK_DEFAULT_MISSING_DIR})"
        ),
    )
    parser.add_argument(
        "--startgg_dir",
        type=Path,
        default=DEFAULT_STARTGG_DIR,
        help=f"Directory to write downloaded event data (default: {DEFAULT_STARTGG_DIR})",
    )
    parser.add_argument(
        "--users_file_path",
        type=Path,
        default=DEFAULT_USERS_FILE,
        help=f"Path to users.jsonl (default: {DEFAULT_USERS_FILE})",
    )
    parser.add_argument(
        "--tournament_file_path",
        type=Path,
        default=DEFAULT_TOURNAMENTS_FILE,
        help=f"Path to tournaments.jsonl (default: {DEFAULT_TOURNAMENTS_FILE})",
    )
    parser.add_argument(
        "--done_events_path",
        type=Path,
        default=DEFAULT_DONE_EVENTS,
        help=f"Path to done events log (default: {DEFAULT_DONE_EVENTS})",
    )
    parser.add_argument(
        "--event_prompt_file_path",
        type=Path,
        default=DEFAULT_EVENT_PROMPT,
        help=f"Prompt file for OpenAI-based labelling (default: {DEFAULT_EVENT_PROMPT})",
    )
    parser.add_argument(
        "--openai_api_key",
        default="",
        help="OpenAI API key (optional). Required only if label generation is desired.",
    )
    parser.add_argument(
        "--indent_num", type=int, default=2, help="Indentation level for saved JSON files."
    )
    parser.add_argument(
        "--max_retries",
        type=int,
        default=10,
        help="Maximum number of retries for start.gg API requests.",
    )
    parser.add_argument(
        "--retry_delay",
        type=int,
        default=5,
        help="Delay between retries for start.gg API requests (seconds).",
    )
    parser.add_argument(
        "--overwrite_done",
        action="store_true",
        help="Re-download events even if they are already marked as processed.",
    )
    parser.add_argument(
        "--skip_fill",
        action="store_true",
        help="Only run the check and do not attempt to download missing events.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed progress information for both checking and downloading.",
    )
    return parser.parse_args()


def find_missing_events(jjpr_json: Path, events_root: Path, verbose: bool) -> list[dict]:
    target_events = load_target_events(jjpr_json)
    index = build_tournament_index(events_root)

    missing: list[dict] = []
    for event in target_events:
        tournament_name = event.get("tournamentName")
        event_id = event.get("id")
        if not tournament_name:
            entry = dict(event)
            entry["missing_reason"] = "tournamentName missing"
            missing.append(entry)
            continue

        normalized = normalize_name(tournament_name)
        matches = index.get(normalized, [])
        if matches:
            if verbose:
                match_paths = ", ".join(str(path) for path in matches)
                print(f"[OK] {tournament_name} (event_id={event_id}): {match_paths}")
        else:
            entry = dict(event)
            entry["missing_reason"] = "no matching directory"
            missing.append(entry)

    return missing


def write_missing_report(
    missing: list[dict], jjpr_json: Path, events_root: Path, missing_dir: Path
) -> Path | None:
    if not missing:
        report_path = missing_dir / "missing_events.json"
        if report_path.exists():
            report_path.unlink()
        return None

    missing_dir.mkdir(parents=True, exist_ok=True)
    report_path = missing_dir / "missing_events.json"
    payload = {
        "source": str(jjpr_json),
        "events_root": str(events_root),
        "missing_events": missing,
    }
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return report_path


def prepare_openai(event_prompt_path: Path, api_key: str, verbose: bool):
    if not api_key:
        return None, None
    if OpenAI is None:
        print(
            "OpenAI package not installed; proceeding without label analysis.",
            file=sys.stderr,
        )
        return None, None
    try:
        client = OpenAI(api_key=api_key)
    except Exception as exc:  # pragma: no cover - network/auth errors
        print(f"Failed to initialise OpenAI client: {exc}", file=sys.stderr)
        return None, None

    if event_prompt_path.is_file():
        with event_prompt_path.open("r", encoding="utf-8") as f:
            prompt = f.read()
        if verbose:
            print(f"[INFO] Loaded event prompt from {event_prompt_path}")
    else:
        print(
            f"Event prompt file not found: {event_prompt_path}. Continuing without labels.",
            file=sys.stderr,
        )
        prompt = None
    return client, prompt


def download_missing_events(
    missing: list[dict],
    args: argparse.Namespace,
) -> tuple[int, int]:
    if not missing:
        return 0, 0

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(str(args.url), args.token)  # type: ignore[attr-defined]

    for target in (
        args.startgg_dir,
        args.users_file_path.parent,
        args.tournament_file_path.parent,
        args.done_events_path.parent,
    ):
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)

    done_events = read_set(str(args.done_events_path), as_int=True)
    users = read_users_jsonl(str(args.users_file_path))
    tournaments = read_tournaments_jsonl(str(args.tournament_file_path))

    if args.verbose:
        print(
            f"[INFO] Missing events: {len(missing)} | "
            f"Known users: {len(users)} | Known tournaments: {len(tournaments)} | "
            f"Done events: {len(done_events)}"
        )

    openai_client, event_prompt = prepare_openai(
        args.event_prompt_file_path, args.openai_api_key, args.verbose
    )

    processed = 0
    failed = 0
    for entry in missing:
        event_id = entry.get("id")
        print(f"[FETCH] event_id={event_id}, tournament='{entry.get('tournamentName')}'")
        success = process_event(
            entry,
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
        else:
            failed += 1

    if args.verbose:
        print(
            f"[INFO] Download summary: processed={processed}, failed={failed}, "
            f"skipped={len(missing) - processed - failed}"
        )

    if processed > 0:
        rewrite_tournaments_file(tournaments, args.tournament_file_path)
    return processed, failed


def main():
    args = parse_args()

    args.url = "https://api.start.gg/gql/alpha"  # ensure attr exists for reuse

    if not args.jjpr_json.is_file():
        print(f"JJPREvents file not found: {args.jjpr_json}", file=sys.stderr)
        return 1
    if not args.events_root.is_dir():
        print(f"Event data directory not found: {args.events_root}", file=sys.stderr)
        return 1

    print("[STEP] Checking JJPR events against local dataset...")
    missing_first = find_missing_events(args.jjpr_json, args.events_root, args.verbose)
    report_path = write_missing_report(
        missing_first, args.jjpr_json, args.events_root, args.missing_dir
    )

    if missing_first:
        print("\n[RESULT] Missing tournaments detected:")
        for entry in missing_first:
            print(
                f"  - event_id={entry.get('id')}, "
                f"tournamentName='{entry.get('tournamentName')}', "
                f"reason='{entry.get('missing_reason')}'"
            )
        if report_path:
            print(f"[INFO] Missing event report saved to {report_path}")
    else:
        print("[RESULT] No missing tournaments detected.")

    if missing_first and not args.skip_fill:
        print("\n[STEP] Attempting to download missing events...")
        processed, failed = download_missing_events(missing_first, args)
        if failed:
            print(
                f"[WARN] {failed} events failed to download. Review logs above.",
                file=sys.stderr,
            )

        print("\n[STEP] Re-checking after download...")
        missing_after = find_missing_events(args.jjpr_json, args.events_root, args.verbose)
        report_path = write_missing_report(
            missing_after, args.jjpr_json, args.events_root, args.missing_dir
        )
        if missing_after:
            print("\n[FINAL] Still missing tournaments:")
            for entry in missing_after:
                print(
                    f"  - event_id={entry.get('id')}, "
                    f"tournamentName='{entry.get('tournamentName')}', "
                    f"reason='{entry.get('missing_reason')}'"
                )
            if report_path:
                print(f"[INFO] Updated missing event report saved to {report_path}")
            return 1

        print("[FINAL] All tournaments accounted for after download.")
        return 0

    return 0 if not missing_first else 1


if __name__ == "__main__":
    sys.exit(main())
