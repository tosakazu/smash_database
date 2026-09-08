#!/usr/bin/env python3
"""upcoming_entrants.json に本番ビルドと同一判定 (休日/pre/制限/下位クラス) を注釈する (日本の大会名パターン)。

判定の定義元は scripts/Japan/classify.py (取得済みイベントは derive.py が同じ判定を derived.json に書く)。
2026-09-08 に spsp/download から smash_database へ移動。

fetch_upcoming_entrants.py の後に実行する。--in と --out を分けると
「注釈なしのファイルが公開される」事故を防げる (fetch は staging に書き、
本ツールが注釈済みを公開先へ atomic に書く)。

使い方:
  annotate_upcoming.py --in STAGING --out PUBLIC
  annotate_upcoming.py PATH            # 旧形式 (in-place)
"""
import argparse
import datetime as dt
import json
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
from scripts.Japan.classify import (is_weekend_range, is_force_weekday_tournament,  # noqa: E402
                                    is_force_weekend_range,
                                    PRE_PATTERN, RESTRICTED_PATTERN, LOWER_CLASS_PATTERN,
                                    SMAPA_PATTERN, SPECIAL_RULES_PATTERN,
                                    UCHI_PATTERN, NON_SERIOUS_PATTERN)

# お盆/年末年始の nent >= 80 大会を休日扱いにする閾値 (spsp/common.py FORCE_WEEKEND_MIN_NENT と同じ値)
FORCE_WEEKEND_MIN_NENT = 80
DEFAULT = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', nargs='?', default=None, help='旧形式: in-place で注釈')
    ap.add_argument('--in', dest='inp', default=None)
    ap.add_argument('--out', dest='out', default=None)
    args = ap.parse_args()
    inp = args.inp or args.path
    out = args.out or args.path
    if not inp or not out:
        ap.error('--in と --out (または PATH) が要る')

    with open(inp) as f:
        d = json.load(f)
    JST = dt.timezone(dt.timedelta(hours=9))
    for eid, ev in d['events'].items():
        tname = ev.get('tournament_name') or ''
        ename = ev.get('event_name') or ''
        hay = f'{tname} {ename}'
        nent = ev.get('num_entrants') or len(ev.get('entrants') or [])
        start_ts = ev.get('start_at')
        is_pre = bool(PRE_PATTERN.search(hay))
        if start_ts:
            start = dt.datetime.fromtimestamp(start_ts, JST).date()
            # 実質休日 (お盆/年末年始 nent>=80) 込み。upcoming の nent は登録者数なので
            # 本ビルド (standings ベース) と境界付近で食い違い得る点は許容。
            wk_real = is_weekend_range(start, start)
            wk = (wk_real
                  or (nent >= FORCE_WEEKEND_MIN_NENT and is_force_weekend_range(start, start)))
            ev['is_weekend'] = (wk and not is_pre
                                and not is_force_weekday_tournament(tname, ename, nent))
            # is_weekend_real = 実暦の土日祝. 食い違いをフロントで「実質休日」表示する
            # (build_tournaments_index.py と同じ規約)
            ev['is_weekend_real'] = wk_real
        else:
            ev['is_weekend'] = False  # 開始日不明は保守的に平日扱い
            ev['is_weekend_real'] = False
        ev['is_pre'] = is_pre
        ev['is_restricted'] = bool(RESTRICTED_PATTERN.search(hay))
        ev['is_lower_class'] = bool(LOWER_CLASS_PATTERN.search(hay))
        ev['is_smapa'] = bool(SMAPA_PATTERN.search(hay))
        # 本ビルド (v4) は NON_SERIOUS_PATTERN = 特殊ルール ∪ 身内 で判定する。
        # ここを SPECIAL_RULES_PATTERN だけにしていたため、身内大会
        # (あらいぶ杯 / もつカップ / 帝国オフ / 合宿 / 篝炎 / invitational 等) が
        # 予定一覧で「ポイントが入る大会」に見えていた (2026-08 発覚)。
        ev['is_non_serious'] = bool(NON_SERIOUS_PATTERN.search(hay))
        # 内訳も出す (表示側で「身内」と「特殊ルール」を出し分けられるように)
        ev['is_uchi'] = bool(UCHI_PATTERN.search(hay))
        ev['is_special_rules'] = bool(SPECIAL_RULES_PATTERN.search(hay))

    tmp = out + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, out)
    print(f'annotated {len(d["events"])} events -> {out}')


if __name__ == '__main__':
    main()
