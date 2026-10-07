"""`move`'s own gates (docs/next-tools.md §4): what makes a rename a tool and not `mv`.

* M5, differential: for every name worth testing, `move` leaves the directory as
  `mv -n` does (the one place the two should agree);
* M7, as the writers': a dry run makes no mutating call (under `strace`, with a
  positive control), a second apply leaves the same state, nothing is replaced,
  and of movers racing for one name exactly one wins;
* M8, confinement: a link is renamed as itself and its target is never touched; a
  link in a *directory* of the path is refused, never followed.

* the rename itself refuses a name taken after the look (`dir_rename_new`:
  `RENAME_NOREPLACE`/`RENAME_EXCL`), which `Unlocked` gates: a creator that takes no lock and
  arrives while the rename is delayed (the 100-of-100 loss of the replacing rename) loses nothing,
  and the kernel's answers are mapped (`EEXIST` is `conflict.exists`; a filesystem without the
  flag is `io.rename-unsupported`), the latter by injecting the errno under `strace`, because CI has
  no filesystem that lacks the flag. The rate of the loss with the replacing rename, at a random
  arrival, is measured by `scripts/move_race.py`.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import time
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


@unittest.skipUnless(shutil.which("strace"), "the delayed-rename and injected-errno checks need strace")
class Unlocked(unittest.TestCase):
    """What the kernel does for `move` when something that takes no lock gets there first."""

    def under_strace(self, fx, inject, args):
        cmd = ["strace", "-f", "-qq", "-o", "/dev/null", "-e", "trace=renameat,renameat2", "-e", "inject=" + inject,
               binary("move"), "--root", str(fx.root)] + args
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=fx.dir)

    def test_a_creator_that_takes_no_lock_loses_nothing_while_the_rename_is_delayed(self):
        # The old behaviour (a look, then `renameat`, which replaces) lost the creator's file in 100 of
        # 100 trials of exactly this. The rename is held 30 ms in the kernel's door, the creator makes
        # DEST with O_EXCL and no lock at 15 ms, and the mover must be refused, not obeyed.
        trials = int(os.environ.get("DELAYED_TRIALS", "20"))
        fx = Fixture()
        try:
            for i in range(trials):
                for n in ("src", "dest"):
                    if (fx.root / n).exists():
                        (fx.root / n).unlink()
                (fx.root / "src").write_bytes(b"mover\n")
                p = self.under_strace(fx, "renameat,renameat2:delay_enter=30ms", ["src", "dest"])
                time.sleep(0.015)
                fd = os.open(fx.root / "dest", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(fd, b"creator\n")
                os.close(fd)
                out, _ = p.communicate()
                self.assertEqual(p.returncode, 5, "trial %d: %r" % (i, out[:300]))
                self.assertEqual(json.loads(out)["error"]["rule"], "conflict.exists")
                self.assertEqual((fx.root / "dest").read_bytes(), b"creator\n", "trial %d: the creator's file was replaced" % i)
                self.assertEqual((fx.root / "src").read_bytes(), b"mover\n", "trial %d: the source did not stay" % i)
            print("\nmove: %d delayed-rename races against a creator without a lock, nothing lost" % trials)
        finally:
            fx.cleanup()

    def test_the_kernels_refusal_after_a_free_look_is_the_same_refusal(self):
        # EEXIST injected for a destination that the look saw free: what a creator that got there
        # between the two would cause. Same rule, same exit, same detail as the look's refusal.
        fx = Fixture()
        try:
            (fx.root / "src").write_bytes(b"mover\n")
            p = self.under_strace(fx, "renameat2:error=EEXIST", ["src", "dest"])
            out, _ = p.communicate()
            self.assertEqual(p.returncode, 5, out)
            err = json.loads(out)["error"]
            self.assertEqual((err["rule"], err["code"]), ("conflict.exists", "CONFLICT"))
            self.assertEqual(err["detail"], {"path": "src", "to": "dest"})
            self.assertEqual((fx.root / "src").read_bytes(), b"mover\n")
            self.assertFalse((fx.root / "dest").exists())
            # And for the look's own refusal: identical.
            (fx.root / "dest").write_bytes(b"taken\n")
            code, doc = run_move(fx, "src", "dest")
            self.assertEqual((code, doc["error"]["rule"], doc["error"]["detail"]), (5, "conflict.exists", err["detail"]))
        finally:
            fx.cleanup()

    def test_a_filesystem_without_the_flag_is_a_refusal_with_its_own_rule(self):
        # Linux answers EINVAL for a rename flag the filesystem does not implement (NFS, ntfs-3g),
        # and the compiler's builtin turns that into EOPNOTSUPP (95); ENOTSUP is 95 as well.
        for errno_name in ("EINVAL", "EOPNOTSUPP"):
            with self.subTest(errno=errno_name):
                fx = Fixture()
                try:
                    (fx.root / "src").write_bytes(b"mover\n")
                    p = self.under_strace(fx, "renameat2:error=" + errno_name, ["src", "dest"])
                    out, _ = p.communicate()
                    self.assertEqual(p.returncode, 8, out)
                    err = json.loads(out)["error"]
                    self.assertEqual((err["rule"], err["code"]), ("io.rename-unsupported", "PRECONDITION_FAILED"))
                    self.assertEqual(err["detail"], {"path": "src", "to": "dest", "errno": 95})
                    self.assertIsNone(err["repair"], "never repairable")
                    self.assertEqual((fx.root / "src").read_bytes(), b"mover\n")
                    self.assertFalse((fx.root / "dest").exists(), "nothing was moved")
                finally:
                    fx.cleanup()

    def test_any_other_kernel_answer_is_a_failed_write_not_a_success(self):
        fx = Fixture()
        try:
            (fx.root / "src").write_bytes(b"mover\n")
            p = self.under_strace(fx, "renameat2:error=EIO", ["src", "dest"])
            out, _ = p.communicate()
            self.assertEqual(p.returncode, 1, out)
            err = json.loads(out)["error"]
            self.assertEqual((err["rule"], err["detail"]["errno"]), ("io.write-failed", 5))
            self.assertEqual((fx.root / "src").read_bytes(), b"mover\n")
            self.assertFalse((fx.root / "dest").exists())
        finally:
            fx.cleanup()

    def test_a_dry_run_of_a_taken_name_is_still_the_looks_refusal(self):
        # A dry run makes no mutating call, so only the look can refuse there.
        fx = Fixture()
        try:
            code, doc = run_move(fx, "--dry-run", "plain.txt", "crlf.txt")
            self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"))
        finally:
            fx.cleanup()


class UnsupportedFilesystem(unittest.TestCase):
    """The same refusal on a real filesystem that cannot rename without replacing (NFS, ntfs-3g and ExFAT
    answer a free name `EINVAL`/`ENOTSUP`). CI has none, so this runs only when
    `CANCHO_RENAME_UNSUPPORTED_DIR` names a writable directory on one (the variable the compiler's own
    test reads), and says so when it does not; `Unlocked` covers the Linux errno by injection."""

    def test_a_free_name_is_refused_and_a_taken_one_is_the_same_conflict(self):
        base = os.environ.get("CANCHO_RENAME_UNSUPPORTED_DIR")
        if not base:
            self.skipTest("CANCHO_RENAME_UNSUPPORTED_DIR is not set: no filesystem without RENAME_NOREPLACE here")
        fx = Fixture(base)
        try:
            (fx.root / "src").write_bytes(b"mover\n")
            code, doc = run_move(fx, "src", "dest")
            self.assertEqual((code, doc["error"]["rule"]), (8, "io.rename-unsupported"), doc)
            self.assertIn(doc["error"]["detail"]["errno"], (95, 45))
            self.assertEqual((fx.root / "src").read_bytes(), b"mover\n")
            self.assertFalse((fx.root / "dest").exists())
            (fx.root / "dest").write_bytes(b"taken\n")
            code, doc = run_move(fx, "src", "dest")
            self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"))
            self.assertEqual((fx.root / "dest").read_bytes(), b"taken\n")
        finally:
            fx.cleanup()


class Remove(unittest.TestCase):
    """`--remove` deletes nothing: the file is renamed to `.NAME.removed-HASH8`, only as the caller read it."""

    def setUp(self):
        self.fx = Fixture()
        self.h = sha256((self.fx.root / "plain.txt").read_bytes())
        self.tomb = ".plain.txt.removed-" + self.h[:8]

    def tearDown(self):
        self.fx.cleanup()

    def test_the_file_is_renamed_to_its_tombstone_and_its_bytes_survive(self):
        content = (self.fx.root / "plain.txt").read_bytes()
        code, doc = run_move(self.fx, "--remove", "--if-sha256", self.h, "plain.txt")
        self.assertEqual((code, doc["data"]["changed"], doc["data"]["to"], doc["data"]["sha256"]), (0, True, self.tomb, self.h))
        self.assertFalse((self.fx.root / "plain.txt").exists())
        self.assertEqual((self.fx.root / self.tomb).read_bytes(), content)

    def test_undoing_it_is_a_move_back(self):
        content = (self.fx.root / "plain.txt").read_bytes()
        run_move(self.fx, "--remove", "--if-sha256", self.h, "plain.txt")
        code, doc = run_move(self.fx, "--if-sha256", self.h, self.tomb, "plain.txt")
        self.assertEqual((code, doc["data"]["changed"]), (0, True))
        self.assertEqual((self.fx.root / "plain.txt").read_bytes(), content)

    def test_without_a_hash_nothing_is_removed(self):
        state = names(self.fx.root)
        code, doc = run_move(self.fx, "--remove", "plain.txt")
        self.assertEqual((code, doc["error"]["rule"]), (2, "args.required-flag"))
        self.assertEqual(names(self.fx.root), state)

    def test_a_new_name_is_refused_beside_remove(self):
        code, doc = run_move(self.fx, "--remove", "--if-sha256", self.h, "plain.txt", "other.txt")
        self.assertEqual((code, doc["error"]["rule"]), (2, "args.too-many-operands"))
        self.assertTrue((self.fx.root / "plain.txt").exists())

    def test_a_stale_hash_removes_nothing(self):
        state = names(self.fx.root)
        code, doc = run_move(self.fx, "--remove", "--if-sha256", "0" * 64, "plain.txt")
        self.assertEqual((code, doc["error"]["rule"]), (5, "precondition.hash-mismatch"))
        self.assertEqual(names(self.fx.root), state)

    def test_a_retry_that_had_landed_is_unchanged(self):
        run_move(self.fx, "--remove", "--if-sha256", self.h, "plain.txt")
        state = tree(self.fx.dir)
        code, doc = run_move(self.fx, "--remove", "--if-sha256", self.h, "plain.txt")
        self.assertEqual((code, doc["data"]["changed"]), (0, False))
        self.assertEqual(tree(self.fx.dir), state)

    def test_a_taken_tombstone_is_a_conflict_and_nothing_is_replaced(self):
        (self.fx.root / self.tomb).write_bytes(b"something else\n")
        code, doc = run_move(self.fx, "--remove", "--if-sha256", self.h, "plain.txt")
        self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"))
        self.assertEqual((self.fx.root / self.tomb).read_bytes(), b"something else\n")
        self.assertTrue((self.fx.root / "plain.txt").exists())

    def test_a_dry_run_plans_a_remove_and_changes_nothing(self):
        state = names(self.fx.root)
        code, doc = run_move(self.fx, "--remove", "--dry-run", "--if-sha256", self.h, "plain.txt")
        self.assertEqual(code, 9)
        self.assertEqual([(a["op"], a["to"]) for a in doc["planned_actions"]], [("remove", self.tomb)])
        self.assertEqual(names(self.fx.root), state)

    def test_only_a_regular_file_is_removed(self):
        for name in ("sub", "link.txt"):
            if not (self.fx.root / name).is_symlink() and not (self.fx.root / name).exists():
                continue
            code, doc = run_move(self.fx, "--remove", "--if-sha256", self.h, name)
            self.assertNotEqual(code, 0, name)
            self.assertTrue(os.path.lexists(self.fx.root / name), name)

    def test_a_name_too_long_for_its_tombstone_is_a_refusal(self):
        name = "n" * 240
        (self.fx.root / name).write_bytes(b"x")
        code, doc = run_move(self.fx, "--remove", "--if-sha256", sha256(b"x"), name)
        self.assertEqual((code, doc["error"]["rule"]), (2, "path.too-long"))
        self.assertTrue((self.fx.root / name).exists())
        # A dry run takes no lock, so the name check alone must refuse what could not be done.
        code, doc = run_move(self.fx, "--remove", "--dry-run", "--if-sha256", sha256(b"x"), name)
        self.assertEqual((code, doc["error"]["rule"]), (2, "path.too-long"))


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
