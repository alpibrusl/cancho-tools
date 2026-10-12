"""`write --create` never replaces a name, by the kernel (`atomic.create`: `dir_rename_new`).

Before, `--create` looked at the path and then installed its temporary with a replacing `renameat`, so a
process that takes no lock and made the path in between lost its file without a word
(`scripts/write_race.py` measures the rate). These are the gates:

* a creator that arrives while the rename is held up (under `strace`) is refused (`conflict.exists`), and its file
  and the writer's temporary are as they should be (the creator's bytes; no temporary left);
* the kernel's answers are mapped, by injecting them under `strace` because CI has no filesystem that lacks
  the flag: `EEXIST` after a free look is the look's refusal; a filesystem without `RENAME_NOREPLACE` is
  `io.rename-unsupported` (the rule `move` has), never a replace; anything else is `io.write-failed`;
* the replace paths (`--if-sha256`) still replace, which is their job, with the plain `renameat`;
* a real filesystem without the flag, when `CANCHO_RENAME_UNSUPPORTED_DIR` names one.
"""

import json
import os
import shutil
import subprocess
import time
import unittest

from harness import Fixture, binary, sha256

STRACE = shutil.which("strace") is not None


def run_write(fx, args, stdin=b"writer\n"):
    p = subprocess.run([binary("write"), "--root", str(fx.root), *args], input=stdin, capture_output=True, cwd=fx.dir)
    return p.returncode, json.loads(p.stdout)


def temporaries(fx):
    return [n for n in os.listdir(fx.root) if n.endswith(".lexsys-tmp")]


@unittest.skipUnless(STRACE, "the delayed-rename and injected-errno checks need strace")
class Unlocked(unittest.TestCase):
    def under_strace(self, fx, inject, args, stdin=b"writer\n"):
        cmd = ["strace", "-f", "-qq", "-o", "/dev/null", "-e", "trace=renameat,renameat2", "-e", "inject=" + inject,
               binary("write"), "--root", str(fx.root)] + args
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=fx.dir)
        p.stdin.write(stdin)
        p.stdin.close()
        return p

    def finish(self, p):
        out = p.stdout.read()
        p.stdout.close()
        p.stderr.close()
        p.wait()
        return p.returncode, json.loads(out)

    def race(self, fx, creator_bytes):
        for n in os.listdir(fx.root):
            if n == "dest" or n.endswith(".lexsys-tmp"):
                os.unlink(fx.root / n)
        p = self.under_strace(fx, "renameat,renameat2:delay_enter=30ms", ["--create", "--stdin", "dest"])
        time.sleep(0.025)
        fd = os.open(fx.root / "dest", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, creator_bytes)
        os.close(fd)
        return self.finish(p)

    def test_a_creator_that_takes_no_lock_loses_nothing_while_the_rename_is_delayed(self):
        # The replacing rename lost the creator's file in every trial of exactly this: the rename is held
        # 30 ms, the creator makes `dest` with O_EXCL and no lock at 25 ms, and the writer must be refused.
        trials = int(os.environ.get("DELAYED_TRIALS", "20"))
        fx = Fixture()
        try:
            for i in range(trials):
                code, doc = self.race(fx, b"creator\n")
                self.assertEqual(code, 5, "trial %d: %r" % (i, doc))
                err = doc["error"]
                self.assertEqual(err["rule"], "conflict.exists")
                self.assertEqual(err["detail"], {"path": "dest", "actual_sha256": sha256(b"creator\n")})
                self.assertEqual((fx.root / "dest").read_bytes(), b"creator\n", "trial %d: the creator's file was replaced" % i)
                self.assertEqual(temporaries(fx), [], "trial %d: the temporary was left" % i)
            print("\nwrite --create: %d delayed-rename races against a creator without a lock, nothing lost" % trials)
        finally:
            fx.cleanup()

    def test_a_creator_that_made_exactly_the_content_is_an_idempotent_create(self):
        fx = Fixture()
        try:
            code, doc = self.race(fx, b"writer\n")
            self.assertEqual((code, doc["ok"], doc["data"]["changed"], doc["data"]["created"]), (0, True, False, False), doc)
            self.assertEqual((fx.root / "dest").read_bytes(), b"writer\n")
            self.assertEqual(temporaries(fx), [])
        finally:
            fx.cleanup()

    def test_the_kernels_refusal_after_a_free_look_is_the_conflict(self):
        fx = Fixture()
        try:
            p = self.under_strace(fx, "renameat2:error=EEXIST", ["--create", "--stdin", "dest"])
            code, doc = self.finish(p)
            err = doc["error"]
            self.assertEqual((code, err["rule"], err["code"]), (5, "conflict.exists", "CONFLICT"), doc)
            self.assertEqual(err["detail"], {"path": "dest", "actual_sha256": None})
            self.assertFalse((fx.root / "dest").exists())
            self.assertEqual(temporaries(fx), [])
        finally:
            fx.cleanup()

    def test_a_filesystem_without_the_flag_is_io_rename_unsupported(self):
        for name, shown in (("EINVAL", 95), ("EOPNOTSUPP", 95), ("EL2NSYNC", 45)):
            with self.subTest(errno=name):
                fx = Fixture()
                try:
                    p = self.under_strace(fx, "renameat2:error=" + name, ["--create", "--stdin", "dest"])
                    code, doc = self.finish(p)
                    err = doc["error"]
                    self.assertEqual((code, err["rule"], err["code"]), (8, "io.rename-unsupported", "PRECONDITION_FAILED"), doc)
                    self.assertEqual(err["detail"], {"path": "dest", "errno": shown})
                    self.assertIsNone(err["repair"])
                    self.assertFalse((fx.root / "dest").exists(), "nothing was created")
                    self.assertEqual(temporaries(fx), [], "the temporary was left")
                finally:
                    fx.cleanup()

    def test_any_other_kernel_answer_is_a_failed_write_and_leaves_no_temporary(self):
        fx = Fixture()
        try:
            p = self.under_strace(fx, "renameat2:error=EIO", ["--create", "--stdin", "dest"])
            code, doc = self.finish(p)
            err = doc["error"]
            self.assertEqual((code, err["rule"], err["detail"]["errno"], err["detail"]["step"]), (1, "io.write-failed", 5, "rename"), doc)
            self.assertFalse((fx.root / "dest").exists())
            self.assertEqual(temporaries(fx), [])
        finally:
            fx.cleanup()

    def test_a_replace_by_hash_still_replaces_and_does_not_use_the_no_replace_rename(self):
        # Replacing an existing file is the point of --if-sha256, with the plain rename: an injected
        # refusal of renameat2 does not touch it.
        fx = Fixture()
        try:
            old = (fx.root / "plain.txt").read_bytes()
            p = self.under_strace(fx, "renameat2:error=EEXIST", ["--if-sha256", sha256(old), "--stdin", "plain.txt"], stdin=b"new\n")
            code, doc = self.finish(p)
            self.assertEqual((code, doc["data"]["changed"]), (0, True), doc)
            self.assertEqual((fx.root / "plain.txt").read_bytes(), b"new\n")
        finally:
            fx.cleanup()


class Plain(unittest.TestCase):
    def test_a_dry_run_of_a_taken_name_is_the_looks_refusal(self):
        fx = Fixture()
        try:
            code, doc = run_write(fx, ["--create", "--dry-run", "--stdin", "plain.txt"])
            self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"))
        finally:
            fx.cleanup()

    def test_a_create_still_creates(self):
        fx = Fixture()
        try:
            code, doc = run_write(fx, ["--create", "--stdin", "fresh.txt"])
            self.assertEqual((code, doc["data"]["created"], doc["data"]["changed"]), (0, True, True), doc)
            self.assertEqual((fx.root / "fresh.txt").read_bytes(), b"writer\n")
            self.assertEqual(temporaries(fx), [])
        finally:
            fx.cleanup()


class UnsupportedFilesystem(unittest.TestCase):
    """A real filesystem that cannot rename without replacing (NFS, ntfs-3g, ExFAT), when
    `CANCHO_RENAME_UNSUPPORTED_DIR` names a writable directory on one; skipped, and saying so, otherwise."""

    def test_a_free_name_is_refused_and_a_taken_one_is_the_conflict(self):
        base = os.environ.get("CANCHO_RENAME_UNSUPPORTED_DIR")
        if not base:
            self.skipTest("CANCHO_RENAME_UNSUPPORTED_DIR is not set: no filesystem without RENAME_NOREPLACE here")
        fx = Fixture(base)
        try:
            code, doc = run_write(fx, ["--create", "--stdin", "dest"])
            self.assertEqual((code, doc["error"]["rule"]), (8, "io.rename-unsupported"), doc)
            self.assertIn(doc["error"]["detail"]["errno"], (95, 45))
            self.assertFalse((fx.root / "dest").exists())
            self.assertEqual(temporaries(fx), [])
            (fx.root / "dest").write_bytes(b"taken\n")
            code, doc = run_write(fx, ["--create", "--stdin", "dest"])
            self.assertEqual((code, doc["error"]["rule"]), (5, "conflict.exists"))
            self.assertEqual((fx.root / "dest").read_bytes(), b"taken\n")
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
