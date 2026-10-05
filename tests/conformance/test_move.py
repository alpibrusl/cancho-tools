"""`move`'s own gates (docs/next-tools.md §4): what makes a rename a tool and not `mv`.

* M5, differential: for every name worth testing, `move` leaves the directory as
  `mv -n` does (the one place the two should agree);
* M7, as the writers': a dry run makes no mutating call (under `strace`, with a
  positive control), a second apply leaves the same state, nothing is replaced,
  and of movers racing for one name exactly one wins;
* M8, confinement: a link is renamed as itself and its target is never touched; a
  link in a *directory* of the path is refused, never followed.

The open question of §2 is not a gate: whether a process that takes no lock can
create the destination between the look and the rename is measured by
`scripts/move_race.py`, and what it found is in the document.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import unittest

from harness import Fixture, binary, sha256
from test_mutation import MUTATING, OPEN_FOR_WRITE, tree


def run_move(fx, *args):
    p = subprocess.run([binary("move"), "--root", str(fx.root), *args], capture_output=True, cwd=fx.dir)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"raw": p.stdout}


def names(root):
    """What is in `root`, without the writers' lock sidecars."""
    return sorted(n for n in os.listdir(root) if not n.endswith(".lexsys-lock"))


class Differential(unittest.TestCase):
    """M5: the same rename as `mv -n`, for names that break naive tools."""

    NAMES = ["plain.txt", "with space.txt", "-dash.txt", "ünïcode-日本.txt", "x" * 243, "UPPER.TXT", ".hidden", "tab\there"]

    def test_the_directory_is_what_mv_leaves(self):
        if not shutil.which("mv"):
            self.skipTest("no mv")
        for old in self.NAMES:
            for new in ["renamed.txt", "-new.txt", "with space 2", "ü.txt"]:
                with self.subTest(old=old, new=new):
                    a, b = Fixture(), Fixture()
                    try:
                        for fx in (a, b):
                            (fx.root / old).write_bytes(b"content of %s\n" % old.encode()[:20])
                        code, doc = run_move(a, "--", old, new)
                        subprocess.run(["mv", "-n", "--", old, new], cwd=b.root, check=True)
                        self.assertEqual((code, doc.get("ok")), (0, True), doc)
                        self.assertEqual(names(a.root), names(b.root))
                        self.assertEqual((a.root / new).read_bytes(), (b.root / new).read_bytes())
                    finally:
                        a.cleanup()
                        b.cleanup()

    def test_a_name_too_long_to_lock_is_a_refusal_not_a_failure(self):
        # The sidecar `<name>.lexsys-lock` must fit the filesystem's 255 bytes (`write` has the limit
        # too): a 244-byte name is refused with a rule, in both positions, and nothing moves.
        fx = Fixture()
        try:
            (fx.root / ("y" * 250)).write_bytes(b"long\n")
            code, doc = run_move(fx, "plain.txt", "z" * 244)
            self.assertEqual((code, doc["error"]["rule"]), (2, "path.bad-name"))
            code, doc = run_move(fx, "y" * 250, "short.txt")
            self.assertEqual((code, doc["error"]["rule"]), (2, "path.too-long"))
            self.assertTrue((fx.root / ("y" * 250)).exists() and (fx.root / "plain.txt").exists())
        finally:
            fx.cleanup()

    def test_mv_replaces_and_move_refuses(self):
        # The difference that is the point: with the destination taken, `mv` overwrites it.
        a, b = Fixture(), Fixture()
        try:
            for fx in (a, b):
                (fx.root / "src").write_bytes(b"source\n")
                (fx.root / "dst").write_bytes(b"destination\n")
            code, doc = run_move(a, "src", "dst")
            subprocess.run(["mv", "--", "src", "dst"], cwd=b.root, check=True)
            self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"))
            self.assertEqual((a.root / "dst").read_bytes(), b"destination\n")
            self.assertEqual((a.root / "src").read_bytes(), b"source\n")
            self.assertEqual((b.root / "dst").read_bytes(), b"source\n")  # lost, by mv
        finally:
            a.cleanup()
            b.cleanup()


@unittest.skipUnless(shutil.which("strace"), "the dry-run check needs strace")
class DryRun(unittest.TestCase):
    def trace(self, fx, args):
        log = fx.dir.parent / ("%s.trace" % fx.dir.name)
        p = subprocess.run(["strace", "-f", "-qq", "-o", str(log), "-e", "trace=file,desc", binary("move"), "--root", str(fx.root)] + args,
                           capture_output=True, cwd=fx.dir)
        text = log.read_bytes()
        log.unlink()
        return p, text

    def test_a_dry_run_makes_no_mutating_call(self):
        fx = Fixture()
        try:
            before = tree(fx.dir)
            p, log = self.trace(fx, ["--dry-run", "plain.txt", "moved.txt"])
            self.assertEqual(p.returncode, 9, p.stdout)
            self.assertEqual(tree(fx.dir), before, "the tree changed under --dry-run")
            self.assertIsNone(MUTATING.search(log), MUTATING.search(log) and MUTATING.search(log).group(0))
            self.assertIsNone(OPEN_FOR_WRITE.search(log), OPEN_FOR_WRITE.search(log) and OPEN_FOR_WRITE.search(log).group(0))
            # The probe can see a rename: the same call without --dry-run makes one.
            p, log = self.trace(fx, ["plain.txt", "moved.txt"])
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertIsNotNone(re.search(rb"renameat", log), "the positive control saw no rename")
            # And the rename is made durable: the directory is synced, which nothing but a trace shows.
            self.assertIsNotNone(re.search(rb"fsync\(", log), "the rename was not followed by an fsync of the directory")
        finally:
            fx.cleanup()


class Apply(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_nothing_is_ever_replaced(self):
        before = {n: (self.fx.root / n).read_bytes() for n in names(self.fx.root) if (self.fx.root / n).is_file()}
        for src in before:
            for dst in before:
                if src == dst:
                    continue
                code, doc = run_move(self.fx, src, dst)
                self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"), (src, dst))
        after = {n: (self.fx.root / n).read_bytes() for n in names(self.fx.root) if (self.fx.root / n).is_file()}
        self.assertEqual(after, before)

    def test_a_second_apply_leaves_the_same_state(self):
        # Without a hash the second is a refusal (the source is gone); the state is the same.
        first = run_move(self.fx, "plain.txt", "moved.txt")
        state = tree(self.fx.dir)
        second = run_move(self.fx, "plain.txt", "moved.txt")
        self.assertEqual((first[0], first[1]["data"]["changed"]), (0, True))
        self.assertEqual((second[0], second[1]["error"]["rule"]), (3, "io.not-found"))
        self.assertEqual(tree(self.fx.dir), state)

    def test_with_a_hash_a_retry_that_had_landed_is_unchanged(self):
        h = sha256((self.fx.root / "plain.txt").read_bytes())
        first = run_move(self.fx, "--if-sha256", h, "plain.txt", "moved.txt")
        state = tree(self.fx.dir)
        second = run_move(self.fx, "--if-sha256", h, "plain.txt", "moved.txt")
        self.assertEqual((first[0], first[1]["data"]["changed"]), (0, True))
        self.assertEqual((second[0], second[1]["data"]["changed"], second[1]["data"]["sha256"]), (0, False, h))
        self.assertEqual(tree(self.fx.dir), state)
        # But a destination holding other content is not a landed move.
        (self.fx.root / "other.txt").write_bytes(b"not the same\n")
        third = run_move(self.fx, "--if-sha256", h, "gone.txt", "other.txt")
        self.assertEqual(third[1]["error"]["rule"], "io.not-found")

    def test_a_stale_hash_changes_nothing(self):
        state = tree(self.fx.dir)
        code, doc = run_move(self.fx, "--if-sha256", "0" * 64, "plain.txt", "moved.txt")
        self.assertEqual((code, doc["error"]["rule"]), (5, "precondition.hash-mismatch"))
        self.assertEqual(doc["error"]["detail"]["actual_sha256"], sha256((self.fx.root / "plain.txt").read_bytes()))
        self.assertEqual({k: v for k, v in tree(self.fx.dir).items() if not k.endswith(".lexsys-lock")},
                         {k: v for k, v in state.items() if not k.endswith(".lexsys-lock")})

    def test_a_lock_held_on_either_name_refuses_the_move(self):
        # Deterministic where the races below are not: a race window of a few microseconds is too
        # small to see a missing destination lock reliably, but a held lock is seen at once.
        for held in ("plain.txt", "moved.txt"):
            lock = open(self.fx.root / (held + ".lexsys-lock"), "a")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                before = {n: (self.fx.root / n).read_bytes() for n in ("plain.txt", "crlf.txt")}
                code, doc = run_move(self.fx, "plain.txt", "moved.txt")
                self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.locked"), held)
                self.assertTrue((self.fx.root / "plain.txt").exists() and not (self.fx.root / "moved.txt").exists(), held)
            finally:
                lock.close()
        # And once the locks are let go, the move goes through.
        code, doc = run_move(self.fx, "plain.txt", "moved.txt")
        self.assertEqual((code, doc["data"]["changed"]), (0, True))

    def test_a_hash_is_asked_only_of_a_regular_file(self):
        # A directory, a link and a FIFO have no content to state; none is opened to find out (a
        # FIFO's `open` would wait for a writer, so a hang here is the failure).
        os.mkfifo(self.fx.root / "pipe")
        for name, rule in [("sub", "io.is-a-directory"), ("link.txt", "path.symlink"), ("pipe", "io.read-failed")]:
            p = subprocess.run([binary("move"), "--root", str(self.fx.root), "--if-sha256", "0" * 64, name, "moved"],
                               capture_output=True, cwd=self.fx.root, timeout=20)
            self.assertEqual(json.loads(p.stdout)["error"]["rule"], rule, name)
        self.assertEqual(sorted(n for n in names(self.fx.root) if n in ("sub", "link.txt", "pipe", "moved")), ["link.txt", "pipe", "sub"])

    def test_the_same_name_is_nothing_to_do(self):
        code, doc = run_move(self.fx, "plain.txt", "plain.txt")
        self.assertEqual((code, doc["data"]["changed"], doc["data"]["kind"]), (0, False, "file"))
        self.assertEqual((self.fx.root / "plain.txt").read_bytes(), b"alpha\nbeta gamma\nGAMMA delta\nepsilon\n")
        code, doc = run_move(self.fx, "--dry-run", "plain.txt", "plain.txt")
        self.assertEqual(code, 9)
        self.assertEqual(doc["planned_actions"], [])
        code, doc = run_move(self.fx, "missing.txt", "missing.txt")
        self.assertEqual(doc["error"]["rule"], "io.not-found")

    def test_a_directory_is_renamed_whole(self):
        code, doc = run_move(self.fx, "sub", "tree")
        self.assertEqual((code, doc["data"]["kind"]), (0, "directory"))
        self.assertEqual((self.fx.root / "tree" / "inner.txt").read_bytes(), b"inner gamma\n")
        self.assertFalse((self.fx.root / "sub").exists())

    def test_two_movers_racing_for_one_name_never_both_win(self):
        trials = int(os.environ.get("RACE_TRIALS", "200"))
        for i in range(trials):
            for n in ("one", "two", "dest"):
                p = self.fx.root / n
                if p.exists():
                    p.unlink()
            (self.fx.root / "one").write_bytes(b"one\n")
            (self.fx.root / "two").write_bytes(b"two\n")
            procs = [subprocess.Popen([binary("move"), "--root", str(self.fx.root), src, "dest"], stdout=subprocess.PIPE, cwd=self.fx.root)
                     for src in ("one", "two")]
            codes = [p.wait() for p in procs]
            for p in procs:
                p.stdout.close()
            self.assertEqual(sorted(codes), [0, 5], "trial %d: statuses %s" % (i, codes))
            self.assertIn((self.fx.root / "dest").read_bytes(), (b"one\n", b"two\n"))
            # The loser's file is where it was: nothing was lost.
            self.assertEqual(sum(1 for n in ("one", "two") if (self.fx.root / n).exists()), 1)
        print("\nmove M7: %d races, exactly one winner each, nothing lost" % trials)

    def test_a_mover_and_a_writer_racing_for_one_name_never_both_win(self):
        trials = int(os.environ.get("RACE_TRIALS", "200"))
        for i in range(trials):
            for n in ("one", "dest"):
                p = self.fx.root / n
                if p.exists():
                    p.unlink()
            (self.fx.root / "one").write_bytes(b"moved\n")
            mover = subprocess.Popen([binary("move"), "--root", str(self.fx.root), "one", "dest"], stdout=subprocess.PIPE, cwd=self.fx.root)
            writer = subprocess.Popen([binary("write"), "--root", str(self.fx.root), "--create", "--stdin", "dest"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, cwd=self.fx.root)
            writer.stdin.write(b"written\n")
            writer.stdin.close()
            codes = [mover.wait(), writer.wait()]
            for p in (mover, writer):
                p.stdout.close()
            self.assertEqual(sorted(codes), [0, 5], "trial %d: statuses %s" % (i, codes))
            content = (self.fx.root / "dest").read_bytes()
            # Whichever won, the other's bytes were not overwritten into it, and the mover's source is intact if it lost.
            self.assertIn(content, (b"moved\n", b"written\n"))
            if content == b"written\n":
                self.assertEqual((self.fx.root / "one").read_bytes(), b"moved\n")
        print("\nmove M7: %d mover/writer races, exactly one winner each" % trials)


class Confinement(unittest.TestCase):
    """M8: no link is followed, and a link is a thing that can be renamed."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_a_link_is_renamed_as_itself_and_its_target_is_untouched(self):
        outside = (self.fx.dir / "outside" / "secret.txt")
        before = outside.read_bytes()
        code, doc = run_move(self.fx, "link.txt", "link2.txt")
        self.assertEqual((code, doc["data"]["kind"]), (0, "link"))
        self.assertTrue(os.path.islink(self.fx.root / "link2.txt"))
        self.assertFalse(os.path.lexists(self.fx.root / "link.txt"))
        self.assertEqual(outside.read_bytes(), before)
        self.assertEqual(os.listdir(self.fx.dir / "outside"), ["secret.txt"])

    def test_a_link_in_a_directory_of_the_path_is_refused(self):
        outside = self.fx.dir / "outside"
        before = sorted(os.listdir(outside))
        for path in ("dirlink/secret.txt", "dirlink/x"):
            code, doc = run_move(self.fx, path, "moved.txt")
            self.assertEqual((code, doc["error"]["rule"]), (4, "path.symlink"), path)
        self.assertEqual(sorted(os.listdir(outside)), before)

    def test_a_name_that_leaves_the_directory_is_refused(self):
        for bad in ["../x", "a/b", "/abs", "..", ".", "", "x.lexsys-lock"]:
            code, doc = run_move(self.fx, "plain.txt", bad)
            self.assertEqual((code, doc["error"]["rule"]), (2, "path.bad-name"), repr(bad))
        self.assertTrue((self.fx.root / "plain.txt").exists())

    def test_a_path_outside_the_root_or_up_a_level_is_refused(self):
        for path, rule in [("../outside/secret.txt", "path.dotdot"), (str(self.fx.dir / "outside" / "secret.txt"), "path.outside-root")]:
            code, doc = run_move(self.fx, path, "moved.txt")
            self.assertEqual(doc["error"]["rule"], rule, path)
        self.assertTrue((self.fx.dir / "outside" / "secret.txt").exists())


if __name__ == "__main__":
    unittest.main()
