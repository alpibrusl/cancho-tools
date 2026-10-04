"""M8 -- `--root` holds, every refusal is a tag and none a trap, and a
symlink inside the root cannot reach outside it: a path is checked
lexically (`toolbox.path`) and then opened beneath the root one component
at a time, following no link (`toolbox.place`, lex-sys #227). Until #227
this file asserted the escape; the test that did is now the one that
asserts the refusal."""

import json
import unittest

from harness import TOOLS, Fixture, introspect, run, validate

CASES = [
    # (operand, rule or None for success)
    ("plain.txt", None),
    ("./plain.txt", None),
    ("sub//inner.txt", None),
    ("sub/./inner.txt", None),
    ("..", "path.dotdot"),
    ("../outside/secret.txt", "path.dotdot"),
    ("sub/../../outside/secret.txt", "path.dotdot"),
    ("a//../plain.txt", "path.dotdot"),
    ("", "path.empty"),
    ("x" * 4097, "path.too-long"),
    ("/etc/passwd", "path.outside-root"),
    ("ROOTevil/x", "path.outside-root"),
    ("ROOT/plain.txt", "path.absolute"),
    ("ROOT//plain.txt", "path.absolute"),
    ("ROOT/sub/", "io.is-a-directory"),
    ("ÿé日.txt", "io.not-found"),
    ("link.txt", "path.symlink"),
    ("dirlink/secret.txt", "path.symlink"),
    ("sub/../link.txt", "path.dotdot"),
]


class Confinement(unittest.TestCase):
    def test_every_case_is_a_tag_or_a_success_never_a_trap(self):
        failures = []
        for tool in ("seek", "peek", "hash", "tally", "jsonq"):
            for operand, rule in CASES:
                fx = Fixture()
                try:
                    root = str(fx.root)
                    op = operand.replace("ROOT", root)
                    if rule == "io.is-a-directory" or tool == "jsonq" and rule is None:
                        continue
                    args = {"seek": ["--root", root, "gamma", op], "jsonq": ["--root", root, op]}.get(tool, ["--root", root, op])
                    r = run(tool, *args, cwd=fx.dir)
                    problems = validate(r)
                    if problems:
                        failures.append((tool, operand[:40], problems))
                    got = r.first_rule()
                    if got != rule:
                        failures.append((tool, operand[:40], "expected %s, got %s" % (rule, got)))
                finally:
                    fx.cleanup()
        self.assertEqual(failures, [])

    def test_the_absolute_repair_is_inside_the_root(self):
        fx = Fixture()
        try:
            r = run("peek", "--root", str(fx.root), str(fx.root / "sub" / "inner.txt"), cwd=fx.dir)
            repair = r.doc()["error"]["repair"]
            self.assertEqual(repair["argv"][-1], "sub/inner.txt")
            again = run("peek", *repair["argv"][1:], cwd=fx.dir)
            self.assertEqual(again.status, 0)
        finally:
            fx.cleanup()

    def test_no_link_reaches_outside_the_root(self):
        """The escape D9 used to state, now refused by every tool: a link to a
        file and a link to a directory, both pointing outside --root, are
        `path.symlink`, and nothing outside is read or written."""
        fx = Fixture()
        try:
            outside = fx.dir / "outside"
            before = sorted(p.name for p in outside.iterdir())
            calls = []
            for op in ("link.txt", "dirlink/secret.txt"):
                calls += [
                    ("seek", ["outside", op]),
                    ("peek", [op]),
                    ("hash", [op]),
                    ("tally", [op]),
                    ("jsonq", [op]),
                    ("write", ["--if-sha256", "0" * 64, "--stdin", op]),
                    ("write", ["--create", "--stdin", op]),
                    ("replace", ["--old", "secret", "--new", "public", op]),
                ]
            calls.append(("write", ["--create", "--stdin", "dirlink/new.txt"]))
            calls.append(("write", ["--create", "--content-file", "link.txt", "fresh.txt"]))
            for tool, args in calls:
                r = run(tool, "--root", str(fx.root), *args, stdin=b"new\n", cwd=fx.dir)
                self.assertEqual(r.first_rule(), "path.symlink", (tool, args, r.stdout[:300]))
                self.assertEqual(r.status, 4, (tool, args))
                self.assertNotIn(b"secret gamma outside", r.stdout)
            self.assertEqual(sorted(p.name for p in outside.iterdir()), before)
            self.assertEqual((outside / "secret.txt").read_bytes(), b"secret gamma outside\n")
            for tool in TOOLS:
                self.assertEqual(introspect(tool)["confinement"], "beneath")
                self.assertIn("following no symbolic link", introspect(tool)["confinement_note"])
        finally:
            fx.cleanup()

    def test_a_link_in_the_root_spelling_is_followed(self):
        """--root itself is the trust anchor: the caller named it, so a link
        there is followed, and what is beneath it is confined."""
        fx = Fixture()
        try:
            alias = fx.dir / "alias"
            alias.symlink_to(fx.root)
            r = run("seek", "--root", str(alias), "gamma", "sub/inner.txt", cwd=fx.dir)
            self.assertEqual(r.status, 0, r.stdout)
            r = run("seek", "--root", str(alias), "gamma", "link.txt", cwd=fx.dir)
            self.assertEqual(r.first_rule(), "path.symlink")
        finally:
            fx.cleanup()

    def test_without_a_root_a_reader_follows_links(self):
        """No --root, no confinement to keep: a reader opens the path as
        given, as `grep` would."""
        fx = Fixture()
        try:
            r = run("seek", "outside", "root/link.txt", cwd=fx.dir)
            matches = [json.loads(l) for l in r.stdout.splitlines() if json.loads(l)["type"] == "match"]
            self.assertEqual([m["text"] for m in matches], ["secret gamma outside"])
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
