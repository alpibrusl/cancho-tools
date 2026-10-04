"""M8 -- `--root` holds lexically, every refusal is a tag and none a trap,
and the known limit is pinned: a symlink inside the root that points
outside it is followed (L6). When a no-follow primitive lands in lex-sys
(#227), `test_the_symlink_escape_is_known` flips and forces D9 and this
file to be rewritten."""

import json
import unittest

from harness import TOOLS, Fixture, run, validate

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

    def test_the_symlink_escape_is_known(self):
        """The limit D9 states, asserted so it cannot change silently: a link
        inside --root that points outside it is followed, and the outside
        file is read."""
        fx = Fixture()
        try:
            r = run("seek", "--root", str(fx.root), "outside", "link.txt", cwd=fx.dir)
            matches = [json.loads(l) for l in r.stdout.splitlines() if json.loads(l)["type"] == "match"]
            self.assertEqual([m["text"] for m in matches], ["secret gamma outside"])
            for tool in TOOLS:
                from harness import introspect
                self.assertEqual(introspect(tool)["confinement"], "lexical")
                self.assertIn("symlinks are followed", introspect(tool)["confinement_note"])
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
