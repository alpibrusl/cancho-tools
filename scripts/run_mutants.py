#!/usr/bin/env python3
"""Mutation check of `run` (docs/next-tools.md section 3): each mutant is tools/run/run.cho
with one deliberate defect, rebuilt as a variant by tests/conformance/test_run.py, and run
against it. A mutant is killed when a test fails. The file is restored after every mutant,
whatever happens.

    python3 scripts/run_mutants.py [name-substring ...]

Run where the pinned compiler is (`cancho` on PATH, or CANCHO). Exit status 1 if one survives.
"""
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "run" / "run.cho"

# (name, the text replaced, its replacement): each `old` occurs exactly once.
MUTANTS = [
    ("a name with a slash is a plain name", "        if int_of(name[i]) == '/' {", "        if int_of(name[i]) == 0 - 7 {"),
    ("an empty name is a plain name", "    if len(name) == 0 || bytes.equal(name, \".\") || bytes.equal(name, \"..\") {", "    if bytes.equal(name, \".\") || bytes.equal(name, \"..\") {"),
    ("`..` is a plain name", "    if len(name) == 0 || bytes.equal(name, \".\") || bytes.equal(name, \"..\") {", "    if len(name) == 0 || bytes.equal(name, \".\") {"),
    ("the environment names another PATH", "        assignment = buffer.append(heap, assignment, programs());", "        assignment = buffer.append(heap, assignment, \"/usr/bin:/bin\");"),
    ("the environment names no PATH", "        assignment = buffer.append(heap, assignment, \"PATH=\");", "        assignment = buffer.append(heap, assignment, \"P=\");"),
    ("the program starts in the working directory, not --root", "    return open_dir(fs, root);\n}", "    return open_dir(fs, \".\");\n}"),
    ("the program is started where the tool is (no directory handed)", "match exec_spawn_in(exec, dir, path, argv, env,", "match exec_spawn(exec, path, argv, env,"),
    ("the deadline is later than asked", "buffer.bytes(ip), most, timeout);", "buffer.bytes(ip), most, timeout + 20000);"),
    ("the output cap is a thousand times larger", "from_errors, input, most, most, timeout);", "from_errors, input, most * 1000, most * 1000, timeout);"),
    ("standard input is always given", "    if !refused && cli.has(parsed, table, \"stdin\") {", "    if !refused {"),
    ("every failed start is 'not found'", "                    if failure == 2 {", "                    if failure != 0 {"),
    ("an overrun is not marked on the stream", "cut_out = buffer.size(ot) >= most;", "cut_out = false;"),
    ("a timeout is not marked", "timed_out = true;\n                                                killed = 1;", "killed = 1;"),
    ("the exit status is lost", "process.Ran::Code(n) => {\n                                                code = n;", "process.Ran::Code(n) => {\n                                                code = 0;"),
    ("an argument after the program is dropped", "        var k = 1;\n        var bad = false;", "        var k = 2;\n        var bad = false;"),
]

original = SOURCE.read_text()


def restore(*_):
    SOURCE.write_text(original)


def build_and_test():
    try:
        p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_run"], cwd=ROOT / "tests" / "conformance",
                           capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "killed", "the tests hung (a mutant that waits on what it should not)"
    text = p.stdout + p.stderr
    if "refused" in text and "run_build" in text and p.returncode:
        return "does not build", text.strip()[-200:]
    failed = sorted({l.split(" ")[1] for l in text.splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main():
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    wanted = sys.argv[1:]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[0] for w in wanted)]
    verdict, _ = build_and_test()
    print("unmutated:", "pass" if verdict == "SURVIVED" else verdict, flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for name, old, new in chosen:
        if original.count(old) != 1:
            print("!! %s: the site occurs %d times" % (name, original.count(old)))
            return 1
        try:
            SOURCE.write_text(original.replace(old, new, 1))
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-9s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict != "killed":
            survivors.append(name)
    assert SOURCE.read_text() == original
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
