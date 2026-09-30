#!/usr/bin/env python3
"""Lower-class brackets run on Challonge (created from the SPSP site) → virtual class events.

A TO creates a class bracket (B/C/D/E) on Challonge from the SPSP page; when it is marked as counted, the
SPSP Worker lists it at GET <waitlist URL>:
    {"ok": true, "items": [{"id", "parent_event_id", "parent_tournament_id", "class_letter", "name",
                            "challonge_id", "challonge_url", "created_at"}]}
Each participant carries misc = "startgg:<start.gg user id>" (set by the SPSP page), which ties it to the player.

For every listed bracket whose Challonge state is "complete", this writes the same files as a start.gg class
bracket (build_class_virtual_tournaments.py):
    <parent event dir>/class_phases/<letter>_virtual/{attr.json, standings.json, matches.json}
plus challonge.json (the Challonge source: participants and matches with start.gg ids). The ranking build
treats the directory as a lower-class virtual event (placement scoring only; matches.json stays empty as for
start.gg classes, whose sets are learned through the parent event).
Brackets still in progress are left for the next run. If the same parent event already has a start.gg class
bracket with that letter, the start.gg one wins and the Challonge one is skipped with a warning.

Two steps, so that a bracket is marked done only after its files were pushed:
  python -m scripts.common.challonge_classes fetch --region Japan --done-out done_ids.txt
  (commit and push data-Japan)
  python -m scripts.common.challonge_classes mark-done --done-in done_ids.txt
Environment: CHALLONGE_API_KEY (fetch), SPSP_CLASS_DONE_KEY (mark-done). Waitlist: --waitlist-url (default
$SPSP_CLASS_WAITLIST_URL or https://spsp.games/api/class_waitlist) or --waitlist-file (a saved response, for tests).
Run from the repository root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.common.utils import read_tournaments_jsonl, write_json_compact, write_json_pretty  # noqa: E402

DEFAULT_WAITLIST_URL = "https://spsp.games/api/class_waitlist"
DEFAULT_API_URL = "https://spsp.games/api"
CHALLONGE_API = "https://api.challonge.com/v1"
SOURCE_FILE = "challonge.json"
_MISC_RE = re.compile(r"^\s*startgg:(\d+)\s*$")
TIMEOUT = 60


# ── pure parts (tested without the network) ──

def startgg_user_id(misc) -> int | None:
    m = _MISC_RE.match(misc or "")
    return int(m.group(1)) if m else None


def parse_challonge(tournament: dict) -> dict:
    """Challonge v1 tournament (with include_participants / include_matches) → the fields used here."""
    t = tournament.get("tournament", tournament)
    parts = []
    for p in t.get("participants") or []:
        p = p.get("participant", p)
        parts.append({"challonge_id": p.get("id"), "name": p.get("name") or p.get("display_name"),
                      "misc": p.get("misc"), "user_id": startgg_user_id(p.get("misc")),
                      "final_rank": p.get("final_rank"), "seed": p.get("seed")})
    uid_by_pid = {p["challonge_id"]: p["user_id"] for p in parts}
    matches = []
    for m in t.get("matches") or []:
        m = m.get("match", m)
        if m.get("state") != "complete" or m.get("winner_id") is None:
            continue
        matches.append({"challonge_id": m.get("id"), "round": m.get("round"), "scores_csv": m.get("scores_csv"),
                        "winner_user_id": uid_by_pid.get(m.get("winner_id")),
                        "loser_user_id": uid_by_pid.get(m.get("loser_id"))})
    return {"id": t.get("id"), "url": t.get("full_challonge_url") or t.get("url"), "name": t.get("name"),
            "state": t.get("state"), "tournament_type": t.get("tournament_type"),
            "participants": parts, "matches": matches}


def standings_from(parsed: dict) -> tuple[list[dict], list[str]]:
    """[{placement, user_id}] sorted, and the names of participants left out (no start.gg id / no rank)."""
    rows, left_out = [], []
    for p in parsed["participants"]:
        if p["user_id"] is None or p["final_rank"] is None:
            left_out.append(p["name"] or str(p["challonge_id"]))
            continue
        rows.append({"placement": int(p["final_rank"]), "user_id": p["user_id"]})
    rows.sort(key=lambda r: (r["placement"], r["user_id"]))
    return rows, left_out


def virtual_attr(parent_attr: dict, letter: str, class_letters, event_name_fn, num_entrants: int) -> dict:
    """Same fields and numbering as build_class_virtual_tournaments.py."""
    try:
        idx = list(class_letters).index(letter) + 1
    except ValueError:
        idx = 0
    ts = parent_attr.get("timestamp")
    return {
        "event_id": -(int(parent_attr.get("event_id") or 0) * 10 + idx),
        "tournament_name": parent_attr.get("tournament_name", ""),
        "event_name": event_name_fn(parent_attr.get("event_name", "Singles"), letter),
        "region": parent_attr.get("region"),
        "place": parent_attr.get("place"),
        "num_entrants": num_entrants,
        "offline": parent_attr.get("offline", True),
        "status": "completed",
        "timestamp": (ts + idx) if isinstance(ts, (int, float)) else ts,
        "end_timestamp": parent_attr.get("end_timestamp"),
        "version": "1.0",
        "url": parent_attr.get("url"),
    }


# ── I/O ──

def load_waitlist(args) -> list[dict]:
    if args.waitlist_file:
        data = json.loads(Path(args.waitlist_file).read_text(encoding="utf-8"))
    else:
        r = requests.get(args.waitlist_url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        raise SystemExit(f"ERROR: waitlist not ok: {str(data)[:300]}")
    return data.get("items") or []


def fetch_challonge(challonge_id, api_key: str) -> dict:
    r = requests.get(f"{CHALLONGE_API}/tournaments/{challonge_id}.json",
                     params={"api_key": api_key, "include_participants": 1, "include_matches": 1}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def event_paths(region_dir: Path) -> dict[int, str]:
    out = {}
    for t in read_tournaments_jsonl(str(region_dir / "tournaments.jsonl")).values():
        for ev in t.get("events") or []:
            if ev.get("event_id") is not None and ev.get("path"):
                out[int(ev["event_id"])] = ev["path"]
    return out


def cmd_fetch(args) -> int:
    api_key = os.environ.get("CHALLONGE_API_KEY")
    if not api_key:
        print("CHALLONGE_API_KEY is not set — skipping Challonge class brackets")
        return 0
    from scripts.common.region import class_support, load_region_classifier
    cls = class_support(load_region_classifier(args.region))
    if cls is None:
        print(f"Region {args.region} does not handle class brackets — nothing to do")
        return 0
    region_dir = Path("data/startgg") / args.region.replace(" ", "_")
    items = load_waitlist(args)
    paths = event_paths(region_dir)
    done, n_wait, n_warn = [], 0, 0
    print(f"[challonge] waitlist {len(items)}")
    for it in items:
        label = f"#{it.get('id')} {it.get('name')} ({it.get('challonge_url')})"
        letter = it.get("class_letter")
        if letter not in cls.CLASS_LETTERS:
            print(f"  WARN {label}: class letter {letter!r} is not one of {cls.CLASS_LETTERS}"); n_warn += 1
            continue
        parent_path = paths.get(int(it.get("parent_event_id") or 0))
        if not parent_path or not (Path(parent_path) / "attr.json").exists():
            print(f"  wait {label}: parent event {it.get('parent_event_id')} not downloaded yet"); n_wait += 1
            continue
        try:
            parsed = parse_challonge(fetch_challonge(it["challonge_id"], api_key))
        except (requests.RequestException, ValueError) as e:
            # the request URL carries the API key, so never print the exception text (it includes the URL)
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(f"  WARN {label}: Challonge fetch failed ({type(e).__name__}{f' HTTP {status}' if status else ''}) — next run retries")
            n_warn += 1
            continue
        if parsed["state"] != "complete":
            print(f"  wait {label}: Challonge state {parsed['state']}"); n_wait += 1
            continue
        vdir = Path(parent_path) / "class_phases" / f"{letter}_virtual"
        if (vdir / "attr.json").exists() and not (vdir / SOURCE_FILE).exists():
            print(f"  WARN {label}: {vdir} already holds a start.gg class bracket — keeping it, Challonge skipped")
            n_warn += 1
            continue
        standings, left_out = standings_from(parsed)
        if left_out:
            print(f"  WARN {label}: {len(left_out)} participant(s) without startgg:<id> or rank left out: {left_out[:10]}")
            n_warn += 1
        if not standings:
            print(f"  WARN {label}: no participant could be tied to a start.gg player — not written"); n_warn += 1
            continue
        parent_attr = json.loads((Path(parent_path) / "attr.json").read_text(encoding="utf-8"))
        attr = virtual_attr(parent_attr, letter, cls.CLASS_LETTERS, cls.class_virtual_event_name, len(parsed["participants"]))
        source = {"spsp_class_id": it.get("id"), "parent_event_id": it.get("parent_event_id"), **parsed}
        if args.dry_run:
            print(f"  DRY {label}: would write {vdir} ({len(standings)} standings, {len(parsed['matches'])} matches)")
        else:
            vdir.mkdir(parents=True, exist_ok=True)
            write_json_pretty(vdir / "attr.json", attr)
            write_json_compact(vdir / "standings.json", standings)
            write_json_compact(vdir / "matches.json", [])   # like start.gg classes: no learning on the virtual side
            write_json_pretty(vdir / SOURCE_FILE, source)
            print(f"  wrote {vdir} ({len(standings)} standings)")
        done.append(it.get("id"))
    if args.done_out and not args.dry_run:
        Path(args.done_out).write_text("".join(f"{i}\n" for i in done), encoding="utf-8")
    print(f"[challonge] written {len(done)}, waiting {n_wait}, warnings {n_warn}")
    return 0


def cmd_mark_done(args) -> int:
    ids = [l.strip() for l in Path(args.done_in).read_text(encoding="utf-8").splitlines() if l.strip()] \
        if Path(args.done_in).exists() else []
    if not ids:
        print("[challonge] nothing to mark done")
        return 0
    key = os.environ.get("SPSP_CLASS_DONE_KEY")
    if not key:
        raise SystemExit("ERROR: SPSP_CLASS_DONE_KEY is not set")
    failed = 0
    for i in ids:
        r = requests.post(args.api_url, json={"action": "class_done", "key": key, "id": int(i)}, timeout=TIMEOUT)
        ok = r.ok and (r.json() or {}).get("ok")
        print(f"  class_done {i}: {'ok' if ok else f'FAILED {r.status_code} {r.text[:200]}'}")
        failed += 0 if ok else 1
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="write the completed Challonge class brackets")
    f.add_argument("--region", required=True)
    f.add_argument("--waitlist-url", default=os.environ.get("SPSP_CLASS_WAITLIST_URL", DEFAULT_WAITLIST_URL))
    f.add_argument("--waitlist-file", default=None, help="read the waitlist from a saved response instead")
    f.add_argument("--done-out", default=None, help="write the ids of the brackets written, one per line")
    f.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("mark-done", help="tell the SPSP Worker which brackets were written (after the push)")
    d.add_argument("--done-in", required=True)
    d.add_argument("--api-url", default=os.environ.get("SPSP_API_URL", DEFAULT_API_URL))
    args = ap.parse_args(argv)
    return cmd_fetch(args) if args.cmd == "fetch" else cmd_mark_done(args)


if __name__ == "__main__":
    sys.exit(main())
