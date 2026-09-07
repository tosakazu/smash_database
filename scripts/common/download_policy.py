"""download_policy — download.py の「取るか・飛ばすか・done にするか」の判定 (副作用なし)。

download.py の大会ループは API 呼び出し・ファイル移動・書き込みと判定が絡み合っていて読みにくく、
判定の分岐を単体で試せなかった。ここに判定だけを切り出す (入力は事実、出力は行動と理由)。
挙動は切り出し前と同じ (tests/fetch/test_dl_policy.py に判定表、tests/fetch/dl_golden.py に工程全体の再生比較)。
"""
from __future__ import annotations

RETRY_WINDOW_SEC = 7 * 86400   # 優勝者未確定の大会を取り直し続ける期間 (終了から 7 日)


def tournament_skip_reason(tournament_state, end_timestamp, timestamp, now_ts, start_date_ts):
    """大会一覧の段階で飛ばす理由。None なら処理する。
    - state == 3 (COMPLETED) なら endAt 前でも結果は確定済みなので取り込む
    - start_date (取得窓の上限) より新しい大会は飛ばす
    """
    if tournament_state != 3 and (end_timestamp is None or end_timestamp > now_ts):
        return "not_finished"
    if start_date_ts is not None and timestamp > start_date_ts:
        return "newer_than_start_date"
    return None


def done_tournament_action(entry, events_complete, champ_retry, recent_refresh):
    """done.csv に載っている大会をどうするか。
    返り値: ("skip", 理由) = 取り直さない (ディレクトリの移動だけ確かめる) / ("redownload", 理由)
    """
    if entry and events_complete and not champ_retry and not recent_refresh:
        return "skip", "already downloaded"
    if champ_retry:
        return "redownload", "marked done but champion missing within 7d"
    if recent_refresh:
        return "redownload", "is marked done but recent refresh window"
    return "redownload", "is marked done but files are missing"


def plan_event_moves(entry, new_path_for):
    """done 済み大会の event が古い日付のディレクトリにあれば移動先を返す: [(event, old_path, new_path)]。
    new_path_for(event) は今の日付でのディレクトリ名。old_path が実在するかは呼び出し側が確かめる。"""
    moves = []
    for ev in (entry or {}).get("events", []):
        old_path = ev.get("path", "")
        new_path = new_path_for(ev)
        if old_path and new_path and old_path != new_path:
            moves.append((ev, old_path, new_path))
    return moves


def existing_event_action(files_complete, needs_recent_refresh, has_champion, ref_end_ts, now_ts, awaiting):
    """同じパスに event のファイルが揃っているときに取り直すか。
    返り値: ("skip", 理由) / ("download", 理由)。has_champion は needs_recent_refresh のとき見ない。
    """
    if not files_complete:
        return "download", "files missing"
    if needs_recent_refresh:
        return "download", "recent refresh"
    if has_champion:
        return "skip", "champion present"
    in_retry_window = bool(ref_end_ts) and (now_ts - ref_end_ts) < RETRY_WINDOW_SEC
    if not in_retry_window and not awaiting:
        return "skip", "no champion but retry window passed"
    if awaiting:
        return "download", "awaiting resume"
    return "download", "no champion within retry window"


def champion_missing_reason(has_champion, awaiting, ref_end_ts, now_ts):
    """ダウンロード後、done にせず取り直し対象に残す理由 (None = 残さない)。"""
    if has_champion:
        return None
    if awaiting:
        return "awaiting"
    if ref_end_ts and (now_ts - ref_end_ts) < RETRY_WINDOW_SEC:
        return "pending"
    return None
