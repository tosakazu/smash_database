#!/usr/bin/env python3
"""クラス bracket (大会の中の下位クラス別ブラケット) データの更新ドライバ.

直近 N 日のイベントから「matches.json にクラス phase があるのに phases.json で
is_class マークされていない」ものを検出し、4 段階パイプラインを通す:
  1. fetch_event_phases.py --event-dirs-file ...   (phases.json 生成)
  2. fetch_class_phase_standings.py               (class_phases/<pid>.json)
  3. fetch_class_phase_players.py                 (played_user_ids 追記)
  4. build_class_virtual_tournaments.py           (<letter>_virtual/ 生成)

2-4 は既存出力をスキップする冪等スクリプトなので全体走査でも軽い.
背景: phases.json は元々 200 人以上の大会への手動バックフィルのみで、
小中規模大会 501 件でクラス bracket が未分離だった (2026-06-11 発覚).

クラスの呼び名も有無も地域による (日本 = B/C/D/E クラス、北米 = Amateur 等) ので、
名前の判定は地域モジュール (scripts/<地域>/classify.py) が持つ。宣言の無い地域では何もしない。

使い方 (smash_db_tournament ディレクトリから):
  STARTGG_TOKEN=... python3 scripts/common/update_class_data.py --region Japan [--since-days 30]
"""
from __future__ import annotations

import os
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from scripts.common import clock  # noqa: E402
from scripts.common.region import load_region_classifier, class_support  # noqa: E402


def events_root_for(region: str) -> Path:
    return ROOT / "data" / "startgg" / region.replace(" ", "_") / "events"


def detect_unseparated(since_days: int, events_root: Path, is_class_name) -> list[str]:
    """直近 since_days 日のイベントで class phase 未マークの event dir を返す。
    is_class_name: phase 名がクラス戦かを返す地域の関数。"""
    events_root = Path(events_root)
    cutoff = clock.today() - dt.timedelta(days=since_days)
    out = []
    for ydir in sorted(events_root.iterdir()):
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
                                    and is_class_name(m.get("phase_name"))}
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.environ.get("STARTGG_TOKEN"), help="start.gg API token (省略時は環境変数 STARTGG_TOKEN)")
    ap.add_argument("--region", required=True, help="判定モジュール scripts/<地域>/classify.py を選ぶ")
    ap.add_argument("--since-days", type=int, default=30)
    ap.add_argument("--events-root", default=None, help="既定 data/startgg/<地域>/events (テストでは一時ディレクトリ)")
    args = ap.parse_args(argv)
    clf = load_region_classifier(args.region)
    cls = class_support(clf)
    if cls is None:
        print(f"[update_class_data] 地域 {args.region} はクラス bracket を扱わない (classify.py に宣言が無い) — 何もしない", flush=True)
        return 0
    if args.events_root is None:
        args.events_root = str(events_root_for(args.region))
    if not args.token:
        raise SystemExit("ERROR: --token か環境変数 STARTGG_TOKEN が必要 (値は argv に載せず env 推奨)")
    os.environ["STARTGG_TOKEN"] = args.token   # 子 (fetch_*.py の main) は env から読む (argv に載せない)
    events_root = Path(args.events_root)

    # 以前は子 4 本を subprocess で起動し、対象一覧をシステム tmp のファイルで渡していた (掃除されず残る)。
    # 同じ順で in-process に呼ぶ。各 main は自分で API 層を設定する (token は env)。
    from scripts.common import (fetch_event_phases, fetch_class_phase_standings, fetch_class_phase_players,
                                build_class_virtual_tournaments)
    dirs = detect_unseparated(args.since_days, events_root, cls.is_unseparated_class_phase)
    print(f"[update_class_data] unseparated class events (last {args.since_days}d): {len(dirs)}", flush=True)
    rc = 0
    if dirs:
        print("+ fetch_event_phases ...", flush=True)
        rc |= fetch_event_phases.main(["--event-dirs-file", "-", "--region", args.region], event_dirs=dirs) or 0
    # 2-4 は冪等 (既存出力 skip) なので毎回全体に対して実行
    print("+ fetch_class_phase_standings ...", flush=True)
    rc |= fetch_class_phase_standings.main(["--events-root", str(events_root), "--region", args.region]) or 0
    print("+ fetch_class_phase_players ...", flush=True)
    rc |= fetch_class_phase_players.main(["--events-root", str(events_root), "--region", args.region]) or 0
    print("+ build_class_virtual_tournaments ...", flush=True)
    rc |= build_class_virtual_tournaments.main(["--events-root", str(events_root), "--region", args.region]) or 0
    print(f"[update_class_data] done rc={rc}", flush=True)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
