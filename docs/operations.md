# Operations

Day-to-day handling of the repository: branches, adding a region, manual repairs,
checks, and what to do when the nightly run fails. The layout itself is described in
the top-level [README](../README.md).

## Branch rules

| Branch | Commits allowed | Never |
|---|---|---|
| `main` | scripts, docs, tests | anything under `data/` |
| `data-<Region>` | files under `data/startgg/<Region>/`, merges of `main`, and — if the region keeps its rules on its own branch — `scripts/<Region>/` | edits to `scripts/common/`, docs, or other regions' paths |
| `data-all` (local only) | merges of the `data-<Region>` branches | direct commits of any kind, pushing |

* Script changes go through a branch or pull request against `main`. Run the tests
  before merging (below). `main` has no CI; the nightly run is the integration test,
  so merge script changes early in the day and watch the next run.
* Data branches are written by one operator (or one machine) each. Fast-forward only;
  if a push is rejected, `git pull --rebase` in the worktree, never force-push a data
  branch that others have fetched.
* Region branches must not touch each other's paths, so merging them never conflicts.

## Production checkout (Japan)

On the production host `~/spsp-ranking/smash_db_tournament` is a checkout of
`data-Japan` (scripts, docs and `data/startgg/` in one tree). The spsp nightly
(`deploy/update_and_deploy.sh`) runs the download there, then
`git add -A -- data/startgg && git commit` and pushes `data-Japan`. It never touches
`main`. Logs: `~/.local/log/spsp_nightly/<date>.log`, step `[2.65/5]`.

To update scripts on the host: push them to `main`, then in the checkout run
`git fetch origin main && git merge origin/main && git push origin data-Japan` between
runs (a run takes ~15 min every 3 hours; check `pgrep -f update_and_deploy`).
If the merge conflicts on `.gitignore` (it differs by design: `main` ignores `data/`, a data
branch does not), keep the data branch's version: `git checkout --ours -- .gitignore && git add .gitignore && git commit`.

## Adding a region

1. Branch from `main` and replace the `.gitignore` with the data-branch version
   (`main` ignores `data/`; a data branch must track it):

   ```sh
   git checkout -b data-North_America main
   git show data-Japan:.gitignore > .gitignore
   git add .gitignore && git commit -m "data-North_America: init"
   ```

2. Make sure the region has a rule module, `scripts/<Region>/classify.py` (North America
   ships with one). `derive.py` refuses to run without it rather than guessing, and the
   module owns everything that differs by region — see "Region modules" below.

3. Run one cycle from the repository root (the operator's guide is
   [region_operator.md](region_operator.md)):

   ```sh
   STARTGG_TOKEN=... bash scripts/common/run_region.sh --country-code US --days 14
   ```

   It downloads the window, writes the judgements, checks the data, then commits and
   pushes `data/startgg/<Region>/` on the `data-<Region>` branch (it refuses to commit
   from another branch). `--dry-run` prints the commands, `--no-commit` / `--no-push`
   stop before git. Put this in the operator's own cron; nothing runs on GitHub.
   The individual steps are in the [README](../README.md) if you need to run them by hand.

`country_code2region()` in `utils.py` decides the region name from the country code
(`US`, `CA`, `MX`, `DO` → `North America`, spaces become `_` in paths and branch names).
Add codes there when a region needs them.

## Region modules

`scripts/<Region>/classify.py` is what `derive.py` loads for a region. Where it lives is
the region's choice: Japan's is on `main` (the ranking build depends on it and its tests
run in CI); North America's lives only on `data-North_America`, edited directly by its
operator without a pull request, with its tests next to it
(`python3 -m unittest scripts.North_America.test_classify`). Merging `main` into a data
branch never removes a branch-only directory, so a region module kept on its branch
survives every script update. The shared code (`scripts/common/`) is always on `main`.

The module must declare:

| Name | Meaning |
|---|---|
| `CLASSIFIER_VERSION` | Bump after changing any rule, then run `derive.py --region <Region> --all` so every `derived.json` is rewritten |
| `TIMEZONE` | The zone the region's dates are decided in (`Asia/Tokyo` for Japan). `derive.py` sets the process TZ from it — there is no default, so no region is ever judged on another region's clock |
| `classify_event(base, ctx)` | Adds the region's judgements to the common facts and returns the `derived.json` body |
| `classify_user(u)` | One line of `users.jsonl` → what to record in `users_derived.jsonl` (`None` = record nothing) |
| `check_requirements()` (optional) | Verify region-specific dependencies (Japan needs `jpholiday` for its holiday table) |
| `upcoming_flags(tournament_name, event_name, num_entrants, start_ts)` (optional) | The same judgements for a tournament that has not happened yet (no event directory, so only the name and the start time). `scripts/common/annotate_upcoming.py` reads it; a region without it is a no-op |
| `CLASS_LETTERS`, `is_class_phase()`, `is_unseparated_class_phase()`, `class_letter()`, `class_virtual_event_name()` (optional, all or nothing) | Class brackets — a lower-class bracket run inside a tournament (Japan: B/C/D/E クラス, North America: Amateur / Novice / B-E class). `scripts/common/update_class_data.py` and the three fetchers it drives are region-independent and read these; a region that declares none skips the whole step. `CLASS_LETTERS` decides the id of the virtual event each class is materialised as, so append to it, never reorder |

Nothing else is shared: holidays, "treat this period as a weekend", name patterns and
series naming are per region. Japan treats 土日祝 plus お盆 and 年末年始 as weekends
(`jpholiday` supplies the holiday table). North America decides holidays per country from
the event's `place.country_code`: `HOLIDAY_RULES` carries the national holidays of US,
CA, MX and DO, each with that country's own observance rule — US and CA shift a holiday
that lands on a weekend to the neighbouring weekday, Mexico moves several to a Monday by
rule (and adds 1 December every sixth year), the Dominican Republic moves five of its
holidays to the nearest Monday under ley 139-97. A country with no table gets **no
holidays at all** rather than another country's, and `derived.json` records which table
was applied (`calendar.holidays`). There is no equivalent of お盆 / 年末年始 because
nobody has decided what 北米 would treat that way, and provincial / state holidays are
still missing — `attr.place` has no state field, so that needs a resolver over
`venue_address` first. The module lists the two observance details that still need a
local check. Copying Japan's rules into a new region is the one thing not to do: start
from `scripts/North_America/classify.py` and add only what you can verify for that region.

## Combining regions: `data-all`

`data-all` is a **local** branch on the machine that needs several regions at once
(the ranking build). It is not on GitHub and is never pushed: it would only duplicate
the region branches. Create it once and advance it only by merging:

```sh
git checkout -b data-all origin/data-Japan         # once
git fetch origin
git merge --no-edit origin/data-Japan
git merge --no-edit origin/data-North_America     # one line per region
```

Run the merges whenever a region branch advances (a cron job on the build machine is
the natural place; the spsp nightly does not do it yet because only Japan exists). The
trees are disjoint and every region branch carries the same `main`, so the merges
never conflict; if one does, something was committed on the wrong branch — fix it
there, not on `data-all`. `users.jsonl` is per region; union them by `user_id` when
loading (later rows win). No script does this yet.

A consumer that needs one region only (the current spsp build) checks out that
region's branch directly; `data-all` is not required.

## Squashing history

Nightly commits rewrite the two index files, so a data branch grows by a few MB per
week. Once a season (or whenever a clone becomes annoying):

```sh
git checkout data-Japan
git checkout --orphan tmp && git commit -q -m "data-Japan: squashed $(date +%F)"
git branch -M tmp data-Japan
git push --force-with-lease origin data-Japan
```

Tell every operator to re-clone the branch. Never squash `main`.

## Manual downloads and repairs

All commands run from the repository root with `STARTGG_TOKEN` set. Tools that read or
write index files take `--region <Region>` (the index paths become
`data/startgg/<Region>/...`) or explicit `--users-file-path` / `--tournament-file-path`
/ `--done-file-path` arguments.

| Task | Command |
|---|---|
| Re-download one tournament / event | edit `target_events` in `scripts/common/manual/download_specific_event.py`, then `python3 scripts/common/manual/download_specific_event.py --region Japan` |
| Re-download a window (e.g. after a results correction) | `python3 scripts/common/download.py --country-code JP --start-date <d1> --finish-date <d0>` — done tournaments inside the refresh window are re-fetched automatically; for older ones remove the tournament id from `done.csv` first |
| Events with missing champion or missing sets | `python3 scripts/common/manual/refetch_incomplete_events.py --token "$STARTGG_TOKEN" [--dry-run]` (detects them locally under `data/startgg/Japan/events`, then refetches; takes no index arguments) |
| Refresh player profiles (city, tag, links) | `python3 scripts/common/manual/refresh_users.py --region Japan [--max-users N]` (resumable; keeps a checkpoint and cursor) |
| Refresh profiles of the top N ranked players only | `python3 scripts/common/manual/refresh_top_ranked.py --region Japan --rank-source <latest_tjpr_full.jsonl>` |
| Rescan for class brackets missed earlier | `python3 scripts/Japan/manual/rescan_lower_class.py --region Japan` |
| Backfill a new field into existing files | `manual/backfill_end_at.py --region Japan`, `manual/backfill_wave_start_at.py --root data/startgg/Japan/events`, `fix/backfill_events.py --region Japan` (see each `--help`) |
| Character picks for older events | `fetch_character_games.py` then `merge_character_games.py` |

After a manual repair, commit on the data branch:

```sh
git add -A -- data/startgg
git commit -m "data: <what and why>"
git push origin data-Japan
```

## Hand-maintained tables (`data/startgg/Japan/manual/`)

Edit the JSON, commit on `data-Japan`, push. The next nightly picks it up (the build reads
them directly; `user_merges.json` is also copied to the site for the seed tool).

* `user_merges.json` — add `{"old": <uid>, "new": <uid>, "note": "why"}` to `merges`. Verify
  first that the two accounts never entered the same tournament (a self-match is dropped, a
  duplicate standing keeps the first row).
* `overseas_manual.json` — `uids` (overseas players without a country) and `jp_uids`
  (registered abroad, treated as Japanese). Players with a country are classified
  automatically and do not need to be listed.
* `awaiting_resume.json` — use `spsp/cli/awaiting_resume.py --add <event_id> "<note>"` /
  `--prune` from the spsp checkout, or edit by hand.

## Checks

| Check | Command |
|---|---|
| Structural validation of every event directory and the index | `python3 scripts/common/fix/validate_data.py --region Japan [--strict]` |
| Same, as per-category counts compared with the accepted baseline (what the nightly runs) | `python3 scripts/common/fix/validate_data.py --region Japan --baseline data/startgg/Japan/validation_baseline.json` |
| Every event directory is registered in `tournaments.jsonl` | `python3 scripts/common/fix/check_events_in_tournaments.py --region Japan` |
| Index entries whose files are missing | `python3 scripts/common/fix/fix_missing_tournaments.py --region Japan --dry-run` |
| Unit tests | `python3 -m unittest discover -s scripts/test` |
| Download regression (spsp repository) | `SPSP_DL_SCRIPTS=<this checkout> python3 tests/fetch/dl_golden.py replay --fixture tests/fetch/fixtures/dl_202609` plus `python3 -m pytest tests/fetch` |

The nightly (spsp step `[2.57/5]`) runs the baseline form. Several hundred events always
report something — entrants without a start.gg account, tournaments that never ran a
bracket — and the ranking build tolerates all of it, so the check only watches for a
**jump**: it counts the findings per category and compares them with
`data/startgg/<Region>/validation_baseline.json`. Within the tolerance (`--tolerance`,
default 10 per category) the baseline is rewritten to the current counts, so slow growth
follows along by itself; above it the run prints `REGRESSION:` lines, adds a warning to
`FAILED_STEPS` and leaves the baseline untouched, so the warning stays until someone looks.
The build is never stopped by this step. After confirming a jump is legitimate (a bulk
import, a new kind of event), accept it with
`--baseline <file> --write-baseline`.


## When the nightly fails

The spsp log ends with a `FAILED_STEPS` list. For the download step:

* **`Max retries exceeded`** on the tournament listing: start.gg outage or token
  problem. The run aborts; the next run retries everything. Check the token if it
  persists.
* **`failed_events.log` grew**: individual events failed. They are not marked done and
  are retried next run; if the same event keeps failing, download it with
  `download_specific_event.py` and look at the error.
* **`data push failed`**: the data branch was pushed from elsewhere. Run
  `git pull --rebase origin data-Japan` in the checkout and push; the commit already
  exists locally.
* **`validate_data`**: a category of known inconsistency grew beyond the tolerance. The
  `REGRESSION:` lines in the log name it; run the check by hand (see "Checks") to see the
  individual events. Nothing is broken for the build — decide whether the jump is a real
  download problem or a legitimate change, then re-baseline.
* **Checkout dirty with unexpected files**: the nightly commits only `data/startgg/`.
  Stray files (checkpoints, `.bak`) are ignored by the data branch's `.gitignore`; add
  new patterns there (on `main`, then merge) rather than committing them.

## GitHub settings

Issues are enabled; wiki and projects are disabled. The default branch is `main`.
Data branches are fetched by operators only; keep `--single-branch` in clone
instructions.

**Actions.** One workflow, `.github/workflows/tests.yml`, runs
`python -m unittest discover -s scripts/test` on pull requests to `main` (and on pushes
to it). It installs `jpholiday` and nothing else, downloads no data and needs no token —
the actual downloading always runs on an operator's own machine, never here.

**Protected branches.**

| Branch | Rule |
|---|---|
| `main` | Pull request required (0 approvals), `tests` must pass, conversations resolved, no force-push, no deletion. Administrators are exempt, so the owner can still push directly; everyone else goes through a PR |
| `data-*` | Force-push and deletion blocked by a repository ruleset (administrators can bypass). Ordinary pushes are untouched, so an operator's nightly keeps working |

A region operator therefore needs write access, pushes only their own `data-<Region>`
branch, and proposes script or documentation changes as a pull request against `main`.

## Licence

The code is MIT (see [LICENSE](../LICENSE)). The tournament data on the data branches
comes from the start.gg API and is not ours to relicense — it is mirrored here so the
rankings can be reproduced, and stays subject to start.gg's terms. `users.jsonl` holds
only what players made public on their start.gg profile; removal requests are handled by
deleting the records from the branch head (see LICENSE).
