"""Operand counts: what `introspect` says each operand may be given, against
what the tool's parser accepts (docs/mcp.md §3).

A schema derived from `introspect` makes an operand required or optional, and
one or many, from `min` and `max`. Each tool checks its own count, with its
own message, so the counts in its table are a second statement of the same
fact; this gate is what keeps the two one. For every tool and every number of
operands from none to two past its bound, `args.missing-operand` appears
exactly when there are fewer than the operands' `min` add up to, and
`args.too-many-operands` exactly when there are more than their `max` (a
`null` is unbounded). Every operand is one the tool can use, so only the
count can raise either rule.
"""

import re
import unittest

from harness import TOOLS, Fixture, introspect, run

# Flags a call needs before its operands mean anything, and an operand that
# names something real for each position (the last is repeated).
SETUP = {
    "seek": ([], ["gamma", "plain.txt"]),
    "write": (["--create", "--stdin", "--dry-run"], ["fresh.txt"]),
    "replace": (["--old", "a", "--new", "b", "--dry-run"], ["plain.txt"]),
    "peek": ([], ["plain.txt"]),
    "jsonq": ([], ["doc.json"]),
    "tally": ([], ["words.txt"]),
    "hash": ([], ["plain.txt"]),
    "list": ([], ["sub"]),
    # Two operands, and a dry run so that none of the counts moves a file.
    "move": (["--dry-run"], ["plain.txt", "moved.txt"]),
}

# What a tool that reads standard input is given when it has no operand.
STDIN = {"jsonq": b"{}", "tally": b"a\n", "write": b"x"}

RULE = re.compile(rb'"rule":"(args\.(?:missing-operand|too-many-operands))"')


def bounds(tool):
    """The fewest and the most operands `introspect` allows, the most `None`
    when one of them is unbounded."""
    operands = introspect(tool)["operands"]
    least = sum(o["min"] for o in operands)
    most = None if any(o["max"] is None for o in operands) else sum(o["max"] for o in operands)
    return least, most


class OperandCounts(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_every_operand_has_a_count(self):
        for tool in TOOLS:
            for o in introspect(tool)["operands"]:
                self.assertIsInstance(o["min"], int, (tool, o))
                self.assertGreaterEqual(o["min"], 0, (tool, o))
                if o["max"] is not None:
                    self.assertGreaterEqual(o["max"], max(o["min"], 1), (tool, o))
                # A `NAME...` operand is the one that repeats.
                self.assertEqual(o["name"].endswith("..."), o["max"] is None, (tool, o))

    def test_the_counts_are_what_the_parser_accepts(self):
        disagreements = []
        for tool in TOOLS:
            flags, fill = SETUP[tool]
            least, most = bounds(tool)
            top = (most if most is not None else least) + 2
            for n in range(top + 1):
                operands = [fill[min(i, len(fill) - 1)] for i in range(n)]
                r = run(tool, "--root", self.fx.root, *flags, *operands,
                        stdin=STDIN.get(tool, b""), cwd=self.fx.root)
                raised = set(m.decode() for m in RULE.findall(r.stdout))
                expected = set()
                if n < least:
                    expected.add("args.missing-operand")
                if most is not None and n > most:
                    expected.add("args.too-many-operands")
                if raised != expected:
                    disagreements.append("%s with %d operand(s): raised %s, introspect says %s"
                                         % (tool, n, sorted(raised), sorted(expected)))
        self.assertEqual(disagreements, [])


if __name__ == "__main__":
    unittest.main()
