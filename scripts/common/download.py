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

# 下位クラス bracket: Bクラス/Cクラス/Bclass/B_Class/b_class 等.
# start.gg で videogame タグが未設定の side event を救うためのフォールバック.
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
# v2 (refetch) の fetch / write logic を流用して新規 daily download でも同一スキーマを生成.
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
    # YYYY-MM-DD は終日扱い (=23:59:59) にして start_date 比較で当日分も含めるようにする.
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
    # コマンドライン引数の設定
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
    parser.add_argument("--startgg-dir", default="data/startgg", help="データルート。イベントは <root>/<地域>/events/<年>/<月>/<日>/... に保存する")
    parser.add_argument("--done-file-path", default=None, help="完了大会の記録 (既定: data/startgg/<地域>/done.csv。地域は --country-code から)")
    parser.add_argument("--users-file-path", default=None, help="ユーザー情報 (既定: data/startgg/<地域>/users.jsonl)")
    parser.add_argument("--tournament-file-path", default=None, help="大会情報 (既定: data/startgg/<地域>/tournaments.jsonl)")
    parser.add_argument("--game-id", default="1386", help="Game ID for tournament retrieval. see https://developer.start.gg/docs/examples/queries/videogame-id-by-name/")
    parser.add_argument("--country-code", default="", help="Country code for tournament retrieval. e.g. JP")
    parser.add_argument("--awaiting-file", default=None,
                        help="再開待ち登録簿 (build/data/awaiting_resume.json)。載っている event は優勝者が出るまで "
                             "7 日窓に関係なく毎回取り直し、done にしない。無指定/不在なら従来どおり。")
    args = parser.parse_args()
    setup_api(args)
    # index (done / users / tournaments) は地域ごとに data/startgg/<地域>/ に置く (2026-09-07〜。データは地域ブランチで管理する)
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


# ── 終了直後の取り直し ──
# 主催者は大会終了後にスコアや順位を直すことがある (2026-08-15 第19回グランドスラムで発覚).
# 一度 done にすると二度と取り直さないので、修正が永久に反映されなくなる。
# そこで「終了から N 日以内」は既取得でも取り直す。ただし 3 時間ごとの nightly で
# 毎回取り直すと API 呼び出しが跳ねるので、event ごとに最短間隔を設ける
# (前回取得時刻 = attr.json の mtime。専用の状態ファイルは持たない)。
RECENT_REFRESH_DAYS = float(os.environ.get("SPSP_REFRESH_DAYS", "3"))
RECENT_REFRESH_MIN_HOURS = float(os.environ.get("SPSP_REFRESH_MIN_HOURS", "12"))


# ── 再開待ち (awaiting resume) ──
# 延期・中断で後日再開する大会。優勝者が出るまで 7 日窓に関係なく毎回取り直し、done にしない
# (= 途中結果を build 側が暫定集計する。登録簿は build/data/awaiting_resume.json、--awaiting-file で渡す)。
_AWAITING_RESUME_IDS: set = set()


def set_awaiting_resume_ids(ids):
    global _AWAITING_RESUME_IDS
    _AWAITING_RESUME_IDS = set(int(x) for x in (ids or ()))


def load_awaiting_resume_file(path):
    """登録簿 JSON → event_id 集合。path 無指定/不在なら空。壊れていれば例外 (黙って無視しない)。"""
    if not path or not os.path.exists(path):
        return set()
    with open(path, "rb") as f:
        d = json.loads(f.read())
    events = d.get("events") if isinstance(d, dict) else d
    if not isinstance(events, list):
        raise ValueError(f"{path}: 'events' が配列でない")
    out = set()
    for e in events:
        eid = e.get("event_id") if isinstance(e, dict) else e
        if not isinstance(eid, int):
            raise ValueError(f"{path}: event_id が整数でない: {e!r}")
        out.add(eid)
    return out


def is_awaiting_resume(event_id):
    return event_id is not None and int(event_id) in _AWAITING_RESUME_IDS


def event_files_complete(event_dir):
    return all(os.path.exists(os.path.join(event_dir, name)) for name in REQUIRED_EVENT_FILES)


def _event_needs_recent_refresh(event_dir, ref_end_ts, now_timestamp):
    """終了 N 日以内 かつ 前回取得から MIN_HOURS 以上経っていれば True."""
    if RECENT_REFRESH_DAYS <= 0 or not ref_end_ts or not event_dir:
        return False
    if (now_timestamp - ref_end_ts) > RECENT_REFRESH_DAYS * 86400:
        return False
    attr = os.path.join(event_dir, "attr.json")
    try:
        last = os.path.getmtime(attr)
    except OSError:
        return True          # 未取得 / 壊れている → 取り直す
    return (now_timestamp - last) >= RECENT_REFRESH_MIN_HOURS * 3600


def _tournament_needs_recent_refresh(tournament_entry, ref_end_ts, now_timestamp):
    """done 済みでも終了直後の取り直し対象か (event のどれか 1 つでも該当すれば True)."""
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
    # 既存 user の情報が更新された id を集める. 最後にまとめて users.jsonl を書き戻す.
    dirty_user_ids = set()
    existing_tournament_ids = set(tournaments.keys())
    # Index of event_id -> (tournament_id, old_path) for detecting date-change duplicates.
    event_id_index = _build_event_id_index(tournaments)

    page = 1
    while True:
        try:
            tournaments_info, total_pages = fetch_latest_tournaments_by_game(game_id, country_code=country_code, limit=TOURNAMENTS_PER_PAGE, page=page)
        except FetchError as e:
            # 以前は print して continue していたが page を進めないので同じページを永遠に再試行していた
            # (fetch 側で既に max_retries 回試している)。止めて nightly に失敗を伝える。
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

                now_timestamp = clock.now_ts()   # 「今」は scripts.common.clock (大会ごとに再評価する挙動は維持)
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
                    # done 済み & ファイル完備でも、終了 7 日以内で優勝者未確定の
                    # event を含む場合は skip せず取り直す (= champion_missing retry の
                    # 実体. これが無いと下の per-event retry は永久に到達しない).
                    _champ_retry = _tournament_needs_champion_retry(
                        tournament_entry, end_timestamp or timestamp, now_timestamp)
                    # 終了直後 (RECENT_REFRESH_DAYS 以内) は修正が入りうるので取り直す
                    _recent_refresh = _tournament_needs_recent_refresh(
                        tournament_entry, end_timestamp or timestamp, now_timestamp)
                    if _recent_refresh:
                        print(f"({tournament_name} {datetime.fromtimestamp(timestamp)}) "
                              f"は終了から {RECENT_REFRESH_DAYS:g} 日以内 — 取り直します")
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

                # 窓の下限判定は endAt 基準 (= 列挙の sortBy "endAt desc" と対応).
                # startAt 基準だと、TO が startAt を告知日に設定した大会 (船スマ) や
                # 開催期間の長い大会が「開催完了時には窓外」になり永久に取得漏れする。
                eff_end_dt = datetime.fromtimestamp(end_timestamp or timestamp)
                if eff_end_dt < finish_date:
                    if end_timestamp is None:
                        # endAt 未設定の大会は endAt desc ソート上の位置が保証されない
                        # ため、打ち切らず個別 skip に留める。
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
                                # Same path, files present. 取り直すかは download_policy.existing_event_action:
                                # 終了直後の取り直し窓なら優勝者が居ても取り直す (主催者の修正を拾う)。
                                # 優勝者が居なければ終了 7 日以内 (か再開待ち登録) に限り取り直す。
                                _ref_end2 = end_timestamp or timestamp
                                _needs_refresh = _event_needs_recent_refresh(old_path, _ref_end2, now_timestamp)
                                _has_champ = None if _needs_refresh else _standings_has_champion(old_path)
                                _act, _why = existing_event_action(True, _needs_refresh, _has_champ, _ref_end2,
                                                                   now_timestamp, is_awaiting_resume(event_id))
                                if _act == "skip":
                                    continue
                                if _why == "recent refresh":
                                    print(f"  [refresh] {event_name}: 終了 {RECENT_REFRESH_DAYS:g} 日以内 — 再取得")
                                elif _why == "awaiting resume":
                                    print(f"  [awaiting] {event_name}: 再開待ち登録 — 優勝者が出るまで取り直し")

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

                        # 優勝者 (placement=1) が無い standings は「結果未確定スナップショット」
                        # の可能性が高い (= 主催者が決勝の入力を後日行うケース. 兵庫対戦会#31 で発覚.
                        # 試合はあるのに standings が空のまま、も同類. Badawi#5 で発覚).
                        # 大会終了から 7 日間だけ done にせず毎晩取り直す. 窓を過ぎたら
                        # 未完のまま done マーク (= 以後ダウンロードしない).
                        _cm = champion_missing_reason(_standings_has_champion(event_dir), is_awaiting_resume(event_id),
                                                      end_timestamp or timestamp, now_timestamp)
                        if _cm == "awaiting":
                            champion_missing = True
                            print(f"  [awaiting] {event_name}: 再開待ち — 優勝者が出るまで done にしない")
                        elif _cm == "pending":
                            champion_missing = True
                            print(f"  [pending] {event_name}: standings に優勝者なし — 7日間は再取得対象に残す")

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

    # 既存 user の更新があれば users.jsonl を書き戻し
    _flush_dirty_users(users, dirty_user_ids, users_file_path)


def _flush_dirty_users(users, dirty_user_ids, users_file_path):
    """既存 user で API 反映で更新された記録があれば users.jsonl 全体を書き直す.
    新規 user は extend_jsonl で append 済なので、ここでは全件 write_jsonl で上書き保存し直す.
    """
    if not dirty_user_ids:
        return
    print(f"[users] {len(dirty_user_ids)} existing users updated, rewriting {users_file_path}", flush=True)
    write_jsonl(list(users.values()), users_file_path, with_version=True)

# イベントのセットデータを保存する関数
def _standings_has_champion(event_dir):
    """standings.json に placement=1 が居るか. 読めない場合は True (= 問題なし扱い)."""
    try:
        with open(os.path.join(event_dir, "standings.json"), "rb") as f:
            rows = json.loads(f.read()).get("data") or []
        if not rows:
            # standings が完全に空: 試合データが付いているなら「大会当日の未確定
            # スナップショット」(Badawi#5 で発覚。翌日以降に standings が確定する) と
            # みなし champion 無し扱い → 7 日窓の再取得対象に載せる。
            # 試合ゼロ (不成立/登録のみ) は従来どおり問題なし扱いで retry しない。
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
    """done 済みでも再取得すべきか. 終了 7 日以内 & 優勝者未確定の event を含むなら True.

    download.py の outer done-skip は file 完備のみ判定するため、これが無いと
    per-event の champion_missing retry (= standings に place=1 が出るまで取り直す)
    に永久に到達しない. 7 日超は「未完として確定」とみなし再取得しない.
    """
    if not tournament_entry:
        return False
    in_window = bool(ref_end_ts) and (now_timestamp - ref_end_ts) < 7 * 86400
    for event in tournament_entry.get("events", []):
        ed = event.get("path")
        if not (ed and event_files_complete(ed)) or _standings_has_champion(ed):
            continue
        # 7 日窓内、または再開待ち登録 (窓に関係なく優勝者が出るまで) なら取り直す
        if in_window or is_awaiting_resume(event.get("event_id")):
            return True
    return False


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
                "startAt": pg.get("startAt"),  # phase_group 開始予定時刻 (Unix timestamp)
                "wave": pg.get("wave"),  # { id, identifier, startAt }
            }
            try:
                # with_games=True: スコア + キャラ/ステージ選択を 1 パスで取得し、
                # matches.json の details にキャラ情報も書き込む (= 別途 fetch_character_games で
                # 同じ sets を二重取得していたのを統合).
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
            _time.sleep(_V2_API_DELAY_SEC)  # 各 phase_group 間で rate-limit 緩和 (v2 と同じ).
    if pg_failures:
        # silent partial の matches.json を書いて done マークされると二度と再取得されない
        # (= 兵庫対戦会#31 で発覚). fail させて caller 側で retry 対象に残す.
        raise FetchError(
            f"[fetch_all_sets] event={event_id}: {len(pg_failures)} phase_groups failed: {pg_failures}")
    return all_sets_with_phase


# startAt 誤設定補正の発動閾値 (日数). 深夜跨ぎや前日 DQ 報告程度では発動せず、
# 告知/登録開始日を startAt にしてしまった大会 (例: 船スマ 7/19 開催分 = startAt 6/8,
# 41 日ずれ) だけを補正する。multi-day 大会は day1 の set が startAt 当日にあるので発動しない。
DATE_CORRECTION_MIN_GAP_DAYS = 3


def corrected_event_window(timestamp, end_timestamp, set_times):
    """set の実時刻から event の開催 window を導出し、startAt 誤設定を補正する.

    set_times: 有効な set (非 DQ / 非 cancel) の started_at / completed_at (Unix ts) リスト.
    返り値: (timestamp, end_timestamp, corrected: bool).

    補正条件 (いずれも DATE_CORRECTION_MIN_GAP_DAYS 日超のずれのみ):
      - startAt が実際の最初の set より大幅に古い (= 告知/登録開始日を設定したケース)
      - startAt が実際の最後の set より大幅に新しい (= 未来のプレースホルダを設定したケース)
    補正時は (min(set_times), max(set_times)) を採用する。
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
    """matches.json から有効 set (非 DQ / 非 cancel) の実時刻リストを読む."""
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
    # startAt 誤設定補正: set の実時刻と大幅にずれる場合のみ、set 時刻由来の window に
    # 差し替える。元の start.gg 値は startgg_start_at / startgg_end_at として保持し、
    # date_corrected_by で補正の事実を明示する (= サイレント書き換えにしない)。
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
              f"(set 実時刻より補正)", flush=True)
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

def extend_user_info(user_data, player_data, users, users_file_path, dirty_user_ids=None):
    """新規 user は users.jsonl に追記、既存 user は差分があれば in-memory 更新.

    dirty_user_ids: 既存 user が更新されたら id を追加する set (= caller がまとめて
        最後に write_jsonl で全件書き戻すために使う).
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
            # 既存 user の差分検出. API 値が None (= 未取得 / 削除) でも上書き反映
            # (= start.gg 側の最新状態を信用する).
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
