#!/usr/bin/env python3
"""archive against gzip and tar: time, peak memory and size of one process doing one job (docs/archive.md section 11, G9).

    python3 scripts/bench_archive.py [--size-mib 64] [--runs 3] [--json out.json]

The input is one file of random lowercase words (a fixed seed), `--size-mib` MiB. Each job is one process timed with `wait4`,
stdout and stderr to /dev/null; the median of `--runs` runs is reported with the spread. The jobs:

    gz create    archive --op create --as gz       gzip -c            (one file to a .gz)
    tgz create   archive --op create --as tgz      tar czf            (the same file in a tar, gzipped)
    tgz list     archive --op list                 tar tzf            (inflate, check the CRC, walk the tar)
    gz inflate   (archive has no gunzip to stdout)  gzip -dc

and checks that what `archive` wrote is a valid gzip/tgz to the incumbents (`gzip -dc` and `tar xzf` give the input back)
and that what the incumbents wrote lists correctly with `archive`. It needs build/archive (`cancho build`).
"""

import argparse
import hashlib
import json
import os
import pathlib
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build"))


def make_input(path, mib):
    rng = random.Random(20261021)
    letters = "abcdefghijklmnopqrstuvwxyz"
    vocabulary = ["".join(rng.choice(letters) for _ in range(rng.randint(3, 9))) for _ in range(20000)]
    target = mib * 1024 * 1024
    with open(path, "wb") as f:
        written = 0
        while written < target:
            line = " ".join(rng.choice(vocabulary) for _ in range(rng.randint(6, 14))) + "\n"
            data = line.encode()
            if written + len(data) > target:
                data = data[: target - written]
            f.write(data)
            written += len(data)


def measure(argv, cwd, stdout=None):
    """(wall, cpu, peak RSS in KiB, exit status)."""
    out = open(stdout, "wb") if stdout else subprocess.DEVNULL
    t0 = time.perf_counter()
    p = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=subprocess.DEVNULL)
    _, status, ru = os.wait4(p.pid, 0)
    wall = time.perf_counter() - t0
    p.returncode = os.waitstatus_to_exitcode(status)
    if stdout:
        out.close()
    return wall, ru.ru_utime + ru.ru_stime, ru.ru_maxrss, p.returncode


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def median_row(samples):
    walls = sorted(s[0] for s in samples)
    return {"median_s": statistics.median(walls), "min_s": walls[0], "max_s": walls[-1],
            "cpu_s": statistics.median(s[1] for s in samples), "rss_kib": statistics.median(s[2] for s in samples),
            "failed": sum(1 for s in samples if s[3] != 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-mib", type=int, default=64)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--json")
    a = ap.parse_args()
    archive = str(BIN / "archive")
    work = pathlib.Path(tempfile.mkdtemp(prefix="archive-bench-"))
    try:
        data = work / "words.txt"
        make_input(data, a.size_mib)
        want = sha(data)
        rows = []

        def job(name, tool, argv, cwd, cleanup=(), stdout=None):
            samples = []
            for _ in range(a.runs):
                for c in cleanup:
                    pathlib.Path(c).unlink(missing_ok=True)
                samples.append(measure(argv, cwd, stdout))
            row = median_row(samples)
            row.update({"job": name, "tool": tool})
            rows.append(row)
            print("%-11s %-8s median %7.3f s  [%6.3f..%6.3f]  cpu %6.3f s  rss %8.0f KiB  failed %d"
                  % (name, tool, row["median_s"], row["min_s"], row["max_s"], row["cpu_s"], row["rss_kib"], row["failed"]), flush=True)
            return row

        sizes = {}
        # create
        a_gz, i_gz = work / "a.gz", work / "i.gz"
        a_tgz, i_tgz = work / "a.tgz", work / "i.tgz"
        job("gz create", "archive", [archive, "--root", str(work), "--op", "create", "--as", "gz", "--max-bytes", str(1 << 30), "a.gz", "words.txt"], work, cleanup=[a_gz])
        job("gz create", "gzip", ["gzip", "-c", "words.txt"], work, stdout=str(i_gz))
        job("tgz create", "archive", [archive, "--root", str(work), "--op", "create", "--as", "tgz", "--max-bytes", str(1 << 30), "a.tgz", "words.txt"], work, cleanup=[a_tgz])
        job("tgz create", "tar czf", ["tar", "czf", "i.tgz", "words.txt"], work, cleanup=[i_tgz])
        for name, p in (("archive gz", a_gz), ("gzip", i_gz), ("archive tgz", a_tgz), ("tar czf", i_tgz)):
            sizes[name] = p.stat().st_size
        # inflate / list, each tool on its own and the other's output
        job("tgz list", "archive", [archive, "--root", str(work), "--op", "list", "--max-bytes", str(1 << 30), "i.tgz"], work)
        job("tgz list", "tar tzf", ["tar", "tzf", "i.tgz"], work)
        job("gz inflate", "gzip -dc", ["gzip", "-dc", "i.gz"], work)
        # interoperability: the incumbents read what archive wrote
        got = subprocess.run(["gzip", "-dc", str(a_gz)], capture_output=True)
        ok_gz = hashlib.sha256(got.stdout).hexdigest() == want
        ex = work / "ex"
        ex.mkdir()
        r = subprocess.run(["tar", "xzf", str(a_tgz), "-C", str(ex)], capture_output=True)
        ok_tgz = r.returncode == 0 and sha(ex / "words.txt") == want
        l = subprocess.run([archive, "--root", str(work), "--op", "list", "--max-bytes", str(1 << 30), "a.tgz"], capture_output=True)
        print("sizes (bytes):", sizes)
        print("gzip -dc of archive's .gz equals the input:", ok_gz, "| tar xzf of archive's .tgz equals it:", ok_tgz,
              "| archive lists its own tgz:", l.returncode == 0)
        if a.json:
            pathlib.Path(a.json).write_text(json.dumps({"rows": rows, "sizes": sizes, "ok": [ok_gz, ok_tgz]}, indent=1))
        return 0 if (ok_gz and ok_tgz) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
