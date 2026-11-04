"""users.jsonl / tournaments.jsonl / tournament_images.jsonl split into id-range shards (jsonl_table, issue #106): every reader and
writer sees the same records as with the single file, and only the shards that changed are rewritten."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.common import jsonl_table as jt  # noqa: E402
from scripts.common import utils  # noqa: E402
from scripts.common.manual import shard_jsonl  # noqa: E402
from scripts.test.test_sync_from_branch import D, commit, git, write  # noqa: E402

SYNC = ROOT / "scripts" / "common" / "sync_from_branch.py"


def u(uid, tag="t"):
    return json.dumps({"user_id": uid, "gamer_tag": tag, "startgg_discriminator": None, "x_id": None, "version": "1.0"},
                      ensure_ascii=False)


# one shard each for 0..499,999 / 500,000..999,999 / 2,000,000..
LINES = [u(35, "a"), u(3_000_000, "ゆ"), u(600_000, "b"), u(36, "c"), u(2_100_000, "d")]


def make_single(d: Path) -> Path:
    p = d / "users.jsonl"
    p.write_text("".join(l + "\n" for l in LINES), encoding="utf-8")
    return p


class ShardToolTests(unittest.TestCase):
    def test_split_moves_lines_verbatim_and_join_restores_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_single(Path(tmp))
            before = p.read_text(encoding="utf-8")
            shard_jsonl.main(["--region", "X", "--data-dir", tmp, "--table", "users.jsonl"])
            self.assertFalse(p.exists())
            self.assertEqual([f.name for f in jt.shard_files(p)], ["0.jsonl", "1.jsonl", "4.jsonl", "6.jsonl"])
            self.assertEqual((Path(tmp) / "users" / "0.jsonl").read_text(encoding="utf-8"), LINES[0] + "\n" + LINES[3] + "\n")
            self.assertEqual(sorted(jt.read_text(p).splitlines()), sorted(before.splitlines()))
            shard_jsonl.main(["--region", "X", "--data-dir", tmp, "--join"])
            self.assertFalse((Path(tmp) / "users").exists())
            self.assertEqual(sorted(p.read_text(encoding="utf-8").splitlines()), sorted(before.splitlines()))

    def test_both_layouts_at_once_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_single(Path(tmp))
            (Path(tmp) / "users").mkdir()
            with self.assertRaises(RuntimeError):
                utils.read_users_jsonl(str(p))


class ReadWriteTests(unittest.TestCase):
    def test_readers_see_the_same_records(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            single = make_single(Path(a))
            split = make_single(Path(b))
            shard_jsonl.main(["--region", "X", "--data-dir", b, "--table", "users.jsonl"])
            self.assertEqual(utils.read_users_jsonl(str(single)), utils.read_users_jsonl(str(split)))
            self.assertEqual(sorted(jt.iter_lines(single)), sorted(jt.iter_lines(split)))
            self.assertEqual(utils.read_users_jsonl(str(Path(b) / "nothing" / "users.jsonl")), {})

    def test_write_touches_only_changed_shards_and_extend_appends_to_the_right_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_single(Path(tmp))
            shard_jsonl.main(["--region", "X", "--data-dir", tmp, "--table", "users.jsonl"])
            users = utils.read_users_jsonl(str(p))
            users[600_000]["gamer_tag"] = "B2"
            written = []
            real = jt._atomic_write
            with mock.patch.object(jt, "_atomic_write", side_effect=lambda f, t: (written.append(f.name), real(f, t))):
                utils.write_jsonl(list(users.values()), str(p), with_version=True)
            self.assertEqual(written, ["1.jsonl"])
            self.assertFalse(p.exists())
            utils.extend_jsonl([{"user_id": 999_999, "gamer_tag": "new"}, {"user_id": 7_000_000, "gamer_tag": "far"}],
                               str(p), with_version=True)
            self.assertEqual(json.loads((Path(tmp) / "users" / "1.jsonl").read_text().splitlines()[-1])["user_id"], 999_999)
            self.assertTrue((Path(tmp) / "users" / "14.jsonl").exists())
            again = utils.read_users_jsonl(str(p))
            self.assertEqual(again[600_000]["gamer_tag"], "B2")
            self.assertEqual(len(again), len(LINES) + 2)
            # a shard whose records are all gone is removed
            utils.write_jsonl([r for k, r in again.items() if k != 7_000_000], str(p), with_version=True)
            self.assertFalse((Path(tmp) / "users" / "14.jsonl").exists())

    def test_single_file_write_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_single(Path(tmp))
            before = p.read_text(encoding="utf-8")
            utils.write_jsonl(list(utils.read_users_jsonl(str(p)).values()), str(p), with_version=True)
            self.assertEqual(p.read_text(encoding="utf-8"), before)
            self.assertFalse((Path(tmp) / "users").exists())

    def test_tournaments_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tournaments.jsonl"
            recs = [{"tournament_id": t, "name": str(t), "events": []} for t in (949_759, 11_858, 120_000)]
            p.write_text("".join(json.dumps(r) + "\n" for r in recs))
            shard_jsonl.main(["--region", "X", "--data-dir", tmp, "--table", "tournaments.jsonl"])
            self.assertEqual([f.name for f in jt.shard_files(p)], ["0.jsonl", "1.jsonl", "9.jsonl"])
            self.assertEqual(set(utils.read_tournaments_jsonl(str(p))), {949_759, 11_858, 120_000})

    def test_tournament_images_table(self):
        from scripts.common import tournament_images as ti
        url = "https://images.start.gg/images/tournament/1/image-a.png"
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(ti.images_path_for(Path(tmp) / "tournaments.jsonl"))
            index = {}
            for t in (949_759, 11_858, 120_000):
                ti.update(index, t, [{"url": url, "width": 1, "height": 1}])
            ti.save(index, p)
            single = p.read_text(encoding="utf-8")
            shard_jsonl.main(["--region", "X", "--data-dir", tmp, "--table", "tournament_images.jsonl"])
            self.assertFalse(p.exists())
            self.assertEqual([f.name for f in jt.shard_files(p)], ["0.jsonl", "1.jsonl", "9.jsonl"])
            self.assertEqual(ti.load(p), index)
            # save keeps the split layout, writes only the shard that changed, and the lines match the single file
            before = {f.name: f.stat().st_mtime_ns for f in jt.shard_files(p)}
            ti.update(index, 949_759, [])
            ti.save(index, p)
            self.assertFalse(p.exists())
            after = {f.name: f.stat().st_mtime_ns for f in jt.shard_files(p)}
            self.assertEqual([n for n in before if before[n] != after[n]], ["9.jsonl"])
            self.assertEqual(ti.load(p), index)
            ti.update(index, 949_759, [{"url": url, "width": 1, "height": 1}])
            ti.save(index, p)
            self.assertEqual(sorted(jt.read_text(p).splitlines()), sorted(single.splitlines()))

    def test_large_file_warning(self):
        from scripts.common.fix import validate_data
        with tempfile.TemporaryDirectory() as tmp:
            make_single(Path(tmp))
            with mock.patch.object(jt, "WARN_BYTES", 10):
                self.assertEqual(len(validate_data.large_index_files(Path(tmp))), 1)
            self.assertEqual(validate_data.large_index_files(Path(tmp)), [])


class SyncTests(unittest.TestCase):
    def run_sync(self, ours_split, theirs_split):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        op = Path(tmp.name)
        git(op, "init", "-q", "-b", "data")
        git(op, "config", "user.email", "t@example.com"); git(op, "config", "user.name", "t")
        write(op, {f"{D}/users.jsonl": u(1, "a") + "\n" + u(600_000, "b") + "\n"})
        old = commit(op, "base")
        git(op, "checkout", "-q", "-b", "auto")
        write(op, {f"{D}/users.jsonl": u(1, "a") + "\n" + u(600_000, "b") + "\n" + u(2, "ours") + "\n"})
        if ours_split:
            shard_jsonl.main(["--region", "X", "--data-dir", str(op / D), "--table", "users.jsonl"])
        commit(op, "actions")
        git(op, "checkout", "-q", "data")
        write(op, {f"{D}/users.jsonl": u(1, "A") + "\n" + u(600_000, "b") + "\n" + u(3_000_000, "c") + "\n"})
        if theirs_split:
            shard_jsonl.main(["--region", "X", "--data-dir", str(op / D), "--table", "users.jsonl"])
        new = commit(op, "operator")
        git(op, "checkout", "-q", "auto")
        subprocess.run([sys.executable, str(SYNC), "--region", "North_America", "--old", old, "--new", new],
                       cwd=op, check=True, stdout=subprocess.PIPE)
        p = op / D / "users.jsonl"
        tags = {json.loads(l)["user_id"]: json.loads(l)["gamer_tag"] for l in jt.read_text(p).splitlines()}
        self.assertEqual(tags, {1: "A", 2: "ours", 600_000: "b", 3_000_000: "c"})
        return p

    def test_all_layouts(self):
        for ours_split, theirs_split in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(ours=ours_split, theirs=theirs_split):
                p = self.run_sync(ours_split, theirs_split)
                # ours stays split, and follows the operator once they split
                self.assertEqual(jt.is_sharded(p), ours_split or theirs_split)


if __name__ == "__main__":
    unittest.main()
