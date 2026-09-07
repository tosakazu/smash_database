# Known issues and open requests

Kept short; the authoritative list is the GitHub issue tracker. Items here are the ones
a contributor is likely to hit or to want to pick up.

## Data

* **No timestamp in `tournaments.jsonl`** (issue #13). Consumers must open each event's
  `attr.json` to sort by date; the path carries the UTC start date only.
* **No `fetched_at` in `attr.json`** (issue #55). The last-download time is only
  available as the file's mtime, which the downloader also uses for the refresh window.
  Adding the field changes every `attr.json` and the replay fixture; do it in one
  coordinated change.
* **`user_id` can be `null`** in standings, seeds and sets for entrants without a
  start.gg account (teams, guests). Consumers must skip them.
* **Class brackets whose phase names do not match the pattern** (`[BCDE]クラス`,
  `B class`) are not separated and stay mixed into the parent's `matches.json`.
* **`labels` in `attr.json` is empty** for real events. Earlier versions carried
  LLM-generated tags (`registration_type`, `event_type`, `game_rule`); those were
  unreliable and are no longer produced.
* **Wave / pool semantics** (issue #30): `wave`, `wave_start_at`, `phase_group_id` and
  `phase_group_start_at` on each set identify the pool and its scheduled time; the
  seeding tool's pool/wave logic itself lives in spsp, not here.

## Validation status (2026-09-08, `validate_data.py --region Japan`)

526 events out of ~4,600 report an error; none block the ranking build, which
tolerates them:

| Count | Finding | Meaning |
|---|---|---|
| 202 | sets with missing winner/loser above threshold | sets whose entrants have no start.gg user (teams, guests) or brackets left unfinished |
| 162 | `matches.json` missing | events whose set download failed at the time and then fell out of the 14-day window (spread over 2019–2026, 13 of them in 2026). `manual/refetch_incomplete_events.py` can restore them; note that doing so adds sets, i.e. changes ranking inputs |
| 156 | standings with many `null` user ids | events where most entrants had no start.gg account |
| 6 | set ids not in standings | organiser edits after the download |

## Scripts

* `manual/download_specific_event.py` adds an event to an existing tournament in
  memory only; the new event is not written back to `tournaments.jsonl`
  (`fix/check_events_in_tournaments.py` repairs the index afterwards). Its header
  comment about `get_event_details_by_slug_query` is stale; it uses
  `get_event_details_by_tournament_query`.
* `utils.fetch_data_with_retries` sends `variables` as a JSON-encoded string. start.gg
  accepts it; a stricter GraphQL server would not.
* `download.py` writes `failed_events.log` into the current directory, so it must be run
  from the repository root to find it later.
* The manual and fix tools have no automated tests beyond `--help`; the replay test
  only covers `download.py` and what it imports.

## Repository

* Data branches grow with every nightly commit (both index files are rewritten). See
  [operations.md](operations.md), "Squashing history".
* The repository is public; `users.jsonl` contains the location, pronoun and linked
  account handles that players made public on their start.gg profiles, nothing else.
