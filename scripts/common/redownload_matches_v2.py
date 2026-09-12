#!/usr/bin/env python3
"""Re-download matches.json using phase_group iteration (instead of event-level pagination).

Problem: the existing event-level pagination (fetch_all_nodes) has an AIMD overlap-skip that may
drop legitimate sets, causing missing sets on large events.
Example, Kagaribi #15 Yuzha: start.gg shows 9 matches but our data only has 6.

New approach (v2):
  1. fetch the event's phase list
  2. fetch the phase_groups of each phase
  3. fetch sets per phase_group (phase_groups are small, so usually 1-2 pages suffice)
  4. attach phase_id, phase_name, phase_num_seeds, phase_group_id, wave_id to each set
  5. add to matches.json: phase_id, phase_name, phase_num_seeds, wave_id

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

from scripts.common.queries import (
    get_event_phases_full_query, get_phase_group_sets_full_query,
    get_phase_group_sets_full_with_games_query, get_event_sets_full_query,
)
from scripts.common.utils import (
    fetch_data_with_retries, fetch_all_nodes, set_retry_parameters, set_api_parameters,
    FetchError,
)

# Inter-call delay to avoid rate limiting (start.gg recommends roughly 1-2 RPS)
API_DELAY_SEC = 0.6


# Extract TOP X from the phase name (= bracket size)
def parse_phase_top_n(name: str) -> int | None:
    if not name:
        return None
    m = re.search(r'TOP\s*(\d+)', name, re.IGNORECASE)
    if m:
        try: return int(m.group(1))
        except: pass
    return None


# Table mapping W2W (Wins-to-Win) in a DE bracket -> placement bucket upper bound (= TOP X).
# e.g. W2W=2 (LB Final loser = 3rd) -> TOP 3, W2W=5 -> TOP 8, W2W=23 -> TOP 4096.
# Formula:
#   W2W=2k (even) -> TOP = 3 x 2^(k-1)   (e.g. w2w=4 -> k=2 -> 6 = upper bound of 5-6)
#   W2W=2k+1 (odd) -> TOP = 2^(k+1)     (e.g. w2w=5 -> k=2 -> 8 = upper bound of 7-8)
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
    """Upper bound of the placement bucket a loser of this winners-side round drops into."""
    # Losing in WB R r -> LB. Losing again in LB R1 (= where WB R r-1 losers land) gives
    # placement: TOP {N / 2^(r-1)} bucket. I.e. the "TOP X" boundary at the WB stage.
    if round_n <= 0 or phase_top_n is None or phase_top_n <= 0:
        return None
    return max(2, phase_top_n // (2 ** max(0, round_n - 1)))


def losers_top_x(round_n: int) -> int:
    """Final placement bucket upper bound (= TOP X) when losing in a losers-side round.
    start.gg round notation: round=-1 -> LB Final, -2 -> LB Semi, ... larger absolute values the further from the LB final.
    """
    if round_n >= 0:
        return None
    # Loser of an LB round (start.gg, |round|=k) has W2W = k + 1
    # e.g. LB Final (k=1) loser = 3rd (W2W=2)
    w2w = abs(round_n) + 1
    return w2w_to_top_x(w2w)


def next_pow2(n: int) -> int:
    if n is None or n <= 1: return 1
    p = 1
    while p < n: p *= 2
    return p


def effective_bracket_capacity(n: int) -> int:
    """Effective bracket capacity after play-in correction = largest pow2 <= n.
    e.g. n=64 -> 64 (= unchanged), n=69 -> 64, n=128 -> 128, n=192 -> 128.
    In a DE bracket where n is not pow2, the surplus (n - prev_pow2) is absorbed by R1 play-ins and
    the effective structure from R2 on is the same as a prev_pow2-entrant SE. Labeling uses this effective capacity.
    """
    if n is None or n <= 1: return 1
    np = next_pow2(n)
    if np == n: return n      # n is already pow2
    return np // 2            # largest pow2 below n


# Class phase (B/C/D/E-class) detection — filter to separate from the main bracket.
# A-class is the top bracket (= TO WIN type) and treated as main, so it is not excluded.
# Supports both English ("B class" / "B-class" / "BClass") and Japanese (letter + katakana "kurasu").
_CLASS_PHASE_RE = re.compile(r'\b[B-E][- ]?class\b|[B-EＢＣＤＥ][- ]?クラス', re.IGNORECASE)
def _is_class_phase(phase_name: str) -> bool:
    return bool(phase_name and _CLASS_PHASE_RE.search(phase_name))


# Fullwidth -> ASCII map (= fullwidth "B" -> "B", etc.)
_FULLWIDTH_TO_ASCII = str.maketrans('ＡＢＣＤＥ', 'ABCDE')

def _get_class_letter(phase_name: str) -> str | None:
    """Return the class phase letter (= 'B'/'C'/'D'/'E'). None if not a class phase."""
    if not phase_name: return None
    m = _CLASS_PHASE_RE.search(phase_name)
    if not m: return None
    # m.group(0) is "B class" / "b-class" / the Japanese or fullwidth form, etc. Take the first letter as ASCII uppercase.
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
    """Compute global round numbers for main (= non-class) phases.

    List the WB rounds of each phase, detect and drop play-ins (= fewer matches than the next round),
    order the remaining effective rounds by phaseOrder and use the cumulative index as global_round.

    SE phases are treated like WB (WB-only, no LB).
    ROUND_ROBIN / SWISS / MATCHMAKING / CUSTOM_SCHEDULE have no bracket-position concept and are skipped.

    Returns:
        phase_info: dict[phase_id] = {
            'phase_order': int,
            'all_wb_rounds_sorted': list,
            'effective_wb_rounds_sorted': list,
            'play_in_rounds': set,
            'global_round_offset': int (cumulative offset),
            'is_se': bool (= whether an SE phase),
        }
        max_main_phase_order: int (for the LB "final phase" check)
    """
    by_phase: dict = {}
    # Collect both main and class phases. main accumulates global_round; class is self-contained (= prefix label only).
    for set_node, phase_info, _ in all_sets_with_phase:
        pname = phase_info.get('name') or ''
        bt = phase_info.get('bracketType')
        # Only DE and SE have a bracket-position concept (= SE is treated as WB-only).
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
    # Main bracket accumulates global_round in phase_order. Class brackets are self-contained (= offset 0).
    sorted_pids = sorted(by_phase.keys(), key=lambda pid: (by_phase[pid]['phase_order'], pid))
    out = {}
    cumulative_main = 0  # cumulative count excluding class
    for pid in sorted_pids:
        info = by_phase[pid]
        all_rounds = sorted(info['wb_round_counts'].keys())
        play_in = set()
        # Play-in detection: leading consecutive rounds that do not follow the natural doubling pattern
        # of the following round (= R[i+1]*2) are treated as play-ins.
        # Examples:
        #   - 759-entrant SE: R1=247, R2=256. R1 < R2*2=512 -> play-in.
        #   - 413-entrant SE: R1=157, R2=128. R1 < R2*2=256 -> play-in.
        #   - 24-seed A class: R1=4, R2=2. R1 = R2*2=4 -> not a play-in.
        #   - 32-seed SE: R1=16, R2=8. R1 = R2*2=16 -> not a play-in.
        # numSeeds is unreliable (= inconsistent across phase_groups even within a phase), so it is not used.
        for i in range(len(all_rounds) - 1):
            cur = all_rounds[i]; nxt = all_rounds[i + 1]
            cur_n = info['wb_round_counts'][cur]
            nxt_n = info['wb_round_counts'][nxt]
            # play-in if it does not match the natural doubling pattern of the next round
            if cur_n != nxt_n * 2:
                play_in.add(cur)
            else:
                break
        effective = [r for r in all_rounds if r not in play_in]
        is_class = info.get('class_letter') is not None
        # Class brackets are not included in the global_round accumulation (= each class is an independent bracket)
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
    """Return (global_round, global_top_x, global_bracket_label) per match.

    WB: based on bracket position. Play-in rounds get global_round=None, global_top_x=bracket_capacity.
    LB: look up the loser's placement in standings and bucket it with placement_to_bucket.
        If loser_uid is missing (= deleted account, etc.) / placement unavailable, fall back to a round-based
        losers_top_x from round_n (= mostly matches; slightly off for non-pow2).
    GF (round=0): global_top_x=2.
    SE phase: WB logic applied as WB-only (= no LB). bracket_capacity however uses the per-phase
        next_pow2(numSeeds) (= SE has no play-in concept, so not the effective one).
    Class phase (B/C/D/E): same formula, but the label gets a "{letter}-" prefix.
        e.g. B-Winners TOP 64 / C-Losers TOP 8 / D-Grand Final.
        bracket_capacity is also computed from the per-phase numSeeds (= the main bracket_capacity is not used).
    ROUND_ROBIN / SWISS / MATCHMAKING / CUSTOM_SCHEDULE: None.
    """
    if round_n is None:
        return (None, None, None)
    pname = phase_info.get('name') or ''
    bt = phase_info.get('bracketType')
    class_letter = _get_class_letter(pname)
    prefix = f"{class_letter}-" if class_letter else ""
    # Non-DE/SE have no bracket-position concept -> only a category label (= Japanese text; written to matches.json, do not change).
    # "Other" such as CUSTOM_SCHEDULE is null (= not displayed).
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

    # Class phases use a per-phase bracket_capacity (= independent of the main bracket_capacity).
    # SE: cap = effective_bracket_capacity (= prev_pow2). Even for non-pow2, computed on the effective bracket from R2 on.
    # DE: cap = effective_bracket_capacity. Play-in label is cap*2 = next_pow2.
    if class_letter:
        ns = phase_info.get('numSeeds') or 0
        cap = effective_bracket_capacity(ns)
    else:
        cap = bracket_capacity

    if round_n > 0:
        if info is None:
            return (None, None, None)
        if is_se:
            # SE: play-in round losers are labeled with placement_to_bucket(numSeeds) (= maps directly to final placement).
            # e.g. 759-entrant SE R1 losers have placement 513-759 -> bucket 768.
            # Effective rounds are computed as cap (= prev_pow2) / 2^(r-1).
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
            # Play-in losers are labeled with the bracket size (= effective * 2 = next_pow2)
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
            return (None, None, None)  # SE has no LB
        # LB label computation:
        #   - main bracket: bucket from placements_map (= tournament-wide standings)
        #   - class bracket: tournament-wide placement does not reflect position within the class, so round-based only
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
        # Fallback: round-based losers_top_x (= deleted account, etc.)
        bucket = losers_top_x(round_n)
        if bucket is None:
            return (None, None, None)
        return (None, bucket, f"Losers TOP {bucket}")
    # round == 0 (Grand Final)
    if is_se:
        return (None, None, None)
    return (None, 2, f"{prefix}Grand Final")


def fetch_event_phases(event_id):
    """Fetch the list of phases + phase_groups."""
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
    """Fetch all sets of one phase_group.

    With with_games=True, use the combined scores + games (character/stage selections) query to
    fetch match results and character details in one pass (= no more double calls for download and character fetch).
    games are high-complexity, so the starting perPage is clamped to 8 and complexity backoff
    lowers it automatically down to a minimum of 4.

    Changes (bug fixes):
      - The `len(nodes) < cur_per_page` break condition caused early breaks due to start.gg's unstable
        page size (observed in production: 50->12 returned, cut off at 62 items).
      - Instead, **page 1's totalPages is authoritative and pagination runs that many times**.
      - Ordering switched to `sortType: NONE` (= ID order) for stability.
      - After fetching, dedup by `set.id`. Retry if the count does not match pageInfo.total.
    """
    _query = (get_phase_group_sets_full_with_games_query if with_games
              else get_phase_group_sets_full_query)
    if with_games:
        per_page = min(per_page, 8)  # games are high-complexity, so start small
    sets = []
    seen_ids = set()
    total_pages = None
    expected_total = None
    page = 1
    max_pages = 50  # safety limit
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
            # GraphQL errors such as rate limits come back as HTTP 200 + errors / data.phaseGroup=null.
            # The old implementation read that as "0 sets" and produced a silent partial
            # (= at Hyogo Taisenkai #31 the whole B-class phase, 96 sets, went missing). Retry -> raise at the end.
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
    # If the fetched set count is short of expected_total, try all pages again with a different per_page.
    # Fallback to recover sets dropped by start.gg page-size jitter.
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
    # If still short of expected_total after the fallback, fail instead of writing a silent partial
    # (= the caller does not mark the event done -> re-fetched on the next nightly).
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
    """Convert a set node's games (character/stage selection history) into the matches.json `details[]` schema.

    Nodes fetched with the no-games query (= get_phase_group_sets_full_query) have no
    node['games'], so [] is returned. Only nodes from the games query
    (= get_phase_group_sets_full_with_games_query) yield content.
    The schema matches download_specific_event.py / merge_character_games.py."""
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
    """Read standings.json -> {user_id: placement}. Returns {} if missing."""
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
        # If the same uid appears with multiple placements, take the best one
        if uid not in out or p < out[uid]:
            out[uid] = p
    return out


def _phase_max_numseeds(all_sets_with_phase):
    """Return the max numSeeds (= total entrants) of the main phases. Class phases excluded."""
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
    # Use the effective capacity after play-in correction (= 69 entrants -> 64, 192 -> 128, etc.; pow2 unchanged)
    bracket_capacity = effective_bracket_capacity(_phase_max_numseeds(all_sets_with_phase))
    phase_global_info, max_main_phase_order = compute_phase_global_rounds(all_sets_with_phase)
    json_data = {
        "data": [],
        "bracket_capacity": bracket_capacity,
    }
    seen_set_ids = set()
    seen_match_keys = set()  # (pg_id, round, round_text, winner_uid, loser_uid) — supplements set.id dedup (= handles the rare case where start.gg returns the same match under different set ids)
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
        # winnerId takes precedence
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
        # games / details: node['games'] is present only with the with_games query (= character selections).
        # With the no-games query it is [] (as before).
        details = _games_to_details(node, entrant2user)
        wave = pg_info.get("wave") or {}
        wid_ent = (winner_slot.get("entrant") or {}).get("id")
        lid_ent = (loser_slot.get("entrant") or {}).get("id")
        # Bracket position info
        phase_top_n = parse_phase_top_n(phase_info.get("name"))
        round_n = node.get("round")
        round_text = node.get("fullRoundText") or ""
        # bracket_label: represents "the placement of this match's loser (= TOP X)".
        # Winners side: losing in WB R r -> LB. I.e. TOP X of WB R r = phase_top_n / 2^(r-1)
        # Losers side: losing in LB R r -> immediate elimination. placement derived from W2W (= |round|+1).
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
        # Global bracket position labels (None for class phases).
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
            "phase_order": phase_info.get("phaseOrder"),     # keeps phase order in multi-phase tournaments
            "phase_num_seeds": phase_info.get("numSeeds"),
            "phase_bracket_type": phase_info.get("bracketType"),
            "phase_top_n": phase_top_n,        # entry size of the bracket within the phase
            "bracket_label": bracket_label,    # = "Winners TOP X" / "Losers TOP X" (where the loser lands, phase-internal)
            "winners_top": winners_top,        # WB side: TOP X the loser drops to (phase-internal)
            "losers_top": losers_top,          # LB side: TOP X finalized on losing (phase-internal)
            "global_round": global_round,                  # cumulative WB round number in the main bracket
            "global_top_x": global_top_x,                  # bracket_capacity / 2^(global_round-1), or the placement bucket for LB
            "global_bracket_label": global_bracket_label,  # = "Winners TOP X" / "Losers TOP X" (global)
            "phase_group_id": pg_info.get("id"),
            "phase_group_start_at": pg_info.get("startAt"),  # Unix timestamp; scheduled start of the phase_group
            "wave_id": wave.get("id"),
            "wave": wave.get("identifier"),
            "wave_start_at": wave.get("startAt"),  # Unix timestamp; scheduled start of the wave
            "dq": dq,
            "cancel": cancel,
            "state": node.get("state"),
            "started_at": node.get("startedAt"),      # Unix timestamp; set start (actual)
            "completed_at": node.get("completedAt"),  # Unix timestamp; set completion (actual)
            "details": details,
        }
        # Double check: if a match with the same (pg, round_text, winner_uid, loser_uid) was already
        # written under a different set id, treat it as a duplicate and skip.
        # Note: with round alone, Grand Final and Grand Final Reset share the same round + same winner/loser
        # and are indistinguishable (= when the LB side wins both GF1 and GF Reset, GF Reset would be
        # wrongly dropped). round_text is added to eliminate that false positive.
        # In ROUND_ROBIN phases the same pair legitimately plays multiple times -> skip tuple-key dedup.
        wuid = match_data.get("winner_id")
        luid = match_data.get("loser_id")
        # ROUND_ROBIN / MATCHMAKING phases legitimately have repeated pairings -> skip tuple-key dedup.
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


def _event_sets_total(event_id):
    """Return the event-level sets.total (for missing-set verification; None on failure). Low complexity, robust."""
    q = "query($e:ID!){ event(id:$e){ sets(page:1,perPage:1){ pageInfo{ total } } } }"
    try:
        r = fetch_data_with_retries(q, {"e": event_id})
        return ((r.get("data") or {}).get("event") or {}).get("sets", {}).get("pageInfo", {}).get("total")
    except Exception:
        return None


def fetch_event_sets_full(event_id, per_page=40):
    """Fetch event.sets with full fields by paging and return a list of (node, phase_info, pg_info) tuples.

    Alternative path avoiding the silent partial where phase_group iteration (fetch_phase_group_sets) returns
    empty phaseGroup.sets under rate limiting (total=0 / no error / phaseGroup non-null).
    event.sets is low-complexity and robustly returns everything, so it is used to recover events with missing sets.
    FetchError if the count does not match pageInfo.total (= never silently write a partial fetch).
    """
    out = []
    seen = set()
    expected = None
    total_pages = None
    page = 1
    while page <= 200:
        cur = per_page
        attempts = 0
        soft = 0
        while True:
            resp = fetch_data_with_retries(
                get_event_sets_full_query(), {"eventId": event_id, "page": page, "perPage": cur})
            errs = resp.get("errors") if isinstance(resp, dict) else None
            if errs and any("complexity" in str(e).lower() for e in errs):
                if cur <= 4 or attempts >= 6:
                    raise FetchError(f"complexity exceeded event={event_id} page={page}: {errs}")
                cur = max(4, cur // 2); attempts += 1; time.sleep(API_DELAY_SEC); continue
            ev = (resp.get("data") or {}).get("event") if isinstance(resp, dict) else None
            if errs or ev is None:
                if soft >= 4:
                    raise FetchError(f"event={event_id} page={page}: errors/null event after retries: {str(errs)[:200]}")
                soft += 1; time.sleep(API_DELAY_SEC * (soft + 1)); continue
            break
        sd = ev.get("sets") or {}
        nodes = sd.get("nodes") or []
        pi = sd.get("pageInfo") or {}
        if page == 1:
            expected = pi.get("total"); total_pages = pi.get("totalPages")
        for n in nodes:
            nid = (n or {}).get("id")
            if nid is None or nid in seen:
                continue
            seen.add(nid)
            pg = n.get("phaseGroup") or {}
            ph = pg.get("phase") or {}
            phase_info = {"id": ph.get("id"), "name": ph.get("name"), "numSeeds": ph.get("numSeeds"),
                          "bracketType": ph.get("bracketType"), "phaseOrder": ph.get("phaseOrder")}
            pg_info = {"id": pg.get("id"), "displayIdentifier": pg.get("displayIdentifier"),
                       "startAt": pg.get("startAt"), "wave": pg.get("wave")}
            out.append((n, phase_info, pg_info))
        if not nodes or (total_pages and page >= total_pages):
            break
        page += 1
        time.sleep(API_DELAY_SEC)
    if expected is not None and len(out) < expected:
        raise FetchError(f"event={event_id}: event.sets got {len(out)} < total {expected} (likely throttled)")
    return out


def refetch_event_robust(event_id, event_dir: Path, per_page=40):
    """Re-fetch matches via event.sets (avoids the silent partial of phase_group iteration). Returns (n, total)."""
    all_sets = fetch_event_sets_full(event_id, per_page=per_page)
    n = write_matches_v2(event_id, all_sets, event_dir)
    return n, len(all_sets)


def refetch_event(event_id, event_dir: Path, per_page=50):
    """Re-fetch the event's matches by phase group iteration."""
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
                "startAt": pg.get("startAt"),  # scheduled start of the phase_group (Unix timestamp)
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
    # Silent-partial detection: under rate limiting, phase_group iteration may return empty phaseGroup.sets
    # (no error) and pass through with 0 items. Compare with the event-level sets.total and, if far short,
    # raise so it stays a retry target (= do not let an empty matches.json be marked done).
    _exp = _event_sets_total(event_id)
    if _exp and len(all_sets_with_phase) < _exp * 0.9:
        raise FetchError(f"event={event_id}: phase_group iteration got {len(all_sets_with_phase)} "
                         f"but event.sets.total={_exp} (silent-partial / throttled) — flagging for retry")
    n = write_matches_v2(event_id, all_sets_with_phase, event_dir)
    return n, total_pgs


def refetch_event_phases(event_id, event_dir: Path, target_phase_ids, per_page=50):
    """Re-fetch only the given phase_ids and merge into the existing matches.json.

    - target_phase_ids: phase_ids to re-fetch (= set/list of int)
    - match data of other phases keeps its existing values
    - global_round of new matches is computed by compute_phase_global_rounds from all newly fetched sets
      (= round info of non-target phases is passed as fake sets built from existing match_data)
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
                "startAt": pg.get("startAt"),  # scheduled start of the phase_group (Unix timestamp)
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
    # 3. Build fake set_nodes (= round info only) from the match_data of non-target phases and pass them to the phase_global_info computation.
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
    # 4. Replicate the write_matches_v2 logic: convert only new sets to match_data; keep existing match_data.
    combined_sets_for_phase_global = new_sets_with_phase + fake_sets_for_global_calc
    entrant2user = _build_entrant2user([s for s, _, _ in new_sets_with_phase])
    placements_map = _load_placements_map(event_dir)
    # bracket_capacity: existing or recompute. Use existing if present, else recompute.
    # Use the effective capacity after play-in correction (= effective_bracket_capacity)
    bracket_capacity = existing_md.get("bracket_capacity")
    if bracket_capacity is None:
        bracket_capacity = effective_bracket_capacity(_phase_max_numseeds(combined_sets_for_phase_global))
    phase_global_info, _ = compute_phase_global_rounds(combined_sets_for_phase_global)

    # Keep existing matches outside target_phase_ids
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
        # ROUND_ROBIN / MATCHMAKING phases legitimately have repeated pairings -> skip tuple-key dedup.
        _is_rr_phase = phase_info.get("bracketType") in ("ROUND_ROBIN", "MATCHMAKING")
        if winner_uid is not None and loser_uid is not None and not _is_rr_phase:
            # Include round_text to distinguish GF vs GF Reset (= same round, same winner/loser).
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
            "phase_group_start_at": pg_info.get("startAt"),  # Unix timestamp; scheduled start of the phase_group
            "wave_id": wave.get("id"),
            "wave": wave.get("identifier"),
            "wave_start_at": wave.get("startAt"),  # Unix timestamp; scheduled start of the wave
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
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=int, default=10)
    parser.add_argument("--per-page", type=int, default=50)
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
