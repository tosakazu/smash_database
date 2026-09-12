#!/usr/bin/env python3
"""Fetch the entrant list for each singles event in upcoming.json.

Output: --out (in spsp, site/data/upcoming_entrants.json). Moved from spsp/download to smash_database on 2026-09-08.
{
  "generated_at": <ts>,
  "events": {
    "<event_id>": {
      "tournament_id", "tournament_name", "event_name", "start_at",
      "num_entrants", "is_online", "is_weekend",
      "entrants": [{"uid": <user.id|null>, "tag": <gamerTag>}, ...]
    }
  },
  "by_uid": {"<uid>": [<event_id>, ...]}
}

Usage: fetch_upcoming_entrants.py [--upcoming PATH] [--out PATH] [--max-events N]
The token is read from $STARTGG_TOKEN → $SPSP_SECRETS_DIR/STARTGG_TOKEN (default ~/spsp-secrets) → ~/spsp-ranking/STARTGG_TOKEN, in that order (the value is never printed).
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API_URL = 'https://api.start.gg/gql/alpha'
TOKEN_PATHS = [os.path.join(os.environ.get('SPSP_SECRETS_DIR') or os.path.expanduser('~/spsp-secrets'), 'STARTGG_TOKEN'),
               os.path.expanduser('~/spsp-ranking/STARTGG_TOKEN')]


def _read_token() -> str:
    for p in TOKEN_PATHS:
        if os.path.exists(p):
            return open(p).read().strip()
    raise SystemExit('STARTGG_TOKEN not found (env or ' + ' / '.join(TOKEN_PATHS) + ')')
DELAY = 0.6

# Exclude waitlist / alternate-slot events (set up as separate events from the main bracket)
WAITLIST_PATTERN = re.compile(r'wait(?:ing)?\s*list|waiting for|キャンセル待ち|補欠|抽選', re.IGNORECASE)

ENTRANTS_QUERY = '''
query($eid: ID!, $page: Int!, $perPage: Int!) {
  event(id: $eid) {
    entrants(query: {perPage: $perPage, page: $page}) {
      pageInfo { totalPages }
      nodes { name participants { player { gamerTag } user { id } } }
    }
  }
}'''


def gql(token, query, variables, retries=4):
    body = json.dumps({'query': query, 'variables': variables}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=body, headers={
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                r = json.load(resp)
            if r.get('errors'):
                raise RuntimeError(str(r['errors'])[:200])
            return r['data']
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            # Exponential backoff to ride out 429 (per-minute rate limit): 5s/15s/45s
            wait = 5.0 * (3 ** attempt)
            print(f'  retry in {wait:.0f}s after error: {e}', file=sys.stderr)
            time.sleep(wait)


def is_weekend_jst(ts):
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=9)))
    return d.weekday() >= 5


def fetch_event_entrants(token, eid):
    out = []
    page = 1
    while True:
        data = gql(token, ENTRANTS_QUERY, {'eid': eid, 'page': page, 'perPage': 64})
        ev = data.get('event') or {}
        en = ev.get('entrants') or {}
        for node in en.get('nodes') or []:
            uid = None
            tag = node.get('name') or ''
            for p in node.get('participants') or []:
                if p and p.get('player') and p['player'].get('gamerTag'):
                    tag = p['player']['gamerTag']
                if p and p.get('user') and p['user'].get('id'):
                    uid = p['user']['id']
            out.append({'uid': uid, 'tag': tag})
        if page >= (en.get('pageInfo') or {}).get('totalPages', 0):
            break
        page += 1
        time.sleep(DELAY)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--upcoming', required=True, help='Output of fetch_upcoming.py (upcoming.json)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--prev', default=None,
                    help='Previously published file (source for carrying over tournaments within 24h after start)')
    ap.add_argument('--max-events', type=int, default=0, help='0=unlimited')
    ap.add_argument('--token', default=None, help='If omitted, $STARTGG_TOKEN then the token file')
    args = ap.parse_args()

    token = args.token or os.environ.get('STARTGG_TOKEN') or _read_token()
    with open(args.upcoming) as f:
        up = json.load(f)

    now = time.time()
    events = {}
    by_uid = {}
    n_done = 0
    n_skip = 0
    for t in up.get('tournaments', []):
        for ev in t.get('events', []):
            if ev.get('type') != 1:
                continue
            if WAITLIST_PATTERN.search(ev.get('event_name') or ''):
                continue
            start = ev.get('start_at') or t.get('start_at')
            # Keep events up to 24h after start (for same-day / in-progress simulation)
            if start and start + 86400 < now:
                continue
            eid = ev['event_id']
            if args.max_events and n_done >= args.max_events:
                break
            print(f'[{n_done+1}] eid={eid} {t["tournament_name"]} / {ev["event_name"]} '
                  f'(entrants={ev.get("num_entrants")})', file=sys.stderr)
            try:
                entrants = fetch_event_entrants(token, eid)
            except Exception as e:
                print(f'  SKIP eid={eid}: {e}', file=sys.stderr)
                n_skip += 1
                continue
            events[str(eid)] = {
                'tournament_id': t.get('tournament_id'),
                'tournament_name': t.get('tournament_name'),
                'event_name': ev.get('event_name'),
                'start_at': start,
                'num_entrants': ev.get('num_entrants'),
                'is_online': t.get('is_online'),
                'is_weekend': is_weekend_jst(start) if start else None,
                'entrants': entrants,
            }
            n_done += 1
            time.sleep(DELAY)

    # Protect the previous data on mass failure (more than half or all failed: do not write, exit with error)
    candidates = n_done + n_skip
    if candidates > 0 and (n_done == 0 or n_skip > n_done):
        print(f'ERROR: too many failures ({n_skip}/{candidates} skipped), keeping previous file',
              file=sys.stderr)
        sys.exit(1)

    # Carry over tournaments from the previous file that should still be kept (within 24h after start)
    # (upcoming.json does not include tournaments that already started, so same-day events are preserved here)
    try:
        with open(args.prev) as f:
            prev = json.load(f)
        for eid, ev in (prev.get('events') or {}).items():
            st = ev.get('start_at')
            if eid not in events and st and st + 86400 > now:
                events[eid] = ev
    except (OSError, ValueError):
        pass

    for eid, ev in events.items():
        for e2 in ev.get('entrants') or []:
            if e2.get('uid') is not None:
                by_uid.setdefault(str(e2['uid']), []).append(int(eid))

    out = {'generated_at': int(now), 'events': events, 'by_uid': by_uid}
    tmp = args.out + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, args.out)  # atomic replace so live readers never see a partial file
    print(f'wrote {args.out}: {len(events)} events ({n_done} fetched, {n_skip} skipped), '
          f'{len(by_uid)} uids', file=sys.stderr)


if __name__ == '__main__':
    main()
