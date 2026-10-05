#!/usr/bin/env python3
"""The MCP server's tool definitions, generated from `introspect` (docs/mcp.md §3).

    python3 scripts/mcp.py [--bin DIR]            # write generated/mcp/tools.ls
    python3 scripts/mcp.py --check                # fail if it is not current
    python3 scripts/mcp.py build --bin DIR --out FILE

The tools are built first (`lex-sys build`); each one's `introspect` is the
only description read. What it writes is one lex-sys module, `mcp.tools`: the
`tools/list` result as one literal, and for each tool the tables a call is
checked against -- the properties it may pass, the flag each becomes, its
operands with their counts, and how standard input reaches it.

The server holds `Exec` narrowed to the directory of the binaries, and
`narrow` takes a literal (docs/mcp.md §5), so the directory is in the source.
The committed module and server say `/opt/lexsys-tools/bin`; `build --bin`
writes both with another directory into a scratch copy and builds that, as
scripts/variant.py bakes `--root` (D14). Nothing a model sends reaches the
directory: it picks one of eight names.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import manifest  # noqa: E402

ROOT = manifest.ROOT
DEFAULT_BIN = "/opt/lexsys-tools/bin"
GENERATED = ROOT / "generated" / "mcp" / "tools.ls"
SERVER = ROOT / "server" / "mcp.ls"

# Flags the server sets or that would change what the answer is: `--root` is
# the server's (role `root`), and the server always asks for JSON.
WITHHELD_ROLES = {"root"}
WITHHELD_FLAGS = {"--format"}


def introspect(tool, build_dir):
    out = subprocess.run([str(pathlib.Path(build_dir) / tool), "introspect"],
                         check=True, capture_output=True).stdout
    return json.loads(out)


def property_of_operand(name):
    """`PATTERN` -> `pattern`, `FILE...` -> `files`."""
    base = name.rstrip(".").lower()
    return base + "s" if name.endswith("...") else base


def schema_of_flag(flag):
    kind = flag["kind"]
    if kind == "bool":
        s = {"type": "boolean"}
    elif kind == "nat":
        s = {"type": "integer", "minimum": 0}
    elif kind == "hex64":
        s = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    elif kind.startswith("choice:"):
        s = {"enum": kind[len("choice:"):].split("/")}
    elif kind in ("text", "any", "path"):
        s = {"type": "string"}
        if kind == "text":
            s["minLength"] = 1
    else:
        sys.exit("mcp: flag %s has a kind this script does not know: %s" % (flag["name"], kind))
    s["description"] = flag["help"]
    if flag["default"] is not None:
        s["default"] = int(flag["default"]) if kind == "nat" else flag["default"]
    return s


def reads_stdin(d):
    """How standard input reaches the tool: `flag` when it has `--stdin`,
    `implicit` when it holds `io_read` and reads it with no operand, or none."""
    if any(f["name"] == "--stdin" for f in d["flags"]):
        return "flag"
    if "io_read" in d["authority"]["effects"]:
        return "implicit"
    return ""


def definition(d):
    """One tool: its `tools/list` entry, and the tables the server checks a
    call against."""
    name = d["tool"]
    props, required, flags, operands = {}, [], [], []
    for f in d["flags"]:
        if f["role"] in WITHHELD_ROLES or f["name"] in WITHHELD_FLAGS or f["name"] == "--stdin":
            continue
        prop = f["name"][2:]
        props[prop] = schema_of_flag(f)
        flags.append((prop, f["name"], "bool" if f["kind"] == "bool" else "nat" if f["kind"] == "nat" else "text"))
    for o in d["operands"]:
        prop = property_of_operand(o["name"])
        if prop in props:
            sys.exit("mcp: %s's operand %s and a flag are both `%s`" % (name, o["name"], prop))
        if o["max"] == 1:
            s = {"type": "string", "description": o["help"]}
        else:
            s = {"type": "array", "items": {"type": "string"}, "description": o["help"]}
            if o["min"] > 0:
                s["minItems"] = o["min"]
            if o["max"] is not None:
                s["maxItems"] = o["max"]
        props[prop] = s
        if o["min"] > 0:
            required.append(prop)
        operands.append((prop, o["min"], "" if o["max"] is None else o["max"]))
    stdin = reads_stdin(d)
    if stdin:
        if "stdin" in props:
            sys.exit("mcp: %s already has a property `stdin`" % name)
        props["stdin"] = {"type": "string",
                          "description": "what the tool reads as standard input"
                                         + (" (sent with --stdin)" if stdin == "flag" else " when no file is named")}
    entry = {
        "name": name,
        "description": d["summary"],
        "inputSchema": {"type": "object", "properties": props, "required": required,
                        "additionalProperties": False},
    }
    if d["output"] == "document":
        entry["outputSchema"] = d["schemas"]["%s.v1" % name]
    for table in (flags, operands):
        for row in table:
            for field in row:
                if any(c in str(field) for c in ";|"):
                    sys.exit("mcp: %s: `%s` would split the table" % (name, field))
    return entry, {
        "name": name,
        "flags": ";".join("|".join(map(str, r)) for r in flags),
        "operands": ";".join("|".join(map(str, r)) for r in operands),
        "stdin": stdin,
        "output": d["output"],
    }


def generated(build_dir, bin_dir):
    tools = [b["name"] for b in manifest.project()["bin"]]
    entries, tables = zip(*(definition(introspect(t, build_dir)) for t in tools))
    lit = manifest.literal
    out = ["""edition 5;

module mcp.tools;

// Generated by scripts/mcp.py from each tool's `introspect` -- do not edit
// (docs/mcp.md §3). The `tools/list` result, and for tool `i` the tables a
// call is checked against: its properties as flags (`property|flag|kind`,
// kind `bool`, `nat` or `text`), its operands in order
// (`property|min|max`, `max` empty for unbounded), how standard input reaches
// it (`flag`, `implicit` or empty), and its output (`document` or `stream`).

pub fn list_result() -> [] &static [byte] {
    return %s;
}

pub fn count() -> [] int {
    return %d;
}
""" % (lit(manifest.compact({"tools": list(entries)})), len(tables))]
    for field in ("name", "flags", "operands", "stdin", "output"):
        out.append("\npub fn %s(i: int) -> [] &static [byte] {\n" % field)
        for i, t in enumerate(tables):
            out.append("    if i == %d {\n        return %s;\n    }\n" % (i, lit(t[field])))
        out.append('    return "";\n}\n')
    out.append("\n// The binary for tool `i`, beneath the directory the server's `Exec` is\n"
               "// narrowed to.\npub fn path(i: int) -> [] &static [byte] {\n")
    for i, t in enumerate(tables):
        out.append("    if i == %d {\n        return %s;\n    }\n" % (i, lit("%s/%s" % (bin_dir, t["name"]))))
    out.append('    return "";\n}\n')
    return "".join(out)


def check_bin(bin_dir):
    p = pathlib.PurePosixPath(bin_dir)
    if not bin_dir.startswith("/") or str(p) != bin_dir or bin_dir == "/" or any(c in bin_dir for c in '"\\\n'):
        sys.exit("mcp: --bin must be an absolute, normalised directory other than /")


def baked(text, bin_dir):
    """The server's source with its directory replaced: every `Exec(...)`,
    `exec(...)` and `narrow` names it."""
    return text.replace('"%s' % DEFAULT_BIN, '"%s' % bin_dir)


def build(build_dir, bin_dir, out):
    check_bin(bin_dir)
    with tempfile.TemporaryDirectory() as scratch:
        src = pathlib.Path(scratch)
        tools_ls = src / "tools.ls"
        tools_ls.write_text(generated(build_dir, bin_dir))
        server_ls = src / "mcp.ls"
        server_ls.write_text(baked(SERVER.read_text(), bin_dir))
        subprocess.run([manifest.compiler(), "build", str(server_ls), str(tools_ls), "--std", "-o", out],
                       check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", choices=["build"])
    ap.add_argument("--bin", default=None)
    ap.add_argument("--build-dir", default=str(ROOT / "build"))
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.command == "build":
        if not a.bin or not a.out:
            sys.exit("mcp: build needs --bin and --out")
        build(a.build_dir, a.bin, a.out)
        return
    text = generated(a.build_dir, a.bin or DEFAULT_BIN)
    if a.check:
        if not GENERATED.exists() or GENERATED.read_text() != text:
            sys.exit("mcp: generated/mcp/tools.ls is not what introspect says; run scripts/mcp.py")
        return
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(text)


if __name__ == "__main__":
    main()
