"""M9 -- memory does not scale with the input (lex-sys docs/agent-toolbox.md
D8 point 4): each streaming tool, run on 1 MiB, 64 MiB and 256 MiB, keeps
its peak resident memory within a factor of 1.5. The examples/seek this
replaces failed it by about 95 times (787,816 KB against 8,304 KB).

The peak is the tool's own `ru_maxrss`, read by `maxrss.c`, a launcher small
enough that the mark a forked child inherits is not the test runner's (see
its header) -- and not RUSAGE_CHILDREN, which is the maximum over every child
the test has waited for. MEMORY_SIZES_MIB overrides the sizes.

Speed against GNU is not gated (§1.2): it is reported by scripts/toolbench.py.
"""

import os
import subprocess
import tempfile
import unittest

from harness import binary

WORDS = [b"alpha", b"beta", b"gamma", b"delta", b"epsilon", b"zeta", b"eta", b"theta"]


def make(path, size):
    """`size` bytes of lines drawn from 40 distinct lines, so that tally's
    state stays small and only the input grows."""
    lines = [b" ".join(WORDS[(i * 7 + j) % 8] for j in range(1 + i % 9)) + b"\n" for i in range(40)]
    block = b"".join(lines) * 256
    with open(path, "wb") as f:
        written = 0
        while written + len(block) <= size:
            f.write(block)
            written += len(block)
        f.write(block[:size - written])


def launcher(directory):
    """Compile maxrss.c (see its header for why it exists)."""
    out = os.path.join(directory, "maxrss")
    subprocess.run(["cc", "-O2", "-o", out, os.path.join(os.path.dirname(__file__), "maxrss.c")], check=True)
    return out


def peak_kb(measure, argv):
    p = subprocess.run([measure] + argv, capture_output=True, check=True)
    kb, status = p.stdout.split()
    return int(status), int(kb)


class MemoryFlatness(unittest.TestCase):
    def test_peak_memory_is_flat_in_the_input(self):
        sizes = [int(s) for s in os.environ.get("MEMORY_SIZES_MIB", "1,64,256").split(",")]
        report = []
        failures = []
        with tempfile.TemporaryDirectory(prefix="toolbox-mem-") as d:
            measure = launcher(d)
            # The launcher's own floor, for the report: a tool cannot be
            # measured below it.
            floor = peak_kb(measure, ["/bin/true"])[1]
            files = {}
            for mib in sizes:
                files[mib] = os.path.join(d, "%d.txt" % mib)
                make(files[mib], mib << 20)
            for tool, args in [("seek", ["gamma"]), ("peek", ["--count-lines", "--lines", "1:10"]), ("hash", []), ("tally", [])]:
                peaks = []
                for mib in sizes:
                    status, kb = peak_kb(measure, [binary(tool)] + args + [files[mib]])
                    self.assertEqual(status, 0, (tool, mib))
                    peaks.append(kb)
                ratio = max(peaks) / min(peaks)
                report.append("%-5s %s KB  max/min %.2f" % (tool, " / ".join(str(k) for k in peaks), ratio))
                if ratio > 1.5:
                    failures.append("%s: %s" % (tool, peaks))
        print("\nM9 at %s MiB (/bin/true measures %d KB):\n  " % (sizes, floor) + "\n  ".join(report))
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
