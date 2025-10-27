import os
import argparse
import sys
from datetime import datetime

# queries.py から必要なクエリ関数をインポート
# get_event_details_by_slug_query を追加する必要がある
from queries import (
    get_event_sets_query, get_standings_query, get_seeds_query,
    get_phase_groups_query, get_event_details_by_tournament_query # この関数を queries.py に追加想定
)
# utils.py から必要なユーティリティ関数をインポート
from utils import (
    country_code2region, get_date_parts, get_event_directory,
    read_users_jsonl, read_set, read_tournaments_jsonl,
    write_json, extend_jsonl,
    set_indent_num,
    fetch_data_with_retries, fetch_all_nodes,
    set_retry_parameters, set_api_parameters,
    FetchError, NoPhaseError,
    analyze_event_setting,
)
from openai import OpenAI

# --- 元のスクリプトから流用する関数群 ---

# イベントのセットデータを保存する関数
def download_all_set(event_id, entrant2user, event_dir):
    """イベントの全セットデータを取得し、matches.jsonとして保存する"""
    all_sets = fetch_all_sets(event_id)
    if not all_sets:
        print(f"No sets found for event {event_id}.")
        return

    os.makedirs(event_dir, exist_ok=True)
    write_matches(all_sets, entrant2user, event_dir)
    print(f"Successfully wrote matches.json for event {event_id} to {event_dir}")

def fetch_all_sets(event_id):
    """APIからイベントの全セットデータを取得する"""
    query = get_event_sets_query()
    variables = {"eventId": event_id}
    keys = ["event", "sets"]
    try:
        all_sets = fetch_all_nodes(query, variables, keys, per_page=10)
        return all_sets
    except FetchError as e:
        print(f"Error fetching sets for event {event_id}: {e}")
        return None

def write_matches(all_nodes, entrant2user, event_dir):
    """取得したセットデータを整形してmatches.jsonに書き込む"""
    json_data = {"data": []}
    processed_count = 0
    skipped_count = 0
    for node in all_nodes:
        # 必要な情報が欠けている場合はスキップ
        if node['slots'] is None or len(node['slots']) != 2:
            skipped_count += 1
            continue
        slot0 = node['slots'][0]
        slot1 = node['slots'][1]
        if (slot0['entrant'] is None or slot1['entrant'] is None or
            slot0['standing'] is None or slot1['standing'] is None or
            slot0['standing']['stats'] is None or slot1['standing']['stats'] is None or
            slot0['standing']['stats']['score'] is None or slot1['standing']['stats']['score'] is None):
            skipped_count += 1
            continue

        # entrant ID が entrant2user マッピングに存在するか確認
        entrant0_id = slot0['entrant']['id']
        entrant1_id = slot1['entrant']['id']
        if entrant0_id not in entrant2user or entrant1_id not in entrant2user:
            # print(f"Skipping set {node.get('id', 'N/A')} due to missing entrant ID in mapping.")
            skipped_count += 1
            continue

        # スコアがNoneの場合は0を設定
        score0 = slot0['standing']['stats']['score']['value'] if slot0['standing']['stats']['score']['value'] is not None else 0
        score1 = slot1['standing']['stats']['score']['value'] if slot1['standing']['stats']['score']['value'] is not None else 0

        # 勝者と敗者を決定
        winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot == slot0 else slot0
        winner_score = score0 if winner_slot == slot0 else score1
        loser_score = score1 if winner_slot == slot0 else score0

        winner_entrant_id = winner_slot['entrant']['id']
        loser_entrant_id = loser_slot['entrant']['id']

        dq = (score0 < 0 or score1 < 0)
        # start.ggではスコア0-0は未プレイまたはキャンセルを示すことが多い
        cancel = score0 == 0 and score1 == 0 and not dq

        # ゲーム詳細情報の処理
        details = []
        if node.get('games'):
            for game in node['games']:
                if game is None: continue # gameがNoneの場合スキップ
                winner_id_in_game = game.get('winnerId')
                selections_data = []
                if game.get('selections'):
                    for selection in game['selections']:
                         # 必要な情報が揃っているか確認
                        if (selection and selection.get('entrant') and selection['entrant'].get('id') and
                            selection.get('character') and selection['character'].get('id') and selection['character'].get('name')):
                            entrant_id_in_selection = selection['entrant']['id']
                            selections_data.append({
                                "user_id": entrant2user.get(entrant_id_in_selection), # .get()で安全にアクセス
                                "selection_id": selection.get('id'),
                                "character_id": selection['character']['id'],
                                "character_name": selection['character']['name']
                            })
                        else:
                            # print(f"Skipping selection due to missing data in game {game.get('id', 'N/A')}")
                            pass # 不完全なselectionはスキップ

                details.append({
                    "game_id": game.get('id'),
                    "order_num": game.get('orderNum'),
                    "winner_id": entrant2user.get(winner_id_in_game) if winner_id_in_game else None, # .get()で安全にアクセス
                    "entrant1_score": game.get('entrant1Score'),
                    "entrant2_score": game.get('entrant2Score'),
                    "stage": game.get('stage', {}).get('name') if game.get('stage') else None, # 安全なアクセス
                    "selections": selections_data
                })
        else:
            details = [] # gamesがない場合は空リスト

        # フェーズとウェーブ情報の処理
        phase = None
        wave = None
        if node.get('phaseGroup'):
            phase = node['phaseGroup'].get('displayIdentifier')
            if node['phaseGroup'].get('wave'):
                wave = node['phaseGroup']['wave'].get('identifier')

        # マッチデータの構築
        match_data = {
            "winner_id": entrant2user.get(winner_entrant_id), # .get()で安全にアクセス
            "loser_id": entrant2user.get(loser_entrant_id), # .get()で安全にアクセス
            "winner_score": winner_score,
            "loser_score": loser_score,
            "round_text": node.get('fullRoundText'),
            "round": node.get('round'),
            "phase": phase,
            "wave": wave,
            "dq": dq,
            "cancel": cancel,
            "state": node.get('state'), # COMPLETED, etc.
            "details": details
        }
        json_data["data"].append(match_data)
        processed_count += 1

    if processed_count > 0:
        write_json(json_data, f"{event_dir}/matches.json", with_version=True)
        print(f"Wrote {processed_count} matches to {event_dir}/matches.json. Skipped {skipped_count} incomplete sets.")
    else:
        print(f"No processable matches found after filtering. Skipped {skipped_count} incomplete sets.")


def write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, url, labels, is_online, event_dir):
    """イベントの属性情報をattr.jsonとして保存する"""
    os.makedirs(event_dir, exist_ok=True) # ディレクトリが存在しない場合は作成
    json_data = {
        "event_id": event_id,
        "tournament_name": tournament_name,
        "event_name": event_name,
        "region": country_code2region(place.get("country_code")) if place.get("country_code") else None, # 安全なアクセス
        "place": place, # country_code, city, lat, lng などを含む辞書
        "num_entrants": num_entrants,
        "offline": not is_online if is_online is not None else None, # is_onlineがNoneの場合を考慮
        "url": url, # トーナメントのURL
        "labels": labels if labels is not None else [], # OpenAIによる分析結果など
        "status": "completed", # イベントが終了していることを前提とする
        "timestamp": timestamp, # イベント開始タイムスタンプ
    }
    write_json(json_data, f"{event_dir}/attr.json", with_version=True)
    print(f"Successfully wrote attr.json for event {event_id} to {event_dir}")

def download_standings(event_id, event_dir):
    """スタンディングデータを取得し、standings.jsonとして保存し、ユーザー情報を返す"""
    standings_data = []
    user_data = []
    player_data = []
    entrant2user = {}

    query = get_standings_query()
    variables = {"eventId": event_id}
    keys = ["event", "standings"]
    try:
        standings_nodes = fetch_all_nodes(query, variables, keys, per_page=100)
        if not standings_nodes:
             print(f"No standings data found for event {event_id}.")
             return [], [], {} # データがない場合は空を返す
    except FetchError as e:
        print(f"Error fetching standings for event {event_id}: {e}")
        return [], [], {} # エラー時も空を返す

    placements_list = []
    processed_count = 0
    skipped_count = 0

    for node in standings_nodes:
        # 必要な情報が欠けている場合はスキップ
        if (node is None or node.get('entrant') is None or
            node['entrant'].get('participants') is None or
            not node['entrant']['participants'] or # リストが空でないか
            node['entrant']['participants'][0].get('user') is None or
            node['entrant']['participants'][0].get('player') is None or
            node.get('placement') is None or node['entrant'].get('id') is None):
            # print(f"Skipping standing entry due to missing data: {node}")
            skipped_count += 1
            continue

        user = node['entrant']['participants'][0]['user']
        player = node['entrant']['participants'][0]['player']
        entrant_id = node['entrant']['id']
        user_id = user.get('id')
        placement = node['placement']

        if user_id is None:
            # print(f"Skipping standing entry due to missing user ID: {node}")
            skipped_count += 1
            continue

        user_data.append(user)
        player_data.append(player)
        entrant2user[entrant_id] = user_id
        placements_list.append((placement, user_id))
        processed_count += 1

    if not placements_list:
        print(f"No valid placements could be processed for event {event_id}. Skipped {skipped_count} entries.")
        return user_data, player_data, entrant2user # ユーザー情報は返す可能性がある

    placements_list.sort(key=lambda x: x[0]) # 順位でソート
    placements_dicts = [
        {"placement": placement, "user_id": user_id}
        for placement, user_id in placements_list
        if user_id is not None # user_idがNoneでないものだけ含める
    ]

    os.makedirs(event_dir, exist_ok=True)
    json_data = {"data": placements_dicts}
    write_json(json_data, f"{event_dir}/standings.json", with_version=True)
    print(f"Successfully wrote {len(placements_dicts)} standings to {event_dir}/standings.json. Processed {processed_count}, Skipped {skipped_count} entries.")

    return user_data, player_data, entrant2user

def download_seeds(event_id, user_data, player_data, entrant2user, event_dir):
    """シードデータを取得し、seeds.jsonとして保存する"""
    try:
        phase_id = fetch_phase_id(event_id)
        if phase_id is None:
             # fetch_phase_id内でエラーログが出るはずなので、ここでは簡単なメッセージ
             print(f"Could not determine phase ID for event {event_id}. Skipping seed download.")
             return # phase_idがなければシードは取得できない
    except NoPhaseError as e:
        print(f"Skipping seed download for event {event_id}: {e}")
        return # NoPhaseErrorの場合もスキップ
    except FetchError as e:
        print(f"Error fetching phase ID for event {event_id}: {e}. Skipping seed download.")
        return # その他のFetchErrorの場合もスキップ

    query = get_seeds_query()
    variables = {"phaseId": phase_id}
    keys = ["phase", "seeds"]
    try:
        seeds_nodes = fetch_all_nodes(query, variables, keys, per_page=100)
        if not seeds_nodes:
            print(f"No seeds data found for phase {phase_id} in event {event_id}.")
            # シードファイルがなくても処理は続行可能なので空ファイルを作成しない
            # 空のseeds.jsonを作成したい場合はここで作成処理を入れる
            # write_json({"data": []}, f"{event_dir}/seeds.json", with_version=True)
            return # データがない場合は終了
    except FetchError as e:
        print(f"Error fetching seeds for event {event_id} (phase {phase_id}): {e}")
        return # エラー時は終了

    seeds_list = []
    processed_count = 0
    skipped_count = 0

    for seed in seeds_nodes:
        # 必要な情報が欠けている場合はスキップ
        if (seed is None or seed.get('entrant') is None or
            seed['entrant'].get('id') is None or
            seed.get('seedNum') is None):
            # print(f"Skipping seed entry due to missing data: {seed}")
            skipped_count += 1
            continue

        entrant_id = seed['entrant']['id']
        seed_num = seed['seedNum']

        # entrant2user マッピングに entrant_id が存在するか確認
        user_id = entrant2user.get(entrant_id)

        # entrant2userにない場合、参加者情報を取得試行 (standingsに含まれないシードのみの参加者など)
        if user_id is None:
            if (seed['entrant'].get('participants') and
                seed['entrant']['participants'][0].get('user') and
                seed['entrant']['participants'][0].get('player')):

                user = seed['entrant']['participants'][0]['user']
                player = seed['entrant']['participants'][0]['player']
                user_id = user.get('id')

                if user_id and entrant_id: # user_idとentrant_idが取得できたら
                    if user_id not in [u['id'] for u in user_data if u]: # 既存リストになければ追加
                         user_data.append(user)
                         player_data.append(player)
                    entrant2user[entrant_id] = user_id # マッピングにも追加
                    # print(f"Added user {user_id} from seed data.")
                else:
                    # print(f"Skipping seed entry {seed.get('id', 'N/A')} as user_id could not be determined even from participant data.")
                    skipped_count += 1
                    continue # user_idが特定できなければスキップ
            else:
                # print(f"Skipping seed entry {seed.get('id', 'N/A')} as user_id is missing and participant data is incomplete.")
                skipped_count += 1
                continue # participant情報もなければスキップ

        # user_idが確定したらリストに追加
        seeds_list.append((seed_num, user_id))
        processed_count += 1

    if not seeds_list:
        print(f"No valid seeds could be processed for event {event_id}. Skipped {skipped_count} entries.")
        return

    seeds_list.sort(key=lambda x: x[0]) # シード番号でソート
    seeds_dicts = [
        {"seed_num": seed_num, "user_id": user_id}
        for seed_num, user_id in seeds_list
        if user_id is not None # user_idがNoneでないものだけ含める
    ]

    os.makedirs(event_dir, exist_ok=True)
    json_data = {"data": seeds_dicts}
    write_json(json_data, f"{event_dir}/seeds.json", with_version=True)
    print(f"Successfully wrote {len(seeds_dicts)} seeds to {event_dir}/seeds.json. Processed {processed_count}, Skipped {skipped_count} entries.")


def extend_user_info(user_data, player_data, users, users_file_path):
    """新しいユーザー情報をusers辞書とusers.jsonlファイルに追加する"""
    new_users = []
    updated_count = 0
    added_count = 0

    for user, player in zip(user_data, player_data):
        # userまたはplayerがNone、またはIDがない場合はスキップ
        if user is None or player is None or user.get('id') is None or player.get('id') is None:
            continue

        user_id = user['id']
        player_id = player['id']
        gamer_tag = player.get('gamerTag')
        prefix = player.get('prefix')
        # genderPronoun が None の場合のデフォルト値を設定
        gender_pronoun = user.get('genderPronoun') if user.get('genderPronoun') is not None else "unknown"
        startgg_discriminator = user.get('discriminator')

        # authorizationsから情報を抽出
        x_id = None
        x_name = None
        discord_id = None
        discord_name = None
        if user.get('authorizations'):
            for auth in user['authorizations']:
                if auth and auth.get('type'): # authが存在し、typeキーを持つか確認
                    if auth['type'] == 'TWITTER':
                        x_id = auth.get('externalId')
                        x_name = auth.get('externalUsername')
                    elif auth['type'] == 'DISCORD':
                        discord_id = auth.get('externalId')
                        discord_name = auth.get('externalUsername')

        # ユーザーが既に存在するかチェック
        if user_id not in users:
            new_user_entry = {
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
            users[user_id] = new_user_entry
            new_users.append(new_user_entry)
            added_count += 1
        else:
            # 既存ユーザー情報と比較し、変更があれば更新（ここでは単純に追加のみ）
            # 更新ロジックが必要な場合はここに追加
            pass

    if new_users:
        extend_jsonl(new_users, users_file_path, with_version=True)
        print(f"Extended user info: Added {added_count} new users to {users_file_path}.")
    else:
        print("No new users to add.")

def extend_tournament_info(new_tournament_info, tournament_file_path):
    """新しいトーナメント情報をtournaments.jsonlファイルに追加する"""
    # tournaments.jsonl は追記型なので、単純に追加する
    extend_jsonl([new_tournament_info], tournament_file_path, with_version=True)
    print(f"Extended tournament info for tournament ID {new_tournament_info.get('tournament_id')} to {tournament_file_path}.")


def fetch_phase_id(event_id):
    """イベントIDから最初のフェーズIDを取得する"""
    page = 1
    per_page = 10 # 通常、フェーズは少ないので10件もあれば十分
    # フェーズID取得はリトライ対象とする
    try:
        response_data = fetch_data_with_retries(
            get_phase_groups_query(),
            {"eventId": event_id, "page": page, "perPage": per_page}
        )
    except FetchError as e:
        # fetch_data_with_retries内でリトライ失敗した場合
        print(f"Failed to fetch phase groups for event {event_id} after retries: {e}")
        raise # エラーを再発生させて呼び出し元で処理

    # レスポンスデータの検証
    if (not response_data or "data" not in response_data or
        response_data["data"] is None or "event" not in response_data["data"] or
        response_data["data"]["event"] is None):
        raise FetchError(f"Invalid response structure when fetching phases for event {event_id}. Response: {response_data}")

    event_data = response_data["data"]["event"]

    # phasesが存在し、空でなく、最初の要素にidがあるか確認
    if event_data.get("phases") and isinstance(event_data["phases"], list) and len(event_data["phases"]) > 0 and event_data["phases"][0].get("id"):
        return event_data["phases"][0]["id"]
    else:
        # フェーズが見つからない場合はNoPhaseErrorを送出
        raise NoPhaseError(f"No phases found for event {event_id}. Response data: {response_data}")


def write_done_event(event_id, file_path):
    """処理済みイベントIDをファイルに追記する"""
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"{event_id}\n")
            f.flush() # ファイルバッファをフラッシュして即時書き込み
    except IOError as e:
        print(f"Error writing to done events file {file_path}: {e}", file=sys.stderr)

# --- 新しく追加する関数 ---

def fetch_event_details_by_slug(tournament_slug, event_slug):
    """トーナメントとイベントのスラッグからイベント詳細を取得する"""
    query = get_event_details_by_tournament_query()
    variables = {"tournamentSlug": tournament_slug, "eventSlug": event_slug}
    try:
        response_data = fetch_data_with_retries(query, variables)
    except FetchError as e:
        print(f"Error fetching event details for {tournament_slug}/{event_slug}: {e}")
        return None # エラー時はNoneを返す

    # レスポンスデータの検証
    if (not response_data or "data" not in response_data or
        response_data["data"] is None or "tournament" not in response_data["data"] or
        response_data["data"]["tournament"] is None):
        print(f"Invalid response structure for event details: {tournament_slug}/{event_slug}. Response: {response_data}")
        return None
    
    tournament_data = response_data["data"]["tournament"]
    
    # events配列を確認
    if not tournament_data.get("events") or not tournament_data["events"]:
        print(f"No matching events found for {tournament_slug}/{event_slug}.")
        return None
    
    # 最初のイベントを使用（フィルタが正しく設定されていれば1つだけのはず）
    event_data = tournament_data["events"][0]
    
    # 必要な情報が揃っているか基本的なチェック
    if not all(k in event_data for k in ["id", "name", "isOnline", "startAt"]):
         print(f"Missing essential keys in event data for {tournament_slug}/{event_slug}.")
         return None
    
    # トーナメント情報とイベント情報を統合
    merged_data = {
        **event_data,
        "tournament": {
            "id": tournament_data.get("id"),
            "name": tournament_data.get("name"),
            "slug": tournament_data.get("slug"),
            "url": tournament_data.get("url"),
            "countryCode": tournament_data.get("countryCode"),
            "city": tournament_data.get("city"),
            "lat": tournament_data.get("lat"),
            "lng": tournament_data.get("lng"),
            "venueName": tournament_data.get("venueName"),
            "timezone": tournament_data.get("timezone"),
            "postalCode": tournament_data.get("postalCode"),
            "venueAddress": tournament_data.get("venueAddress"),
            "mapsPlaceId": tournament_data.get("mapsPlaceId")
        }
    }

    return merged_data # 統合されたデータを返す


def download_specific_event(tournament_slug, event_slug, startgg_dir, done_file_path, users_file_path, tournament_file_path, users, tournaments, done_events, openai_client, event_prompt):
    """指定された単一のイベントデータをダウンロードして保存する"""
    print(f"--- Processing event: {tournament_slug} / {event_slug} ---")

    # 1. イベント詳細を取得
    event_data = fetch_event_details_by_slug(tournament_slug, event_slug)
    if not event_data:
        print(f"Could not fetch details for event {tournament_slug}/{event_slug}. Skipping.")
        return False # 処理失敗

    # 2. 必要な情報を抽出
    try:
        event_id = event_data["id"]
        event_name = event_data["name"]
        is_online = event_data["isOnline"]
        timestamp = event_data["startAt"] # 開始時刻
        tournament_info = event_data["tournament"]
        tournament_id = tournament_info["id"]
        tournament_name = tournament_info["name"]
        tournament_url = tournament_info["url"] # トーナメント全体のURL

        # 場所情報 (存在しない場合もあるので .get() を使う)
        place = {
            "country_code": tournament_info.get("countryCode"),
            "city": tournament_info.get("city"),
            "lat": tournament_info.get("lat"),
            "lng": tournament_info.get("lng"),
            "venue_name": tournament_info.get("venueName"),
            "timezone": tournament_info.get("timezone"),
            "postal_code": tournament_info.get("postalCode"),
            "venue_address": tournament_info.get("venueAddress"),
            "maps_place_id": tournament_info.get("mapsPlaceId")
        }
        country_code = place.get("country_code", "") # ディレクトリ生成用に取得

    except KeyError as e:
        print(f"Missing expected key in event data for {tournament_slug}/{event_slug}: {e}. Skipping.")
        return False # 処理失敗

    # 3. 処理済みかチェック
    if event_id in done_events:
        print(f"Event ID {event_id} ({tournament_name} - {event_name}) already processed. Skipping.")
        return True # 既に処理済みなので成功扱い

    # 4. イベントデータ保存用ディレクトリを決定
    year, month, day = get_date_parts(timestamp)
    event_dir = get_event_directory(startgg_dir, country_code, year, month, day, tournament_name, event_name)
    print(f"Data will be saved to: {event_dir}")
    os.makedirs(event_dir, exist_ok=True) # ディレクトリ作成

    # 5. 各種データをダウンロード・保存
    try:
        # 5a. スタンディング (ユーザー情報と entrant->user マッピングも得る)
        user_data, player_data, entrant2user = download_standings(event_id, event_dir)
        if not entrant2user: # entrant2userが空なら、以降の処理が困難な場合がある
            print(f"Warning: No entrant-to-user mapping created for event {event_id}. Subsequent data might be incomplete.")
            # ここで処理を中断するかどうかは要件による
            # return False # 中断する場合

        num_entrants = len(user_data) # 参加者数
        print(f"Found {num_entrants} entrants for event {event_id}.")

        # 5b. シード (スタンディング後に実行し、ユーザー情報を更新する可能性あり)
        download_seeds(event_id, user_data, player_data, entrant2user, event_dir)

        # 5c. ユーザー情報を更新 (シードで追加されたユーザーも含む)
        extend_user_info(user_data, player_data, users, users_file_path)

        # 5d. 全セット (試合結果)
        download_all_set(event_id, entrant2user, event_dir)

        # 5e. イベント属性 (OpenAI分析含む)
        labels = []
        if openai_client and event_prompt:
             try:
                 labels = analyze_event_setting(openai_client, event_prompt, tournament_name, event_name, event_id)
                 print(f"OpenAI analysis labels: {labels}")
             except Exception as e:
                 print(f"Error during OpenAI analysis: {e}", file=sys.stderr)
                 labels = ["analysis_failed"] # エラーがあったことを示すラベル
        else:
             labels = ["analysis_skipped"] # スキップされたことを示すラベル

        write_event_attributes(num_entrants, event_id, event_name, tournament_name, timestamp, place, tournament_url, labels, is_online, event_dir)

        # 6. tournaments.jsonl を更新
        # トーナメントがまだ記録されていなければ追加、存在すればイベント情報を追加
        if tournament_id not in tournaments:
            tournaments[tournament_id] = {
                "tournament_id": tournament_id,
                "name": tournament_name,
                "events": []
            }
            # 新規トーナメントとしてファイルに追記
            tournament_entry = tournaments[tournament_id].copy() # コピーを作成
            tournament_entry["events"].append({
                 "event_id": event_id,
                 "event_name": event_name,
                 "path": event_dir
            })
            extend_tournament_info(tournament_entry, tournament_file_path)
        else:
            # 既存トーナメント情報にイベントを追加 (ファイル全体を書き換える必要があるため、ここでは追記しない)
            # 元のスクリプトの extend_tournament_info は追記のみなので、
            # 既存トーナメントにイベントを追加する場合は、ファイル読み込み->更新->書き込み直しが必要。
            # ここでは簡単化のため、新規トーナメントの場合のみファイル書き込みを行う。
            # 既存トーナメントへのイベント追加はメモリ上の辞書には反映される。
            # 必要であれば、全イベント処理後に tournaments 辞書全体をファイルに書き出す処理を追加する。
            tournaments[tournament_id]["events"].append({
                 "event_id": event_id,
                 "event_name": event_name,
                 "path": event_dir
            })
            print(f"Event {event_id} added to existing tournament {tournament_id} in memory.")
            # 注意: この変更は tournaments.jsonl には即時反映されません。

        # 7. 処理済みリストに追加・保存
        done_events.add(event_id)
        write_done_event(event_id, done_file_path)
        print(f"--- Successfully processed event: {tournament_slug} / {event_slug} (ID: {event_id}) ---")
        return True # 処理成功

    except (FetchError, NoPhaseError) as e:
        print(f"An error occurred processing event {event_id}: {e}", file=sys.stderr)
        return False # 処理失敗
    except Exception as e: # 予期せぬエラー
        print(f"An unexpected error occurred processing event {event_id}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False # 処理失敗


# --- メイン処理 ---
def main():
    # コマンドライン引数の設定 (元のスクリプトから流用・調整)
    parser = argparse.ArgumentParser(description="Download specific tournament event data from start.gg")
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha", help="API URL")
    parser.add_argument("--token", required=True, help="API token")
    # finish_date は特定イベントDLには不要だが、他の関数で使われる可能性を考慮し残すか削除
    # parser.add_argument("--finish_date", type=lambda s: datetime.strptime(s, '%Y-%m-%d'), default=datetime(2018, 1, 1), help="Finish date (not used for specific download)")
    parser.add_argument("--max_retries", type=int, default=10, help="Maximum number of retries for API requests") # デフォルト値を少し下げる
    parser.add_argument("--retry_delay", type=int, default=5, help="Delay between retries in seconds")
    parser.add_argument("--indent_num", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--startgg_dir", default="data/startgg/events", help="Directory to save event data")
    # 完了済みリストはイベント単位にする
    parser.add_argument("--done_file_path", default="data/startgg/done_events.csv", help="Path to the file recording completed event downloads")
    parser.add_argument("--users_file_path", default="data/startgg/users.jsonl", help="Path to the file recording startgg user info")
    parser.add_argument("--tournament_file_path", default="data/startgg/tournaments.jsonl", help="Path to the file recording tournament info")
    # game_id, country_code は特定イベントDLには直接不要
    # parser.add_argument("--game_id", default="1386", help="Game ID (not used for specific download)")
    # parser.add_argument("--country_code", default="", help="Country code (not used for specific download)")
    parser.add_argument("--event_prompt_file_path", default="scripts/event_analysis_prompt.txt", help="Path to the file containing event prompt")
    parser.add_argument("--openai_api_key", default="", help="OpenAI API key")
    args = parser.parse_args()

    # 設定値のセット
    set_indent_num(args.indent_num)
    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    # OpenAIクライアントの初期化
    openai_client = None
    event_prompt = None
    if args.openai_api_key:
        try:
            openai_client = OpenAI(api_key=args.openai_api_key)
            print("OpenAI API key is set.")
            # プロンプトファイルの読み込み
            if os.path.exists(args.event_prompt_file_path):
                 with open(args.event_prompt_file_path, "r", encoding="utf-8") as f:
                    event_prompt = f.read()
                 print(f"Loaded event prompt from {args.event_prompt_file_path}")
            else:
                 print(f"Warning: Event prompt file not found at {args.event_prompt_file_path}. OpenAI analysis might not work as expected.", file=sys.stderr)
                 event_prompt = None # ファイルがない場合はNoneにする
        except Exception as e:
            print(f"Failed to initialize OpenAI client: {e}", file=sys.stderr)
            openai_client = None # 初期化失敗
    else:
        print("OpenAI API key is not set. Event analysis will be skipped.")

    # 既存データの読み込み
    # 存在しない場合は空のデータで初期化
    if not os.path.exists(os.path.dirname(args.done_file_path)):
        os.makedirs(os.path.dirname(args.done_file_path), exist_ok=True)
    if not os.path.exists(os.path.dirname(args.users_file_path)):
        os.makedirs(os.path.dirname(args.users_file_path), exist_ok=True)
    if not os.path.exists(os.path.dirname(args.tournament_file_path)):
        os.makedirs(os.path.dirname(args.tournament_file_path), exist_ok=True)

    done_events = read_set(args.done_file_path, as_int=True)
    users = read_users_jsonl(args.users_file_path)
    tournaments = read_tournaments_jsonl(args.tournament_file_path) # tournament_id をキーとする辞書
    print(f"Loaded {len(done_events)} completed event IDs.")
    print(f"Loaded {len(users)} users.")
    print(f"Loaded {len(tournaments)} tournaments.")

    # ダウンロード対象のイベントリスト (tournament_slug, event_slug)
    target_events = [
        ("battle-of-bc-7-6", "main-event-ultimate-singles"),
        ("genesis-x2", "ultimate-singles"),
    ]

    # 各イベントを処理
    success_count = 0
    fail_count = 0
    for t_slug, e_slug in target_events:
        success = download_specific_event(
            t_slug, e_slug,
            args.startgg_dir, args.done_file_path, args.users_file_path, args.tournament_file_path,
            users, tournaments, done_events,
            openai_client, event_prompt
        )
        if success:
            success_count += 1
        else:
            fail_count += 1

    print("\n--- Download Summary ---")
    print(f"Successfully processed: {success_count} events")
    print(f"Failed or skipped: {fail_count} events")
    # 注意: tournaments.jsonl の更新は、新規トーナメントの場合のみ追記されます。
    # 既存トーナメントへのイベント追加はメモリ上で行われ、ファイルには反映されません。
    # 必要に応じて、最後に tournaments 辞書全体をファイルに上書き保存する処理を追加してください。
    # 例: write_jsonl(tournaments.values(), args.tournament_file_path, with_version=True) のような関数を utils に用意するなど。

if __name__ == "__main__":
    main()
