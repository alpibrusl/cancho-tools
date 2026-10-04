"""M2 -- the same invocation gives the same bytes and status, whatever the
environment, locale, time zone, terminal or sink.

The tools hold no environment, clock or tty capability (their authority
says so), so this is expected to hold by construction; the test is what
makes it a measured property rather than a reading of the row.
"""

import os
import pty
import subprocess
import tempfile
import unittest

from harness import Fixture, binary
from test_schema import corpus

ENVIRONMENTS = [
    {},
    {"LANG": "tr_TR.UTF-8", "LC_ALL": "tr_TR.UTF-8"},
    {"LANG": "C", "LC_ALL": "POSIX", "TZ": "Pacific/Kiritimati"},
    {"TERM": "xterm-256color", "NO_COLOR": "", "COLUMNS": "20"},
    {"TZ": "UTC", "PATH": "/nonexistent", "HOME": "/nonexistent"},
]


def via_pipe(argv, stdin, cwd, env):
    p = subprocess.run(argv, input=stdin, capture_output=True, cwd=cwd, env=env, timeout=60)
    return p.returncode, p.stdout


def via_file(argv, stdin, cwd, env):
    with tempfile.TemporaryFile() as out:
        p = subprocess.run(argv, input=stdin, stdout=out, stderr=subprocess.DEVNULL, cwd=cwd, env=env, timeout=60)
        out.seek(0)
        return p.returncode, out.read()


def via_pty(argv, stdin, cwd, env):
    """Standard output a terminal. The pty's line discipline turns `\\n`
    into `\\r\\n` on the way out; that is the terminal's, not the tool's, and
    is undone before comparing."""
    master, slave = pty.openpty()
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=slave, stderr=subprocess.DEVNULL, cwd=cwd, env=env)
    os.close(slave)
    p.stdin.write(stdin)
    p.stdin.close()
    chunks = []
    while True:
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    status = p.wait(timeout=60)
    os.close(master)
    return status, b"".join(chunks).replace(b"\r\n", b"\n")


class Determinism(unittest.TestCase):
    def test_reruns_are_byte_identical_under_every_environment_and_sink(self):
        failures = []
        cases = 0
        for tool, args, stdin in corpus_without_writes():
            fx = Fixture()
            try:
                argv = [binary(tool)] + [a.replace(str(fx.root), str(fx.root)) for a in args]
                baseline = via_pipe(argv, stdin, fx.root, dict(os.environ))
                for extra in ENVIRONMENTS:
                    env = dict(os.environ)
                    env.update(extra)
                    for sink in (via_pipe, via_file, via_pty):
                        cases += 1
                        got = sink(argv, stdin, fx.root, env)
                        if got != baseline:
                            failures.append("%s %s under %s through %s: %r != %r" % (
                                tool, args, extra, sink.__name__, got[1][:120], baseline[1][:120]))
            finally:
                fx.cleanup()
        self.assertEqual(failures, [])
        print("\nM2: %d runs, all byte-identical to their baseline" % cases)


def corpus_without_writes():
    """The M1 corpus, minus invocations that change the tree (a second run of
    a write is a different question -- M7's idempotence)."""
    fx = Fixture()
    try:
        out = []
        for tool, args, stdin in corpus(fx):
            if tool in ("write", "replace") and "--dry-run" not in args:
                continue
            # The corpus names the fixture's root; each run here makes its
            # own, so the root is passed as a relative `.` instead.
            out.append((tool, [("." if a == str(fx.root) else a) for a in args], stdin))
        return out
    finally:
        fx.cleanup()


if __name__ == "__main__":
    unittest.main()
