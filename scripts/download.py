import requests
import time
import json
import os
import csv
import datetime
import argparse
from queries import (
    get_event_sets_query, get_standings_query, get_seeds_query, 
    get_tournament_query, get_phase_groups_query, get_tournaments_by_game_query,
    fetch_data_with_retries,
    set_retry_parameters,
    set_api_parameters
)
from utils import (
    country_code2region, get_date_parts, get_event_directory,
    load_users_json, load_csv, load_set,
    is_ultimate_singles,
    write_tournaments, write_event_paths, write_id_paths,
    write_json, set_indent_num
)

JSON_VERSION = "1.0"

def main():
    # コマンドライン引数の設定
    parser = argparse.ArgumentParser(description="Download tournament data from start.gg")
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="API URL")
    parser.add_argument("--token", required=True, help="API token")
    parser.add_argument("--finish_date", type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'), default=datetime.datetime(2019, 1, 1), help="Finish date for data retrieval (YYYY-MM-DD)")
    parser.add_argument("--max_retries", type=int, default=100, help="Maximum number of retries for API requests")
    parser.add_argument("--retry_delay", type=int, default=5, help="Delay between retries in seconds")
    parser.add_argument("--indent_num", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--startgg_dir", default="data/startgg/events", help="Directory to save event data")
    parser.add_argument("--done_file_path", default="data/startgg/done.csv", help="Path to the file recording completed downloads")
    parser.add_argument("--users_file_path", default="data/startgg/users.json", help="Path to the file recording startgg user info")
    parser.add_argument("--tournament_file_path", default="data/startgg/tournaments.csv", help="Path to the file recording tournament info")
    parser.add_argument("--event_path_file_path", default="data/startgg/event2path.csv", help="Path to the file recording event path info")
    parser.add_argument("--id_path_file_path", default="data/startgg/events/id2path.csv", help="Path to the file recording id path info")
    parser.add_argument("--game_id", default="1386", help="Game ID for tournament retrieval. see https://developer.start.gg/docs/examples/queries/videogame-id-by-name/")

    args = parser.parse_args()

    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    download_all_tournaments(args.game_id, args.finish_date, args.startgg_dir, args.done_file_path, args.users_file_path, args.tournament_file_path, args.event_path_file_path, args.id_path_file_path)

def download_all_tournaments(game_id, finish_date, startgg_dir, done_file_path, users_file_path, tournament_file_path, event_path_file_path, id_path_file_path):
    done_tournaments = load_set(done_file_path)
    users = load_users_json(users_file_path)
    tournaments = load_csv(tournament_file_path)
    event_paths = load_csv(event_path_file_path)
    id_paths = load_csv(id_path_file_path)
    print(f"done_tournaments: {len(done_tournaments)}")
    print(f"users: {len(users)}")
    print(f"tournaments: {len(tournaments)}")
    print(f"event_paths: {len(event_paths)}")
    print(f"id_paths: {len(id_paths)}")

    page = 1
    while True:
        tournaments_info, total_pages = fetch_latest_tournaments_by_game(game_id, page=page)
        if not tournaments_info:
            break

        for tournament in tournaments_info:
            tournament_id = str(tournament["id"])

            if tournament_id in done_tournaments:
                print(f"Tournament {tournament_id} already downloaded. Skipping.")
                continue

            tournament_name = tournament["name"]
            date = tournament["startAt"]
            country_code = tournament.get("countryCode", "")
            is_online = tournament.get("isOnline", False)

            if datetime.datetime.fromtimestamp(date) < finish_date:
                print("!!!downloaded all!!!")
                return

            tournaments[tournament_id] = tournament_name
            events_info = fetch_event_ids_from_tournament(tournament_id, game_id)

            for event_id, event_name in events_info:

                if is_not_ultimate_singles(event_name):
                    continue
                
                year, month, day = get_date_parts(date)
                event_dir = get_event_directory(startgg_dir, country_code, year, month, day, tournament_name, event_name)

                user_data, player_data, entrant2user = download_standings(event_id, event_dir)
                num_entrants = len(user_data)
                add_user_info(user_data, player_data, users)
                download_seeds(event_id, user_data, player_data, entrant2user, event_dir)
                download_all_set(event_id, entrant2user, event_dir)
                write_event_attributes(num_entrants, event_id, event_name, tournament_name, date, country_code, is_online, event_dir)

                event_paths[event_id] = (date, event_dir)
                id_paths[event_id] = event_dir

            # ファイルを保存
            write_users(users, users_file_path)
            write_event_paths(event_paths, event_path_file_path)
            write_id_paths(id_paths, id_path_file_path)
            write_tournaments(tournaments, tournament_file_path)

            done_tournaments.add(tournament_id)
            write_done_tournaments(tournament_id, done_file_path)

        if page >= total_pages:
            break
        page += 1

# イベントのセットデータを保存する関数
def download_all_set(event_id, entrant2user, event_dir):
    all_nodes = fetch_event_nodes(event_id)
    if not all_nodes:
        return

    os.makedirs(event_dir, exist_ok=True)
    write_matches(all_nodes, entrant2user, event_dir)

def fetch_event_nodes(event_id):
    """イベントのノードを取得する関数"""
    all_nodes = []
    print("\nrequest event", event_id)
    for i in range(1000):
        response_data = fetch_data_with_retries(get_event_sets_query(), {"eventId": event_id, "perPage": 10, "page": i+1})
        if "data" not in response_data or "event" not in response_data["data"]:
            print("Error: 'data' or 'event' key not found in response")
            print(response_data)
            break
        nodes = response_data["data"]["event"]["sets"]["nodes"]
        if len(nodes) == 0:
            break
        all_nodes += nodes
        time.sleep(2)
        print("page", i+1, end=" ", flush=True)
    return all_nodes

def write_matches(all_nodes, entrant2user, event_dir):
    """マッチデータを保存する関数"""
    json_data = {
        "version": JSON_VERSION,
        "data": []
    }
    for node in all_nodes:
        if node['slots'] is None or len(node['slots']) != 2:
            continue

        slot0 = node['slots'][0]
        slot1 = node['slots'][1]
        if slot0['entrant'] is None or slot1['entrant'] is None or slot0['standing'] is None or slot1['standing'] is None:
            continue
        
        # スコアがNoneの場合は-1を設定
        score0 = slot0['standing']['stats']['score']['value'] if slot0['standing']['stats']['score']['value'] is not None else -1
        score1 = slot1['standing']['stats']['score']['value'] if slot1['standing']['stats']['score']['value'] is not None else -1
        
        winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot == slot0 else slot0
        winner_score = score0 if winner_slot == slot0 else score1
        loser_score = score1 if winner_slot == slot0 else score0
        
        dq = score0 < 0 or score1 < 0
        
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

        match_data = {
                "winner_id": entrant2user[winner_slot['entrant']['id']] if winner_slot['entrant']['id'] in entrant2user else None,
                "loser_id": entrant2user[loser_slot['entrant']['id']] if loser_slot['entrant']['id'] in entrant2user else None,
                "winner_score": winner_score,
                "loser_score": loser_score,
                "round_text": node['fullRoundText'],
                "round": node['round'],
                "dq": dq,
                "state": node['state'],
                "details": details
            }
        json_data["data"].append(match_data)
        
        write_json(json_data, f"{event_dir}/matches.json")

def write_event_attributes(num_entrants, event_id, event_name, tournament_name, date, country_code, is_online, event_dir):
    """イベントの属性を保存する関数"""
    json_data = {
        "version": JSON_VERSION,
        "event_id": str(event_id),
        "tournament_name": tournament_name,
        "event_name": event_name,
        "date": date,
        "region": country_code2region(country_code),
        "country_code": country_code,
        "num_entrants": num_entrants,
        "offline": not is_online,
        "rule": "unknown"
    }
    write_json(json_data, f"{event_dir}/attr.json")

def download_standings(event_id, event_dir):
    """スタンディングデータを保存する関数"""
    standings_data = []
    user_data = []
    response_data = fetch_data_with_retries(get_standings_query(), {"eventId": event_id})
    if response_data["data"]["event"]["standings"] is not None:
        standings_data = response_data["data"]["event"]["standings"]["nodes"]
    else:
        print(f"No standings data found for event {event_id}. Response: {response_data}")

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
        "version": JSON_VERSION,
        "data": placements_dicts
    }
    write_json(json_data, f"{event_dir}/standings.json")
    return user_data, player_data, entrant2user

def download_seeds(event_id, user_data, player_data, entrant2user, event_dir):
    """シードデータを保存する関数"""
    seeds_data = fetch_seeds_data(fetch_phase_id(event_id))

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
        "version": JSON_VERSION,
        "data": seeds_dicts
    }
    write_json(json_data, f"{event_dir}/seeds.json")

def add_user_info(user_data, player_data, users):
    """プレイヤー情報を収集する関数"""
    for user, player in zip(user_data, player_data):
        if user is None or player is None:
            continue
        user_id = str(user['id'])
        gamer_tag = player['gamerTag']
        prefix = player['prefix']
        gender_pronoun = user['genderPronoun'] if user['genderPronoun'] is not None else "unknown"
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
            users[user_id] = {
                "user_id": user_id,
                "gamer_tag": gamer_tag,
                "prefix": prefix,
                "gender_pronoun": gender_pronoun,
                "x_id": x_id,
                "x_name": x_name,
                "discord_id": discord_id,
                "discord_name": discord_name
            }

# プレイヤー情報を保存す関数
def write_users(users, file_path):
    write_json({"version": JSON_VERSION, "data": list(users.values())}, file_path)

# 特定のゲームのトーナメントを最新のものから取得する関数
def fetch_latest_tournaments_by_game(game_id, limit=5, page=1):
    response_data = fetch_data_with_retries(
        get_tournaments_by_game_query(),
        {"gameId": game_id, "perPage": limit, "page": page},
    )
    tournaments = response_data["data"]["tournaments"]["nodes"]
    total_pages = response_data["data"]["tournaments"]["pageInfo"]["totalPages"]
    return tournaments, total_pages

def fetch_event_ids_from_tournament(tournament_id, game_id):
    response_data = fetch_data_with_retries(
        get_tournament_query(),
        {"tournamentId": tournament_id, "gameId": game_id},
    )
    if "data" not in response_data or "tournament" not in response_data["data"]:
        print(f"Error: 'data' or 'tournament' key not found in response for tournament {tournament_id}.")
        return []
    
    events = response_data["data"]["tournament"]["events"]
    return [(event["id"], event["name"]) for event in events]

def fetch_phase_id(event_id):
    response_data = fetch_data_with_retries(
        get_phase_groups_query(),
        {"eventId": event_id},
    )
    event_data = response_data.get("data", {}).get("event")
    if event_data and event_data["phases"]:
        return event_data["phases"][0]["id"]
    else:
        print("Error: No phases found for event", event_id)
        return None

def fetch_seeds_data(phase_id):
    response_data = fetch_data_with_retries(
        get_seeds_query(),
        {"phaseId": phase_id, "page": 1, "perPage": 100},
    )
    if "data" in response_data and response_data["data"]["phase"] is not None:
        seeds_nodes = response_data["data"]["phase"]["seeds"]["nodes"]
        return seeds_nodes
    else:
        print(f"Error: 'data' or 'phase' key not found in response for phase {phase_id}.")
        return []

def write_done_tournaments(tournament_id, file_path):
    with open(file_path, "a") as f:
        f.write(f"{tournament_id}\n")
        f.flush()

if __name__ == "__main__":
    main()
