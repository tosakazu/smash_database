"""An index file (users.jsonl / tournaments.jsonl) that may be split into shards by id range.

GitHub refuses files over 100 MiB, and North America's users.jsonl reached 97.9 MiB in 2026-11 (issue #106).
A table is addressed by its old file path everywhere (data/startgg/<region>/users.jsonl); on disk it is one of

  - the single file  data/startgg/<region>/users.jsonl            (every region until it is migrated)
  - a directory      data/startgg/<region>/users/<n>.jsonl         (n = user_id // SHARDS["users.jsonl"].width)

never both. Lines and their keys are exactly those of the single file; only where they live changes.
Ids only grow, so a record never moves to another shard, new records land in the newest shard, and the
table gains a file every `width` ids instead of ever needing a re-split. A rewrite touches only the
shards whose text changed.

Readers outside this repository (spsp-build) only need: if the directory exists, read every `<n>.jsonl`
in it in ascending n; otherwise read the single file.

Migrate a region with scripts/common/manual/shard_jsonl.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShardSpec:
    key: str      # integer id field of a record
    width: int    # ids per shard


# Sized from North America (2026-11): users 334k rows / max id 3.52M → 8 shards of ≤ ~25 MiB;
# tournaments 158k rows / max id 0.97M → 10 shards of ≤ ~9 MiB.
SHARDS = {
    "users.jsonl": ShardSpec("user_id", 500_000),
    "tournaments.jsonl": ShardSpec("tournament_id", 100_000),
}

WARN_BYTES = 80 * 1024 * 1024   # any single data file over this is reported (GitHub's hard limit is 100 MiB)


def spec_for(path) -> ShardSpec | None:
    return SHARDS.get(Path(path).name)


def shard_dir(path) -> Path:
    """data/.../users.jsonl → data/.../users"""
    p = Path(path)
    return p.with_name(p.name[: -len(".jsonl")] if p.name.endswith(".jsonl") else p.name + ".d")


def is_sharded(path) -> bool:
    p = Path(path)
    d = shard_dir(p)
    if d.is_dir():
        if p.exists():
            raise RuntimeError(f"{p} and {d}/ both exist; keep one (scripts/common/manual/shard_jsonl.py)")
        return True
    return False


def exists(path) -> bool:
    return is_sharded(path) or Path(path).exists()


def shard_files(path) -> list[Path]:
    """The shard files of a sharded table, in id order."""
    out = []
    for f in shard_dir(path).iterdir():
        if f.suffix == ".jsonl" and f.stem.isdigit():
            out.append(f)
    return sorted(out, key=lambda f: int(f.stem))


def files(path) -> list[Path]:
    """Every file holding the table's lines, in read order ([] if the table does not exist)."""
    if is_sharded(path):
        return shard_files(path)
    return [Path(path)] if Path(path).exists() else []


def read_text(path) -> str:
    return "".join(f.read_text(encoding="utf-8") for f in files(path))


def iter_lines(path):
    """Lines (with their newline) of the whole table, like iterating over the single file."""
    for f in files(path):
        with open(f, encoding="utf-8") as fh:
            yield from fh


def shard_name(spec: ShardSpec, key_value) -> str:
    return f"{int(key_value) // spec.width}.jsonl"


def shard_of_record(path, spec: ShardSpec, rec: dict) -> str:
    v = rec.get(spec.key) if isinstance(rec, dict) else None
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError(f"{path}: a record without an integer {spec.key} cannot be placed in a shard: {str(rec)[:200]}")
    return shard_name(spec, v)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)




def write_shards(path, by_shard: dict[str, list[str]]) -> int:
    """Make the table's directory hold exactly these shards; a shard whose text is unchanged is not
    written. Returns the number of files written or removed."""
    d = shard_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for name, lines in by_shard.items():
        f = d / name
        text = "".join(lines)
        if f.exists() and f.read_text(encoding="utf-8") == text:
            continue
        _atomic_write(f, text)
        n += 1
    for f in shard_files(path):
        if f.name not in by_shard:
            f.unlink()
            n += 1
    return n


def append_shards(path, by_shard: dict[str, list[str]]) -> None:
    d = shard_dir(path)
    for name, lines in by_shard.items():
        with open(d / name, "a", encoding="utf-8") as f:
            f.write("".join(lines))
            f.flush()
            os.fsync(f.fileno())


def total_bytes(path) -> int:
    return sum(f.stat().st_size for f in files(path))
