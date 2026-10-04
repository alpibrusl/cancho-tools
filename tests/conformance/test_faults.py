"""M4 -- no input reaches a trap.

Seeded random invocations per tool, built from the tool's own flag table
(read from `introspect`), hostile values and hostile paths, in a fresh
fixture tree each; plus a sparse file far larger than any cap. Every case
must end without a signal and, in JSON mode, with output that validates
against the schema. N per tool is FAULTS_N (default 250); the seed is
FAULTS_SEED (default 1), so a failure is reproducible from its number.

Not covered, by design (lex-sys docs/agent-toolbox.md D8 point 7): a source
that never ends or never answers -- /dev/zero, a FIFO with no writer, an
idle standard input. No tool takes a clock, so none can time out; the
supervisor owns the wall clock.
"""

import os
import subprocess
import unittest

from harness import TOOLS, TRAPS, Fixture, binary, introspect, rng, validate, Result

VALUES = ["", "0", "1", "2", "-1", "18446744073709551616", "999999999999999999", "1:2", "2:1", "0:0", ":", "5:",
          "abc", "/", "/a/0", "/zz", "~2", "a/b", ".a", "x" * 5000, "é", "sha512", "sha256", "json", "text", "ndjson",
          "0" * 64, "f" * 128, ",", "\t", "--", "-"]
PATHS = ["plain.txt", "crlf.txt", "no-newline.txt", "nul.bin", "latin1.txt", "empty.txt", "long.txt", "doc.json",
         "bad.json", "words.txt", "fields.csv", "sub", "sub/", "sub/inner.txt", "./plain.txt", "a//../b", "..", "../x",
         "/etc/passwd", "", "missing", "plain.txt/x", "link.txt", "-", "/proc/self/mem", "/dev/null", "x" * 5000,
         "sub/../plain.txt", "/"]
BYTES = [b"\xff\xfe", b"caf\xe9", b"a\x01b"]
STDIN = [b"", b"x\ny\nx\n", b'{"a":[1,2]}', b"\x00" * 100, b"\xff" * 10 + b"\n", b"[" * 200, b"g" * 70000]


def invocation(tool, r):
    table = introspect(tool)["flags"]
    argv = []
    for _ in range(r.randint(0, 6)):
        choice = r.random()
        if choice < 0.45:
            flag = r.choice(table)
            spelled = flag["name"] if (flag["short"] is None or r.random() < 0.7) else flag["short"]
            if flag["kind"] == "bool":
                argv.append(spelled if r.random() < 0.9 else spelled + "=1")
            elif r.random() < 0.2:
                argv.append(spelled + "=" + r.choice(VALUES))
            else:
                argv.append(spelled)
                if r.random() < 0.95:
                    argv.append(r.choice(VALUES + PATHS))
        elif choice < 0.55:
            argv.append(r.choice(["-z", "--recursive", "-r", "--", "-", "-mx", "--max", "--format=", "-n1:2"]))
        elif choice < 0.62:
            argv.append(r.choice(BYTES))
        else:
            argv.append(r.choice(PATHS + VALUES))
    if r.random() < 0.6 and "--root" not in argv:
        argv = ["--root", "."] + argv
    return argv


class Faults(unittest.TestCase):
    def test_no_input_reaches_a_trap(self):
        n = int(os.environ.get("FAULTS_N", "250"))
        seed = int(os.environ.get("FAULTS_SEED", "1"))
        failures = []
        total = 0
        for t, tool in enumerate(TOOLS):
            r = rng(seed * 1000 + t)
            for case in range(n):
                argv = invocation(tool, r)
                stdin = r.choice(STDIN)
                fx = Fixture()
                try:
                    full = [binary(tool)] + argv
                    try:
                        p = subprocess.run(full, input=stdin, capture_output=True, cwd=fx.root, timeout=60)
                    except subprocess.TimeoutExpired:
                        failures.append("%s case %d %r: timed out" % (tool, case, argv))
                        continue
                    total += 1
                    if p.returncode in TRAPS or p.returncode < 0:
                        failures.append("%s case %d %r: status %d" % (tool, case, argv, p.returncode))
                        continue
                    text_mode = any(a in (b"text",) or a == "text" or a == "--format=text" for a in argv)
                    if not text_mode:
                        res = Result(tool, full, p.returncode, p.stdout, p.stderr)
                        problems = validate(res)
                        if problems:
                            failures.append("%s case %d %r: %s" % (tool, case, argv, problems[:2]))
                finally:
                    fx.cleanup()
        self.assertEqual(failures, [])
        print("\nM4: %d cases (seed %d, N=%d per tool), 0 traps, every JSON output valid" % (total, seed, n))

    def test_a_sparse_file_larger_than_every_cap(self):
        size = int(os.environ.get("SPARSE_BYTES", str(1 << 30)))
        fx = Fixture()
        try:
            big = fx.root / "sparse.bin"
            with open(big, "wb") as f:
                f.truncate(size)
            for tool, args in [("seek", ["--root", ".", "x", "sparse.bin"]), ("peek", ["--root", ".", "sparse.bin"]),
                               ("tally", ["--root", ".", "sparse.bin"]), ("jsonq", ["--root", ".", "sparse.bin"]),
                               ("hash", ["--root", ".", "sparse.bin"]),
                               ("replace", ["--root", ".", "--old", "a", "--new", "b", "sparse.bin"])]:
                p = subprocess.run([binary(tool)] + args, capture_output=True, cwd=fx.root, timeout=600)
                res = Result(tool, [binary(tool)] + args, p.returncode, p.stdout, p.stderr)
                self.assertEqual(validate(res), [], (tool, p.returncode, p.stdout[:300]))
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
