# An MCP server for the tools (#10): the design

> **Status: built** (`server/mcp.ls`, `scripts/mcp.py`,
> `tests/conformance/test_mcp.py`), on lex-sys edition 7 with
> `std.process` (lex-sys `docs/processes.md` §7.1, merged as lex-sys#276,
> `fe32ac2`; `lex-sys.toml` now pins `f8ebe98`, lex-sys#299, which only adds statics in
> packages). Every claim below was measured on
> macOS 26.2 arm64 and Linux 7.0 x86_64, unless it says otherwise. §8 is what
> building it found.

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
* `isError`: ~~true when the exit code is not 0~~ true when the exit code
  is neither 0 nor 9. **Corrected:** a completed `--dry-run` exits 9
  (`DRY_RUN`) with `"ok": true` (measured with `write --create --dry-run`),
  and is an answer, not a failure. The tool's own `error` record, with its
  rule and repair, is in the text.
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
reads standard input"), and `list`'s `DIR...`, meaning the root. Parsing prose for a schema would make a second
source of truth, so `introspect`'s operands gain `min` and `max` counts
(`PATH` 1/1, `FILE...` 1/unbounded for `seek`, 0/unbounded for `tally`),
written in the operand table `introspect` prints. That change comes first
(#16), with its own gate: for every tool and every count, the parser's
`args.missing-operand` and `args.too-many-operands` appear exactly where the
counts say (`test_operands.py`).

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
`--root`. ~~The row it should derive is `args`, `clock`, `err_write`,
`exec("<bin>")`, `heap`, `io_read`, `io_write` and `poll`.~~ **Measured**
(`lex-sys authority`): bounded, and `args`, `child_signal`, `clock`,
`err_write`, `exec("/opt/lexsys-tools/bin")`, `heap`, `io_read`,
`io_write`, `pipe_read`, `pipe_write` and `poll`. The three the design left
out are what it does with the children and channels it owns, so they reach
nothing beyond them. `test_mcp.py` holds the row to exactly this set.

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

## 8. What building it found

* **A newline inside a response.** A tool ends its document with `\n`, and
  `std.json`'s `put_fragment` splices what it is given, so the first
  `structuredContent` ended the client's line partway through the response.
  The fragment now stops before trailing whitespace, and every test reads the
  answers one line at a time.
* **`put_fragment` traps on anything that is not one value.** Tool output is
  parsed first, and only one object becomes `structuredContent`; anything
  else is text only. Three hundred seeded hostile requests (bytes, deep
  nesting, wrong types, NULs, unknown tools and keys) are each answered,
  with no trap, and the server answers the `ping` after them.
* **A local binding hides a qualified call.** `tools.name(k)` with a local
  `name` in scope, and `process.list(l)` with a local `list`, are refused as
  "a local binding, not a function". It is lex-sys's (found while building
  `std.process`), and the server names its locals otherwise.
* **The project pin.** `lex-sys build` refuses a compiler other than the one
  `lex-sys.toml` names, which is right. The pin moved to `fe32ac2` (#276
  merged): the eight tools' authorities and the generated definitions are
  unchanged under it, and only the embedded compiler revision moved.
* **Mutants.** Nine, one choice each undone: no flush, no `--` fence,
  any key accepted, `isError` on any non-zero exit, the fragment not
  trimmed, no `--root`, structured content for streams, a boolean's type
  unchecked, a NUL in a flag's value let through. The first run killed 7.
  *No flush* was killed only by a test added for it: every other test sends
  all its requests and closes the input, and the server's buffer is flushed
  at exit, so only a client that waits for each answer with the input still
  open sees an answer that never comes. The two survivors were tests that did
  not look: the NUL was only ever in an operand, not a flag's value; and a
  stream that is one line (`list` of an empty directory prints only `end`)
  is one JSON object, so only the `document` check keeps it text. With those
  cases, **9 of 9 are killed**.
* **Not the server's:** on a Linux whose `/tmp` is tmpfs, `peek` on a
  directory answers `io.read-failed` (`lseek(SEEK_END)` is `EINVAL` on a
  tmpfs directory) where `test_rules.py` expects `io.is-a-directory`. It
  fails the same way on `main`, and is reported separately.

## 9. Tried with Claude Code

One task, run once by each route, with Claude Code 2.1.289 headless
(`claude -p`) on Linux x86_64, the tools and server built at the pinned
compiler. It is a smoke test of the integration, not the agent-in-the-loop
evaluation the README does not claim (lex-sys#228).

**The task**, in a five-file project (two Python files holding three `TODO`
comments, `notes.txt`, `package.json`, `docs/README.md`): list every file,
find every `TODO` with its line, give the version in `package.json`, and
append a line to `notes.txt` "making sure you do not overwrite a change
someone else made since you read it". The prompt names no tool and no flag.

**The routes.**

* *MCP*: the server, given with `--mcp-config` and `--strict-mcp-config`,
  and Claude Code's own file tools and Bash disallowed, so the server was the
  only way to touch the files.
* *Skills*: the eight `SKILL.md` files that `<tool> skill` prints, placed in
  the project's `.claude/skills/`, the binaries on `PATH`, and Bash allowed
  for those eight binaries only.

| | MCP | Skills |
|---|---|---|
| Found | all 8 tools (`connected`) | all 8 skills |
| Files, `TODO`s, version | all correct | all correct |
| The guarded append | `peek`, `hash`, then `replace` with `if-sha256` | `peek`, `hash`, then `write --if-sha256 --stdin` |
| Turns, cost | 8, $0.08 | 21, $0.21 |

In both routes the model chose a write that is refused if the file changed,
from the tools' own descriptions: the prompt said what it wanted, not how.

**What it found.**

* **`hash` is a shell builtin.** Through Bash, Claude Code refuses
  `hash --root . notes.txt` before running it ("evaluates arguments as shell
  code"); the binary works by its absolute path. On a first skills run, with
  only bare names allowed, the model stopped before the append and said why,
  rather than reach for `sha256sum`; the run above allowed the absolute path.
  Over MCP the name is only an identifier and nothing is in the way. Two
  remedies are open: rename the binary (`digest`, say) and keep `hash` as
  the MCP tool's name, or keep it and have its `SKILL.md` say to call it by
  absolute path.
* **The skills route costs more turns**: each skill is loaded before use,
  and a Bash line that chains several commands is held for approval as a
  whole. Neither applies to MCP, which is the route to prefer for Claude
  Code; the skills remain for a runtime without MCP.

## 10. A refusal names the fix (found by the benchmark)

The server's own refusals were the one place the tools' principle, *an error names
a rule and says what to change*, did not hold. A bad argument was answered with "an
argument of the wrong type; the tool's inputSchema gives each one's": no property, no
expected type, no rule. In the agent benchmark (`docs/agent-bench.md` §10) the `mcp`
arm's commonest refusal was exactly that, 9 times on `replace`, 3 on `peek` and 2 on
`jsonq`, and the calls inspected were `llama3.1:8b` sending every value as a string
(`"expect": "1"`, `"dry-run": "false"`, even the defaults at their ceilings), several
wrong at once.

Now every refusal is `-32602` with a message that says which property, what the
schema wants, what arrived and, for the mistake made most, what to send, and a `data`
object an agent can branch on:

```
`expect` must be a non-negative integer, not a string; send a JSON number such as 1, not a string such as "1";
`dry-run` must be a boolean, not a string; send true or false, not a string
{"rule": "mcp.wrong-type", "problems": [{"property": "expect", "expected": "a non-negative integer", "got": "string"}, ...]}
```

* **All the type faults, in one answer.** A caller that sent three values as strings
  is told all three, not the first and then the second. (An unknown property is
  reported once the types are right.)
* **Rules:** `mcp.wrong-type`, `mcp.unknown-argument` (it lists the tool's real
  properties, so one retry is enough), `mcp.nul-in-argument`,
  `mcp.arguments-not-object`.
* **Strictness is unchanged.** `"1"` is still refused where an integer is declared:
  the server validates against the schema it published, as the specification
  requires, and says how to correct it. Coercing would make the schema a suggestion.
* **Checked** by `test_mcp.py`: the benchmark's own mistake, each kind of refusal
  and its rule, and four mutants (no hint, only the first fault, the wrong rule, an
  unnamed property), all killed.
* **Not measured yet:** whether models recover on the retry. That is the `mcp` arm
  rerun on the same models (the benchmark is paused, `docs/agent-bench.md` §10), and
  it is what would show whether a precise message changes a pass rate or only reads
  better.

