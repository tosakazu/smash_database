#!/usr/bin/env python3
"""Re-download matches.json using phase_group iteration (instead of event-level pagination).

問題: 既存の event-level pagination (fetch_all_nodes) は AIMD overlap-skip が
legitimate な sets を捨てる可能性があり、大規模 event で取りこぼしが発生.
篝火#15 Yuzha の例: start.gg では 9 試合あるが我々のデータでは 6 試合しか取れていない.

新方式 (v2):
  1. event の phases 一覧を取得
  2. 各 phase の phase_groups 一覧を取得
  3. 各 phase_group ごとに sets を fetch (phase_group 単位は小さいので 1-2 ページで完結する場合が多い)
  4. set ごとに phase_id, phase_name, phase_num_seeds, phase_group_id, wave_id を付与
  5. matches.json に追加: phase_id, phase_name, phase_num_seeds, wave_id

Usage:
    python3 scripts/fetch/redownload_matches_v2.py --token <T> --dup-list /tmp/all_events_to_refetch.json --min-dups 0
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.queries import (
    get_event_phases_full_query, get_phase_group_sets_full_query,
    get_phase_group_sets_full_with_games_query,
)
from scripts.utils import (
    fetch_data_with_retries, fetch_all_nodes, set_retry_parameters, set_api_parameters,
    FetchError,
)

# Inter-call delay to avoid rate limiting (start.gg は 1-2 RPS 程度推奨)
API_DELAY_SEC = 0.6


# Phase name から TOP X を抽出 (= bracket size)
def parse_phase_top_n(name: str) -> int | None:
    if not name:
        return None
    m = re.search(r'TOP\s*(\d+)', name, re.IGNORECASE)
    if m:
        try: return int(m.group(1))
        except: pass
    return None


# DE bracket における W2W (Wins-to-Win) → placement bucket upper bound (= TOP X) のテーブル.
# 例: W2W=2 (LB Final 敗者=3位) → TOP 3, W2W=5 → TOP 8, W2W=23 → TOP 4096.
# 算式:
#   W2W=2k (偶数) → TOP = 3 × 2^(k-1)   (例: w2w=4 → k=2 → 6 = 5-6 上限)
#   W2W=2k+1 (奇数) → TOP = 2^(k+1)     (例: w2w=5 → k=2 → 8 = 7-8 上限)
def w2w_to_top_x(w2w: int) -> int:
    if w2w <= 0:
        return 1
    if w2w == 1:
        return 2
    k = w2w // 2
    if w2w % 2 == 0:
        return 3 * (2 ** (k - 1))
    return 2 ** (k + 1)


def winners_top_x(round_n: int, phase_top_n: int) -> int:
    """Winners side round で「この試合に負けたら下にいく」placement bucket の上限."""
    # WB R r で敗北 → LB へ. LB R1 (= WB R r-1 losers が落ちてくる) でさらに負けると
    # placement: TOP {N / 2^(r-1)} のバケット. 即ち WB 段階での「TOP X」境界.
    if round_n <= 0 or phase_top_n is None or phase_top_n <= 0:
        return None
    return max(2, phase_top_n // (2 ** max(0, round_n - 1)))


def losers_top_x(round_n: int) -> int:
    """Losers side round で敗北したときの最終 placement bucket 上限 (= TOP X).
    start.gg の round 表記: round=-1 → LB Final, -2 → LB Semi, ... と LB final から離れるほど大きな絶対値.
    """
    if round_n >= 0:
        return None
    # LB Round (start.gg, |round|=k) の敗者 W2W = k + 1
    # 例: LB Final (k=1) 敗者 = 3位 (W2W=2)
    w2w = abs(round_n) + 1
    return w2w_to_top_x(w2w)


def next_pow2(n: int) -> int:
    if n is None or n <= 1: return 1
    p = 1
    while p < n: p *= 2
    return p


def effective_bracket_capacity(n: int) -> int:
    """play-in 補正後の有効 bracket 容量 = 最大の pow2 で <= n.
    例: n=64 → 64 (= そのまま), n=69 → 64, n=128 → 128, n=192 → 128.
    DE bracket で n が pow2 でない場合、余剰 (n - prev_pow2) が R1 play-in に吸収され、
    R2 以降の effective 構造は prev_pow2 名 SE と同じ. ラベル付けはこの effective 容量を使う.
    """
    if n is None or n <= 1: return 1
    np = next_pow2(n)
    if np == n: return n      # n is already pow2
    return np // 2            # n 未満の最大 pow2


# クラス phase (B/C/D/E-class) 判定 — main bracket と分離するためのフィルタ.
# A-class は最上位ブラケット (= TO WIN 系) で main 扱いするので除外しない.
# English ("B class" / "B-class" / "BClass") + Japanese ("Bクラス") 両対応.
_CLASS_PHASE_RE = re.compile(r'\b[B-E][- ]?class\b|[B-EＢＣＤＥ][- ]?クラス', re.IGNORECASE)
def _is_class_phase(phase_name: str) -> bool:
    return bool(phase_name and _CLASS_PHASE_RE.search(phase_name))


# 全角→半角 マップ (= "Ｂ" → "B" 等)
_FULLWIDTH_TO_ASCII = str.maketrans('ＡＢＣＤＥ', 'ABCDE')

def _get_class_letter(phase_name: str) -> str | None:
    """class phase の letter (= 'B'/'C'/'D'/'E') を返す. 該当なし None."""
    if not phase_name: return None
    m = _CLASS_PHASE_RE.search(phase_name)
    if not m: return None
    # m.group(0) は "B class" / "b-class" / "Bクラス" / "Ｂクラス" 等. 先頭の letter を半角大文字に.
    first = m.group(0)[0]
    return first.translate(_FULLWIDTH_TO_ASCII).upper()


def placement_to_bucket(p: int) -> int:
    """Standard DE placement bucket upper edge.
    Sequence: 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, ...
    """
    if p is None or p <= 0: return None
    if p <= 4: return p
    k = 1
    while True:
        b1 = 3 * (2 ** k)        # 6, 12, 24, 48, 96, 192, ...
        b2 = 2 ** (k + 2)        # 8, 16, 32, 64, 128, 256, ...
        if p <= b1: return b1
        if p <= b2: return b2
        k += 1
        if k > 20: return None   # safety


def compute_phase_global_rounds(all_sets_with_phase):
    """Main phase (= non-class) について global round 番号を計算する.

    各 phase の WB rounds を列挙、play-in 判定 (= 試合数が次の round より少ない) して除外、
    残った effective rounds を phaseOrder 順に並べて累積した index を global_round とする.

    SE phase は WB-only (= LB なし) として WB と同じ扱い.
    ROUND_ROBIN / SWISS / MATCHMAKING / CUSTOM_SCHEDULE は bracket-position 概念無しで skip.

    Returns:
        phase_info: dict[phase_id] = {
            'phase_order': int,
            'all_wb_rounds_sorted': list,
            'effective_wb_rounds_sorted': list,
            'play_in_rounds': set,
            'global_round_offset': int (累積 offset),
            'is_se': bool (= SE phase か),
        }
        max_main_phase_order: int (LB の "final phase" 判定用)
    """
    by_phase: dict = {}
    # main + class 両方の phase を収集. main は global_round 累積, class は自己完結 (= prefix label のみ).
    for set_node, phase_info, _ in all_sets_with_phase:
        pname = phase_info.get('name') or ''
        bt = phase_info.get('bracketType')
        # DE と SE のみ bracket-position 概念あり (= SE は WB-only として扱う).
        if bt and bt not in ('DOUBLE_ELIMINATION', 'SINGLE_ELIMINATION'):
            continue
        pid = phase_info.get('id')
        if pid is None:
            continue
        r = set_node.get('round')
        if r is None or r <= 0:
            continue
        class_letter = _get_class_letter(pname)
        by_phase.setdefault(pid, {
            'phase_order': phase_info.get('phaseOrder') or 0,
            'wb_round_counts': {},
            'is_se': (bt == 'SINGLE_ELIMINATION'),
            'class_letter': class_letter,
            'num_seeds': phase_info.get('numSeeds') or 0,
        })
        by_phase[pid]['wb_round_counts'][r] = by_phase[pid]['wb_round_counts'].get(r, 0) + 1
    # main bracket は phase_order 順に global_round 累積. class bracket は自己完結 (= offset 0).
    sorted_pids = sorted(by_phase.keys(), key=lambda pid: (by_phase[pid]['phase_order'], pid))
    out = {}
    cumulative_main = 0  # class 以外の累積
    for pid in sorted_pids:
        info = by_phase[pid]
        all_rounds = sorted(info['wb_round_counts'].keys())
        play_in = set()
        # play-in 検出: 連続する先頭の round が後続 round の自然な doubling pattern (= R[i+1]*2)
        # に乗らない場合 play-in 扱い.
        # 例:
        #   - 759名 SE: R1=247, R2=256. R1 < R2*2=512 → play-in.
        #   - 413名 SE: R1=157, R2=128. R1 < R2*2=256 → play-in.
        #   - 24seed A class: R1=4, R2=2. R1 = R2*2=4 → play-in でない.
        #   - 32seed SE: R1=16, R2=8. R1 = R2*2=16 → play-in でない.
        # numSeeds は信用できない (= 同一 phase でも phase_groups 間で不整合あり) ので使わない.
        for i in range(len(all_rounds) - 1):
            cur = all_rounds[i]; nxt = all_rounds[i + 1]
            cur_n = info['wb_round_counts'][cur]
            nxt_n = info['wb_round_counts'][nxt]
            # 後続 round の自然な doubling パターンと一致しなければ play-in
            if cur_n != nxt_n * 2:
                play_in.add(cur)
            else:
                break
        effective = [r for r in all_rounds if r not in play_in]
        is_class = info.get('class_letter') is not None
        # class bracket は global_round 累積に含めない (= 各 class が独立した bracket)
        offset = 0 if is_class else cumulative_main
        out[pid] = {
            'phase_order': info['phase_order'],
            'all_wb_rounds_sorted': all_rounds,
            'effective_wb_rounds_sorted': effective,
            'play_in_rounds': play_in,
            'global_round_offset': offset,
            'is_se': info.get('is_se', False),
            'class_letter': info.get('class_letter'),
        }
        if not is_class:
            cumulative_main += len(effective)
    max_main_phase_order = max(
        (info['phase_order'] for info in out.values() if not info.get('class_letter')),
        default=0,
    )
    return out, max_main_phase_order


def compute_global_top_x(round_n, phase_info, phase_global_info, bracket_capacity,
                          placements_map, loser_uid):
    """Per-match の (global_round, global_top_x, global_bracket_label) を返す.

    WB: bracket-position 基準. play-in round は global_round=None, global_top_x=bracket_capacity.
    LB: loser placement を standings から引いて placement_to_bucket で bucket 化.
        loser_uid が無い (= account 削除 player 等) / placement 取得不可なら round_n から
        losers_top_x で round-based fallback (= 大体一致, 非pow2 では微妙にズレる).
    GF (round=0): global_top_x=2.
    SE phase: WB-only (= LB なし) として WB ロジック適用. ただし bracket_capacity は
        per-phase の next_pow2(numSeeds) を使う (= SE には play-in 概念ないので effective じゃない).
    Class phase (B/C/D/E): 同じ計算式だが label に "{letter}-" prefix を付ける.
        例: B-Winners TOP 64 / C-Losers TOP 8 / D-Grand Final.
        bracket_capacity も per-phase の numSeeds から計算 (= main の bracket_capacity は使わない).
    ROUND_ROBIN / SWISS / MATCHMAKING / CUSTOM_SCHEDULE: None.
    """
    if round_n is None:
        return (None, None, None)
    pname = phase_info.get('name') or ''
    bt = phase_info.get('bracketType')
    class_letter = _get_class_letter(pname)
    prefix = f"{class_letter}-" if class_letter else ""
    # DE / SE 以外は bracket-position 概念無し → カテゴリラベルのみ付与 (= 日本語表記).
    # CUSTOM_SCHEDULE 等の "その他" は null (= 表示しない).
    _BT_LABEL = {
        'ROUND_ROBIN': '総当たり',
        'SWISS': 'スイスドロー',
        'MATCHMAKING': 'レート戦',
    }
    if bt and bt not in ('DOUBLE_ELIMINATION', 'SINGLE_ELIMINATION'):
        cat = _BT_LABEL.get(bt)
        if cat:
            return (None, None, f"{prefix}{cat}")
        return (None, None, None)
    pid = phase_info.get('id')
    info = phase_global_info.get(pid)
    is_se = bool(info and info.get('is_se'))
    if class_letter is None and info and info.get('class_letter'):
        class_letter = info.get('class_letter')
        prefix = f"{class_letter}-"

    # class phase は per-phase の bracket_capacity を使う (= main の bracket_capacity に依存しない).
    # SE: cap = effective_bracket_capacity (= prev_pow2). 非pow2 でも R2 以降の effective bracket で計算.
    # DE: cap = effective_bracket_capacity. play-in label は cap*2 = next_pow2.
    if class_letter:
        ns = phase_info.get('numSeeds') or 0
        cap = effective_bracket_capacity(ns)
    else:
        cap = bracket_capacity

    if round_n > 0:
        if info is None:
            return (None, None, None)
        if is_se:
            # SE: play-in round 敗者は placement_to_bucket(numSeeds) でラベル付け (= 最終 placement に直結).
            # 例: 759名 SE R1 losers は placement 513-759 → bucket 768.
            # effective rounds は cap (= prev_pow2) / 2^(r-1) で計算.
            if cap is None or cap <= 1:
                return (None, None, None)
            if round_n in info['play_in_rounds']:
                ns = phase_info.get('numSeeds') or 0
                top = placement_to_bucket(ns) or (cap * 2)
                return (None, top, f"{prefix}Winners TOP {top}")
            eff = info['effective_wb_rounds_sorted']
            if round_n not in eff:
                return (None, None, None)
            idx = eff.index(round_n)
            global_r = info['global_round_offset'] + idx + 1
            top = max(2, cap // (2 ** (global_r - 1)))
            return (global_r, top, f"{prefix}Winners TOP {top}")
        # DE bracket (main or class)
        if round_n in info['play_in_rounds']:
            # play-in 敗者は bracket-size (= effective * 2 = next_pow2) でラベル付け
            play_in_label_n = cap * 2 if cap else None
            return (None, play_in_label_n,
                    f"{prefix}Winners TOP {play_in_label_n}" if play_in_label_n else None)
        eff = info['effective_wb_rounds_sorted']
        if round_n not in eff:
            return (None, None, None)
        idx = eff.index(round_n)
        global_r = info['global_round_offset'] + idx + 1
        if cap is None or cap <= 0:
            return (global_r, None, None)
        top = max(2, cap // (2 ** (global_r - 1)))
        return (global_r, top, f"{prefix}Winners TOP {top}")
    if round_n < 0:
        if is_se:
            return (None, None, None)  # SE には LB 無し
        # LB ラベルの算出:
        #   - main bracket: placements_map (= 大会全体 standings) から bucket 化
        #   - class bracket: 大会全体 placement は class 内の position を表さないので round-based 一択
        if class_letter:
            bucket = losers_top_x(round_n)
            if bucket is None:
                return (None, None, None)
            return (None, bucket, f"{prefix}Losers TOP {bucket}")
        # main: 1st choice = loser placement → bucket
        p = None
        if loser_uid is not None and placements_map is not None:
            p = placements_map.get(loser_uid)
        if p is not None:
            bucket = placement_to_bucket(p)
            if bucket is not None:
                return (None, bucket, f"Losers TOP {bucket}")
        # Fallback: round-based losers_top_x (= account 削除 player 等)
        bucket = losers_top_x(round_n)
        if bucket is None:
            return (None, None, None)
        return (None, bucket, f"Losers TOP {bucket}")
    # round == 0 (Grand Final)
    if is_se:
        return (None, None, None)
    return (None, 2, f"{prefix}Grand Final")


def fetch_event_phases(event_id):
    """phases + phase_groups リストを取得."""
    resp = fetch_data_with_retries(
        get_event_phases_full_query(),
        {"eventId": event_id},
    )
    if not isinstance(resp, dict) or "data" not in resp:
        raise FetchError(f"phases response missing 'data': {resp}")
    ev = resp.get("data", {}).get("event")
    if not ev:
        raise FetchError(f"event not found: {event_id}")
    phases = ev.get("phases") or []
    return phases


def fetch_phase_group_sets(pg_id, per_page=50, with_games=False):
    """1 phase_group の sets を全件取得.

    with_games=True のときは スコア + games (キャラ/ステージ選択) 統合クエリを使い、
    1 パスで試合結果とキャラ details の両方を取得する (= download とキャラ取得の二重叩き解消).
    games は complexity が高いので開始 perPage を 8 にクランプし、complexity backoff で
    最小 4 まで自動で下げる。

    変更点 (バグ修正):
      - `len(nodes) < cur_per_page` の break 条件は start.gg の不安定なページサイズで
        早期 break する原因になっていた (production で 50→12 と返ってきて 62 件で打切るケース観測).
      - 代わりに **page 1 の totalPages を authoritative とし、その回数まで pagination する**.
      - 並びは `sortType: NONE` (= ID 順) に切替えて安定化.
      - 取得後、`set.id` で dedup. pageInfo.total と一致しなければ再試行.
    """
    _query = (get_phase_group_sets_full_with_games_query if with_games
              else get_phase_group_sets_full_query)
    if with_games:
        per_page = min(per_page, 8)  # games は complexity が高いので小さめに開始
    sets = []
    seen_ids = set()
    total_pages = None
    expected_total = None
    page = 1
    max_pages = 50  # 安全装置
    while page <= max_pages:
        variables = {"phaseGroupId": pg_id, "page": page, "perPage": per_page}
        cur_per_page = per_page
        attempts = 0
        soft_attempts = 0
        while True:
            variables["perPage"] = cur_per_page
            resp = fetch_data_with_retries(_query(), variables)
            errs = resp.get("errors") if isinstance(resp, dict) else None
            if errs and any("complexity" in str(e).lower() for e in errs):
                if cur_per_page <= 4 or attempts >= 6:
                    raise FetchError(f"complexity exceeded at pg={pg_id} page={page}: {errs}")
                cur_per_page = max(4, cur_per_page // 2)
                attempts += 1
                time.sleep(API_DELAY_SEC)
                continue
            # rate limit 等の GraphQL エラーは HTTP 200 + errors / data.phaseGroup=null で
            # 返ってくる. 旧実装はこれを「sets 0 件」と解釈して silent partial になっていた
            # (= 兵庫対戦会#31 で Bクラス phase 96 sets が丸ごと欠落). retry → 最終 raise.
            _pg_null = not ((resp.get("data") or {}).get("phaseGroup") if isinstance(resp, dict) else None)
            if errs or _pg_null:
                if soft_attempts >= 4:
                    raise FetchError(f"pg={pg_id} page={page}: errors or null phaseGroup after retries: {str(errs)[:200]}")
                soft_attempts += 1
                time.sleep(API_DELAY_SEC * (soft_attempts + 1))
                continue
            break
        pg_data = (resp.get("data", {}) or {}).get("phaseGroup") or {}
        sets_data = pg_data.get("sets") or {}
        nodes = sets_data.get("nodes") or []
        page_info = sets_data.get("pageInfo") or {}
        if page == 1:
            total_pages = page_info.get("totalPages")
            expected_total = page_info.get("total")
        for n in nodes:
            nid = (n or {}).get("id")
            if nid is None or nid in seen_ids:
                continue
            seen_ids.add(nid)
            sets.append(n)
        if not nodes:
            break
        if total_pages and page >= total_pages:
            break
        page += 1
        time.sleep(API_DELAY_SEC)
    # 取得 sets 数が expected_total に届かない場合、もう一度全 page を別 per_page で試行.
    # start.gg のページサイズ揺れで取りこぼした sets を回収するための fallback.
    if expected_total is not None and len(sets) < expected_total:
        fallback_per_page = max(4 if with_games else 8, per_page // 2)
        page = 1
        while page <= max_pages:
            variables = {"phaseGroupId": pg_id, "page": page, "perPage": fallback_per_page}
            try:
                resp = fetch_data_with_retries(_query(), variables)
            except Exception:
                break
            pg_data = (resp.get("data", {}) or {}).get("phaseGroup") or {}
            sets_data = pg_data.get("sets") or {}
            nodes = sets_data.get("nodes") or []
            page_info = sets_data.get("pageInfo") or {}
            tp_fb = page_info.get("totalPages")
            added = 0
            for n in nodes:
                nid = (n or {}).get("id")
                if nid is None or nid in seen_ids: continue
                seen_ids.add(nid); sets.append(n); added += 1
            if not nodes:
                break
            if tp_fb and page >= tp_fb:
                break
            if len(sets) >= expected_total:
                break
            page += 1
            time.sleep(API_DELAY_SEC)
    # fallback 後も expected_total に届かない場合は silent partial にせず fail させる
    # (= caller 側で event を done にしない → 次回 nightly で再取得される).
    if expected_total is not None and len(sets) < expected_total:
        raise FetchError(f"pg={pg_id} incomplete: fetched {len(sets)}/{expected_total} sets")
    return sets


def _build_entrant2user(all_nodes):
    out = {}
    for node in all_nodes:
        if not isinstance(node, dict): continue
        for slot in (node.get("slots") or []):
            ent = slot.get("entrant") or {}
            eid = ent.get("id")
            if eid is None: continue
            parts = ent.get("participants") or []
            if not parts: continue
            u = (parts[0] or {}).get("user") or {}
            uid = u.get("id")
            if uid is not None:
                out[eid] = uid
    return out


def _games_to_details(node, entrant2user):
    """set node の games (character/stage 選択履歴) を matches.json `details[]` schema へ変換.

    games 無しクエリ (= get_phase_group_sets_full_query) で取得した node では
    node['games'] が存在しないため [] を返す。games 付きクエリ
    (= get_phase_group_sets_full_with_games_query) のときのみ中身が入る。
    schema は download_specific_event.py / merge_character_games.py と一致。"""
    details = []
    for game in (node.get("games") or []):
        if not isinstance(game, dict):
            continue
        winner_id_in_game = game.get("winnerId")
        selections_data = []
        for selection in (game.get("selections") or []):
            if not isinstance(selection, dict):
                continue
            ent = selection.get("entrant") or {}
            char = selection.get("character") or {}
            if ent.get("id") is None or char.get("id") is None or char.get("name") is None:
                continue
            selections_data.append({
                "user_id": entrant2user.get(ent.get("id")),
                "selection_id": selection.get("id"),
                "character_id": char.get("id"),
                "character_name": char.get("name"),
            })
        details.append({
            "game_id": game.get("id"),
            "order_num": game.get("orderNum"),
            "winner_id": entrant2user.get(winner_id_in_game) if winner_id_in_game else None,
            "entrant1_score": game.get("entrant1Score"),
            "entrant2_score": game.get("entrant2Score"),
            "stage": (game.get("stage") or {}).get("name") if game.get("stage") else None,
            "selections": selections_data,
        })
    return details


def _load_placements_map(event_dir: Path):
    """standings.json → {user_id: placement} を読む. 無ければ {} を返す."""
    sp = event_dir / "standings.json"
    if not sp.exists():
        return {}
    try:
        with sp.open("r", encoding="utf-8") as fh:
            sd = json.load(fh)
    except Exception:
        return {}
    items = sd.get("data") if isinstance(sd, dict) else sd
    if not isinstance(items, list):
        return {}
    out = {}
    for it in items:
        if not isinstance(it, dict): continue
        uid = it.get("user_id")
        p = it.get("placement")
        if uid is None or p is None: continue
        # 同じ uid が複数 placement に出る場合は最も上位を採用
        if uid not in out or p < out[uid]:
            out[uid] = p
    return out


def _phase_max_numseeds(all_sets_with_phase):
    """main phase の max numSeeds (= 総参加者) を返す. class phase は除外."""
    seen_phase = {}
    for _, phase_info, _ in all_sets_with_phase:
        pid = phase_info.get('id')
        pname = phase_info.get('name') or ''
        if pid is None or _is_class_phase(pname): continue
        if pid in seen_phase: continue
        seen_phase[pid] = phase_info.get('numSeeds') or 0
    return max(seen_phase.values()) if seen_phase else 0


def write_matches_v2(event_id, all_sets_with_phase, event_dir: Path):
    """Write matches.json from sets enriched with phase info."""
    # all_sets_with_phase: list of (set_node, phase_info dict, pg_info dict)
    entrant2user = _build_entrant2user([s for s, _, _ in all_sets_with_phase])
    placements_map = _load_placements_map(event_dir)
    # play-in 補正後の有効 capacity を使う (= 69 名 → 64, 192 名 → 128 等. pow2 ならそのまま)
    bracket_capacity = effective_bracket_capacity(_phase_max_numseeds(all_sets_with_phase))
    phase_global_info, max_main_phase_order = compute_phase_global_rounds(all_sets_with_phase)
    json_data = {
        "data": [],
        "bracket_capacity": bracket_capacity,
    }
    seen_set_ids = set()
    seen_match_keys = set()  # (pg_id, round, round_text, winner_uid, loser_uid) — set.id dedup の補助 (= start.gg が同じ試合を異なる set id で返す稀ケース対応)
    dup_set_id = 0
    dup_match_key = 0
    for node, phase_info, pg_info in all_sets_with_phase:
        if not isinstance(node, dict): continue
        nid = node.get("id")
        if nid is not None:
            if nid in seen_set_ids:
                dup_set_id += 1
                continue
            seen_set_ids.add(nid)
        slots = node.get("slots") or []
        if len(slots) != 2: continue
        slot0, slot1 = slots[0], slots[1]
        if not slot0 or not slot1: continue
        if not (slot0.get("entrant") and slot1.get("entrant")): continue
        st0 = slot0.get("standing") or {}; st1 = slot1.get("standing") or {}
        if st0.get("stats") is None or st1.get("stats") is None: continue
        score0 = ((st0.get("stats") or {}).get("score") or {}).get("value")
        score1 = ((st1.get("stats") or {}).get("score") or {}).get("value")
        if score0 is None: score0 = 0
        if score1 is None: score1 = 0
        # winnerId 優先
        winner_eid = node.get("winnerId")
        ent0_id = (slot0.get("entrant") or {}).get("id")
        ent1_id = (slot1.get("entrant") or {}).get("id")
        if winner_eid is not None and winner_eid in (ent0_id, ent1_id):
            winner_slot = slot0 if winner_eid == ent0_id else slot1
        else:
            if score0 == score1:
                continue
            winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot is slot0 else slot0
        winner_score = score0 if winner_slot is slot0 else score1
        loser_score = score1 if winner_slot is slot0 else score0
        dq = (score0 < 0 or score1 < 0)
        cancel = (score0 == 0 and score1 == 0 and winner_eid is None)
        # games / details: with_games クエリのときのみ node['games'] が入る (= キャラ選択).
        # games 無しクエリでは [] (従来どおり).
        details = _games_to_details(node, entrant2user)
        wave = pg_info.get("wave") or {}
        wid_ent = (winner_slot.get("entrant") or {}).get("id")
        lid_ent = (loser_slot.get("entrant") or {}).get("id")
        # Bracket position info
        phase_top_n = parse_phase_top_n(phase_info.get("name"))
        round_n = node.get("round")
        round_text = node.get("fullRoundText") or ""
        # bracket_label: 「この試合の敗者の placement (= TOP X)」を表す.
        # Winners side: WB R r 敗北 → LB へ. 即ち WB R r の TOP X = phase_top_n / 2^(r-1)
        # Losers side: LB R r 敗北 → 即 elimination. placement = W2W (= |round|+1) から逆引き.
        winners_top = winners_top_x(round_n, phase_top_n) if (round_n is not None and round_n > 0) else None
        losers_top = losers_top_x(round_n) if (round_n is not None and round_n < 0) else None
        bracket_label = None
        if round_n is not None:
            if round_n > 0 and winners_top is not None:
                bracket_label = f"Winners TOP {winners_top}"
            elif round_n < 0 and losers_top is not None:
                bracket_label = f"Losers TOP {losers_top}"
            elif round_n == 0:
                bracket_label = "Grand Final"
        # Global bracket position labels (class phase は None になる).
        loser_uid = entrant2user.get(lid_ent)
        global_round, global_top_x, global_bracket_label = compute_global_top_x(
            round_n, phase_info, phase_global_info, bracket_capacity,
            placements_map, loser_uid,
        )
        match_data = {
            "match_id": nid,
            "winner_id": entrant2user.get(wid_ent),
            "loser_id": loser_uid,
            "winner_score": winner_score,
            "loser_score": loser_score,
            "round_text": round_text,
            "round": round_n,
            "phase": pg_info.get("displayIdentifier"),
            "phase_id": phase_info.get("id"),
            "phase_name": phase_info.get("name"),
            "phase_order": phase_info.get("phaseOrder"),     # multi-phase 大会で phase 順序を保持
            "phase_num_seeds": phase_info.get("numSeeds"),
            "phase_bracket_type": phase_info.get("bracketType"),
            "phase_top_n": phase_top_n,        # phase 内 bracket の入場サイズ
            "bracket_label": bracket_label,    # = "Winners TOP X" / "Losers TOP X" (敗者着地, phase-internal)
            "winners_top": winners_top,        # WB 側: 敗北で落ちる TOP X (phase-internal)
            "losers_top": losers_top,          # LB 側: 敗北で確定する TOP X (phase-internal)
            "global_round": global_round,                  # main bracket での累積 WB round 番号
            "global_top_x": global_top_x,                  # bracket_capacity / 2^(global_round-1) or LB は placement bucket
            "global_bracket_label": global_bracket_label,  # = "Winners TOP X" / "Losers TOP X" (global)
            "phase_group_id": pg_info.get("id"),
            "phase_group_start_at": pg_info.get("startAt"),  # Unix timestamp; phase_group 開始予定時刻
            "wave_id": wave.get("id"),
            "wave": wave.get("identifier"),
            "wave_start_at": wave.get("startAt"),  # Unix timestamp; wave 開始予定時刻
            "dq": dq,
            "cancel": cancel,
            "state": node.get("state"),
            "details": details,
        }
        # 二重チェック: 同じ (pg, round_text, winner_uid, loser_uid) を持つ試合が既に
        # 別 set id で書かれていたら重複と見なして skip.
        # 注: round だけだと Grand Final と Grand Final Reset が同 round + 同 winner/loser
        # で識別不能になる (= LB-side が GF1+GF Reset の双方を勝つケースで GF Reset が
        # 誤削除される). round_text を加えてその偽陽性を排除.
        # ただし ROUND_ROBIN phase では同ペアが複数回対戦するのが正当 → tuple-key dedup skip.
        wuid = match_data.get("winner_id")
        luid = match_data.get("loser_id")
        # ROUND_ROBIN / MATCHMAKING phase は同ペアが複数回対戦するのが正当 → tuple-key dedup skip.
        is_rr_phase = phase_info.get("bracketType") in ("ROUND_ROBIN", "MATCHMAKING")
        if wuid is not None and luid is not None and not is_rr_phase:
            mkey = (pg_info.get("id"), round_n, round_text or '', wuid, luid)
            if mkey in seen_match_keys:
                dup_match_key += 1
                continue
            seen_match_keys.add(mkey)
        json_data["data"].append(match_data)
    json_data["dup_set_id"] = dup_set_id
    json_data["dup_match_key"] = dup_match_key
    (event_dir / "matches.json").write_text(json.dumps(json_data, ensure_ascii=False))
    if dup_set_id or dup_match_key:
        print(f"    event={event_id} dedup: set_id_dups={dup_set_id} match_key_dups={dup_match_key}", flush=True)
    return len(json_data["data"])


def refetch_event(event_id, event_dir: Path, per_page=50):
    """Phase group iteration で event の matches を再取得."""
    phases = fetch_event_phases(event_id)
    time.sleep(API_DELAY_SEC)
    all_sets_with_phase = []
    total_pgs = sum(len((p.get("phaseGroups") or {}).get("nodes") or []) for p in phases)
    pg_failures = []
    pg_done = 0
    for phase in phases:
        phase_info = {
            "id": phase.get("id"),
            "name": phase.get("name"),
            "numSeeds": phase.get("numSeeds"),
            "bracketType": phase.get("bracketType"),
            "phaseOrder": phase.get("phaseOrder"),
        }
        for pg in (phase.get("phaseGroups") or {}).get("nodes") or []:
            pg_info = {
                "id": pg.get("id"),
                "displayIdentifier": pg.get("displayIdentifier"),
                "startAt": pg.get("startAt"),  # phase_group 開始予定時刻 (Unix timestamp)
                "wave": pg.get("wave"),  # { id, identifier, startAt }
            }
            try:
                sets = fetch_phase_group_sets(pg.get("id"), per_page=per_page)
                pg_done += 1
            except FetchError as e:
                print(f"    pg={pg.get('id')} fetch failed: {e}", flush=True)
                pg_failures.append({"pg_id": pg.get("id"), "phase_id": phase.get("id"), "error": str(e)[:300]})
                sets = []
            for s in sets:
                all_sets_with_phase.append((s, phase_info, pg_info))
            time.sleep(API_DELAY_SEC)
    if pg_failures:
        # Raise to flag this event as needing manual retry
        raise FetchError(f"{len(pg_failures)}/{total_pgs} phase_groups failed for event {event_id}: {pg_failures[:3]}")
    n = write_matches_v2(event_id, all_sets_with_phase, event_dir)
    return n, total_pgs


def refetch_event_phases(event_id, event_dir: Path, target_phase_ids, per_page=50):
    """指定 phase_id 群だけを refetch して既存 matches.json に merge.

    - target_phase_ids: 再取得対象の phase_id (= int の set/list)
    - 他の phase の match data は既存値を保持
    - 新しい match の global_round は新規取得 set 全てから compute_phase_global_rounds で計算
      (= 非対象 phase の round 情報も既存 match_data から fake set にして渡す)
    """
    target_phase_ids = set(int(p) for p in target_phase_ids)
    # 1. Load existing matches.json
    existing_file = event_dir / "matches.json"
    if existing_file.exists():
        try:
            existing_md = json.loads(existing_file.read_text())
        except Exception:
            existing_md = {"data": []}
    else:
        existing_md = {"data": []}
    existing_matches = existing_md.get("data", []) or []
    # 2. Fetch phases (1 query). Need phase_info for target phases.
    phases = fetch_event_phases(event_id)
    time.sleep(API_DELAY_SEC)
    new_sets_with_phase = []
    n_pgs_fetched = 0
    for phase in phases:
        pid = phase.get("id")
        if pid not in target_phase_ids:
            continue
        phase_info = {
            "id": pid,
            "name": phase.get("name"),
            "numSeeds": phase.get("numSeeds"),
            "bracketType": phase.get("bracketType"),
            "phaseOrder": phase.get("phaseOrder"),
        }
        for pg in (phase.get("phaseGroups") or {}).get("nodes") or []:
            pg_info = {
                "id": pg.get("id"),
                "displayIdentifier": pg.get("displayIdentifier"),
                "startAt": pg.get("startAt"),  # phase_group 開始予定時刻 (Unix timestamp)
                "wave": pg.get("wave"),  # { id, identifier, startAt }
            }
            try:
                sets = fetch_phase_group_sets(pg.get("id"), per_page=per_page)
                n_pgs_fetched += 1
            except FetchError as e:
                print(f"    pg={pg.get('id')} fetch failed: {e}", flush=True)
                sets = []
            for s in sets:
                new_sets_with_phase.append((s, phase_info, pg_info))
            time.sleep(API_DELAY_SEC)
    # 3. 非対象 phase の match_data → fake set_node (= round 情報のみ) を作って phase_global_info 計算に渡す.
    existing_phases_info_by_pid = {}
    for ph in phases:
        existing_phases_info_by_pid[ph.get("id")] = {
            "id": ph.get("id"),
            "name": ph.get("name"),
            "numSeeds": ph.get("numSeeds"),
            "bracketType": ph.get("bracketType"),
            "phaseOrder": ph.get("phaseOrder"),
        }
    fake_sets_for_global_calc = []
    for m in existing_matches:
        pid = m.get("phase_id")
        if pid is None or pid in target_phase_ids:
            continue
        ph_info = existing_phases_info_by_pid.get(pid)
        if ph_info is None:
            ph_info = {
                "id": pid,
                "name": m.get("phase_name", ""),
                "numSeeds": m.get("phase_num_seeds"),
                "bracketType": m.get("phase_bracket_type"),
                "phaseOrder": None,
            }
        fake_node = {"round": m.get("round")}
        fake_sets_for_global_calc.append((fake_node, ph_info, {"id": m.get("phase_group_id")}))
    # 4. write_matches_v2 ロジックを再現: 新規 set のみ match_data に変換、既存 match_data は保持.
    combined_sets_for_phase_global = new_sets_with_phase + fake_sets_for_global_calc
    entrant2user = _build_entrant2user([s for s, _, _ in new_sets_with_phase])
    placements_map = _load_placements_map(event_dir)
    # bracket_capacity: existing or recompute. Use existing if present, else recompute.
    # play-in 補正後の有効 capacity を使う (= effective_bracket_capacity)
    bracket_capacity = existing_md.get("bracket_capacity")
    if bracket_capacity is None:
        bracket_capacity = effective_bracket_capacity(_phase_max_numseeds(combined_sets_for_phase_global))
    phase_global_info, _ = compute_phase_global_rounds(combined_sets_for_phase_global)

    # 既存 matches を target_phase_ids 以外で保持
    kept_matches = [m for m in existing_matches if m.get("phase_id") not in target_phase_ids]
    seen_set_ids = set(m.get("match_id") for m in kept_matches if m.get("match_id") is not None)
    seen_match_keys = set()
    for m in kept_matches:
        wuid = m.get("winner_id"); luid = m.get("loser_id")
        if wuid is not None and luid is not None:
            seen_match_keys.add((m.get("phase_group_id"), m.get("round"), m.get("round_text") or '', wuid, luid))

    new_match_data = []
    dup_set_id = 0
    dup_match_key = 0
    for node, phase_info, pg_info in new_sets_with_phase:
        if not isinstance(node, dict): continue
        nid = node.get("id")
        if nid is not None:
            if nid in seen_set_ids:
                dup_set_id += 1
                continue
            seen_set_ids.add(nid)
        if node.get("state") != 3: continue
        slots = node.get("slots") or []
        if len(slots) != 2: continue
        slot0, slot1 = slots[0], slots[1]
        if not slot0 or not slot1: continue
        if not (slot0.get("entrant") and slot1.get("entrant")): continue
        st0 = slot0.get("standing") or {}; st1 = slot1.get("standing") or {}
        if st0.get("stats") is None or st1.get("stats") is None: continue
        score0 = ((st0.get("stats") or {}).get("score") or {}).get("value")
        score1 = ((st1.get("stats") or {}).get("score") or {}).get("value")
        if score0 is None: score0 = 0
        if score1 is None: score1 = 0
        winner_eid = node.get("winnerId")
        ent0_id = (slot0.get("entrant") or {}).get("id")
        ent1_id = (slot1.get("entrant") or {}).get("id")
        if winner_eid is not None and winner_eid in (ent0_id, ent1_id):
            winner_slot = slot0 if winner_eid == ent0_id else slot1
        else:
            if score0 == score1: continue
            winner_slot = slot0 if score0 > score1 else slot1
        loser_slot = slot1 if winner_slot is slot0 else slot0
        winner_score = score0 if winner_slot is slot0 else score1
        loser_score = score1 if winner_slot is slot0 else score0
        dq = (score0 < 0 or score1 < 0)
        cancel = (score0 == 0 and score1 == 0 and winner_eid is None)
        wave = pg_info.get("wave") or {}
        wid_ent = (winner_slot.get("entrant") or {}).get("id")
        lid_ent = (loser_slot.get("entrant") or {}).get("id")
        phase_top_n = parse_phase_top_n(phase_info.get("name"))
        round_n = node.get("round")
        round_text = node.get("fullRoundText") or ""
        winners_top = winners_top_x(round_n, phase_top_n) if (round_n is not None and round_n > 0) else None
        losers_top = losers_top_x(round_n) if (round_n is not None and round_n < 0) else None
        bracket_label = None
        if round_n is not None:
            if round_n > 0 and winners_top is not None:
                bracket_label = f"Winners TOP {winners_top}"
            elif round_n < 0 and losers_top is not None:
                bracket_label = f"Losers TOP {losers_top}"
            elif round_n == 0:
                bracket_label = "Grand Final"
        loser_uid = entrant2user.get(lid_ent)
        global_round, global_top_x, global_bracket_label = compute_global_top_x(
            round_n, phase_info, phase_global_info, bracket_capacity,
            placements_map, loser_uid,
        )
        winner_uid = entrant2user.get(wid_ent)
        # ROUND_ROBIN / MATCHMAKING phase は同ペアが複数回対戦するのが正当 → tuple-key dedup skip.
        _is_rr_phase = phase_info.get("bracketType") in ("ROUND_ROBIN", "MATCHMAKING")
        if winner_uid is not None and loser_uid is not None and not _is_rr_phase:
            # round_text を含めて GF vs GF Reset (= 同 round, 同 winner/loser) を識別.
            mkey = (pg_info.get("id"), round_n, round_text or '', winner_uid, loser_uid)
            if mkey in seen_match_keys:
                dup_match_key += 1
                continue
            seen_match_keys.add(mkey)
        match_data = {
            "match_id": nid,
            "winner_id": winner_uid,
            "loser_id": loser_uid,
            "winner_score": winner_score,
            "loser_score": loser_score,
            "round_text": round_text,
            "round": round_n,
            "phase": pg_info.get("displayIdentifier"),
            "phase_id": phase_info.get("id"),
            "phase_name": phase_info.get("name"),
            "phase_order": phase_info.get("phaseOrder"),
            "phase_num_seeds": phase_info.get("numSeeds"),
            "phase_bracket_type": phase_info.get("bracketType"),
            "phase_top_n": phase_top_n,
            "bracket_label": bracket_label,
            "winners_top": winners_top,
            "losers_top": losers_top,
            "global_round": global_round,
            "global_top_x": global_top_x,
            "global_bracket_label": global_bracket_label,
            "phase_group_id": pg_info.get("id"),
            "phase_group_start_at": pg_info.get("startAt"),  # Unix timestamp; phase_group 開始予定時刻
            "wave_id": wave.get("id"),
            "wave": wave.get("identifier"),
            "wave_start_at": wave.get("startAt"),  # Unix timestamp; wave 開始予定時刻
            "dq": dq,
            "cancel": cancel,
            "state": node.get("state"),
            "details": _games_to_details(node, entrant2user),
        }
        new_match_data.append(match_data)

    final_data = kept_matches + new_match_data
    json_out = {
        "data": final_data,
        "bracket_capacity": bracket_capacity,
        "dup_set_id": dup_set_id,
        "dup_match_key": dup_match_key,
        "partial_refetched_phase_ids": sorted(list(target_phase_ids)),
    }
    existing_file.write_text(json.dumps(json_out, ensure_ascii=False))
    if dup_set_id or dup_match_key:
        print(f"    event={event_id} partial refetch dedup: set_id_dups={dup_set_id} match_key_dups={dup_match_key}", flush=True)
    return len(new_match_data), n_pgs_fetched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--max_retries", type=int, default=5)
    parser.add_argument("--retry_delay", type=int, default=10)
    parser.add_argument("--per_page", type=int, default=50)
    parser.add_argument("--min-dups", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dup-list", default="/tmp/all_events_to_refetch.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-list", default="/tmp/refetch_v2_failed_events.jsonl")
    parser.add_argument("--start-idx", type=int, default=0, help="Skip first N targets (for resuming)")
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    with open(args.dup_list) as f:
        affected = json.load(f)
    targets = [a for a in affected if a.get("duplicates", 0) >= args.min_dups]
    if args.start_idx > 0:
        targets = targets[args.start_idx:]
    if args.limit > 0:
        targets = targets[:args.limit]
    print(f"Targets: {len(targets)} events (v2 phase_group iteration)", flush=True)

    n_ok = 0; n_fail = 0
    for i, a in enumerate(targets):
        if i % 5 == 0:
            print(f"  [{i}/{len(targets)}] ok={n_ok} fail={n_fail}", flush=True)
        ev_id = a["event_id"]
        event_dir = Path(a["path"])
        before_unique = a.get("unique_matches", 0)
        try:
            new_count, n_pgs = refetch_event(ev_id, event_dir, per_page=args.per_page)
        except FetchError as e:
            err_str = str(e)
            print(f"  fail event={ev_id} '{a.get('tournament_name', '')}': {err_str}", flush=True)
            n_fail += 1
            try:
                with open(args.fail_list, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "event_id": ev_id, "path": str(event_dir),
                        "tournament_name": a.get("tournament_name", ""),
                        "error": err_str[:500],
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass
            continue
        delta = new_count - before_unique
        print(f"  ✓ event={ev_id} '{a.get('tournament_name', '')}' "
              f"pgs={n_pgs} before={before_unique} → after={new_count} (Δ {delta:+d})", flush=True)
        n_ok += 1

    print(f"\nDone. ok={n_ok} fail={n_fail}", flush=True)


if __name__ == "__main__":
    main()
