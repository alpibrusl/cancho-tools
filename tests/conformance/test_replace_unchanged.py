"""`replace` answers changed: false, and writes nothing, when the replacement leaves the file as it is (cancho-tools 69).

The replacement used to write the file and answer changed: true whenever it found its text, even when the new text was
the old text, or the text was not there to change. `docs/reference.md` says a file that already holds the new content
gets changed: false, and `write` does that. What is checked, by running the built binary on a real file:

* the replacement that changes nothing answers changed false, exit 0, before_sha256 equal to after_sha256, an empty diff;
* the file is not written: its bytes and its modification time are as they were.
"""

import os
import unittest

from harness import Fixture, run


class ReplaceUnchanged(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.path = self.fx.path("plain.txt")
        self.before = self.path.read_bytes()

    def tearDown(self):
        self.fx.cleanup()

    def check_unchanged(self, *flags):
        mtime = os.stat(self.path).st_mtime_ns
        r = run("replace", "--root", self.fx.root, *flags, "plain.txt")
        self.assertEqual(r.status, 0, r)
        d = r.doc()["data"]
        self.assertIs(d["changed"], False, d)
        self.assertEqual(d["before_sha256"], d["after_sha256"], d)
        self.assertEqual(d["diff"], [], d)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(os.stat(self.path).st_mtime_ns, mtime, "the file was written")

    def test_new_text_equal_to_old_is_not_a_change(self):
        self.check_unchanged("--old", "alpha", "--new", "alpha")

    def test_zero_occurrences_expected_zero_is_not_a_change(self):
        self.check_unchanged("--old", "no such text", "--new", "x", "--expect", "0")


if __name__ == "__main__":
    unittest.main()
