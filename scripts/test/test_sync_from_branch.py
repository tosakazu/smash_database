"""scripts/common/sync_from_branch.py: the operator's changes win, the Actions' own additions survive."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "common" / "sync_from_branch.py"
sys.path.insert(0, str(ROOT))
from scripts.common.sync_from_branch import merge_keyed  # noqa: E402

D = "data/startgg/North_America"


def u(uid, tag):
    return json.dumps({"user_id": uid, "gamer_tag": tag, "version": 1})


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def write(cwd, files):
    for p, text in files.items():
        f = Path(cwd, p)
        if text is None:
            f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")


def commit(cwd, msg):
    git(cwd, "add", "-A"); git(cwd, "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")


class MergeKeyedTests(unittest.TestCase):
    def test_operator_change_wins_and_ours_survive(self):
        old = "\n".join([u(1, "a"), u(2, "b")]) + "\n"
        new = "\n".join([u(1, "a"), u(2, "B"), u(3, "c")]) + "\n"          # operator: changed 2, added 3
        ours = "\n".join([u(1, "a1"), u(2, "b"), u(4, "d")]) + "\n"        # actions: refreshed 1, added 4
        merged, st = merge_keyed(ours, old, new, "user_id", False)
        tags = {json.loads(l)["user_id"]: json.loads(l)["gamer_tag"] for l in merged.splitlines()}
        self.assertEqual(tags, {1: "a1", 2: "B", 3: "c", 4: "d"})
        self.assertEqual(st, {"taken": 1, "added": 1, "removed": 0})

    def test_removed_by_operator(self):
        merged, st = merge_keyed(u(1, "a") + "\n" + u(2, "b") + "\n", u(1, "a") + "\n" + u(2, "b") + "\n",
                                 u(1, "a") + "\n", "user_id", False)
        self.assertEqual(merged, u(1, "a") + "\n")
        self.assertEqual(st["removed"], 1)

    def test_lines_as_set_and_sorted(self):
        merged, _ = merge_keyed("1\n5\n", "1\n", "1\n3\n", None, False)
        self.assertEqual(merged, "1\n3\n5\n")   # the operator's order, then ours
        img = lambda t: json.dumps({"tournament_id": t, "url": None})
        merged, _ = merge_keyed(img(5) + "\n", "", img(2) + "\n", "tournament_id", True)   # sort_by_key=True
        self.assertEqual(merged, img(2) + "\n" + img(5) + "\n")


class SyncScriptTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            op = Path(tmp, "op"); op.mkdir(); git(op, "init", "-q", "-b", "data")
            git(op, "config", "user.email", "t@example.com"); git(op, "config", "user.name", "t")
            write(op, {f"{D}/done.csv": "1\n", f"{D}/users.jsonl": u(1, "a") + "\n",
                       f"{D}/events/2026/11/01/A/e/attr.json": "{}", f"{D}/events/2026/11/01/Old/e/attr.json": "{}",
                       "scripts/North_America/classify.py": "V = 1\n"})
            old = commit(op, "base")
            # the actions branch: starts from the operator's commit and adds its own tournament
            git(op, "checkout", "-q", "-b", "auto")
            write(op, {f"{D}/done.csv": "1\n2\n", f"{D}/users.jsonl": u(1, "a") + "\n" + u(2, "b") + "\n",
                       f"{D}/events/2026/11/02/B/e/attr.json": '{"ours": 1}'})
            commit(op, "actions")
            # the operator: the same tournament B (their version), a new one C, a rule change, a deletion
            git(op, "checkout", "-q", "data")
            write(op, {f"{D}/done.csv": "1\n2\n3\n", f"{D}/users.jsonl": u(1, "A") + "\n" + u(3, "c") + "\n",
                       f"{D}/events/2026/11/02/B/e/attr.json": '{"theirs": 1}',
                       f"{D}/events/2026/11/02/C/e/attr.json": "{}",
                       f"{D}/events/2026/11/01/Old/e/attr.json": None,
                       "scripts/North_America/classify.py": "V = 2\n"})
            new = commit(op, "operator")
            git(op, "checkout", "-q", "auto")
            subprocess.run([sys.executable, str(SCRIPT), "--region", "North_America", "--old", old, "--new", new],
                           cwd=op, check=True, stdout=subprocess.PIPE)
            r = lambda p: Path(op, p).read_text(encoding="utf-8")
            self.assertEqual(sorted(r(f"{D}/done.csv").split()), ["1", "2", "3"])
            users = {json.loads(l)["user_id"]: json.loads(l)["gamer_tag"] for l in r(f"{D}/users.jsonl").splitlines()}
            self.assertEqual(users, {1: "A", 2: "b", 3: "c"})
            self.assertEqual(r(f"{D}/events/2026/11/02/B/e/attr.json"), '{"theirs": 1}')
            self.assertTrue(Path(op, f"{D}/events/2026/11/02/C/e/attr.json").exists())
            self.assertFalse(Path(op, f"{D}/events/2026/11/01/Old/e/attr.json").exists())
            self.assertTrue(Path(op, f"{D}/events/2026/11/01/A/e/attr.json").exists())
            self.assertEqual(r("scripts/North_America/classify.py"), "V = 2\n")


if __name__ == "__main__":
    unittest.main()
