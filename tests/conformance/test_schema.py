"""M1 -- every output validates against its versioned schema, every exit code
is in the tool's declared table, and introspect describes the tool."""

import json
import unittest

from harness import TOOLS, Fixture, introspect, run, sha256, validate


def corpus(fx):
    """Invocations per tool: successes, every shape of output, and errors."""
    r = str(fx.root)
    out = []
    for args in [
        ["gamma", "plain.txt"], ["--root", r, "gamma", "plain.txt", "crlf.txt", "no-newline.txt"],
        ["--root", r, "gamma", "nul.bin", "latin1.txt", "empty.txt"], ["--root", r, "-i", "gamma", "plain.txt"],
        ["--root", r, "-m", "1", "gamma", "plain.txt", "crlf.txt"], ["--root", r, "--skip", "1", "gamma", "plain.txt", "crlf.txt"],
        ["--root", r, "--max-line-bytes", "100", "gamma", "long.txt"], ["--root", r, "zzz", "plain.txt"],
        ["--root", r, "--require-match", "zzz", "plain.txt"], ["--root", r, "gamma", "missing.txt", "sub", "../x", "/etc/passwd"],
        ["--bogus", "gamma"], ["gamma"], [],
    ]:
        out.append(("seek", args, b""))
    for args in [["--root", r, "plain.txt"], ["--root", r, "--lines", "2:3", "crlf.txt"], ["--root", r, "--lines", "2:", "--count-lines", "no-newline.txt"],
                 ["--root", r, "--bytes", "0:4", "nul.bin"], ["--root", r, "--max-line-bytes", "10", "long.txt"], ["--root", r, "--max-bytes", "3", "plain.txt"],
                 ["--root", r, "latin1.txt"], ["--root", r, "empty.txt"], ["--root", r, "--lines", "0:1", "plain.txt"], ["--root", r, "sub"], ["--root", r, "nope"]]:
        out.append(("peek", args, b""))
    for args in [["--root", r, "doc.json"], ["--root", r, "-p", "/a/2/b", "doc.json"], ["--root", r, "--keys", "doc.json"], ["--root", r, "--length", "-p", "/a", "doc.json"],
                 ["--root", r, "--type", "-p", "/t", "doc.json"], ["--root", r, "--exists", "-p", "/zz", "doc.json"], ["--root", r, "-p", "/zz", "doc.json"],
                 ["--root", r, "bad.json"], ["--root", r, "-p", "x", "doc.json"], ["--root", r, "--keys", "-p", "/a", "doc.json"], ["--keys", "--type", "-"]]:
        out.append(("jsonq", args, b""))
    out.append(("jsonq", ["-p", "/1"], b"[1,2]"))
    for args in [["--root", r, "words.txt"], ["--root", r, "-f", "2", "-d", ",", "fields.csv"], ["--root", r, "--max-keys", "1", "words.txt"], ["--root", r, "--top", "1", "words.txt", "plain.txt"],
                 ["--delim", "ab"], ["--root", r, "latin1.txt"]]:
        out.append(("tally", args, b""))
    out.append(("tally", [], b"x\ny\nx\n"))
    for args in [["--root", r, "plain.txt", "nul.bin", "empty.txt"], ["--root", r, "--algo", "sha512", "plain.txt"], ["--root", r, "--verify", "0" * 64, "plain.txt"],
                 ["--root", r, "--verify", "x", "plain.txt"], ["--root", r, "sub", "missing"], []]:
        out.append(("hash", args, b""))
    for args in [["--root", r], ["--root", r, "--depth", "3", "--long"], ["--root", r, "--max-entries", "2"], ["--root", r, "--max-entries", "2", "--skip", "2"],
                 ["--root", r, "sub", "plain.txt", "missing"], ["--root", r, "link.txt"], ["--root", r, "--depth", "0"]]:
        out.append(("list", args, b""))
    for args in [["--root", r, "--dry-run", "--create", "--stdin", "new.txt"], ["--root", r, "--stdin", "plain.txt"], ["--root", r, "--create", "--stdin", "plain.txt"],
                 ["--root", r, "--if-sha256", "0" * 64, "--stdin", "plain.txt"], ["--root", r, "--create", "--if-sha256", "0" * 64, "--stdin", "x"],
                 ["--root", r, "--dry-run", "--max-diff-lines", "1", "--if-sha256", sha256(fx.path("plain.txt").read_bytes()), "--stdin", "plain.txt"],
                 ["--root", r, "--dry-run", "--max-bytes", "20", "--if-sha256", sha256(fx.path("plain.txt").read_bytes()), "--stdin", "plain.txt"],
                 ["--root", r, "--dry-run", "--if-sha256", sha256(fx.path("latin1.txt").read_bytes()), "--stdin", "latin1.txt"]]:
        out.append(("write", args, b"new content\n"))
    for args in [["--root", r, "--old", "gamma", "--new", "GAMMA", "--dry-run", "plain.txt"], ["--root", r, "--old", "a", "--new", "b", "plain.txt"],
                 ["--root", r, "--old", "zzz", "--new", "b", "plain.txt"], ["--root", r, "--new", "b", "plain.txt"],
                 ["--root", r, "--old", "gamma", "--new", "G", "--dry-run", "latin1.txt"], ["--root", r, "--old", "a", "--new", "A", "--expect", "6", "--max-diff-lines", "0", "--dry-run", "plain.txt"]]:
        out.append(("replace", args, b""))
    return out


class SchemaConformance(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_every_output_in_the_corpus_conforms(self):
        failures = []
        for tool, args, stdin in corpus(self.fx):
            result = run(tool, *args, stdin=stdin, cwd=self.fx.root)
            for p in validate(result):
                failures.append("%s %s: %s" % (tool, args, p))
        self.assertEqual(failures, [])

    def test_introspect_describes_each_tool(self):
        for tool in TOOLS:
            d = introspect(tool)
            self.assertEqual(d["tool"], tool)
            self.assertIn(tool + ".v1", d["schemas"])
            self.assertFalse(d["reads_environment"])
            self.assertTrue(d["authority"]["bounded"])
            self.assertIn("not run", d["evidence"]["agent_in_the_loop"])
            for flag in d["flags"]:
                # A stray `;` or `|` in a help text splits the table and shows
                # up here as an entry with an empty role or kind.
                self.assertIn(flag["role"], {"none", "path-read", "path-write", "root", "guard"}, flag)
                self.assertTrue(flag["kind"], flag)
            for operand in d["operands"]:
                self.assertIn(operand["role"], {"none", "path-read", "path-write"}, operand)
            for rule in d["rules"]:
                self.assertIn(rule["repairable"], {"always", "sometimes", "never"})

    def test_skill_is_generated_markdown(self):
        for tool in TOOLS:
            r = run(tool, "skill")
            self.assertEqual(r.status, 0)
            text = r.stdout.decode()
            self.assertTrue(text.startswith("---\nname: %s\n" % tool))
            for flag in introspect(tool)["flags"]:
                self.assertIn("`" + flag["name"], text)


if __name__ == "__main__":
    unittest.main()
