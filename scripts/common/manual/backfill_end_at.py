"""Backfill end_timestamp into existing attr.json files.

For each tournament listed in tournaments.jsonl, query startgg for the tournament's
endAt and write end_timestamp into each event's attr.json.

Usage:
  python3 scripts/fetch/backfill_end_at.py --token <STARTGG_TOKEN>

Existing attr.json files that already contain a non-null end_timestamp are skipped.
Batch aliases are used to reduce API calls (one HTTP request per BATCH tournaments).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent  # smash_db_tournament/ (scripts/common/manual から 3 つ上)
sys.path.insert(0, str(ROOT))

from scripts.common import utils  # noqa: E402

API_URL = "https://api.start.gg/gql/alpha"
BATCH = 8  # aliases per request — keep small to avoid complexity errors
PAGE_DELAY = 0.4
MAX_WORKERS = 4


def build_batched_query(tournament_ids):
    """Build a single GraphQL query that fetches endAt for multiple tournaments via aliases."""
    parts = []
    for i, tid in enumerate(tournament_ids):
        parts.append(f"  t{i}: tournament(id: {int(tid)}) {{ id endAt startAt }}")
    return "query BatchEndAt {\n" + "\n".join(parts) + "\n}"


def fetch_batch(tournament_ids):
    """Returns dict {tid: endAt}. None for unresolved."""
    query = build_batched_query(tournament_ids)
    try:
        resp = utils.fetch_data_with_retries(query, {})
    except utils.FetchError as e:
        print(f"[fetch_batch] FetchError: {e}", flush=True)
        return {tid: None for tid in tournament_ids}
    data = (resp or {}).get("data") or {}
    out = {}
    for i, tid in enumerate(tournament_ids):
        node = data.get(f"t{i}")
        if node is None:
            out[tid] = None
        else:
            out[tid] = node.get("endAt")
    return out


def collect_targets(events_root, tournaments_jsonl):
    """Returns dict: tournament_id -> list[attr_json_path].

    Only includes tournaments whose attr.json is missing end_timestamp (or has it null).
    """
    targets = {}
    skipped_already = 0
    skipped_missing = 0
    with open(tournaments_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = rec.get("tournament_id")
            if tid is None:
                continue
            for ev in rec.get("events", []):
                p = ev.get("path")
                if not p:
                    continue
                attr_path = Path(p) / "attr.json"
                if not attr_path.is_file():
                    skipped_missing += 1
                    continue
                try:
                    with attr_path.open("r", encoding="utf-8") as af:
                        attr = json.load(af)
                except (json.JSONDecodeError, OSError):
                    skipped_missing += 1
                    continue
                if attr.get("end_timestamp") is not None:
                    skipped_already += 1
                    continue
                targets.setdefault(tid, []).append(attr_path)
    print(f"  targets: {len(targets)} tournaments | already-filled events: {skipped_already} | missing attr: {skipped_missing}", flush=True)
    return targets


def write_end_timestamp(attr_paths, end_ts):
    """Update each attr.json to include end_timestamp.

    If end_ts is None, set end_timestamp to null so we don't re-query next run
    (NB: we still set the field to mark as attempted).
    """
    for p in attr_paths:
        try:
            with p.open("r", encoding="utf-8") as f:
                attr = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        attr["end_timestamp"] = end_ts
        try:
            with p.open("w", encoding="utf-8") as f:
                json.dump(attr, f, ensure_ascii=False, indent=2)
        except OSError:
            print(f"  [write] failed: {p}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--events_root", default="data/startgg/events")
    ap.add_argument("--tournaments_jsonl", default=None)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--max", type=int, default=0, help="if > 0, only process this many tournaments")
    from scripts.common._cli import add_region_arg, resolve_index_paths
    add_region_arg(ap)
    args = ap.parse_args()
    resolve_index_paths(ap, args, tournaments_jsonl="tournaments.jsonl")

    utils.set_api_parameters(API_URL, args.token)
    utils.set_retry_parameters(max_retries=5, retry_delay=2.0)

    cwd = Path.cwd()
    events_root = (cwd / args.events_root).resolve()
    tournaments_jsonl = (cwd / args.tournaments_jsonl).resolve()
    print(f"events_root: {events_root}", flush=True)
    print(f"tournaments_jsonl: {tournaments_jsonl}", flush=True)
    print("Scanning attr.json files ...", flush=True)
    targets = collect_targets(events_root, tournaments_jsonl)
    if not targets:
        print("Nothing to backfill.")
        return

    ids = list(targets.keys())
    if args.max > 0:
        ids = ids[:args.max]
    n = len(ids)
    print(f"Backfilling {n} tournaments in batches of {args.batch} with {args.workers} workers ...", flush=True)

    batches = [ids[i:i + args.batch] for i in range(0, n, args.batch)]
    t0 = time.time()
    done = 0
    succ = 0
    fail = 0

    def process(batch):
        return batch, fetch_batch(batch)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(process, b) for b in batches]
        for fut in as_completed(futs):
            batch, mapping = fut.result()
            for tid in batch:
                end_ts = mapping.get(tid)
                attr_paths = targets[tid]
                if end_ts is None:
                    fail += 1
                else:
                    succ += 1
                write_end_timestamp(attr_paths, end_ts)
            done += len(batch)
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (n - done) / rate if rate > 0 else 0
            print(f"  [{done}/{n}] elapsed={elapsed:.0f}s rate={rate:.1f}/s eta={eta:.0f}s  succ={succ} fail={fail}", flush=True)

    print(f"\nDONE in {time.time()-t0:.0f}s  ({succ} succ, {fail} fail)")


if __name__ == "__main__":
    main()
