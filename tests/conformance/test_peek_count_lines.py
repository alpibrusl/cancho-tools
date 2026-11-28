"""`peek --count-lines` changes only `total_lines` (cancho-tools 68).

With `--count-lines` the reader used to go on to the end of the file to count, and answered `eof: true` and
`next: null` even when the range asked for stopped before the end. The flag must add `total_lines` and nothing else:
`eof` and `next` are the same with and without it.

Checked on a five-line file, by running the built binary:

* `--lines 2:3` answers `eof: false` and `next: {"line": 4}` with and without `--count-lines`, and `total_lines` is 5;
* `--lines 1:5 --max-bytes 3` is truncated, so `eof` is false, with `next: {"line": 4}`, with `--count-lines` too.
"""

import os
import shutil
import tempfile
import unittest

from harness import run

FIVE = b"a\nb\nc\nd\ne\n"


class PeekCountLines(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="peek-count-")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "five.txt"), "wb") as f:
            f.write(FIVE)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def peek(self, *args):
        r = run("peek", "--root", self.root, *args, "five.txt")
        self.assertEqual(r.status, 0, r)
        return r.doc()["data"]

    def test_count_lines_does_not_change_eof_or_next(self):
        plain = self.peek("--lines", "2:3")
        counted = self.peek("--lines", "2:3", "--count-lines")
        self.assertEqual(counted["eof"], plain["eof"])
        self.assertEqual(counted["next"], plain["next"])
        self.assertFalse(counted["eof"])
        self.assertEqual(counted["next"], {"line": 4})
        self.assertEqual(counted["total_lines"], 5)

    def test_truncated_range_is_not_eof_with_count_lines(self):
        c = self.peek("--lines", "1:5", "--max-bytes", "3", "--count-lines")
        self.assertTrue(c["truncated"])
        self.assertFalse(c["eof"])
        self.assertEqual(c["next"], {"line": 4})
        self.assertEqual(c["total_lines"], 5)


if __name__ == "__main__":
    unittest.main()
