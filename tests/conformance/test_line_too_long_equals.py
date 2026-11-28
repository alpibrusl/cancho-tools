"""`limit.line-too-long` repairs the `--max-line-bytes=N` spelling too (cancho-tools 70).

The retry repair used to be built only when the flag and its value were two arguments. With `--max-line-bytes=N` the
code fell to its last branch, which is for a line longer than the ceiling, and said so falsely. The repair must keep
the `=` form and name the longest line's length.

The fixture's long.txt has a longest line of 5006 bytes, under the ceiling, so a retry is the only honest answer.
"""

import unittest

from harness import Fixture, run


class LineTooLongEquals(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def limit_error(self, *flags):
        r = run("seek", "--root", self.fx.root, *flags, "gamma", "long.txt")
        errs = [e for e in r.errors() if e["rule"] == "limit.line-too-long"]
        self.assertEqual(len(errs), 1, r)
        return errs[0]

    def test_equals_form_gets_a_retry_in_the_same_form(self):
        rep = self.limit_error("--max-line-bytes=100")["repair"]
        self.assertEqual(rep["kind"], "retry", rep)
        self.assertIn("--max-line-bytes=5006", rep["argv"])
        self.assertNotIn("--max-line-bytes=100", rep["argv"])

    def test_spaced_form_still_gets_a_retry(self):
        rep = self.limit_error("--max-line-bytes", "100")["repair"]
        self.assertEqual(rep["kind"], "retry", rep)
        self.assertIn("5006", rep["argv"])
        self.assertNotIn("100", rep["argv"])


if __name__ == "__main__":
    unittest.main()
