# Download pipeline

How data gets from start.gg into a data branch. Code: `scripts/common/download.py`
(driver), `scripts/common/download_policy.py` (decisions), `scripts/common/redownload_matches_v2.py`
(sets), `scripts/Japan/update_class_data.py` (class separation).

## Nightly run

The spsp deployment script (`deploy/update_and_deploy.sh` in the spsp repository) runs
every 3 hours on the production host, from the `smash_db_tournament/` checkout:

1. `scripts/common/download.py --country-code JP --start-date <today> --finish-date <today-14d> ...`
   downloads tournaments whose end date falls in the last two weeks.
2. `scripts/common/fetch_upcoming.py` writes the upcoming-tournament list for the seed tool.
3. `scripts/Japan/update_class_data.py --since-days 30` separates class brackets.
4. `scripts/common/curate.py --region Japan` refreshes `curated.json` for events whose files changed and
   rewrites `users_curated.jsonl` when the resolved prefectures change
   (see [data_model.md](data_model.md), "curated.json"). A failure here stops the run: the build refuses
   to guess when a judgement is missing.
5. Everything under `data/startgg/` is committed as `data: YYYY-MM-DD nightly` and pushed
   to `origin/data-Japan`. Nothing else is committed.

The token is passed through the environment (`STARTGG_TOKEN`), never on the command line.

## download.py

### Enumeration

Tournaments are listed with `get_tournaments_by_game_query` (game id 1386 = SSBU,
`countryCode` filter, sorted by `endAt` descending, 100 per page). For each tournament:

| Situation | Action | Where |
|---|---|---|
| Not finished (`state != 3` and `endAt` in the future) | skip | `tournament_skip_reason` |
| `startAt` newer than `--start-date` | skip | `tournament_skip_reason` |
| `endAt` older than `--finish-date` | stop enumeration (the list is sorted by `endAt`) | driver |
| `endAt` unset and `startAt` older than `--finish-date` | skip this one, keep going | driver |
| Listed in `done.csv`, all event files present, no re-download rule applies | skip (only move directories if the date changed) | `done_tournament_action` |
| Otherwise | download | |

The lower bound is checked against `endAt`, not `startAt`: organisers sometimes set
`startAt` to the announcement date, and multi-day events would otherwise fall outside
the window by the time they finish.

### Per event

For each event of a tournament (singles events of the game; the event list comes from
`get_tournament_events_query`):

1. Standings (`get_standings_query`, 100 per page) → `standings.json`, and every user in
   them is merged into `users.jsonl`.
2. Seeds of the first phase (`get_seeds_query`) → `seeds.json`.
3. Sets, via `redownload_matches_v2.fetch_all_sets`: phase groups are enumerated, sets
   fetched per phase group including games and character selections, duplicates
   across groups removed → `matches.json`.
4. `attr.json` with the tournament's place and time; the time window is corrected from
   set timestamps when `startAt` is clearly wrong (more than 3 days off).
5. The tournament entry is written to `tournaments.jsonl`.

An event whose files already exist at the same path is re-downloaded only if
(`existing_event_action`):

* a file is missing, or
* the event ended within the last `SPSP_REFRESH_DAYS` days (default 3) and the last
  download is at least `SPSP_REFRESH_MIN_HOURS` hours old (default 12) — organisers
  correct results shortly after an event, or
* it has no champion yet and ended less than 7 days ago (`RETRY_WINDOW_SEC`), or
* it is listed in the awaiting-resume registry (below).

### done.csv and the retry window

After downloading, a tournament is appended to `done.csv` unless one of its events has
no champion in `standings.json` and is still inside the 7-day window (or awaiting
resume). Such tournaments are picked up again on the next run; once 7 days pass with no
champion the tournament is marked done as is (typically a cancelled or never-finished
bracket).

### Awaiting resume

Tournaments that were interrupted (weather, venue trouble) and resume on a later date
are registered in a JSON file passed with `--awaiting-file`
(`build/data/awaiting_resume.json` in spsp). Their events are re-downloaded every run
regardless of the 7-day window and are not marked done until a champion exists, so the
ranking build can use partial results in the meantime.

### Date changes

When start.gg moves a tournament to another date, the event directory path changes.
The downloader detects an event id that already exists under a different path, removes
the old directory, and rewrites the `path` in `tournaments.jsonl`
(`plan_event_moves`, `[move]` / `[dedup]` lines in the log).

### Failures

`fetch_data_with_retries` retries each request `--max-retries` times (see
[startgg_api.md](startgg_api.md)). If an event still fails, it is written to
`failed_events.log` in the current directory and the run continues; the tournament is
not marked done, so it is retried on the next run. If the tournament listing itself
fails, the run aborts with a non-zero exit code so the nightly job reports it.

## Class separation (Japan)

`scripts/Japan/update_class_data.py` runs four steps in one process, each idempotent:

1. `fetch_event_phases` — for events of the last N days whose sets contain a phase named
   like a class bracket but which have no `phases.json` marking it, fetch the phase
   list and write `phases.json` with `is_class` flags.
2. `fetch_class_phase_standings` — for events with `has_class_phases`, fetch the
   standings of every class phase group → `class_phases/<phase_id>.json`.
3. `fetch_class_phase_players` — add `played_user_ids` to each phase group (users with
   at least one played set), used to drop no-shows.
4. `build_class_virtual_tournaments` — derive class-internal placements and write
   `class_phases/<Letter>_virtual/` (see [data_model.md](data_model.md)).

Steps 2–4 skip existing output, so running over the whole tree is cheap;
`scripts/Japan/manual/rescan_lower_class.py` forces a rescan.

## fetch_upcoming.py

Lists tournaments of the country starting within `--lookahead-days` (default 21) that
have 1-on-1 singles events, sorted by start time, and writes one JSON file (`--out`).
It reads nothing from the data branch.

## Regression tests

The download step is covered by record/replay tests in the spsp repository
(`tests/fetch/`): a cassette of real API responses is replayed with a frozen clock
and the produced tree must be byte-identical to the recording. Run it after any change
to `download.py`, `download_policy.py`, `redownload_matches_v2.py` or the writers:

```sh
cd <spsp>; SPSP_DL_SCRIPTS=<path to smash_db_tournament> python3 tests/fetch/dl_golden.py replay
```

Unit tests of the decision table: `tests/fetch/test_dl_policy.py`.
