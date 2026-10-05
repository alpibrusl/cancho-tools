"""The agent benchmark's own gates (docs/agent-bench.md §4).

No model is run here. What is shown is that the benchmark could fail: for every
task, the reference solutions pass its checker, doing nothing fails it, and the
unsafe solution fails it; that the arms give an agent exactly the tools §2 says;
that the prompts name no tool; and that the parsers read what the two agents
really print (transcripts captured from real runs, with their paths removed).
"""

import json
import pathlib
import re
import sys
import unittest
from unittest import mock

from harness import BIN, ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import agentbench as ab  # noqa: E402
import agentbench_tasks as tasks  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "agentbench_fixtures"


class Checks(unittest.TestCase):
    def test_every_check_can_fail_and_every_reference_passes(self):
        self.assertEqual(ab.verify(BIN), [])

    def test_the_task_set_is_what_the_design_says(self):
        by = {}
        for t in tasks.TASKS:
            by.setdefault(t.category, []).append(t.id)
        self.assertEqual({k: len(v) for k, v in by.items()}, {"read": 9, "edit": 4, "safety": 6, "scope": 2})
        self.assertEqual(len(tasks.BY_ID), 21)
        self.assertEqual(sorted(t.id for t in tasks.TASKS if t.pilot),
                         sorted(["r1-version", "r2-todos", "e1-append", "e2-rename", "s1a-stale-changed", "s1b-stale-same", "s2-link"]))
        # The tools cannot do the two outside their scope, by design; they can do all the rest.
        self.assertEqual(sorted(t.id for t in tasks.TASKS if t.tools is None), ["o1-regex", "o2-run"])
        # Every edit and safety task has its unsafe solution.
        self.assertEqual(sorted(t.id for t in tasks.TASKS if t.category in ("edit", "safety") and not t.bad), [])

    def test_prompts_name_no_tool_flag_or_arm(self):
        for t in tasks.TASKS:
            for word in ["cancho", "mcp", "jsonq", "seek", "peek", "tally", "skill", "--"]:
                self.assertNotIn(word, t.prompt.lower(), "%s names %r" % (t.id, word))

    def test_a_fixture_is_the_same_every_time(self):
        for t in tasks.TASKS:
            if t.id == "r5-biglog":
                continue  # 32 MiB, and built once into a cache
            a_dir, a_ws, a_facts = ab.new_workspace(t)
            b_dir, b_ws, b_facts = ab.new_workspace(t)
            try:
                self.assertEqual(tasks.snapshot(a_ws), tasks.snapshot(b_ws), t.id)
                self.assertEqual({k: v for k, v in a_facts.items() if k != "_before"},
                                 {k: v for k, v in b_facts.items() if k != "_before"}, t.id)
            finally:
                import shutil
                shutil.rmtree(a_dir, ignore_errors=True)
                shutil.rmtree(b_dir, ignore_errors=True)

    def test_a_safety_case_and_its_twin_differ_only_in_the_state(self):
        for a, b in [("s1a-stale-changed", "s1b-stale-same"), ("s3a-count-mismatch", "s3b-count-ok")]:
            self.assertEqual(tasks.BY_ID[a].prompt, tasks.BY_ID[b].prompt)

    def test_the_writers_lock_sidecar_is_litter_not_a_failure(self):
        t = tasks.BY_ID["e1-append"]
        run_dir, ws, facts = ab.new_workspace(t)
        try:
            ab.run_script(t.tools, ws, facts, BIN)
            self.assertTrue(t.check(ws, "", facts)[0])
            self.assertEqual(tasks.litter(ws, facts), 1)
        finally:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


    def test_what_the_harness_installs_is_not_a_change(self):
        # Every arm writes config and skills into the workspace; none of it may
        # turn a state-checking task into a failure.
        import json
        import shutil
        t = tasks.BY_ID["e1-append"]
        run_dir, ws, facts = ab.new_workspace(t)
        try:
            (ws / "opencode.json").write_text(json.dumps(ab.opencode_config("skills", ws, "ollama/q")))
            ab.skills_into(ws, ".opencode/skills")
            ab.skills_into(ws, ".claude/skills")
            self.assertEqual(tasks.changed(ws, facts), [])
            self.assertEqual(tasks.litter(ws, facts), 0)
            ab.run_script(t.bash, ws, facts, BIN)
            self.assertTrue(t.check(ws, "", facts)[0])
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class Parsers(unittest.TestCase):
    def test_claude_code_stream(self):
        r = ab.claude_parse((FIXTURES / "claude-mcp.jsonl").read_text())
        self.assertEqual((r["turns"], r["tool_errors"]), (8, 0))
        self.assertAlmostEqual(r["cost"], 0.083596)
        self.assertTrue(r["answer"].startswith("All four steps are done"))
        self.assertEqual(r["calls"]["mcp__cancho-tools__replace"], 1)
        self.assertGreater(r["first_input"], 10000)
        self.assertGreater(r["tokens_in"], r["first_input"])

    def test_opencode_events(self):
        r = ab.opencode_parse((FIXTURES / "opencode-mcp.jsonl").read_text())
        self.assertEqual((r["answer"], r["turns"], r["calls"]), ("1.2.3", 2, {"cancho-tools_jsonq": 1}))
        self.assertEqual(r["tokens_out"], 62 + 21)

    def test_an_answer_is_what_follows_the_last_call(self):
        events = [{"type": "text", "part": {"text": "let me look"}},
                  {"type": "tool_use", "part": {"tool": "bash", "state": {"status": "error"}}},
                  {"type": "text", "part": {"text": "42"}}]
        r = ab.opencode_parse("\n".join(json.dumps(e) for e in events))
        self.assertEqual((r["answer"], r["tool_errors"]), ("42", 1))

    def test_answers_are_read_by_form_not_by_prose(self):
        self.assertEqual(tasks.answer_lines("```\n- src/a.py:2\n1. src/b.py:7\n```"), ["src/a.py:2", "src/b.py:7"])
        self.assertEqual(tasks.last_int("It is on line **271,828**."), 271828)
        self.assertEqual(tasks.first_word("**CONFLICT** - it changed"), "CONFLICT")
        self.assertEqual(tasks.first_word(""), "")


class Arms(unittest.TestCase):
    def claude_command(self, arm):
        t = tasks.BY_ID["r1-version"]
        run_dir, ws, facts = ab.new_workspace(t)
        try:
            with mock.patch.object(ab, "spawn", return_value=(0, "", "")) as spawn, \
                    mock.patch.object(ab, "mcp_server_command", return_value=["/m", "--root", str(ws)]):
                ab.claude_run(t, arm, ws, "p", "m", 5, ws.parent / "log")
            return spawn.call_args[0][0], spawn.call_args[0][2]
        finally:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)

    def test_claude_code_arms(self):
        cmd, _ = self.claude_command("bash")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Bash")
        for tool in ab.CLAUDE_BUILTINS:
            self.assertIn(tool, cmd[cmd.index("--disallowedTools"):])
        self.assertNotIn("--mcp-config", cmd)

        cmd, _ = self.claude_command("mcp")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__cancho-tools__*")
        self.assertIn("Bash", cmd[cmd.index("--disallowedTools"):])
        self.assertIn("--strict-mcp-config", cmd)

        cmd, env = self.claude_command("skills")
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
        self.assertEqual(sorted(a for a in allowed if a != "Skill"),
                         sorted("Bash(%s:*)" % p for p in ab.tool_patterns()))
        self.assertTrue(env["PATH"].startswith(str(BIN.resolve())))
        self.assertNotIn("Bash", cmd[cmd.index("--disallowedTools"):])
        # Every arm sees only the project's settings, never a person's own.
        for arm in ab.ARMS:
            c, _ = self.claude_command(arm)
            self.assertEqual(c[c.index("--setting-sources") + 1], "project")

    def test_opencode_arms(self):
        ws = pathlib.Path("/w")
        with mock.patch.object(ab, "mcp_server_command", return_value=["/m", "--root", "/w"]):
            bash, mcp, skills = (ab.opencode_config(a, ws, "ollama/q") for a in ab.ARMS)
        on = lambda c: sorted(k for k, v in c["tools"].items() if v)
        self.assertEqual(on(bash), ["bash"])
        self.assertEqual(on(mcp), [])
        self.assertIn("cancho-tools", mcp["mcp"])
        self.assertEqual(mcp["permission"]["bash"], "deny")
        self.assertEqual(on(skills), ["bash", "skill"])
        self.assertEqual(skills["permission"]["bash"]["*"], "deny")
        for p in ab.tool_patterns():
            self.assertEqual(skills["permission"]["bash"]["%s *" % p], "allow")
        for c in (bash, mcp, skills):
            self.assertEqual(c["permission"]["edit"], "deny")

    def test_an_agent_is_started_in_the_workspace_by_pwd_too(self):
        # An agent that trusts `$PWD` would otherwise work in the repository.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = ab.spawn(["bash", "-c", "echo $PWD; pwd"], d, dict(os.environ), 10, os.path.join(d, "log"))
            real = os.path.realpath(d)
            self.assertEqual([os.path.realpath(x) for x in out.split()], [real, real])

    def test_a_timeout_kills_the_whole_process_group(self):
        import os
        import tempfile
        import time
        started = time.time()
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = ab.spawn(["bash", "-c", "sleep 30 & sleep 30"], d, dict(os.environ), 1, os.path.join(d, "log"))
        self.assertEqual(code, "timeout")
        self.assertLess(time.time() - started, 10)


if __name__ == "__main__":
    unittest.main()
