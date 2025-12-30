#!/usr/bin/env python3
"""Ensure every downloaded event directory is registered in tournaments.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_TOURNAMENTS = Path("data/startgg/tournaments.jsonl")
DEFAULT_EVENTS_ROOT = Path("data/startgg/events")
DEFAULT_API_URL = "https://api.start.gg/gql/alpha"
JSON_VERSION = "1.0"

EVENT_TOURNAMENT_QUERY = """
query EventTournament($eventId: ID!) {
  event(id: $eventId) {
    id
    name
    tournament {
      id
      name
    }
  }
}
""".strip()


@dataclass
class MissingEvent:
    path: Path
    event_id: Optional[int]
    event_name: Optional[str]
    tournament_name: Optional[str]
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check that every event directory under data/startgg/events has a corresponding "
            "entry in tournaments.jsonl, and optionally add the missing ones."
        )
    )
    parser.add_argument(
        "--tournaments-file",
        type=Path,
        default=DEFAULT_TOURNAMENTS,
        help=f"Path to tournaments.jsonl (default: {DEFAULT_TOURNAMENTS})",
    )
    parser.add_argument(
        "--events-root",
        type=Path,
        default=DEFAULT_EVENTS_ROOT,
        help=f"Root directory containing event folders (default: {DEFAULT_EVENTS_ROOT})",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root used to compute relative paths (default: current working directory).",
    )
    parser.add_argument(
        "--token",
        help="start.gg API token. Required to fetch tournament IDs when applying fixes.",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"start.gg GraphQL endpoint (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update tournaments.jsonl with missing entries. Requires --token.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=None,
        help="Indent width when rewriting tournaments.jsonl (default: compact JSON).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned additions without writing tournaments.jsonl.",
    )
    return parser.parse_args()


def load_tournaments(path: Path) -> List[dict]:
    entries: List[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def build_event_index(tournaments: List[dict]) -> Dict[str, dict]:
    index: Dict[str, dict] = {}
    for entry in tournaments:
        events = entry.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            path = event.get("path")
            if isinstance(path, str):
                index[path] = event
    return index


def iter_event_dirs(events_root: Path) -> List[Path]:
    return [path.parent for path in events_root.glob("**/attr.json")]


def to_repo_relative(path: Path, repo_root: Path) -> str:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    return rel.as_posix()


def read_attr(event_dir: Path) -> dict | None:
    attr_file = event_dir / "attr.json"
    if not attr_file.is_file():
        return None
    try:
        with attr_file.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return None


def fetch_tournament_id(event_id: int, api_url: str, token: str) -> tuple[Optional[int], Optional[str]]:
    payload = json.dumps({"query": EVENT_TOURNAMENT_QUERY, "variables": {"eventId": event_id}}).encode("utf-8")
    request = Request(api_url, data=payload)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP error {exc.code} while fetching event {event_id}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while fetching event {event_id}: {exc.reason}") from exc

    errors = data.get("errors")
    if errors:
        message = errors[0].get("message", "unknown error")
        raise RuntimeError(f"GraphQL error for event {event_id}: {message}")

    event_data = data.get("data", {}).get("event")
    if not event_data:
        return None, None
    tournament = event_data.get("tournament")
    if not tournament:
        return None, None
    tid = tournament.get("id")
    name = tournament.get("name")
    try:
        return int(tid), name
    except (TypeError, ValueError):
        return None, name


def write_tournaments(entries: List[dict], path: Path, indent: Optional[int]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            serialisable = dict(entry)
            serialisable["version"] = JSON_VERSION
            json.dump(serialisable, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")


def main() -> int:
    args = parse_args()

    tournaments_file = args.tournaments_file
    events_root = args.events_root
    repo_root = args.repo_root.resolve()

    if not tournaments_file.is_file():
        print(f"{tournaments_file} が見つかりません。", file=sys.stderr)
        return 1
    if not events_root.is_dir():
        print(f"{events_root} が見つかりません。", file=sys.stderr)
        return 1

    tournaments = load_tournaments(tournaments_file)
    event_index = build_event_index(tournaments)

    missing_events: List[MissingEvent] = []

    for event_dir in iter_event_dirs(events_root):
        attr = read_attr(event_dir)
        rel_path = to_repo_relative(event_dir, repo_root)
        if rel_path in event_index:
            continue

        if attr is None:
            missing_events.append(
                MissingEvent(
                    path=event_dir,
                    event_id=None,
                    event_name=None,
                    tournament_name=None,
                    reason="attr.json が読み込めません",
                )
            )
            continue

        event_id = attr.get("event_id")
        event_name = attr.get("event_name")
        tournament_name = attr.get("tournament_name")
        reason = "tournaments.jsonl にパスが未登録"
        missing_events.append(
            MissingEvent(
                path=event_dir,
                event_id=event_id if isinstance(event_id, int) else None,
                event_name=event_name if isinstance(event_name, str) else None,
                tournament_name=tournament_name if isinstance(tournament_name, str) else None,
                reason=reason,
            )
        )

    if not missing_events:
        print("欠落しているイベントは見つかりませんでした。")
        return 0

    print("登録されていないイベントが見つかりました:")
    for item in missing_events:
        rel = to_repo_relative(item.path, repo_root)
        print(
            f"- {rel} | event_id={item.event_id} | event_name={item.event_name} | "
            f"tournament_name={item.tournament_name} | reason={item.reason}"
        )

    if not args.apply:
        print(" --apply を指定すると tournaments.jsonl を更新できます。")
        return 1

    if not args.token:
        print("--apply には --token が必要です。", file=sys.stderr)
        return 1

    tournaments_by_id: Dict[int, dict] = {
        entry.get("tournament_id"): entry for entry in tournaments if isinstance(entry.get("tournament_id"), int)
    }

    updated = False
    for item in missing_events:
        if item.event_id is None:
            print(f"[SKIP] {item.path}: event_id が取得できないため追加できません。", file=sys.stderr)
            continue
        try:
            tournament_id, tournament_name = fetch_tournament_id(item.event_id, args.api_url, args.token)
        except RuntimeError as exc:
            print(f"[ERROR] {item.path}: {exc}", file=sys.stderr)
            continue

        if tournament_id is None:
            print(f"[SKIP] {item.path}: トーナメントIDを取得できませんでした。", file=sys.stderr)
            continue

        rel_path = to_repo_relative(item.path, repo_root)
        tournament_entry = tournaments_by_id.get(tournament_id)
        event_payload = {
            "event_id": item.event_id,
            "event_name": item.event_name or "",
            "path": rel_path,
        }

        if tournament_entry:
            events = tournament_entry.setdefault("events", [])
            if any(ev.get("event_id") == item.event_id or ev.get("path") == rel_path for ev in events):
                print(f"[SKIP] {rel_path}: 既に登録済みです。")
                continue
            events.append(event_payload)
            print(f"[ADD] 既存トーナメント {tournament_id} にイベント {item.event_id} を追加しました。")
        else:
            name = tournament_name or item.tournament_name or f"Tournament {tournament_id}"
            new_entry = {
                "tournament_id": tournament_id,
                "name": name,
                "events": [event_payload],
            }
            tournaments.append(new_entry)
            tournaments_by_id[tournament_id] = new_entry
            print(f"[ADD] 新規トーナメント {tournament_id} ({name}) を追加しました。")
        updated = True

    if not updated:
        print("tournaments.jsonl に対する変更はありませんでした。")
        return 1

    if args.dry_run:
        print("Dry-run モードのため tournaments.jsonl は書き換えていません。")
        return 0

    write_tournaments(tournaments, tournaments_file, args.indent)
    print(f"{tournaments_file} を更新しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
