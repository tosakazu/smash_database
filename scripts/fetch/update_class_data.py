#!/usr/bin/env python3
"""クラス bracket (B/C/D/E クラス) データの nightly 更新ドライバ.

直近 N 日のイベントから「matches.json にクラス phase があるのに phases.json で
is_class マークされていない」ものを検出し、4 段階パイプラインを通す:
  1. fetch_event_phases.py --event-dirs-file ...   (phases.json 生成)
  2. fetch_class_phase_standings.py               (class_phases/<pid>.json)
  3. fetch_class_phase_players.py                 (played_user_ids 追記)
  4. build_class_virtual_tournaments.py           (<letter>_virtual/ 生成)

2-4 は既存出力をスキップする冪等スクリプトなので全体走査でも軽い.
背景: phases.json は元々 200 人以上の大会への手動バックフィルのみで、
小中規模大会 501 件でクラス bracket が未分離だった (2026-06-11 発覚).

使い方 (smash_db_tournament ディレクトリから):
  python3 scripts/fetch/update_class_data.py --token "$STARTGG_TOKEN" [--since-days 30]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EVENTS_ROOT = ROOT / "data" / "startgg" / "events" / "Japan"

LOWER_CLASS_PAT = re.compile(
    r'[BCDEＢＣＤＥ]\s*クラス|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)


def detect_unseparated(since_days: int) -> list[str]:
    """直近 since_days 日のイベントで class phase 未マークの event dir を返す."""
    cutoff = dt.date.today() - dt.timedelta(days=since_days)
    out = []
    for ydir in sorted(EVENTS_ROOT.iterdir()):
        if not ydir.is_dir() or not ydir.name.isdigit():
            continue
        for mdir in sorted(ydir.iterdir()):
            if not mdir.is_dir() or not mdir.name.isdigit():
                continue
            for ddir in sorted(mdir.iterdir()):
                if not ddir.is_dir() or not ddir.name.isdigit():
                    continue
                try:
                    d = dt.date(int(ydir.name), int(mdir.name), int(ddir.name))
                except ValueError:
                    continue
                if d < cutoff:
                    continue
                for mp in ddir.rglob("matches.json"):
                    ed = mp.parent
                    if "_virtual" in str(ed):
                        continue
                    try:
                        md = json.loads(mp.read_bytes())
                    except Exception:
                        continue
                    ms = md.get("data", md) if isinstance(md, dict) else md
                    if not isinstance(ms, list):
                        continue
                    class_phases = {m.get("phase_name") for m in ms
                                    if isinstance(m, dict) and m.get("phase_name")
                                    and LOWER_CLASS_PAT.search(m.get("phase_name"))}
                    if not class_phases:
                        continue
                    marked = set()
                    pj = ed / "phases.json"
                    if pj.exists():
                        try:
                            pd_ = json.loads(pj.read_bytes())
                            marked = {p.get("name") for p in (pd_.get("phases") or [])
                                      if p.get("is_class")}
                        except Exception:
                            pass
                    if class_phases - marked:
                        out.append(str(ed))
    return out


def run(cmd: list[str]) -> int:
    print(f"+ {' '.join(c if 'token' not in c.lower() else c for c in cmd[:3])} ...", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--since-days", type=int, default=30)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    dirs = detect_unseparated(args.since_days)
    print(f"[update_class_data] unseparated class events (last {args.since_days}d): {len(dirs)}",
          flush=True)
    rc = 0
    if dirs:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(dirs, f, ensure_ascii=False)
            list_path = f.name
        rc |= run([args.python, "-u", str(HERE / "fetch_event_phases.py"),
                   "--token", args.token, "--event-dirs-file", list_path])
    # 2-4 は冪等 (既存出力 skip) なので毎回全体に対して実行
    rc |= run([args.python, "-u", str(HERE / "fetch_class_phase_standings.py"),
               "--token", args.token])
    rc |= run([args.python, "-u", str(HERE / "fetch_class_phase_players.py"),
               "--token", args.token])
    rc |= run([args.python, "-u",
               str(ROOT.parent / "ranking_eval" / "build_class_virtual_tournaments.py"),
               "--events-root", str(EVENTS_ROOT)])
    print(f"[update_class_data] done rc={rc}", flush=True)
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
