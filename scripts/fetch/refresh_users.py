import argparse
import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import get_user_player_query, get_user_query
from scripts.utils import (
    read_users_jsonl,
    write_jsonl,
    extend_jsonl,
    set_indent_num,
    set_retry_parameters,
    set_api_parameters,
    fetch_data_with_retries,
    FetchError,
)


class UserNotFoundError(Exception):
    """Raised when the start.gg API returns null for a user."""


def fetch_user_and_player_details(user_id, player_id, sleep_duration):
    if sleep_duration > 0:
        time.sleep(sleep_duration)
    if player_id is not None:
        query = get_user_player_query()
        variables = {"userId": user_id, "playerId": player_id}
    else:
        query = get_user_query()
        variables = {"userId": user_id}
    response = fetch_data_with_retries(
        query,
        variables,
    )
    if (
        "data" not in response
        or response["data"] is None
        or "user" not in response["data"]
        or response["data"]["user"] is None
    ):
        raise UserNotFoundError(
            f"User {user_id} not found on start.gg (API returned null)."
        )
    user = response["data"]["user"]
    player = response["data"].get("player") if player_id is not None else None
    return user, player


def refresh_user_record(existing_record, sleep_duration):
    user_id = existing_record["user_id"]
    player_id = existing_record.get("player_id")

    user_detail, player_detail = fetch_user_and_player_details(
        user_id, player_id, sleep_duration
    )
    gamer_tag = existing_record.get("gamer_tag")
    prefix = existing_record.get("prefix")
    if player_detail is not None:
        gamer_tag = player_detail.get("gamerTag", gamer_tag)
        prefix = player_detail.get("prefix", prefix)

    gender_pronoun = user_detail.get("genderPronoun", "unknown")
    startgg_discriminator = user_detail.get("discriminator")

    x_id = None
    x_name = None
    discord_id = None
    discord_name = None
    authorizations = user_detail.get("authorizations") or []
    for auth in authorizations:
        auth_type = auth.get("type")
        if auth_type == "TWITTER":
            x_id = auth.get("externalId")
            x_name = auth.get("externalUsername")
        elif auth_type == "DISCORD":
            discord_id = auth.get("externalId")
            discord_name = auth.get("externalUsername")

    refreshed = {
        "user_id": user_id,
        "player_id": player_id,
        "gamer_tag": gamer_tag,
        "prefix": prefix,
        "gender_pronoun": gender_pronoun if gender_pronoun is not None else "unknown",
        "startgg_discriminator": startgg_discriminator,
        "x_id": x_id,
        "x_name": x_name,
        "discord_id": discord_id,
        "discord_name": discord_name,
    }
    return refreshed


def main():
    parser = argparse.ArgumentParser(
        description="Refresh start.gg user information and overwrite users.jsonl"
    )
    parser.add_argument(
        "--url",
        default="https://api.start.gg/gql/alpha",
        help="API URL",
    )
    parser.add_argument("--token", required=True, help="start.gg API token")
    parser.add_argument(
        "--users_file_path",
        default="data/startgg/users.jsonl",
        help="Existing users.jsonl file to read",
    )
    parser.add_argument(
        "--output_file_path",
        default=None,
        help="Destination file path (defaults to users_file_path)",
    )
    parser.add_argument(
        "--max_retries",
        type=int,
        default=10,
        help="Maximum number of retries for API requests",
    )
    parser.add_argument(
        "--retry_delay",
        type=int,
        default=5,
        help="Delay between retries in seconds",
    )
    parser.add_argument(
        "--indent_num",
        type=int,
        default=2,
        help="Indentation level for JSON output",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.25,
        help="Optional sleep duration between API calls to avoid rate limits",
    )
    parser.add_argument(
        "--user_retries",
        type=int,
        default=5,
        help="Maximum refresh attempts per user before falling back to existing data",
    )
    parser.add_argument(
        "--pause_every",
        type=int,
        default=200,
        help="Pause after processing this many users (0 disables pauses)",
    )
    parser.add_argument(
        "--pause_seconds",
        type=float,
        default=20.0,
        help="Duration of the periodic pause in seconds",
    )
    parser.add_argument(
        "--progress_interval",
        type=int,
        default=50,
        help="Print progress every N users (0 disables periodic progress output)",
    )
    parser.add_argument(
        "--checkpoint_path",
        default=None,
        help="Path to store intermediate refreshed users for resuming later",
    )
    parser.add_argument(
        "--force_refresh",
        action="store_true",
        help="Ignore checkpoint data and refresh all users",
    )
    parser.add_argument(
        "--max_users",
        type=int,
        default=0,
        help="Maximum users to refresh in a single run (0 means all users).",
    )
    parser.add_argument(
        "--cursor_path",
        default=None,
        help="Path to store and read the refresh cursor index.",
    )
    args = parser.parse_args()

    output_path = args.output_file_path or args.users_file_path

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    users = read_users_jsonl(args.users_file_path)
    if not users:
        print(
            f"No users found in {args.users_file_path}. Nothing to refresh.",
            file=sys.stderr,
        )
        return

    user_order = list(users.keys())
    total_users = len(user_order)

    start_index = 0
    if args.cursor_path and os.path.exists(args.cursor_path):
        try:
            with open(args.cursor_path, "r", encoding="utf-8") as cursor_file:
                start_index = int(cursor_file.read().strip() or "0")
        except ValueError:
            print(
                f"Warning: invalid cursor value in {args.cursor_path}. Starting from 0.",
                file=sys.stderr,
            )
            start_index = 0
    if total_users:
        start_index %= total_users

    target_count = total_users
    if args.max_users and args.max_users > 0:
        target_count = min(args.max_users, total_users)

    target_user_ids = []
    if total_users:
        for offset in range(target_count):
            idx = (start_index + offset) % total_users
            target_user_ids.append(user_order[idx])

    next_index = 0
    if total_users:
        next_index = (start_index + target_count) % total_users

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    checkpoint_records = {}
    if args.checkpoint_path:
        checkpoint_dir = os.path.dirname(args.checkpoint_path)
        if checkpoint_dir:
            os.makedirs(checkpoint_dir, exist_ok=True)
        if os.path.exists(args.checkpoint_path):
            checkpoint_records = read_users_jsonl(args.checkpoint_path)
            if checkpoint_records:
                print(
                    f"Loaded {len(checkpoint_records)} users from checkpoint {args.checkpoint_path}."
                )
        if args.force_refresh and checkpoint_records:
            print(
                "Force refresh enabled. Ignoring existing checkpoint data.",
                file=sys.stderr,
            )
            checkpoint_records = {}
            try:
                os.remove(args.checkpoint_path)
            except FileNotFoundError:
                pass

    # Apply checkpoint data to current users so final output contains latest info
    for user_id, record in checkpoint_records.items():
        if user_id in users:
            users[user_id] = record

    skip_existing = args.checkpoint_path is not None and not args.force_refresh
    processed_ids = set(checkpoint_records.keys()) if skip_existing else set()
    initial_processed = sum(1 for user_id in target_user_ids if user_id in processed_ids)
    if initial_processed and skip_existing:
        pct = (initial_processed / target_count) * 100 if target_count else 0
        print(
            f"Resuming from checkpoint: {initial_processed}/{target_count} target users already processed ({pct:.1f}%)."
        )

    if total_users:
        print(
            f"Refresh target: {target_count}/{total_users} users (cursor start={start_index}, next={next_index})."
        )

    failures = set()
    missing_users = set()
    skipped_count = 0
    newly_processed = 0
    consecutive_rate_limits = 0

    for index, user_id in enumerate(target_user_ids, start=1):
        if skip_existing and user_id in processed_ids:
            skipped_count += 1
            continue

        record = users[user_id]
        user_attempt = 0
        refreshed_record = record
        success = False

        while user_attempt < args.user_retries:
            user_attempt += 1
            try:
                refreshed_record = refresh_user_record(record, args.sleep)
                consecutive_rate_limits = 0
                success = True
                break
            except UserNotFoundError as e:
                print(f"Info: {e} Keeping existing data.", file=sys.stderr)
                consecutive_rate_limits = 0
                missing_users.add(user_id)
                success = True
                refreshed_record = record
                break
            except FetchError as e:
                if "Too Many Requests" in str(e):
                    consecutive_rate_limits += 1
                    backoff = max(
                        args.retry_delay * consecutive_rate_limits,
                        args.sleep * 5,
                        10,
                    )
                    print(
                        f"Rate limit hit while refreshing user {user_id} (attempt {user_attempt}/{args.user_retries}). Sleeping {backoff:.1f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(backoff)
                    continue
                print(f"Warning: {e}", file=sys.stderr)
                consecutive_rate_limits = 0
                break
            except Exception as e:
                print(
                    f"Failed to refresh user {user_id}: {e}",
                    file=sys.stderr,
                )
                consecutive_rate_limits = 0
                break

        if not success:
            failures.add(user_id)
            continue

        users[user_id] = refreshed_record
        processed_ids.add(user_id)
        newly_processed += 1

        if args.checkpoint_path and refreshed_record is not record:
            extend_jsonl([refreshed_record.copy()], args.checkpoint_path, with_version=True)

        done = index

        if (
            args.progress_interval
            and args.progress_interval > 0
            and done % args.progress_interval == 0
        ):
            pct = (done / target_count) * 100 if target_count else 0
            print(f"[Progress] {done}/{target_count} users processed ({pct:.1f}%).")

        if (
            args.pause_every
            and args.pause_every > 0
            and done % args.pause_every == 0
        ):
            print(
                f"Processed {done} users. Pausing for {args.pause_seconds:.1f}s to avoid rate limits...",
                file=sys.stderr,
            )
            time.sleep(args.pause_seconds)

    final_records = [users[user_id] for user_id in user_order]
    write_jsonl(final_records, output_path, with_version=True)

    failure_total = len(failures)

    if args.cursor_path:
        cursor_dir = os.path.dirname(args.cursor_path)
        if cursor_dir:
            os.makedirs(cursor_dir, exist_ok=True)
        with open(args.cursor_path, "w", encoding="utf-8") as cursor_file:
            cursor_file.write(str(next_index))

    print(
        f"Refreshed {newly_processed} users in this run (target {target_count}/{total_users}). Output written to {output_path}."
    )
    if skipped_count:
        print(
            f"Skipped {skipped_count} users already present in the checkpoint."
        )
    if args.checkpoint_path:
        print(
            f"Checkpoint stored at {args.checkpoint_path}."
        )
    if target_count:
        print(
            f"Summary: refreshed={newly_processed}, failed={failure_total}, skipped={skipped_count}, target={target_count}."
        )
    if args.cursor_path:
        print(f"Next cursor index written to {args.cursor_path}: {next_index}")
    if missing_users:
        print(
            f"{len(missing_users)} users returned no data from start.gg and were left unchanged.",
            file=sys.stderr,
        )
    if failures:
        print(
            f"Failed to refresh {failure_total} users. See stderr for details.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
