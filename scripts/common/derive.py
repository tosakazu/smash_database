#!/usr/bin/env python3
"""derive — 取得済みイベントごとに判定結果 (classify.py) を derived.json に書く (「導出」= 生データから機械的に導いたもの。人が編集する表は manual/)。

  python3 scripts/common/derive.py --region Japan            # 更新のあったイベントだけ
  python3 scripts/common/derive.py --region Japan --all      # 全件再処理 (判定を変えたとき)

判定そのもの (大会名のパターン・祝日・クラス bracket) は地域依存なので scripts/<地域>/classify.py にある。
ここは駆動部と、地域に依らない事実 (standings / matches の形) だけ。

生ファイル (attr.json 等) は書き換えない: derived.json は sidecar。download.py の取り直し判定は attr.json の
mtime を見るので、そこに触らないため。内容が同じなら書き直さない (mtime も動かない = 冪等)。
更新判定: derived.json が無い / classifier_version が古い / 入力 (attr, standings, matches, phases, class_phases/*.json)
のどれかが derived.json より新しい。日付は JST で決める (TZ を固定)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["TZ"] = "Asia/Tokyo"
time.tzset()

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import importlib  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from scripts.common._cli import add_region_arg, resolve_index_paths  # noqa: E402

DERIVED = "derived.json"
USERS_DERIVED = "users_derived.jsonl"   # data/startgg/<地域>/ に置く (users.jsonl の隣。users.jsonl 自体は download.py が書き直すので触らない)
INPUT_FILES = ("attr.json", "standings.json", "matches.json", "phases.json")


def load_region_classifier(region: str):
    """scripts/<地域>/classify.py (CLASSIFIER_VERSION と classify_event を持つ)。無ければ止める (推測しない)。"""
    modname = f"scripts.{region.replace(' ', '_')}.classify"
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        raise SystemExit(f"地域 {region!r} の判定モジュール {modname} が無い: {e}")


def result_facts(attr: dict, standings_rows: list, matches_rows: list) -> dict:
    """standings / matches から見える事実 (地域に依らない): 有効 standings 行数、最小 placement、DE phase の有無
    (完了・非キャンセル・非 DQ・両者 user あり・自己対戦でない試合に限る = spsp の読み込み条件と同じ)。"""
    places = []
    n = 0
    for s in standings_rows or []:
        if not isinstance(s, dict):
            continue
        if s.get("placement") is None or s.get("user_id") is None:
            continue
        n += 1
        places.append(int(s["placement"]))
    has_de = False
    for m in matches_rows or []:
        if not isinstance(m, dict) or m.get("state") != 3 or m.get("cancel"):
            continue
        wid, lid = m.get("winner_id"), m.get("loser_id")
        if wid is None or lid is None or wid == lid or m.get("dq"):
            continue
        if m.get("phase_bracket_type") == 'DOUBLE_ELIMINATION':
            has_de = True
            break
    return {
        "n_standings": n,
        "min_placement": min(places) if places else None,
        "has_de_phase": has_de,
        "has_gf_recorded": attr.get("has_gf_recorded"),
    }


def _load(path: Path):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read())
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"  WARN: {path} が読めない: {e}", file=sys.stderr)
        return None


def _rows(blob):
    if isinstance(blob, dict) and "data" in blob:
        return blob["data"]
    return blob if isinstance(blob, list) else []


def derive_event(event_dir: Path, clf) -> dict | None:
    attr = _load(event_dir / "attr.json")
    if not isinstance(attr, dict):
        return None
    tname = attr.get("tournament_name") or event_dir.parent.name
    ename = attr.get("event_name") or event_dir.name
    # クラス bracket の仮想大会かどうかはディレクトリの形 (class_phases/<字>_virtual) で決まる。
    # build_class_virtual_tournaments.py がその名前で作る = 生データ側にフラグを持たせない (2026-09-09〜)
    is_virtual = event_dir.parent.name == "class_phases" and event_dir.name.endswith("_virtual")
    class_letter = event_dir.name[: -len("_virtual")] or None if is_virtual else None
    parent_eid = None
    if is_virtual:
        parent_attr = _load(event_dir.parent.parent / "attr.json")
        if isinstance(parent_attr, dict) and parent_attr.get("event_id") is not None:
            parent_eid = int(parent_attr["event_id"])
    phases = _load(event_dir / "phases.json")
    cp_files = []
    cp_dir = event_dir / "class_phases"
    if isinstance(phases, dict) and cp_dir.is_dir():
        class_ids = {p.get("id") for p in (phases.get("phases") or []) if p.get("is_class")}
        for pid in sorted(class_ids, key=lambda x: str(x)):
            d = _load(cp_dir / f"{pid}.json")
            if isinstance(d, dict):
                cp_files.append(d)
    base = {
        "classifier_version": clf.CLASSIFIER_VERSION,
        "event_id": int(attr["event_id"]) if attr.get("event_id") is not None else None,
        "tournament_name": tname,
        "event_name": ename,
        "num_entrants": int(attr.get("num_entrants") or 0),
        "is_class_virtual": is_virtual,
        "parent_event_id": parent_eid,
        "class_letter": class_letter,
        "results": result_facts(attr, _rows(_load(event_dir / "standings.json")), _rows(_load(event_dir / "matches.json"))),
    }
    ctx = SimpleNamespace(event_dir=event_dir, attr=attr, tname=tname, ename=ename,
                          phases=phases if isinstance(phases, dict) else None, class_phase_files=cp_files)
    return clf.classify_event(base, ctx)


def _inputs_mtime(event_dir: Path) -> float:
    m = 0.0
    for name in INPUT_FILES:
        p = event_dir / name
        if p.exists():
            m = max(m, p.stat().st_mtime)
    cp = event_dir / "class_phases"
    if cp.is_dir():
        for f in cp.glob("*.json"):
            m = max(m, f.stat().st_mtime)
    return m


def needs_update(event_dir: Path, version: int) -> bool:
    out = event_dir / DERIVED
    if not out.exists():
        return True
    try:
        cur = json.loads(out.read_bytes())
        if cur.get("classifier_version") != version:
            return True
    except Exception:
        return True
    return _inputs_mtime(event_dir) > out.stat().st_mtime


def iter_event_dirs(events_root: Path):
    for dirpath, dirnames, filenames in os.walk(events_root):
        dirnames.sort()
        if "attr.json" in filenames:
            yield Path(dirpath)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_region_arg(ap)
    ap.add_argument("--events-root", default=None, help="default: data/startgg/<region>/events")
    ap.add_argument("--users-file-path", default=None, help="default: data/startgg/<region>/users.jsonl (users_derived.jsonl を隣に書く)")
    ap.add_argument("--all", action="store_true", help="全件再処理 (更新判定を無視)")
    ap.add_argument("--dry-run", action="store_true", help="書かずに件数だけ出す")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    resolve_index_paths(ap, args, events_root="events", users_file_path="users.jsonl")
    root = Path(args.events_root)
    if not root.is_dir():
        ap.error(f"events root が無い: {root}")
    if not args.region:
        ap.error("--region が要る (判定モジュール scripts/<地域>/classify.py を選ぶ)")
    clf = load_region_classifier(args.region)
    try:
        import jpholiday  # noqa: F401  (暦判定に必要)
    except ImportError:
        ap.error("jpholiday が無い (pip install jpholiday)")
    seen = checked = written = unchanged = failed = 0
    for ev in iter_event_dirs(root):
        seen += 1
        if not args.all and not needs_update(ev, clf.CLASSIFIER_VERSION):
            continue
        checked += 1
        cur = derive_event(ev, clf)
        if cur is None:
            failed += 1
            continue
        text = json.dumps(cur, ensure_ascii=False, indent=2) + "\n"
        out = ev / DERIVED
        if out.exists() and out.read_text(encoding="utf-8") == text:
            unchanged += 1
            if args.all:
                os.utime(out, None)   # 入力より新しいことを記録 (内容は同じ)
            continue
        if not args.dry_run:
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, out)
        written += 1
    # ── users: users.jsonl → users_derived.jsonl (地域モジュールの classify_user が対象を決める) ──
    users_written = None
    if hasattr(clf, "classify_user") and Path(args.users_file_path).exists():
        rows = []
        with open(args.users_file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    urec = json.loads(line)
                except Exception:
                    continue
                res = clf.classify_user(urec)
                if res is None or urec.get("user_id") is None:
                    continue
                rows.append({"user_id": int(urec["user_id"]), **res})
        rows.sort(key=lambda r: r["user_id"])
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        out = Path(args.users_file_path).parent / USERS_DERIVED
        if out.exists() and out.read_text(encoding="utf-8") == text:
            users_written = False
        else:
            if not args.dry_run:
                tmp = out.with_suffix(".jsonl.tmp")
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, out)
            users_written = True
        users_n = len(rows)
    if not args.quiet:
        if users_written is not None:
            print(f"[derive] users_derived.jsonl: {users_n} users {'written' if users_written else 'unchanged'}", flush=True)
        print(f"[derive] events={seen} checked={checked} written={written} unchanged={unchanged} failed={failed}"
              f"{' (dry-run)' if args.dry_run else ''} classifier_version={clf.CLASSIFIER_VERSION} region={args.region}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
