#!/usr/bin/env python3
"""Split a region's users.jsonl / tournaments.jsonl into id-range shards, or join them back (issue #106).

    python3 scripts/common/manual/shard_jsonl.py --region North_America                 # both tables
    python3 scripts/common/manual/shard_jsonl.py --region North_America --table users.jsonl
    python3 scripts/common/manual/shard_jsonl.py --region North_America --join          # back to one file

data/startgg/<region>/users.jsonl becomes data/startgg/<region>/users/<user_id // 500000>.jsonl
(tournaments: tournament_id // 100000, see scripts/common/jsonl_table.py). Lines are moved verbatim and
keep their order within a shard; the old file is removed only after the shards were checked to hold
exactly its lines. Commit the result with `git add -A -- data/startgg/<region>`.

Everyone who writes the region's data must run scripts that know about shards (main from 2026-11):
older scripts would create users.jsonl next to users/, which every reader then refuses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.common import jsonl_table  # noqa: E402


def split(path: Path, dry_run: bool) -> None:
    if jsonl_table.is_sharded(path):
        print(f"{path}: already split into {jsonl_table.shard_dir(path)}/")
        return
    if not path.exists():
        print(f"{path}: not found, skipped")
        return
    spec = jsonl_table.spec_for(path)
    with open(path, encoding="utf-8") as f:
        lines = [line if line.endswith("\n") else line + "\n" for line in f if line.strip()]
    by_shard: dict[str, list[str]] = {}
    for i, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
        except ValueError as e:
            raise SystemExit(f"{path}:{i}: not JSON ({e}); fix the file first (validate_data.py)")
        by_shard.setdefault(jsonl_table.shard_of_record(path, spec, rec), []).append(line)
    sizes = {n: sum(len(l.encode()) for l in ls) for n, ls in by_shard.items()}
    print(f"{path}: {len(lines)} lines → {len(by_shard)} shards in {jsonl_table.shard_dir(path)}/ "
          f"(largest {max(sizes.values(), default=0) / 2**20:.1f} MiB)")
    if dry_run:
        return
    d = jsonl_table.shard_dir(path)
    if d.exists() and any(d.iterdir()):
        raise SystemExit(f"{d}/ exists and is not empty; remove it first")
    d.mkdir(parents=True, exist_ok=True)
    for name, ls in by_shard.items():
        (d / name).write_text("".join(ls), encoding="utf-8")
    got = Counter()
    for f in jsonl_table.shard_files(path):   # read back before the single file goes (both exist until then)
        got.update(f.read_text(encoding="utf-8").splitlines(keepends=True))
    if got != Counter(lines):
        raise SystemExit(f"{d}/ does not hold exactly the lines of {path}; {path} was kept (remove {d}/ and retry)")
    path.unlink()
    print(f"  ok, {path.name} removed")


def join(path: Path, dry_run: bool) -> None:
    if not jsonl_table.is_sharded(path):
        print(f"{path}: not split, skipped")
        return
    files = jsonl_table.shard_files(path)
    text = "".join(f.read_text(encoding="utf-8") for f in files)
    print(f"{jsonl_table.shard_dir(path)}/: {len(files)} shards → {path} ({len(text.encode()) / 2**20:.1f} MiB)")
    if dry_run:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for f in files:
        f.unlink()
    d = jsonl_table.shard_dir(path)
    if not any(d.iterdir()):
        d.rmdir()
    else:
        raise SystemExit(f"{d}/ holds other files besides shards; {tmp} written but not moved into place")
    os.replace(tmp, path)
    print("  ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True)
    ap.add_argument("--table", choices=sorted(jsonl_table.SHARDS), action="append",
                    help="default: every table that can be split")
    ap.add_argument("--data-dir", default=None, help="default: data/startgg/<region> (a temp dir in tests)")
    ap.add_argument("--join", action="store_true", help="join the shards back into one file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    data_dir = Path(args.data_dir or ROOT / "data" / "startgg" / args.region.replace(" ", "_"))
    for name in args.table or sorted(jsonl_table.SHARDS):
        (join if args.join else split)(data_dir / name, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
