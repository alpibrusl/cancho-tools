"""The MCP server (#10, docs/mcp.md §6).

The server is built with `scripts/mcp.py build`, its directory baked to this
checkout's `build/`, and driven over stdio as a client would: one request per
line, one answer per line. #10's gates first -- each tool's answer is the
CLI's byte for byte, `--root` cannot be overridden, a tool not on the list is
refused -- then the limits, the protocol, the authority and hostile input.
"""

import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest

from harness import BIN, ROOT, Fixture, introspect, run_argv, schema, validate

sys.path.insert(0, str(ROOT / "scripts"))
import manifest  # noqa: E402
import mcp  # noqa: E402

BUILT = {}


def server():
    """The server binary, built once for the module."""
    if "path" not in BUILT:
        out = pathlib.Path(tempfile.mkdtemp(prefix="mcp-")) / "mcp"
        mcp.build(str(BIN), str(BIN.resolve()), str(out))
        BUILT["path"] = out
    return BUILT["path"]


def talk(root, lines, flags=(), timeout=120):
    """Send `lines` (each a request object, or bytes as they are) and answer
    `(responses, exit status)`."""
    data = b"".join((l if isinstance(l, bytes) else json.dumps(l).encode()) + b"\n" for l in lines)
    p = subprocess.run([str(server()), "--root", str(root), *flags], input=data, capture_output=True,
                       timeout=timeout)
    responses = [json.loads(x) for x in p.stdout.split(b"\n") if x]
    return responses, p.returncode


def call(name, arguments, n=1):
    return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


# One call per tool, and the argv the server must build for it (docs/mcp.md §1).
CASES = [
    ("seek", {"pattern": "gamma", "files": ["plain.txt", "crlf.txt"], "max-count": 2},
     ["--max-count=2", "--", "gamma", "plain.txt", "crlf.txt"]),
    ("list", {"dirs": ["sub"]}, ["--", "sub"]),
    ("peek", {"path": "plain.txt", "lines": "2:3"}, ["--lines=2:3", "--", "plain.txt"]),
    ("jsonq", {"file": "doc.json", "pointer": "/a/2/b"}, ["--pointer=/a/2/b", "--", "doc.json"]),
    ("tally", {"files": ["words.txt"]}, ["--", "words.txt"]),
    ("hash", {"paths": ["plain.txt"], "algo": "sha512"}, ["--algo=sha512", "--", "plain.txt"]),
    ("replace", {"path": "plain.txt", "old": "beta", "new": "BETA", "dry-run": True},
     ["--old=beta", "--new=BETA", "--dry-run", "--", "plain.txt"]),
    ("write", {"path": "fresh.txt", "create": True, "dry-run": True, "stdin": "new\n"},
     ["--create", "--dry-run", "--stdin", "--", "fresh.txt"]),
]


class Server(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    # ---- #10's gates ---------------------------------------------------------

    def test_each_tool_answers_what_its_cli_prints(self):
        for tool, arguments, argv in CASES:
            with self.subTest(tool=tool):
                [r], status = talk(self.fx.root, [call(tool, arguments)])
                self.assertEqual(status, 0)
                stdin = arguments.get("stdin", "").encode()
                cli = run_argv(tool, [str(BIN / tool), "--root=%s" % self.fx.root, *argv], stdin=stdin)
                self.assertEqual(validate(cli), [], cli)
                result = r["result"]
                self.assertEqual(result["content"][0]["text"].encode(), cli.stdout)
                self.assertEqual(result["_meta"]["exit_code"], cli.status)
                self.assertEqual(result["isError"], cli.status not in (0, 9))
                if introspect(tool)["output"] == "document":
                    self.assertEqual(result["structuredContent"], json.loads(cli.stdout))
                else:
                    self.assertNotIn("structuredContent", result)

    def test_root_cannot_be_overridden(self):
        # Not a property of any tool.
        [r], _ = talk(self.fx.root, [call("seek", {"pattern": "x", "files": ["a"], "root": "/"})])
        self.assertEqual(r["error"]["code"], -32602)
        # An operand is an operand, after `--`.
        [r], _ = talk(self.fx.root, [call("seek", {"pattern": "--root=/", "files": ["plain.txt"]})])
        end = json.loads(r["result"]["content"][0]["text"].splitlines()[-1])
        self.assertEqual((end["type"], end["files"], end["matches"]), ("end", 1, 0))
        # A flag's value is a value: `--content-file=--root` names a file.
        [r], _ = talk(self.fx.root, [call("write", {"path": "fresh.txt", "create": True, "content-file": "--root"})])
        self.assertEqual(r["result"]["structuredContent"]["error"]["rule"], "io.not-found")
        # And a file outside the root is still outside it.
        [r], _ = talk(self.fx.root, [call("peek", {"path": "../outside/secret.txt"})])
        self.assertEqual(r["result"]["structuredContent"]["error"]["rule"], "path.dotdot")

    def test_a_tool_not_on_the_list_is_refused(self):
        for name in ["sh", "../seek", "/bin/sh", "seek/../../bin/sh", "", "SEEK"]:
            [r], _ = talk(self.fx.root, [call(name, {})])
            self.assertEqual(r["error"]["code"], -32602, name)

    def test_tools_list_is_what_introspect_says(self):
        [r], _ = talk(self.fx.root, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        listed = {t["name"]: t for t in r["result"]["tools"]}
        self.assertEqual(sorted(listed), sorted(b["name"] for b in manifest.project()["bin"]))
        for name, t in listed.items():
            props = t["inputSchema"]["properties"]
            self.assertNotIn("root", props, name)
            self.assertNotIn("format", props, name)
            self.assertFalse(t["inputSchema"]["additionalProperties"])
            d = introspect(name)
            self.assertEqual(t["description"], d["summary"])
            if d["output"] == "document":
                self.assertEqual(t["outputSchema"], schema(name))
            else:
                self.assertNotIn("outputSchema", t)
            required = {mcp.property_of_operand(o["name"]) for o in d["operands"] if o["min"] > 0}
            self.assertEqual(set(t["inputSchema"]["required"]), required, name)

    # ---- the writers through the server --------------------------------------

    def test_write_with_stdin_and_a_stale_hash(self):
        [r], _ = talk(self.fx.root, [call("write", {"path": "notes.txt", "create": True, "stdin": "one\n"})])
        self.assertFalse(r["result"]["isError"])
        self.assertEqual((self.fx.root / "notes.txt").read_bytes(), b"one\n")
        stale = "0" * 64
        [r], _ = talk(self.fx.root, [call("write", {"path": "notes.txt", "if-sha256": stale, "stdin": "two\n"})])
        self.assertTrue(r["result"]["isError"])
        self.assertEqual(r["result"]["_meta"]["exit_code"], 5)
        self.assertEqual(r["result"]["structuredContent"]["error"]["rule"], "precondition.hash-mismatch")
        self.assertEqual((self.fx.root / "notes.txt").read_bytes(), b"one\n")

    def test_arguments_of_the_wrong_kind_are_refused(self):
        for arguments in [{"pattern": 1, "files": ["a"]}, {"pattern": "a", "files": "a"},
                          {"pattern": "a", "files": [1]}, {"pattern": "a", "files": ["a"], "max-count": -1},
                          {"pattern": "a", "files": ["a"], "max-count": "2"},
                          {"pattern": "a", "files": ["a"], "require-match": "yes"},
                          {"pattern": "a\u0000--root=/", "files": ["a"]},
                          {"pattern": "a", "files": ["a"], "max-line-bytes": 10, "skip": 0, "require-match": True,
                           "max-count": 1, "ascii-case-insensitive": True, "x\u0000": 1}]:
            [r], _ = talk(self.fx.root, [call("seek", arguments)])
            self.assertEqual(r["error"]["code"], -32602, arguments)
        [r], _ = talk(self.fx.root, [call("seek", ["a"])])
        self.assertEqual(r["error"]["code"], -32602)
        # A NUL in a flag's value, not only in an operand.
        [r], _ = talk(self.fx.root, [call("peek", {"path": "plain.txt", "lines": "1:2\u0000--root=/"})])
        self.assertEqual(r["error"]["code"], -32602)

    def test_a_refusal_names_every_fault_and_the_fix(self):
        """The mistake small models made most (docs/agent-bench.md §10): numbers and
        booleans sent as strings, several at once. The answer says which, what was wanted,
        what arrived and what to send, for all of them, with the rule and each property in
        `data`, as the tools' own errors do."""
        sent = {"path": "notes.txt", "old": "a", "new": "b", "expect": "1", "dry-run": "false",
                "max-bytes": "1073741824", "max-diff-lines": "100000"}
        [r], _ = talk(self.fx.root, [call("replace", sent)])
        error = r["error"]
        self.assertEqual(error["code"], -32602)
        self.assertEqual(error["data"]["rule"], "mcp.wrong-type")
        self.assertEqual([p["property"] for p in error["data"]["problems"]], ["expect", "dry-run", "max-bytes", "max-diff-lines"])
        self.assertEqual(error["data"]["problems"][1], {"property": "dry-run", "expected": "a boolean", "got": "string"})
        for fragment in ["`expect` must be a non-negative integer, not a string", "send a JSON number such as 1",
                         "`dry-run` must be a boolean, not a string", "send true or false", "`max-bytes`", "`max-diff-lines`"]:
            self.assertIn(fragment, error["message"])
        # Nothing was run: a refusal is not a call.
        self.assertFalse((self.fx.root / "notes.txt").exists())

    def test_each_kind_of_refusal_has_its_rule_and_names_the_property(self):
        cases = [
            ("seek", {"pattern": "x", "files": ["a"], "root": "/"}, "mcp.unknown-argument", "`root` is not a property of seek"),
            ("seek", {"pattern": "x", "files": ["a", 2]}, "mcp.wrong-type", "`files` must be an array of strings, but an item is a number"),
            ("seek", {"pattern": "x", "files": "a"}, "mcp.wrong-type", "`files` must be an array of strings, not a string"),
            ("seek", {"pattern": "x", "files": ["a"], "max-count": -1}, "mcp.wrong-type", "negative or fractional"),
            ("seek", {"pattern": "a\u0000b", "files": ["a"]}, "mcp.nul-in-argument", "`pattern` contains a NUL byte"),
            ("seek", {"pattern": "x", "files": ["a\u0000b"]}, "mcp.nul-in-argument", "`files` contains a NUL byte"),
            ("seek", ["a"], "mcp.arguments-not-object", "`arguments` must be an object"),
            ("write", {"path": "n", "create": True, "stdin": 5}, "mcp.wrong-type", "`stdin` must be a string, not a number"),
        ]
        for tool, arguments, rule, fragment in cases:
            with self.subTest(arguments=arguments):
                [r], _ = talk(self.fx.root, [call(tool, arguments)])
                self.assertEqual(r["error"]["code"], -32602)
                self.assertEqual(r["error"]["data"]["rule"], rule)
                self.assertIn(fragment, r["error"]["message"])
        # An unknown property lists the real ones, so one retry is enough.
        [r], _ = talk(self.fx.root, [call("seek", {"pattern": "x", "files": ["a"], "root": "/"})])
        self.assertIn("max-count", r["error"]["message"])
        self.assertEqual(r["error"]["data"]["property"], "root")

    def test_a_stream_is_text_even_when_it_is_one_line(self):
        # `list` of an empty directory prints one record, `end`: one JSON
        # object, and still a stream, so it is not structured content.
        (self.fx.root / "empty").mkdir()
        [r], _ = talk(self.fx.root, [call("list", {"dirs": ["empty"]})])
        cli = run_argv("list", [str(BIN / "list"), "--root=%s" % self.fx.root, "--", "empty"])
        self.assertEqual(cli.stdout.count(b"\n"), 1)
        self.assertEqual(r["result"]["content"][0]["text"].encode(), cli.stdout)
        self.assertNotIn("structuredContent", r["result"])

    # ---- limits ---------------------------------------------------------------

    def test_the_limits_end_a_call_and_the_server_goes_on(self):
        big = self.fx.root / "big.txt"
        big.write_bytes(b"gamma gamma gamma\n" * 2_000_000)
        responses, status = talk(self.fx.root, [
            call("seek", {"pattern": "gamma", "files": ["big.txt"]}, 1),
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ], flags=["--max-output", "1000"])
        self.assertEqual(status, 0)
        self.assertEqual([r["id"] for r in responses], [1, 2])
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertIn("mcp.output-too-large", responses[0]["result"]["content"][0]["text"])
        responses, _ = talk(self.fx.root, [
            call("hash", {"paths": ["big.txt"] * 40}, 1),
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ], flags=["--timeout-ms", "1"])
        self.assertIn("mcp.timeout", responses[0]["result"]["content"][0]["text"])
        self.assertEqual(responses[1]["result"], {})

    # ---- the protocol ---------------------------------------------------------

    def test_the_protocol(self):
        responses, status = talk(self.fx.root, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}},
            {"jsonrpc": "2.0", "id": "two", "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
            b"not json",
            b'{"id": 4, "method": "ping"}',
            b"x" * (4 * 1024 * 1024 + 10),
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ])
        self.assertEqual(status, 0, "the end of input ends the server")
        self.assertEqual([r.get("id") for r in responses], [1, "two", 3, None, 4, None, 5])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(responses[0]["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(responses[1]["result"], {})
        self.assertEqual([r["error"]["code"] for r in responses[2:6]], [-32601, -32700, -32600, -32600])

    def test_each_answer_arrives_before_the_next_request(self):
        # A client waits for an answer before it asks again, with the input
        # still open: an answer left in the server's buffer never comes.
        import select
        p = subprocess.Popen([str(server()), "--root", str(self.fx.root)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE)
        try:
            for n, request in enumerate([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                         call("peek", {"path": "plain.txt"}, 2)]):
                p.stdin.write(json.dumps(request).encode() + b"\n")
                p.stdin.flush()
                ready, _, _ = select.select([p.stdout], [], [], 10)
                self.assertTrue(ready, "no answer to request %d while the input is open" % (n + 1))
                self.assertEqual(json.loads(p.stdout.readline())["id"], n + 1)
        finally:
            p.stdin.close()
            p.wait(timeout=10)
            p.stdout.close()

    def test_a_bad_command_line_is_refused(self):
        for flags in [[], ["--root", "relative"], ["--root"], ["--root", "/", "--timeout-ms", "0"],
                      ["--root", "/", "--max-output", "x"], ["--root", "/", "--other", "1"]]:
            p = subprocess.run([str(server()), *flags], input=b"", capture_output=True)
            self.assertEqual(p.returncode, 2, flags)
            self.assertEqual(p.stdout, b"", flags)
            self.assertTrue(p.stderr.startswith(b"mcp: "), flags)

    # ---- authority ------------------------------------------------------------

    def test_the_authority_is_the_bin_directory_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as scratch:
            src = pathlib.Path(scratch)
            (src / "tools.ls").write_text(mcp.generated(str(BIN), mcp.DEFAULT_BIN))
            (src / "mcp.ls").write_text(mcp.SERVER.read_text())
            out = subprocess.run([manifest.compiler(), "authority", str(src / "mcp.ls"), str(src / "tools.ls"),
                                  "--std", "--output", "json"], check=True, capture_output=True).stdout
        d = json.loads(out)
        self.assertTrue(d["bounded"])
        labels = {(l["name"], l["argument"]) for l in d["labels"]}
        self.assertEqual(labels, {("args", None), ("child_signal", None), ("clock", None), ("err_write", None),
                                  ("exec", mcp.DEFAULT_BIN), ("heap", None), ("io_read", None),
                                  ("io_write", None), ("pipe_read", None), ("pipe_write", None), ("poll", None)})

    # ---- hostile input --------------------------------------------------------

    def test_seeded_hostile_requests_never_trap(self):
        rnd = random.Random(10)
        names = ["seek", "write", "peek", "nope", "", None, 7]
        values = [None, True, 0, -1, 2 ** 70, 1.5, "", "x" * 300, "--root=/", "\u0000", [], {}, ["a", 1], {"a": []}]
        lines = []
        for n in range(300):
            kind = rnd.randrange(5)
            if kind == 0:
                lines.append(bytes(rnd.randrange(256) for _ in range(rnd.randrange(40))).replace(b"\n", b" "))
            elif kind == 1:
                lines.append(b"[" * rnd.randrange(1, 300))
            else:
                arguments = {rnd.choice(["pattern", "files", "path", "stdin", "max-count", "root", "x"]): rnd.choice(values)
                             for _ in range(rnd.randrange(4))}
                lines.append({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                              "params": {"name": rnd.choice(names), "arguments": rnd.choice([arguments, None, "a"])}})
        lines.append({"jsonrpc": "2.0", "id": "last", "method": "ping"})
        responses, status = talk(self.fx.root, lines)
        self.assertEqual(status, 0)
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "last", "result": {}})
        for r in responses:
            self.assertEqual(r["jsonrpc"], "2.0")
            self.assertTrue(("result" in r) != ("error" in r), r)


class Generated(unittest.TestCase):
    def test_the_definitions_are_current(self):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp.py"), "--check", "--build-dir", str(BIN)],
                           capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
