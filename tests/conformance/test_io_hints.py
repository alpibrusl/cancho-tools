"""The operating system's refusals carry advice (contract/fail.cho `io_error`).

`io_error` is the one place that turns an errno into a refusal, and every tool that opens, reads or writes a path
comes through it. Three of its answers used to carry no hint (`"hint":null`): the path runs through something that is
not a directory, permission was denied, and any errno the table does not name. They carry one now, and this file pins
it, on every tool that reaches it:

* io.not-a-directory   (errno 20)       message "a component of the path is not a directory"
* io.permission-denied (errno 13 and 1) message "permission denied", for a read and for a write
* io.read-failed / io.write-failed      message "the operating system failed the operation", any other errno

What is checked, by running the built binaries:

* every fixture of `test_rules.py` that ends in one of those messages carries the exact sentence below, so each tool
  those fixtures cover is covered here too (seek, write, replace, peek, jsonq, tally, hash, list, move);
* `archive` and `run`, which `test_rules.py` does not drive, are driven here;
* a permission refusal is checked for a read and for a write (the sentence has to be true of both), made by an
  unprivileged user so that it is real when the suite runs as root (root bypasses file modes);
* the refusal still has no repair (`"repair":null`): nothing safe to offer;
* the rule, the exit status and `detail` (`path`, `errno`) are the ones they were: only `hint` moved.

NOT REACHED from a test: an errno the table does not name that is not a path's own problem (EIO from a failing disk,
ENOSPC, EROFS). The default branch is one assignment before the ladder, and the cases below reach it with EINVAL
(reading /proc/self/mem, a FIFO's lseek) and EFBIG (a write past RLIMIT_FSIZE), which take the same branch.
"""

import json
import os
import resource
import subprocess
import unittest

from harness import EVERY, Fixture, binary
import test_rules
from test_rules import as_nobody, fixtures, small_files

NOT_A_DIRECTORY = "a name in the path that has to be a directory is a file; name a path whose directories are all directories"
PERMISSION = "the user running the tool lacks permission for this path; check the permissions of the path and of the directories above it"
OTHER = "the operating system refused the operation (detail.errno is its code); check the path and the storage, then try again"

BY_MESSAGE = {
    "a component of the path is not a directory": ("io.not-a-directory", NOT_A_DIRECTORY),
    "permission denied": ("io.permission-denied", PERMISSION),
    "the operating system failed the operation": (("io.read-failed", "io.write-failed"), OTHER),
}


def errors_of(stdout):
    """Every error in the output, whichever of the tools' three shapes it is (a document, a stream, archive's)."""
    out = []
    for line in stdout.decode("utf-8").splitlines():
        record = json.loads(line)
        if record.get("type") == "error":
            out.append(record["error"])
        elif isinstance(record.get("errors"), list):
            out.extend(record["errors"])
    return out


class IoHints(unittest.TestCase):
    def check(self, label, errors, want_message=None):
        """The first refusal that `io_error` wrote carries its sentence; returns its message, or None if it is not one."""
        self.assertTrue(errors, "%s: no refusal" % label)
        first = errors[0]
        if first["message"] not in BY_MESSAGE:
            return None
        rules, hint = BY_MESSAGE[first["message"]]
        self.assertIn(first["rule"], (rules,) if isinstance(rules, str) else rules, label)
        self.assertEqual(first["hint"], hint, label)
        self.assertIsNone(first["repair"], "%s: nothing safe to offer, so no repair" % label)
        self.assertEqual(sorted(first["detail"]), ["errno", "path"], label)
        if want_message:
            self.assertEqual(first["message"], want_message, label)
        return first["message"]

    def test_every_fixture_of_test_rules_that_ends_in_an_os_message_carries_the_hint(self):
        wanted = {"io.not-a-directory", "io.permission-denied", "io.read-failed", "io.write-failed"}
        rules = test_rules.Rules("run_fixture")
        seen = {}
        probe = Fixture()
        try:
            every = fixtures(probe)
        finally:
            probe.cleanup()
        for index, fixture in enumerate(every):
            tool, rule = fixture[0], fixture[1]
            if rule not in wanted:
                continue
            result, errors, _ = rules.run_fixture(index)
            message = self.check("%s %s" % (tool, rule), errors)
            seen.setdefault((tool, rule), set()).add(message)
        # Each of the nine tools reaches io.not-a-directory; the three messages are all reached by some fixture.
        for tool in ("seek", "write", "replace", "peek", "jsonq", "tally", "hash", "list", "move"):
            self.assertIn((tool, "io.not-a-directory"), seen)
            self.assertEqual(seen[(tool, "io.not-a-directory")], {"a component of the path is not a directory"}, tool)
        reached = set().union(*seen.values())
        self.assertEqual(reached - {None}, set(BY_MESSAGE), "a message no fixture reaches: %s" % sorted(set(BY_MESSAGE) - reached))

    def run_in(self, fx, tool, args, preexec=None, stdin=b"x\n"):
        p = subprocess.run([binary(tool), "--root", str(fx.root)] + args, input=stdin, capture_output=True,
                           cwd=fx.root, preexec_fn=preexec, timeout=60)
        return p

    def test_archive_and_run_carry_the_hints_too(self):
        fx = Fixture()
        try:
            # A directory the unprivileged user can enter, so that the refusal is the file's mode and not the tree's.
            os.chmod(fx.dir, 0o755)
            os.chmod(fx.root, 0o755)
            os.chmod(fx.root / "sub", 0o755)
            os.chmod(fx.root / "plain.txt", 0o644)
            os.chmod(fx.root / "crlf.txt", 0)
            for tool in ("archive", "run"):
                self.assertIn(tool, EVERY)
            # A path through a file (archive), and a --root that is a file (run: the same errno, 20).
            p = self.run_in(fx, "archive", ["--op", "list", "plain.txt/x"])
            self.assertEqual(self.check("archive not-a-directory", errors_of(p.stdout)), "a component of the path is not a directory")
            p = subprocess.run([binary("run"), "--root", str(fx.root / "plain.txt"), "--", "true"], capture_output=True, cwd=fx.root, timeout=60)
            self.assertEqual(self.check("run not-a-directory", errors_of(p.stdout)), "a component of the path is not a directory")
            # Permission, read: an unreadable file.
            p = self.run_in(fx, "archive", ["--op", "list", "crlf.txt"], preexec=as_nobody)
            self.assertEqual(self.check("archive permission (read)", errors_of(p.stdout)), "permission denied")
        finally:
            os.chmod(fx.root / "crlf.txt", 0o644)
            os.chmod(fx.root, 0o755)
            fx.cleanup()

    def test_permission_is_true_of_a_write_as_well_as_a_read(self):
        fx = Fixture()
        try:
            os.chmod(fx.dir, 0o755)
            os.chmod(fx.root, 0o755)
            os.mkdir(fx.root / "ro")
            (fx.root / "ro" / "a").write_bytes(b"a\n")
            os.chmod(fx.root / "ro", 0o555)
            cases = [
                # a new file in a directory the user cannot write to
                ("write", ["--create", "--stdin", "ro/new.txt"]),
                # a rename in a directory the user cannot write to (the file itself is readable)
                ("move", ["ro/a", "b"]),
                # an archive written into one
                ("archive", ["--op", "create", "ro/out.tgz", "plain.txt"]),
            ]
            for tool, args in cases:
                label = "%s %s" % (tool, args)
                p = self.run_in(fx, tool, args, preexec=as_nobody)
                errors = [e for e in errors_of(p.stdout)]
                self.assertEqual(errors[0]["rule"], "io.permission-denied", label)
                self.assertEqual(self.check(label, errors), "permission denied", label)
        finally:
            os.chmod(fx.root / "ro", 0o755)
            fx.cleanup()

    def test_a_write_the_system_fails_carries_the_default_hint(self):
        # No descriptor left (EMFILE, 24) for the lock a write takes, and a file-size limit (EFBIG, 27) for a tarball
        # being written: errnos the table does not name, on the write side.
        def no_descriptors():
            resource.setrlimit(resource.RLIMIT_NOFILE, (4, 4))

        cases = [
            ("write", ["--if-sha256", "0" * 64, "--stdin", "plain.txt"], no_descriptors, 24),
            ("replace", ["--old", "alpha", "--new", "b", "plain.txt"], no_descriptors, 24),
            ("move", ["plain.txt", "moved.txt"], no_descriptors, 24),
            ("archive", ["--op", "create", "big.tgz", "plain.txt", "long.txt"], small_files, 27),
        ]
        for tool, args, limit, errno in cases:
            fx = Fixture()
            try:
                p = self.run_in(fx, tool, args, preexec=limit)
                errors = errors_of(p.stdout)
                label = "%s %s" % (tool, args)
                self.assertEqual((errors[0]["rule"], errors[0]["detail"]["errno"]), ("io.write-failed", errno), label)
                self.assertEqual(self.check(label, errors), "the operating system failed the operation", label)
            finally:
                fx.cleanup()

    def test_a_read_the_system_fails_carries_the_default_hint(self):
        # Reading a FIFO's size (move --if-sha256 opens nothing it cannot hash) and /proc/self/mem (EIO or EINVAL):
        # the rule is io.read-failed, the sentence the same as for a write.
        fx = Fixture()
        try:
            os.mkfifo(fx.root / "pipe")
            p = self.run_in(fx, "move", ["--if-sha256", "0" * 64, "pipe", "moved"])
            errors = errors_of(p.stdout)
            self.assertEqual(errors[0]["rule"], "io.read-failed")
            self.assertEqual(self.check("move pipe", errors), "the operating system failed the operation")
            p = subprocess.run([binary("peek"), "/proc/self/mem"], capture_output=True, cwd=fx.root, timeout=30)
            errors = errors_of(p.stdout)
            self.assertEqual(errors[0]["rule"], "io.read-failed")
            self.assertEqual(self.check("peek /proc/self/mem", errors), "the operating system failed the operation")
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
