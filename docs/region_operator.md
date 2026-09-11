# Running a region

This is the page for whoever keeps one region's data up to date — for example
North America. Everything you need is one script, one branch and one token; the
ranking build, the other regions and the shared scripts are not your concern.

## What you own

| | |
|---|---|
| Branch | `data-<Region>` (North America: `data-North_America`). Only you push to it |
| Directory | `data/startgg/<Region>/` — events, the index files, `upcoming.json`, `validation_baseline.json`, optional `manual/` |
| Rules | `scripts/<Region>/classify.py` — what counts as a 1-on-1 event, the calendar and holidays, class-bracket names. Changes go to `main` through a pull request |

What the files are is in [data_model.md](data_model.md); what you should *not* touch
(scripts, other regions' paths, `main` directly) is in [operations.md](operations.md).

## One-time setup

```sh
git clone --single-branch --branch data-North_America git@github.com:tosakazu/smash_database.git
cd smash_database
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+

# start.gg API token (https://start.gg/admin/profile/developer) — never commit it, never put it on a command line
mkdir -p ~/.config/smash_database
printf '%s\n' '<your token>' > ~/.config/smash_database/STARTGG_TOKEN
chmod 600 ~/.config/smash_database/STARTGG_TOKEN
```

The branch already contains the scripts and docs; you do not need `main` checked out.

## Daily run

```sh
cd smash_database
bash scripts/common/run_region.sh --country-code US --days 14 --python .venv/bin/python3
```

That does, in order:

1. **Download** the tournaments that ended in the last `--days` days (`download.py`).
   Resumable — `done.csv` remembers what is complete, so a crash or a Ctrl-C just
   means the next run continues. The script itself retries twice.
2. **Upcoming tournaments** for the next 21 days → `upcoming.json`.
3. **Class brackets** (Amateur / Novice … brackets run inside a tournament) →
   `phases.json`, `class_phases/`, and one virtual event per class.
4. **Judgements** → `derived.json` next to each event, `users_derived.jsonl`.
5. **Checks** — counts the known kinds of inconsistency and warns if one jumped.
6. **Commit and push** `data/startgg/<Region>/` on `data-<Region>`.

Options: `--no-push` (commit only), `--no-commit` (look at `git status` first),
`--dry-run` (print the commands). `--token-file PATH` if the token is elsewhere.
The log goes to `~/.local/log/smash_database/<Region>_<date>.log` (kept 60 days);
the last lines are a summary:

```
═══ North_America (US) 2026-09-12 03:41:07  rc=0  1893s ═══
  events: 1210 → 1274   tournaments.jsonl: 802 → 845 行   users.jsonl: 9310 → 9502 行
  commit: 4f2a9c1e0d
```

`rc=0` and no "⚠️" block means everything went through. Two runs cannot overlap
(a lock file); a second one just prints `already running` and exits.

### From cron

```
17 */6 * * *  cd /home/you/smash_database && bash scripts/common/run_region.sh --country-code US --days 14 --python .venv/bin/python3 >/dev/null 2>&1
```

Every 6 hours is plenty — the download window is 14 days, so a missed run costs
nothing. The first run over a long window takes a while (the US has a few hundred
Ultimate events a week and the API allows about 80 requests a minute); after that a
run is minutes.

## The first run

Start with a short window so you can look at the result before committing:

```sh
bash scripts/common/run_region.sh --country-code US --days 7 --no-commit --python .venv/bin/python3
git status                                   # everything should be under data/startgg/North_America/
python3 scripts/common/fix/validate_data.py --region North_America --summary
```

Then decide how far back you want history (`--days 365` for a year; it is resumable,
so just rerun until the download step reports nothing new) and commit:

```sh
git add -A -- data/startgg/North_America && git commit -m "data: initial North America import" && git push -u origin data-North_America
```

## When something is off

| Symptom | What to do |
|---|---|
| `Max retries exceeded` on the tournament list | start.gg is down or the token is wrong. Nothing was lost; run again later |
| `failed_events.log` in the repository root grew | Individual events failed. They are not marked done and are retried next run; if one keeps failing, `python3 scripts/common/manual/download_specific_event.py --help` |
| `REGRESSION:` lines from the check step | A kind of inconsistency jumped (e.g. many events with no `matches.json`). Run `validate_data.py --region North_America` without `--baseline` to see them. If it is legitimate (a bulk import), accept with `--baseline data/startgg/North_America/validation_baseline.json --write-baseline` |
| `push failed` | Someone else pushed to your branch. `git pull --rebase origin data-North_America` and run again |
| `ERROR: いま main にいる` | You are not on the data branch. `git switch data-North_America` |
| A phase name that is a class bracket but was not recognised (or the reverse) | Adjust `CLASS_PHASE_PATTERN` / `CLASS_LETTER_PATTERN` in `scripts/North_America/classify.py`, bump `CLASSIFIER_VERSION`, open a PR. After it is merged, `git merge origin/main` on your branch and run `derive.py --region North_America --all` |
| A holiday is wrong or missing | Same, `HOLIDAY_RULES` in the same file. Provincial / state holidays are not in yet |

## Getting script changes

Scripts and docs live on `main`. When something there changes, bring it into your
branch (between runs):

```sh
git fetch origin main && git merge --no-edit origin/main && git push origin data-North_America
```

Never edit scripts on the data branch; make the change on a branch off `main` and open
a pull request — the tests run automatically, and someone will merge it.
