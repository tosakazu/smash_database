#!/usr/bin/env python3
"""upcoming_entrants.json に本番ビルドと同一判定 (休日/pre/制限/下位クラス) を注釈する。

判定の定義元は地域モジュール scripts/<地域>/classify.py の upcoming_flags()
(取得済みイベントは derive.py が同じ判定を derived.json に書く)。地域が upcoming_flags を
持たなければ何もしない。2026-09-08 に spsp/download から smash_database へ移動。

fetch_upcoming_entrants.py の後に実行する。--in と --out を分けると
「注釈なしのファイルが公開される」事故を防げる (fetch は staging に書き、
本ツールが注釈済みを公開先へ atomic に書く)。

使い方:
  annotate_upcoming.py --region Japan --in STAGING --out PUBLIC
  annotate_upcoming.py --region Japan PATH      # 旧形式 (in-place)
"""
import argparse
import json
import os
import sys
import time

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
from scripts.common.region import load_region_classifier  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--region', required=True, help='判定モジュール scripts/<地域>/classify.py を選ぶ')
    ap.add_argument('path', nargs='?', default=None, help='旧形式: in-place で注釈')
    ap.add_argument('--in', dest='inp', default=None)
    ap.add_argument('--out', dest='out', default=None)
    args = ap.parse_args()
    inp = args.inp or args.path
    out = args.out or args.path
    if not inp or not out:
        ap.error('--in と --out (または PATH) が要る')

    clf = load_region_classifier(args.region)
    flags_of = getattr(clf, 'upcoming_flags', None)
    if flags_of is None:
        print(f'地域 {args.region} は upcoming の判定を持たない (classify.py に upcoming_flags が無い) — 何もしない')
        return 0
    tz = getattr(clf, 'TIMEZONE', None)
    if not tz:
        ap.error(f'scripts/{args.region}/classify.py に TIMEZONE が無い')
    os.environ['TZ'] = tz
    time.tzset()

    with open(inp) as f:
        d = json.load(f)
    for ev in d['events'].values():
        nent = ev.get('num_entrants') or len(ev.get('entrants') or [])
        ev.update(flags_of(ev.get('tournament_name') or '', ev.get('event_name') or '',
                           nent, ev.get('start_at')))

    tmp = out + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, out)
    print(f'annotated {len(d["events"])} events -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
