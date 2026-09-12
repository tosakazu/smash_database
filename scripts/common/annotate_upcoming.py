#!/usr/bin/env python3
"""Annotate upcoming_entrants.json with the same flags as the production build (weekend/pre/restricted/lower class).

The flags are defined by upcoming_flags() in the region module scripts/<region>/classify.py
(for downloaded events, derive.py writes the same flags to derived.json). If the region has no
upcoming_flags, nothing is done. Moved from spsp/download to smash_database on 2026-09-08.

Run after fetch_upcoming_entrants.py. Separating --in and --out prevents the accident
of publishing an unannotated file (fetch writes to staging, and
this tool atomically writes the annotated file to the public location).

Usage:
  annotate_upcoming.py --region Japan --in STAGING --out PUBLIC
  annotate_upcoming.py --region Japan PATH      # legacy form (in-place)
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
    ap.add_argument('--region', required=True, help='Select the classifier module scripts/<region>/classify.py')
    ap.add_argument('path', nargs='?', default=None, help='Legacy form: annotate in place')
    ap.add_argument('--in', dest='inp', default=None)
    ap.add_argument('--out', dest='out', default=None)
    args = ap.parse_args()
    inp = args.inp or args.path
    out = args.out or args.path
    if not inp or not out:
        ap.error('--in and --out (or PATH) are required')

    clf = load_region_classifier(args.region)
    flags_of = getattr(clf, 'upcoming_flags', None)
    if flags_of is None:
        print(f'Region {args.region} has no upcoming flags (no upcoming_flags in classify.py) — nothing to do')
        return 0
    tz = getattr(clf, 'TIMEZONE', None)
    if not tz:
        ap.error(f'scripts/{args.region}/classify.py has no TIMEZONE')
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
