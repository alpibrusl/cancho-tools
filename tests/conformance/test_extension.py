"""A tool's own rules (`toolbox.rules`, "A tool's own rules, beside the shared catalogue").

`tests/extension/ext.ls` is a program that is built with the contract and has two rules of its
own, `demo.ragged` (exit 8, repairable sometimes) and `demo.other` (exit 3). The exit code, the
code name, `introspect` and `skill` must all take them from `extra_rules`, and the shared rules
must mean what they meant. Built here, not in `lex-sys.toml`: it is not one of the tools.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from harness import ROOT

COMPILER = os.environ.get("LEX_SYS", "lex-sys")


class Extension(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = pathlib.Path(cls.tmp.name) / "ext"
        sources = sorted(str(p) for p in (ROOT / "contract").glob("*.ls")) + [str(ROOT / "tests" / "extension" / "ext.ls")]
        b = subprocess.run([COMPILER, "build", *sources, "--std", "-o", str(cls.exe)], capture_output=True, text=True)
        if b.returncode != 0:
            raise AssertionError("ext.ls did not build:\n" + b.stdout + b.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_ext(self, *args):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True)

    def test_the_tools_own_rule_has_its_own_exit_and_code(self):
        p = self.run_ext()
        doc = json.loads(p.stdout)
        self.assertEqual(p.returncode, 8)
        self.assertEqual(doc["error"]["rule"], "demo.ragged")
        self.assertEqual(doc["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual([e["rule"] for e in doc["errors"]], ["demo.ragged", "path.empty"])
        self.assertEqual(doc["errors"][1]["code"], "INVALID_ARGS")

    def test_a_repeat_of_a_shared_tag_is_ignored(self):
        # `ext.ls` repeats `path.empty` in its own rules with exit 8; the shared meaning (2) wins.
        p = self.run_ext("shared")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)["error"]["rule"], "path.empty")
        rules = {r["rule"]: r for r in json.loads(self.run_ext("introspect").stdout)["rules"]}
        self.assertNotIn("path.empty", rules)

    def test_introspect_lists_it_with_its_summary_and_repairability(self):
        doc = json.loads(self.run_ext("introspect").stdout)
        rules = {r["rule"]: r for r in doc["rules"]}
        self.assertEqual((rules["demo.ragged"]["exit"], rules["demo.ragged"]["repairable"]), (8, "sometimes"))
        self.assertEqual(rules["demo.ragged"]["summary"], "a row with the wrong number of fields")
        self.assertEqual(rules["demo.other"]["exit"], 3)
        # A shared rule is what it was.
        self.assertEqual(rules["args.unknown-flag"]["exit"], 2)
        # And the exit codes the tool can end with include the ones its rules map to.
        codes = {c["code"] for c in doc["exit_codes"]} if "exit_codes" in doc else set()
        if codes:
            self.assertIn(8, codes)
            self.assertIn(3, codes)

    def test_the_skill_names_it(self):
        text = self.run_ext("skill").stdout
        self.assertIn("`demo.ragged` (exit 8): a row with the wrong number of fields", text)


if __name__ == "__main__":
    unittest.main()
