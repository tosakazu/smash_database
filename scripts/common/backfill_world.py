"""Backfill the past of every region other than Japan / North America (backfill-world workflow, 2026-11-02).

Each region gets its own data branch data-<Region> (main + data/startgg/<Region>/, like data-Japan and
data-North_America). This never touches Japan or North America: their regions are refused, and only
data/startgg/<Region>/ of the region being filled is committed.

    python scripts/common/backfill_world.py --workdir W --minutes 320 [--regions Europe,Korea,...]

Work is cut into calendar months, newest first, from last month back to 2018-12 (Smash Ultimate's release).
For each month every country of the region is downloaded with scripts/common/download.py
(--start-date <month end> --finish-date <month start>). Progress is kept in
data/startgg/<Region>/backfill_progress.json: a country is recorded once its download of the month finished,
a month once all its countries did. After every month the branch is committed and pushed, so a run that
hits the time limit loses at most the country it was in (done.csv makes the rerun skip what was saved).
The current month is left to a regular fetch.

Prints "REMAINING=<n>" (months still to do over all regions) as its last line; the workflow re-dispatches
itself while it is > 0.

Environment: STARTGG_TOKEN (for download.py only), GH_TOKEN (push), GITHUB_REPOSITORY.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the main checkout these scripts come from
sys.path.insert(0, str(ROOT))
from scripts.common.utils import OTHER_REGION_COUNTRIES  # noqa: E402

DATA_BRANCH_GITIGNORE = """# data-<Region> = main (scripts / docs / README) + that region's data under data/startgg/.
# Merge main to update the scripts. Only generated and temporary files under data/startgg/ are ignored here.
download.out
*/__pycache__/
__pycache__/
*.py[cod]
*.bak
*.dupbak
affected_tournament_ids.txt
data_integrity_report.jsonl
failed_events.log
data_audits/
logs/
data/startgg/*.bak*
data/startgg/*/*.bak*
data/startgg/.refresh_*
data/startgg/*/users_ranked.jsonl
data/startgg/*/*checkpoint*
"""
FIRST_MONTH = dt.date(2018, 12, 1)
MAX_FAILURES = 3
FORBIDDEN = {"Japan", "North_America"}
DEFAULT_ORDER = ["Europe", "Korea", "East Asia", "Other Asia", "Oceania", "South America", "Middle East Africa"]


def dir_name(region: str) -> str:
    return region.replace(" ", "_").replace("/", "-")


def months(today: dt.date) -> list[str]:
    """'YYYY-MM' from last month back to FIRST_MONTH."""
    out = []
    y, m = today.year, today.month
    while True:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        if dt.date(y, m, 1) < FIRST_MONTH:
            return out
        out.append(f"{y:04d}-{m:02d}")


def month_window(ym: str) -> tuple[str, str]:
    """(start-date = first day of the next month, finish-date = first day) for download.py (which walks newest → oldest
    from start-date, exclusive, so the next month's 1st covers the whole last day; the overlap is skipped by done.csv)."""
    y, m = map(int, ym.split("-"))
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt.isoformat(), first.isoformat()


def run(cmd, cwd, env=None, timeout=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, check=check)


def run_until(cmd, cwd, env, seconds) -> int | None:
    """Run; at the deadline stop it with SIGINT like `timeout --signal=INT` (download.py finishes the file it is
    writing), SIGKILL only if it is still there 120 s later. None = stopped at the deadline."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env)
    try:
        return p.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
        return None


def git_out(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def auth_args() -> list[str]:
    tok = os.environ["GH_TOKEN"]
    hdr = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    return ["-c", f"http.extraheader=AUTHORIZATION: basic {hdr}"]


def repo_url() -> str:
    return f"https://github.com/{os.environ['GITHUB_REPOSITORY']}.git"


def prepare_branch(workdir: Path, region: str, main_sha: str) -> Path:
    """A depth-1 clone of data-<Region>; created from main when it does not exist yet."""
    branch = f"data-{dir_name(region)}"
    repo = workdir / branch
    if repo.exists():
        return repo
    exists = subprocess.run(["git", "ls-remote", "--exit-code", repo_url(), f"refs/heads/{branch}"],
                            stdout=subprocess.DEVNULL).returncode == 0
    if exists:
        run(["git", "clone", "-q", "--depth", "1", "--branch", branch, repo_url(), str(repo)], cwd=workdir)
    else:
        print(f"[backfill] {branch} does not exist yet: creating it from main {main_sha[:10]}", flush=True)
        run(["git", "clone", "-q", "--depth", "1", repo_url(), str(repo)], cwd=workdir)
        run(["git", "checkout", "-q", "-b", branch], cwd=repo)
    run(["git", "config", "user.name", "github-actions[bot]"], cwd=repo)
    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=repo)
    if not exists:
        # main ignores data/ entirely; a region branch tracks data/startgg/<Region>/ (same .gitignore as data-North_America)
        (repo / ".gitignore").write_text(DATA_BRANCH_GITIGNORE, encoding="utf-8")
        run(["git", "add", ".gitignore"], cwd=repo)
        run(["git", "commit", "-q", "-m", f"{branch}: region data branch (main + data/startgg/{dir_name(region)}/)"], cwd=repo)
    return repo


def load_progress(path: Path) -> dict:
    if path.exists():
        prog = json.loads(path.read_text(encoding="utf-8"))
        prog.setdefault("failures", {}); prog.setdefault("gave_up", [])
        return prog
    return {"done_months": [], "partial": {}, "failures": {}, "gave_up": []}


def save_progress(path: Path, prog: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prog["done_months"] = sorted(set(prog["done_months"]), reverse=True)
    path.write_text(json.dumps(prog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def commit_push(repo: Path, region: str, msg: str) -> None:
    ddir = f"data/startgg/{dir_name(region)}"
    run(["git", "add", "-A", "--", ddir], cwd=repo)
    # nothing outside this region's directory may change (Japan / North America / scripts are never written)
    other = [p for p in git_out(repo, "diff", "--cached", "--name-only").splitlines() if not p.startswith(ddir + "/")]
    if other:
        raise SystemExit(f"refusing to commit paths outside {ddir}: {other[:5]}")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode == 0:
        return
    run(["git", "commit", "-q", "-m", msg], cwd=repo)
    branch = f"data-{dir_name(region)}"
    for attempt in range(3):
        if subprocess.run(["git", *auth_args(), "push", "-q", repo_url(), f"HEAD:refs/heads/{branch}"], cwd=repo).returncode == 0:
            print(f"[backfill] pushed {branch} {git_out(repo, 'rev-parse', '--short', 'HEAD')}: {msg}", flush=True)
            return
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"push to {branch} failed 3 times")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--minutes", type=float, default=320, help="stop starting new downloads after this many minutes")
    ap.add_argument("--regions", default=",".join(DEFAULT_ORDER), help="comma separated, in this order")
    args = ap.parse_args()
    deadline = time.time() + args.minutes * 60
    workdir = Path(args.workdir); workdir.mkdir(parents=True, exist_ok=True)
    main_sha = git_out(ROOT, "rev-parse", "HEAD") if (ROOT / ".git").exists() else "main"
    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    for r in regions:
        if dir_name(r) in FORBIDDEN or r not in OTHER_REGION_COUNTRIES:
            raise SystemExit(f"not a backfill region: {r}")
    all_months = months(dt.date.today())
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    download = str(ROOT / "scripts" / "common" / "download.py")

    remaining = 0
    out_of_time = False
    for region in regions:
        ddir = f"data/startgg/{dir_name(region)}"
        repo = None
        prog = None
        todo = None
        for ym in all_months:
            if out_of_time:
                break
            if repo is None:
                repo = prepare_branch(workdir, region, main_sha)
                prog = load_progress(repo / ddir / "backfill_progress.json")
                todo = [m for m in all_months if m not in prog["done_months"]]
                if not todo:
                    print(f"[backfill] {region}: complete", flush=True)
                    break
            if ym in prog["done_months"]:
                continue
            start, finish = month_window(ym)
            got = set(prog["partial"].get(ym, []))
            for cc in OTHER_REGION_COUNTRIES[region]:
                if cc in got:
                    continue
                left = deadline - time.time()
                if left < 120:
                    out_of_time = True
                    break
                print(f"── {region} {ym} {cc} (残り {int(left // 60)} 分)", flush=True)
                rc = run_until([sys.executable, "-u", download, "--country-code", cc,
                                "--start-date", start, "--finish-date", finish], repo, env, left)
                if rc is None:   # stopped at the time limit (what was saved stays; done.csv skips it next run)
                    out_of_time = True
                    break
                if rc != 0:
                    key = f"{ym}/{cc}"
                    prog["failures"][key] = prog["failures"].get(key, 0) + 1
                    if prog["failures"][key] < MAX_FAILURES:
                        print(f"::warning::{region} {ym} {cc}: download rc={rc} (retried next run)", flush=True)
                        continue
                    # give up on this country-month so the backfill can finish (listed in gave_up to redo by hand)
                    print(f"::warning::{region} {ym} {cc}: download failed {MAX_FAILURES} times, giving up", flush=True)
                    prog["gave_up"] = sorted(set(prog["gave_up"]) | {key})
                got.add(cc)
                prog["partial"][ym] = sorted(got)
            if set(OTHER_REGION_COUNTRIES[region]) <= got:
                prog["done_months"].append(ym)
                prog["partial"].pop(ym, None)
            save_progress(repo / ddir / "backfill_progress.json", prog)
            commit_push(repo, region, f"data: {region} {ym} backfill ({len(got)}/{len(OTHER_REGION_COUNTRIES[region])} countries, GitHub Actions)")
        if prog is not None:
            remaining += len([m for m in all_months if m not in prog["done_months"]])
        elif out_of_time:
            remaining += len(all_months)   # not looked at this run
    print(f"REMAINING={remaining}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
