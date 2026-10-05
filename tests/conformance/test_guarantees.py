"""The guarantees `introspect` claims are the ones the gates check.

Each tool's `introspect` carries a `guarantees` object (contract/describe.ls).
A guarantee is a promise an agent or a supervisor will act on, so none may be
claimed without the gate that tests it, and this file is where the two are
tied together:

* `deterministic` -- the tool is in M2's corpus (test_determinism);
* `bounded_memory` -- the tool is one M9 measures (test_memory.MEASURED);
* `atomic`, `dry_run` -- the tool is one M7 races, interrupts and straces
  (test_mutation.WRITERS);
* `requires_precondition` -- refusing to write without a stated belief is
  reached by an M3 fixture, and checked here directly;
* `idempotent` -- for a writer M7 applies everything twice; for a reader it is
  checked here: a second run changes nothing and prints the same bytes.
"""

import os
import unittest

from harness import TOOLS, Fixture, introspect, run
from test_memory import MEASURED
from test_mutation import MOVERS, WRITERS
from test_schema import corpus

KEYS = ["deterministic", "idempotent", "atomic", "requires_precondition", "dry_run", "bounded_memory"]


def tree(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                with open(p, "rb") as f:
                    out[p] = f.read()
    return out


class Guarantees(unittest.TestCase):
    def setUp(self):
        self.g = {tool: introspect(tool)["guarantees"] for tool in TOOLS}

    def test_every_tool_reports_every_guarantee(self):
        for tool, g in self.g.items():
            self.assertEqual(list(g), KEYS + ["concurrency"], tool)
            for key in KEYS:
                self.assertIsInstance(g[key], bool, (tool, key))

    def test_each_claim_has_its_gate(self):
        fx = Fixture()
        try:
            in_m2 = {tool for tool, _, _ in corpus(fx)}
        finally:
            fx.cleanup()
        in_m9 = {tool for tool, _ in MEASURED}
        for tool, g in self.g.items():
            if g["deterministic"]:
                self.assertIn(tool, in_m2, "%s claims deterministic, and M2 does not run it" % tool)
            self.assertEqual(g["bounded_memory"], tool in in_m9, "%s: bounded_memory and M9 disagree" % tool)
            for key in ("atomic", "dry_run"):
                self.assertEqual(g[key], tool in WRITERS | MOVERS, "%s: %s and M7 disagree" % (tool, key))
            self.assertEqual(g["requires_precondition"], tool in WRITERS, tool)
            self.assertEqual(g["concurrency"].startswith("locked"), tool in WRITERS | MOVERS, tool)

    def test_a_writer_refuses_without_a_stated_belief(self):
        fx = Fixture()
        try:
            r = run("write", "--root", str(fx.root), "--stdin", "plain.txt", stdin=b"x", cwd=fx.dir)
            self.assertEqual(r.first_rule(), "precondition.required")
            # replace's belief is --expect (default 1): two occurrences refuse.
            r = run("replace", "--root", str(fx.root), "--old", "a", "--new", "b", "plain.txt", cwd=fx.dir)
            self.assertEqual(r.first_rule(), "precondition.count-mismatch")
            self.assertEqual((fx.root / "plain.txt").read_bytes(), b"alpha\nbeta gamma\nGAMMA delta\nepsilon\n")
        finally:
            fx.cleanup()

    def test_a_reader_run_twice_changes_nothing_and_says_the_same(self):
        fx = Fixture()
        try:
            before = tree(fx.root)
            for tool, args, stdin in corpus(fx):
                if tool in WRITERS or tool in MOVERS or not self.g[tool]["idempotent"]:
                    continue
                first = run(tool, *args, stdin=stdin, cwd=fx.root)
                second = run(tool, *args, stdin=stdin, cwd=fx.root)
                self.assertEqual((first.status, first.stdout), (second.status, second.stdout), (tool, args))
            self.assertEqual(tree(fx.root), before)
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
