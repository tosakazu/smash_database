"""Merge character_games.json sidecars into matches.json `details` field.

For each event under data/startgg/Japan/events/**/ that has BOTH a matches.json
and a character_games.json with character data, populate the `details` field
on the corresponding match (joined by match_id == set_id) using the same
schema that download.py / download_specific_event.py emit:

    details[]: {
        game_id, order_num, winner_id,
        entrant1_score, entrant2_score, stage,
        selections[]: {user_id, character_id, character_name}
    }

(download.py also writes selection_id alongside, but that is start.gg's
per-selection record ID — not useful for analysis. We omit it here.)

Existing matches.json fields are preserved untouched. The merge is idempotent.

Cleanup pass: if a match has a legacy `games` field (from an earlier version
of this script that wrote sidecar shape verbatim), it is removed and its data
is converted into `details` instead. Same data ends up in the canonical place.

Atomic write: temp file + rename. Format preservation: indent style of the
original matches.json is detected and preserved.

Top-level list shape matches.json (legacy ~126 files) is skipped — those need
re-fetch via download.py to gain the v2 wrapper before they can carry details.

Usage:
    cd smash_db_tournament
    ../ranking_eval/.venv/bin/python3 -u scripts/fetch/merge_character_games.py [--dry-run]

Options:
    --dry-run    Print what would change, don't write
    --since YYYY-MM-DD   Only process events on/after this date (default: all)
    --limit N    Process at most N events (for testing)
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
SMASH_DB = HERE.parents[3]   # scripts/common/manual/x.py → smash_db_tournament/
EVENTS_DIR = SMASH_DB / "data" / "startgg" / "Japan" / "events"


def detect_indent(file_path: Path) -> int | None:
    with open(file_path, "rb") as f:
        head = f.read(200)
    return 2 if b'\n  "' in head else None


def atomic_write_json(data: dict, file_path: Path, indent: int | None):
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=file_path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def parse_event_date(event_dir: Path) -> str | None:
    try:
        rel = event_dir.relative_to(EVENTS_DIR).parts
        if len(rel) < 3:
            return None
        return f"{rel[0]}-{rel[1]}-{rel[2]}"
    except ValueError:
        return None


def sidecar_games_to_details(games: list) -> list:
    """Convert character_games sidecar `games[]` shape -> matches.json `details[]` shape.

    Fields aligned with download.py L490-509 / download_specific_event.py L116-145,
    minus selection_id (start.gg's per-selection record ID — not useful for
    analysis; download.py's output still includes it for capture parity but
    consumers should not rely on it being populated).
    """
    out = []
    for g in games or []:
        selections = [
            {
                "user_id": s.get("user_id"),
                "character_id": s.get("character_id"),
                "character_name": s.get("character_name"),
            }
            for s in (g.get("selections") or [])
        ]
        out.append({
            "game_id": g.get("game_id"),
            "order_num": g.get("order_num"),
            "winner_id": g.get("winner_user_id"),
            "entrant1_score": g.get("entrant1_score"),
            "entrant2_score": g.get("entrant2_score"),
            "stage": g.get("stage_name"),
            "selections": selections,
        })
    return out


def merge_one(matches_path: Path, dry_run: bool) -> tuple[str, int]:
    """Merge sidecar games into matches.json `details`.

    Returns (status, n_match_records_touched). Status:
      'updated' | 'no_change' | 'legacy_list' | 'shape_error' | 'no_chargames' | 'parse_error'
    """
    event_dir = matches_path.parent
    char_path = event_dir / "character_games.json"

    try:
        with open(matches_path, "r", encoding="utf-8") as f:
            matches_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[error] {matches_path}: {e}", file=sys.stderr)
        return ("parse_error", 0)

    if isinstance(matches_data, list):
        # Legacy top-level list shape — out of scope.
        return ("legacy_list", 0)
    if not isinstance(matches_data, dict):
        return ("shape_error", 0)
    data_list = matches_data.get("data")
    if not isinstance(data_list, list):
        return ("shape_error", 0)

    games_by_set_id: dict = {}
    if char_path.exists():
        try:
            with open(char_path, "r", encoding="utf-8") as f:
                char_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[error] {char_path}: {e}", file=sys.stderr)
            return ("parse_error", 0)
        for m in char_data.get("matches", []):
            sid = m.get("set_id")
            g = m.get("games")
            if sid is not None and g:
                games_by_set_id[sid] = g

    n_changed = 0
    for match in data_list:
        if not isinstance(match, dict):
            continue

        # Cleanup: remove legacy 'games' field if present (prior script version).
        if "games" in match:
            del match["games"]
            n_changed += 1

        mid = match.get("match_id")
        if mid is None:
            continue
        sidecar_games = games_by_set_id.get(mid)
        if not sidecar_games:
            continue

        converted = sidecar_games_to_details(sidecar_games)
        if match.get("details") != converted:
            match["details"] = converted
            n_changed += 1

    if n_changed == 0:
        if not games_by_set_id:
            return ("no_chargames", 0)
        return ("no_change", 0)

    if not dry_run:
        indent = detect_indent(matches_path)
        atomic_write_json(matches_data, matches_path, indent)

    return ("updated", n_changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not EVENTS_DIR.exists():
        print(f"events dir not found: {EVENTS_DIR}", file=sys.stderr)
        sys.exit(2)

    candidates = sorted(EVENTS_DIR.rglob("matches.json"))
    if args.since:
        candidates = [
            p for p in candidates
            if (parse_event_date(p.parent) or "0") >= args.since
        ]
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Scanning {len(candidates)} matches.json files.")

    counts = {
        "updated": 0,
        "no_change": 0,
        "no_chargames": 0,
        "legacy_list": 0,
        "shape_error": 0,
        "parse_error": 0,
    }
    total_touched = 0

    for i, matches_path in enumerate(candidates, start=1):
        status, n = merge_one(matches_path, args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        if status == "updated":
            total_touched += n
            rel = matches_path.parent.relative_to(EVENTS_DIR)
            print(f"[{i}/{len(candidates)}] touched {n} records <- {rel}")

    print()
    print("=" * 60)
    print(f"updated events       : {counts['updated']}")
    print(f"no-op (no change)    : {counts['no_change']}")
    print(f"no sidecar           : {counts['no_chargames']}")
    print(f"legacy list shape    : {counts['legacy_list']} (skipped)")
    print(f"shape errors         : {counts['shape_error']}")
    print(f"parse errors         : {counts['parse_error']}")
    print(f"total match records  : {total_touched}")
    if args.dry_run:
        print("(dry-run: no files written)")


if __name__ == "__main__":
    main()
