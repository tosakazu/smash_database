#!/usr/bin/env python3
"""derive — write the classification result (classify.py) for each downloaded event to derived.json ("derived" = mechanically derived from raw data; human-edited tables live in manual/).

  python3 scripts/common/derive.py --region Japan            # only events that changed
  python3 scripts/common/derive.py --region Japan --all      # reprocess everything (after changing the classifier)

The classification itself (tournament-name patterns, holidays, class brackets) is region-specific and lives in scripts/<region>/classify.py.
This file is only the driver plus region-independent facts (the shape of standings / matches).

Raw files (attr.json etc.) are never rewritten: derived.json is a sidecar. download.py decides re-fetches from the
mtime of attr.json, so that must stay untouched. If the content is unchanged it is not rewritten (mtime stays = idempotent).
Update rule: derived.json missing / classifier_version outdated / any input (attr, standings, matches, phases, class_phases/*.json)
newer than derived.json. The timezone used to decide dates is the region module's TIMEZONE
(e.g. Japan = Asia/Tokyo); nothing is hard-coded here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from types import SimpleNamespace  # noqa: E402
from scripts.common._cli import add_region_arg, resolve_index_paths  # noqa: E402
from scripts.common.region import load_region_classifier  # noqa: E402

DERIVED = "derived.json"
USERS_DERIVED = "users_derived.jsonl"   # placed in data/startgg/<region>/ (next to users.jsonl; users.jsonl itself is rewritten by download.py, so leave it alone)
INPUT_FILES = ("attr.json", "standings.json", "matches.json", "phases.json")


def result_facts(attr: dict, standings_rows: list, matches_rows: list) -> dict:
    """Facts visible from standings / matches (region-independent): valid standings row count, min placement, presence of a DE phase
    (only completed, non-cancelled, non-DQ matches with both users present and not self-play = same as spsp's load conditions)."""
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
        print(f"  WARN: cannot read {path}: {e}", file=sys.stderr)
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
    # Whether this is a class-bracket virtual tournament is decided by the directory shape (class_phases/<letter>_virtual).
    # build_class_virtual_tournaments.py creates it under that name = no flag is kept in the raw data (since 2026-09-09)
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
        # start.gg isOnline (attr.offline). "Online tournaments are not counted" is decided by this fact, not by the name (since 2026-09-12)
        "is_offline": bool(attr["offline"]) if attr.get("offline") is not None else None,
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
    ap.add_argument("--users-file-path", default=None, help="default: data/startgg/<region>/users.jsonl (users_derived.jsonl is written next to it)")
    ap.add_argument("--all", action="store_true", help="reprocess every event (ignore the update rule)")
    ap.add_argument("--dry-run", action="store_true", help="print counts only, write nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    resolve_index_paths(ap, args, events_root="events", users_file_path="users.jsonl")
    root = Path(args.events_root)
    if not root.is_dir():
        ap.error(f"events root not found: {root}")
    if not args.region:
        ap.error("--region is required (selects the classifier module scripts/<region>/classify.py)")
    clf = load_region_classifier(args.region)
    # The region decides the timezone used for dates (never classify NA events on Japan's calendar). No default.
    tz = getattr(clf, "TIMEZONE", None)
    if not tz:
        ap.error(f"scripts/{args.region}/classify.py has no TIMEZONE (e.g. TIMEZONE = \"Asia/Tokyo\")")
    os.environ["TZ"] = tz
    time.tzset()
    # Region-specific dependencies (jpholiday for Japan) are checked by the region module itself
    check = getattr(clf, "check_requirements", None)
    if check is not None:
        try:
            check()
        except Exception as e:
            ap.error(str(e))
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
                os.utime(out, None)   # record that it is newer than the inputs (content unchanged)
            continue
        if not args.dry_run:
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, out)
        written += 1
    # ── users: users.jsonl → users_derived.jsonl (the region module's classify_user decides who is included) ──
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
