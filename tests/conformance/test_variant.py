"""D14 -- a variant built with its root baked in reports that directory in its
authority, and still answers every path case with a tag, never a trap.

The variant's `Fs` is narrowed, so the builtins trap on any path outside the
prefix, on a relative path and on `..` (lex-sys docs/agent-toolbox.md A.4).
That makes D9's in-tool validation load-bearing here: this test is the M8
case list run against a variant, where a hole in the validation would be a
dead process. The transform needs the compiler (LEX_SYS or lex-sys on PATH).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from harness import ROOT, Fixture, TRAPS
from test_confinement import CASES

sys.path.insert(0, str(ROOT / "scripts"))
import variant  # noqa: E402


@unittest.skipUnless(shutil.which(os.environ.get("LEX_SYS", "lex-sys")), "the variant transform needs the compiler")
class Variant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        cls.out = tempfile.mkdtemp(prefix="variant-")
        cls.root = str(cls.fx.root)
        cls.seek, cls.seek_authority = variant.build("seek", cls.root, os.path.join(cls.out, "seek"))
        cls.write, cls.write_authority = variant.build("write", cls.root, os.path.join(cls.out, "write"))

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_authority_names_the_directory(self):
        labels = {(l["name"], l["argument"]) for l in self.seek_authority["labels"]}
        self.assertIn(("fs_read", self.root), labels)
        self.assertNotIn(("fs_read", ""), labels)
        labels = {(l["name"], l["argument"]) for l in self.write_authority["labels"]}
        self.assertIn(("fs_write", self.root), labels)
        embedded = json.loads(subprocess.run([str(self.seek), "introspect"], capture_output=True).stdout)["authority"]
        self.assertEqual(embedded, self.seek_authority)

    def test_no_path_case_traps_the_variant(self):
        failures = []
        for operand, rule in CASES:
            op = operand.replace("ROOT", self.root)
            p = subprocess.run([str(self.seek), "gamma", op], capture_output=True, cwd=self.fx.dir)
            if p.returncode in TRAPS or p.returncode < 0:
                failures.append((operand[:30], p.returncode))
                continue
            records = [json.loads(l) for l in p.stdout.splitlines()]
            self.assertEqual(records[-1]["type"], "end")
        self.assertEqual(failures, [])

    def test_root_is_fixed(self):
        p = subprocess.run([str(self.seek), "--root", "/etc", "x", "passwd"], capture_output=True)
        self.assertEqual(json.loads(p.stdout.splitlines()[0])["error"]["rule"], "path.outside-root")
        p = subprocess.run([str(self.seek), "--root", self.root, "gamma", "plain.txt"], capture_output=True)
        self.assertEqual(p.returncode, 0)

    def test_the_write_variant_writes_inside_and_nowhere_else(self):
        p = subprocess.run([str(self.write), "--create", "--stdin", "made.txt"], input=b"v\n", capture_output=True, cwd="/")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual((self.fx.root / "made.txt").read_bytes(), b"v\n")
        p = subprocess.run([str(self.write), "--create", "--stdin", str(self.fx.dir / "outside" / "x")], input=b"v\n", capture_output=True)
        self.assertEqual(json.loads(p.stdout)["error"]["rule"], "path.outside-root")
        self.assertFalse((self.fx.dir / "outside" / "x").exists())


if __name__ == "__main__":
    unittest.main()
