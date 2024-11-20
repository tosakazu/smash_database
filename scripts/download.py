import time
import os
import json
import csv
import sys
import requests

# 国コードをリージョンに変換する関数
def country_code2region(country_code):
    japan = ["JP"]
    other_asia = ["CN", "KR", "IN", "SG", "TH", "MY", "PH", "VN", "ID"]
    europe = ["FR", "DE", "GB", "IT", "ES", "RU", "NL", "SE", "CH", "BE"]
    north_america = ["US", "CA", "DO", "MX"]

    if country_code in japan:
        return "Japan"
    elif country_code in other_asia:
        return "Other Asia"
    elif country_code in europe:
        return "Europe"
    elif country_code in north_america:
        return "North America"
    else:
        return "Other"


# UltimateのSinglesイベントのみをフィタリングするための関数
def is_not_ultimate_singles(event_name):
    # 除外するキワード
    exclude_keywords = ["64", "Melee", "WiiU", "ダブルス", "チーム", "Team", "Doubles", "Crew_Battle", "Squad_Strike", "団体戦", "おまかせ", "おかわり", "Granblue", "Guilty_Gear", "Redemption", "Rivals_of_Aether", "Dobles"]
    
    # 除外キーワードが含まれている場合はFalseを返す
    if any(keyword.replace(" ", "_").lower() in event_name.replace(" ", "_").lower() for keyword in exclude_keywords):
        return True
    
    return False

def get_date_parts(date):
    """日付を年、月、日に分割する関数"""
    year = time.strftime("%Y", time.gmtime(date))
    month = time.strftime("%m", time.gmtime(date))
    day = time.strftime("%d", time.gmtime(date))
    return year, month, day

def get_event_directory(startgg_dir, region, year, month, day, tournament_name, event_name):
    """保存するディレクトリのパスを取得する関数"""
    region = country_code2region(region)
    region = region.replace(" ", "_").replace("/", "-")
    tournament_name = tournament_name.replace(" ", "_").replace("/", "-")
    event_name = event_name.replace(" ", "_").replace("/", "-")
    return f"{startgg_dir}/{region}/{year}/{month}/{day}/{tournament_name}/{event_name}"


JSON_VERSION = "1.0"
__indent_num = 2

def write_json(data, file_path, with_version):
    with open(file_path, "w", encoding="utf-8") as f:
        if with_version:
            data["version"] = JSON_VERSION
        json.dump(data, f, indent=__indent_num, ensure_ascii=False)

def read_json(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_jsonl(data, file_path, with_version):
    with open(file_path, "w", encoding="utf-8") as f:
        for d in data:
            if with_version:
                d["version"] = JSON_VERSION
            json.dump(d, f, ensure_ascii=False)
            f.write("\n")

def extend_jsonl(data, file_path, with_version):
    with open(file_path, "a", encoding="utf-8") as f:
        for d in data:
            if with_version:
                d["version"] = JSON_VERSION
            json.dump(d, f, ensure_ascii=False)
            f.write("\n")

def read_jsonl(file_path):
    output = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                output.append(json.loads(line))
    return output

def set_indent_num(num):
    global __indent_num
    __indent_num = num

def read_users_jsonl(file_path):
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        users = {}
        for line in f:
            user = json.loads(line)
            del user["version"]
            users[user["user_id"]] = user
    return users
    
def read_tournaments_jsonl(file_path):
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        tournaments = {}
        for line in f:
            tournament = json.loads(line)
            del tournament["version"]
            tournaments[tournament["tournament_id"]] = tournament
    return tournaments

def read_csv(file_path):
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r") as f:
        data = {}
        for row in csv.reader(f):
            id = row[0]
            if len(row) == 2:
                data[id] = row[1]
            else:
                data[id] = tuple(row[1:])
        return data
    
def read_set(file_path):
    if not os.path.exists(file_path):
        return set()
    with open(file_path, "r") as f:
        return set(line.strip() for line in f)

# イベントパス情報を保存する関数
def write_event_paths(event_paths, file_path):
    with open(file_path, "w", newline='') as f:
        writer = csv.writer(f)
        for event_id, (date, path) in event_paths.items():
            writer.writerow([event_id, date, path])

# IDパス情報を保存する関数
def write_id_paths(id_paths, file_path):
    # ディレクトリが存在しない場合は作成
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", newline='') as f:
        writer = csv.writer(f)
        for id, path in id_paths.items():
            writer.writerow([id, path])
            
class FetchError(Exception):
    def __init__(self, message):
        super().__init__(message)
        print(message, file=sys.stderr)

class NoPhaseError(Exception):
    def __init__(self, message):
        super().__init__(message)

__max_retries = 100
__retry_delay = 5
__page_delay = 2
__api_url = "https://api.start.gg/gql/alpha"
__headers = {}

def set_page_delay(delay):
    global __page_delay
    __page_delay = delay

def set_retry_parameters(max_retries, retry_delay):
    global __max_retries, __retry_delay
    __max_retries = max_retries
    __retry_delay = retry_delay

def set_api_parameters(url, token):
    global __api_url, __headers
    __api_url = url
    __headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

def fetch_data_with_retries(query, variables):
    for attempt in range(__max_retries):
        try:
            response = requests.post(__api_url, json={"query": query, "variables": json.dumps(variables)}, headers=__headers)
            response.raise_for_status()
            response_data = json.loads(response.text)
            return response_data
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            print(query)
            print(variables)
            print(f"Request or JSON parsing failed: {e}. Retrying {attempt + 1}/{__max_retries}...")
            time.sleep(__retry_delay)
    raise Exception("Max retries exceeded")

def fetch_all_nodes(query, variables, keys, per_page=10):
    all_nodes = []
    variables = variables.copy()
    variables["page"] = 1
    variables["perPage"] = per_page
    keys = ["data"] + keys
    while True:
        response_data = fetch_data_with_retries(query, variables)
        data = response_data
        for key in keys:
            if key not in data:
                raise FetchError(f"Error: '{key}' key not found in response. Query: {query}\nVariables: {variables}\nKeys: {keys}\nResponse data: {response_data}\n in fetch_all_nodes")
            data = data[key]
        if "nodes" not in data:
            return all_nodes
        nodes = data["nodes"]
        all_nodes.extend(nodes)
        if len(nodes) > 0:
            variables["page"] += 1
            time.sleep(__page_delay)
        else:
            break
    return all_nodes
