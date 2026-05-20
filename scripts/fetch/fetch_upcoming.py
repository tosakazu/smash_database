#!/usr/bin/env python3
"""Fetch upcoming SSBU tournaments (JP, next 3 weeks) for seed picker UI.

Output: site/data/upcoming.json  (sorted by startAt asc).

Filters:
  - countryCode = "JP"
  - videogameIds = [1386] (SSBU)
  - startAt >= now AND startAt <= now + 21 days
  - 1on1 (type == 1) singles events only

Each entry: { tournament_id, tournament_slug, tournament_name, start_at, city,
              num_attendees, url, events: [{event_id, event_slug, event_name,
              num_entrants, type, start_at}] }

Usage:
    python3 scripts/fetch/fetch_upcoming.py \
        --token "$(cat .startgg_token)" \
        --out site/data/upcoming.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.utils import (  # type: ignore  # noqa: E402
    fetch_data_with_retries,
    set_api_parameters,
    set_retry_parameters,
    FetchError,
)

# SSBU
SSBU_VIDEOGAME_ID = 1386
# 1on1 singles event type (start.gg: 1 = singles, 5 = teams)
SINGLES_EVENT_TYPE = 1
# 3 weeks
LOOKAHEAD_DAYS = 21
API_DELAY_SEC = 0.6


def upcoming_tournaments_query() -> str:
    """Tournaments query with afterDate/beforeDate + events list incl. type/numEntrants.

    Note: `event.type` returns null for events that have not yet started seeding in
    some cases on start.gg. We still rely on it because seed UI needs type==1
    filtering.
    """
    return """query UpcomingTournaments(
      $perPage: Int!, $page: Int!, $gameId: ID!,
      $countryCode: String!, $afterDate: Timestamp!, $beforeDate: Timestamp!
    ) {
      tournaments(query: {
        perPage: $perPage,
        page: $page,
        sortBy: "startAt asc",
        filter: {
          videogameIds: [$gameId],
          countryCode: $countryCode,
          afterDate: $afterDate,
          beforeDate: $beforeDate,
          published: true,
          upcoming: true
        }
      }) {
        pageInfo { totalPages total }
        nodes {
          id
          name
          slug
          startAt
          endAt
          isOnline
          city
          countryCode
          venueName
          numAttendees
          url
          events {
            id
            name
            slug
            startAt
            numEntrants
            type
            videogame { id }
          }
        }
      }
    }"""


def fetch_upcoming_tournaments(
    country_code: str, after_ts: int, before_ts: int, per_page: int = 30
) -> list[dict]:
    """Paginate through tournaments matching filter; returns raw nodes."""
    all_nodes: list[dict] = []
    seen = set()
    page = 1
    total_pages = None
    max_pages = 50  # safety
    while page <= max_pages:
        resp = fetch_data_with_retries(
            upcoming_tournaments_query(),
            {
                "perPage": per_page,
                "page": page,
                "gameId": SSBU_VIDEOGAME_ID,
                "countryCode": country_code,
                "afterDate": after_ts,
                "beforeDate": before_ts,
            },
        )
        if not isinstance(resp, dict) or "data" not in resp or resp["data"] is None:
            raise FetchError(f"Upcoming tournaments response missing 'data': {resp}")
        tdata = resp["data"].get("tournaments") or {}
        nodes = tdata.get("nodes") or []
        page_info = tdata.get("pageInfo") or {}
        if total_pages is None:
            total_pages = page_info.get("totalPages")
        for n in nodes:
            tid = n.get("id")
            if tid is None or tid in seen:
                continue
            seen.add(tid)
            all_nodes.append(n)
        if not nodes:
            break
        if total_pages is not None and page >= total_pages:
            break
        page += 1
        time.sleep(API_DELAY_SEC)
    return all_nodes


def filter_singles_events(events: list[dict]) -> list[dict]:
    """Return SSBU singles events only."""
    out = []
    for e in events or []:
        vg = (e.get("videogame") or {}).get("id")
        # videogame may be null for some side events; require explicit SSBU tag here.
        if vg is None or int(vg) != SSBU_VIDEOGAME_ID:
            continue
        etype = e.get("type")
        # Accept type==1 (singles) or null (some upcoming events not yet typed).
        # Be conservative and only include explicit singles to avoid teams.
        if etype is None or int(etype) != SINGLES_EVENT_TYPE:
            continue
        out.append(e)
    return out


def normalize_tournament(node: dict) -> dict | None:
    events = filter_singles_events(node.get("events") or [])
    if not events:
        return None
    # Slug normalization (start.gg returns "tournament/<slug>")
    raw_slug = node.get("slug") or ""
    tslug = raw_slug
    if tslug and not tslug.startswith("tournament/"):
        tslug = "tournament/" + tslug
    out_events = []
    for e in events:
        e_slug = e.get("slug") or ""
        out_events.append({
            "event_id": e.get("id"),
            "event_slug": e_slug,
            "event_name": e.get("name") or "",
            "num_entrants": e.get("numEntrants") or 0,
            "type": e.get("type"),
            "start_at": e.get("startAt"),
        })
    # Sort events by entrants desc as secondary ordering
    out_events.sort(key=lambda x: -(x.get("num_entrants") or 0))
    return {
        "tournament_id": node.get("id"),
        "tournament_slug": tslug,
        "tournament_name": node.get("name") or "",
        "start_at": node.get("startAt"),
        "end_at": node.get("endAt"),
        "is_online": bool(node.get("isOnline")),
        "city": node.get("city") or "",
        "venue_name": node.get("venueName") or "",
        "country_code": node.get("countryCode") or "",
        "num_attendees": node.get("numAttendees") or 0,
        "url": node.get("url") or "",
        "events": out_events,
    }


def build_upcoming(country_code: str, lookahead_days: int) -> list[dict]:
    now = datetime.utcnow()
    after_ts = int(now.timestamp())
    before_ts = int((now + timedelta(days=lookahead_days)).timestamp())
    print(
        f"[fetch_upcoming] country={country_code} window=[{after_ts}, {before_ts}] "
        f"({now.isoformat()}Z + {lookahead_days}d)",
        flush=True,
    )
    nodes = fetch_upcoming_tournaments(country_code, after_ts, before_ts)
    print(f"[fetch_upcoming] fetched {len(nodes)} tournaments raw", flush=True)
    out = []
    for n in nodes:
        norm = normalize_tournament(n)
        if norm is None:
            continue
        out.append(norm)
    out.sort(key=lambda t: (t.get("start_at") or 0))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="https://api.start.gg/gql/alpha")
    parser.add_argument("--country", default="JP")
    parser.add_argument("--lookahead-days", type=int, default=LOOKAHEAD_DAYS)
    parser.add_argument(
        "--out",
        default=str(Path(ROOT_DIR).parent / "site" / "data" / "upcoming.json"),
    )
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=int, default=10)
    args = parser.parse_args()

    set_retry_parameters(args.max_retries, args.retry_delay)
    set_api_parameters(args.url, args.token)

    upcoming = build_upcoming(args.country, args.lookahead_days)
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": int(datetime.utcnow().timestamp()),
        "country_code": args.country,
        "lookahead_days": args.lookahead_days,
        "count": len(upcoming),
        "tournaments": upcoming,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(
        f"[fetch_upcoming] wrote {len(upcoming)} tournaments → {out_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
