import os
import re
import json
import shutil
import argparse
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.common import clock
from scripts.common._cli import add_api_args, setup_api
from scripts.common.download_policy import (
    tournament_skip_reason, done_tournament_action, plan_event_moves, existing_event_action, champion_missing_reason,
)
from scripts.common.queries import (
    get_standings_query, get_seeds_query,
    get_tournament_events_query, get_phase_groups_query, get_tournaments_by_game_query,
)

# Lower-class brackets: "B class"/"C class" etc., in Japanese (letter + katakana "kurasu") or English (Bclass/B_Class/b_class).
# Fallback to rescue side events whose videogame tag is unset on start.gg.
_LOWER_CLASS_NAME_PAT = re.compile(
    r'[BCDEＢＣＤＥ]\s*クラス|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)
from scripts.common.utils import (
    country_code2region, get_date_parts, get_event_directory,
    read_users_jsonl, read_set, read_tournaments_jsonl,
    write_json, extend_jsonl, write_jsonl,
    set_indent_num,
    fetch_data_with_retries, fetch_all_nodes,
    FetchError, NoPhaseError,
)
# Reuse the v2 (refetch) fetch / write logic so new daily downloads produce the same schema.
from scripts.common.redownload_matches_v2 import (
    write_matches_v2 as _write_matches_v2_impl,
    fetch_event_phases as _fetch_event_phases_impl,
    fetch_phase_group_sets as _fetch_phase_group_sets_impl,
    API_DELAY_SEC as _V2_API_DELAY_SEC,
)

REQUIRED_EVENT_FILES = ("attr.json", "matches.json", "standings.json", "seeds.json")
TOURNAMENTS_PER_PAGE = 100
STANDINGS_PER_PAGE = 100
SEEDS_PER_PAGE = 100
SETS_PER_PAGE = 20

def parse_date_or_datetime(value):
    # Treat YYYY-MM-DD as end of day (=23:59:59) so the start_date comparison includes that day.
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        f"Invalid datetime '{value}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
    )

def main():
    # Command-line arguments
    parser = argparse.ArgumentParser(description="Download tournament data from start.gg")
    add_api_args(parser, max_retries=100, retry_delay=5)   # --token --url --max-retries --retry-delay
    parser.add_argument(
        "--start-date",
        type=parse_date_or_datetime,
        default=None,
        help="Upper bound datetime for retrieval (inclusive). Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
    )
    parser.add_argument(
        "--finish-date",
        type=parse_date_or_datetime,
        default=datetime(2018, 1, 1),
        help="Lower bound datetime for retrieval (inclusive). Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
    )
    parser.add_argument("--indent-num", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--startgg-dir", default="data/startgg", help="Data root. Events are saved under <root>/<region>/events/<year>/<month>/<day>/...")
    parser.add_argument("--done-file-path", default=None, help="Record of completed tournaments (default: data/startgg/<region>/done.csv; region derived from --country-code)")
    parser.add_argument("--users-file-path", default=None, help="User info (default: data/startgg/<region>/users.jsonl)")
    parser.add_argument("--tournament-file-path", default=None, help="Tournament info (default: data/startgg/<region>/tournaments.jsonl)")
    parser.add_argument("--game-id", default="1386", help="Game ID for tournament retrieval. see https://developer.start.gg/docs/examples/queries/videogame-id-by-name/")
    parser.add_argument("--country-code", default="", help="Country code for tournament retrieval. e.g. JP")
    parser.add_argument("--awaiting-file", default=None,
                        help="Awaiting-resume registry (build/data/awaiting_resume.json). Listed events are re-fetched every run "
                             "regardless of the 7-day window until a champion appears, and are never marked done. Omitted/missing: legacy behaviour.")
    args = parser.parse_args()
    setup_api(args)
    # Index files (done / users / tournaments) live per region under data/startgg/<region>/ (since 2026-09-07; data is managed on per-region branches)
    _region_dir = os.path.join("data", "startgg", country_code2region(args.country_code).replace(" ", "_"))
    if args.done_file_path is None:
        args.done_file_path = os.path.join(_region_dir, "done.csv")
    if args.users_file_path is None:
        args.users_file_path = os.path.join(_region_dir, "users.jsonl")
    if args.tournament_file_path is None:
        args.tournament_file_path = os.path.join(_region_dir, "tournaments.jsonl")
    os.makedirs(_region_dir, exist_ok=True)

    set_indent_num(args.indent_num)
    set_awaiting_resume_ids(load_awaiting_resume_file(args.awaiting_file))
    if args.start_date is not None and args.start_date < args.finish_date:
        raise ValueError("--start-date must be greater than or equal to --finish-date.")

    download_all_tournaments(
        args.game_id,
        args.country_code,
        args.start_date,
        args.finish_date,
        args.startgg_dir,
        args.done_file_path,
        args.users_file_path,
        args.tournament_file_path,
    )


# ── Post-completion refresh ──
# Organizers sometimes fix scores or placements after a tournament ends (found 2026-08-15 at Grand Slam #19).
# Once a tournament is marked done it is never re-fetched, so such fixes would never be picked up.
# So we re-fetch anything that ended within the last N days even if already downloaded. But the nightly
# runs every 3 hours and re-fetching each time would spike API calls, so enforce a minimum interval per event
# (last fetch time = mtime of attr.json; no dedicated state file).
RECENT_REFRESH_DAYS = float(os.environ.get("SPSP_REFRESH_DAYS", "3"))
RECENT_REFRESH_MIN_HOURS = float(os.environ.get("SPSP_REFRESH_MIN_HOURS", "12"))


# ── Awaiting resume ──
# Tournaments postponed/suspended and resumed later. Re-fetched every run regardless of the 7-day window until a champion appears, never marked done
# (= the build side aggregates partial results provisionally. Registry is build/data/awaiting_resume.json, passed via --awaiting-file).
_AWAITING_RESUME_IDS: set = set()


def set_awaiting_resume_ids(ids):
    global _AWAITING_RESUME_IDS
    _AWAITING_RESUME_IDS = set(int(x) for x in (ids or ()))


def load_awaiting_resume_file(path):
    """Registry JSON -> set of event_id. Empty if path is unset/missing. Raises if malformed (never silently ignored)."""
    if not path or not os.path.exists(path):
        return set()
    with open(path, "rb") as f:
        d = json.loads(f.read())
    events = d.get("events") if isinstance(d, dict) else d
    if not isinstance(events, list):
        raise ValueError(f"{path}: 'events' is not a list")
    out = set()
    for e in events:
        eid = e.get("event_id") if isinstance(e, dict) else e
        if not isinstance(eid, int):
            raise ValueError(f"{path}: event_id is not an integer: {e!r}")
        out.add(eid)
    return out


def is_awaiting_resume(event_id):
    return event_id is not None and int(event_id) in _AWAITING_RESUME_IDS


def event_files_complete(event_dir):
    return all(os.path.exists(os.path.join(event_dir, name)) for name in REQUIRED_EVENT_FILES)


def _event_needs_recent_refresh(event_dir, ref_end_ts, now_timestamp):
    """True if the event ended within N days and at least MIN_HOURS have passed since the last fetch."""
    if RECENT_REFRESH_DAYS <= 0 or not ref_end_ts or not event_dir:
        return False
    if (now_timestamp - ref_end_ts) > RECENT_REFRESH_DAYS * 86400:
        return False
    attr = os.path.join(event_dir, "attr.json")
    try:
        last = os.path.getmtime(attr)
    except OSError:
        return True          # not fetched yet / broken -> re-fetch
    return (now_timestamp - last) >= RECENT_REFRESH_MIN_HOURS * 3600


def _tournament_needs_recent_refresh(tournament_entry, ref_end_ts, now_timestamp):
    """Whether a done tournament still qualifies for the post-completion refresh (True if any one event does)."""
    if not tournament_entry:
        return False
    return any(_event_needs_recent_refresh(ev.get("path"), ref_end_ts, now_timestamp)
               for ev in tournament_entry.get("events", []))

def tournament_events_complete(tournament_entry):
    events = tournament_entry.get("events", [])
    if not events:
        return False
    for event in events:
        event_dir = event.get("path")
        if not event_dir or not event_files_complete(event_dir):
            return False
    return True

def _build_event_id_index(tournaments):
    """Build {event_id: (tournament_id, event_path)} from existing tournaments.jsonl data."""
    idx = {}
    for tid, entry in tournaments.items():
        for ev in entry.get("events", []):
            eid = ev.get("event_id")
            if eid:
                idx[eid] = (tid, ev.get("path"))
    return idx


def download_all_tournaments(game_id, country_code, start_date, finish_date, startgg_dir, done_file_path, users_file_path, tournament_file_path):
    done_tournaments = read_set(done_file_path, as_int=True)
    users = read_users_jsonl(users_file_path)
    tournaments = read_tournaments_jsonl(tournament_file_path)
    print(f"done_tournaments: {len(done_tournaments)}")
    print(f"users: {len(users)}")
    print(f"tournaments: {len(tournaments)}")
    rewrite_tournaments = False
    # Collect ids of existing users whose info was updated; users.jsonl is written back once at the end.
    dirty_user_ids = set()
    existing_tournament_ids = set(tournaments.keys())
    # Index of event_id -> (tournament_id, old_path) for detecting date-change duplicates.
    event_id_index = _build_event_id_index(tournaments)

    page = 1
    while True:
        try:
            tournaments_info, total_pages = fetch_latest_tournaments_by_game(game_id, country_code=country_code, limit=TOURNAMENTS_PER_PAGE, page=page)
        except FetchError as e:
            # Previously this printed and continued, but without advancing page it retried the same page forever
            # (fetch already tried max_retries times). Stop and let the nightly see the failure.
            raise FetchError(f"tournament list page {page} could not be fetched: {e}") from e
        print(f"Progress: {page}/{total_pages}")
        if not tournaments_info:
            break

        for tournament in tournaments_info:
            try:
                tournament_id = tournament["id"]
                tournament_name = tournament["name"]
                tournament_state = tournament.get("state")
                timestamp = tournament["startAt"]
                end_timestamp = tournament["endAt"]

                _country_code = tournament["countryCode"]
                city = tournament["city"]
                lat = tournament["lat"]
                lng = tournament["lng"]
                venue_name = tournament["venueName"]
                timezone = tournament["timezone"]
                postal_code = tournament["postalCode"]
                venue_address = tournament["venueAddress"]
                maps_place_id = tournament["mapsPlaceId"]
                url = tournament["url"]
                place = {
                    "country_code": _country_code,
                    "city": city,
                    "lat": lat,
                    "lng": lng,
                    "venue_name": venue_name,
                    "timezone": timezone,
                    "postal_code": postal_code,
                    "venue_address": venue_address,
                    "maps_place_id": maps_place_id
                }

                now_timestamp = clock.now_ts()   # "now" comes from scripts.common.clock (still re-evaluated per tournament)
                tournament_dt = datetime.fromtimestamp(timestamp)
                _skip = tournament_skip_reason(tournament_state, end_timestamp, timestamp, now_timestamp,
                                               int(start_date.timestamp()) if start_date is not None else None)
                if _skip == "not_finished":
                    print(f"({tournament_name} {tournament_dt}) is not finished yet.")
                    continue
                if _skip == "newer_than_start_date":
                    print(f"({tournament_name} {tournament_dt}) is newer than start_date. Skipping.")
                    continue

                if tournament_id in done_tournaments:
                    tournament_entry = tournaments.get(tournament_id)
                    # Even if done & files complete, do not skip when the tournament ended within 7 days
                    # and contains an event without a champion (= the actual champion_missing retry;
                    # without this the per-event retry below is never reached).
                    _champ_retry = _tournament_needs_champion_retry(
                        tournament_entry, end_timestamp or timestamp, now_timestamp)
                    # Re-fetch right after completion (within RECENT_REFRESH_DAYS) since fixes may come in
                    _recent_refresh = _tournament_needs_recent_refresh(
                        tournament_entry, end_timestamp or timestamp, now_timestamp)
                    if _recent_refresh:
                        print(f"({tournament_name} {datetime.fromtimestamp(timestamp)}) "
                              f"ended within {RECENT_REFRESH_DAYS:g} days — re-fetching")
                    _action, _why = done_tournament_action(
                        tournament_entry, bool(tournament_entry) and tournament_events_complete(tournament_entry),
                        _champ_retry, _recent_refresh)
                    if _action == "skip":
                        # Check if any event is stored under an outdated date
                        # directory. If so, move it to the correct path.
                        year, month, day = get_date_parts(timestamp)
                        needs_move = False
                        for ev, old_path, new_path in plan_event_moves(
                                tournament_entry,
                                lambda ev: get_event_directory(startgg_dir, country_code, year, month, day,
                                                               tournament_name, ev.get("event_name", ""))):
                            if os.path.isdir(old_path):
                                print(f"  [move] {tournament_name}: {old_path} -> {new_path}")
                                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                                shutil.move(old_path, new_path)
                                ev["path"] = new_path
                                # Update event_id index
                                eid = ev.get("event_id")
                                if eid:
                                    event_id_index[eid] = (tournament_id, new_path)
                                needs_move = True
                                # Clean up empty parent dirs
                                old_parent = os.path.dirname(old_path)
                                if os.path.isdir(old_parent) and not os.listdir(old_parent):
                                    os.rmdir(old_parent)
                        if needs_move:
                            rewrite_tournaments = True
                        else:
                            print(f"({tournament_name} {datetime.fromtimestamp(timestamp)}) already downloaded.")
                        continue
                    print(f"({tournament_name} {tournament_dt}) {_why}. Re-downloading.")

                print(f"Download {tournament_name}, date: {tournament_dt}")

                # The lower bound of the window is judged by endAt (= matches the listing's sortBy "endAt desc").
                # Judging by startAt, tournaments whose TO set startAt to the announcement date (Funasuma)
                # or long-running tournaments would fall outside the window by the time they finish and never be fetched.
                eff_end_dt = datetime.fromtimestamp(end_timestamp or timestamp)
                if eff_end_dt < finish_date:
                    if end_timestamp is None:
                        # Tournaments without endAt have no guaranteed position in the endAt desc sort,
                        # so do not stop here; just skip this one.
                        print(f"({tournament_name} {tournament_dt}) endAt unset & older than finish_date. Skipping.")
                        continue
                    print("!!!downloaded all!!!")
                    _flush_dirty_users(users, dirty_user_ids, users_file_path)
                    return

                if tournament_id in tournaments:
                    tournaments[tournament_id]["name"] = tournament_name
                    tournaments[tournament_id].setdefault("events", [])
                else:
                    tournaments[tournament_id] = {
                        "tournament_id": tournament_id,
                        "name": tournament_name,
                        "events": []
                    }
                events_info = fetch_event_ids_from_tournament(tournament_id, game_id)

                any_event_failed = False
                champion_missing = False
                for event_id, event_name, is_online in events_info:
                    try:
                        year, month, day = get_date_parts(timestamp)
                        event_dir = get_event_directory(startgg_dir, country_code, year, month, day, tournament_name, event_name)

                        # Date-change dedup: if this event_id was previously saved
                        # at a different path (due to tournament date change on
                        # start.gg), remove the old directory and update the index.
                        if event_id in event_id_index:
                            old_tid, old_path = event_id_index[event_id]
                            if old_path and old_path != event_dir and os.path.isdir(old_path):
                                print(f"  [dedup] event {event_id} date changed: removing old dir {old_path}")
                                shutil.rmtree(old_path)
                                # Update tournaments entry to drop the old path
                                if old_tid in tournaments:
                                    tournaments[old_tid]["events"] = [
                                        e for e in tournaments[old_tid]["events"]
                                        if e.get("event_id") != event_id
                                    ]
                                    rewrite_tournaments = True
                            elif old_path == event_dir and event_files_complete(old_path):
                                # Same path, files present. Whether to re-fetch is decided by download_policy.existing_event_action:
                                # inside the post-completion refresh window, re-fetch even if a champion exists (pick up organizer fixes).
                                # Without a champion, re-fetch only within 7 days of the end (or if registered as awaiting resume).
                                _ref_end2 = end_timestamp or timestamp
                                _needs_refresh = _event_needs_recent_refresh(old_path, _ref_end2, now_timestamp)
                                _has_champ = None if _needs_refresh else _standings_has_champion(old_path)
                                _act, _why = existing_event_action(True, _needs_refresh, _has_champ, _ref_end2,
                                                                   now_timestamp, is_awaiting_resume(event_id))
                                if _act == "skip":
                                    continue
                                if _why == "recent refresh":
                                    print(f"  [refresh] {event_name}: ended within {RECENT_REFRESH_DAYS:g} days — re-fetching")
                                elif _why == "awaiting resume":
                                    print(f"  [awaiting] {event_name}: registered as awaiting resume — re-fetching until a champion appears")

                        user_data, player_data, entrant2user = download_standings(event_id, event_dir)
                        num_entrants = len(user_data)
                        try:
                            download_seeds(event_id, user_data, player_data, entrant2user, event_dir)
                        except NoPhaseError as e:
                            print(f"No phase found for event {event_name}. Skipping.")
                            continue
                        extend_user_info(user_data, player_data, users, users_file_path, dirty_user_ids=dirty_user_ids)
                        download_all_set(event_id, entrant2user, event_dir)
                        labels = {}
                        write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir, end_timestamp=end_timestamp)

                        # Standings without a champion (placement=1) are most likely an "unfinalized results
                        # snapshot" (= the organizer enters the finals later. Found at Hyogo Taisenkai #31.
                        # Empty standings despite existing matches is the same case. Found at Badawi #5).
                        # For 7 days after the tournament ends, do not mark done and re-fetch nightly. After the
                        # window, mark done as-is (= no further downloads).
                        _cm = champion_missing_reason(_standings_has_champion(event_dir), is_awaiting_resume(event_id),
                                                      end_timestamp or timestamp, now_timestamp)
                        if _cm == "awaiting":
                            champion_missing = True
                            print(f"  [awaiting] {event_name}: awaiting resume — not marking done until a champion appears")
                        elif _cm == "pending":
                            champion_missing = True
                            print(f"  [pending] {event_name}: no champion in standings — kept as a re-fetch target for 7 days")

                        existing_events = tournaments[tournament_id]["events"]
                        if not any(e.get("event_id") == event_id for e in existing_events):
                            existing_events.append({
                                "event_id": event_id,
                                "event_name": event_name,
                                "path": event_dir
                            })
                            if tournament_id in existing_tournament_ids:
                                rewrite_tournaments = True
                        # Keep event_id index up to date for subsequent iterations.
                        event_id_index[event_id] = (tournament_id, event_dir)
                    except FetchError as e:
                        any_event_failed = True
                        print(f"FetchError on event {event_id} ({event_name}) in tournament {tournament_name}: {e}")
                        try:
                            with open("failed_events.log", "a", encoding="utf-8") as fl:
                                fl.write(f"{tournament_id}\t{event_id}\t{event_name}\n")
                        except Exception:
                            pass
                        continue

                # Save files
                if len(tournaments[tournament_id]["events"]) > 0:
                    # Append to tournaments.jsonl ONLY if this is a brand-new
                    # tournament not already in the file. Otherwise we rely on
                    # the final rewrite (rewrite_tournaments) to persist updates.
                    if tournament_id not in existing_tournament_ids:
                        extend_tournament_info(tournaments[tournament_id], tournament_file_path)
                        # Remember that we've persisted this new tid so that a
                        # second encounter within the same run doesn't re-append.
                        existing_tournament_ids.add(tournament_id)
                    # else: already in file; if we changed it, rewrite_tournaments
                    # is True and the full rewrite at end of run handles it.

                    # Only mark done if no event failed (so retry will pick it up).
                    # Use the in-memory done_tournaments set to avoid double-writing
                    # the same tid to done.csv when a tournament is re-processed.
                    if not any_event_failed and not champion_missing:
                        if tournament_id not in done_tournaments:
                            write_done_tournaments(tournament_id, done_file_path)
                            done_tournaments.add(tournament_id)

            except FetchError as e:
                print(f"FetchError on tournament {tournament.get('name','?')}: {e}")
                continue

        if page >= total_pages:
            break
        page += 1

    if rewrite_tournaments:
        write_jsonl(list(tournaments.values()), tournament_file_path, with_version=True)

    # Write users.jsonl back if any existing user was updated
    _flush_dirty_users(users, dirty_user_ids, users_file_path)


def _flush_dirty_users(users, dirty_user_ids, users_file_path):
    """Rewrite the whole users.jsonl if any existing user record was updated from the API.
    New users were already appended via extend_jsonl, so here we overwrite the full file with write_jsonl.
    """
    if not dirty_user_ids:
        return
    print(f"[users] {len(dirty_user_ids)} existing users updated, rewriting {users_file_path}", flush=True)
    write_jsonl(list(users.values()), users_file_path, with_version=True)

# Functions for saving event set data
def _standings_has_champion(event_dir):
    """Whether standings.json has a placement=1 entry. If unreadable, True (= treated as fine)."""
    try:
        with open(os.path.join(event_dir, "standings.json"), "rb") as f:
            rows = json.loads(f.read()).get("data") or []
        if not rows:
            # Standings completely empty: if match data exists, treat as an "unfinalized snapshot
            # taken on tournament day" (found at Badawi #5; standings finalize the next day or later)
            # and as having no champion -> put on the 7-day re-fetch list.
            # Zero matches (cancelled / registration only) stays fine as before and is not retried.
            try:
                with open(os.path.join(event_dir, "matches.json"), "rb") as mf:
                    return not (json.loads(mf.read()).get("data") or [])
            except Exception:
                return True
        places = [r.get("placement") for r in rows if isinstance(r.get("placement"), int)]
        return not (len(rows) >= 8 and places and min(places) != 1)
    except Exception:
        return True


def _tournament_needs_champion_retry(tournament_entry, ref_end_ts, now_timestamp):
    """Whether a done tournament should be re-fetched. True if it contains an event without a champion within 7 days of the end.

    The outer done-skip in download.py only checks that files are complete, so without this the
    per-event champion_missing retry (= re-fetch until placement=1 appears in standings)
    is never reached. Past 7 days it is treated as "finalized as incomplete" and not re-fetched.
    """
    if not tournament_entry:
        return False
    in_window = bool(ref_end_ts) and (now_timestamp - ref_end_ts) < 7 * 86400
    for event in tournament_entry.get("events", []):
        ed = event.get("path")
        if not (ed and event_files_complete(ed)) or _standings_has_champion(ed):
            continue
        # Re-fetch if within the 7-day window, or registered as awaiting resume (until a champion appears, regardless of the window)
        if in_window or is_awaiting_resume(event.get("event_id")):
            return True
    return False


def download_all_set(event_id, entrant2user, event_dir):
    """Generate the event's matches.json in the v2 schema (match_id / bracket_label / global_round, etc.).

    The entrant2user argument is accepted for API compatibility but unused; it is rebuilt from all_sets internally.
    """
    all_sets_with_phase = fetch_all_sets(event_id)
    if not all_sets_with_phase:
        return
    os.makedirs(event_dir, exist_ok=True)
    from pathlib import Path as _Path
    _write_matches_v2_impl(event_id, all_sets_with_phase, _Path(event_dir))

def fetch_all_sets(event_id):
    """Return all sets of the event as a list of (set_node, phase_info, pg_info) tuples.

    Changed from v1 (event-level query + fetch_all_nodes) to the v2 logic (per phase_group + totalPages
    + fallback retry + dedup). Fixes the missing-sets bug on large events such as Kagaribi #15.
    Returns a tuple list enriched with phase_info / pg_info so it can be passed to write_matches_v2.
    """
    import time as _time
    phases = _fetch_event_phases_impl(event_id)
    _time.sleep(_V2_API_DELAY_SEC)  # ease rate limiting after the phases query.
    all_sets_with_phase = []
    seen_ids = set()
    pg_failures = []
    for ph in phases:
        phase_info = {
            "id": ph.get("id"),
            "name": ph.get("name"),
            "numSeeds": ph.get("numSeeds"),
            "bracketType": ph.get("bracketType"),
            "phaseOrder": ph.get("phaseOrder"),
        }
        for pg in (ph.get("phaseGroups") or {}).get("nodes") or []:
            pg_id = pg.get("id")
            if pg_id is None:
                continue
            pg_info = {
                "id": pg_id,
                "displayIdentifier": pg.get("displayIdentifier"),
                "startAt": pg.get("startAt"),  # scheduled start of the phase_group (Unix timestamp)
                "wave": pg.get("wave"),  # { id, identifier, startAt }
            }
            try:
                # with_games=True: fetch scores + character/stage selections in one pass and
                # write character info into the details of matches.json (= merges what fetch_character_games
                # used to fetch separately for the same sets).
                pg_sets = _fetch_phase_group_sets_impl(pg_id, per_page=50, with_games=True)
            except FetchError as e:
                print(f"[fetch_all_sets] WARN pg={pg_id} failed: {e}", flush=True)
                pg_failures.append(pg_id)
                pg_sets = []
            for s in pg_sets:
                sid = s.get("id")
                if sid is None or sid in seen_ids:
                    continue
                seen_ids.add(sid)
                all_sets_with_phase.append((s, phase_info, pg_info))
            _time.sleep(_V2_API_DELAY_SEC)  # ease rate limiting between phase_groups (same as v2).
    if pg_failures:
        # Writing a silently partial matches.json and marking done means it is never re-fetched
        # (= found at Hyogo Taisenkai #31). Fail so the caller keeps it as a retry target.
        raise FetchError(
            f"[fetch_all_sets] event={event_id}: {len(pg_failures)} phase_groups failed: {pg_failures}")
    return all_sets_with_phase


# Threshold (days) for triggering the startAt correction. Does not fire for past-midnight or day-before DQ
# reports; only corrects tournaments whose startAt was set to the announcement/registration date (e.g. Funasuma
# held 7/19 = startAt 6/8, 41 days off). Multi-day tournaments have day-1 sets on the startAt day, so it does not fire.
DATE_CORRECTION_MIN_GAP_DAYS = 3


def corrected_event_window(timestamp, end_timestamp, set_times):
    """Derive the event's window from actual set times and correct a misconfigured startAt.

    set_times: list of started_at / completed_at (Unix ts) of valid sets (non-DQ / non-cancel).
    Returns: (timestamp, end_timestamp, corrected: bool).

    Correction conditions (each only for gaps over DATE_CORRECTION_MIN_GAP_DAYS days):
      - startAt is far older than the first actual set (= announcement/registration date was set)
      - startAt is far newer than the last actual set (= a future placeholder was set)
    When corrected, (min(set_times), max(set_times)) is used.
    """
    times = [t for t in (set_times or []) if t]
    if not times or not timestamp:
        return timestamp, end_timestamp, False
    s_min, s_max = min(times), max(times)
    gap = DATE_CORRECTION_MIN_GAP_DAYS * 86400
    if (s_min - timestamp) > gap or (timestamp - s_max) > gap:
        return s_min, s_max, True
    return timestamp, end_timestamp, False


def read_event_set_times(event_dir):
    """Read the actual times of valid sets (non-DQ / non-cancel) from matches.json."""
    try:
        with open(os.path.join(event_dir, "matches.json"), encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:
        return []
    rows = blob.get("data") if isinstance(blob, dict) else blob
    times = []
    for m in rows or []:
        if not isinstance(m, dict) or m.get("dq") or m.get("cancel"):
            continue
        for k in ("started_at", "completed_at"):
            if m.get(k):
                times.append(m[k])
    return times


def write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir, end_timestamp=None):
    # startAt correction: only when far off from actual set times, replace with the window derived
    # from set times. The original start.gg values are kept as startgg_start_at / startgg_end_at and
    # date_corrected_by records the fact of correction (= not a silent rewrite).
    set_times = read_event_set_times(event_dir)
    corr_ts, corr_end, corrected = corrected_event_window(timestamp, end_timestamp, set_times)
    json_data = {
        "event_id": event_id,
        "tournament_name": tournament_name,
        "event_name": event_name,
        "region": country_code2region(place["country_code"]),
        "place": place,
        "num_entrants": num_entrants,
        "offline": not is_online,
        "url": url,
        "labels": labels,
        "status": "completed",
        "timestamp": corr_ts,
        "end_timestamp": corr_end,
    }
    if corrected:
        json_data["startgg_start_at"] = timestamp
        json_data["startgg_end_at"] = end_timestamp
        json_data["date_corrected_by"] = "set_times"
        print(f"  [date-fix] {tournament_name} / {event_name}: startAt "
              f"{datetime.fromtimestamp(timestamp)} → {datetime.fromtimestamp(corr_ts)} "
              f"(corrected from actual set times)", flush=True)
    write_json(json_data, f"{event_dir}/attr.json", with_version=True)

def download_standings(event_id, event_dir):
    """Save standings data"""
    standings_data = []
    user_data = []

    query = get_standings_query()
    variables = {"eventId": event_id}
    keys = ["event", "standings"]
    standings_data = fetch_all_nodes(query, variables, keys, per_page=STANDINGS_PER_PAGE)

    user_data = []
    player_data = []
    entrant2user = {}
    for node in standings_data:
        if node['entrant']['participants'] is not None:
            user_data.append(node['entrant']['participants'][0]['user'])
            player_data.append(node['entrant']['participants'][0]['player'])
            if node['entrant']['participants'][0]['user'] is not None and node['entrant']['participants'][0]['player'] is not None:
                entrant2user[node['entrant']['id']] = node['entrant']['participants'][0]['user']['id']

    placements = [
        (node['placement'], entrant2user[node['entrant']['id']] if node['entrant']['id'] in entrant2user else None)
        for node in standings_data
        if node['entrant']['participants'] is not None
    ]
    placements.sort(key=lambda x: x[0])
    placements_dicts = [
        {"placement": placement, "user_id": user_id}
        for placement, user_id in placements
    ]
    
    os.makedirs(event_dir, exist_ok=True)
    json_data = {
        "data": placements_dicts
    }
    write_json(json_data, f"{event_dir}/standings.json", with_version=True)
    return user_data, player_data, entrant2user

def download_seeds(event_id, user_data, player_data, entrant2user, event_dir):
    phase_id = fetch_phase_id(event_id)
    query = get_seeds_query()
    variables = {"phaseId": phase_id}
    keys = ["phase", "seeds"]
    seeds_data = fetch_all_nodes(query, variables, keys, per_page=SEEDS_PER_PAGE)

    for seed in seeds_data:
        if seed['entrant']['participants'] is not None:
            if seed['entrant']['id'] not in entrant2user:
                user_data.append(seed['entrant']['participants'][0]['user'])
                player_data.append(seed['entrant']['participants'][0]['player'])
                if seed['entrant']['participants'][0]['user'] is not None and seed['entrant']['participants'][0]['player'] is not None:
                    entrant2user[seed['entrant']['id']] = seed['entrant']['participants'][0]['user']['id']

    seeds_numbers = [(seed['seedNum'], entrant2user[seed['entrant']['id']] if seed['entrant']['id'] in entrant2user else None) for seed in seeds_data]
    seeds_numbers.sort(key=lambda x: x[0])
    seeds_dicts = [
        {"seed_num": seed_num, "user_id": user_id}
        for seed_num, user_id in seeds_numbers
    ]
    json_data = {
        "data": seeds_dicts
    }
    write_json(json_data, f"{event_dir}/seeds.json", with_version=True)

def extend_user_info(user_data, player_data, users, users_file_path, dirty_user_ids=None):
    """Append new users to users.jsonl; update existing users in memory if anything differs.

    dirty_user_ids: set to which ids of updated existing users are added (= used by the caller
        to write everything back with write_jsonl at the end).
    """
    new_users = []

    for user, player in zip(user_data, player_data):
        if user is None or player is None:
            continue
        user_id = user['id']
        player_id = player['id']
        gamer_tag = player['gamerTag']
        prefix = player['prefix']
        gender_pronoun = user['genderPronoun'] if user['genderPronoun'] is not None else "unknown"
        startgg_discriminator = user.get('discriminator')
        location = user.get('location') or {}
        country = location.get('country')
        addr_state = location.get('state')
        city = location.get('city')
        x_id = None
        x_name = None
        discord_id = None
        discord_name = None
        if user['authorizations'] is not None:
            for authorization in user['authorizations']:
                if authorization['type'] == 'TWITTER':
                    x_id = authorization['externalId']
                    x_name = authorization['externalUsername']
                elif authorization['type'] == 'DISCORD':
                    discord_id = authorization['externalId']
                    discord_name = authorization['externalUsername']

        api_record = {
            "user_id": user_id,
            "player_id": player_id,
            "gamer_tag": gamer_tag,
            "prefix": prefix,
            "gender_pronoun": gender_pronoun,
            "startgg_discriminator": startgg_discriminator,
            "country": country,
            "addr_state": addr_state,
            "city": city,
            "x_id": x_id,
            "x_name": x_name,
            "discord_id": discord_id,
            "discord_name": discord_name,
        }
        if user_id not in users:
            users[user_id] = api_record
            new_users.append(api_record)
        else:
            # Diff detection for existing users. API values of None (= not fetched / deleted) are also applied
            # (= trust the latest state on start.gg).
            existing = users[user_id]
            changed = False
            for k, v in api_record.items():
                if existing.get(k) != v:
                    existing[k] = v
                    changed = True
            if changed and dirty_user_ids is not None:
                dirty_user_ids.add(user_id)

    extend_jsonl(new_users, users_file_path, with_version=True)

def extend_tournament_info(new_tournament_info, tournament_file_path):
    extend_jsonl([new_tournament_info], tournament_file_path, with_version=True)

# Fetch tournaments for a given game, newest first
def fetch_latest_tournaments_by_game(game_id, country_code, limit=5, page=1):
    response_data = fetch_data_with_retries(
        get_tournaments_by_game_query(country_code),
        {"gameId": game_id, "perPage": limit, "page": page},
    )
    if "data" not in response_data or response_data["data"] is None or "tournaments" not in response_data["data"] or response_data["data"]["tournaments"] is None:
        raise FetchError(f"Error: 'data' or 'tournament' key not found in response for game {game_id}. Response data: {response_data}\n in fetch_latest_tournaments_by_game")
        
    tournaments = response_data["data"]["tournaments"]["nodes"]
    total_pages = response_data["data"]["tournaments"]["pageInfo"]["totalPages"]
    return tournaments, total_pages

def fetch_event_ids_from_tournament(tournament_id, game_id):
    # The query no longer filters by videogameId so $gameId is not needed there, but the argument
    # is still taken to check the SSBU tag match via game_id comparison.
    response_data = fetch_data_with_retries(
        get_tournament_events_query(),
        {"tournamentId": tournament_id},
    )
    if "data" not in response_data or response_data["data"] is None or "tournament" not in response_data["data"] or response_data["data"]["tournament"] is None:
        raise FetchError(f"Error: 'data' or 'tournament' key not found in response for tournament {tournament_id}. Response data: {response_data}\n in fetch_event_ids_from_tournament")

    events = response_data["data"]["tournament"]["events"] or []
    out = []
    for event in events:
        vg = (event.get("videogame") or {}).get("id")
        name = event.get("name") or ""
        # Normal: only events matching the given game_id.
        # Exception: lower-class brackets (B class / C class / Bclass, etc.) are accepted even without a videogame tag.
        # (On start.gg, B-class side events sometimes have their videogame left unset.)
        if str(vg) == str(game_id):
            out.append((event["id"], event["name"], event["isOnline"]))
        elif _LOWER_CLASS_NAME_PAT.search(name):
            out.append((event["id"], event["name"], event["isOnline"]))
    return out

def fetch_phase_id(event_id):
    """Return the id of the event's first phase (= for the seeding query).

    The original implementation was a pagination-style while True loop, but both branches always
    returned/raised, so it never looped. Changed to fetch many phases in one page with a larger
    per_page. (Phases are usually 1-5, so per_page=100 is plenty.)
    """
    response_data = fetch_data_with_retries(
        get_phase_groups_query(),
        {"eventId": event_id, "page": 1, "perPage": 100},
    )
    if "data" not in response_data or "event" not in response_data["data"]:
        raise FetchError(
            f"Error: 'data' or 'event' key not found in response for event {event_id}. "
            f"Response data: {response_data}\n in fetch_phase_id"
        )
    event_data = response_data["data"]["event"]
    if event_data and event_data.get("phases"):
        return event_data["phases"][0]["id"]
    raise NoPhaseError(
        f"Error: No phases found for event {event_id}. Response data: {response_data}\n in fetch_phase_id"
    )

def write_done_tournaments(tournament_id, file_path):
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{tournament_id}\n")
        f.flush()

if __name__ == "__main__":
    main()
