#!/usr/bin/env python3
"""Derive class-internal placements from class_phase data and create virtual
sub-tournament files for SPSP ingestion.

For each event with class_phases/<phase_id>.json files:
1. Group class phases by class letter (B/C/D/E) via phase name pattern.
2. For each class letter, compute each participant's internal class placement
   by taking their deepest (smallest num_seeds) phase + their placement there.
3. Rank participants by (deepest_phase_num_seeds, placement_in_phase) — tied
   players get the same B-class placement (= position of first tied player).
4. Filter the event's matches.json to only matches within the corresponding
   class phase groups (via phase displayIdentifier mapping).
5. Save as virtual sub-tournament directory:
     <event_dir>/class_phases/<letter>_virtual/{attr.json, standings.json, matches.json}
   The directory is later loaded as a normal Tournament by data_loader.py.

Run from the smash_db_tournament directory.
"""
import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path


CLASS_LETTER_PAT = re.compile(r'([BCDE])\s*(?:[-_\s]*[Cc]lass|クラス)', re.IGNORECASE)

# Use the same fullwidth-to-halfwidth normalization implicitly in regex
def _class_letter(name: str) -> str | None:
    if not name: return None
    # Normalize fullwidth letters
    n = name.translate(str.maketrans('ＢＣＤＥｂｃｄｅ', 'BCDEbcde'))
    m = CLASS_LETTER_PAT.search(n)
    if m:
        return m.group(1).upper()
    return None


def load_class_phase_files(event_dir: Path) -> list[dict]:
    cp_dir = event_dir / "class_phases"
    if not cp_dir.is_dir(): return []
    out = []
    for fp in cp_dir.glob("*.json"):
        # Skip our own virtual outputs
        if fp.parent.name.endswith("_virtual"): continue
        if "_virtual" in fp.name: continue
        try:
            d = json.loads(fp.read_text())
            if "phase_groups" not in d: continue
            out.append(d)
        except Exception:
            continue
    return out


def derive_class_placements(
    class_phase_data: list[dict],
    played_in_class: set[int] | None = None,
) -> tuple[dict[int, int], int]:
    """Returns (uid → class internal placement) + num_entrants.

    played_in_class (DQ 対応, optional):
      uid set 「該当 class で 1 試合以上プレイした」プレイヤー. 与えられた場合、
      この set に含まれない uid は虚 entry 扱いで除外. 試合 >= 1 ならフル参加と判定.
      None なら従来通り standings ベース.
    """
    # Per player: (smallest num_seeds, placement_in_phase) is best
    player_best = {}  # uid -> (num_seeds, placement)
    for ph in class_phase_data:
        num_seeds = ph.get("num_seeds")
        if not num_seeds: continue
        for pg in ph.get("phase_groups") or []:
            for s in pg.get("standings") or []:
                uid = s.get("user_id")
                placement = s.get("placement")
                if uid is None or placement is None: continue
                # DQ filter: B-class で 1 試合もしてないなら除外
                if played_in_class is not None and uid not in played_in_class:
                    continue
                key = (num_seeds, placement)
                if uid not in player_best or key < player_best[uid]:
                    player_best[uid] = key
    if not player_best:
        return {}, 0
    # Sort by depth_score, assign bucket_start placement to tied groups
    sorted_p = sorted(player_best.items(), key=lambda x: x[1])
    result = {}
    pos = 1
    prev_key = None
    bucket_start = 1
    for uid, key in sorted_p:
        if key != prev_key:
            bucket_start = pos
            prev_key = key
        result[uid] = bucket_start
        pos += 1
    # num_entrants = largest phase size in this class (絞り込み前の真の entry 人数)
    num_entrants = max(ph.get("num_seeds", 0) or 0 for ph in class_phase_data)
    return result, num_entrants


def build_played_in_class_set(class_phase_data: list[dict]) -> set[int]:
    """Returns: uid set who have ≥1 match in ANY of the given class phases.

    Uses `played_user_ids` field embedded per phase_group in class_phases/<phase_id>.json
    (populated by fetch_class_phase_players.py). フィールドが存在しない場合は空 set.
    """
    result: set[int] = set()
    any_data = False
    for ph in class_phase_data:
        for pg in ph.get("phase_groups") or []:
            uids = pg.get("played_user_ids")
            if uids is None: continue
            any_data = True
            for u in uids:
                if u is not None:
                    result.add(int(u))
    return result if any_data else set()


def build_phase_group_display_set(class_phase_data: list[dict]) -> set[str]:
    """Set of phaseGroup displayIdentifiers that belong to this class's phases."""
    out = set()
    for ph in class_phase_data:
        for pg in ph.get("phase_groups") or []:
            d = pg.get("display")
            if d: out.add(d)
    return out


def filter_matches(event_dir: Path, phase_group_displays: set[str]) -> list[dict]:
    """Read event matches.json, filter to those in given phase_group displays."""
    mp = event_dir / "matches.json"
    if not mp.exists(): return []
    try:
        d = json.loads(mp.read_text())
    except Exception:
        return []
    items = d if isinstance(d, list) else (d.get("data") or [])
    out = []
    for m in items:
        if not isinstance(m, dict): continue
        ph = m.get("phase")
        if ph in phase_group_displays:
            out.append(m)
    return out


def get_played_player_ids(event_dir: Path) -> set[int]:
    """親 event の matches.json から、1 試合以上プレイした player_id 集合を返す.
    DQ no-show 判定用 (= matches に出てこないプレイヤーは virtual standings からも除外).
    """
    mp = event_dir / "matches.json"
    if not mp.exists(): return set()
    try:
        d = json.loads(mp.read_text())
    except Exception:
        return set()
    items = d if isinstance(d, list) else (d.get("data") or [])
    played = set()
    for m in items:
        if not isinstance(m, dict): continue
        # 親 event の data_loader と同じ条件 (state==3, not DQ, not cancel) を確認
        if m.get("state") != 3: continue
        if m.get("dq") or m.get("cancel"): continue
        wid = m.get("winner_id"); lid = m.get("loser_id")
        if wid is None or lid is None: continue
        if wid == lid: continue
        played.add(int(wid)); played.add(int(lid))
    return played


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-root",
                        default="/Users/kasaito/dev/delbugeki-seed/smash_db_tournament/data/startgg/events/Japan")
    parser.add_argument("--force", action="store_true", help="Overwrite existing virtual files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.events_root)
    n_events = 0
    n_virtual = 0
    n_skip = 0
    for event_attr in root.rglob("attr.json"):
        # Skip virtual dirs
        if "_virtual" in str(event_attr): continue
        event_dir = event_attr.parent
        phases_file = event_dir / "phases.json"
        if not phases_file.exists(): continue
        try:
            phases_data = json.loads(phases_file.read_text())
        except Exception:
            continue
        if not phases_data.get("has_class_phases"): continue
        # 全 phase が is_class の event は「event 自体がクラス大会」(= 別 event として
        # B クラスを開催しているケース). virtual を作ると丸ごと重複するのでスキップ.
        _phs = phases_data.get("phases") or []
        if _phs and all(p.get("is_class") for p in _phs):
            continue
        n_events += 1
        # Load class phase standing data
        cp_data = load_class_phase_files(event_dir)
        if not cp_data: continue
        # Group by class letter
        by_letter = defaultdict(list)
        for ph in cp_data:
            letter = _class_letter(ph.get("phase_name") or "")
            if letter:
                by_letter[letter].append(ph)
        # Load original event attr for inheritance
        try:
            ev_attr = json.loads(event_attr.read_text())
        except Exception:
            continue
        # DQ filter: matches.json で 1 試合以上プレイしたプレイヤーのみ採用 (no-show 除外)
        played_uids = get_played_player_ids(event_dir)
        for letter, ph_list in by_letter.items():
            # その class で 1 試合以上した player set (DQ 除外用; played_user_ids が
            # class phase JSON に埋め込まれていれば使用、なければ filter なし).
            played_in_class = build_played_in_class_set(ph_list)
            placements, num_ent = derive_class_placements(
                ph_list, played_in_class=(played_in_class or None)
            )
            if not placements: continue
            # 念のため: 全 event で 1 試合もしてない player も除外
            if played_uids:
                placements = {uid: p for uid, p in placements.items() if uid in played_uids}
                if not placements: continue
            virt_dir = event_dir / "class_phases" / f"{letter}_virtual"
            virt_attr = virt_dir / "attr.json"
            if not args.force and virt_attr.exists():
                n_skip += 1
                continue
            # Build standings (DQ filter 後; placement の再付番は不要 — bucket_start 維持で良い)
            standings = [
                {"placement": p, "user_id": uid}
                for uid, p in sorted(placements.items(), key=lambda x: x[1])
            ]
            # Virtual tournament: 空の matches (BT は親 event で完結).
            # 親 event の matches.json には B-class マッチも含まれており、BT 学習は親で行う.
            # → virtual 側でマッチを処理すると double-count になるため空にする.
            # data_loader / build 側で is_class_virtual=True の場合、DQ filter と BT learning
            # を skip し、TJPR (placement scoring) だけ動かす.
            matches = []
            # Build attr (inherit from parent event)
            virt_event_id = -(int(ev_attr.get("event_id") or 0) * 10 + ord(letter) - ord("A"))
            virt_attr_d = {
                "event_id": virt_event_id,  # synthetic negative ID
                "tournament_name": ev_attr.get("tournament_name", ""),
                "event_name": f"{ev_attr.get('event_name', 'Singles')} / {letter}クラス",
                "region": ev_attr.get("region", "Japan"),
                "place": ev_attr.get("place"),
                "num_entrants": num_ent,
                "offline": ev_attr.get("offline", True),
                "status": "completed",
                "timestamp": ev_attr.get("timestamp"),
                "end_timestamp": ev_attr.get("end_timestamp"),
                "version": "1.0",
                "labels": {**(ev_attr.get("labels") or {}), "is_class_virtual": True, "class_letter": letter},
                "url": ev_attr.get("url"),
                "is_class_virtual": True,
            }
            if args.dry_run:
                print(f"DRY: would write {virt_dir.relative_to(root)} ({len(standings)} std, {len(matches)} matches)")
                n_virtual += 1
                continue
            virt_dir.mkdir(parents=True, exist_ok=True)
            virt_attr.write_text(json.dumps(virt_attr_d, ensure_ascii=False, indent=2))
            (virt_dir / "standings.json").write_text(json.dumps(standings, ensure_ascii=False))
            (virt_dir / "matches.json").write_text(json.dumps(matches, ensure_ascii=False))
            n_virtual += 1
    print(f"events_with_class_phases={n_events}, virtual_written={n_virtual}, skipped={n_skip}")


if __name__ == "__main__":
    main()
