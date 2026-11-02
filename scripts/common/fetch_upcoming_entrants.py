#!/usr/bin/env python3
"""Fetch the entrant list for each singles event in upcoming.json.

Output: --out (in spsp, site/data/upcoming_entrants.json). Moved from spsp/download to smash_database on 2026-09-08.
{
  "generated_at": <ts>,
  "events": {
    "<event_id>": {
      "tournament_id", "tournament_name", "event_name", "start_at",
      "num_entrants", "is_online", "is_weekend",
      "entrants": [{"uid": <user.id|null>, "tag": <gamerTag>, "seed": <seedNum>, "pg": <phaseGroup id>}, ...],
      "bracket": {"phase_id", "phase", "pools": {"<phaseGroup id>": {"ident", "url"}}}
    }
  },
  "by_uid": {"<uid>": [<event_id>, ...]}
}

seed / pg / bracket are only present when the first phase's bracket and seeding are both published on start.gg
(event.publishing.bracket / .seeding, true or per phase) and the phase has seeds. Unpublished phases are not returned by the
API at all. "First phase" = the lowest phaseOrder that is not an exhibition (the pools players start in).
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
    publishing
    phases { id name phaseOrder isExhibition }
    entrants(query: {perPage: $perPage, page: $page}) {
      pageInfo { totalPages }
      nodes { name participants { player { gamerTag } user { id } } }
    }
  }
}'''


PHASE_GROUPS_QUERY = '''
query($pid: ID!, $page: Int!) {
  phase(id: $pid) {
    phaseGroups(query: {page: $page, perPage: 64}) {
      pageInfo { totalPages }
      nodes { id displayIdentifier bracketUrl }
    }
  }
}'''

PHASE_SEEDS_QUERY = '''
query($pid: ID!, $page: Int!, $perPage: Int!) {
  phase(id: $pid) {
    seeds(query: {page: $page, perPage: $perPage}) {
      pageInfo { totalPages }
      nodes { seedNum phaseGroup { id } entrant { participants { player { gamerTag } user { id } } } }
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


def fetch_event_entrants(token, eid, meta=None):
    """Entrants [{uid, tag}]. If meta (dict) is given, the event's publishing and phases are stored in it."""
    out = []
    page = 1
    while True:
        data = gql(token, ENTRANTS_QUERY, {'eid': eid, 'page': page, 'perPage': 64})
        ev = data.get('event') or {}
        if meta is not None and page == 1:
            meta['publishing'] = ev.get('publishing')
            meta['phases'] = ev.get('phases') or []
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


def _published(flag, phase_id):
    """event.publishing.bracket / .seeding is either a bool for the whole event or {"<phase id>": bool}."""
    if isinstance(flag, dict):
        return bool(flag.get(str(phase_id)))
    return bool(flag)


def first_phase(phases):
    """The phase players start in: lowest phaseOrder among non-exhibition phases (list order breaks ties)."""
    cand = [(p.get('phaseOrder') if p.get('phaseOrder') is not None else 10 ** 6, i, p)
            for i, p in enumerate(phases or []) if p and p.get('id') is not None and not p.get('isExhibition')]
    return min(cand)[2] if cand else None


def _paged(token, query, variables, path):
    """All nodes of a paged connection (path = keys from data to the connection)."""
    nodes, page = [], 1
    while True:
        data = gql(token, query, dict(variables, page=page))
        conn = data
        for k in path:
            conn = (conn or {}).get(k)
        nodes.extend((conn or {}).get('nodes') or [])
        if page >= ((conn or {}).get('pageInfo') or {}).get('totalPages', 0):
            return nodes
        page += 1
        time.sleep(DELAY)


def fetch_event_bracket(token, meta, entrants):
    """If the first phase's bracket and seeding are published and it has seeds: add seed / pg to entrants (in place)
    and return {"phase_id", "phase", "pools": {pg id: {"ident", "url"}}}. Otherwise None (entrants unchanged)."""
    pub = meta.get('publishing') or {}
    ph = first_phase(meta.get('phases'))
    if not ph or not _published(pub.get('bracket'), ph['id']) or not _published(pub.get('seeding'), ph['id']):
        return None
    groups = _paged(token, PHASE_GROUPS_QUERY, {'pid': ph['id']}, ['phase', 'phaseGroups'])
    time.sleep(DELAY)
    seeds = _paged(token, PHASE_SEEDS_QUERY, {'pid': ph['id'], 'perPage': 100}, ['phase', 'seeds'])
    by_uid, by_tag = {}, {}
    for e in entrants:
        if e.get('uid') is not None:
            by_uid[e['uid']] = e
        else:
            by_tag.setdefault(e.get('tag') or '', []).append(e)
    n = 0
    for sd in seeds:
        if not sd or sd.get('seedNum') is None or not (sd.get('phaseGroup') or {}).get('id'):
            continue
        uid, tag = None, ''
        for p in ((sd.get('entrant') or {}).get('participants') or []):
            if p and p.get('user') and p['user'].get('id'):
                uid = p['user']['id']
            if p and p.get('player') and p['player'].get('gamerTag'):
                tag = p['player']['gamerTag']
        e = by_uid.get(uid) if uid is not None else None
        if e is None and uid is None and by_tag.get(tag):
            e = by_tag[tag].pop(0)
        if e is None:
            continue
        e['seed'] = sd['seedNum']
        e['pg'] = sd['phaseGroup']['id']
        n += 1
    if not n:
        return None
    return {'phase_id': ph['id'], 'phase': ph.get('name'),
            'pools': {str(g['id']): {'ident': g.get('displayIdentifier'), 'url': g.get('bracketUrl')} for g in groups if g and g.get('id')}}


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
            meta = {}
            try:
                entrants = fetch_event_entrants(token, eid, meta)
            except Exception as e:
                print(f'  SKIP eid={eid}: {e}', file=sys.stderr)
                n_skip += 1
                continue
            # Seeds and pools (only when the bracket and seeding are published). A failure here keeps the entrants
            try:
                bracket = fetch_event_bracket(token, meta, entrants)
            except Exception as e:
                print(f'  bracket skipped eid={eid}: {e}', file=sys.stderr)
                bracket = None
                for e2 in entrants:
                    e2.pop('seed', None)
                    e2.pop('pg', None)
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
            if bracket:
                events[str(eid)]['bracket'] = bracket
                print(f'  bracket: {bracket["phase"]} ({len(bracket["pools"])} pools)', file=sys.stderr)
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
        if not args.prev:
            raise OSError('no --prev')
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
