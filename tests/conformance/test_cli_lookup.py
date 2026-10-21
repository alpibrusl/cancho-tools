"""`toolbox.cli`'s flag lookups: the same answers as before, and work linear in the table.

Two gates, written before the change they judge.

DIFFERENTIAL. `tests/reference/cli_old.cho` is `toolbox.cli` as it stood when every lookup was
`entries x field-scan` (quadratic in the table). A generated program (`cli_cases.differential_program`)
holds ~35 tables -- empty, one entry, entries with missing fields, empty entries, names that are
prefixes of other names, short letters that are not letters, every kind, duplicates, 200 entries,
seeded random ones -- and asks both modules everything: every accessor at every index (and past both
ends), `index_of` and `nearest` for every name, every prefix of it, the name plus a byte and the
name with its first byte replaced, `parse` and every reader on many argument lists. Any difference
is a failure. `tests/reference/rules_old.cho` is `toolbox.rules` the same way (its catalogue is read
by tag with the same per-index scan), and each table also serves as a tool's own rules
(`extra_rules`) for it.

COST. One program per table size (10, 50, 150, 400 entries) makes N calls of one kind and nothing
else. Its instructions are counted by `valgrind --tool=callgrind` (exact, and independent of the
load on the machine, so this is a gate where a clock would not be); the program's own cost (the same
run with 0 calls) is subtracted, and the rest is divided by N. A call must cost at most
`BYTES_FACTOR x table bytes + FIXED` instructions, and from 150 to 400 entries the cost may grow by
at most 1.5 times as much as the table does (a quadratic lookup grows 7 times as much). The
constants are in this file, with what they were chosen from.
"""

import json
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
        cls.exe = build(cls.tmp.name, "diffcli", cli_cases.differential_program(cls.tables), [str(ROOT / "tests" / "reference" / "cli_old.cho"), str(ROOT / "tests" / "reference" / "rules_old.cho")])

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


def catalogue():
    text = (ROOT / "contract" / "rules.cho").read_text()
    return re.search(r'pub fn catalogue\(\) -> \[\] &static \[byte\] \{\n    return "(.*?)";', text).group(1)


def fields(entry, n):
    f = entry.split("|")
    return (f + [""] * n)[:n] if n > len(f) else f


def digits(text):
    n = 0
    for c in text:
        n = n * 10 + (int(c) if c.isdigit() else -1)
    return n


class Describe(unittest.TestCase):
    """`introspect` and `skill` of a tool whose flag table, rules and rules of its own are these, against
    what the table says, worked out here from the documented format and not from the code."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cases = cli_cases.describe_cases()
        cls.exe = build(cls.tmp.name, "describe", cli_cases.describe_program(cls.cases))
        cls.catalogue = [e for e in catalogue().split(";")]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def say(self, i, which):
        p = subprocess.run([str(self.exe), str(i), which], capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0)
        return p.stdout.decode()

    def entries(self, table):
        return table.split(";") if table else []

    def find(self, extra, tag):
        for e in self.catalogue + self.entries(extra):
            if fields(e, 1)[0] == tag:
                return e
        return None

    def flags(self, table):
        out = []
        for e in self.entries(table):
            f = fields(e, 6)
            out.append({"name": "--" + f[0], "short": "-" + f[1] if f[1] else None, "kind": f[2], "role": f[3], "default": f[4] or None, "help": f[5]})
        return out

    def rules(self, rules, extra):
        out = []
        for tag in self.entries(rules):
            e = self.find(extra, tag)
            if e is None:
                out.append({"rule": tag, "exit": 1, "repairable": "never", "summary": ""})
            else:
                f = fields(e, 4)
                out.append({"rule": tag, "exit": digits(f[1]), "repairable": f[2], "summary": f[3]})
        return out

    def codes(self, table, rules):
        found = {0}
        if "dry-run|" in table:
            found.add(9)
        found |= {r["exit"] for r in rules if 0 <= r["exit"] <= 8}
        return sorted(found)

    def test_the_lists_written_from_literals_are_whole(self):
        # `put_list` writes `not_narrowable` and `evidence.offline` from `;`-separated literals in describe.cho.
        lists = re.findall(r'put_list\(heap, w, "(.*?)"\);\n', (ROOT / "contract" / "describe.cho").read_text())
        self.assertEqual(len(lists), 2)
        d = json.loads(self.say(0, "introspect"))
        self.assertEqual(d["not_narrowable"], lists[0].replace('\\"', '"').split(";"))
        self.assertEqual(d["evidence"]["offline"], lists[1].split(";"))

    def test_introspect_says_what_the_table_says(self):
        wrong = []
        for i, (table, rules, extra) in enumerate(self.cases):
            d = json.loads(self.say(i, "introspect"))
            want_rules = self.rules(rules, extra)
            got = (d["flags"], d["rules"], [c["code"] for c in d["exit_codes"]])
            want = (self.flags(table), want_rules, self.codes(table, want_rules))
            if got != want:
                wrong.append("case %d (%r): %r != %r" % (i, table[:50], got, want))
        self.assertEqual(wrong[:3], [])

    def test_skill_says_what_the_table_says(self):
        wrong = []
        for i, (table, rules, extra) in enumerate(self.cases):
            text = self.say(i, "skill")
            want_rules = self.rules(rules, extra)
            lines = []
            for f in self.flags(table):
                line = "- `" + f["name"] + ("" if f["kind"] == "bool" else " <" + f["kind"] + ">") + "`"
                if f["short"]:
                    line += " (`" + f["short"] + "`)"
                line += ": " + f["help"] + (" Default `" + f["default"] + "`." if f["default"] else "")
                lines.append(line + "\n")
            rule_lines = []
            for r in want_rules:
                line = "- `%s` (exit %d)" % (r["rule"], r["exit"])
                if self.find(extra, r["rule"]) is not None:
                    line += ": " + r["summary"]
                rule_lines.append(line + "\n")
            got_flags = text.split("## Flags\n\n", 1)[1].split("\nAn unknown flag is refused", 1)[0]
            got_rules = text.split("## Rules\n\n", 1)[1].split("\n## Authority", 1)[0]
            got_codes = [int(c) for c in re.findall(r"^- `(\d+)` ", text.split("## Exit codes\n\n", 1)[1].split("\n## Rules", 1)[0], re.M) if int(c) < 10]
            got = (got_flags, got_rules, got_codes)
            want = ("".join(lines), "".join(rule_lines), self.codes(table, want_rules))
            if got != want:
                wrong.append("case %d (%r): %r != %r" % (i, table[:50], got, want))
        self.assertEqual(wrong[:3], [])


# What the constants were chosen from (Linux x86-64, callgrind, `cancho` 62c2be5; the per-call instruction
# counts the Cost test prints, at 400 entries = 45,677 table bytes):
#
#   linear code:  index, has, kind_of, default_of    0.5 - 0.8 instructions per table byte
#                 text, parse (one flag)              1.6 - 2.1
#                 nearest                             56  (a Levenshtein distance per entry, linear)
#                 introspect / skill                  60 / 18 (the JSON or Markdown it writes)
#   old code:     index, has, parse (one flag)        2,200 per table byte at 400 entries, and 7.1 times
#                 the cost at 150 entries where the table is 2.7 times as big (quadratic)
#
# BYTES_FACTOR is the linear code's dearest call (60) doubled; FIXED is the part of `introspect` that does
# not depend on the table (the rules catalogue: 199,540 at 10 entries, 1,097 bytes) rounded up.
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
