"""`run` (docs/next-tools.md section 3): the gates that are about starting a program.

`run` starts only programs found in one directory, baked into the binary, so these
tests build a variant with a scratch programs directory (scripts/run_build.py) and judge
it from outside: the bytes of its answer, the processes it leaves, the files the programs
write. The shared gates (M1, M3, M6) that need a built `build/run` are in the other test
files; `run` is not in harness.TOOLS because it is opt-in and several of those gates (M2
determinism, M7 mutation, M8 confinement of writes) are claims it deliberately does not make.

* the argument list reaches the program byte for byte, with no shell;
* the environment is exactly `PATH=<programs directory>`;
* the program starts in `--root`, and not elsewhere;
* PROGRAM is a name: a path, `..`, empty and a name that is not there are each a tag;
* a deadline and an output cap end the program, kill it, and leave no process;
* the answer validates against schemas/run.v1.json;
* the memory held is bounded by --max-output, not by what the program writes;
* the authority row names the baked directory and nothing wider.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
from harness import ROOT, peak_rss, schema  # noqa: E402
import run_build  # noqa: E402


def link(programs, name, target):
    path = shutil.which(target) or target
    os.symlink(path, programs / name)


def script(programs, name, body):
    p = programs / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)


class Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        cls.programs = base / "programs"
        cls.programs.mkdir()
        cls.work = base / "work"
        cls.work.mkdir()
        cls.out = base / "run"
        script(cls.programs, "args", 'for a in "$@"; do printf "[%s]\\n" "$a"; done\n')
        script(cls.programs, "where", 'pwd\n')
        script(cls.programs, "envdump", 'echo "PATH=$PATH"; echo "HOME=${HOME-unset}"; echo "LANG=${LANG-unset}"; echo "n=$(env | wc -l)"\n')
        script(cls.programs, "status", 'echo out; echo err >&2; exit "$1"\n')
        script(cls.programs, "selfkill", 'kill -9 $$\n')
        script(cls.programs, "pidfile", 'echo $$ > "$1"; exec sleep 60\n')
        script(cls.programs, "noexec", 'echo no\n')
        (cls.programs / "noexec").chmod(0o644)
        link(cls.programs, "cat", "cat")
        link(cls.programs, "yes", "yes")
        link(cls.programs, "sleep", "sleep")
        link(cls.programs, "env", "env")
        link(cls.programs, "wc", "wc")
        run_build.build(str(cls.programs), str(cls.out))
        cls.validator = jsonschema.Draft202012Validator(schema("run"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, *args, stdin=b"", cwd=None, timeout=60):
        p = subprocess.run([str(self.out), *args], input=stdin, capture_output=True,
                           cwd=cwd or self.work, timeout=timeout)
        doc = json.loads(p.stdout) if p.stdout.strip().startswith(b"{") else None
        if doc is not None:
            problems = [e.message[:160] for e in self.validator.iter_errors(doc)]
            self.assertEqual(problems, [], p.stdout[:400])
        return p, doc

    def rule(self, doc):
        return doc["error"]["rule"]

    # ---- the argument list and the environment --------------------------------

    def test_arguments_reach_the_program_byte_for_byte(self):
        hostile = ["a b", ";", "$(id)", "`id`", "*", "-n", "--root", "--", "", "é日😀", "x\ty", "a'b\"c", "-"]
        p, d = self.run_tool("--", "args", *hostile)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["data"]["exit"], 0)
        self.assertEqual(d["data"]["stdout"], "".join("[%s]\n" % a for a in hostile))

    def test_flags_before_the_double_dash_are_the_tools_and_after_are_the_programs(self):
        p, d = self.run_tool("--timeout-ms", "5000", "--", "args", "--timeout-ms", "1", "--format", "text")
        self.assertEqual(d["data"]["stdout"], "[--timeout-ms]\n[1]\n[--format]\n[text]\n")

    def test_the_environment_is_exactly_path(self):
        p, d = self.run_tool("--", "env")
        self.assertEqual(d["data"]["stdout"], "PATH=%s\n" % self.programs)
        p, d = self.run_tool("--", "envdump")
        self.assertEqual(d["data"]["stdout"].splitlines()[:3], ["PATH=%s" % self.programs, "HOME=unset", "LANG=unset"])

    def test_the_callers_environment_does_not_arrive(self):
        env = dict(os.environ, SECRET="hunter2", LD_PRELOAD="", HOME="/root")
        p = subprocess.run([str(self.out), "--", "env"], capture_output=True, cwd=self.work, env=env, stdin=subprocess.DEVNULL, timeout=60)
        self.assertNotIn(b"hunter2", p.stdout)
        self.assertNotIn(b"HOME", p.stdout)

    def test_the_program_starts_in_root(self):
        sub = self.work / "sub"
        sub.mkdir(exist_ok=True)
        p, d = self.run_tool("--root", str(sub), "--", "where")
        self.assertEqual(d["data"]["stdout"].strip(), str(sub.resolve()))
        p, d = self.run_tool("--", "where")
        self.assertEqual(d["data"]["stdout"].strip(), str(self.work.resolve()))

    def test_standard_input_only_with_the_flag(self):
        p, d = self.run_tool("--", "cat", stdin=b"given")
        self.assertEqual(d["data"]["stdout"], "")
        p, d = self.run_tool("--stdin", "--", "cat", stdin=b"given\x00\xff")
        self.assertEqual(d["data"]["stdout"], {"b64": "Z2l2ZW4A/w=="})
        p, d = self.run_tool("--stdin", "--max-stdin", "4", "--", "cat", stdin=b"too long")
        self.assertEqual(self.rule(d), "limit.input-too-large")

    # ---- what ran, and how it ended ------------------------------------------------

    def test_the_tool_exits_zero_whatever_the_program_did(self):
        p, d = self.run_tool("--", "status", "7")
        self.assertEqual(p.returncode, 0)
        self.assertEqual((d["data"]["exit"], d["data"]["signal"]), (7, None))
        self.assertEqual((d["data"]["stdout"], d["data"]["stderr"]), ("out\n", "err\n"))

    def test_a_program_ended_by_a_signal_has_no_exit_status(self):
        p, d = self.run_tool("--", "selfkill")
        self.assertEqual(p.returncode, 0)
        self.assertIsNone(d["data"]["exit"])
        self.assertIsNotNone(d["data"]["signal"])

    def test_a_program_that_is_not_executable_is_spawn_failed(self):
        p, d = self.run_tool("--", "noexec")
        self.assertEqual(p.returncode, 8)
        self.assertEqual(self.rule(d), "run.spawn-failed")
        self.assertEqual(d["error"]["detail"]["errno"], 13)

    # ---- PROGRAM is a name ------------------------------------------------------------

    def test_a_name_that_is_not_one_plain_name_is_refused(self):
        for bad in ["", ".", "..", "../args", "/bin/true", "sub/args", "./args", "args/", str(self.programs / "args")]:
            p, d = self.run_tool("--", bad)
            self.assertEqual((p.returncode, self.rule(d)), (2, "run.program-name"), bad)

    def test_a_program_the_directory_does_not_hold_is_not_found(self):
        p, d = self.run_tool("--", "nosuchprogram")
        self.assertEqual((p.returncode, self.rule(d)), (3, "run.program-not-found"))
        self.assertEqual(d["error"]["detail"]["program"], "nosuchprogram")

    def test_a_program_outside_the_directory_is_not_reachable_by_name(self):
        # `true` is on PATH of the caller and in /bin, and is not in the directory.
        p, d = self.run_tool("--", "true")
        self.assertEqual(self.rule(d), "run.program-not-found")

    def test_no_program_operand(self):
        p, d = self.run_tool()
        self.assertEqual((p.returncode, self.rule(d)), (2, "args.missing-operand"))

    def test_there_is_no_dry_run(self):
        p, d = self.run_tool("--dry-run", "--", "args")
        self.assertEqual((p.returncode, self.rule(d)), (2, "args.unknown-flag"))

    # ---- a deadline and a cap --------------------------------------------------------------

    def pid_alive(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:
            stat = pathlib.Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return False
        return stat != "Z"

    def test_the_deadline_kills_the_program_and_leaves_no_process(self):
        pidfile = self.work / "pid"
        pidfile.unlink(missing_ok=True)
        started = time.monotonic()
        p, d = self.run_tool("--timeout-ms", "400", "--", "pidfile", str(pidfile))
        took = time.monotonic() - started
        self.assertEqual((p.returncode, self.rule(d)), (8, "limit.timeout"))
        self.assertTrue(d["data"]["timed_out"])
        self.assertLess(took, 5)
        pid = int(pidfile.read_text())
        time.sleep(0.2)
        self.assertFalse(self.pid_alive(pid), "the killed program is still there")

    def test_the_output_cap_kills_the_program_and_keeps_the_cap(self):
        p, d = self.run_tool("--max-output", "1000", "--", "yes")
        self.assertEqual((p.returncode, self.rule(d)), (8, "limit.output-too-large"))
        self.assertEqual(len(d["data"]["stdout"]), 1000)
        self.assertTrue(d["data"]["truncated"]["stdout"])
        self.assertFalse(d["data"]["truncated"]["stderr"])

    def test_output_exactly_at_the_cap_is_not_an_overrun(self):
        # `args abcd` writes 7 bytes: a cap of 7 holds it all, a cap of 6 is passed by one.
        p, d = self.run_tool("--max-output", "7", "--", "args", "abcd")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["data"]["stdout"], "[abcd]\n")
        self.assertEqual(d["data"]["truncated"], {"stdout": False, "stderr": False})
        p, d = self.run_tool("--max-output", "6", "--", "args", "abcd")
        self.assertEqual(self.rule(d), "limit.output-too-large")
        self.assertEqual(d["data"]["stdout"], "[abcd]")

    def test_flags_are_checked(self):
        for flag, value in [("--timeout-ms", "0"), ("--timeout-ms", "600001"), ("--max-output", "0"),
                            ("--max-output", "67108865"), ("--max-stdin", "67108865"), ("--timeout-ms", "x")]:
            p, d = self.run_tool(flag, value, "--", "args")
            self.assertEqual((p.returncode, self.rule(d)), (2, "args.bad-value"), (flag, value))

    def test_the_memory_held_is_bounded_by_the_cap_not_by_the_program(self):
        cap = 8 * 1024 * 1024
        status, peak_kib = peak_rss([self.out, "--max-output", cap, "--timeout-ms", "20000", "--", "yes"], cwd=self.work)
        self.assertEqual(status, 8, "the program writes without end, so the cap ends it")
        print("run: peak RSS %d KiB for a %d-byte cap" % (peak_kib, cap), file=sys.stderr)
        # Two streams of at most `cap`, the JSON being built from one, and the buffers' growth: under
        # ten times the cap, and far under what `yes` can write in the 20 s it is allowed.
        self.assertLess(peak_kib * 1024, 10 * cap, "peak %d KiB" % peak_kib)

    # ---- the contract ---------------------------------------------------------------------------

    def test_the_flag_table_is_well_formed(self):
        # A `;` in a help text splits the table (it is `;`-separated): every flag is `--name`, of a known kind and role.
        import re
        d = json.loads(subprocess.run([str(self.out), "introspect"], capture_output=True, check=True).stdout)
        self.assertGreater(len(d["flags"]), 3)
        for f in d["flags"]:
            self.assertRegex(f["name"], r"^--[a-z][a-z0-9-]*$", f)
            self.assertRegex(f["kind"], r"^(bool|nat|hex64|text|any|path|choice:[a-z/]+)$", f)
            self.assertIn(f["role"], ["none", "path-read", "path-write", "root", "guard"], f)
            self.assertTrue(f["help"], f)

    def test_the_authority_row_names_the_baked_directory_and_nothing_wider(self):
        p = subprocess.run([str(self.out), "introspect"], capture_output=True, check=True)
        d = json.loads(p.stdout)
        labels = {(l["name"], l["argument"]) for l in d["authority"]["labels"]}
        self.assertIn(("exec", str(self.programs)), labels)
        self.assertTrue(d["authority"]["bounded"])
        self.assertEqual(d["authority"]["unbounded_by"], [])
        names = {n for n, _ in labels}
        self.assertEqual(names & {"net_out", "net_in", "ffi", "fs_write", "dir_write", "file_write"}, set())
        self.assertEqual(d["reversibility"], "irreversible-consequential")
        guaranteed = {k for k, v in d["guarantees"].items() if v is True}
        self.assertEqual(guaranteed, {"bounded_memory"})

    def test_the_default_build_names_the_default_directory(self):
        committed = json.loads((ROOT / "manifests" / "run.authority.json").read_text())
        labels = {(l["name"], l["argument"]) for l in committed["labels"]}
        self.assertIn(("exec", run_build.DEFAULT), labels)

    def test_the_directory_is_validated(self):
        for bad in ["relative/dir", "/", "/a/../b", '/a"b', "/a/./b"]:
            with self.assertRaises(SystemExit):
                run_build.build(bad, str(self.work / "never"))


if __name__ == "__main__":
    unittest.main()
