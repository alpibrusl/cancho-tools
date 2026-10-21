"""`toolbox.cli`'s flag lookups: the same answers as before, and work linear in the table.

Two gates, written before the change they judge.

DIFFERENTIAL. `tests/reference/cli_old.cho` is `toolbox.cli` as it stood when every lookup was
`entries x field-scan` (quadratic in the table). A generated program (`cli_cases.differential_program`)
holds ~35 tables -- empty, one entry, entries with missing fields, empty entries, names that are
prefixes of other names, short letters that are not letters, every kind, duplicates, 200 entries,
seeded random ones -- and asks both modules everything: every accessor at every index (and past both
ends), `index_of` and `nearest` for every name, every prefix of it, the name plus a byte and the
name with its first byte replaced, `parse` and every reader on many argument lists. Any difference
is a failure.

COST. One program per table size (10, 50, 150, 400 entries) makes N calls of one kind and nothing
else. Its instructions are counted by `valgrind --tool=callgrind` (exact, and independent of the
load on the machine, so this is a gate where a clock would not be); the program's own cost (the same
run with 0 calls) is subtracted, and the rest is divided by N. A call must cost at most
`BYTES_FACTOR x table bytes + FIXED` instructions, and from 150 to 400 entries the cost may grow by
at most 1.5 times as much as the table does (a quadratic lookup grows 7 times as much). The
constants are in this file, with what they were chosen from.
"""

import os
import pathlib
import random
import re
import subprocess
import tempfile
import unittest

import cli_cases
from harness import ROOT

COMPILER = os.environ.get("CANCHO", "cancho")


def build(tmp, name, source, extra=()):
    src = pathlib.Path(tmp) / (name + ".cho")
    src.write_text(source)
    exe = pathlib.Path(tmp) / name
    contract = sorted(str(p) for p in (ROOT / "contract").glob("*.cho"))
    b = subprocess.run([COMPILER, "build", *contract, *extra, str(src), "--std", "-o", str(exe)], capture_output=True, text=True)
    if b.returncode != 0:
        raise AssertionError("%s did not build:\n%s%s" % (name, b.stdout, b.stderr))
    return exe


class Differential(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tables = cli_cases.tables()
        cls.exe = build(cls.tmp.name, "diffcli", cli_cases.differential_program(cls.tables), [str(ROOT / "tests" / "reference" / "cli_old.cho")])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, table_id, *args):
        p = subprocess.run([str(self.exe), str(table_id), *args], capture_output=True, timeout=120)
        return p.returncode

    def test_the_tables_are_the_edge_cases_and_the_random_ones(self):
        self.assertGreaterEqual(len(self.tables), 30)
        self.assertIn("", self.tables)
        self.assertTrue(any(len(t.split(";")) >= 200 for t in self.tables))

    def test_accessors_and_lookups_answer_as_the_old_ones_did(self):
        r = random.Random(7)
        failures = []
        for i, table in enumerate(self.tables):
            queries = cli_cases.extra_queries(r) + cli_cases.names_in(table)
            code = self.ask(i, "acc", *queries)
            if code != 0:
                failures.append("table %d (%r...): %s" % (i, table[:60], cli_cases.CODES.get(code, code)))
        self.assertEqual(failures, [])

    def test_parse_and_the_readers_answer_as_the_old_ones_did(self):
        r = random.Random(11)
        failures = []
        runs = 0
        for i, table in enumerate(self.tables):
            for argv in cli_cases.argvs(table, r):
                runs += 1
                code = self.ask(i, "parse", *argv)
                if code != 0:
                    failures.append("table %d %r: %s" % (i, argv, cli_cases.CODES.get(code, code)))
        self.assertGreater(runs, 500)
        self.assertEqual(failures[:10], [])

    def test_a_wrong_table_or_mode_is_not_taken_for_agreement(self):
        # The harness must be able to say no: these are the program's own refusals.
        self.assertEqual(self.ask(10000, "acc"), 30)
        self.assertEqual(subprocess.run([str(self.exe)], capture_output=True).returncode, 31)


# What the constants were chosen from (Linux x86-64, callgrind, `cancho` 62c2be5; the numbers are the
# per-call instruction counts the Cost test prints, at 400 entries = 45,677 table bytes):
#
#   linear code:  index, has, kind_of, default_of    0.5 - 1.1 instructions per table byte
#                 parse (one flag)                    1.6 - 2.1
#                 nearest                             56  (a Levenshtein distance per entry, linear)
#                 introspect / skill                  61 / 19 (the JSON or Markdown it writes)
#   old code:     index, has, parse (one flag)        2,200 per table byte at 400 entries, and 7.1 times
#                 the cost at 150 entries where the table is 2.7 times as big (quadratic)
#
# BYTES_FACTOR is the linear code's dearest call (61) doubled; FIXED is the part of `introspect`
# that does not depend on the table (the rules catalogue, 275 K - 7 K at 10 entries) rounded up.
BYTES_FACTOR = 120
FIXED = 300000
GROWTH = 1.5

SIZES = (10, 50, 150, 400)
REPS = 2

# (name, mode, how to build the arguments after `MODE REPS`, repetitions). `parse` modes take their
# repetitions as the flags they are given.
OPS = ["index", "index-last", "kind-last", "default-last", "nearest", "has", "text", "parse-long", "parse-short", "parse-unknown", "introspect", "skill"]


def collected(argv):
    p = subprocess.run(["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null", *argv], capture_output=True, text=True, timeout=1800)
    m = re.search(r"Collected\s*:\s*(\d+)", p.stderr)
    if not m:
        raise AssertionError("callgrind said nothing about %s:\n%s" % (argv, p.stderr[-800:]))
    return int(m.group(1))


class Cost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exes = {k: build(cls.tmp.name, "cost%d" % k, cli_cases.cost_program(k)) for k in SIZES}
        cls.size = {k: len(cli_cases.cost_table(k)) for k in SIZES}
        cls.per_call = {op: {} for op in OPS}
        for k in SIZES:
            for op in OPS:
                cls.per_call[op][k] = cls.measure(k, op)
        print()
        print("instructions per call, by table size (entries -> bytes):", {k: cls.size[k] for k in SIZES})
        for op in OPS:
            print("  %-14s" % op + "".join("%14s" % format(cls.per_call[op][k], ",") for k in SIZES))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def measure(cls, k, op):
        exe = str(cls.exes[k])
        last = cli_cases.cost_name(k - 1)
        if op in ("index", "nearest", "has", "kind-last", "default-last", "introspect", "skill"):
            base, more, reps = [exe, op, "0"], [exe, op, str(REPS)], REPS
        elif op in ("index-last", "text"):
            base, more, reps = [exe, op, "0", last], [exe, op, str(REPS), last], REPS
        elif op == "parse-long":
            flags = ["--" + cli_cases.cost_name(k - 1 - j) for j in range(4)]
            base, more, reps = [exe, "parse", "0"], [exe, "parse", "4", *flags], 4
        elif op == "parse-short":
            base, more, reps = [exe, "parse", "0"], [exe, "parse", "4", "-Q", "-Q", "-Q", "-Q"], 4
        else:
            base, more, reps = [exe, "parse", "0"], [exe, "parse", "4", "--zz-1", "--zz-2", "--zz-3", "--zz-4"], 4
        # The run is judged right by its own exit status (0), except that `parse` modes cannot be wrong.
        ok = subprocess.run(more, capture_output=True).returncode
        if ok != 0:
            raise AssertionError("%s at %d entries answered wrongly" % (op, k))
        return (collected(more) - collected(base)) // reps

    def test_every_call_costs_at_most_a_constant_per_table_byte(self):
        over = []
        for op in OPS:
            for k in SIZES:
                allowed = BYTES_FACTOR * self.size[k] + FIXED
                if self.per_call[op][k] > allowed:
                    over.append("%s at %d entries (%d bytes): %s instructions, at most %s allowed" % (op, k, self.size[k], format(self.per_call[op][k], ","), format(allowed, ",")))
        if over:
            self.fail("\n" + "\n".join(over))

    def test_the_cost_grows_with_the_table_not_with_its_square(self):
        grew = []
        for op in OPS:
            small, big = self.per_call[op][150], self.per_call[op][400]
            table_growth = self.size[400] / self.size[150]
            if big > GROWTH * table_growth * small + FIXED:
                grew.append("%s: %s -> %s from 150 to 400 entries (table x%.1f)" % (op, format(small, ","), format(big, ","), table_growth))
        if grew:
            self.fail("\n" + "\n".join(grew))


if __name__ == "__main__":
    unittest.main()
