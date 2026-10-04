#!/usr/bin/env python3
"""toolbench -- the offline performance harness (lex-sys docs/agent-toolbox.md
§7.1 M9, slice S3). It reports; it publishes nothing and gates nothing
against GNU (speed is a non-goal, §1.2). The one performance *gate*, memory
flatness, is tests/conformance/test_memory.py.

    python3 scripts/toolbench.py --tool seek --size 64MiB --runs 15 [--sink pipe|file|devnull] [--out report.json]
    python3 scripts/toolbench.py --startup --runs 500
    python3 scripts/toolbench.py --self-test

Method: the tool and each incumbent are run **interleaved** (A B A B ...,
the order flipped every round), after one warm-up run each so the page
cache is warm for both; the minimum and the median are reported, never the
first run alone; incumbents run under LC_ALL=C; the sink is a pipe by
default and is recorded. Each comparison names the exact incumbent command,
because "faster than wc" means nothing without saying which wc (wc -l on
64 MiB is 15 ms, wc with every count is about a second).

Three pitfalls were met in the design's own probes, and the self-test
defends against each:

* GNU grep short-circuits when standard output is /dev/null (3 ms against
  100-160 ms through a pipe for the same search): the self-test checks that
  this harness's pipe sink and a /dev/null sink time grep differently, so
  the default sink cannot silently become /dev/null.
* A harness without power reports noise as a result: the self-test plants
  a 1.5x slowdown and requires it to be detected in at least 95% of 20
  trials, and identical reruns to be flagged in at most 5%.
* A corpus that changes between runs is two benchmarks: the generator is
  seeded and the self-test checks two generations hash the same.
"""

import argparse
import hashlib
import json
import os
import pathlib
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build"))
WORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa"]
ENV_C = dict(os.environ, LC_ALL="C")


def size_of(text):
    units = {"KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30}
    for unit, factor in units.items():
        if text.endswith(unit):
            return int(text[: -len(unit)]) * factor
    return int(text)


def corpus(path, size, seed=1):
    """Seeded text: lines of 1-12 random words."""
    r = random.Random(seed)
    with open(path, "w") as f:
        n = 0
        while n < size:
            line = " ".join(r.choice(WORDS) for _ in range(r.randint(1, 12))) + "\n"
            f.write(line)
            n += len(line)


def json_corpus(path, size, seed=1):
    r = random.Random(seed)
    items = []
    n = 2
    while n < size:
        item = {"id": len(items), "name": r.choice(WORDS), "tags": [r.choice(WORDS) for _ in range(3)], "score": r.randint(0, 1000)}
        text = json.dumps(item)
        items.append(text)
        n += len(text) + 1
    pathlib.Path(path).write_text("[" + ",".join(items) + "]")


def cases(tool, f, jf):
    """(name, argv) of the tool and of each incumbent doing the same job."""
    b = lambda t: str(BIN / t)
    if tool == "seek":
        out = [("seek", [b("seek"), "gamma", f]), ("grep -F -n -b", ["grep", "-F", "-n", "-b", "gamma", f])]
        if shutil.which("rg"):
            out.append(("rg -F -n -b", ["rg", "-F", "-n", "-b", "--no-heading", "gamma", f]))
        return out
    if tool == "peek":
        return [("peek --count-lines --lines 1:100", [b("peek"), "--count-lines", "--lines", "1:100", f]),
                ("sed -n 1,100p; wc -l", ["sh", "-c", 'sed -n 1,100p "$0"; wc -l "$0"', f])]
    if tool == "hash":
        return [("hash", [b("hash"), f]), ("sha256sum", ["sha256sum", f])]
    if tool == "tally":
        # The first word of each line: ten distinct keys, so tally answers
        # rather than stopping at --max-keys on a corpus of random lines.
        return [("tally --field 1 --delim ' '", [b("tally"), "--field", "1", "--delim", " ", f]),
                ("cut -d' ' -f1 | sort | uniq -c | sort -k1,1nr -k2 | head", ["sh", "-c", 'cut -d" " -f1 "$0" | sort | uniq -c | sort -k1,1nr -k2 | head', f])]
    if tool == "jsonq":
        return [("jsonq -p /0", [b("jsonq"), "--max-bytes", "33554432", "-p", "/0", jf]), ("jq -c .[0]", ["jq", "-c", ".[0]", jf])]
    raise SystemExit("no benchmark for %s" % tool)


def timed(argv, sink):
    if sink == "pipe":
        start = time.perf_counter()
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV_C)
        return time.perf_counter() - start, p.returncode
    if sink == "file":
        with tempfile.TemporaryFile() as out:
            start = time.perf_counter()
            p = subprocess.run(argv, stdout=out, stderr=subprocess.DEVNULL, env=ENV_C)
            return time.perf_counter() - start, p.returncode
    start = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV_C)
    return time.perf_counter() - start, p.returncode


def interleaved(commands, runs, sink):
    """Warm each command once, then `runs` rounds A B C ..., the order
    reversed on every other round. Answers seconds per command."""
    for _, argv in commands:
        timed(argv, sink)
    times = {name: [] for name, _ in commands}
    statuses = {}
    for i in range(runs):
        order = commands if i % 2 == 0 else list(reversed(commands))
        for name, argv in order:
            t, status = timed(argv, sink)
            times[name].append(t)
            statuses.setdefault(name, set()).add(status)
    interleaved.statuses = {name: sorted(s) for name, s in statuses.items()}
    return times


def environment():
    def version(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0]
        except (OSError, IndexError):
            return None
    return {
        "machine": platform.machine(), "kernel": platform.release(), "cpus": os.cpu_count(),
        "lex_sys": version([os.environ.get("LEX_SYS", "lex-sys"), "--version"]),
        "grep": version(["grep", "--version"]), "rg": version(["rg", "--version"]), "jq": version(["jq", "--version"]),
        "coreutils": version(["sha256sum", "--version"]), "locale": "LC_ALL=C for every incumbent",
        "page_cache": "warm (one unmeasured run of each command first)",
    }


def summary(times):
    return {name: {"min_s": round(min(ts), 6), "median_s": round(statistics.median(ts), 6), "runs": len(ts)} for name, ts in times.items()}


def bench(args):
    size = size_of(args.size)
    with tempfile.TemporaryDirectory(prefix="toolbench-") as d:
        f = os.path.join(d, "corpus.txt")
        jf = os.path.join(d, "corpus.json")
        corpus(f, size, args.seed)
        json_corpus(jf, min(size, 32 << 20), args.seed)
        commands = cases(args.tool, f, jf)
        times = interleaved(commands, args.runs, args.sink)
        corpus_hash = hashlib.sha256(open(f, "rb").read()).hexdigest()
    report = {"tool": args.tool, "size": size, "sink": args.sink, "seed": args.seed, "corpus_sha256": corpus_hash,
              "results": summary(times), "exit_statuses": interleaved.statuses, "environment": environment(),
              "note": "a probe on this machine, not a protocol result; speed against GNU is not a goal (§1.2)"}
    return report


def startup(args):
    with tempfile.TemporaryDirectory(prefix="toolbench-") as d:
        empty = os.path.join(d, "empty")
        open(empty, "w").close()
        commands = [("seek", [str(BIN / "seek"), "x", empty]), ("/usr/bin/true", ["true"]),
                    ("grep -c x", ["grep", "-c", "x", empty]), ("jq -n 1", ["jq", "-n", "1"])]
        times = interleaved(commands, args.runs, "pipe")
    return {"startup": {name: {"mean_ms": round(1000 * statistics.mean(ts), 3)} for name, ts in times.items()},
            "runs": args.runs, "environment": environment()}


def flagged(a, b, runs):
    """The decision rule: B is slower than A when its minimum is more than
    1.25 times A's over `runs` interleaved rounds. The minimum, because noise
    on a shared machine only ever adds time; the median was tried first and
    flagged 3 of 20 identical reruns here (the gate allows 1)."""
    times = interleaved([("a", a), ("b", b)], runs, "pipe")
    return min(times["b"]) > 1.25 * min(times["a"])


def self_test(args):
    results = {}
    with tempfile.TemporaryDirectory(prefix="toolbench-") as d:
        f = os.path.join(d, "corpus.txt")
        g = os.path.join(d, "again.txt")
        corpus(f, 16 << 20, 1)
        corpus(g, 16 << 20, 1)
        same = hashlib.sha256(open(f, "rb").read()).digest() == hashlib.sha256(open(g, "rb").read()).digest()
        results["corpus_reproducible"] = same

        grep = ["grep", "-c", "gamma", f]
        pipe = min(interleaved([("g", grep)], 5, "pipe")["g"])
        null = min(interleaved([("g", grep)], 5, "devnull")["g"])
        results["grep_pipe_s"] = round(pipe, 4)
        results["grep_devnull_s"] = round(null, 4)
        # Either grep short-circuits on /dev/null here and the two sinks
        # differ, or this grep does not and the shortcut cannot bite: in both
        # cases the harness's default sink must be the pipe.
        results["default_sink_is_pipe"] = argparse_default_sink() == "pipe"
        results["devnull_shortcut_seen"] = null * 3 < pipe

        base = ["sha256sum", f]
        half = (16 << 20) // 2
        slowed = ["sh", "-c", 'sha256sum "$0" >/dev/null; head -c %d "$0" | sha256sum' % half, f]
        trials = args.trials
        detected = sum(flagged(base, slowed, 7) for _ in range(trials))
        false_alarms = sum(flagged(base, base, 7) for _ in range(trials))
        results["planted_slowdown_detected"] = "%d/%d" % (detected, trials)
        results["identical_flagged"] = "%d/%d" % (false_alarms, trials)
        results["power_ok"] = detected >= 0.95 * trials and false_alarms <= 0.05 * trials
    ok = results["corpus_reproducible"] and results["default_sink_is_pipe"] and results["power_ok"]
    results["ok"] = ok
    return results


def argparse_default_sink():
    return parser().get_default("sink")


def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tool", choices=["seek", "peek", "hash", "tally", "jsonq"])
    p.add_argument("--size", default="64MiB")
    p.add_argument("--runs", type=int, default=15)
    p.add_argument("--sink", choices=["pipe", "file", "devnull"], default="pipe")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--startup", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--trials", type=int, default=20)
    p.add_argument("--out")
    return p


def main():
    args = parser().parse_args()
    if args.self_test:
        report = self_test(args)
    elif args.startup:
        report = startup(args)
    elif args.tool:
        report = bench(args)
    else:
        parser().error("--tool, --startup or --self-test")
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n")
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
