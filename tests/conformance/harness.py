"""Shared pieces of the offline gates (lex-sys docs/agent-toolbox.md §7.1).

The binaries are build/<tool>, built by `lex-sys build` before the tests run
(TOOLBOX_BIN overrides the directory). Every test runs a real process and
judges it from outside: exit status, the bytes on standard output, the files
on disk.
"""

import hashlib
import json
import os
import pathlib
import random
import shutil
import subprocess
import tempfile

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build"))
TOOLS = ["seek", "write", "replace", "peek", "jsonq", "tally", "hash", "list"]
STREAMS = {"seek", "hash", "list"}
TRAPS = {132, 134, 136, 139, -4, -6, -8, -11}

_schemas = {}
_introspect = {}


def binary(tool):
    return str(BIN / tool)


class Result:
    def __init__(self, tool, argv, status, stdout, stderr):
        self.tool = tool
        self.argv = argv
        self.status = status
        self.stdout = stdout
        self.stderr = stderr

    def lines(self):
        return [json.loads(l) for l in self.stdout.decode("utf-8").splitlines()]

    def doc(self):
        lines = self.lines()
        assert len(lines) == 1, "a document tool writes one line: %r" % self.stdout[:400]
        return lines[0]

    def errors(self):
        """Every error, in order, from either shape of output."""
        if self.tool in STREAMS:
            return [r["error"] for r in self.lines() if r.get("type") == "error"]
        d = self.doc()
        return d.get("errors", [])

    def first_rule(self):
        errs = self.errors()
        return errs[0]["rule"] if errs else None

    def __repr__(self):
        return "<%s %r -> %d %r>" % (self.tool, self.argv[1:], self.status, self.stdout[:300])


def run(tool, *args, stdin=b"", cwd=None, env=None, timeout=120):
    argv = [binary(tool), *[str(a) for a in args]]
    p = subprocess.run(argv, input=stdin, capture_output=True, cwd=cwd, env=env, timeout=timeout)
    return Result(tool, argv, p.returncode, p.stdout, p.stderr)


def run_argv(tool, argv, stdin=b"", cwd=None, timeout=120):
    p = subprocess.run(argv, input=stdin, capture_output=True, cwd=cwd, timeout=timeout)
    return Result(tool, argv, p.returncode, p.stdout, p.stderr)


def schema(tool):
    if tool not in _schemas:
        _schemas[tool] = json.loads((ROOT / "schemas" / ("%s.v1.json" % tool)).read_text())
    return _schemas[tool]


def introspect(tool):
    if tool not in _introspect:
        p = subprocess.run([binary(tool), "introspect"], capture_output=True, check=True)
        _introspect[tool] = json.loads(p.stdout)
    return _introspect[tool]


def validate(result):
    """M1 for one invocation: the output is valid against the tool's schema,
    the status is in its declared table, and a stream ends with its end
    record. Answers a list of problems (empty when it conforms)."""
    problems = []
    tool = result.tool
    if result.status in TRAPS or result.status < 0:
        return ["trapped: status %d" % result.status]
    declared = {c["code"] for c in introspect(tool)["exit_codes"]}
    if result.status not in declared:
        problems.append("status %d not in the declared table %s" % (result.status, sorted(declared)))
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return problems + ["stdout is not UTF-8"]
    if not text.endswith("\n"):
        problems.append("output does not end with a newline")
    lines = text.splitlines()
    if tool not in STREAMS and len(lines) != 1:
        problems.append("a document tool wrote %d lines" % len(lines))
    validator = jsonschema.Draft202012Validator(schema(tool))
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError as e:
            problems.append("not JSON: %s" % e)
            continue
        for err in validator.iter_errors(value):
            problems.append("schema: %s at %s" % (err.message[:200], list(err.absolute_path)))
    if tool in STREAMS and lines:
        last = json.loads(lines[-1])
        if last.get("type") != "end":
            problems.append("the stream does not end with an end record")
        else:
            n_errors = sum(1 for l in lines if json.loads(l).get("type") == "error")
            if last["ok"] != (n_errors == 0):
                problems.append("end.ok disagrees with the error records")
            if last["complete"] and n_errors:
                problems.append("complete:true after an error")
    if tool not in STREAMS and len(lines) == 1:
        d = json.loads(lines[0])
        if d.get("ok") is False and result.status == 0:
            problems.append("ok:false with exit 0")
        if d.get("ok") is True and result.status not in (0, 9):
            problems.append("ok:true with exit %d" % result.status)
        if d.get("errors"):
            first = d["errors"][0]
            if d["error"] != first:
                problems.append("error is not errors[0]")
    if result.stderr and tool not in STREAMS and "--format" not in " ".join(result.argv):
        problems.append("stderr is not empty in JSON mode: %r" % result.stderr[:200])
    return problems


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    """A scratch directory with the edge-case corpus of §7.1 M5."""

    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="toolbox-"))
        self.root = self.dir / "root"
        self.root.mkdir()
        (self.root / "sub").mkdir()
        files = {
            "plain.txt": b"alpha\nbeta gamma\nGAMMA delta\nepsilon\n",
            "crlf.txt": b"one\r\ntwo gamma\r\nthree\r\n",
            "no-newline.txt": b"first\nlast gamma",
            "nul.bin": b"head\x00gamma\nnext\x00\n",
            "latin1.txt": b"caf\xe9 gamma\n\xff\xfe\n",
            "empty.txt": b"",
            "long.txt": b"short\n" + b"g" * 5000 + b" gamma\nend\n",
            "doc.json": b'{"a":[1,2.50,{"b":"x\\u00e9"}],"c~d":{"e/f":null},"t":true,"s":"text"}',
            "bad.json": b'{"a":1,}',
            "words.txt": b"b\na\nb\nc\na\nb\n",
            "fields.csv": b"x,1\ny,2\nx,3\nnofield\n",
            "sub/inner.txt": b"inner gamma\n",
        }
        for name, data in files.items():
            (self.root / name).write_bytes(data)
        outside = self.dir / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret gamma outside\n")
        os.symlink(outside / "secret.txt", self.root / "link.txt")
        os.symlink(outside, self.root / "dirlink")

    def path(self, name):
        return self.root / name

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def rng(seed=1):
    return random.Random(seed)
