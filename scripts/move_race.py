#!/usr/bin/env python3
"""The limit docs/next-tools.md §4 measured, and its closing: `move` looked at the destination and
then renamed with `renameat`, which replaces silently. Both names are locked, so two *toolbox*
processes cannot interleave there; a process that takes no lock was not stopped (55 of 20,000 random
arrivals and 100 of 100 delayed ones lost the creator's file). `move` now renames with `dir_rename_new`
(`renameat2` with `RENAME_NOREPLACE`; `renameatx_np` with `RENAME_EXCL` on macOS), so the kernel refuses
a name taken after the look. This is the measurement, and it runs against either build: pass `--bin`.

    python3 scripts/move_race.py [--trials N] [--bin DIR] [--mode natural|delayed]

Each trial: a source and no destination; `move SRC DEST` runs while a creator in this process makes
`DEST` with `open(O_CREAT|O_EXCL)`, which takes no lock. `natural`: the creator arrives at a random moment
(0 to 2.5 ms) after the mover is launched, so the chance it lands in the window is what it would be for a
careless process. `delayed`: the mover's `renameat`/`renameat2` is delayed 30 ms under `strace`, and the creator arrives
at 15 ms: not a rate, a demonstration that the window exists. A trial is LOST when the creator's file was made
and `move` then reported success with DEST holding the mover's bytes: the creator's file was
replaced without a word. The answer is counts, not an assertion.
"""
import argparse
import os
import random
import time
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--mode", choices=["natural", "delayed"], default="natural")
    ap.add_argument("--bin", default=str(os.environ.get("TOOLBOX_BIN", ROOT / "build")))
    a = ap.parse_args()
    move = str(pathlib.Path(a.bin) / "move")
    work = pathlib.Path(tempfile.mkdtemp(prefix="move-race-"))
    counts = {"creator_won": 0, "mover_won": 0, "LOST": 0, "other": 0}
    try:
        for _ in range(a.trials):
            for n in ("src", "dest"):
                try:
                    (work / n).unlink()
                except FileNotFoundError:
                    pass
            (work / "src").write_bytes(b"mover\n")
            made = []
            cmd = [move, "--root", str(work), "src", "dest"]
            if a.mode == "delayed":
                cmd = ["strace", "-f", "-qq", "-o", "/dev/null", "-e", "trace=renameat,renameat2", "-e", "inject=renameat,renameat2:delay_enter=30ms"] + cmd
                arrive = 0.015
            else:
                arrive = random.uniform(0, 0.0025)
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.perf_counter() + arrive
            while time.perf_counter() < deadline:
                pass
            try:
                fd = os.open(work / "dest", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(fd, b"creator\n")
                os.close(fd)
                made.append(True)
            except FileExistsError:
                pass
            p.communicate()
            content = (work / "dest").read_bytes() if (work / "dest").exists() else b""
            if made and p.returncode == 0 and content == b"mover\n":
                counts["LOST"] += 1
            elif made and p.returncode == 5:
                counts["creator_won"] += 1
            elif not made and p.returncode == 0:
                counts["mover_won"] += 1
            else:
                counts["other"] += 1
    finally:
        for f in os.listdir(work):
            os.unlink(work / f)
        os.rmdir(work)
    n = a.trials
    print("%d trials: %s" % (n, counts))
    print("a creator's file replaced without a word: %d of %d" % (counts["LOST"], n))
    if counts["LOST"] == 0:
        print("no loss seen; at 95%% confidence the rate is below %.4f%% (rule of three)" % (300.0 / n))


if __name__ == "__main__":
    main()
