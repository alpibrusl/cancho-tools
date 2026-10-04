"""M3 -- every rule has a fixture that reaches it, every tool's declared rule
list is exactly what its fixtures reach, and every repair a script can apply
works when applied and takes no authority.

* coverage: the rules reached by the fixtures equal the catalogue
  (contract/rules.ls) -- `every_tool_rule_has_a_fixture`;
* each fixture's first error is its rule, with the catalogue's exit code;
* hint soundness: a `retry` repair, run as it stands in the same state,
  succeeds and does not reach the same rule again; and every flag it adds
  has role `none` (lex-sys docs/agent-toolbox.md D6 rule 1).

Actionability (how many errors carry a repair at all) is reported, not
gated, until a baseline exists (§7.1).
"""

import fcntl
import os
import re
import resource
import signal
import subprocess
import unittest

from harness import ROOT, TOOLS, Fixture, binary, introspect, run_argv


def catalogue():
    text = (ROOT / "contract" / "rules.ls").read_text()
    body = re.search(r'pub fn catalogue\(\) -> \[\] &static \[byte\] \{\s*return "(.*?)";', text, re.S).group(1)
    out = {}
    for entry in body.split(";"):
        tag, code, repairable, summary = entry.split("|")
        out[tag] = (int(code), repairable)
    return out


def as_nobody():
    """Drop to an unprivileged user in the child, so a permission check is
    real even when the suite runs as root (root bypasses file modes)."""
    if os.geteuid() == 0:
        os.setgid(65534)
        os.setuid(65534)


def small_files():
    """A 16-byte file-size limit, with SIGXFSZ ignored so a write past it
    fails with EFBIG instead of killing the process: a failed write."""
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (16, 16))


# Near misses of one flag of role `none`, per tool: the repair the parser
# should suggest, and that must work when applied.
NEAR = {
    "seek": (["--max-cont", "5", "gamma", "plain.txt"], "--max-count"),
    "write": (["--create", "--stdn", "fresh.txt"], "--stdin"),
    "replace": (["--old", "alpha", "--new", "ALPHA", "--expct", "1", "plain.txt"], "--expect"),
    "peek": (["--count-line", "plain.txt"], "--count-lines"),
    "jsonq": (["--keyz", "doc.json"], "--keys"),
    "tally": (["--tops", "2", "words.txt"], "--top"),
    "hash": (["--alg", "sha512", "plain.txt"], "--algo"),
}

# A valid invocation per tool whose PATH operand (or one of them) is `X`,
# for the path and io rules; every other argument is fine.
WITH_PATH = {
    "seek": ["gamma", "X"],
    "write": ["--create", "--stdin", "X"],
    "replace": ["--old", "alpha", "--new", "ALPHA", "X"],
    "peek": ["X"],
    "jsonq": ["X"],
    "tally": ["X"],
    "hash": ["X"],
}

# A boolean flag per tool, for args.unexpected-value.
BOOL = {"seek": "--require-match", "write": "--dry-run", "replace": "--dry-run", "peek": "--count-lines", "jsonq": "--keys"}


def fixtures(fx):
    """(tool, rule, argv-after-the-binary, stdin, preexec, setup) for every
    rule each tool can reach. Arguments are relative to --root, which every
    fixture passes first unless it is about --root itself."""
    r = str(fx.root)
    out = []

    def add(tool, rule, args, stdin=b"new\n", preexec=None, setup=None, root=True):
        argv = (["--root", r] if root else []) + args
        out.append((tool, rule, argv, stdin, preexec, setup))

    for tool in TOOLS:
        near, _ = NEAR[tool]
        add(tool, "args.unknown-flag", near)
        base = WITH_PATH[tool]
        # The file a valid call of this tool names: one that exists and suits
        # it, or for `write` one that does not exist yet.
        good = {"jsonq": "doc.json", "write": "fresh.txt"}.get(tool, "plain.txt")
        form = "ndjson" if tool in ("seek", "hash") else "json"

        def at(name):
            return [a.replace("X", name) for a in base]

        add(tool, "args.bad-value", ["--format", "bogus"] + at(good))
        add(tool, "args.duplicate-flag", ["--format", form, "--format", form] + at(good))
        add(tool, "args.missing-value", at(good) + ["--format"])
        if tool in BOOL:
            add(tool, "args.unexpected-value", [BOOL[tool] + "=yes"] + at(good))

        add(tool, "path.empty", at(""))
        add(tool, "path.dotdot", at("sub/../" + good))
        add(tool, "path.absolute", at(str(fx.root / good)))
        add(tool, "path.outside-root", at(str(fx.dir / "outside" / "secret.txt")))
        add(tool, "path.too-long", at("a" * 5000))
        add(tool, "path.symlink", at("link.txt"))
        if tool != "write":
            add(tool, "io.not-found", at("missing.txt"))
        add(tool, "io.not-a-directory", at("plain.txt/x"))
        add(tool, "io.is-a-directory", at("sub"))
        add(tool, "io.permission-denied", at("plain.txt"), preexec=as_nobody,
            setup=lambda: os.chmod(fx.root / "plain.txt", 0))
        if tool == "write":
            add(tool, "io.read-failed", ["--create", "--content-file", "/proc/self/mem", "fresh.txt"], root=False)
        elif tool == "replace":
            add(tool, "io.read-failed", ["--old", "a", "--new", "b", "--dry-run", "/proc/self/mem"], root=False)
        else:
            add(tool, "io.read-failed", [a.replace("X", "/proc/self/mem") for a in base], root=False)

    # Operands.
    add("seek", "args.missing-operand", ["gamma"])
    add("write", "args.missing-operand", ["--create", "--stdin"])
    add("replace", "args.missing-operand", ["--old", "a", "--new", "b"])
    add("peek", "args.missing-operand", [])
    add("hash", "args.missing-operand", [])
    add("write", "args.too-many-operands", ["--create", "--stdin", "a.txt", "b.txt"])
    add("replace", "args.too-many-operands", ["--old", "a", "--new", "b", "plain.txt", "crlf.txt"])
    add("peek", "args.too-many-operands", ["plain.txt", "crlf.txt"])
    add("jsonq", "args.too-many-operands", ["doc.json", "doc.json"])
    add("hash", "args.too-many-operands", ["--verify", "0" * 64, "plain.txt", "crlf.txt"])
    add("write", "args.conflict", ["--create", "--if-sha256", "0" * 64, "--stdin", "x.txt"])
    add("peek", "args.conflict", ["--lines", "1:2", "--bytes", "0:2", "plain.txt"])
    add("jsonq", "args.conflict", ["--keys", "--type", "doc.json"])
    add("write", "args.required-flag", ["--create", "fresh.txt"])
    add("replace", "args.required-flag", ["--new", "b", "plain.txt"])
    add("write", "io.not-found", ["--if-sha256", "0" * 64, "--stdin", "missing.txt"])
    add("write", "io.write-failed", ["--create", "--stdin", "big.txt"], stdin=b"x" * 100, preexec=small_files)
    add("replace", "io.write-failed", ["--old", "g", "--new", "G" * 40, "--expect", "5001", "long.txt"], preexec=small_files)

    # Limits.
    add("seek", "limit.line-too-long", ["--max-line-bytes", "100", "gamma", "long.txt"])
    add("peek", "limit.line-too-long", ["--max-line-bytes", "100", "long.txt"])
    add("tally", "limit.line-too-long", ["long.txt"] + ["--max-line-bytes", "100"])
    add("write", "limit.input-too-large", ["--max-bytes", "3", "--create", "--stdin", "fresh.txt"])
    add("replace", "limit.input-too-large", ["--max-bytes", "3", "--old", "a", "--new", "b", "plain.txt"])
    add("jsonq", "limit.input-too-large", ["--max-bytes", "3", "doc.json"])
    add("tally", "limit.too-many-keys", ["--max-keys", "1", "words.txt"])

    # Preconditions and conflicts.
    add("write", "precondition.required", ["--stdin", "plain.txt"])
    add("write", "precondition.hash-mismatch", ["--if-sha256", "0" * 64, "--stdin", "plain.txt"])
    add("replace", "precondition.hash-mismatch", ["--if-sha256", "0" * 64, "--old", "alpha", "--new", "b", "plain.txt"])
    add("write", "precondition.content-mismatch", ["--content-sha256", "0" * 64, "--create", "--stdin", "fresh.txt"])
    add("replace", "precondition.count-mismatch", ["--old", "a", "--new", "b", "plain.txt"])
    add("seek", "precondition.no-match", ["--require-match", "zzz", "plain.txt"])
    add("hash", "precondition.hash-failed", ["--verify", "0" * 64, "plain.txt"])
    add("write", "conflict.exists", ["--create", "--stdin", "plain.txt"])

    def hold_lock():
        f = open(fx.root / "plain.txt.lexsys-lock", "a")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fx.held = f

    add("write", "conflict.locked", ["--if-sha256", "0" * 64, "--stdin", "plain.txt"], setup=hold_lock)
    add("replace", "conflict.locked", ["--old", "alpha", "--new", "b", "plain.txt"], setup=hold_lock)

    # Queries.
    add("jsonq", "parse.json", ["bad.json"])
    add("jsonq", "query.bad-pointer", ["-p", "a/b", "doc.json"])
    add("jsonq", "query.no-such-path", ["-p", "/zz", "doc.json"])
    add("jsonq", "query.wrong-kind", ["--keys", "-p", "/a", "doc.json"])
    add("jsonq", "query.unsupported-syntax", ["-p", ".a[0]", "doc.json"])
    return out


def flags_in(argv):
    return {a.split("=")[0] for a in argv if a.startswith("--") and a != "--"}


class Rules(unittest.TestCase):
    def run_fixture(self, index):
        # Each fixture runs in a fresh tree, and its arguments name that tree.
        fx = Fixture()
        fx.held = None
        tool, rule, args, stdin, preexec, setup = fixtures(fx)[index]
        try:
            if setup:
                setup()
            argv = [binary(tool)] + args
            p = subprocess.run(argv, input=stdin, capture_output=True, cwd=fx.root, preexec_fn=preexec, timeout=60)
            result = run_argv(tool, argv, stdin)
            result.status, result.stdout, result.stderr = p.returncode, p.stdout, p.stderr
            repair_outcome = None
            errors = result.errors() if p.stdout else []
            if errors and errors[0]["repair"] and errors[0]["repair"]["kind"] == "retry":
                again = subprocess.run(errors[0]["repair"]["argv"], input=stdin, capture_output=True, cwd=fx.root, preexec_fn=preexec, timeout=60)
                repair_outcome = (errors[0]["repair"]["argv"], again)
            return result, errors, repair_outcome
        finally:
            if fx.held:
                fx.held.close()
            os.chmod(fx.root / "plain.txt", 0o644)
            fx.cleanup()

    def test_every_rule_has_a_fixture_and_every_fixture_its_rule(self):
        cat = catalogue()
        fx = Fixture()
        try:
            all_fixtures = fixtures(fx)
        finally:
            fx.cleanup()
        reached = set()
        per_tool = {t: set() for t in TOOLS}
        failures = []
        repaired = 0
        for index, fixture in enumerate(all_fixtures):
            tool, rule = fixture[0], fixture[1]
            result, errors, repair = self.run_fixture(index)
            if not errors:
                failures.append("%s %s: no error (status %d) %r" % (tool, rule, result.status, result.stdout[:200]))
                continue
            first = errors[0]
            if first["rule"] != rule:
                failures.append("%s %s: first error is %s" % (tool, rule, first["rule"]))
                continue
            if result.status != cat[rule][0]:
                failures.append("%s %s: exit %d, catalogue says %d" % (tool, rule, result.status, cat[rule][0]))
            reached.add(rule)
            per_tool[tool].add(rule)
            if cat[rule][1] == "never" and first["repair"] and first["repair"]["kind"] == "retry":
                failures.append("%s %s: a retry repair on a rule the catalogue calls never-repairable" % (tool, rule))
            if repair:
                repaired += 1
                argv, again = repair
                if again.returncode not in (0, 9):
                    failures.append("%s %s: the repair %r exited %d: %r" % (tool, rule, argv[1:], again.returncode, again.stdout[:300]))
                if ('"rule":"%s"' % rule).encode() in again.stdout:
                    failures.append("%s %s: the repair reached the same rule again" % (tool, rule))
                roles = {f["name"]: f["role"] for f in introspect(tool)["flags"]}
                for added in flags_in(argv) - flags_in(result.argv):
                    if roles.get(added) != "none":
                        failures.append("%s %s: the repair adds %s, whose role is %s" % (tool, rule, added, roles.get(added)))
        self.maxDiff = None
        self.assertEqual(failures, [])
        self.assertEqual(reached, set(cat), "rules without a fixture: %s" % sorted(set(cat) - reached))
        for tool in TOOLS:
            declared = {r["rule"] for r in introspect(tool)["rules"]}
            self.assertEqual(declared, per_tool[tool], "%s declares %s; fixtures reach %s" % (
                tool, sorted(declared - per_tool[tool]), sorted(per_tool[tool] - declared)))
        print("\nM3: %d fixtures, %d rules, %d retry repairs applied" % (len(all_fixtures), len(reached), repaired))


if __name__ == "__main__":
    unittest.main()
