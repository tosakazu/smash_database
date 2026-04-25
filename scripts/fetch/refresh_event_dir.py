import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.fetch.download import (  # noqa: E402
    download_all_set,
    download_seeds,
    download_standings,
    extend_user_info,
)
from scripts.utils import (  # noqa: E402
    read_json,
    read_users_jsonl,
    set_api_parameters,
    set_indent_num,
    set_retry_parameters,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Refresh one existing event directory in place using the current start.gg fetch logic."
    )
    parser.add_argument("--token", required=True, help="start.gg API token")
    parser.add_argument("--event-dir", required=True, help="Existing event directory path")
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="API URL")
    parser.add_argument("--users-file-path", default="data/startgg/users.jsonl", help="Path to users.jsonl")
    parser.add_argument("--max-retries", type=int, default=20, help="Maximum number of retries for API requests")
    parser.add_argument("--retry-delay", type=int, default=5, help="Delay between retries in seconds")
    parser.add_argument("--indent-num", type=int, default=2, help="Indentation level for JSON output")
    return parser.parse_args()


def main():
    args = parse_args()
    event_dir = Path(args.event_dir)
    attr_path = event_dir / "attr.json"
    if not attr_path.exists():
        raise SystemExit(f"attr.json not found: {attr_path}")

    attr = read_json(str(attr_path))
    event_id = attr.get("event_id")
    if event_id is None:
        raise SystemExit(f"event_id missing in {attr_path}")

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    users = read_users_jsonl(args.users_file_path)

    print(f"Refreshing event_id={event_id}")
    print(f"event_dir={event_dir}")

    user_data, player_data, entrant2user = download_standings(event_id, str(event_dir))
    if not entrant2user:
        raise SystemExit(f"entrant2user mapping is empty for event_id={event_id}")

    download_seeds(event_id, user_data, player_data, entrant2user, str(event_dir))
    extend_user_info(user_data, player_data, users, args.users_file_path)
    download_all_set(event_id, entrant2user, str(event_dir))

    print("Refresh complete.")


if __name__ == "__main__":
    main()
