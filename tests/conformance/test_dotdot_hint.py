"""`path.dotdot` says what to do instead (cancho-tools#62, cancho-table#107).

A path with a `..` component is refused, and the hint used to say why (a symlink makes `a/link/../b` something other than
`a/b`) and not what to do. It names the two ways out that exist: run the tool in the directory that holds the file, or give
`--root DIR` and name the file relative to it.

What is checked, by running the built binaries, on every tool that takes a path (the operands of `test_rules.WITH_PATH`):

* the refusal is still `path.dotdot`, exit status 2, with the message it had;
* the hint names `--root DIR`, and the tool has a `--root` flag (a hint must not send a person to a flag that is not there);
* the reason is still in the hint.
"""

import json
import os
import shutil
import tempfile
import unittest

from harness import introspect, run
from test_rules import WITH_PATH

WHY = "a symlink makes `a/link/../b` something other than `a/b`"
WAYS = "run the tool in the directory that holds it, or give --root DIR and name the file relative to DIR"


class DotdotHint(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dotdot-")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(os.path.join(self.root, "sub"))
        for name in ("plain.txt", "doc.json", "words.txt"):
            with open(os.path.join(self.root, name), "w") as f:
                f.write("alpha\n" if not name.endswith(".json") else "{}\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_every_tool_that_takes_a_path_says_what_to_do(self):
        for tool, base in WITH_PATH.items():
            with self.subTest(tool=tool):
                args = [a.replace("X", "sub/../plain.txt") for a in base]
                r = run(tool, "--root", self.root, *args)
                err = json.loads(r.stdout.decode().splitlines()[0])["error"]       # a streaming tool writes more than one line
                self.assertEqual((r.status, err["rule"]), (2, "path.dotdot"), r)
                self.assertEqual(err["message"], "the path has a `..` component")
                self.assertIn(WAYS, err["hint"])
                self.assertIn(WHY, err["hint"])
                names = {f["name"] for f in introspect(tool)["flags"]}
                self.assertIn("--root", names, "the hint names --root and the tool has none")


if __name__ == "__main__":
    unittest.main()
