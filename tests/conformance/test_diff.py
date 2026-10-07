"""The diff `write` and `replace` answer with is the change they make (issue #8).

`contract/diff.cho` answers one hunk: the lines between the common leading and
trailing lines of the old and new content. The claim an agent relies on is
that the hunk is exact -- keeping the first `old_start - 1` old lines, then
`added`, then the old lines after the `removed_count` removed gives the new
content byte for byte -- and that it is the same in `data` and in a dry run's
`planned_actions`. Checked here on random edits of random line-shaped content
(with and without a last newline, with blank and repeated lines, with bytes
that are not UTF-8), through both tools, dry and real.

When the diff is cut (`--max-diff-lines`), the counts stay whole and the lines
shown are a prefix of the removed lines, then of the added ones.
"""

import base64
import unittest

from harness import Fixture, rng, run, sha256, validate

ALPHABET = [b"a", b"b", b"", b"x y", b"caf\xe9", b"a"]


def lines_of(data):
    return data.splitlines(keepends=True)


def text(value):
    if isinstance(value, dict):
        return base64.b64decode(value["b64"])
    return value.encode()


def content(r):
    body = b"".join(r.choice(ALPHABET) + b"\n" for _ in range(r.randrange(0, 8)))
    if r.random() < 0.3:
        body += r.choice(ALPHABET)
    return body


def edit(r, before):
    lines = lines_of(before)
    i = r.randrange(0, len(lines) + 1)
    j = r.randrange(i, min(len(lines), i + 3) + 1)
    new = [r.choice(ALPHABET) + b"\n" for _ in range(r.randrange(0, 3))]
    after = b"".join(lines[:i] + new + lines[j:])
    if r.random() < 0.2:
        after += b"tail"
    return after


def apply(before, diff):
    if not diff:
        return before
    (h,) = diff
    old = lines_of(before)
    k = h["old_start"] - 1
    return b"".join(old[:k] + [text(x) for x in h["added"]] + old[k + h["removed_count"]:])


class Diff(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.f = self.fx.path("edit.txt")

    def tearDown(self):
        self.fx.cleanup()

    def check(self, result, before, after):
        self.assertEqual(validate(result), [], result)
        doc = result.doc()
        data = doc["data"]
        self.assertFalse(data["diff_truncated"], result)
        self.assertEqual(apply(before, data["diff"]), after, (before, after, data["diff"]))
        if before == after:
            self.assertEqual(data["diff"], [])
        else:
            (h,) = data["diff"]
            self.assertEqual(h["old_start"], h["new_start"])
            self.assertEqual(len(h["removed"]), h["removed_count"])
            self.assertEqual(len(h["added"]), h["added_count"])
        if "planned_actions" in doc:
            for action in doc["planned_actions"]:
                self.assertEqual(action["diff"], data["diff"])

    def test_write_diff_reproduces_the_new_content(self):
        r = rng(8)
        for case in range(150):
            before = content(r)
            after = edit(r, before)
            self.f.write_bytes(before)
            dry = case % 2 == 0
            args = ["--root", str(self.fx.root), "--if-sha256", sha256(before), "--stdin", "edit.txt"]
            result = run("write", *(["--dry-run"] if dry else []), *args, stdin=after)
            self.assertEqual(result.status, 9 if dry else 0, result)
            self.check(result, before, after)
            self.assertEqual(self.f.read_bytes(), before if dry else after)

    def test_create_diff_is_every_line_added(self):
        after = b"one\ntwo\nthree"
        result = run("write", "--root", str(self.fx.root), "--create", "--stdin", "new.txt", stdin=after)
        self.assertEqual(result.status, 0, result)
        self.check(result, b"", after)
        (h,) = result.doc()["data"]["diff"]
        self.assertEqual((h["old_start"], h["removed_count"], h["added_count"]), (1, 0, 3))

    def test_replace_diff_reproduces_the_new_content(self):
        r = rng(9)
        done = 0
        for case in range(300):
            before = content(r)
            old = r.choice([b"a", b"b", b"x y", b"a\nb", b"\n"])
            new = r.choice([b"", b"Q", b"Q\nR", b"\n\n"])
            found = before.count(old)
            if found == 0 or old == new or new.count(old) or before.replace(old, new).count(old):
                continue
            self.f.write_bytes(before)
            dry = case % 2 == 0
            result = run("replace", "--root", str(self.fx.root), *(["--dry-run"] if dry else []), "--old", old.decode(),
                         "--new", new.decode(), "--expect", str(found), "edit.txt")
            self.assertEqual(result.status, 9 if dry else 0, result)
            self.check(result, before, before.replace(old, new))
            done += 1
        self.assertGreater(done, 50)

    def test_already_applied_is_an_empty_diff(self):
        self.f.write_bytes(b"x\n")
        result = run("write", "--root", str(self.fx.root), "--if-sha256", sha256(b"x\n"), "--stdin", "edit.txt", stdin=b"x\n")
        self.assertEqual(result.status, 0, result)
        self.assertEqual(result.doc()["data"]["diff"], [])

    def test_a_cut_diff_keeps_whole_counts(self):
        before = b"".join(b"%d\n" % i for i in range(10))
        after = b"".join(b"%d\n" % (i + 100) for i in range(10))
        self.f.write_bytes(before)
        for most, removed, added in [(0, 0, 0), (4, 4, 0), (10, 10, 0), (13, 10, 3), (20, 10, 10)]:
            result = run("write", "--root", str(self.fx.root), "--dry-run", "--max-diff-lines", str(most),
                         "--if-sha256", sha256(before), "--stdin", "edit.txt", stdin=after)
            self.assertEqual(validate(result), [], result)
            data = result.doc()["data"]
            (h,) = data["diff"]
            self.assertEqual((h["removed_count"], h["added_count"]), (10, 10))
            self.assertEqual([text(x) for x in h["removed"]], lines_of(before)[:removed])
            self.assertEqual([text(x) for x in h["added"]], lines_of(after)[:added])
            self.assertEqual(data["diff_truncated"], most < 20, most)

    def test_an_old_file_past_max_bytes_has_no_diff(self):
        before = b"0123456789\n" * 4
        self.f.write_bytes(before)
        result = run("write", "--root", str(self.fx.root), "--max-bytes", "20", "--if-sha256", sha256(before),
                     "--stdin", "edit.txt", stdin=b"short\n")
        self.assertEqual(result.status, 0, result)
        self.assertEqual(validate(result), [], result)
        data = result.doc()["data"]
        self.assertIsNone(data["diff"])
        self.assertTrue(data["diff_truncated"])
        self.assertEqual(data["before_sha256"], sha256(before))
        self.assertEqual(self.f.read_bytes(), b"short\n")


if __name__ == "__main__":
    unittest.main()
