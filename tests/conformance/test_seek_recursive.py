"""`seek --recursive`: a directory operand is walked, and the walk is the one a careful person would write.

* R1, differential: for random trees (nested directories, empty files, binary files, names that sort oddly)
  the matches are those of an independent walk in Python -- depth first, names in bytewise order -- with the
  same path, line number, offset and text;
* R2, nothing is followed: a link to a file or a directory is skipped and counted, and what it points at is
  never read (the secret in the target never appears);
* R3, bounds: `--max-files` ends the walk with `limit.too-many-files`, `--max-count` and `--skip` resume across
  files, a tree deeper than 64 levels is skipped and counted, and without `--recursive` a directory is still
  `io.is-a-directory`;
* R4, bounded output: a file record is written only for a file with a match, so a tree of thousands of files
  with one match is a few records, not thousands.
"""

import json
import os
import random
import subprocess
import unittest

from harness import Fixture, binary


def seek(fx, *args):
    p = subprocess.run([binary("seek"), "--root", str(fx.root), *args], capture_output=True, cwd=fx.dir)
    recs = [json.loads(l) for l in p.stdout.splitlines() if l]
    return p.returncode, recs


def oracle(root, rel, needle):
    """Matches of `needle` under root/rel: depth first, names bytewise, no link followed."""
    out = []
    skipped = 0

    def walk(rel_dir):
        nonlocal skipped
        full = os.path.join(root, rel_dir)
        for name in sorted(os.listdir(full), key=lambda n: n.encode()):
            r = name if rel_dir == "." else rel_dir + "/" + name
            p = os.path.join(root, r)
            if os.path.islink(p):
                skipped += 1
            elif os.path.isdir(p):
                walk(r)
            elif os.path.isfile(p):
                data = open(p, "rb").read()
                offset = 0
                for n, line in enumerate(data.split(b"\n"), 1):
                    if n == len(data.split(b"\n")) and line == b"":
                        break
                    if needle in line:
                        out.append((r, n, offset, line.decode("utf-8", "replace")))
                    offset += len(line) + 1
            else:
                skipped += 1

    walk(rel)
    return out, skipped


def tree(root, rng, depth=0):
    names = set()
    for _ in range(rng.randint(2, 6)):
        names.add(rng.choice(["a", "b", "B", "z", "-x", "m n", "ü", "file.txt", "zz", "10", "9"]) + rng.choice(["", ".txt", ".bin"]))
    for name in sorted(names):
        p = os.path.join(root, name)
        if depth < 3 and rng.random() < 0.35 and not os.path.exists(p):
            os.mkdir(p)
            tree(p, rng, depth + 1)
        elif not os.path.exists(p):
            lines = []
            for _ in range(rng.randint(0, 8)):
                lines.append(rng.choice(["needle here", "nothing", "a needle in it", "", "NEEDLE", "xneedlex"]))
            body = "\n".join(lines) + ("\n" if lines and rng.random() < 0.8 else "")
            data = body.encode()
            if name.endswith(".bin"):
                data = b"\x00\x01needle\x02" + data
            open(p, "wb").write(data)


class Differential(unittest.TestCase):
    """R1."""

    def test_the_matches_are_those_of_an_independent_walk(self):
        rng = random.Random(20251102)
        for case in range(60):
            fx = Fixture()
            try:
                top = fx.root / "t"
                top.mkdir()
                tree(str(top), rng)
                want, _ = oracle(str(fx.root), "t", b"needle")
                rc, recs = seek(fx, "--recursive", "needle", "t")
                self.assertEqual(rc, 0, recs[-1])
                got = [(r["path"], r["line"], r["offset"], r["text"]) for r in recs if r["type"] == "match"]
                with self.subTest(case=case):
                    # a binary file is searched like any other: compare the text-only ones exactly, the rest by path
                    self.assertEqual([g for g in got if not g[0].endswith(".bin")], [w for w in want if not w[0].endswith(".bin")])
                    self.assertEqual(sorted({g[0] for g in got}), sorted({w[0] for w in want}))
                    self.assertEqual(recs[-1]["type"], "end")
                    self.assertEqual(recs[-1]["matches"], len(got))
            finally:
                fx.cleanup()

    def test_the_same_tree_gives_the_same_bytes(self):
        fx = Fixture()
        (fx.root / "t").mkdir()
        tree(str(fx.root / "t"), random.Random(5))
        a = subprocess.run([binary("seek"), "--root", str(fx.root), "-r", "needle", "t"], capture_output=True).stdout
        b = subprocess.run([binary("seek"), "--root", str(fx.root), "-r", "needle", "t"], capture_output=True).stdout
        self.assertEqual(a, b)
        fx.cleanup()


class NothingIsFollowed(unittest.TestCase):
    """R2."""

    SECRET = "SECRET-TOKEN-xyz"

    def test_links_are_skipped_counted_and_never_read(self):
        fx = Fixture()
        out = fx.root.parent / (fx.root.name + "-outside")
        out.mkdir()
        (out / "keys.txt").write_text("needle %s\n" % self.SECRET)
        (out / "deep").mkdir()
        (out / "deep" / "more.txt").write_text("needle %s\n" % self.SECRET)
        t = fx.root / "t"
        t.mkdir()
        (t / "real.txt").write_text("needle real\n")
        (t / "dirlink").symlink_to(out)
        (t / "filelink.txt").symlink_to(out / "keys.txt")
        (t / "dangling").symlink_to("/nonexistent/x")
        rc, recs = seek(fx, "-r", "needle", "t")
        raw = json.dumps(recs)
        self.assertEqual(rc, 0, recs[-1])
        self.assertNotIn(self.SECRET, raw)
        self.assertEqual([r["path"] for r in recs if r["type"] == "match"], ["t/real.txt"])
        self.assertEqual(recs[-1]["skipped"], 3)
        fx.cleanup()

    def test_a_link_given_as_the_operand_is_refused_as_before(self):
        fx = Fixture()
        (fx.root / "real.txt").write_text("needle\n")
        (fx.root / "ln").symlink_to("real.txt")
        rc, recs = seek(fx, "-r", "needle", "ln")
        self.assertNotEqual(rc, 0)
        self.assertEqual([r for r in recs if r["type"] == "error"][0]["error"]["rule"], "path.symlink")
        fx.cleanup()


class Bounds(unittest.TestCase):
    """R3, R4."""

    def make(self, n):
        fx = Fixture()
        t = fx.root / "t"
        t.mkdir()
        for i in range(n):
            (t / ("f%04d.txt" % i)).write_text("needle %d\nother\n" % i)
        return fx

    def test_max_files_ends_the_walk_with_its_rule(self):
        fx = self.make(30)
        rc, recs = seek(fx, "-r", "--max-files", "10", "needle", "t")
        err = [r for r in recs if r["type"] == "error"]
        self.assertEqual(rc, 8)
        self.assertEqual(err[0]["error"]["rule"], "limit.too-many-files")
        self.assertEqual(recs[-1]["files"], 10)
        self.assertEqual(sum(r["type"] == "match" for r in recs), 10)
        fx.cleanup()

    def test_max_count_and_skip_resume_across_files(self):
        fx = self.make(12)
        seen = []
        skip = 0
        for _ in range(10):
            rc, recs = seek(fx, "-r", "--max-count", "5", "--skip", str(skip), "needle", "t")
            seen += [r["path"] for r in recs if r["type"] == "match"]
            end = recs[-1]
            if not end["truncated"]:
                break
            skip = end["next"]["skip"]
        self.assertEqual(seen, ["t/f%04d.txt" % i for i in range(12)])
        fx.cleanup()

    def test_a_tree_deeper_than_64_levels_is_skipped_and_counted(self):
        fx = Fixture()
        p = fx.root / "t"
        p.mkdir()
        (p / "top.txt").write_text("needle top\n")
        deep = p
        for i in range(70):
            deep = deep / "d"
            try:
                deep.mkdir()
            except OSError:
                self.skipTest("path too long on this filesystem")
        (deep / "bottom.txt").write_text("needle bottom\n")
        rc, recs = seek(fx, "-r", "needle", "t")
        paths = [r["path"] for r in recs if r["type"] == "match"]
        self.assertEqual(paths, ["t/top.txt"])
        self.assertGreaterEqual(recs[-1]["skipped"], 1)
        fx.cleanup()

    def test_without_recursive_a_directory_is_still_refused(self):
        fx = self.make(3)
        rc, recs = seek(fx, "needle", "t")
        self.assertEqual([r for r in recs if r["type"] == "error"][0]["error"]["rule"], "io.is-a-directory")
        self.assertNotIn("skipped", recs[-1])
        fx.cleanup()

    def test_only_a_file_with_a_match_gets_a_file_record(self):
        fx = Fixture()
        t = fx.root / "t"
        t.mkdir()
        for i in range(200):
            (t / ("n%03d.txt" % i)).write_text("nothing here\n")
        (t / "one.txt").write_text("needle\n")
        rc, recs = seek(fx, "-r", "needle", "t")
        self.assertEqual([r["type"] for r in recs], ["match", "file", "end"])
        self.assertEqual(recs[-1]["files"], 201)
        fx.cleanup()

    def test_a_file_operand_with_recursive_is_searched_as_a_file(self):
        fx = Fixture()
        (fx.root / "f.txt").write_text("a needle\n")
        rc, recs = seek(fx, "-r", "needle", "f.txt")
        self.assertEqual(rc, 0)
        self.assertEqual(recs[0]["path"], "f.txt")
        fx.cleanup()


if __name__ == "__main__":
    unittest.main()
