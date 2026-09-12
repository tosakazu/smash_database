"""_cli — command-line boilerplate shared by the fetch scripts.

Previously the --token / --url / --max-retries / --retry-delay definitions and the set_api_parameters call were duplicated across 6 scripts
(with differing defaults: download uses 100 retries / 5 s, the others 5 / 10 s; each script passes its current values as arguments here).
The token is read from the env var STARTGG_TOKEN when --token is omitted (kept out of argv).
"""
from __future__ import annotations

import argparse
import os

from scripts.common import utils

API_URL = "https://api.start.gg/gql/alpha"


def add_region_arg(parser: argparse.ArgumentParser, default=None) -> None:
    """--region: default the index files (done.csv / users.jsonl / tournaments.jsonl) to data/startgg/<region>/."""
    parser.add_argument("--region", default=default,
                        help="data region, e.g. Japan. Index files default to data/startgg/<region>/... "
                             "(required unless every index path is given explicitly)")


def resolve_index_paths(parser: argparse.ArgumentParser, args: argparse.Namespace, **files: str) -> None:
    """files = {dest: filename}. Fill any dest still None from --region. Without --region, parser.error (never guess)."""
    for dest, fname in files.items():
        if getattr(args, dest) is None:
            region = getattr(args, "region", None)
            if not region:
                parser.error(f"--region is required when --{dest.replace('_', '-')} is omitted")
            setattr(args, dest, os.path.join("data", "startgg", region.replace(" ", "_"), fname))


def add_api_args(parser: argparse.ArgumentParser, *, max_retries: int, retry_delay: int) -> None:
    """Add --token --url --max-retries --retry-delay (option names consistently hyphenated)."""
    parser.add_argument("--token", default=os.environ.get("STARTGG_TOKEN"),
                        help="start.gg API token (default: env var STARTGG_TOKEN)")
    parser.add_argument("--url", default=API_URL, help="API URL")
    mr, rd = "--max-retries", "--retry-delay"
    parser.add_argument(mr, dest="max_retries", type=int, default=max_retries, help="max retries per API request")
    parser.add_argument(rd, dest="retry_delay", type=int, default=retry_delay, help="delay between retries (seconds)")


def setup_api(args: argparse.Namespace) -> None:
    """Configure the API layer from args. Stop if there is no token (never continue silently with an empty one)."""
    if not args.token:
        raise SystemExit("ERROR: --token or env var STARTGG_TOKEN is required (prefer env; keep the value out of argv)")
    utils.set_api_parameters(args.url, args.token)
    utils.set_retry_parameters(args.max_retries, args.retry_delay)
