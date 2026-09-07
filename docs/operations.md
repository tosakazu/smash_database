# Operations

Day-to-day handling of the repository: branches, adding a region, manual repairs,
checks, and what to do when the nightly run fails. The layout itself is described in
the top-level [README](../README.md).

## Branch rules

| Branch | Commits allowed | Never |
|---|---|---|
| `main` | scripts, docs, tests | anything under `data/` |
| `data-<Region>` | files under `data/startgg/<Region>/` and `data/startgg/<Region>/events/`, and merges of `main` | direct edits to scripts or docs, other regions' paths |
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

2. Download from the repository root:
   `STARTGG_TOKEN=... python3 scripts/common/download.py --country-code US --start-date ... --finish-date ...`.
   The index files default to `data/startgg/North_America/` and events go to
   `data/startgg/North_America/events/`.
3. `git add -A -- data/startgg && git commit && git push -u origin data-North_America`.
   Add a nightly job on the operator's machine that repeats steps 2–3.

`country_code2region()` in `utils.py` decides the region name from the country code
(`US`, `CA`, `MX`, `DO` → `North America`). Add codes there when a region needs them.

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

## Checks

| Check | Command |
|---|---|
| Structural validation of every event directory and the index | `python3 scripts/common/fix/validate_data.py --region Japan [--strict]` |
| Every event directory is registered in `tournaments.jsonl` | `python3 scripts/common/fix/check_events_in_tournaments.py --region Japan` |
| Index entries whose files are missing | `python3 scripts/common/fix/fix_missing_tournaments.py --region Japan --dry-run` |
| Unit tests | `python3 -m unittest discover -s scripts/test` |
| Download regression (spsp repository) | `SPSP_DL_SCRIPTS=<this checkout> python3 tests/fetch/dl_golden.py replay --fixture tests/fetch/fixtures/dl_202609` plus `python3 -m pytest tests/fetch` |

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
* **Checkout dirty with unexpected files**: the nightly commits only `data/startgg/`.
  Stray files (checkpoints, `.bak`) are ignored by the data branch's `.gitignore`; add
  new patterns there (on `main`, then merge) rather than committing them.

## GitHub settings

Issues are enabled; wiki, projects and GitHub Actions are disabled (the download is
not run on GitHub). The default branch is `main`. Data branches are fetched by
operators only; keep `--single-branch` in clone instructions.
