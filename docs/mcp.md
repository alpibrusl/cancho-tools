# An MCP server for the tools (#10): the design

> **Status: designed, not built.** It needs lex-sys edition 7 with
> `std.process` (lex-sys `docs/processes.md` §7.1, lex-sys#276), and
> `lex-sys.toml`'s pin moves to the commit that merges it. Every claim
> below was measured on lex-sys `processes-slice3-std-process` (`f4b45bb`),
> macOS 26.2 arm64, unless it says otherwise.

An agent runtime adopts tools most easily over MCP (#10). Every tool here
already describes itself (`introspect`, the schemas), so the server derives
its tool definitions from that and has no description of its own to drift.
The server is a lex-sys program, because what it may do should be what the
compiler derives, as for each tool: **start the eight binaries, under a
fixed `--root`, and nothing else.**

## 1. What a call becomes

A `tools/call` names a tool and gives `arguments`. The server turns it into
a fixed argv, runs the binary with `std.process.capture`, and answers with
what the binary printed.

```
tools/call {"name": "seek", "arguments": {"pattern": "TODO", "files": ["src/a.ls"], "max-count": 5}}
    -> <bin>/seek --root=<ROOT> --max-count=5 -- TODO src/a.ls
```

* **The binary is one of eight, by name.** The name is matched against the
  generated table (§3), and anything else is a JSON-RPC error (`-32602`,
  "unknown tool"), as the spec has it. No path from the model reaches
  `exec_spawn`.
* **`--root` is the server's, first, and once.** It comes from the
  server's own command line (`mcp --root DIR`) and is not in any input
  schema. The tools refuse a second `--root` (`args.duplicate-flag`, exit 2,
  measured), so even a flag the server failed to filter could not replace
  it.
* **A model's values are bound or fenced.** A flag's value is passed as one
  argument, `--name=value`, so it cannot become a flag of its own. Operands
  come after `--`, after which the tools take everything as an operand:
  `seek --root=/w -- --root=/ a.txt` searches `a.txt` for the text
  `--root=/` (0 matches, exit 0, measured).
* **Only flags whose role is `none` or `guard` are offered.** Flags with
  role `root` are the server's. `path-read` and `path-write` (for example
  `--content-file`) are offered, because they resolve beneath `--root`, which
  the tools already confine (M8). `--format` is not offered: the server
  always asks for the tool's JSON (or NDJSON).
* **Standard input is an argument.** Three tools can read standard input:
  `write --stdin` (the new content), `jsonq` with no `FILE` or `-`, and
  `tally` with no `FILE`. Their schemas have `stdin` (a string). The
  server sends it as `capture`'s `input`, and for `write` adds `--stdin`.
  Every other call sends empty input, so a tool that reads standard input
  unasked sees its end at once.

## 2. What comes back

Per the 2025-06-18 specification (`server/tools`), a tool's failure is a
result with `isError`, and a protocol failure is a JSON-RPC error.

* `content`: one `text` item holding the tool's standard output exactly,
  as a JSON string. #10 asks for "the tool's JSON unchanged".
* `structuredContent`: for the five tools whose output is one `document`,
  the same JSON as an object, and `outputSchema` in `tools/list` is that
  tool's `<tool>.v1` schema. The spec's "MUST conform" is met by M1, which
  validates every output against its schema. `seek`, `list` and `hash`
  print a `stream` (NDJSON), which is not one object, so they have text only.
* `isError`: true when the exit code is not 0. The tool's own `error`
  record, with its rule and repair, is in the text.
* `_meta`: `{"exit_code": n}`, because #10 asks for the exit code and the
  JSON alone does not carry it.
* When the server ends the child itself (`capture` answered `TimedOut`,
  `TooMuch` or `Failed`), the result is `isError` with an error record
  in the tools' own shape, rule `mcp.timeout`, `mcp.output-too-large` or
  `mcp.spawn`. The limits are the server's flags (`--timeout-ms`, default
  30000; `--max-output`, default 16 MiB).

## 3. Tool definitions are generated, not written

`introspect` gives each tool's flags (name, kind, role, default, help),
operands (name, role, help, `...` for one or more), `output` (`document`
or `stream`), summary and schema. At build time `scripts/mcp.py` turns that
into `generated/mcp/tools.ls`: the `tools/list` result as one literal, and
the table each call is checked against. `--check` fails when it differs
from a fresh derivation, as `manifest.py --check` does for authority.

| `introspect` | input schema |
|---|---|
| `kind: bool` | `{"type": "boolean"}`, passed as `--name` when true |
| `kind: nat` | `{"type": "integer", "minimum": 0}` |
| `kind: hex64` | `{"type": "string", "pattern": "^[0-9a-f]{64}$"}` |
| `kind: text`, `any`, `path` | `{"type": "string"}` (`text` with `minLength: 1`) |
| `kind: choice:a/b` | `{"enum": ["a", "b"]}` |
| an operand | a string, or an array of strings for `NAME...` |
| `help`, `default` | `description`, `default` |

**`introspect` has to say one thing it does not yet.** Whether an operand
is required is only in the `usage` prose: `jsonq`'s `FILE` and `tally`'s
`FILE...` may be left out, meaning standard input (`[FILE | -]`, "none
reads standard input"). Parsing prose for a schema would make a second
source of truth, so `introspect`'s operands gain `min` and `max` counts
(`PATH` 1/1, `FILE...` 1/unbounded for `seek`, 0/unbounded for `tally`),
taken from the same table the parser runs on (D11). That change comes first,
with its own gate: each tool's counts match what its parser accepts.

Properties are named as the flags are, without the dashes (`max-count`),
and operands in lower case (`pattern`, `files`). `additionalProperties` is
false, and the server refuses a property it does not know (`-32602`), so
the schema is the whole of what a model can pass.

## 4. The protocol, as much as it needs

* **stdio**, newline-delimited JSON-RPC 2.0. A line is read with
  `getchar` up to `\n` (lex-sys has no buffered read, and libc's buffer is
  underneath; `docs/standard-input.md`). A line over 4 MiB is refused,
  and nothing but responses goes to standard output.
* **`initialize`**: answers `protocolVersion` with the client's if it is
  `2025-06-18`, and `2025-06-18` otherwise (the spec's negotiation), with
  `capabilities: {"tools": {"listChanged": false}}` and `serverInfo`.
* **`notifications/initialized`** and other notifications: no answer, as
  a notification has none.
* **`ping`**: `{}`. **`tools/list`**: the generated result, with no
  pagination: eight tools fit in one page.
* **Anything else**: `-32601`. A line that is not JSON: `-32700` with
  `id: null`. One request at a time: a call blocks the next line until
  `capture` returns, which a stdio client already expects.

Requests are parsed with `std.json` (a tape, no tree) and answers written
with its `Writer`, which escapes the tool's output into the `text` string.

## 5. Its authority, and where the bound comes from

The server holds `Exec` narrowed to the directory of the eight binaries,
and no `Fs` or `Net`. It reads nothing itself; the tools read, under
`--root`. The row it should derive is `args`, `clock`, `err_write`,
`exec("<bin>")`, `heap`, `io_read`, `io_write` and `poll`. That is a
claim to measure when it is built (`lex-sys authority`), and to hold as a
ceiling in `tools.toml`'s style.

**The bound is baked in at build time.** `narrow` takes a literal ("`narrow`
takes a literal, so the refinement can be checked where it is written",
measured with an argument as the prefix). A library cannot take an `Exec`
of any prefix either (lex-sys `processes.md` §7.1). So the bound is a
source substitution, as D14's variant bakes `--root` into `seek`:
`scripts/mcp.py build --bin /opt/lexsys-tools/bin` writes the literal into
`main`, and the authority report of that binary says
`exec("/opt/lexsys-tools/bin")`. A deployment that moves the tools rebuilds
the server. That is the cost of a bound the compiler can see, and lex-sys
`processes.md` §9 keeps the general question open.

## 6. How it will be checked

#10's gates, plus the ones the design adds:

* For each tool, a call through the server gives, byte for byte, the
  CLI's output for the same argv, in `content[0].text`. For each document
  tool, `structuredContent` parses to the same value and validates against
  the schema.
* `--root` cannot be overridden: a `root` property is refused (`-32602`);
  an operand `--root=/` is a literal; a flag value `--root` is a value.
* A tool not on the list is refused, and so is a path in its name
  (`../seek`, `/bin/sh`).
* `write` with `stdin` replaces a file; a stale `--if-sha256` is
  `isError` with `precondition.hash-mismatch` and exit 5 in `_meta`.
* The limits: a call past `--timeout-ms` or `--max-output` ends as
  `mcp.timeout` or `mcp.output-too-large`, and the server keeps serving.
* The protocol: `initialize` negotiation, notifications unanswered,
  `-32601`, `-32700`, an over-long line, and end of input ends the server
  with exit 0.
* The authority: the derived row equals §5's, and `--check` holds for the
  generated definitions.
* Fault injection, as M4: seeded malformed and hostile requests, 0 traps.

## 7. Not in it

* `listChanged`, resources, prompts, sampling: the eight tools are fixed
  at build.
* HTTP transport: stdio is what a local agent runtime starts.
* Concurrency: one call at a time. Two calls racing on one file is what
  `write`'s guards are for, and they hold across processes (M7).
