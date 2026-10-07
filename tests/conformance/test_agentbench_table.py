"""The table suite's own gates (docs/agent-bench-table.md section 4).

No model is run. What is shown: the answers the tasks expect are right (the file, read back with
`csv`, `decimal` and `fractions`, gives them); the checker passes the truth and fails a wrong answer,
a refusal and silence, and tells the four verdicts apart; the arms give an agent exactly the programs the
design says; a path outside the workspace in a command is seen. With a built `table` in `TABLE_BIN`,
one `table` command per task also gives the truth (the tasks `table` can answer are answerable).
"""

import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

from harness import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import agentbench_table as atb  # noqa: E402
import agentbench_table_tasks as tt  # noqa: E402

REFS = {  # the one `table` command per task that answers it, and how its output is read
    "quoted-records": (["--where", "customer = 'Okafor, Ngozi \"Ngo\"'", "--agg", "count"], "cell"),
    "empty-cell": (["--where", "bytes != '' and bytes:int > 1000", "--agg", "count"], "cell"),
    "crlf": (["--where", "region = 'EU'", "--agg", "count"], "cell"),
    "bom": (["--agg", "max:order_id:int"], "cell"),
    "prefix-columns": (["--agg", "sum:price:int"], "cell"),
    "decimal-10k": (["--agg", "sum:amount:dec(2)"], "cell"),
    "sum-past-64-bits": (["--agg", "sum:bytes:int"], "cell"),
    "stable-ties": (["--order-by", "-score:int", "--limit", "6", "--select", "id", "--format", "csv"], "col"),
    "top-3": (["--group", "customer", "--agg", "sum:amount:int", "--sort", "-sum:amount", "--top", "3", "--format", "csv"], "pairs"),
    "10k-keys": (["--group", "user_id", "--sort", "-count", "--top", "1", "--format", "csv"], "first"),
    "distinct-count": (["--agg", "distinct:session"], "cell"),
    "filter-404": (["--where", "status = 404 and bytes:int > 50000", "--agg", "count"], "cell"),
    "mean-rounding": (["--where", "grade = 'A'", "--agg", "mean:score:dec(3)@3"], "cell"),
    "unknown-column": (["--agg", "sum:bytes:int"], "cell"),  # after the repair: the name as the header has it
    "million-rows": (["--where", "status = 500", "--agg", "count"], "cell"),
}


def read(kind, out):
    if kind == "cell":
        return json.loads(out)["data"]["rows"][0][0]
    lines = out.strip().splitlines()[1:]
    if kind == "col":
        return ",".join(lines)
    if kind == "pairs":
        return ",".join(l.replace(",", ":") for l in lines)
    return lines[0].split(",")[0]


class Checks(unittest.TestCase):
    def test_every_expected_answer_is_what_the_file_gives(self):
        self.assertEqual(atb.verify(), [])

    def test_the_task_set_is_what_the_design_says(self):
        self.assertEqual(len(tt.TASKS), 18)
        self.assertEqual(len({t.id for t in tt.TASKS}), 18)
        controls = [t for t in tt.TASKS if t.category == "control"]
        self.assertGreaterEqual(len(controls), 3)  # tasks where plain tools do fine (design section 3)
        for t in tt.TASKS:
            self.assertNotRegex(tt.prompt(t).lower(), r"\b(awk|jq|python|pandas|table|csvkit|cut)\b", t.id)  # a prompt names no tool

    def test_the_four_verdicts(self):
        t, f = tt.BY_ID["crlf"], {"truth": 89}
        self.assertEqual(tt.classify(t, f, "I counted.\nANSWER: 89")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER: **89**")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER: 90")[0], "wrong")
        self.assertEqual(tt.classify(t, f, "ANSWER: CANNOT, the file is odd")[0], "refused")
        self.assertEqual(tt.classify(t, f, "I am unable to read the file.")[0], "refused")
        self.assertEqual(tt.classify(t, f, "The count is 89.")[0], "no-answer")
        self.assertEqual(tt.classify(t, f, "")[0], "no-answer")
        self.assertEqual(tt.classify(t, f, "ANSWER: 89", harness_failed=True)[0], "no-answer")

    def test_confidence_is_read_from_the_text(self):
        t, f = tt.BY_ID["crlf"], {"truth": 89}
        self.assertTrue(tt.classify(t, f, "Probably 90, I think.\nANSWER: 90")[2])
        self.assertFalse(tt.classify(t, f, "It is 90.\nANSWER: 90")[2])

    def test_a_number_is_exact_not_close(self):
        t, f = tt.BY_ID["decimal-10k"], {"truth": Decimal("65913.50")}
        self.assertEqual(tt.classify(t, f, "ANSWER: 65913.50")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER: 65,913.5")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER: 65913.4999999977")[0], "wrong")
        self.assertEqual(tt.classify(t, f, "ANSWER: 65913.5 dollars")[0], "wrong")
        t, f = tt.BY_ID["sum-past-64-bits"], {"truth": 200831828606425987744}
        self.assertEqual(tt.classify(t, f, "ANSWER: 2.00831828606426e+20")[0], "wrong")

    def test_a_list_keeps_its_order(self):
        t, f = tt.BY_ID["stable-ties"], {"truth": ["r1", "r3", "r7"]}
        self.assertEqual(tt.classify(t, f, "ANSWER: r1, r3, r7")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER: r3,r1,r7")[0], "wrong")

    def test_the_refusal_task_wants_the_cell_named_and_a_number_alone_is_wrong(self):
        t = tt.BY_ID["na-cell"]
        f = {"bad_id": "SKU-0412", "bad_line": 413, "valid_total": Decimal("297939.44")}
        self.assertEqual(tt.classify(t, f, "ANSWER: 297939.44")[0], "wrong")
        self.assertEqual(tt.classify(t, f, "ANSWER: CANNOT")[0], "wrong")  # says nothing about where
        self.assertEqual(tt.classify(t, f, "Row SKU-0412 holds N/A.\nANSWER: CANNOT")[0], "right")
        self.assertEqual(tt.classify(t, f, "Line 413 is N/A.\nANSWER: CANNOT")[0], "right")
        self.assertEqual(tt.classify(t, f, "SKU-0412 is N/A; without it 297939.44.\nANSWER: 297939.44")[0], "right")

    def test_the_header_only_file_is_zero(self):
        t, f = tt.BY_ID["header-only"], {"truth": 0}
        self.assertEqual(tt.classify(t, f, "ANSWER: 0")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER: 0.00")[0], "right")
        self.assertEqual(tt.classify(t, f, "ANSWER:")[0], "no-answer")


class Arms(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        fake = self.tmp / "table"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        os.environ["TABLE_BIN"] = str(fake)
        os.environ["TABLE_MCP"] = "/bin/echo --root {root}"

    def names(self, arm):
        return {p.name for p in atb.arm_dir(arm, self.tmp).iterdir()}

    def test_each_arm_reaches_exactly_its_programs(self):
        b, p, s = self.names("bash"), self.names("python"), self.names("table-skill")
        self.assertIn("awk", b)
        self.assertIn("jq", b)
        for gone in ("python3", "table", "perl", "sqlite3"):
            self.assertNotIn(gone, b)
        self.assertIn("python3", p)
        for gone in ("awk", "jq", "sort", "table", "perl"):
            self.assertNotIn(gone, p)
        self.assertEqual(s - {"env.sh"}, (b - {"env.sh"}) | {"table"})
        self.assertNotIn("env", b | p | s)  # `env PATH=... cmd` would walk out of the arm

    def test_path_is_read_only_inside_the_shell(self):
        d = atb.arm_dir("bash", self.tmp)
        r = subprocess.run(["/bin/bash", "-c", "export PATH=/usr/bin:/bin; echo $PATH"], env={"PATH": str(d), "BASH_ENV": str(d / "env.sh")},
                           capture_output=True, text=True)
        self.assertIn("readonly", r.stderr, r)
        r = subprocess.run(["/bin/bash", "-c", "python3 --version"], env={"PATH": str(d), "BASH_ENV": str(d / "env.sh")},
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_the_agent_configuration_per_arm(self):
        ws = self.tmp / "ws"
        m = "ollama/x"
        for arm in ("bash", "python"):
            c = atb.config(arm, ws, m)
            self.assertTrue(c["tools"]["bash"])
            self.assertFalse(c["tools"]["skill"])
            self.assertNotIn("mcp", c)
        c = atb.config("table-skill", ws, m)
        self.assertTrue(c["tools"]["bash"] and c["tools"]["skill"])
        c = atb.config("table-mcp", ws, m)
        self.assertFalse(c["tools"]["bash"])
        self.assertEqual(c["permission"]["bash"], "deny")
        self.assertEqual(c["mcp"]["table"]["command"], ["/bin/echo", "--root", str(ws)])
        for arm in atb.ARMS:
            for harness_tool in ("read", "write", "edit", "grep", "glob", "list", "webfetch", "task"):
                self.assertFalse(atb.config(arm, ws, m)["tools"][harness_tool], (arm, harness_tool))


class Exposure(unittest.TestCase):
    def transcript(self, *commands):
        f = pathlib.Path(tempfile.mkdtemp()) / "t.jsonl"
        f.write_text("".join(json.dumps({"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": c}}}}) + "\n" for c in commands))
        return f

    def test_a_path_outside_the_workspace_is_seen_and_an_awk_regex_is_not(self):
        run = pathlib.Path(tempfile.mkdtemp(prefix="agentbench-")).resolve()
        ws = run / "ws"
        ws.mkdir()
        (run / "private.txt").write_text("x")
        home = os.path.expanduser("~")
        t = self.transcript("awk '$3 ~ /EU/ {n++} END{print n}' data.csv", "cat ../private.txt", "ls %s" % home, "cat data.csv", "head /usr/bin/awk",
                            "echo hi > /tmp/scratch-%d.txt" % os.getpid())
        pathlib.Path("/tmp/scratch-%d.txt" % os.getpid()).write_text("")
        outside, scratch = atb.touched_outside(t, ws)
        self.assertIn(str(run / "private.txt"), outside)
        self.assertIn(str(pathlib.Path(home).resolve()), outside)
        self.assertEqual([o for o in outside if o.startswith("/usr")], [])
        self.assertTrue(any("scratch-" in s for s in scratch))
        quiet = atb.touched_outside(self.transcript("awk '$3 ~ /EU/ {n++}' data.csv", "sort data.csv | uniq -c"), ws)
        self.assertEqual(quiet, ([], []))
        os.unlink("/tmp/scratch-%d.txt" % os.getpid())

    def test_leaving_the_arm_is_seen(self):
        t = self.transcript("export PATH=/usr/bin:$PATH; python3 x.py", "ls", "command -p python3 -V")
        self.assertEqual(len(atb.left_the_arm(t, "bash")), 2)
        self.assertEqual(atb.left_the_arm(t, "table-mcp"), [])


@unittest.skipUnless(os.environ.get("TABLE_BIN_REAL"), "set TABLE_BIN_REAL to a built `table` to check the reference commands")
class TableReferences(unittest.TestCase):
    def test_one_table_command_per_task_gives_the_truth(self):
        exe = os.environ["TABLE_BIN_REAL"]
        for tid, (args, kind) in REFS.items():
            t = tt.BY_ID[tid]
            ws = pathlib.Path(tempfile.mkdtemp())
            facts = t.build(ws, random.Random(t.id))
            out = subprocess.run([exe, *args, "data.csv"], cwd=ws, capture_output=True, text=True).stdout
            got = read(kind, out)
            truth = facts["truth"]
            if kind in ("cell",):
                self.assertEqual(Decimal(got), Decimal(truth), tid)
            else:
                self.assertEqual(got, ",".join(truth) if isinstance(truth, list) else truth, tid)

    def test_the_traps_table_itself_refuses_with_a_rule(self):
        exe = os.environ["TABLE_BIN_REAL"]
        ws = pathlib.Path(tempfile.mkdtemp())
        facts = tt.BY_ID["na-cell"].build(ws, random.Random("na-cell"))
        r = subprocess.run([exe, "--agg", "sum:price:dec(2)", "data.csv"], cwd=ws, capture_output=True, text=True)
        e = json.loads(r.stdout)["error"]
        self.assertEqual(r.returncode, 8)
        self.assertEqual(e["rule"], "value.not-decimal")
        self.assertEqual(e["detail"]["line"], facts["bad_line"])


if __name__ == "__main__":
    unittest.main()
