from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
UTC = timezone.utc
DEFAULT_START_DATE = date(2018, 12, 29)


@dataclass
class CheckedRecord:
    checked_at_utc: str
    workflow: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update docs/chore-tornament markdown from tournament folders."
    )
    parser.add_argument(
        "--docs-dir",
        default="docs/chore-tornament",
        help="Directory that stores the generated markdown and metadata.",
    )
    parser.add_argument(
        "--events-root",
        default="data/startgg/events/Japan",
        help="Root directory that contains YYYY/MM/DD tournament folders.",
    )
    parser.add_argument(
        "--mark-start",
        help="Start date to mark as checked by GitHub Actions (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--mark-end",
        help="End date to mark as checked by GitHub Actions (YYYY-MM-DD). Defaults to --mark-start.",
    )
    parser.add_argument(
        "--workflow",
        help="Workflow name to record with marked dates.",
    )
    parser.add_argument(
        "--checked-at",
        help="Checked timestamp in ISO-8601 UTC. Defaults to current UTC time.",
    )
    return parser.parse_args()


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def load_metadata(metadata_path: Path) -> Dict[str, CheckedRecord]:
    if not metadata_path.exists():
        return {}

    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {
        day: CheckedRecord(
            checked_at_utc=value["checked_at_utc"],
            workflow=value["workflow"],
        )
        for day, value in raw.items()
    }


def save_metadata(metadata_path: Path, metadata: Dict[str, CheckedRecord]) -> None:
    serialized = {
        day: {
            "checked_at_utc": value.checked_at_utc,
            "workflow": value.workflow,
        }
        for day, value in sorted(metadata.items())
    }
    metadata_path.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def mark_checked_dates(
    metadata: Dict[str, CheckedRecord],
    start: date | None,
    end: date | None,
    workflow: str | None,
    checked_at_utc: str,
) -> None:
    if start is None:
        return

    if workflow is None:
        raise ValueError("--workflow is required when --mark-start is used.")

    final_end = end or start
    for day in daterange(start, final_end):
        metadata[day.isoformat()] = CheckedRecord(
            checked_at_utc=checked_at_utc,
            workflow=workflow,
        )


def folder_exists(events_root: Path, day: date) -> bool:
    return (events_root / day.strftime("%Y/%m/%d")).is_dir()


def format_checked_at_jst(checked_at_utc: str) -> str:
    checked_at = datetime.fromisoformat(checked_at_utc.replace("Z", "+00:00"))
    return checked_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def render_markdown(
    events_root: Path,
    metadata: Dict[str, CheckedRecord],
    generated_at_utc: datetime,
) -> str:
    today_jst = datetime.now(JST).date()
    lines = [
        "# Chore Tournament Log",
        "",
        "GitHub Actions が確認した日付と、`data/startgg/events/Japan/YYYY/MM/DD` の存在状況を記録します。",
        "",
        f"- 集計開始日: `{DEFAULT_START_DATE.isoformat()}`",
        f"- 集計終了日: `{today_jst.isoformat()}`",
        f"- 最終更新 (UTC): `{generated_at_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        "",
        "| Date | Folder Exists | Checked By GitHub Actions | Last Checked At (JST) | Workflow |",
        "| --- | --- | --- | --- | --- |",
    ]

    for day in daterange(DEFAULT_START_DATE, today_jst):
        key = day.isoformat()
        record = metadata.get(key)
        exists = "yes" if folder_exists(events_root, day) else "no"
        checked = "yes" if record else "no"
        checked_at_jst = format_checked_at_jst(record.checked_at_utc) if record else ""
        workflow = record.workflow if record else ""
        lines.append(
            f"| {day.isoformat()} | {exists} | {checked} | {checked_at_jst} | {workflow} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    docs_dir = Path(args.docs_dir)
    events_root = Path(args.events_root)
    metadata_path = docs_dir / "checked_dates.json"
    markdown_path = docs_dir / "README.md"

    docs_dir.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(metadata_path)
    checked_at_utc = args.checked_at or datetime.now(UTC).replace(microsecond=0).isoformat()

    mark_start = parse_iso_date(args.mark_start) if args.mark_start else None
    mark_end = parse_iso_date(args.mark_end) if args.mark_end else None
    mark_checked_dates(metadata, mark_start, mark_end, args.workflow, checked_at_utc)

    save_metadata(metadata_path, metadata)
    markdown = render_markdown(events_root, metadata, datetime.now(UTC).replace(microsecond=0))
    markdown_path.write_text(markdown, encoding="utf-8")


if __name__ == "__main__":
    main()
