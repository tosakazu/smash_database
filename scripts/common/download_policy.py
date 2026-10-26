"""download_policy — the "fetch / skip / mark done" decisions of download.py (no side effects).

The tournament loop in download.py mixes API calls, file moves and writes with the decisions, which is hard to read,
and the decision branches could not be tested in isolation. Only the decisions live here (input: facts, output: action and reason).
Behaviour is unchanged from before the extraction (decision table in tests/fetch/test_dl_policy.py, full-pipeline replay in tests/fetch/dl_golden.py).
"""
from __future__ import annotations

RETRY_WINDOW_SEC = 7 * 86400   # how long to keep re-fetching tournaments without a champion (7 days after the end)


def tournament_skip_reason(tournament_state, end_timestamp, timestamp, now_ts, start_date_ts):
    """Reason to skip at the tournament-list stage. None means process it.
    - state == 3 (COMPLETED): results are final even before endAt, so take it
    - tournaments newer than start_date (upper bound of the fetch window) are skipped
    """
    if tournament_state != 3 and (end_timestamp is None or end_timestamp > now_ts):
        return "not_finished"
    if start_date_ts is not None and timestamp > start_date_ts:
        return "newer_than_start_date"
    return None


def quick_tournament_action(end_timestamp, timestamp, finish_ts, start_date_ts, is_done):
    """Decision at the tournament-list stage for --quick (the light mode run every 15 minutes).
    Returns ("stop", reason) = end the listing / ("skip", reason) / (None, None) = process it.
    - the window is checked first, by endAt (the listing is sorted "endAt desc"), so the run stops at the first
      tournament that ended before finish_ts even if it is done (the full mode only stops at one that is not done)
    - the tournament's own state is not looked at: organizers often leave it CREATED after every event has finished
      (e.g. the weekday Ueno Smacomi). Each event's state is checked instead (quick_event_state_action)
    - tournaments in done.csv are never re-fetched (no post-completion refresh, no champion retry, no directory moves)
    """
    if (end_timestamp or timestamp) < finish_ts:
        if end_timestamp is None:
            return "skip", "endAt unset & older than finish_date"
        return "stop", "older than finish_date"
    if start_date_ts is not None and timestamp > start_date_ts:
        return "skip", "newer than start_date"
    if is_done:
        return "skip", "already downloaded"
    return None, None


def quick_event_state_action(event_state):
    """--quick: only events that start.gg reports as COMPLETED are fetched. A tournament with an event that is not
    COMPLETED yet is not marked done (the next run, or the full mode, takes it)."""
    return (None, None) if event_state == "COMPLETED" else ("skip", f"event not completed ({event_state})")


def quick_event_action(files_complete):
    """--quick: an event whose files are all present at the same path is not re-fetched (even without a champion:
    the 7-day champion retry and awaiting resume are left to the full mode)."""
    return ("skip", "files present") if files_complete else ("download", "files missing")


def done_tournament_action(entry, events_complete, champ_retry, recent_refresh):
    """What to do with a tournament listed in done.csv.
    Returns: ("skip", reason) = do not re-fetch (only check directory moves) / ("redownload", reason)
    """
    if entry and events_complete and not champ_retry and not recent_refresh:
        return "skip", "already downloaded"
    if champ_retry:
        return "redownload", "marked done but champion missing within 7d"
    if recent_refresh:
        return "redownload", "is marked done but recent refresh window"
    return "redownload", "is marked done but files are missing"


def plan_event_moves(entry, new_path_for):
    """For a done tournament, return events stored under an old date directory and their destinations: [(event, old_path, new_path)].
    new_path_for(event) gives the directory name for the current date. The caller checks whether old_path actually exists."""
    moves = []
    for ev in (entry or {}).get("events", []):
        old_path = ev.get("path", "")
        new_path = new_path_for(ev)
        if old_path and new_path and old_path != new_path:
            moves.append((ev, old_path, new_path))
    return moves


def existing_event_action(files_complete, needs_recent_refresh, has_champion, ref_end_ts, now_ts, awaiting):
    """Whether to re-fetch when the event's files are all present at the same path.
    Returns: ("skip", reason) / ("download", reason). has_champion is ignored when needs_recent_refresh.
    """
    if not files_complete:
        return "download", "files missing"
    if needs_recent_refresh:
        return "download", "recent refresh"
    if has_champion:
        return "skip", "champion present"
    in_retry_window = bool(ref_end_ts) and (now_ts - ref_end_ts) < RETRY_WINDOW_SEC
    if not in_retry_window and not awaiting:
        return "skip", "no champion but retry window passed"
    if awaiting:
        return "download", "awaiting resume"
    return "download", "no champion within retry window"


def champion_missing_reason(has_champion, awaiting, ref_end_ts, now_ts):
    """After downloading, the reason to keep the event as a re-fetch target instead of marking done (None = do not keep)."""
    if has_champion:
        return None
    if awaiting:
        return "awaiting"
    if ref_end_ts and (now_ts - ref_end_ts) < RETRY_WINDOW_SEC:
        return "pending"
    return None
