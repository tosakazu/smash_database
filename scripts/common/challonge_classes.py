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
  python -m scripts.common.challonge_classes mark-done --region Japan --done-in done_ids.txt
  (if a TO deleted a class meanwhile, the Worker answers status "deleted": its directory is removed again,
   so commit and push once more)
Environment: CHALLONGE_CLIENT_ID / CHALLONGE_CLIENT_SECRET (fetch; the SPSP Challonge app, API v2.1), SPSP_CLASS_DONE_KEY (mark-done). Waitlist: --waitlist-url (default
$SPSP_CLASS_WAITLIST_URL or https://spsp.games/api/class_waitlist) or --waitlist-file (a saved response, for tests).
Run from the repository root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.common.utils import read_tournaments_jsonl, write_json_compact, write_json_pretty  # noqa: E402

DEFAULT_WAITLIST_URL = "https://spsp.games/api/class_waitlist"
DEFAULT_API_URL = "https://spsp.games/api"
CHALLONGE_API = "https://api.challonge.com/v2.1"
CHALLONGE_TOKEN_URL = "https://api.challonge.com/oauth/token"
CHALLONGE_HEADERS = {"Accept": "application/json", "User-Agent": "spsp-ranking (https://spsp.games)"}
SOURCE_FILE = "challonge.json"
CLASS_MATCHES_FILE = "class_matches.json"
_MISC_RE = re.compile(r"^\s*startgg:(\d+)\s*$")
TIMEOUT = 60


# ── pure parts (tested without the network) ──

def startgg_user_id(misc) -> int | None:
    m = _MISC_RE.match(misc or "")
    return int(m.group(1)) if m else None


def parse_challonge(tournament: dict, participants: list[dict], matches: list[dict]) -> dict:
    """Challonge API v2.1 (JSON:API) tournament / participants / matches → the fields used here."""
    t = tournament.get("data", tournament)
    ta = t.get("attributes") or {}
    parts = []
    for p in participants:
        a = p.get("attributes") or {}
        parts.append({"challonge_id": int(p["id"]), "name": a.get("name"),
                      "misc": a.get("misc"), "user_id": startgg_user_id(a.get("misc")),
                      "final_rank": a.get("final_rank"), "seed": a.get("seed")})
    uid_by_pid = {p["challonge_id"]: p["user_id"] for p in parts}
    out_matches = []
    for m in matches:
        a = m.get("attributes") or {}
        if a.get("state") != "complete" or a.get("winner_id") is None:
            continue
        pts = {int(x["participant_id"]): x.get("scores") or [] for x in a.get("points_by_participant") or []}
        winner = int(a["winner_id"])
        loser = next((x for x in pts if x != winner), None)
        # games won in the set (Challonge keeps one number per reported set; a class set is one set)
        w_score = sum(pts.get(winner) or []) if pts.get(winner) else None
        l_score = sum(pts.get(loser) or []) if loser is not None and pts.get(loser) else None
        out_matches.append({"challonge_id": int(m["id"]), "round": a.get("round"), "scores": a.get("scores"),
                            "winner_user_id": uid_by_pid.get(winner), "loser_user_id": uid_by_pid.get(loser),
                            "winner_score": w_score, "loser_score": l_score})
    return {"id": int(t["id"]), "url": ta.get("full_challonge_url"), "name": ta.get("name"),
            "state": ta.get("state"), "tournament_type": ta.get("tournament_type"),
            "participants": parts, "matches": out_matches}


def standings_from(parsed: dict) -> tuple[list[dict], list[str], list[int]]:
    """[{placement, user_id}] sorted; the names of participants left out (no start.gg id / no rank); and the start.gg
    ids that appeared more than once (the same misc on two participants: only the better placement is kept)."""
    best, left_out, dups = {}, [], []
    for p in parsed["participants"]:
        if p["user_id"] is None or p["final_rank"] is None:
            left_out.append(p["name"] or str(p["challonge_id"]))
            continue
        uid, rank = p["user_id"], int(p["final_rank"])
        if uid in best:
            dups.append(uid)
            rank = min(rank, best[uid])
        best[uid] = rank
    rows = sorted(({"placement": r, "user_id": u} for u, r in best.items()), key=lambda r: (r["placement"], r["user_id"]))
    return rows, left_out, dups


_BRACKET_TYPE = {"single elimination": "SINGLE_ELIMINATION", "double elimination": "DOUBLE_ELIMINATION",
                 "round robin": "ROUND_ROBIN", "swiss": "SWISS"}


def class_matches_from(parsed: dict) -> list[dict]:
    """The Challonge sets as rows of a start.gg matches.json (same keys; start.gg-only fields are null).
    Written to class_matches.json in the virtual directory: the ranking build reads it into the parent event's
    matches as class-bracket sets, the way start.gg class sets already are (they live in the parent's matches.json).
    Sets with a participant that is not tied to a start.gg player are left out."""
    btype = _BRACKET_TYPE.get((parsed.get("tournament_type") or "").lower())
    rows = []
    for m in parsed["matches"]:
        if m["winner_user_id"] is None or m["loser_user_id"] is None or m["winner_user_id"] == m["loser_user_id"]:
            continue
        rows.append({
            "match_id": m["challonge_id"], "winner_id": m["winner_user_id"], "loser_id": m["loser_user_id"],
            "winner_score": m.get("winner_score"), "loser_score": m.get("loser_score"),
            "round_text": None, "round": m.get("round"), "phase": None, "phase_id": None, "phase_name": None,
            "phase_order": None, "phase_num_seeds": None, "phase_bracket_type": btype, "phase_top_n": None,
            "bracket_label": None, "winners_top": None, "losers_top": None, "global_round": None,
            "global_top_x": None, "global_bracket_label": None, "phase_group_id": None,
            "phase_group_start_at": None, "wave_id": None, "wave": None, "wave_start_at": None,
            "dq": False, "cancel": False, "state": 3, "started_at": None, "completed_at": None, "details": [],
            "source": "challonge",
        })
    return rows


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


def challonge_token(client_id: str, client_secret: str) -> str:
    """OAuth client-credentials token of the SPSP Challonge app with the application scope: it reads every tournament
    created through the app (TOs authorise the app with "Log in with Challonge" on the SPSP site and create the class
    bracket with their own token), under /v2.1/application/tournaments/..."""
    r = requests.post(CHALLONGE_TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id,
                                                 "client_secret": client_secret,
                                                 "scope": "application:manage"},
                      headers=CHALLONGE_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def _get_all(path: str, headers: dict) -> list[dict]:
    out, page = [], 1
    while True:
        r = requests.get(f"{CHALLONGE_API}{path}", params={"page": page, "per_page": 100}, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data") or []
        out += data
        if len(data) < 100:
            return out
        page += 1


def fetch_challonge(challonge_id, token: str) -> dict:
    h = {**CHALLONGE_HEADERS, "Authorization": f"Bearer {token}", "Authorization-Type": "v2",
         "Content-Type": "application/vnd.api+json"}
    base = f"/application/tournaments/{int(challonge_id)}"
    r = requests.get(f"{CHALLONGE_API}{base}.json", headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_challonge(r.json(), _get_all(f"{base}/participants.json", h), _get_all(f"{base}/matches.json", h))


def event_paths(region_dir: Path) -> dict[int, str]:
    out = {}
    for t in read_tournaments_jsonl(str(region_dir / "tournaments.jsonl")).values():
        for ev in t.get("events") or []:
            if ev.get("event_id") is not None and ev.get("path"):
                out[int(ev["event_id"])] = ev["path"]
    return out


def cmd_fetch(args) -> int:
    client_id, client_secret = os.environ.get("CHALLONGE_CLIENT_ID"), os.environ.get("CHALLONGE_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("CHALLONGE_CLIENT_ID / CHALLONGE_CLIENT_SECRET are not set — skipping Challonge class brackets")
        return 0
    from scripts.common.region import class_support, load_region_classifier
    cls = class_support(load_region_classifier(args.region))
    if cls is None:
        print(f"Region {args.region} does not handle class brackets — nothing to do")
        return 0
    region_dir = Path("data/startgg") / args.region.replace(" ", "_")
    items = load_waitlist(args)
    paths = event_paths(region_dir)
    done, n_wait, n_warn, token = [], 0, 0, None
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
            if token is None:
                token = challonge_token(client_id, client_secret)
            parsed = fetch_challonge(it["challonge_id"], token)
        except Exception as e:   # network, HTTP error, or an unexpected response shape: skip this one, keep the rest
            # never print the exception text: keep anything credential-related out of the (public) Actions log
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
        standings, left_out, dups = standings_from(parsed)
        if left_out:
            print(f"  WARN {label}: {len(left_out)} participant(s) without startgg:<id> or rank left out: {left_out[:10]}")
            n_warn += 1
        if dups:
            print(f"  WARN {label}: start.gg id(s) on more than one participant (kept the better placement): {dups[:10]}")
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
            # the sets, learned as part of the parent event (the build adds them to the parent's matches, is_class)
            write_json_compact(vdir / CLASS_MATCHES_FILE, class_matches_from(parsed))
            write_json_pretty(vdir / SOURCE_FILE, source)
            print(f"  wrote {vdir} ({len(standings)} standings)")
        done.append(it.get("id"))
    if args.done_out and not args.dry_run:
        Path(args.done_out).write_text("".join(f"{i}\n" for i in done), encoding="utf-8")
    print(f"[challonge] written {len(done)}, waiting {n_wait}, warnings {n_warn}")
    return 0


def remove_ingested(region_dir: Path, class_ids: set[int]) -> list[Path]:
    """Delete the virtual directories written for these SPSP class ids (found by challonge.json spsp_class_id)."""
    removed = []
    for src in (region_dir / "events").glob(f"**/class_phases/*_virtual/{SOURCE_FILE}"):
        try:
            cid = json.loads(src.read_text(encoding="utf-8")).get("spsp_class_id")
        except ValueError:
            continue
        if cid is not None and int(cid) in class_ids:
            shutil.rmtree(src.parent)
            removed.append(src.parent)
    return removed


def cmd_mark_done(args) -> int:
    ids = [l.strip() for l in Path(args.done_in).read_text(encoding="utf-8").splitlines() if l.strip()] \
        if Path(args.done_in).exists() else []
    if not ids:
        print("[challonge] nothing to mark done")
        return 0
    key = os.environ.get("SPSP_CLASS_DONE_KEY")
    if not key:
        raise SystemExit("ERROR: SPSP_CLASS_DONE_KEY is not set")
    failed, deleted = 0, set()
    for i in ids:
        # the Worker takes the body as text/plain (like the site's other /api calls) and always answers HTTP 200;
        # the result is in "ok" / "error.code" (auth_failed / bad_request / not_found / internal).
        # status "deleted" = the TO deleted the class while it was being ingested (the Challonge bracket is gone too):
        # the Worker did not mark it done, and what was just ingested must be thrown away
        r = requests.post(args.api_url, data=json.dumps({"action": "class_done", "key": key, "id": int(i)}),
                          headers={"Content-Type": "text/plain"}, timeout=TIMEOUT)
        try:
            body = r.json()
        except ValueError:
            body = {}
        ok = r.ok and body.get("ok")
        if ok and body.get("status") == "deleted":
            deleted.add(int(i))
            print(f"  class_done {i}: deleted by the TO — removing what was ingested")
            continue
        err = (body.get("error") or {}).get("code") or f"HTTP {r.status_code}"
        print(f"  class_done {i}: {'ok' if ok else f'FAILED ({err})'}")
        failed += 0 if ok else 1
    if deleted:
        region_dir = Path("data/startgg") / args.region.replace(" ", "_")
        for d in remove_ingested(region_dir, deleted):
            print(f"  removed {d}")
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
    d.add_argument("--region", required=True, help="where to remove brackets the TO deleted meanwhile")
    d.add_argument("--api-url", default=os.environ.get("SPSP_API_URL", DEFAULT_API_URL))
    args = ap.parse_args(argv)
    return cmd_fetch(args) if args.cmd == "fetch" else cmd_mark_done(args)


if __name__ == "__main__":
    sys.exit(main())
