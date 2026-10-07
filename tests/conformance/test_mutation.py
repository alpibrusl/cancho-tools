"""M7 -- a dry run makes no mutating system call and leaves the tree as it
was; a second apply is `changed: false`; a replacement is atomic; and of
two writers racing with the same precondition, exactly one wins.

The dry-run property is not in the type system: the row is the program's,
not the invocation's, so `write --dry-run` reports `dir_write` like a real
write (cancho docs/agent-toolbox.md §2.4, D10). It is a property of the
code, and this is the test of it: `strace` sees every file-system call.
"""

import hashlib
import os
import re
import shutil
import subprocess
import unittest

from harness import Fixture, binary, sha256


# The tools M7 races, interrupts and straces: a guarantee of `atomic`,
# `dry_run` or `requires_precondition` must name one of these
# (tests/conformance/test_guarantees.py).
WRITERS = {"write", "replace"}

# `move` is atomic, locked and has a dry run like the writers, but it states no belief
# about content (its precondition is that the new name is free, checked every time), so
# it is not a writer in the sense of `requires_precondition`. Its gates are in
# test_move.py.
MOVERS = {"move"}

MUTATING = re.compile(rb"^(?:\d+ +|\[pid +\d+\] )?(rename\w*|unlink\w*|mkdir\w*|rmdir|truncate|ftruncate|link\w*|symlink\w*|fsync|fdatasync|flock)\(", re.M)
OPEN_FOR_WRITE = re.compile(rb"open(?:at)?\([^)]*O_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)")


def tree(path):
    out = {}
    for dirpath, dirnames, filenames in os.walk(path):
        for name in filenames + dirnames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                out[p] = ("link", os.readlink(p))
            elif os.path.isfile(p):
                out[p] = ("file", hashlib.sha256(open(p, "rb").read()).hexdigest())
            else:
                out[p] = ("dir",)
    return out


@unittest.skipUnless(shutil.which("strace"), "M7's dry-run check needs strace")
class DryRun(unittest.TestCase):
    def check_quiet(self, tool, args, stdin=b"new\n"):
        fx = Fixture()
        try:
            before = tree(fx.dir)
            trace = fx.dir.parent / ("%s.trace" % fx.dir.name)
            p = subprocess.run(["strace", "-f", "-qq", "-o", str(trace), "-e", "trace=file,desc", binary(tool)] + args,
                               input=stdin, capture_output=True, cwd=fx.root)
            log = trace.read_bytes()
            trace.unlink()
            self.assertEqual(p.returncode, 9, p.stdout)
            self.assertEqual(tree(fx.dir), before, "the tree changed under --dry-run")
            self.assertIsNone(MUTATING.search(log), MUTATING.search(log) and MUTATING.search(log).group(0))
            self.assertIsNone(OPEN_FOR_WRITE.search(log), OPEN_FOR_WRITE.search(log) and OPEN_FOR_WRITE.search(log).group(0))
            # The probe can see a mutating call: the same invocation without
            # --dry-run makes them (so a quiet trace above means something).
            real = [a for a in args if a != "--dry-run"]
            subprocess.run(["strace", "-f", "-qq", "-o", str(trace), "-e", "trace=file,desc", binary(tool)] + real,
                           input=stdin, capture_output=True, cwd=fx.root)
            log = trace.read_bytes()
            trace.unlink()
            self.assertIsNotNone(MUTATING.search(log))
        finally:
            fx.cleanup()

    def test_write_dry_run_is_quiet(self):
        self.check_quiet("write", ["--dry-run", "--create", "--stdin", "fresh.txt"])
        fx = Fixture()
        try:
            h = sha256((fx.root / "plain.txt").read_bytes())
        finally:
            fx.cleanup()
        self.check_quiet("write", ["--dry-run", "--if-sha256", h, "--stdin", "plain.txt"])

    def test_replace_dry_run_is_quiet(self):
        self.check_quiet("replace", ["--dry-run", "--old", "alpha", "--new", "ALPHA", "plain.txt"])


class Apply(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def run_tool(self, tool, args, stdin=b""):
        return subprocess.run([binary(tool)] + args, input=stdin, capture_output=True, cwd=self.fx.root)

    def test_every_mutating_case_twice(self):
        import json
        h = sha256((self.fx.root / "plain.txt").read_bytes())
        for tool, args, stdin in [
            ("write", ["--create", "--stdin", "fresh.txt"], b"fresh\n"),
            ("write", ["--if-sha256", h, "--stdin", "plain.txt"], b"replaced\n"),
            ("replace", ["--old", "gamma", "--new", "GAMMA", "crlf.txt"], b""),
            ("replace", ["--old", "beta", "--new", "", "words.txt"], b""),
        ]:
            first = self.run_tool(tool, args, stdin)
            state = tree(self.fx.dir)
            second = self.run_tool(tool, args, stdin)
            a, b = json.loads(first.stdout), json.loads(second.stdout)
            if tool == "replace" and not a["ok"]:
                # words.txt has no "beta": the count check refuses, twice.
                self.assertEqual(a["error"]["rule"], "precondition.count-mismatch")
                continue
            self.assertEqual((first.returncode, a["data"]["changed"]), (0, True), first.stdout)
            self.assertEqual((second.returncode, b["data"]["changed"]), (0, False), second.stdout)
            self.assertEqual(tree(self.fx.dir), state, "the second apply changed something")

    def test_no_temporary_is_left_behind(self):
        self.run_tool("write", ["--create", "--stdin", "fresh.txt"], b"x")
        self.run_tool("write", ["--create", "--stdin", "plain.txt"], b"x")
        left = [p for p in os.listdir(self.fx.root) if p.endswith(".lexsys-tmp")]
        self.assertEqual(left, [])

    def test_a_stale_temporary_does_not_block_forever(self):
        import json
        content = b"hello\n"
        h = sha256(content)
        stale = self.fx.root / (".fresh.txt.%s.lexsys-tmp" % h[:16])
        stale.write_bytes(b"half a write")
        r = self.run_tool("write", ["--create", "--stdin", "fresh.txt"], content)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.fx.root / "fresh.txt").read_bytes(), content)
        self.assertFalse(stale.exists())

    def test_two_writers_with_one_precondition_never_both_win(self):
        trials = int(os.environ.get("RACE_TRIALS", "200"))
        both = 0
        for i in range(trials):
            target = self.fx.root / "race.txt"
            target.write_bytes(b"base\n")
            h = sha256(b"base\n")
            procs = [subprocess.Popen([binary("write"), "--if-sha256", h, "--stdin", "race.txt"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, cwd=self.fx.root) for _ in range(2)]
            for n, p in enumerate(procs):
                p.stdin.write(b"writer %d\n" % n)
                p.stdin.close()
            codes = [p.wait() for p in procs]
            for p in procs:
                p.stdout.close()
            wins = codes.count(0)
            self.assertEqual(wins, 1, "trial %d: statuses %s" % (i, codes))
            self.assertEqual(sorted(codes), [0, 5])
            self.assertIn(target.read_bytes(), (b"writer 0\n", b"writer 1\n"))
        print("\nM7: %d races, exactly one winner each" % trials)


if __name__ == "__main__":
    unittest.main()
