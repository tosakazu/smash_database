"""_cli — fetch スクリプト共通のコマンドライン定型。

以前は --token / --url / --max_retries / --retry_delay の定義と set_api_parameters の呼び出しが 6 本に複製されていた
(既定値も script ごとに違う: download は 100 回 / 5 秒、他は 5 回 / 10 秒。ここでは script ごとの現行値を引数で渡す)。
トークンは --token 省略時に環境変数 STARTGG_TOKEN から読む (argv に載せない)。
"""
from __future__ import annotations

import argparse
import os

from scripts.common import utils

API_URL = "https://api.start.gg/gql/alpha"


def add_region_arg(parser: argparse.ArgumentParser, default=None) -> None:
    """--region: index ファイル (done.csv / users.jsonl / tournaments.jsonl) の既定を data/startgg/<region>/ にする。"""
    parser.add_argument("--region", default=default,
                        help="data region, e.g. Japan. Index files default to data/startgg/<region>/... "
                             "(required unless every index path is given explicitly)")


def resolve_index_paths(parser: argparse.ArgumentParser, args: argparse.Namespace, **files: str) -> None:
    """files = {dest: filename}. None のままの dest を --region から埋める。--region も無ければ parser.error (推測しない)。"""
    for dest, fname in files.items():
        if getattr(args, dest) is None:
            region = getattr(args, "region", None)
            if not region:
                parser.error(f"--region is required when --{dest} is omitted")
            setattr(args, dest, os.path.join("data", "startgg", region.replace(" ", "_"), fname))


def add_api_args(parser: argparse.ArgumentParser, *, max_retries: int, retry_delay: int, dash: bool = False) -> None:
    """--token --url --max_retries --retry_delay を足す。dash=True なら --max-retries / --retry-delay (fetch_upcoming の流儀)。"""
    parser.add_argument("--token", default=os.environ.get("STARTGG_TOKEN"),
                        help="start.gg API token (省略時は環境変数 STARTGG_TOKEN)")
    parser.add_argument("--url", default=API_URL, help="API URL")
    mr, rd = ("--max-retries", "--retry-delay") if dash else ("--max_retries", "--retry_delay")
    parser.add_argument(mr, dest="max_retries", type=int, default=max_retries, help="API リクエストの最大再試行回数")
    parser.add_argument(rd, dest="retry_delay", type=int, default=retry_delay, help="再試行の間隔 (秒)")


def setup_api(args: argparse.Namespace) -> None:
    """引数から API 層を設定する。token が無ければ止める (黙って空で続けない)。"""
    if not args.token:
        raise SystemExit("ERROR: --token か環境変数 STARTGG_TOKEN が必要 (値は argv に載せず env 推奨)")
    utils.set_api_parameters(args.url, args.token)
    utils.set_retry_parameters(args.max_retries, args.retry_delay)
