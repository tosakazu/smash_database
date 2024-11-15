import time
import os
import json
import csv

# 国コードをリージョンに変換する関数
def country_code2region(country_code):
    japan = ["JP"]
    other_asia = ["CN", "KR", "IN", "SG", "TH", "MY", "PH", "VN", "ID"]
    europe = ["FR", "DE", "GB", "IT", "ES", "RU", "NL", "SE", "CH", "BE"]
    north_america = ["US", "CA", "DO", "MX"]  # 北アメリカの国コード
    south_america = ["BR", "AR", "CL", "CO", "PE", "VE", "UY", "EC", "BO"]

    if country_code in japan:
        return "Japan"
    elif country_code in other_asia:
        return "Other Asia"
    elif country_code in europe:
        return "Europe"
    elif country_code in north_america:
        return "North America"
    elif country_code in south_america:
        return "South America"
    else:
        return "Other"


# UltimateのSinglesイベントのみをフィタリングするための関数
def is_ultimate_singles(event_name):
    # 除外するキワード
    exclude_keywords = ["64", "Melee", "for", "ダブルス", "Doubles", "Crew_Battle", "Squad_Strike", "団体戦", "おまかせ", "おかわり", "Granblue", "Guilty_Gear", "Redemption", "Rivals_of_Aether", "Dobles"]
    
    # 除外キーワードが含まれている場合はFalseを返す
    if any(keyword.replace(" ", "_").lower() in event_name.replace(" ", "_").lower() for keyword in exclude_keywords):
        return False
    
    return True

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


__indent_num = 2
def write_json(data, file_path):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=__indent_num)

def set_indent_num(num):
    global __indent_num
    __indent_num = num

def load_users_json(file_path):
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r") as f:
        data = json.load(f)["data"]
        return {user["user_id"]: user for user in data}

def load_csv(file_path):
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
    
def load_set(file_path):
    if not os.path.exists(file_path):
        return set()
    with open(file_path, "r") as f:
        return set(line.strip() for line in f)
    

# プレイヤー情報を保存す関数
def write_users(users, file_path):
    write_json({"version": "1.0", "data": list(users.values())}, file_path)

# トーナメント情報を保存する関数
def write_tournaments(tournaments, file_path):
    with open(file_path, "w", newline='') as f:
        writer = csv.writer(f)
        for tournament_id, tournament_name in tournaments.items():
            writer.writerow([tournament_id, tournament_name])

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
