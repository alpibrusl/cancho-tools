"""A tool's own rules (`toolbox.rules`, "A tool's own rules, beside the shared catalogue").

`tests/extension/ext.cho` is a program that is built with the contract and has two rules of its
own, `demo.ragged` (exit 8, repairable sometimes) and `demo.other` (exit 3). The exit code, the
code name, `introspect` and `skill` must all take them from `extra_rules`, and the shared rules
must mean what they meant. Built here, not in `cancho.toml`: it is not one of the tools.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from harness import ROOT

COMPILER = os.environ.get("CANCHO", "cancho")


class Extension(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = pathlib.Path(cls.tmp.name) / "ext"
        sources = sorted(str(p) for p in (ROOT / "contract").glob("*.cho")) + [str(ROOT / "tests" / "extension" / "ext.cho")]
        b = subprocess.run([COMPILER, "build", *sources, "--std", "-o", str(cls.exe)], capture_output=True, text=True)
        if b.returncode != 0:
            raise AssertionError("ext.cho did not build:\n" + b.stdout + b.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_ext(self, *args):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True)

    def run_raw(self, *args):
        return subprocess.run([str(self.exe), *args], capture_output=True)

    def error_schema(self):
        """The shared `error` definition every tool's schema carries, with its `repair` forms."""
        defs = json.loads((ROOT / "schemas" / "peek.v1.json").read_text())["$defs"]
        return {"$ref": "#/$defs/error", "$defs": defs}

    def test_the_tools_own_rule_has_its_own_exit_and_code(self):
        p = self.run_ext()
        doc = json.loads(p.stdout)
        self.assertEqual(p.returncode, 8)
        self.assertEqual(doc["error"]["rule"], "demo.ragged")
        self.assertEqual(doc["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual([e["rule"] for e in doc["errors"]], ["demo.ragged", "path.empty"])
        self.assertEqual(doc["errors"][1]["code"], "INVALID_ARGS")

    def test_a_repeat_of_a_shared_tag_is_ignored(self):
        # `ext.cho` repeats `path.empty` in its own rules with exit 8; the shared meaning (2) wins.
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

    # `toolbox.fail`'s choose-repair and detail helpers, and `toolbox.sort` (docs: README, "As a package").

    def test_choose_repair_is_the_schemas_shape(self):
        doc = json.loads(self.run_ext("choose", "two").stdout)
        err = doc["error"]
        self.assertEqual(err["repair"], {"kind": "choose", "options": [
            {"argv": ["one", "choose", "two"]}, {"argv": ["ext", 'two "quoted"']}]})
        self.assertEqual(err["detail"], {})
        try:
            import jsonschema
        except ImportError:
            return
        jsonschema.validate(err, self.error_schema())

    def test_detail_helpers_write_key_and_value_in_one_call(self):
        err = json.loads(self.run_ext("detail", "a/b").stdout)["error"]
        self.assertEqual(list(err["detail"].items()), [("name", "plain"), ("row", 3), ("negative", -7), ("ragged", True), ("path", "a/b")])
        self.assertIsNone(err["repair"])
        try:
            import jsonschema
        except ImportError:
            return
        jsonschema.validate(err, self.error_schema())

    def test_detail_text_writes_bytes_that_are_not_utf8_as_b64(self):
        p = self.run_raw("detail", b"\xff\xfe")
        err = json.loads(p.stdout)["error"]
        self.assertEqual(err["detail"]["path"], {"b64": "//4="})

    def test_detail_text_with_no_argument_is_the_empty_string(self):
        self.assertEqual(json.loads(self.run_ext("detail").stdout)["error"]["detail"]["path"], "")

    def order(self, *args):
        p = self.run_ext(*args)
        self.assertEqual(p.returncode, 0, p.stdout)
        return json.loads(p.stdout)["data"]["order"]

    def test_sort_by_keys_is_stable(self):
        self.assertEqual(self.order("sort", "3", "1", "2", "1", "3", "2"), [1, 3, 2, 5, 0, 4])

    def test_sort_by_keys_descending_is_stable(self):
        self.assertEqual(self.order("sort-desc", "3", "1", "2", "1", "3", "2"), [0, 4, 2, 5, 1, 3])

    def test_sort_by_a_comparator_with_a_context(self):
        self.assertEqual(self.order("sort-odd", "2", "3", "4", "5", "6"), [1, 3, 0, 2, 4])

    def test_sort_of_nothing_and_of_one(self):
        self.assertEqual(self.order("sort"), [])
        self.assertEqual(self.order("sort", "5"), [0])

    def test_sort_matches_python_on_many_keys(self):
        keys = [(i * 7919 + 13) % 97 for i in range(700)]
        want = sorted(range(len(keys)), key=lambda i: keys[i])
        self.assertEqual(self.order("sort", *map(str, keys)), want)
        want = sorted(range(len(keys)), key=lambda i: -keys[i])
        self.assertEqual(self.order("sort-desc", *map(str, keys)), want)


if __name__ == "__main__":
    unittest.main()
