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

from scripts.queries import get_event_phases_full_query, get_phase_group_sets_full_query
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


# クラス phase (B/C/D/E-class) 判定 — main bracket と分離するためのフィルタ
_CLASS_PHASE_RE = re.compile(r'\b[A-E][- ]?class\b', re.IGNORECASE)
def _is_class_phase(phase_name: str) -> bool:
    return bool(phase_name and _CLASS_PHASE_RE.search(phase_name))


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

    Returns:
        phase_info: dict[phase_id] = {
            'phase_order': int,
            'all_wb_rounds_sorted': list,
            'effective_wb_rounds_sorted': list,
            'play_in_rounds': set,
            'global_round_offset': int (累積 offset),
        }
        max_main_phase_order: int (LB の "final phase" 判定用)
    """
    by_phase: dict = {}
    for set_node, phase_info, _ in all_sets_with_phase:
        pname = phase_info.get('name') or ''
        if _is_class_phase(pname):
            continue
        pid = phase_info.get('id')
        if pid is None:
            continue
        r = set_node.get('round')
        if r is None or r <= 0:
            continue
        by_phase.setdefault(pid, {
            'phase_order': phase_info.get('phaseOrder') or 0,
            'wb_round_counts': {},
        })
        by_phase[pid]['wb_round_counts'][r] = by_phase[pid]['wb_round_counts'].get(r, 0) + 1
    sorted_pids = sorted(by_phase.keys(), key=lambda pid: (by_phase[pid]['phase_order'], pid))
    out = {}
    cumulative = 0
    for pid in sorted_pids:
        info = by_phase[pid]
        all_rounds = sorted(info['wb_round_counts'].keys())
        # play-in: 最も小さい side から連続して "次の round より試合数が少ない" rounds
        # 例: R1(766), R2(1024), R3(512) → R1 < R2 なので R1 は play-in. R2 と R3 は effective.
        play_in = set()
        for i in range(len(all_rounds) - 1):
            cur = all_rounds[i]; nxt = all_rounds[i + 1]
            if info['wb_round_counts'][cur] < info['wb_round_counts'][nxt]:
                play_in.add(cur)
            else:
                break
        effective = [r for r in all_rounds if r not in play_in]
        out[pid] = {
            'phase_order': info['phase_order'],
            'all_wb_rounds_sorted': all_rounds,
            'effective_wb_rounds_sorted': effective,
            'play_in_rounds': play_in,
            'global_round_offset': cumulative,
        }
        cumulative += len(effective)
    max_main_phase_order = max((info['phase_order'] for info in out.values()), default=0)
    return out, max_main_phase_order


def compute_global_top_x(round_n, phase_info, phase_global_info, bracket_capacity,
                          placements_map, loser_uid):
    """Per-match の (global_round, global_top_x, global_bracket_label) を返す.

    WB: bracket-position 基準. play-in round は global_round=None, global_top_x=bracket_capacity.
    LB: loser placement を standings から引いて placement_to_bucket で bucket 化.
    GF (round=0): global_top_x=2.
    Class phase / 不明: None.
    """
    if round_n is None:
        return (None, None, None)
    pname = phase_info.get('name') or ''
    if _is_class_phase(pname):
        return (None, None, None)
    pid = phase_info.get('id')
    info = phase_global_info.get(pid)
    if round_n > 0:
        if info is None:
            return (None, None, None)
        if round_n in info['play_in_rounds']:
            # play-in: 敗者は最下位 bucket
            return (None, bracket_capacity, f"Winners TOP {bracket_capacity}" if bracket_capacity else None)
        eff = info['effective_wb_rounds_sorted']
        if round_n not in eff:
            return (None, None, None)
        idx = eff.index(round_n)
        global_r = info['global_round_offset'] + idx + 1
        if bracket_capacity is None or bracket_capacity <= 0:
            return (global_r, None, None)
        top = max(2, bracket_capacity // (2 ** (global_r - 1)))
        return (global_r, top, f"Winners TOP {top}")
    if round_n < 0:
        # LB: loser placement → bucket
        if loser_uid is None or placements_map is None:
            return (None, None, None)
        p = placements_map.get(loser_uid)
        if p is None:
            return (None, None, None)
        bucket = placement_to_bucket(p)
        if bucket is None:
            return (None, None, None)
        return (None, bucket, f"Losers TOP {bucket}")
    # round == 0 (Grand Final)
    return (None, 2, "Grand Final")


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


def fetch_phase_group_sets(pg_id, per_page=50):
    """1 phase_group の sets を全件取得.

    変更点 (バグ修正):
      - `len(nodes) < cur_per_page` の break 条件は start.gg の不安定なページサイズで
        早期 break する原因になっていた (production で 50→12 と返ってきて 62 件で打切るケース観測).
      - 代わりに **page 1 の totalPages を authoritative とし、その回数まで pagination する**.
      - 並びは `sortType: NONE` (= ID 順) に切替えて安定化.
      - 取得後、`set.id` で dedup. pageInfo.total と一致しなければ再試行.
    """
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
        while True:
            variables["perPage"] = cur_per_page
            resp = fetch_data_with_retries(get_phase_group_sets_full_query(), variables)
            errs = resp.get("errors") if isinstance(resp, dict) else None
            if errs and any("complexity" in str(e).lower() for e in errs):
                if cur_per_page <= 4 or attempts >= 6:
                    raise FetchError(f"complexity exceeded at pg={pg_id} page={page}: {errs}")
                cur_per_page = max(4, cur_per_page // 2)
                attempts += 1
                time.sleep(API_DELAY_SEC)
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
        fallback_per_page = max(8, per_page // 2)
        page = 1
        while page <= max_pages:
            variables = {"phaseGroupId": pg_id, "page": page, "perPage": fallback_per_page}
            try:
                resp = fetch_data_with_retries(get_phase_group_sets_full_query(), variables)
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
    bracket_capacity = next_pow2(_phase_max_numseeds(all_sets_with_phase))
    phase_global_info, max_main_phase_order = compute_phase_global_rounds(all_sets_with_phase)
    json_data = {
        "data": [],
        "bracket_capacity": bracket_capacity,
    }
    seen_set_ids = set()
    seen_match_keys = set()  # (pg_id, round, winner_uid, loser_uid) — start.gg が同じ試合を異なる set id で返す稀ケース対応
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
        # games / details: 廃止 (complexity 抑制のため query から削除). 必要なら別 query で取得.
        details = []
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
            "wave_id": wave.get("id"),
            "wave": wave.get("identifier"),
            "dq": dq,
            "cancel": cancel,
            "state": node.get("state"),
            "details": details,
        }
        # 二重チェック: 同じ (pg, round, winner_uid, loser_uid) を持つ試合が既に
        # 別 set id で書かれていたら重複と見なして skip.
        wuid = match_data.get("winner_id")
        luid = match_data.get("loser_id")
        if wuid is not None and luid is not None:
            mkey = (pg_info.get("id"), round_n, wuid, luid)
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
                "wave": pg.get("wave"),
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
                "wave": pg.get("wave"),
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
    bracket_capacity = existing_md.get("bracket_capacity")
    if bracket_capacity is None:
        bracket_capacity = next_pow2(_phase_max_numseeds(combined_sets_for_phase_global))
    phase_global_info, _ = compute_phase_global_rounds(combined_sets_for_phase_global)

    # 既存 matches を target_phase_ids 以外で保持
    kept_matches = [m for m in existing_matches if m.get("phase_id") not in target_phase_ids]
    seen_set_ids = set(m.get("match_id") for m in kept_matches if m.get("match_id") is not None)
    seen_match_keys = set()
    for m in kept_matches:
        wuid = m.get("winner_id"); luid = m.get("loser_id")
        if wuid is not None and luid is not None:
            seen_match_keys.add((m.get("phase_group_id"), m.get("round"), wuid, luid))

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
        if winner_uid is not None and loser_uid is not None:
            mkey = (pg_info.get("id"), round_n, winner_uid, loser_uid)
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
            "wave_id": wave.get("id"),
            "wave": wave.get("identifier"),
            "dq": dq,
            "cancel": cancel,
            "state": node.get("state"),
            "details": [],
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
