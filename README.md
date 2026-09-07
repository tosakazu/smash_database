# smash_database

Tournament data for Super Smash Bros. Ultimate, downloaded from the
[start.gg](https://www.start.gg/) GraphQL API: standings, seeds, sets (with per-game
results where available), event attributes, and a player index. The data feeds the
[spsp](https://github.com/tosakazu/spsp) ranking build, but the repository is
self-contained: the scripts here only need a start.gg API token.

Detailed documentation in `docs/`: [data_model.md](docs/data_model.md) (file formats),
[pipeline.md](docs/pipeline.md) (what the downloader does and when it re-downloads),
[startgg_api.md](docs/startgg_api.md) (API layer, paging, retries),
[operations.md](docs/operations.md) (branches, adding a region, manual repairs, checks),
[known_issues.md](docs/known_issues.md).

## Repository layout

The repository is split into one **script branch** and one **data branch per region**.
They never share files, so a region branch can be merged into another region branch
without conflicts, and scripts can change without touching data history.

| Branch | Contents | Who maintains it |
|---|---|---|
| `main` | `scripts/`, `docs/`, this README. `data/` is git-ignored. No history before 2026-09-07 | Everyone (shared code) |
| `data-Japan` | `startgg/events/Japan/**` and `startgg/Japan/{done.csv,done_events.csv,tournaments.jsonl,users.jsonl}` | Japan operator. Committed and pushed nightly by the spsp pipeline |
| `data-<Region>` (future, e.g. `data-North_America`) | `startgg/events/<Region>/**` and `startgg/<Region>/{...}` | That region's operator |

A data branch contains nothing but `startgg/` and a `.gitignore`. The `.gitignore`
lists `scripts/`, `docs/`, `README.md`: that is only a guard so that copies of the
script tree placed inside a standalone data checkout are never committed to a data
branch. It does not mean scripts are unnecessary there.

### Working checkout

Check out `main`, then mount the data branch as a git worktree at `data/`. Every
script defaults to `data/startgg/...`, so paths look like a single repository:

```sh
git clone --single-branch --branch main git@github.com:tosakazu/smash_database.git smash_db_tournament
cd smash_db_tournament
git fetch origin data-Japan
git worktree add data data-Japan        # -> data/startgg/events/Japan/... , data/startgg/Japan/...
```

Use `--single-branch`: a plain `git clone` downloads the data history of every region.
To work on another region, fetch and mount that region's branch instead
(`git worktree add data data-North_America`). A checkout can hold several regions at
once by mounting them at different directories, but the nightly pipeline expects
exactly one at `data/`.

Resulting tree:

```
smash_db_tournament/            main worktree
├── README.md
├── docs/
├── scripts/
│   ├── common/                 region-independent code (API layer, downloader, tools)
│   │   ├── download.py         nightly downloader
│   │   ├── download_policy.py  pure decision logic used by download.py
│   │   ├── fetch_upcoming.py   upcoming-tournament list
│   │   ├── redownload_matches_v2.py
│   │   ├── utils.py  queries.py  clock.py  _cli.py  storeJson.py
│   │   ├── manual/             one-off tools (not used by the pipeline)
│   │   └── fix/                data validation / repair tools
│   ├── Japan/                  Japan-specific: lower-class bracket separation
│   └── test/                   unit tests
└── data/                       worktree of data-Japan (git-ignored by main)
    └── startgg/
        ├── Japan/
        │   ├── done.csv            tournament ids whose download is complete
        │   ├── done_events.csv     event ids whose download is complete
        │   ├── tournaments.jsonl   one line per tournament: id, name, events + paths
        │   └── users.jsonl         one line per player: user_id, gamer_tag, country, city, ...
        └── events/Japan/YYYY/MM/DD/<Tournament>/<Event>/
            ├── attr.json           event attributes (entrants, offline, timestamp, url, ...)
            ├── standings.json      {"data": [{placement, user_id}], "version"}
            ├── seeds.json          {"data": [{seed_num, user_id}], "version"}
            ├── matches.json        {"data": [{match_id, winner_id, loser_id, scores, round, phase, games...}], ...}
            ├── phases.json         (Japan) bracket phases with is_class flags
            └── class_phases/       (Japan) standings of each class bracket, and
                └── <X>_virtual/    a class bracket materialised as its own event
```

Data files are written with fixed key order and indentation. Downstream readers
(spsp `build/data_loader.py`) depend on that format, so use the writers in
`scripts/common/utils.py` (`write_json`, `write_json_pretty`, `write_json_compact`,
`write_matches_v2`) rather than `json.dump` directly.

### Data branch operation

* **Nightly (Japan).** The spsp pipeline (`deploy/update_and_deploy.sh`) runs the
  download, commits everything under `startgg/` in the `data/` worktree as one commit
  (`data: YYYY-MM-DD nightly`) and pushes it to `origin/data-Japan`. Nothing else is
  committed automatically.
* **Manual fixes** to data (re-downloads, backfills) are committed on the data branch the
  same way: `git -C data add -A -- startgg && git -C data commit`.
* **Adding a region.** Create an orphan branch `data-<Region>` whose first commit
  contains `startgg/<Region>/` and `startgg/events/<Region>/`, plus the same
  `.gitignore` as `data-Japan`. Run `scripts/common/download.py --country_code <CC>`;
  the index-file defaults follow the region derived from the country code.
* **Combining regions.** Since region branches only touch their own paths,
  `git merge data-North_America` on a `data-Japan` checkout (or into a dedicated
  `data-all` branch) yields both trees. The one shared concept is the player index:
  `startgg/<Region>/users.jsonl` files are per region and a player who enters
  tournaments in two regions appears in both. Consumers must union them by `user_id`
  (the spsp build currently reads Japan's only).
* **History size.** Every nightly commit rewrites `tournaments.jsonl` and `users.jsonl`,
  so a data branch grows steadily. Squash old history occasionally
  (e.g. once per season, `git checkout --orphan` + force-push) and tell other operators
  to re-fetch. The script branch is unaffected.

## Scripts

All scripts run from the repository root (`smash_db_tournament/`), read the token from
the `STARTGG_TOKEN` environment variable (or `--token`), and need Python 3.10+ with
`requests`. Do not pass the token on the command line in shared environments; it
shows up in the process list.

### Nightly pipeline

| Script | Role |
|---|---|
| `scripts/common/download.py` | Enumerate tournaments in a date window for one country, then per event save `attr.json`, `standings.json`, `seeds.json`, `matches.json` under `data/startgg/events/<Region>/...` and update `users.jsonl`, `tournaments.jsonl`, `done.csv`, `done_events.csv`. Events that fail are appended to `failed_events.log` in the working directory |
| `scripts/common/download_policy.py` | "Download / skip / mark done" decisions used by `download.py`. Pure functions, no I/O; the decision table is tested in spsp `tests/fetch/test_dl_policy.py` |
| `scripts/common/fetch_upcoming.py` | Upcoming tournaments for the next N days → one JSON file (used by the seed picker) |
| `scripts/common/redownload_matches_v2.py` | Set download and the `matches.json` format. `download.py` imports its functions; run it directly only to re-fetch sets |
| `scripts/Japan/update_class_data.py` | Japan only. Finds recent events whose sets contain class brackets (B/C/D/E class) and runs `fetch_event_phases` → `fetch_class_phase_standings` → `fetch_class_phase_players` → `build_class_virtual_tournaments` in one process. Steps 2-4 skip existing output, so a full rescan is cheap |

Typical nightly commands (the dates are the window's upper and lower bounds):

```sh
export STARTGG_TOKEN=...
python3 scripts/common/download.py --country_code JP \
    --start_date 2026-09-07 --finish_date 2026-08-24 \
    --startgg_dir data/startgg/events \
    --done_file_path data/startgg/Japan/done.csv \
    --users_file_path data/startgg/Japan/users.jsonl \
    --tournament_file_path data/startgg/Japan/tournaments.jsonl \
    [--awaiting_file path/to/awaiting_resume.json]
python3 scripts/common/fetch_upcoming.py --country JP --lookahead-days 21 --out upcoming.json
python3 scripts/Japan/update_class_data.py --since-days 30
```

`--done_file_path`, `--users_file_path` and `--tournament_file_path` default to
`data/startgg/<Region>/...` where the region is derived from `--country_code`, so they can
be omitted. `--awaiting_file` points at a registry of postponed or interrupted events:
those are re-fetched on every run until a winner exists, ignoring the normal
"done after 7 days" rule.

### Shared modules (`scripts/common/`)

| Module | Role |
|---|---|
| `utils.py` | API layer: `fetch_data_with_retries` is the only place that performs HTTP; `fetch_all_nodes` handles paging. Also the JSON/JSONL readers and writers |
| `queries.py` | GraphQL query strings |
| `clock.py` | The single source of "now" (`now()` / `set_now()`), so tests can freeze time |
| `_cli.py` | Common CLI options (`--token --url --max_retries --retry_delay`) and API-layer setup |
| `storeJson.py` | Legacy JSON store helper |

### Manual tools (`scripts/common/manual/`, `scripts/Japan/manual/`)

Not used by the pipeline and independent of each other; removing one breaks nothing else.

* `download_specific_event.py` – download a fixed list of events; edit the `target_events` list of `(tournament_slug, event_slug)` in the script, then run it
* `refresh_users.py` – refresh `users.jsonl` from the API (resumable via checkpoint/cursor files)
* `refresh_top_ranked.py`, `refetch_incomplete_events.py`, `redownload_matches.py` (v1),
  `fetch_event_matches_phased.py`, `backfill_end_at.py`, `backfill_wave_start_at.py`
* `fetch_character_games.py` / `merge_character_games.py` – bulk backfill of character usage per game
* `scripts/Japan/manual/rescan_lower_class.py` – rescan events for class brackets

Tools that read or write the index files take `--region <Region>`; the defaults then
become `data/startgg/<Region>/{users.jsonl,tournaments.jsonl,...}`. Explicit
`--users_file_path` etc. override them. Without either, the tool exits with an error
instead of guessing a path.

### Validation / repair (`scripts/common/fix/`)

`validate_data.py` (integrity checks; `scripts/test/test_validate_data.py` covers it),
`check_events_in_tournaments.py`, `backfill_events.py`, `fix_missing_tournaments.py`.

## Testing

* `scripts/test/` – unit tests, `python3 -m pytest scripts/test`.
* Regression tests for the download step live in the spsp repository under `tests/fetch/`:
  `dl_golden.py` replays recorded API responses (a cassette) with a frozen clock and
  checks that the output tree is byte-identical to the recording, so a refactor can be
  verified without network access. `test_dl_policy.py`, `test_dl_pure.py`,
  `test_api_layer.py` cover the decision table, pure helpers and API error handling.
  Point them at another checkout with `SPSP_DL_SCRIPTS=/path/to/smash_db_tournament`.

When changing `download.py` or the writers, run the replay before merging; the output
format is what downstream builds depend on.

## Conventions for contributors

* Region-independent code goes in `scripts/common/`; anything that assumes one region's
  tournament customs (class brackets, venue naming, ...) goes in `scripts/<Region>/`.
  A region package may import `scripts.common`, never the other way round.
* Keep HTTP inside `utils.fetch_data_with_retries` and time inside `clock.py`; the
  record/replay tests rely on those two seams.
* Never change the on-disk format of the data files (key order, indentation, the
  `version` field) without coordinating with downstream consumers.
* Data commits go only to data branches; script commits go only to `main`.

## History

The history was recreated on 2026-09-07. Earlier commits and the data of four overseas
regions that were downloaded before then are not kept. GitHub Actions workflows that
used to run the download were retired at the same time; the nightly download is driven
by the spsp deployment pipeline.
