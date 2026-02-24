import os
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
from scripts.utils import (
    country_code2region, get_date_parts, get_event_directory,
    read_users_jsonl, read_set, read_tournaments_jsonl,
    write_json, extend_jsonl, write_jsonl,
    set_indent_num,
    fetch_data_with_retries, fetch_all_nodes,
    set_retry_parameters, set_api_parameters,
    FetchError, NoPhaseError,
)

REQUIRED_EVENT_FILES = ("attr.json", "matches.json", "standings.json", "seeds.json")
TOURNAMENTS_PER_PAGE = 100
STANDINGS_PER_PAGE = 200
SEEDS_PER_PAGE = 200
SETS_PER_PAGE = 50

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

def download_all_tournaments(game_id, country_code, start_date, finish_date, startgg_dir, done_file_path, users_file_path, tournament_file_path):
    done_tournaments = read_set(done_file_path, as_int=True)
    users = read_users_jsonl(users_file_path)
    tournaments = read_tournaments_jsonl(tournament_file_path)
    print(f"done_tournaments: {len(done_tournaments)}")
    print(f"users: {len(users)}")
    print(f"tournaments: {len(tournaments)}")
    rewrite_tournaments = False
    existing_tournament_ids = set(tournaments.keys())

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

                for event_id, event_name, is_online in events_info:
                    
                    year, month, day = get_date_parts(timestamp)
                    event_dir = get_event_directory(startgg_dir, country_code, year, month, day, tournament_name, event_name)

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
                    write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir)

                    existing_events = tournaments[tournament_id]["events"]
                    if not any(e.get("event_id") == event_id for e in existing_events):
                        existing_events.append({
                            "event_id": event_id,
                            "event_name": event_name,
                            "path": event_dir
                        })
                        if tournament_id in existing_tournament_ids:
                            rewrite_tournaments = True
                # ファイルを保存
                if len(tournaments[tournament_id]["events"]) > 0:
                    if rewrite_tournaments:
                        pass
                    else:
                        extend_tournament_info(tournaments[tournament_id], tournament_file_path)
                    done_tournaments.add(tournament_id)
                    write_done_tournaments(tournament_id, done_file_path)

            except FetchError as e:
                print(e)
                continue

        if page >= total_pages:
            break
        page += 1

    if rewrite_tournaments:
        write_jsonl(list(tournaments.values()), tournament_file_path, with_version=True)

# イベントのセットデータを保存する関数
def download_all_set(event_id, entrant2user, event_dir):
    all_sets = fetch_all_sets(event_id)
    if not all_sets:
        return

    os.makedirs(event_dir, exist_ok=True)
    write_matches(all_sets, entrant2user, event_dir)

def fetch_all_sets(event_id):
    query = get_event_sets_query()
    variables = {"eventId": event_id}
    keys = ["event", "sets"]
    all_sets = fetch_all_nodes(query, variables, keys, per_page=SETS_PER_PAGE)
    
    return all_sets

def write_matches(all_nodes, entrant2user, event_dir):
    """マッチデータを保存する関数"""
    json_data = {"data": []}
    for node in all_nodes:
        if node['slots'] is None or len(node['slots']) != 2:
            continue

        slot0 = node['slots'][0]
        slot1 = node['slots'][1]
        if slot0['entrant'] is None or slot1['entrant'] is None or slot0['standing'] is None or slot1['standing'] is None:
            continue
        
        # スコアがNoneの場合は0を設定
        score0 = slot0['standing']['stats']['score']['value'] if slot0['standing']['stats']['score']['value'] is not None else 0
        score1 = slot1['standing']['stats']['score']['value'] if slot1['standing']['stats']['score']['value'] is not None else 0
        
        winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot == slot0 else slot0
        winner_score = score0 if winner_slot == slot0 else score1
        loser_score = score1 if winner_slot == slot0 else score0
        
        dq = (score0 < 0 or score1 < 0)
        cancel = score0 == 0 and score1 == 0
        
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
        json_data["data"].append(match_data)
        
    write_json(json_data, f"{event_dir}/matches.json", with_version=True)

def write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir):
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
    response_data = fetch_data_with_retries(
        get_tournament_events_query(),
        {"tournamentId": tournament_id, "gameId": game_id},
    )
    if "data" not in response_data or response_data["data"] is None or "tournament" not in response_data["data"] or response_data["data"]["tournament"] is None:
        raise FetchError(f"Error: 'data' or 'tournament' key not found in response for tournament {tournament_id}. Response data: {response_data}\n in fetch_event_ids_from_tournament")
    
    events = response_data["data"]["tournament"]["events"]
    return [(event["id"], event["name"], event["isOnline"]) for event in events]

def fetch_phase_id(event_id):
    page = 1
    per_page = 10
    while True:
        response_data = fetch_data_with_retries(
            get_phase_groups_query(),
            {"eventId": event_id, "page": page, "perPage": per_page}
        )
        if "data" not in response_data or "event" not in response_data["data"]:
            raise FetchError(f"Error: 'data' or 'event' key not found in response for event {event_id}. Response data: {response_data}\n in fetch_phase_id")
        event_data = response_data["data"]["event"]
        if event_data and event_data["phases"]:
            return event_data["phases"][0]["id"]
        else:
            raise NoPhaseError(f"Error: No phases found for event {event_id}. Response data: {response_data}\n in fetch_phase_id")

def write_done_tournaments(tournament_id, file_path):
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{tournament_id}\n")
        f.flush()

if __name__ == "__main__":
    main()
