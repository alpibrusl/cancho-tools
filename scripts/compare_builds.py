#!/usr/bin/env python3
"""Byte-identity of two builds of the tools: the same arguments must give the same status, the same
standard output and the same standard error.

    python3 scripts/compare_builds.py BUILD_A BUILD_B

Each directory holds the tools as `cancho build` writes them (`build/`). The invocations are
`introspect` and `skill` of every tool (the table drives both, and `scripts/mcp.py` writes the MCP
tool definitions from the first), the conformance corpus of `tests/conformance/test_schema.py` (every
shape of success and refusal, on one shared fixture), and, for every flag of every tool read from its
own `introspect`: the flag alone, with `=value`, mistyped (a letter dropped, a letter added: the
repairs that name the nearest flag), its short letter alone and doubled, and an unknown long and
short flag. Exit status 1 and the first differences printed when anything differs.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "conformance"))

import harness  # noqa: E402
import test_schema  # noqa: E402


# argv[0] is the tool's bare name in both runs: a repair that retries the command repeats it, and the
# two builds live in different directories.
def call(directory, tool, argv, stdin, cwd):
    try:
        p = subprocess.run([tool, *argv], executable=str(pathlib.Path(directory) / tool), input=stdin, capture_output=True, cwd=cwd, timeout=60)
    except subprocess.TimeoutExpired:
        return ("timeout", b"", b"")
    return (p.returncode, p.stdout, p.stderr)


def probes(directory):
    out = []
    for tool in harness.EVERY:
        out += [(tool, ["introspect"]), (tool, ["skill"]), (tool, ["introspect", "--output", "json"]), (tool, ["introspect", "--output=json"]), (tool, [])]
        doc = json.loads(subprocess.run([str(pathlib.Path(directory) / tool), "introspect"], capture_output=True, check=True).stdout)
        for f in doc["flags"]:
            name = f["name"][2:]
            out += [(tool, [f["name"]]), (tool, [f["name"] + "=1"]), (tool, [f["name"], "1"]), (tool, [f["name"], f["name"]]),
                    (tool, ["--" + name[:-1]]), (tool, ["--" + name + "x"]), (tool, ["--" + name[1:]]), (tool, ["--" + name[:-1] + "q=3"])]
            if f["short"]:
                out += [(tool, [f["short"]]), (tool, [f["short"], f["short"]]), (tool, [f["short"] + "3"])]
        out += [(tool, ["--zzzzzz"]), (tool, ["-Q"]), (tool, ["-Q", "--zz=1"]), (tool, ["--"]), (tool, ["-"])]
    return out


def main():
    a, b = (str(pathlib.Path(p).resolve()) for p in sys.argv[1:3])
    differences = []
    count = 0
    with tempfile.TemporaryDirectory() as empty:
        for tool, argv in probes(a):
            count += 1
            x, y = call(a, tool, argv, b"", empty), call(b, tool, argv, b"", empty)
            if x != y:
                differences.append((tool, argv, x, y))
    fx = harness.Fixture()
    try:
        for tool, argv, stdin in test_schema.corpus(fx):
            count += 1
            x, y = call(a, tool, argv, stdin, fx.root), call(b, tool, argv, stdin, fx.root)
            if x != y:
                differences.append((tool, argv, x, y))
    finally:
        fx.cleanup()
    print("%d invocations, %d differ" % (count, len(differences)))
    for tool, argv, x, y in differences[:10]:
        print("  %s %s\n    A: %s\n    B: %s" % (tool, argv, [x[0], x[1][:200], x[2][:200]], [y[0], y[1][:200], y[2][:200]]))
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
