#!/usr/bin/env python3
"""`write --create` against a creator that takes no lock: the same measurement as `move_race.py`.

    python3 scripts/write_race.py [--trials N] [--bin DIR] [--mode natural|delayed] [--window-ms MS]

`write --create` looks at the path, and then installs a temporary it has written. With a replacing
`renameat` as the last step (what it did before `atomic.create`), a process that makes the path in between
-- and takes no lock -- lost its file without a word. Now the last step is `renameat2(RENAME_NOREPLACE)`
(`renameatx_np(RENAME_EXCL)` on macOS), and the kernel refuses a taken name.

Each trial: no destination; `write --create --stdin dest` runs while a creator in this process makes `dest`
with `open(O_CREAT|O_EXCL)` and no lock. `natural`: the creator arrives at a random moment 0 to --window-ms
(default 6) after the writer is launched, which spans the writer's whole look-to-rename interval (it
fsyncs its temporary in between). `delayed`: the writer's rename is delayed 30 ms under `strace` and the creator
arrives at 25 ms. A trial is LOST when the creator's file was made and `write` then reported success with
`dest` holding the writer's bytes. A stray temporary (`.dest.*.lexsys-tmp`) is counted as LEFTOVER.
The answer is counts, not an assertion.
"""
import argparse
import os
import pathlib
import random
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--mode", choices=["natural", "delayed"], default="natural")
    ap.add_argument("--window-ms", type=float, default=6.0)
    ap.add_argument("--bin", default=str(os.environ.get("TOOLBOX_BIN", ROOT / "build")))
    a = ap.parse_args()
    write = str(pathlib.Path(a.bin) / "write")
    work = pathlib.Path(tempfile.mkdtemp(prefix="write-race-"))
    counts = {"creator_won": 0, "writer_won": 0, "LOST": 0, "LEFTOVER": 0, "other": 0}
    try:
        for _ in range(a.trials):
            for n in os.listdir(work):
                if not n.endswith(".lexsys-lock"):
                    os.unlink(work / n)
            cmd = [write, "--root", str(work), "--create", "--stdin", "dest"]
            if a.mode == "delayed":
                cmd = ["strace", "-f", "-qq", "-o", "/dev/null", "-e", "trace=renameat,renameat2", "-e", "inject=renameat,renameat2:delay_enter=30ms"] + cmd
                arrive = 0.025
            else:
                arrive = random.uniform(0, a.window_ms / 1000.0)
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p.stdin.write(b"writer\n")
            p.stdin.close()
            deadline = time.perf_counter() + arrive
            while time.perf_counter() < deadline:
                pass
            made = False
            try:
                fd = os.open(work / "dest", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(fd, b"creator\n")
                os.close(fd)
                made = True
            except FileExistsError:
                pass
            p.communicate()
            content = (work / "dest").read_bytes() if (work / "dest").exists() else b""
            if any(n.endswith(".lexsys-tmp") for n in os.listdir(work)):
                counts["LEFTOVER"] += 1
            if made and p.returncode == 0 and content == b"writer\n":
                counts["LOST"] += 1
            elif made and p.returncode == 5 and content == b"creator\n":
                counts["creator_won"] += 1
            elif not made and p.returncode == 0 and content == b"writer\n":
                counts["writer_won"] += 1
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
