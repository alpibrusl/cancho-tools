# lexsys-tools

Seven small command-line tools for AI agents, written in
[lex-sys](https://github.com/alpibrusl/lex-sys). They do the everyday work
of `grep`, `sed -n`, `jq`, `sort | uniq -c`, `sha256sum` and a careful
`cp`, but every answer is JSON, every error names a rule and often comes
with a command that fixes it, and every binary carries the exact authority
the compiler proved it needs.

| Tool | Instead of | Does |
|---|---|---|
| [`seek`](#seek-search) | `grep -F -n -b` | literal search over files, a stream of matches |
| [`peek`](#peek-read-part-of-a-file) | `sed -n`, `head`, `tail`, `wc -l` | a range of lines or bytes, with numbers and a cursor |
| [`jsonq`](#jsonq-one-value-from-a-json-document) | `jq -c` | one value from one strict JSON document, by RFC 6901 pointer |
| [`tally`](#tally-count-distinct-lines-or-fields) | `sort \| uniq -c \| sort -rn` | distinct lines or fields, counted and ranked |
| [`hash`](#hash-sha-256-or-sha-512) | `sha256sum`, `sha512sum` | file digests of any size, `--verify` |
| [`write`](#write-replace-a-file-only-if-it-is-what-you-think) | `cp`, `>`, `tee` | replace a file atomically, only if it holds what the caller says |
| [`replace`](#replace-change-exact-text) | `sed -i` | replace exact text an expected number of times, atomically |

**Site:** <https://alpibrusl.github.io/lexsys-tools/> has one page per tool,
generated from the tool itself: flags, rules, exit codes, authority, JSON
schemas and a ready-made `SKILL.md`.

## Why an agent would use these instead

`grep`, `sed` and `jq` were made for a person at a terminal, who reads the
prose, notices when something is off and retries. An agent reads text it
has to parse, often cannot see stderr, and has to decide its next step from
what came back. Each difference below is about that gap:

| An agent with the classic tool… | With lexsys-tools |
|---|---|
| **parses text.** `grep` prints `path:line:text`, which is ambiguous when a file name holds a `:`; a match in a binary file becomes the sentence `binary file matches`; bytes that are not UTF-8 reach the model raw or replaced. | **reads records with a schema.** Every output is JSON against a published schema (`seek.v1` …). A path, a line number and a byte offset are fields. Binary content is flagged and still searched. Bytes that are not UTF-8 come back as `{"b64": …}`, so nothing is lost. |
| **cannot tell a short answer from a cut one.** Output that stopped because of a pipe, a timeout or a kill looks like output that ended. | **knows when it has everything.** A stream ends with an `end` record, so a stream without one was cut short. A capped answer says `truncated: true` and gives the `next` cursor (`--skip`, `next.line`) to continue exactly where it stopped. |
| **guesses from exit codes and English.** `grep` exits 2 when one of three files is missing even though it printed matches; `jq` answers `Cannot index number with number`, exit 5. | **branches on a rule.** Each error is `{code, rule, message, hint, repair, detail}`, with 37 stable rule tags (`io.not-found`, `path.dotdot`, `precondition.hash-mismatch` …) and exit codes with one meaning each. One bad file is an error record in the stream, and the other files are still searched. |
| **has to work out the fix.** | **is often handed the fix.** When the correction is mechanical, `repair` is an argv to run as it stands, and a repair never widens what the tool may touch. |
| **overwrites blindly.** `sed -i`, `>` and `cp` replace whatever is there, even if the file changed since the agent read it, or another agent is writing it. | **states what it expects.** `write` needs `--create` or `--if-sha256` of the content it read. `replace` needs the text to occur exactly `--expect` times. A stale view is `precondition.hash-mismatch`, not a lost edit. Writes are atomic and locked (of two racing writers exactly one wins), idempotent (a retried write that already landed answers `changed: false`), and `--dry-run` previews the change without making it. |
| **can be led out of its workspace.** A path with `..`, an absolute path or a symbolic link reaches anything the process can. | **stays under `--root`.** `..`, absolute and outside paths are refused before anything is opened, and no link below the root is followed (`path.symlink`). |
| **gets different answers in different places.** `sort` follows the locale; output can change with `LANG`, the terminal or the clock. | **gets the same bytes every time.** No locale, no clock, no environment: the same input is byte-identical through a pipe, a file or a terminal. |
| **can blow its memory or its context.** A huge line or file goes straight through. | **has caps and is told about them.** Memory is bounded (about 1.7 MB at 256 MiB of input), and every cap is a flag with a ceiling and its own rule, such as `limit.line-too-long`, whose repair raises the cap. |
| **learns the tool from `--help` prose**, or from memory of some version of it. | **asks the tool.** `introspect` gives the flags, rules, exit codes, limits and schema as JSON, and its `guarantees` (`deterministic`, `idempotent`, `atomic`, `requires_precondition`, `dry_run`, `bounded_memory`, `concurrency`) as booleans an agent can branch on, each tied to the gate that tests it. `skill` gives a `SKILL.md`. Both are generated from the tables the parser runs on, so they cannot drift. |
| **has to trust the binary.** | **can check what it may do.** Each binary embeds its authority as the compiler derived it: no network, no foreign code, no clock, and no write except beneath a directory it opened. A sandbox can be configured from it. |

### Two agents, one file

The difference in one example: two agents both read `notes.txt` and each adds a line.

<!-- demo-race:begin (generated by scripts/demo_race.py; do not edit) -->

**With plain writes**, the second write wins and agent A's line is gone. Nobody is told:

```console
$ # A and B both read notes.txt, then each writes back what it read plus its own line
$ printf 'shopping list\n- milk (agent A)\n'  > notes.txt   # agent A
$ printf 'shopping list\n- bread (agent B)\n' > notes.txt   # agent B
$ cat notes.txt
shopping list
- bread (agent B)
```

**With `write --if-sha256`**, the stale write is refused, and the refusal carries what the agent needs to retry:

```console
$ hash --root . notes.txt        # A and B both read it, and both see H1
{"type":"hash","path":"notes.txt","algo":"sha256","hex":"936bb8ff57a0…","bytes":14}
$ printf 'shopping list\n- milk (agent A)\n' | write --root . --if-sha256 H1 --stdin notes.txt   # agent A
{"ok":true,"command":"write","schema":"write.v1","data":{"path":"notes.txt","changed":true,"created":false,"bytes":31,"before_sha256":"936bb8ff57a0…","after_sha256":"7bde7db5f30a…"},"meta":{"version":"0.1.0"}}
$ printf 'shopping list\n- bread (agent B)\n' | write --root . --if-sha256 H1 --stdin notes.txt   # agent B, stale
{"ok":false,"command":"write","schema":"write.v1","error":{"code":"CONFLICT","rule":"precondition.hash-mismatch","message":"the file does not hold the content --if-sha256 named","hint":"re-read the file, then decide","repair":{"kind":"none","reason":"the file changed since it was read; re-read, then decide"},"detail":{"path":"notes.txt","expected_sha256":"936bb8ff57a0…","actual_sha256":"7bde7db5f30a…","size":31}},"errors":[{"code":"CONFLICT","rule":"precondition.hash-mismatch","message":"the file does not hold the content --if-sha256 named","hint":"re-read the file, then decide","repair":{"kind":"none","reason":"the file changed since it was read; re-read, then decide"},"detail":{"path":"notes.txt","expected_sha256":"936bb8ff57a0…","actual_sha256":"7bde7db5f30a…","size":31}}],"meta":{"version":"0.1.0"}}
$ echo $?
5
$ # agent B re-reads (the refusal carried the new hash, H2), re-applies its line, and retries
$ printf 'shopping list\n- milk (agent A)\n- bread (agent B)\n' | write --root . --if-sha256 H2 --stdin notes.txt
{"ok":true,"command":"write","schema":"write.v1","data":{"path":"notes.txt","changed":true,"created":false,"bytes":49,"before_sha256":"7bde7db5f30a…","after_sha256":"60e7843a980c…"},"meta":{"version":"0.1.0"}}
$ cat notes.txt
shopping list
- milk (agent A)
- bread (agent B)
```

<!-- demo-race:end -->

What is **not** claimed: that agents finish more tasks, or finish them
faster, with these tools. That needs an agent-in-the-loop evaluation
([lex-sys#228](https://github.com/alpibrusl/lex-sys/issues/228)) that has
not been run. Everything in the table is a property the conformance gates
check on every commit.

## Quick start

The tools are built by the lex-sys compiler at the commit pinned in
[`lex-sys.toml`](lex-sys.toml). You need Rust (for the compiler) and `clang`.

```sh
# 1. The compiler, at the pinned commit
git clone https://github.com/alpibrusl/lex-sys
git clone https://github.com/alpibrusl/lexsys-tools
cd lex-sys
git checkout "$(sed -n 's/^lex-sys *= *"\([0-9a-f]*\)".*/\1/p' ../lexsys-tools/lex-sys.toml)"
cargo build --release -p lex-sys
export PATH="$PWD/target/release:$PATH"

# 2. The tools
cd ../lexsys-tools
lex-sys build            # every tool into build/
export PATH="$PWD/build:$PATH"

# 3. Try one
seek --root . --format text 'fn main' tools/seek/seek.ls
```

Every tool describes itself:

```sh
seek introspect          # flags and roles, exit codes, rules, limits, schema, authority, guarantees (JSON)
seek skill               # a SKILL.md an agent can load
```

## Using the tools

The examples run in a small project (`src/main.rs`, `package.json`,
`access.log`) and pass `--root .`, which every tool takes: paths are then
relative to the root, and nothing outside it can be reached. Output is as
the tools print it, with long hashes shortened and some fields elided (`…`).

### `seek`: search

```console
$ seek --root . add src/main.rs
{"type":"match","path":"src/main.rs","line":2,"offset":12,"text":"    let total = add(2, 3);"}
{"type":"match","path":"src/main.rs","line":6,"offset":69,"text":"fn add(a: i32, b: i32) -> i32 {"}
{"type":"file","path":"src/main.rs","matches":2,"bytes":113,"binary":false,"complete":true}
{"type":"end","ok":true,"command":"seek","schema":"seek.v1","complete":true,"files":1,"matches":2,"errors":0,"truncated":false,"next":null}

$ seek --root . --format text add src/main.rs
src/main.rs:2:    let total = add(2, 3);
src/main.rs:6:fn add(a: i32, b: i32) -> i32 {
```

The pattern is literal bytes, not a regular expression. A stream always ends
with an `end` record; **a stream without one was cut short**. Results are
paged with `--max-count` (`-m`) and resumed from the `next` it returns:

```console
$ seek --root . -m 1 add src/main.rs | tail -1
{"type":"end",…,"truncated":true,"next":{"skip":1}}
$ seek --root . -m 1 --skip 1 add src/main.rs | head -1
{"type":"match","path":"src/main.rs","line":6,"offset":69,"text":"fn add(a: i32, b: i32) -> i32 {"}
```

Other flags:
* `-i` ignores ASCII case.
* `--require-match` makes no match an error (exit 8).
* `--format text` prints `path:line:text` for a person; it is lossy, so do not parse it.

### `peek`: read part of a file

```console
$ peek --root . --lines 2:3 src/main.rs
{"ok":true,"command":"peek","schema":"peek.v1","data":{"path":"src/main.rs","size":113,"binary":false,"mode":"lines","from":2,"to":3,
 "lines":[{"n":2,"offset":12,"text":"    let total = add(2, 3);"},{"n":3,"offset":39,"text":"    println!(\"{}\", total);"}],
 "eof":false,"truncated":false,"next":{"line":4},"total_lines":null},"meta":{"version":"0.1.0"}}

$ peek --root . -n 2:3 --format text src/main.rs
2	    let total = add(2, 3);
3	    println!("{}", total);
```

`--bytes A:B` (`-c`) reads a byte range instead, `--count-lines` fills in
`total_lines`, and `next` says where to continue.

### `jsonq`: one value from a JSON document

```console
$ jsonq --root . -p /deps/serde package.json
{"ok":true,"command":"jsonq","schema":"jsonq.v1","data":{"pointer":"/deps/serde","kind":"string","value":"1.0"},"meta":{"version":"0.1.0"}}

$ jsonq --root . --keys -p /deps package.json
{"ok":true,"command":"jsonq","schema":"jsonq.v1","data":{"pointer":"/deps","kind":"object","keys":["serde","rand"]},"meta":{"version":"0.1.0"}}
```

Other flags:
* `--type`, `--length` and `--exists` answer just those.
* A pointer that names nothing is `query.no-such-path` (exit 3).
* A jq-style filter such as `.a[0]` is refused as `query.unsupported-syntax`, with a hint pointing to `jq`.
* With no file, `jsonq` reads standard input. The document must be strict JSON.

### `tally`: count distinct lines or fields

```console
$ tally --root . --field 3 --delim ' ' access.log
{"ok":true,"command":"tally","schema":"tally.v1","data":{"total":5,"distinct":3,"skipped":0,
 "top":[{"key":"200","count":3},{"key":"404","count":1},{"key":"500","count":1}],"truncated":false},"meta":{"version":"0.1.0"}}

$ tally --root . -f 3 -d ' ' --format text access.log
3	200
1	404
1	500
```

Without `--field`, whole lines are counted. Ties are ordered by bytes, so
the answer is deterministic. `--top N` limits the list, and `--max-keys`
bounds memory.

### `hash`: SHA-256 or SHA-512

```console
$ hash --root . package.json
{"type":"hash","path":"package.json","algo":"sha256","hex":"18ba6cb1…6b38","bytes":92}
{"type":"end","ok":true,"command":"hash","schema":"hash.v1","complete":true,"files":1,"errors":0}

$ hash --root . --verify 18ba6cb1…6b38 package.json     # exit 0, or 8 with precondition.hash-failed
```

Memory stays flat at any file size. `--algo sha512` selects SHA-512, and
`--format text` prints `sha256sum`'s layout.

### `write`: replace a file, only if it is what you think

`write` never overwrites blindly. The caller states a belief: `--create`
(the file must not exist) or `--if-sha256 HEX` (its content must hash to
HEX). The new content comes from `--stdin` or `--content-file`.

```console
$ printf 'hello\n' | write --root . --create --stdin NOTES.md
{"ok":true,"command":"write","schema":"write.v1","data":{"path":"NOTES.md","changed":true,"created":true,"bytes":6,
 "before_sha256":null,"after_sha256":"5891b5b5…be03"},"meta":{"version":"0.1.0"}}

$ printf 'hello again\n' | write --root . --create --stdin NOTES.md                     # exit 5
{"ok":false,…,"error":{"code":"CONFLICT","rule":"conflict.exists",
 "message":"--create was given and the path exists with other content",
 "hint":"read the file, then write with --if-sha256 and its current hash",
 "repair":{"kind":"none","reason":"the file exists; whether to replace it is a decision, not a retry"},
 "detail":{"path":"NOTES.md","actual_sha256":"5891b5b5…be03"}},…}

$ printf 'hello again\n' | write --root . --if-sha256 5891b5b5…be03 --stdin NOTES.md    # succeeds
```

* **Idempotent.** If the file already holds the new content, the answer is `changed: false`, exit 0.
* **Atomic.** The content goes to a temporary file, which is synced and renamed over the target.
* **Locked.** A lock is held from the check to the rename, so of two writers racing with the same `--if-sha256`, exactly one wins.
* **Dry run.** `--dry-run` reports what would happen and exits 9 without changing anything.

### `replace`: change exact text

```console
$ replace --root . --old 'a + b' --new 'a.wrapping_add(b)' --dry-run src/main.rs        # exit 9
{"ok":true,"command":"replace","schema":"replace.v1","data":{"path":"src/main.rs","changed":false,"replacements":1,…},
 "dry_run":true,"planned_actions":[{"op":"replace","path":"src/main.rs","replacements":1,
 "before_sha256":"98ace448…162c","after_sha256":"63261420…cf20","bytes":125}],…}

$ replace --root . --old 'a + b' --new 'a.wrapping_add(b)' src/main.rs
{"ok":true,"command":"replace","schema":"replace.v1","data":{"path":"src/main.rs","changed":true,"replacements":1,"bytes":125,
 "before_sha256":"98ace448…162c","after_sha256":"63261420…cf20"},"meta":{"version":"0.1.0"}}
```

`--old` must occur exactly `--expect` times (default 1). Any other count is
`precondition.count-mismatch` (exit 5), reporting the count found, and
nothing is written. `--if-sha256` adds a guard on the whole file. Like
`write`, it is atomic and locked, and `--dry-run` makes no mutating system
call.

## Errors you can act on

Every error is data: `{code, rule, message, hint, repair, detail}`. Match on
`rule`; the 37 rules are listed by `introspect` and on the site. When a fix
can be applied mechanically, `repair` is a command to run as it stands:

```console
$ peek --root . --lines 1:1 src/../src/main.rs                                       # exit 2
{"ok":false,…,"error":{"code":"INVALID_ARGS","rule":"path.dotdot","message":"the path has a `..` component",…,
 "repair":{"kind":"retry","argv":["peek","--root",".","--lines","1:1","src/main.rs"]},…}}
```

A repair never widens what the tool may touch: it never adds `--root`, a
write flag or `--create`, and never removes `--dry-run`.

| Exit | Meaning |
|---|---|
| 0 | done (zero matches is still success) |
| 1 | the operating system failed mid-operation |
| 2 | malformed invocation; nothing was read |
| 3 | a named input does not exist |
| 4 | refused, by the OS or by the tool's own confinement |
| 5 | conflict: the state is not what the caller said |
| 8 | the answer is no, or a limit was reached |
| 9 | a `--dry-run` completed |

## Safety

### Confinement

With `--root DIR`, `..`, absolute paths and paths outside the root are
refused before anything is opened. Every path is then opened beneath the
root one component at a time, following no symbolic link, so a link inside
the root cannot reach outside it. Such a link is `path.symlink` (exit 4):

```console
$ seek --root . secret leak.txt        # leak.txt -> ../outside/s.txt
{"type":"error","error":{"code":"PERMISSION_DENIED","rule":"path.symlink",
 "message":"the path runs through a symbolic link, and the tool does not follow links",…}}
```

Links in the root's own path are followed, because you named it. Without
`--root`, readers open paths as given, as `grep` would.

### Authority

Each binary embeds the effects the compiler derived from its source;
`introspect` prints them under `authority`. No tool can use the network, a
clock, foreign code or the environment.

* The five readers hold only read labels.
* `write` and `replace` can write only beneath a directory they opened
  (`dir_write`), never to an arbitrary path: no tool holds `fs_write`.
* The ceilings are reviewed in [`tools.toml`](tools.toml), and CI fails if
  a tool ever exceeds its ceiling.

| Tool | Authority |
|---|---|
| `seek`, `peek`, `hash` | `args, dir_read, err_write, file_read, fs_read(""), heap, io_write` |
| `jsonq`, `tally` | the same, plus `io_read` (they also read standard input) |
| `replace` | `args, dir_read, dir_write, err_write, file_read, file_write, fs_read(""), heap, io_write` |
| `write` | the same as `replace`, plus `io_read` |

`fs_read("")` names the *kind* of access, not its extent. Extent comes from
`--root`, or from a build with the root baked in. For example,
`scripts/variant.py --tool seek --root /srv/work` builds a `seek` whose
authority reads `fs_read("/srv/work")`.

### Memory and determinism

* **Bounded memory.** Streaming tools hold one chunk plus the longest line,
  capped by `--max-line-bytes`. Peak memory is about 1.7 MB whether the file
  is 1 MiB or 256 MiB.
* **Determinism.** No clock, no locale, integers only. The same input gives
  byte-identical output through a pipe, a file or a terminal.
* **Bytes kept.** Bytes that are not UTF-8 come out as `{"b64": "…"}`, never
  replaced.

## Performance

A 64 MiB text file, through a pipe, minimum of 7 interleaved rounds
(`scripts/toolbench.py`), Linux x86_64:

| Command | lexsys-tools | Incumbent |
|---|---|---|
| `seek gamma` (about a million matches) | **0.49 s** | `grep -F -n -b` 0.51 s, `rg` 0.39 s |
| `seek` with no match | **0.04 s** | `grep -F -n -b` 0.04 s |
| `seek -i GAMMA` | 0.58 s | `grep -F -n -b -i` 0.52 s |
| `hash` (SHA-256) | 0.52 s | `sha256sum` 0.17 s |
| `peek --count-lines --lines 1:100` | **0.08 s** | `sed -n 1,100p; wc -l` 0.13 s |
| `tally --field 1` | **0.16 s** | `cut \| sort \| uniq -c \| sort \| head` 0.68 s |
| `jsonq -p /0`, 16 MiB array | **0.14 s** | `jq -c .[0]` 0.73 s |

* `sha256sum`'s lead is OpenSSL's hand-written vector assembly, which
  lex-sys has no way to express.
* Startup is 1.76 ms, against 1.70 ms for `/usr/bin/true`.
* `--root` adds about 5.5 µs per file opened.
* Speed is reported, not gated, and these times come from a shared machine.
* How these numbers were reached is in
  [`docs/history.md`](docs/history.md#performance-round-by-round).

```sh
python3 scripts/toolbench.py --tool seek --size 64MiB    # measure on your machine
```

## Developing

```
contract/           the shared package: rules, fail, out, cli, path, place (open
                    beneath --root), lines, text, sha, atomic, limit, describe
tools/<tool>/       one program per tool
generated/<tool>/   the embedded manifest (written by scripts/manifest.py)
manifests/          each tool's authority, as the compiler reports it
schemas/            one JSON Schema per tool (written by scripts/schemas.py)
tools.toml          the authority ceiling a person writes and reviews
tests/*.ls          unit tests of the contract
tests/conformance/  the conformance gates, run against the built binaries
scripts/            manifest.py, schemas.py, variant.py, toolbench.py, site.py
```

The checks CI runs, in order:

```sh
lex-sys fmt --check contract tools tests generated
lex-sys build
lex-sys test
python3 scripts/schemas.py --check      # schemas are generated, not edited
python3 scripts/manifest.py --check     # authority = the compiler's, embedded, within tools.toml
python3 -m unittest discover -s tests/conformance -v   # needs jsonschema, jq, strace, ripgrep, cc
```

After changing a tool's code, run `python3 scripts/manifest.py` to
regenerate its manifest, then `lex-sys build` to embed it. A change that
needs a new effect fails the check until `tools.toml` allows it.

The conformance gates check:
* schema conformance;
* byte-for-byte determinism;
* that every rule is reached and every `retry` repair works;
* fault injection with zero crashes;
* differential agreement with `grep`, `sed`, `jq`, `sha256sum`, `sort | uniq -c` and `cp`;
* authority;
* dry-run and race behaviour under `strace`;
* confinement, including symlinks;
* flat memory.

Results are in [`docs/history.md`](docs/history.md).

## Status and limits

* **Done:** all seven tools, the contract, the authority gate, the
  benchmark harness, symlink-safe `--root`.
* **Not yet:** a `list` tool. It needs directory listing in lex-sys
  ([lex-sys#222](https://github.com/alpibrusl/lex-sys/issues/222)), and so
  does recursive `seek`. Moving SHA-256 into the lex-sys standard library
  ([#219](https://github.com/alpibrusl/lex-sys/issues/219)) and the lex-os
  bridge that would enforce these authorities at run time are also not done.
* **Not claimed:** that agents do better with these tools than with the
  incumbents. That needs an agent-in-the-loop evaluation
  ([lex-sys#228](https://github.com/alpibrusl/lex-sys/issues/228)) that has
  not been run. What is claimed is what the gates measure.

The design is lex-sys
[`docs/agent-toolbox.md`](https://github.com/alpibrusl/lex-sys/blob/main/docs/agent-toolbox.md)
(epic [lex-sys#214](https://github.com/alpibrusl/lex-sys/issues/214)). The
record of how this was built, including where it departs from that design
and what it found in the compiler, is in [`docs/history.md`](docs/history.md).

## License

EUPL-1.2, as lex-sys.
