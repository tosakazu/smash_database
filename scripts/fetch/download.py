import os
import re
import shutil
import argparse
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import (
    get_event_sets_query, get_standings_query, get_seeds_query,
    get_tournament_events_query, get_phase_groups_query, get_tournaments_by_game_query,
)

# 下位クラス bracket: Bクラス/Cクラス/Bclass/B_Class/b_class 等.
# start.gg で videogame タグが未設定の side event を救うためのフォールバック.
_LOWER_CLASS_NAME_PAT = re.compile(
    r'[BCDEＢＣＤＥ]\s*クラス|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)
from scripts.utils import (
    country_code2region, get_date_parts, get_event_directory,
    read_users_jsonl, read_set, read_tournaments_jsonl,
    write_json, extend_jsonl, write_jsonl,
    set_indent_num,
    fetch_data_with_retries, fetch_all_nodes,
    set_retry_parameters, set_api_parameters,
    FetchError, NoPhaseError,
)
# v2 (refetch) の fetch / write logic を流用して新規 daily download でも同一スキーマを生成.
from scripts.fetch.redownload_matches_v2 import (
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
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid datetime '{value}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
    )

def main():
    # コマンドライン引数の設定
    parser = argparse.ArgumentParser(description="Download tournament data from start.gg")
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="API URL")
    parser.add_argument("--token", required=True, help="API token")
    parser.add_argument(
        "--start_date",
        type=parse_date_or_datetime,
        default=None,
        help="Upper bound datetime for retrieval (inclusive). Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
    )
    parser.add_argument(
        "--finish_date",
        type=parse_date_or_datetime,
        default=datetime(2018, 1, 1),
        help="Lower bound datetime for retrieval (inclusive). Format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
    )
    parser.add_argument("--max_retries", type=int, default=100, help="Maximum number of retries for API requests")
    parser.add_argument("--retry_delay", type=int, default=5, help="Delay between retries in seconds")
    parser.add_argument("--indent_num", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--startgg_dir", default="data/startgg/events", help="Directory to save event data")
    parser.add_argument("--done_file_path", default="data/startgg/done.csv", help="Path to the file recording completed downloads")
    parser.add_argument("--users_file_path", default="data/startgg/users.jsonl", help="Path to the file recording startgg user info")
    parser.add_argument("--tournament_file_path", default="data/startgg/tournaments.jsonl", help="Path to the file recording tournament info")
    parser.add_argument("--game_id", default="1386", help="Game ID for tournament retrieval. see https://developer.start.gg/docs/examples/queries/videogame-id-by-name/")
    parser.add_argument("--country_code", default="", help="Country code for tournament retrieval. e.g. JP")
    args = parser.parse_args()

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)
    if args.start_date is not None and args.start_date < args.finish_date:
        raise ValueError("--start_date must be greater than or equal to --finish_date.")

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

def event_files_complete(event_dir):
    return all(os.path.exists(os.path.join(event_dir, name)) for name in REQUIRED_EVENT_FILES)

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
    existing_tournament_ids = set(tournaments.keys())
    # Index of event_id -> (tournament_id, old_path) for detecting date-change duplicates.
    event_id_index = _build_event_id_index(tournaments)

    page = 1
    while True:
        try:
            tournaments_info, total_pages = fetch_latest_tournaments_by_game(game_id, country_code=country_code, limit=TOURNAMENTS_PER_PAGE, page=page)
        except FetchError as e:
            print(e)
            continue
        print(f"Progress: {page}/{total_pages}")
        if not tournaments_info:
            break

        for tournament in tournaments_info:
            try:
                tournament_id = tournament["id"]
                tournament_name = tournament["name"]
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

                now_timestamp = int(datetime.now().timestamp())
                if end_timestamp is None or end_timestamp > now_timestamp:
                    print(f"({tournament_name} {datetime.fromtimestamp(timestamp)}) is not finished yet.")
                    continue

                tournament_dt = datetime.fromtimestamp(timestamp)
                if start_date is not None and tournament_dt > start_date:
                    print(f"({tournament_name} {tournament_dt}) is newer than start_date. Skipping.")
                    continue

                if tournament_id in done_tournaments:
                    tournament_entry = tournaments.get(tournament_id)
                    if tournament_entry and tournament_events_complete(tournament_entry):
                        # Check if any event is stored under an outdated date
                        # directory. If so, move it to the correct path.
                        year, month, day = get_date_parts(timestamp)
                        needs_move = False
                        for ev in tournament_entry.get("events", []):
                            old_path = ev.get("path", "")
                            new_path = get_event_directory(
                                startgg_dir, country_code, year, month, day,
                                tournament_name, ev.get("event_name", ""),
                            )
                            if old_path and new_path and old_path != new_path and os.path.isdir(old_path):
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
                    print(f"({tournament_name} {datetime.fromtimestamp(timestamp)}) is marked done but files are missing. Re-downloading.")

                print(f"Download {tournament_name}, date: {tournament_dt}")

                if tournament_dt < finish_date:
                    print("!!!downloaded all!!!")
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
                                # Same path, files present — skip re-download
                                continue

                        user_data, player_data, entrant2user = download_standings(event_id, event_dir)
                        num_entrants = len(user_data)
                        try:
                            download_seeds(event_id, user_data, player_data, entrant2user, event_dir)
                        except NoPhaseError as e:
                            print(f"No phase found for event {event_name}. Skipping.")
                            continue
                        extend_user_info(user_data, player_data, users, users_file_path)
                        download_all_set(event_id, entrant2user, event_dir)
                        labels = {}
                        write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir, end_timestamp=end_timestamp)

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

                # ファイルを保存
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
                    if not any_event_failed:
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

# イベントのセットデータを保存する関数
def download_all_set(event_id, entrant2user, event_dir):
    """event の matches.json を v2 schema で生成 (match_id / bracket_label / global_round 等).

    entrant2user 引数は API 互換性のため受け取るが、内部で all_sets から再構築するので未使用.
    """
    all_sets_with_phase = fetch_all_sets(event_id)
    if not all_sets_with_phase:
        return
    os.makedirs(event_dir, exist_ok=True)
    from pathlib import Path as _Path
    _write_matches_v2_impl(event_id, all_sets_with_phase, _Path(event_dir))

def fetch_all_sets(event_id):
    """event の全 sets を (set_node, phase_info, pg_info) tuple のリストで返す.

    v1 (event-level query + fetch_all_nodes) から v2 logic (phase_group 単位 + totalPages
    + fallback retry + dedup) に変更. 篝火#15 等の大型 event での取りこぼしバグを修正.
    write_matches_v2 に渡せるよう phase_info / pg_info を enrich した tuple リストを返す.
    """
    import time as _time
    phases = _fetch_event_phases_impl(event_id)
    _time.sleep(_V2_API_DELAY_SEC)  # phases query 後に rate-limit 緩和.
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
                "wave": pg.get("wave"),
            }
            try:
                pg_sets = _fetch_phase_group_sets_impl(pg_id, per_page=50)
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
            _time.sleep(_V2_API_DELAY_SEC)  # 各 phase_group 間で rate-limit 緩和 (v2 と同じ).
    if pg_failures:
        print(f"[fetch_all_sets] event={event_id}: {len(pg_failures)} phase_groups failed: {pg_failures}", flush=True)
    return all_sets_with_phase

def write_matches(all_nodes, entrant2user, event_dir):
    """マッチデータを保存する関数"""
    json_data = {"data": []}
    # set.id ベース dedup (= fetch 層を通り抜けた同 ID の重複を最終 cut).
    # tuple (pg_id, round, winner_uid, loser_uid) ベース dedup (= start.gg が同じ試合を
    # 異なる set id で返す稀ケース対策, v2 と同じポリシー).
    seen_set_ids = set()
    seen_match_keys = set()
    dup_set_id = 0
    dup_match_key = 0
    for node in all_nodes:
        nid = node.get('id') if isinstance(node, dict) else None
        if nid is not None:
            if nid in seen_set_ids:
                dup_set_id += 1
                continue
            seen_set_ids.add(nid)
        if node['slots'] is None or len(node['slots']) != 2:
            continue

        slot0 = node['slots'][0]
        slot1 = node['slots'][1]
        if slot0['entrant'] is None or slot1['entrant'] is None or slot0['standing'] is None or slot1['standing'] is None:
            continue
        
        # スコアがNoneの場合は0を設定
        score0 = slot0['standing']['stats']['score']['value'] if slot0['standing']['stats']['score']['value'] is not None else 0
        score1 = slot1['standing']['stats']['score']['value'] if slot1['standing']['stats']['score']['value'] is not None else 0

        # winner/loser 判定は node.winnerId (entrant ID) を優先使用.
        # score 比較だけだと score 0/0 (cancel/DQ で score 取得失敗) で常に slot1 winner と
        # 誤判定するバグがあった (池スマ#4 メロンおじさんで発覚).
        winner_eid = node.get('winnerId')
        ent0_id = slot0['entrant']['id']
        ent1_id = slot1['entrant']['id']
        if winner_eid is not None and winner_eid in (ent0_id, ent1_id):
            winner_slot = slot0 if winner_eid == ent0_id else slot1
        else:
            # winnerId 不明 → score 比較. 同点 (0-0等) なら確定できないので skip.
            if score0 == score1:
                continue
            winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot == slot0 else slot0
        winner_score = score0 if winner_slot == slot0 else score1
        loser_score = score1 if winner_slot == slot0 else score0

        dq = (score0 < 0 or score1 < 0)
        cancel = (score0 == 0 and score1 == 0 and winner_eid is None)
        
        details = [
                    {
                        "game_id": game['id'],
                        "order_num": game['orderNum'],
                        "winner_id": entrant2user[game['winnerId']] if game['winnerId'] in entrant2user else None,
                        "entrant1_score": game['entrant1Score'],
                        "entrant2_score": game['entrant2Score'],
                        "stage": game['stage']['name'] if game['stage'] else None,
                        "selections": [
                            {
                                "user_id": entrant2user[selection['entrant']['id']] if selection['entrant']['id'] in entrant2user else None,
                                "selection_id": selection['id'],
                                "character_id": selection['character']['id'],
                                "character_name": selection['character']['name']
                            }
                            for i, selection in enumerate(game['selections'])
                        ] if game['selections'] is not None else []
                    }
                    for game in node['games']
                ] if node['games'] is not None else []

        phase = None
        wave = None
        if node['phaseGroup'] is not None:
            phase = node['phaseGroup']['displayIdentifier']
            if node['phaseGroup']['wave'] is not None:
                wave = node['phaseGroup']['wave']['identifier']
        match_data = {
                "winner_id": entrant2user[winner_slot['entrant']['id']] if winner_slot['entrant']['id'] in entrant2user else None,
                "loser_id": entrant2user[loser_slot['entrant']['id']] if loser_slot['entrant']['id'] in entrant2user else None,
                "winner_score": winner_score,
                "loser_score": loser_score,
                "round_text": node['fullRoundText'],
                "round": node['round'],
                "phase": phase,
                "wave": wave,
                "dq": dq,
                "cancel": cancel,
                "state": node['state'],
                "details": details
            }
        # tuple-level dedup: 同じ (pg_id, round, winner_uid, loser_uid) を持つ match が既に
        # 別 set_id で書かれていたら重複扱いで skip (v2 と同じポリシー).
        wuid = match_data["winner_id"]
        luid = match_data["loser_id"]
        pg_id = node['phaseGroup']['id'] if (node.get('phaseGroup') is not None) else None
        if wuid is not None and luid is not None:
            mkey = (pg_id, node['round'], wuid, luid)
            if mkey in seen_match_keys:
                dup_match_key += 1
                continue
            seen_match_keys.add(mkey)
        json_data["data"].append(match_data)
    if dup_set_id or dup_match_key:
        print(f"  [write_matches] dedup: set_id_dups={dup_set_id} match_key_dups={dup_match_key}", flush=True)
    write_json(json_data, f"{event_dir}/matches.json", with_version=True)

def write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir, end_timestamp=None):
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
        "timestamp": timestamp,
        "end_timestamp": end_timestamp,
    }
    write_json(json_data, f"{event_dir}/attr.json", with_version=True)

def download_standings(event_id, event_dir):
    """スタンディングデータを保存する関数"""
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

def extend_user_info(user_data, player_data, users, users_file_path):
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

        if user_id not in users:
            new_user = {
                "user_id": user_id,
                "player_id": player_id,
                "gamer_tag": gamer_tag,
                "prefix": prefix,
                "gender_pronoun": gender_pronoun,
                "startgg_discriminator": startgg_discriminator,
                "x_id": x_id,
                "x_name": x_name,
                "discord_id": discord_id,
                "discord_name": discord_name
            }
            users[user_id] = new_user
            new_users.append(new_user)

    extend_jsonl(new_users, users_file_path, with_version=True)

def extend_tournament_info(new_tournament_info, tournament_file_path):
    extend_jsonl([new_tournament_info], tournament_file_path, with_version=True)

# 特定のゲームのトーナメントを最新のものから取得する関数
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
    # クエリは videogameId フィルタを外したので $gameId は不要だが、
    # game_id 比較で SSBU タグマッチを確認するため引数として受け取る.
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
        # 通常: 指定 game_id にマッチするイベントのみ採用.
        # 例外: 下位クラス bracket (Bクラス/Cクラス/Bclass 等) は videogame タグ未設定でも採用.
        # (start.gg では Bクラス side event の videogame を設定し忘れているケースがある)
        if str(vg) == str(game_id):
            out.append((event["id"], event["name"], event["isOnline"]))
        elif _LOWER_CLASS_NAME_PAT.search(name):
            out.append((event["id"], event["name"], event["isOnline"]))
    return out

def fetch_phase_id(event_id):
    """event の最初の phase の id を返す (= seeding query 用).

    元実装は while True ループで pagination 風だったが、両分岐で必ず return/raise する
    無限ループ無し dead loop だった. per_page を大きめにして 1 ページで多くの phase を
    取得するよう変更. (phase 数は通常 1-5 なので per_page=100 で十分.)
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
