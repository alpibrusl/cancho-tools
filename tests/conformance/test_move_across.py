"""`move --across`: a file into another directory, without ever replacing a name and without losing a byte.

* A1, the result is what a careful `cp` then rename-to-tombstone leaves: the destination holds the bytes, the
  source is renamed (not deleted) to `.NAME.removed-HASH8` and holds the bytes too, nothing else changes;
* A2, never over anything: a destination name that exists, whatever it holds, is `conflict.exists` and both files
  are as they were; eight movers racing for one destination give exactly one winner and lose nothing;
* A3, it reaches nowhere it should not: a link as the source or as a directory of either path, a directory, an
  absolute or `..` path are refused; `--dry-run` changes nothing; a wrong `--if-sha256` changes nothing;
* A4, the answer is complete: `tombstone` names what was left behind, and the plain move is unchanged.
"""

import hashlib
import json
import os
import random
import subprocess
import unittest

from harness import Fixture, binary


def move(fx, *args):
    p = subprocess.run([binary("move"), "--root", str(fx.root), *args], capture_output=True, cwd=fx.dir)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"raw": p.stdout}


def tree(root):
    """Every entry under `root` by relative path: its hash, or its target for a link, or a marker for a directory."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for n in dirs + files:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                out[rel] = "-> " + os.readlink(full)
            elif os.path.isdir(full):
                out[rel] = "dir"
            else:
                out[rel] = hashlib.sha256(open(full, "rb").read()).hexdigest()
    return out


def without_locks(t):
    return {k: v for k, v in t.items() if not k.endswith(".lexsys-lock")}


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Result(unittest.TestCase):
    """A1, A4."""

    CONTENTS = [b"", b"x", b"line\nline two\n", bytes(range(256)) * 40, "ünï côdé\n".encode(), b"\x00\x00\x00"]
    NAMES = ["plain.txt", "with space.txt", "-dash.txt", "ünï-日本.txt", "UPPER.TXT", ".hidden", "x" * 200]

    def test_the_destination_has_the_bytes_and_the_source_is_a_tombstone_with_them(self):
        for data in self.CONTENTS:
            for name in self.NAMES:
                with self.subTest(size=len(data), name=name[:20]):
                    fx = Fixture()
                    (fx.root / "a").mkdir()
                    (fx.root / "b").mkdir()
                    (fx.root / "a" / name).write_bytes(data)
                    before = without_locks(tree(fx.root))
                    rc, out = move(fx, "--across", "a/" + name, "b/new.bin")
                    self.assertEqual(rc, 0, out)
                    h = sha(data)
                    tomb = ".%s.removed-%s" % (name, h[:8])
                    self.assertEqual((fx.root / "b" / "new.bin").read_bytes(), data)
                    self.assertFalse((fx.root / "a" / name).exists())
                    self.assertEqual((fx.root / "a" / tomb).read_bytes(), data)
                    self.assertEqual(out["data"]["tombstone"], "a/" + tomb)
                    self.assertEqual(out["data"]["sha256"], h)
                    self.assertTrue(out["data"]["changed"])
                    after = without_locks(tree(fx.root))
                    gone = set(before) - set(after)
                    new = set(after) - set(before)
                    self.assertEqual(gone, {"a/" + name})
                    self.assertEqual(new, {"b/new.bin", "a/" + tomb})
                    fx.cleanup()

    def test_the_plain_move_is_unchanged_and_has_no_tombstone_member(self):
        fx = Fixture()
        (fx.root / "p.txt").write_text("x")
        rc, out = move(fx, "p.txt", "q.txt")
        self.assertEqual(rc, 0)
        self.assertNotIn("tombstone", out["data"])
        fx.cleanup()

    def test_a_slash_without_across_says_how_to_cross(self):
        fx = Fixture()
        (fx.root / "p.txt").write_text("x")
        (fx.root / "d").mkdir()
        rc, out = move(fx, "p.txt", "d/q.txt")
        self.assertEqual(out["error"]["rule"], "path.bad-name")
        self.assertIn("--across", out["error"]["hint"])
        fx.cleanup()

    def test_the_same_path_is_left_alone(self):
        fx = Fixture()
        (fx.root / "p.txt").write_text("keep")
        rc, out = move(fx, "--across", "p.txt", "p.txt")
        self.assertEqual((rc, out["data"]["changed"]), (0, False))
        self.assertEqual((fx.root / "p.txt").read_text(), "keep")
        fx.cleanup()


class NeverOver(unittest.TestCase):
    """A2."""

    def test_any_existing_destination_is_a_conflict_and_nothing_changes(self):
        rng = random.Random(3)
        for case in range(25):
            fx = Fixture()
            (fx.root / "a").mkdir()
            (fx.root / "b").mkdir()
            src = bytes(rng.randrange(256) for _ in range(rng.randint(0, 300)))
            dst = src if case % 3 == 0 else bytes(rng.randrange(256) for _ in range(rng.randint(0, 300)))
            (fx.root / "a" / "s").write_bytes(src)
            (fx.root / "b" / "d").write_bytes(dst)
            before = without_locks(tree(fx.root))
            rc, out = move(fx, "--across", "a/s", "b/d")
            with self.subTest(case=case, same_bytes=case % 3 == 0):
                self.assertEqual(out["error"]["rule"], "conflict.exists")
                self.assertEqual(without_locks(tree(fx.root)), before)
            fx.cleanup()

    def test_a_dry_run_reports_the_conflict_it_would_hit(self):
        fx = Fixture()
        (fx.root / "a").mkdir()
        (fx.root / "b").mkdir()
        (fx.root / "a" / "s").write_text("source")
        (fx.root / "b" / "d").write_text("taken")
        before = without_locks(tree(fx.root))
        rc, out = move(fx, "--across", "--dry-run", "a/s", "b/d")
        self.assertEqual(out["error"]["rule"], "conflict.exists")
        self.assertEqual(without_locks(tree(fx.root)), before)
        fx.cleanup()

    def test_eight_movers_for_one_destination_give_one_winner_and_lose_nothing(self):
        for round_ in range(6):
            fx = Fixture()
            (fx.root / "src").mkdir()
            (fx.root / "dst").mkdir()
            for i in range(8):
                (fx.root / "src" / ("f%d" % i)).write_bytes(b"content %d\n" % i * 50)
            procs = [subprocess.Popen([binary("move"), "--root", str(fx.root), "--across", "src/f%d" % i, "dst/target"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(8)]
            results = []
            for p in procs:
                out, _ = p.communicate()
                results.append((p.returncode, json.loads(out)))
            winners = [r for r in results if r[0] == 0]
            with self.subTest(round=round_):
                self.assertEqual(len(winners), 1, [r[0] for r in results])
                got = (fx.root / "dst" / "target").read_bytes()
                self.assertIn(got, [b"content %d\n" % i * 50 for i in range(8)])
                # every other source is still where it was, whole
                held = 0
                for i in range(8):
                    p = fx.root / "src" / ("f%d" % i)
                    if p.exists():
                        self.assertEqual(p.read_bytes(), b"content %d\n" % i * 50)
                        held += 1
                self.assertEqual(held, 7)
                for rc, out in results:
                    if rc != 0:
                        self.assertIn(out["error"]["rule"], ("conflict.exists", "conflict.locked"))
            fx.cleanup()


class Reach(unittest.TestCase):
    """A3."""

    def setUp(self):
        self.fx = Fixture()
        r = self.fx.root
        (r / "a").mkdir()
        (r / "b").mkdir()
        (r / "a" / "f.txt").write_text("data\n")
        self.outside = r.parent / (r.name + "-outside")
        self.outside.mkdir()
        (self.outside / "o.txt").write_text("outside\n")
        (r / "a" / "link.txt").symlink_to("f.txt")
        (r / "dlink").symlink_to(self.outside)

    def tearDown(self):
        self.fx.cleanup()

    def refused(self, *args):
        before = without_locks(tree(self.fx.root))
        rc, out = move(self.fx, "--across", *args)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(without_locks(tree(self.fx.root)), before)
        self.assertEqual(sorted(os.listdir(self.outside)), ["o.txt"])
        return out["error"]["rule"]

    def test_a_link_as_the_source(self):
        self.refused("a/link.txt", "b/x")

    def test_a_link_in_a_directory_of_either_path(self):
        self.assertEqual(self.refused("dlink/o.txt", "b/x"), "path.symlink")
        self.assertEqual(self.refused("a/f.txt", "dlink/x"), "path.symlink")

    def test_a_directory_and_paths_that_leave_the_root(self):
        self.refused("a", "b/x")
        self.assertEqual(self.refused("a/f.txt", "../x"), "path.dotdot")
        self.assertEqual(self.refused("a/f.txt", "/tmp/x"), "path.outside-root")

    def test_a_dry_run_and_a_wrong_hash_change_nothing(self):
        before = without_locks(tree(self.fx.root))
        rc, out = move(self.fx, "--across", "--dry-run", "a/f.txt", "b/x")
        self.assertEqual(rc, 9, out)
        self.assertEqual(without_locks(tree(self.fx.root)), before)
        self.assertEqual(self.refused("--if-sha256", "0" * 64, "a/f.txt", "b/x"), "precondition.hash-mismatch")

    def test_a_right_hash_moves(self):
        rc, out = move(self.fx, "--across", "--if-sha256", sha(b"data\n"), "a/f.txt", "b/x")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.fx.root / "b" / "x").read_bytes(), b"data\n")

    def test_a_file_over_max_bytes_is_refused_before_anything_is_written(self):
        self.assertEqual(self.refused("--max-bytes", "2", "a/f.txt", "b/x"), "limit.input-too-large")

    def test_an_unwritable_destination_directory_leaves_the_source_alone(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory modes")
        os.chmod(self.fx.root / "b", 0o555)
        try:
            before = without_locks(tree(self.fx.root))
            rc, out = move(self.fx, "--across", "a/f.txt", "b/x")
            self.assertNotEqual(rc, 0)
            self.assertEqual(without_locks(tree(self.fx.root)), before)
        finally:
            os.chmod(self.fx.root / "b", 0o755)


if __name__ == "__main__":
    unittest.main()
