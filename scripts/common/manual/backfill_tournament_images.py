"""Backfill tournament_images.jsonl (tournament icon URLs) for tournaments already in tournaments.jsonl.

download.py records the icon for every tournament in its listing window; this fills in the past.
Only the index is written (URL / width / height); image files are not downloaded here.

Usage (from the repository root):
  STARTGG_TOKEN=... python3 -m scripts.common.manual.backfill_tournament_images --region Japan
  [--all]        re-check tournaments that already have a record (default: only missing ones)
  [--limit N]    stop after N tournaments (for a trial run)

Progress is saved every --save-every tournaments, so an interrupted run resumes where it stopped.
Tournaments start.gg no longer returns (deleted) are left without a record and reported at the end.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.common import _cli, tournament_images, utils  # noqa: E402

BATCH = 50          # aliases per request (each alias is a tournament with one image list)
REQUEST_DELAY = 0.8  # seconds between requests (start.gg allows 80 requests / minute)


def build_query(tournament_ids):
    parts = [f'  t{tid}: tournament(id: {int(tid)}) {{ id images(type: "profile") {{ url width height }} }}'
             for tid in tournament_ids]
    return "query BackfillTournamentImages {\n" + "\n".join(parts) + "\n}"


def fetch_batch(tournament_ids):
    """{tid: images list} for the tournaments start.gg returned; missing (deleted) ones are absent."""
    resp = utils.fetch_data_with_retries(build_query(tournament_ids), {})
    data = (resp or {}).get("data")
    if data is None:
        raise utils.FetchError(f"no data in response: {str(resp)[:300]}")
    out = {}
    for tid in tournament_ids:
        node = data.get(f"t{tid}")
        if node is not None:
            out[tid] = node.get("images")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _cli.add_region_arg(parser)
    parser.add_argument("--tournament-file-path", default=None, help="default: data/startgg/<region>/tournaments.jsonl")
    parser.add_argument("--all", action="store_true", help="re-check tournaments that already have a record")
    parser.add_argument("--limit", type=int, default=None, help="stop after N tournaments")
    parser.add_argument("--save-every", type=int, default=500)
    _cli.add_api_args(parser, max_retries=5, retry_delay=10)
    args = parser.parse_args()
    _cli.resolve_index_paths(parser, args, tournament_file_path="tournaments.jsonl")
    _cli.setup_api(args)

    tournaments = utils.read_tournaments_jsonl(args.tournament_file_path)
    path = tournament_images.images_path_for(args.tournament_file_path)
    index = tournament_images.load(path)
    targets = sorted(int(t) for t in tournaments if args.all or int(t) not in index)
    if args.limit is not None:
        targets = targets[:args.limit]
    print(f"tournaments: {len(tournaments)}  already recorded: {len(index)}  to check: {len(targets)}", flush=True)

    missing, changed, since_save = [], 0, 0
    for i in range(0, len(targets), BATCH):
        batch = targets[i:i + BATCH]
        got = fetch_batch(batch)
        for tid in batch:
            if tid not in got:
                missing.append(tid)
            elif tournament_images.update(index, tid, got[tid]):
                changed += 1
        since_save += len(batch)
        if since_save >= args.save_every:
            tournament_images.save(index, path)
            since_save = 0
        print(f"  {min(i + BATCH, len(targets))}/{len(targets)}", flush=True)
        time.sleep(REQUEST_DELAY)
    tournament_images.save(index, path)

    with_icon = sum(1 for r in index.values() if r.get("url"))
    print(f"done: {changed} records written, {with_icon}/{len(index)} tournaments have an icon")
    if missing:
        print(f"not returned by start.gg ({len(missing)}, no record): {missing[:20]}{' ...' if len(missing) > 20 else ''}")


if __name__ == "__main__":
    main()
