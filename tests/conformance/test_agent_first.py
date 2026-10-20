"""Agent-first (cancho docs/agent-toolbox.md D2, D6, D11): the properties that make a tool something an agent can use
without a person reading its manual, checked on every binary and on the server that serves them.

These are not claims that agents do better with these tools (that needs an agent in the loop, cancho#228). They are the
mechanical half of "first citizen":

* a tool describes itself completely (`introspect`, `skill`), and the description covers every flag and operand;
* the answer to a bad invocation is data: one JSON record per line on standard output, nothing on standard error, a rule
  from the tool's own table, the exit status that table promises, a hint that says what to do, and the same bytes
  whatever the locale, time zone, terminal or colour settings;
* no refusal is written without a hint: a source scan, so a new refusal cannot be added without one;
* the MCP server tells a host what a tool may do (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`), derived from the same description, so a host can gate on it without parsing prose.
"""

import json
import os
import pathlib
import re
import sys
import tempfile
import unittest

from harness import BIN, EVERY, ROOT, introspect, run

sys.path.insert(0, str(ROOT / "scripts"))
import mcp  # noqa: E402

REQUIRED = ["tool", "version", "summary", "usage", "output", "schemas", "flags", "operands", "exit_codes", "rules",
            "limits", "authority", "reversibility", "guarantees"]
GUARANTEES = ["deterministic", "idempotent", "atomic", "requires_precondition", "dry_run", "bounded_memory",
              "concurrency"]
FOREIGN_ENV = dict(os.environ, LANG="de_DE.UTF-8", LC_ALL="de_DE.UTF-8", TZ="Asia/Tokyo", TERM="xterm-256color",
                   COLUMNS="20", NO_COLOR="", CLICOLOR_FORCE="1")


def records(result):
    """Every line of standard output as JSON (a failure to parse is the test's failure)."""
    return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines() if line.strip()]


def errors_of(result):
    out = []
    for r in records(result):
        if isinstance(r.get("error"), dict):
            out.append(r["error"])
        elif isinstance(r.get("errors"), list):
            out.extend(r["errors"])
    # a document repeats its first error as `error` and in `errors`; keep each once
    seen, unique = set(), []
    for e in out:
        key = json.dumps(e, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


class SelfDescription(unittest.TestCase):
    def test_introspect_is_complete(self):
        for tool in EVERY:
            d = introspect(tool)
            for key in REQUIRED:
                self.assertIn(key, d, "%s: introspect lacks %s" % (tool, key))
            for f in d["flags"]:
                self.assertTrue(f.get("help"), "%s: flag %s has no help" % (tool, f["name"]))
            for o in d["operands"]:
                self.assertTrue(o.get("help"), "%s: operand %s has no help" % (tool, o["name"]))
            for r in d["rules"]:
                self.assertTrue(r.get("summary"), "%s: rule %s has no summary" % (tool, r["rule"]))
                self.assertIn(r["repairable"], ("always", "sometimes", "never"), "%s %s" % (tool, r["rule"]))
            self.assertTrue({0, 2} <= {e["code"] for e in d["exit_codes"]}, "%s: exit codes lack 0 and 2" % tool)
            for key in GUARANTEES:
                self.assertIn(key, d["guarantees"], "%s: guarantees lack %s" % (tool, key))

    def test_the_skill_names_every_flag_and_operand(self):
        for tool in EVERY:
            d = introspect(tool)
            skill = run(tool, "skill").stdout.decode("utf-8")
            self.assertTrue(skill.startswith("---\nname: %s\ndescription: " % tool), tool)
            for f in d["flags"]:
                self.assertIn(f["name"], skill, "%s: the skill omits %s" % (tool, f["name"]))
            for o in d["operands"]:
                self.assertIn(o["name"].rstrip("."), skill, "%s: the skill omits %s" % (tool, o["name"]))


class RefusalsAreData(unittest.TestCase):
    def test_a_bad_flag_is_one_json_record_with_a_rule_a_hint_and_the_promised_status(self):
        for tool in EVERY:
            d = introspect(tool)
            table = {r["rule"]: r for r in d["rules"]}
            result = run(tool, "--no-such-flag")
            self.assertEqual(result.stderr, b"", "%s: a refusal writes nothing to standard error" % tool)
            self.assertNotIn(b"\x1b", result.stdout, "%s: no terminal escapes" % tool)
            errs = errors_of(result)
            self.assertTrue(errs, "%s: a bad flag produced no error record: %r" % (tool, result.stdout[:200]))
            first = errs[0]
            self.assertEqual(first["rule"], "args.unknown-flag", tool)
            self.assertIn(first["rule"], table, "%s: a rule outside its own table" % tool)
            self.assertEqual(result.status, table[first["rule"]]["exit"], "%s: status is not the table's" % tool)
            for key in ("code", "rule", "message", "hint", "repair", "detail"):
                self.assertIn(key, first, "%s: the error lacks %s" % (tool, key))
            self.assertTrue(first["hint"], "%s: the hint is empty" % tool)

    def test_the_bytes_do_not_depend_on_the_environment(self):
        for tool in EVERY:
            plain = run(tool, "--no-such-flag")
            foreign = run(tool, "--no-such-flag", env=FOREIGN_ENV)
            self.assertEqual(plain.stdout, foreign.stdout, "%s: a refusal depends on the locale, zone or terminal" % tool)
            self.assertEqual(plain.status, foreign.status, tool)

    def test_no_tool_waits_for_a_terminal(self):
        # with nothing on standard input and no operands, every tool answers at once (the harness times out at 120 s)
        for tool in EVERY:
            result = run(tool, timeout=20)
            self.assertIn(result.status, range(0, 9), tool)


class EveryRefusalHasAHint(unittest.TestCase):
    """A source scan: the hint argument of a refusal is never the empty string.

    The constructors are `fail.open`, `fail.open_in`, `flag_error`, `flag_problem` and `path_error`; the hint is the
    argument that follows the message. A refusal whose only advice is "no repair" leaves the agent to guess."""

    EMPTY = re.compile(r'(?:fail\.open_in|fail\.open|flag_error|flag_problem|path_error)\([^;]*?"[^"]*",\s*""\s*[,)]')

    def test_no_refusal_is_written_with_an_empty_hint(self):
        offenders = []
        for base in ("tools", "contract"):
            for path in sorted((ROOT / base).rglob("*.cho")):
                text = path.read_text()
                for line_no, line in enumerate(text.splitlines(), 1):
                    if self.EMPTY.search(line):
                        offenders.append("%s:%d" % (path.relative_to(ROOT), line_no))
        self.assertEqual(offenders, [], "a refusal with an empty hint: say what to do")


class McpAnnotations(unittest.TestCase):
    def test_every_tool_tells_a_host_what_it_may_do(self):
        for tool in EVERY:
            d = introspect(tool)
            entry, _ = mcp.definition(d)
            a = entry["annotations"]
            self.assertEqual(set(a), {"title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"})
            effects = set(d["authority"]["effects"])
            writes = bool(effects & {"file_write", "dir_write", "exec"})
            self.assertEqual(a["readOnlyHint"], not writes and d["reversibility"] == "reversible-cheap", tool)
            self.assertEqual(a["destructiveHint"], d["reversibility"] != "reversible-cheap", tool)
            self.assertEqual(a["idempotentHint"], bool(d["guarantees"]["idempotent"]), tool)
            self.assertEqual(a["openWorldHint"], "exec" in effects or "net_out" in effects, tool)

    def test_the_readers_are_read_only_and_the_one_that_starts_programs_reaches_outside(self):
        by = {t: mcp.definition(introspect(t))[0]["annotations"] for t in EVERY}
        for reader in ("seek", "list", "peek", "jsonq", "tally", "hash"):
            self.assertTrue(by[reader]["readOnlyHint"], reader)
            self.assertFalse(by[reader]["destructiveHint"], reader)
        for writer in ("write", "replace", "move", "archive"):
            self.assertFalse(by[writer]["readOnlyHint"], writer)
            self.assertFalse(by[writer]["openWorldHint"], writer)
        self.assertTrue(by["run"]["destructiveHint"])
        self.assertTrue(by["run"]["openWorldHint"])


if __name__ == "__main__":
    unittest.main()
