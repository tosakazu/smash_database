# Running a region

The complete guide for the person who keeps one region's data up to date (the
examples use North America / `US`). It covers, in this order: what you own, where
every file lives and who writes it, what each kind of data contains, every script you
will run, the daily routine, and what to do when something is off. You should not need
any other page for day-to-day work; `data_model.md` and `operations.md` go deeper if you
want it.

Contents

1. [What you own](#1-what-you-own)
2. [Setup](#2-setup)
3. [Where the data lives](#3-where-the-data-lives)
4. [What the data is](#4-what-the-data-is)
5. [The scripts](#5-the-scripts)
6. [Daily operation](#6-daily-operation)
7. [Changing the rules for your region](#7-changing-the-rules-for-your-region)
8. [When something is off](#8-when-something-is-off)
9. [Git rules](#9-git-rules)

---

## 1. What you own

| | North America |
|---|---|
| Branch | `data-North_America` — only you push to it. It is `main` (scripts, docs) plus your data |
| Directory | `data/startgg/North_America/` — everything under it is yours; nothing outside it is |
| Rules | `scripts/North_America/classify.py` — the region's judgements (section 7). It lives **on your branch, not on `main`**: you edit it directly, no pull request |
| Countries | `US`, `CA`, `MX`, `DO` all map to this region (`country_code2region()` in `scripts/common/utils.py`). Each run downloads **one** country (`--country-code`); run once per country if you cover several. Note that `upcoming.json` and `validation_baseline.json` are per region, not per country: `upcoming.json` holds the country of the *last* run only (`fetch_upcoming.py` takes one country), so if you cover more than the US and need all of them listed, say so and the file will be made per country |

Not yours: `main` (never push to it directly — that is where the shared scripts under
`scripts/common/` live, and changes there go through a pull request), other regions'
directories, the ranking build that reads this data (a separate repository).

## 2. Setup

```sh
git clone --single-branch --branch data-North_America git@github.com:tosakazu/smash_database.git
cd smash_database
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+

# start.gg API token (https://start.gg/admin/profile/developer). Never commit it, never put it on a command line.
mkdir -p ~/.config/smash_database
printf '%s\n' '<your token>' > ~/.config/smash_database/STARTGG_TOKEN
chmod 600 ~/.config/smash_database/STARTGG_TOKEN
```

The scripts look for the token in the environment variable `STARTGG_TOKEN`, then
`--token-file`, then `./STARTGG_TOKEN`, then `~/.config/smash_database/STARTGG_TOKEN`.
Every script runs from the repository root and finds its region's files by itself.
Linux is the reference environment. On macOS install `flock` first (`brew install flock`;
the runner uses it to refuse overlapping runs), and on Windows use WSL.

Then confirm the environment:

```sh
bash scripts/common/run_region.sh --country-code US --check --python .venv/bin/python3
```

It reports Python, dependencies, the token, the branch, GitHub and the start.gg API
one line each and ends with `→ ready`. Optionally
`git config gc.autoPackLimit 8` so a year of daily pushes does not leave the local
repository in dozens of pack files.

## 3. Where the data lives

```
data/startgg/North_America/                 one directory per region (the region name from the country code, spaces → _)
├── events/YYYY/MM/DD/<Tournament>/<Event>/ one directory per event (section 4.1)
│   ├── attr.json                           ← download.py       raw: what start.gg says about the event
│   ├── standings.json                      ← download.py       raw
│   ├── seeds.json                          ← download.py       raw
│   ├── matches.json                        ← download.py       raw (every set)
│   ├── derived.json                        ← derive.py         generated: the region's judgements
│   ├── phases.json                         ← update_class_data  raw, only for events that have a class bracket
│   └── class_phases/
│       ├── <phase_id>.json                 ← update_class_data  raw standings of one class bracket
│       └── <CLASS>_virtual/                ← update_class_data  generated: the class bracket as its own event
│           ├── attr.json  standings.json  seeds.json  matches.json  derived.json
├── done.csv                                ← download.py       tournament ids whose download is complete
├── done_events.csv                         ← download_specific_event.py (manual imports only)
├── tournaments.jsonl                       ← download.py       index: one line per tournament, with the event paths
├── users.jsonl                             ← download.py       index: one line per player seen in any event
├── users_derived.jsonl                     ← derive.py         generated: per-player judgements (empty for North America for now)
├── upcoming.json                           ← fetch_upcoming.py tournaments in the next 21 days
├── validation_baseline.json                ← validate_data.py  accepted counts of known inconsistencies
└── manual/                                 hand-maintained tables, optional (section 4.5)
    └── awaiting_resume.json
```

Rules that follow from this layout:

* **Three kinds of file.** *Raw* files are what came from start.gg and are never
  edited by hand (re-download instead). *Generated* files are rewritten from raw files
  by a script; editing them is pointless because the next run overwrites them. *Hand-
  maintained* files live only under `manual/` and nothing regenerates them.
* **Paths are stable.** An event's directory name is `events/<UTC date of startAt>/
  <tournament name>/<event name>` with spaces and slashes replaced by `_`. Once a
  directory exists it is never renamed, even if the organiser renames the event; the
  `tournaments.jsonl` line carries the path, so other tools find it there. If start.gg
  moves the start date, the downloader moves the directory and updates the index in
  the same run.
* **Everything under `data/startgg/North_America/` is committed**, generated files
  included — the point of the branch is that anyone can check it out and have the same
  dataset without running anything. The only things not committed are what
  `.gitignore` lists: `*.bak`, `*checkpoint*`, `users_ranked.jsonl`, `__pycache__`.
* **One writer per file.** The downloader owns the raw files and the index files;
  `derive.py` owns `derived.json` / `users_derived.jsonl`; the class-bracket step owns
  `phases.json` / `class_phases/`. A run executes them in that order, so a later step
  always sees what an earlier one wrote.
* **Region-independent facts vs. region rules.** The raw files and the common part of
  `derived.json` mean the same thing in every region. What differs — what a "1-on-1"
  event is, which days are holidays, what a class bracket is called — comes only from
  `scripts/North_America/classify.py`.

## 4. What the data is

### 4.1 Events (`events/…/<Event>/`)

An *event* is one bracket in one tournament (start.gg's "event"; a tournament with
Singles and Doubles has two). Only Super Smash Bros. Ultimate events are downloaded
(`--game-id 1386`); every event of the tournament is taken, including doubles,
crews, amiibo — the judgement that only singles count is made later, in `derived.json`,
so nothing is lost if a rule changes.

**`attr.json`** — the event as start.gg describes it:

| Field | Meaning |
|---|---|
| `event_id` | start.gg event id (the key everything else uses) |
| `tournament_name`, `event_name` | names at download time (the directory keeps the original) |
| `region` | `"North America"` (from the country code) |
| `place` | `country_code`, `city`, `lat`, `lng`, `venue_name`, `timezone`, `postal_code`, `venue_address`, `maps_place_id` — the venue as registered. `timezone` is what the calendar uses; `country_code` picks the holiday table |
| `num_entrants` | registered entrants |
| `offline` | `true` for in-person, `false` for online (start.gg's `isOnline`). The ranking skips online events |
| `url` | `/tournament/<slug>` |
| `status` | `"completed"` / `"in_progress"` / … |
| `timestamp`, `end_timestamp` | start and end (Unix seconds, UTC) |
| `labels` | always `{}` (a retired field) |
| `version` | file format version |

**`standings.json`** — `{"data": [{"placement": 1, "user_id": 2459300}, …], "version"}`.
Final placements. `user_id` is `null` for entrants without a start.gg account (guests,
teams); such rows are kept but consumers skip them.

**`seeds.json`** — `{"data": [{"seed_num": 1, "user_id": …}, …]}`. The seeding.

**`matches.json`** — `{"data": [set, …], "bracket_capacity", "dup_set_id", "dup_match_key"}`.
One record per set:

| Field | Meaning |
|---|---|
| `match_id` | start.gg set id |
| `winner_id`, `loser_id` | user ids (`null` when the entrant has no account) |
| `winner_score`, `loser_score` | games won (start.gg reports `-1` for the DQ'd side) |
| `dq`, `cancel`, `state` | DQ flag, cancelled flag, start.gg set state (3 = completed) |
| `round`, `round_text`, `global_round`, `global_top_x`, `global_bracket_label`, `winners_top`, `losers_top`, `bracket_label` | where in the bracket the set was played |
| `phase`, `phase_id`, `phase_name`, `phase_order`, `phase_num_seeds`, `phase_bracket_type`, `phase_top_n` | the phase (pools, top 8, redemption …) the set belongs to |
| `phase_group_id`, `phase_group_start_at`, `wave_id`, `wave`, `wave_start_at` | the pool and its scheduled time |
| `started_at`, `completed_at` | timestamps |
| `details` | per-game character picks, when reported |

**`derived.json`** — the judgements, rebuilt by `derive.py`. This is what the ranking
build reads instead of re-deciding anything. For North America (`classifier_version` 8):

| Field | Meaning |
|---|---|
| `classifier_version` | version of the rules that produced the file; bumped when rules change so every file is rebuilt |
| `event_id`, `tournament_name`, `event_name`, `num_entrants` | copied from `attr.json` |
| `is_class_virtual`, `parent_event_id`, `class_letter` | set for the virtual events under `class_phases/` (section 4.2) |
| `is_offline` | copy of `attr.offline`; `false` → the ranking skips the event |
| `results` | facts read from standings/matches: `n_standings`, `min_placement`, `has_de_phase` (a double-elimination phase exists), `has_gf_recorded` |
| `is_1on1`, `not_1on1_reason` | whether the event counts as a singles bracket. Doubles, crews, squad strike, amiibo, ladders and `N v N` names are excluded; the reason names the keyword (`keyword:doubles`, `regex:NvN`) |
| `calendar` | `date` and `end_date` in the venue's own `timezone`, `country_code`, `holidays` (which country's holiday table was applied, `null` if none exists for that country), `is_weekend_real` (Saturday, Sunday, or a national holiday of that country) |
| `names` | flags from the names: `lower_class` (the event itself is a lower bracket — "Redemption Bracket", "Novice …"), `restricted_tname` / `restricted_ename` (entry is restricted — Arcadian; kept separately for tournament and event name so a co-hosted main event is not affected) |
| `class_bracket` | `all_phases_class` (every phase is a class bracket — the whole event is one) and `phase_group_ids` of the class-bracket pools, so consumers can leave those sets out of the main bracket |

Japan's file has more (`naming`, `place.prefecture`, more `names`); a field missing in
North America simply has no rule yet.

### 4.2 Class brackets (`phases.json`, `class_phases/`)

Many tournaments run a second bracket for players eliminated early — in the US usually
called **Redemption**, sometimes Amateur / Novice; in Japan B/C/D/E クラス. Two shapes
occur:

* **A separate event** ("Redemption Bracket" next to "Ultimate Singles"). Nothing
  special is needed: it is its own directory, and `derived.names.lower_class` marks it.
* **A phase inside the main event** (a "Redemption" phase in the Singles event's
  bracket). Its sets sit in the main `matches.json` and would distort the main
  bracket, so `update_class_data.py` separates them:
  * `phases.json` — every phase of the event with `is_class` decided by the region's
    `is_class_phase()`;
  * `class_phases/<phase_id>.json` — that phase's pools with their `standings` and
    `played_user_ids` (who actually played, so no-shows are dropped);
  * `class_phases/<CLASS>_virtual/` — the class bracket materialised as an event of its
    own: full `attr.json` (`event_name` = `"<event> / Redemption"`, negative `event_id`
    = `-(parent id × 10 + index)`, `timestamp` = parent + index seconds), standings,
    matches, and its own `derived.json` with `is_class_virtual: true` and
    `parent_event_id`.

  The index (position in `CLASS_LETTERS`) is what makes the virtual id and time
  unique, which is why `CLASS_LETTERS` must only ever be appended to.

### 4.3 Index files

| File | Content |
|---|---|
| `done.csv` | One tournament id per line: its events have been downloaded completely. A listed tournament is still re-fetched for a while — every 12 hours until 3 days after it ended (organisers correct results), and every run for 7 days while its standings have no winner — and skipped after that. Delete a line to force a re-download |
| `done_events.csv` | Same for events imported by hand with `download_specific_event.py` |
| `tournaments.jsonl` | One line per tournament: `tournament_id`, `name`, `events: [{event_id, event_name, path}]`, `version`. The `path` is how every tool finds the event directory |
| `users.jsonl` | One line per player who appears in any downloaded event: `user_id`, `player_id`, `gamer_tag`, `prefix` (team tag), `gender_pronoun`, `startgg_discriminator`, `country`, `addr_state`, `city`, `x_id`, `x_name`, `discord_id`, `discord_name`, `version`. All of it is what the player made public on their start.gg profile. New players are appended; a player whose profile changed is updated in place (the whole file is rewritten) |
| `users_derived.jsonl` | Per-player judgements from `users.jsonl`. Japan derives the prefecture from `city`; North America has no per-player rule yet, so the file is empty |
| `upcoming.json` | `{generated_at, country_code, lookahead_days, count, tournaments: [...]}` — tournaments starting in the next 21 days with `tournament_id`, `tournament_slug`, `tournament_name`, `start_at`, `end_at`, `is_online`, `city`, `venue_name`, `country_code`, `num_attendees`, `url`, `events: [{event_id, event_slug, event_name, num_entrants, type, start_at}]`. Rewritten every run (it is a snapshot, not history) |
| `validation_baseline.json` | `{updated_at, counts: {<kind>: n}}` — how many events currently show each known kind of inconsistency (section 5.5). The run compares against it and rewrites it when the counts drift within tolerance |

### 4.4 Sizes to expect

One US week (2026-09-05 … 11): 515 events, 332 tournaments, 5,400 players, 20 MB;
median event 12 entrants. A year of the US is on the order of 1 GB. Decide how far
back you want history before the first import (section 6.3).

### 4.5 Hand-maintained tables (`manual/`)

Optional. Create the directory when you need one of these; nothing else writes there.

| File | What it is for |
|---|---|
| `awaiting_resume.json` | Events that were interrupted and will resume later (weather, venue closed). `{"events": [{"event_id", "name", "added", "note"}]}`. Listed events are re-downloaded every run until a winner exists, ignoring the 7-day rule, and consumers may use their partial results. Pass it with `--awaiting-file` (the runner does when the file exists) |
| `user_merges.json` | Two start.gg accounts that are the same person → one id. Used by the Japanese ranking; only needed once a ranking consumes your region |
| `overseas_manual.json` | Players to treat as foreign / domestic regardless of their profile. Same |

## 5. The scripts

Everything is invoked from the repository root. `--help` on any script lists its
options. The one you run daily is the first; the rest are its steps, useful on their
own for repairs.

### 5.1 `scripts/common/run_region.sh` — one full cycle

```sh
bash scripts/common/run_region.sh --country-code US [--days 14] [--python .venv/bin/python3]
     [--token-file PATH] [--log-dir DIR] [--download-retries 2] [--no-commit] [--no-push] [--dry-run]
```

| Step | Script | What it does |
|---|---|---|
| 1 | `download.py` | Tournaments that ended in the last `--days` days (endAt-based, so a weekly whose start date was set early is still found). Resumable through `done.csv`; the runner retries a failed download `--download-retries` times |
| 2 | `fetch_upcoming.py` | Rewrites `upcoming.json` |
| 3 | `update_class_data.py` | Class brackets of the last `--days` days (section 4.2). Instant when there are none |
| 4 | `derive.py` | `derived.json` for new or changed events, `users_derived.jsonl` |
| 5 | `validate_data.py --baseline` | Counts the known inconsistencies, warns on a jump (never stops the run) |
| 6 | git | `git add -A -- data/startgg/North_America && git commit && git push origin data-North_America`. Refuses if the checkout is on another branch |

Behaviour: logs to `~/.local/log/smash_database/North_America_<date>.log` (screen too;
logs older than 60 days are deleted); a second copy started while one runs prints
`already running` and exits; the token never appears in the log or in `ps`; steps 1
and 4 failing stop the run (`rc≠0`), the others only add a warning. The last lines are
a summary:

```
═══ North_America (US) 2026-09-12 02:27:09  rc=0  475s ═══
  events: 513 → 515   tournaments.jsonl: 332 → 332 rows   users.jsonl: 5424 → 5424 rows
  commit: 5a879f7114
```

### 5.2 `scripts/common/download.py` — the downloader

```sh
python3 scripts/common/download.py --country-code US --start-date 2026-09-12 --finish-date 2026-08-29 [--awaiting-file PATH]
```

`--start-date` is the newer bound, `--finish-date` the older one (the window is on the
tournament's end date). Index paths default to `data/startgg/<Region>/…` from the
country code. For each tournament in the window it fetches every Ultimate event's
attributes, standings, seeds and sets, writes the event directory, updates
`users.jsonl` and `tournaments.jsonl`, and marks the tournament in `done.csv` once
it is complete and old enough. Events that fail are appended to `failed_events.log`
in the repository root and retried next run. Rate: about 80 API requests a minute, i.e.
roughly 6 events a minute — a week of the US is about an hour, a day's increment a
few minutes.

Useful options: `--game-id` (default 1386 = Ultimate), `--max-retries` / `--retry-delay`
for the API, `--indent-num` (JSON pretty-printing).

### 5.3 `scripts/common/fetch_upcoming.py`

```sh
python3 scripts/common/fetch_upcoming.py --country US [--lookahead-days 21] [--out PATH]
```

Writes `data/startgg/<Region>/upcoming.json`. `--region` only if the country does not
determine it.

### 5.4 `scripts/common/update_class_data.py` — class brackets

```sh
python3 scripts/common/update_class_data.py --region North_America --since-days 30
```

Finds events of the last N days whose `matches.json` contains a phase that the
region calls a class bracket (`is_unseparated_class_phase()`) but that `phases.json`
has not marked yet, then runs the four steps in one process: `fetch_event_phases`
(→ `phases.json`), `fetch_class_phase_standings` (→ `class_phases/<id>.json`),
`fetch_class_phase_players` (adds `played_user_ids`), `build_class_virtual_tournaments`
(→ `<CLASS>_virtual/`). Steps 2–4 skip what already exists, so re-running is cheap.
Each of the four can be run alone with `--region North_America` (and `--events-root`);
`fetch_event_phases.py --force` rebuilds `phases.json` after a pattern change.

### 5.5 `scripts/common/derive.py` — judgements

```sh
python3 scripts/common/derive.py --region North_America [--all] [--dry-run]
```

Loads `scripts/North_America/classify.py`, sets the process timezone from its
`TIMEZONE`, and writes `derived.json` for every event whose file is missing, older
than its inputs, or from an older `classifier_version`. Identical content is not
rewritten (file times stay put). `--all` ignores the freshness check and rebuilds
everything — run it after any rule change.

### 5.6 `scripts/common/fix/validate_data.py` — checks

```sh
python3 scripts/common/fix/validate_data.py --region North_America                  # every finding, one line each
python3 scripts/common/fix/validate_data.py --region North_America --summary        # counts per kind
python3 scripts/common/fix/validate_data.py --region North_America --baseline data/startgg/North_America/validation_baseline.json [--tolerance 10] [--write-baseline]
```

Checks every event directory (required files, required fields, sets with missing
players, set ids that are not in the standings) and the index (paths that do not
exist). Several hundred findings are normal — entrants without an account, tournaments
that never ran a bracket — and consumers tolerate them, so the run only watches for a
jump: with `--baseline` the counts per kind are compared with the file; within the
tolerance the file is updated, above it the run prints `REGRESSION:` lines and exits 2.
After confirming a jump is legitimate, `--write-baseline` accepts it.

### 5.7 Repair tools (run by hand)

| Tool | When |
|---|---|
| `scripts/common/manual/download_specific_event.py --region North_America --event <URL> [--event …]` | Import events that the window missed (an old tournament, or one start.gg's listing does not return). `--event` takes any start.gg URL of the tournament — `…/tournament/<t>/event/<e>/standings`, `…/tournament/<t>/events`, `…/tournament/<t>` — or `<t-slug>/<e-slug>`. With no event in the URL every Ultimate event of the tournament is imported (`--game-id` to change the game). Then run `fix/check_events_in_tournaments.py --region North_America --apply` so the index knows the new directories, and `derive.py` |
| `scripts/common/fix/check_events_in_tournaments.py --region North_America [--apply]` | Every event directory must appear in `tournaments.jsonl`; this lists (and with `--apply` adds) the missing ones |
| `scripts/common/fix/fix_missing_tournaments.py --region North_America --dry-run` | The reverse: index lines whose directory is gone |
| `scripts/common/manual/refetch_incomplete_events.py [--dry-run] [--limit N]` | Re-download events whose standings have no winner |
| `scripts/common/manual/redownload_matches.py`, `fetch_event_matches_phased.py` | Re-fetch sets of an event (after an organiser corrected results) |
| Remove a tournament id from `done.csv` | The general way to make the next run re-download a tournament inside the window |

## 6. Daily operation

### 6.1 The routine

```sh
cd smash_database
bash scripts/common/run_region.sh --country-code US --days 14 --python .venv/bin/python3
```

Read the summary. `rc=0` and no "⚠️" block means the data is downloaded, judged,
checked, committed and pushed.

### 6.2 From cron

```
17 */6 * * *  cd /home/you/smash_database && bash scripts/common/run_region.sh --country-code US --days 14 --python .venv/bin/python3 >/dev/null 2>&1
```

Every 6 hours is plenty: the window is 14 days, so a missed run costs nothing, and
results that organisers correct are picked up because a finished tournament is still
re-fetched for 3 days (and for 7 days while it has no winner). Look at the log when the summary shows a
warning; the runner keeps the last 60 days.

### 6.3 The first import

```sh
bash scripts/common/run_region.sh --country-code US --days 7 --no-commit --python .venv/bin/python3
git status                                                     # only data/startgg/North_America/ should appear
python3 scripts/common/fix/validate_data.py --region North_America --summary
```

Look at a few `derived.json` (`is_1on1`, `calendar`, `names`) and at
`class_phases/` directories to see the rules doing what you expect (section 7). Then
decide the history depth — `--days 365` for a year — and run again; it is resumable, so
just rerun until step 1 reports nothing new. Commit with

```sh
git add -A -- data/startgg/North_America && git commit -m "data: initial North America import" && git push -u origin data-North_America
```

or simply run the runner without `--no-commit`.

## 7. Changing the rules for your region

`scripts/North_America/classify.py` is the only place region knowledge lives. It
declares:

| Name | Decides |
|---|---|
| `CLASSIFIER_VERSION` | Bump it after any change below, then `derive.py --region North_America --all` |
| `TIMEZONE` | Default zone when an event has no `place.timezone` (`America/New_York`) |
| `EXCLUDE_PATTERNS`, `EXCLUDE_REGEX`, `ALLOW_PATTERNS` → `is_1on1_event()` | Which events are not singles brackets |
| `HOLIDAY_RULES`, `OBSERVED_SHIFT_COUNTRIES`, `EXTRA_HOLIDAY_DATES` → `calendar_flags()` | National holidays per country (US / CA / MX / DO), weekend-observance shifts, one-off additions. A country without a table gets no holidays rather than another country's |
| `LOWER_CLASS_EVENT_PATTERN`, `RESTRICTED_PATTERN` → `name_flags()` | `names.lower_class`, `names.restricted_*` |
| `CLASS_LETTERS`, `CLASS_PHASE_PATTERN`, `CLASS_LETTER_PATTERN` → `is_class_phase()`, `class_letter()`, `class_virtual_event_name()` | What a class bracket phase is called and how its virtual event is named. Append to `CLASS_LETTERS`, never reorder |
| `upcoming_flags()` | The same judgements for `upcoming.json` entries (only names and a start time exist yet) |
| `classify_user()` | Per-player judgements → `users_derived.jsonl` (returns `None` for now) |

What is deliberately *not* there: anything Japanese. お盆 / 年末年始 as weekends,
Japanese name patterns, prefectures — North America starts from what was verified on
its own data (a week of the US: Redemption is the class bracket, Arcadian is a
restricted tournament) and grows from there. Provincial / state holidays are not in
yet because `attr.place` has no state field.

The module and its tests (`scripts/North_America/test_classify.py`) live on your
branch only, so a change is an ordinary commit — no pull request, nobody to wait for:

```sh
# edit scripts/North_America/classify.py, bump CLASSIFIER_VERSION, add a case to scripts/North_America/test_classify.py
python3 -m unittest scripts.North_America.test_classify
python3 scripts/common/derive.py --region North_America --all
python3 scripts/common/update_class_data.py --region North_America --since-days 3650   # only if class-bracket patterns changed
git add scripts/North_America data/startgg/North_America && git commit -m "North America rules v9: …" && git push
```

`scripts/common/` (the downloader, `derive.py`, the runner) is shared by every region
and stays on `main`; a change there is a pull request against `main`.

## 8. When something is off

| Symptom | What to do |
|---|---|
| `Max retries exceeded` on the tournament list | start.gg is down or the token is wrong. Nothing was lost; run again later |
| `429 Too Many Requests` lines | Normal; the client backs off and retries. They only matter if the run finally fails |
| `failed_events.log` grew | Individual events failed; they are not marked done and are retried next run. If one keeps failing, import it with `download_specific_event.py` and read its error |
| `REGRESSION:` from the check step | A kind of inconsistency jumped. Run `validate_data.py --region North_America` without `--baseline` to see the events. Legitimate (bulk import, new kind of event) → `--write-baseline` |
| `[pending] … no champion in standings` in the download log | The bracket was not finished when fetched; the tournament is re-fetched every run for 7 days. If it will resume later than that, register it in `manual/awaiting_resume.json` |
| A phase that is a class bracket was not separated (or the reverse) | Adjust `CLASS_PHASE_PATTERN` / `CLASS_LETTER_PATTERN` (section 7), then `fetch_event_phases.py --region North_America --force` on the affected events and `update_class_data.py` |
| A holiday is wrong or missing | `HOLIDAY_RULES` / `EXTRA_HOLIDAY_DATES` (section 7) |
| `push failed` | Someone else pushed to your branch. `git pull --rebase origin data-North_America` and run again |
| `ERROR: currently on main …` | Not on the data branch. `git switch data-North_America` |
| `already running` | A previous run is still going (or died leaving the lock held — check `ps`, then delete `~/.local/log/smash_database/.North_America.lock`) |
| The checkout shows changes outside `data/startgg/North_America/` and `scripts/North_America/` | Do not commit them. Shared scripts change only through `main`; stray files belong in `.gitignore` (on `main`) |

## 9. Git rules

* `data-North_America`: you commit `data/startgg/North_America/` and
  `scripts/North_America/`, fast-forward only (force-push and deletion are blocked).
  Bring shared-script updates in with
  `git fetch origin main && git merge --no-edit origin/main && git push` — the merge
  keeps your `scripts/North_America/` (it does not exist on `main`).
* `main`: pull requests only; the unit tests must pass and the owner approves (the
  owner also checks that the Japan nightly's output is unchanged). Never edit
  `scripts/common/` or the docs on the data branch.
* Other regions' directories and branches: never touch.
* The repository is public. `users.jsonl` holds only what players published on their
  profile; a removal request is handled by deleting the lines and committing. Players
  can file one with the "Removal request" issue template; handle the ones for your
  region (delete the player's lines from `users.jsonl` and `users_derived.jsonl`; the
  `user_id` stays in the event files as an opaque number).
