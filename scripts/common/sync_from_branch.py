"""Bring another writer's changes into this checkout without a git merge (used by download-north-america).

The Actions keep their own data branch (auto/<Region>) next to the region operator's branch
(data-<Region>), so neither ever has to merge the other's pushes. Before each Actions run fetches new
tournaments, this script carries over what the operator changed since the last sync:

    python scripts/common/sync_from_branch.py --region North_America --old <sha> --new <sha>

<old> is the operator commit synced last time (recorded in auto_sync.json), <new> the operator's
current head. Both commits must be present locally (a depth-1 fetch of each is enough: only the two
trees are compared). The working tree is the Actions branch.

Rules (the operator wins whatever they changed; everything they did not touch stays as the Actions have it):
  - index files with one record per key (users.jsonl / tournaments.jsonl / tournament_images.jsonl, and
    done.csv per line): per key, if the operator's version changed between <old> and <new>, take theirs
    (removed keys are removed); keys only the Actions have stay. Lines are copied verbatim.
    users.jsonl / tournaments.jsonl may be split into users/<n>.jsonl shards (jsonl_table) on either
    side; they are compared as whole tables. Ours stays split if it was, and becomes split as soon as
    the operator's is.
  - every other path the operator changed (event files, derived files, upcoming.json, manual/, scripts/,
    docs/ ...): take their file as a whole (or delete it if they deleted it).
Derived files of events the Actions wrote are recomputed by derive.py afterwards (the workflow runs it).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
from scripts.common import jsonl_table  # noqa: E402

# file name → key of a record (None = the whole line is the key, i.e. a set of lines)
KEYED = {
    "users.jsonl": "user_id",
    "tournaments.jsonl": "tournament_id",
    "tournament_images.jsonl": "tournament_id",
    "done.csv": None,
}
SORTED_BY_KEY: set[str] = set()   # the operator's order is kept (tournament_images.save sorts on its next write anyway)


def git(*args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE, input=input).stdout


def blob(commit: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "cat-file", "blob", f"{commit}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return r.stdout if r.returncode == 0 else None


def tree_files(commit: str, directory: str) -> list[str]:
    r = subprocess.run(["git", "ls-tree", "--name-only", "-z", f"{commit}:{directory}"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        return []
    return [n.decode("utf-8", "surrogateescape") for n in r.stdout.split(b"\0") if n]


def table_at(commit: str, path: str) -> tuple[str, bool]:
    """(text of the whole table at commit, whether it is split there). path = .../users.jsonl."""
    single = blob(commit, path)
    d = str(jsonl_table.shard_dir(path))
    shards = sorted((n for n in tree_files(commit, d) if n.endswith(".jsonl") and n[:-6].isdigit()),
                    key=lambda n: int(n[:-6]))
    if single is not None or not shards:
        return (single or b"").decode("utf-8"), False
    return "".join((blob(commit, f"{d}/{n}") or b"").decode("utf-8") for n in shards), True


def table_of(path: str, data_dir: str) -> str | None:
    """Index file name (KEYED) a changed path belongs to: the file itself, or one of its shards."""
    if not path.startswith(data_dir + "/"):
        return None
    rel = path[len(data_dir) + 1:]
    if rel in KEYED:
        return rel
    head, _, tail = rel.partition("/")
    name = f"{head}.jsonl"
    if name in KEYED and jsonl_table.spec_for(name) and "/" not in tail and tail.endswith(".jsonl") and tail[:-6].isdigit():
        return name
    return None


def write_table(path: str, text: str, split: bool) -> None:
    if not split:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return
    spec = jsonl_table.spec_for(path)
    by_shard: dict[str, list[str]] = {}
    for line in text.splitlines(keepends=True):
        if line.strip():
            by_shard.setdefault(jsonl_table.shard_of_record(path, spec, json.loads(line)), []).append(line)
    jsonl_table.write_shards(path, by_shard)
    if Path(path).exists():   # the single file became shards (git add -A stages both)
        os.remove(path)


def _key(line: str, field: str | None):
    if field is None:
        return line
    try:
        return json.loads(line)[field]
    except (ValueError, KeyError, TypeError):
        return ("raw", line)   # a broken line is kept as its own record (validate_data reports it)


def group(text: str, field: str | None) -> dict:
    """key → list of lines (in file order; a key can appear more than once in an append-only file)."""
    out: dict = {}
    for line in text.splitlines():
        if line.strip():
            out.setdefault(_key(line, field), []).append(line)
    return out


def merge_keyed(ours: str, theirs_old: str, theirs_new: str, field: str | None, sort_by_key: bool) -> tuple[str, dict]:
    a, o, n = group(ours, field), group(theirs_old, field), group(theirs_new, field)
    stats = {"taken": 0, "removed": 0, "added": 0}
    for k in o.keys() - n.keys():          # the operator removed it
        if k in a:
            del a[k]; stats["removed"] += 1
    for k, lines in n.items():             # the operator added or changed it
        if o.get(k) != lines and a.get(k) != lines:
            stats["added" if k not in a else "taken"] += 1
            a[k] = lines
    # the operator's order first (so a branch the Actions did not touch comes out byte-identical to theirs),
    # then the keys only the Actions have, in their order
    keys = [k for k in n if k in a] + [k for k in a if k not in n]
    if sort_by_key:
        keys.sort(key=lambda k: (isinstance(k, tuple), k if not isinstance(k, tuple) else 0))
    return "".join(line + "\n" for k in keys for line in a[k]), stats


def changed_paths(old: str, new: str) -> list[tuple[str, str]]:
    out = git("diff", "--name-status", "-z", "--no-renames", old, new).split(b"\0")
    pairs = []
    for i in range(0, len(out) - 1, 2):
        pairs.append((out[i].decode(), out[i + 1].decode("utf-8", "surrogateescape")))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True)
    ap.add_argument("--old", required=True, help="operator commit synced last time")
    ap.add_argument("--new", required=True, help="operator commit to sync now")
    args = ap.parse_args()
    data_dir = f"data/startgg/{args.region}"
    pairs = changed_paths(args.old, args.new)
    take, delete, keyed = [], [], []
    for status, path in pairs:
        name = table_of(path, data_dir)
        if name is not None:
            if name not in keyed:
                keyed.append(name)
        elif status.startswith("D"):
            delete.append(path)
        else:
            take.append(path)
    print(f"[sync] {args.old[:10]} → {args.new[:10]}: {len(take)} files taken, {len(delete)} deleted, {len(keyed)} index files merged")

    # whole files: theirs (in batches through a pathspec file, so 100k paths are fine)
    if take:
        git("checkout", args.new, "--pathspec-from-file=-", "--pathspec-file-nul",
            input=b"\0".join(p.encode("utf-8", "surrogateescape") for p in take))
    if delete:   # staged too (git rm), like the checkout above, so paths outside data/ need no git add
        git("rm", "-q", "-f", "--ignore-unmatch", "--pathspec-from-file=-", "--pathspec-file-nul",
            input=b"\0".join(p.encode("utf-8", "surrogateescape") for p in delete))

    for name in keyed:
        path = f"{data_dir}/{name}"
        ours_split = jsonl_table.is_sharded(path)
        ours = jsonl_table.read_text(path)
        old, _ = table_at(args.old, path)
        new, theirs_split = table_at(args.new, path)
        merged, st = merge_keyed(ours, old, new, KEYED[name], name in SORTED_BY_KEY)
        split = ours_split or (theirs_split and jsonl_table.spec_for(name) is not None)
        if merged != ours or split != ours_split:
            write_table(path, merged, split)
        print(f"[sync] {name}: taken {st['taken']}, added {st['added']}, removed {st['removed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
