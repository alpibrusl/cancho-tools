"""`list` pages the sorted listing (cancho-tools 67).

`list` used to ask the directory for its first `skip + max-entries + 1` names as the kernel gives them, and sort only those, so
`--max-entries` did not answer the first entries of the sorted listing, and paging with `next.skip` lost some entries and repeated
others. The listing is sorted and the same every time, and `--skip N` resumes it; these tests check that by running the built
binary on a directory whose files were created in an order that is not sorted.

What is checked:

* the first page of `--max-entries 5` is the first five names in sorted order, and `next` is `{"skip": 5}`;
* the pages of `--max-entries 7`, each resumed with `--skip` from the `next` of the one before, are the whole sorted listing,
  each entry once and in order.
"""

import os
import random
import shutil
import tempfile
import unittest

from harness import run

NAMES = ["f%02d" % i for i in range(40)]


def entries(r):
    return [l["path"] for l in r.lines() if l.get("type") == "entry"]


def end(r):
    return [l for l in r.lines() if l.get("type") == "end"][-1]


class ListPages(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="list-pages-")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(os.path.join(self.root, "many"))
        order = list(NAMES)
        random.Random(3).shuffle(order)
        for name in order:
            with open(os.path.join(self.root, "many", name), "w"):
                pass

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_first_page_is_the_first_entries_of_the_sorted_listing(self):
        r = run("list", "--root", self.root, "--max-entries", "5", "many")
        self.assertEqual(r.status, 0, r)
        self.assertEqual(entries(r), ["many/" + n for n in NAMES[:5]], r)
        e = end(r)
        self.assertTrue(e["truncated"], e)
        self.assertEqual(e["next"], {"skip": 5}, e)

    def test_pages_resumed_with_skip_are_the_whole_sorted_listing(self):
        got = []
        skip = 0
        pages = 0
        while True:
            args = ["list", "--root", self.root, "--max-entries", "7"]
            if skip:
                args += ["--skip", str(skip)]
            r = run(*args, "many")
            self.assertEqual(r.status, 0, r)
            got += entries(r)
            pages += 1
            self.assertLess(pages, 20, "the paging does not end")
            e = end(r)
            if not e["truncated"]:
                break
            skip = e["next"]["skip"]
        self.assertEqual(got, ["many/" + n for n in NAMES], got)


if __name__ == "__main__":
    unittest.main()
