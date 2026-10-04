# lexsys-tools

An agent toolbox in [lex-sys](https://github.com/alpibrusl/lex-sys): seven
small unix-like tools with a JSON contract, rule-tagged errors with a
machine-applicable repair, and an authority the compiler derives and the
build embeds. The design is lex-sys
[`docs/agent-toolbox.md`](https://github.com/alpibrusl/lex-sys/blob/main/docs/agent-toolbox.md)
(decisions D1–D18, gates M1–M9, slices S0–S-last); the epic is
[lex-sys#214](https://github.com/alpibrusl/lex-sys/issues/214). This
repository is D18's separate repository.

**Site:** <https://alpibrusl.github.io/lexsys-tools/>, with one page per tool
generated from its own `introspect` (flags, rules, exit codes, authority,
schemas and `SKILL.md`) by `scripts/site.py` on every push to `main`.

**What this repository does not claim.** Nothing here says these tools are
"more agent-friendly" than the incumbents, or that they improve task success.
That needs the agent-in-the-loop evaluation (§7.2, lex-sys#228), which has not
been run. What is claimed below is what the offline gates measure, and only
those (§7.3). Every tool's `introspect` says the same in its `evidence` field.

## The tools

| Tool | Does | Output | Authority (derived by `lex-sys authority`) |
|---|---|---|---|
| `seek` | literal search over files (`grep -F`) | NDJSON stream | `args, dir_read, err_write, file_read, fs_read(""), heap, io_write` |
| `write` | replace a file atomically, only with `--create` or `--if-sha256`; idempotent; `--dry-run` | document | `args, dir_read, dir_write, err_write, file_read, file_write, fs_read(""), heap, io_read, io_write` |
| `replace` | replace exact text `--expect` times, atomically; `--dry-run` | document | `args, dir_read, dir_write, err_write, file_read, file_write, fs_read(""), heap, io_write` |
| `peek` | a line or byte range with numbers, offsets, size, a cursor (`head`/`tail`/`sed -n`/`cat -n`/`wc -l`) | document | `args, dir_read, err_write, file_read, fs_read(""), heap, io_write` |
| `jsonq` | one RFC 6901 pointer into one strict JSON document | document | `args, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write` |
| `tally` | distinct lines or fields, counted, ordered by count then bytes (`sort \| uniq -c \| sort -rn`) | document | `args, err_write, file_read, fs_read(""), heap, io_read, io_write` |
| `hash` | SHA-256/512 of files of any size, `--verify` | NDJSON stream | `args, dir_read, err_write, file_read, fs_read(""), heap, io_write` |

No tool holds `ffi`, a network label or a clock; every report is
`bounded: true`. The five read-only tools hold no `dir_write` or
`file_write`, and **no tool holds `fs_write`**: `write` and `replace` write
only beneath a directory they opened (`dir_write`, lex-sys
`docs/directory-handles.md`), never by path. The rows name *kinds* of access,
not *extent*: a path from the command line is checked at run time, so the row
says `fs_read("")` (§0 amendment 2). Extent comes from `--root` (D9: checked
lexically, then opened beneath the root following no link), from a build with
the root baked in (D14, `scripts/variant.py`, whose row then reads
`fs_read("/srv/work")`), or from the perimeter.

Each tool answers `tool introspect` (flags with their roles, exit codes, the
rule catalogue, limits, the schema, the embedded authority) and `tool skill`
(a generated `SKILL.md`), both from the same tables that drive the parser
(D11).

## The contract

* **JSON by default** (D2). A document tool writes one object and a newline;
  a stream tool writes NDJSON ending with an `end` record. **A stream with no
  `end` record was cut short.** Bytes that are not UTF-8 are written as
  `{"b64": "…"}`, never replaced. Integers only, no clock, no locale.
* **The envelope** (D3) is ACLI's without `meta.duration_ms`, with `schema`,
  `error.rule`, `error.repair` and `error.detail` added:
  `{ok, command, schema, data, error, errors, meta}`.
* **Exit codes** (D4): 0 done (zero matches is success), 1 the OS failed,
  2 malformed invocation (nothing was read), 3 not found, 4 refused,
  5 conflict, 8 the answer is no or a limit was reached, 9 dry run.
* **Errors are data** (D5): `{code, rule, message, hint, repair, detail}`.
  37 rules in one catalogue (`contract/rules.ls`); every independent error is
  reported, the first decides the exit code.
* **Repairs** (D6): `null`, `{"kind":"none","reason"}`, or
  `{"kind":"retry","argv":[…]}` that a script can run as it stands. A repair
  never adds a flag whose role is not `none` (no `--root`, no write flag, no
  `--create`, never removes `--dry-run`).
* **Mutation** (D10): no blind overwrite; idempotent; a sidecar `flock` on
  `<path>.lexsys-lock` held across check and rename; a synced temporary
  renamed over the target, every step beneath the directory that holds it;
  `--dry-run` makes no mutating system call.
* **Confinement** (D9): with `--root`, a path is checked lexically
  (`..`, absolute, outside) and then opened beneath the root one component
  at a time with `O_NOFOLLOW`; a symbolic link anywhere below the root is
  `path.symlink` (exit 4), whether it points inside or out. The root's own
  spelling is the caller's and may hold links. Without `--root`, a reader
  opens the path as given, links and all; a writer never follows a link in
  the file's own name. `introspect` says `confinement: beneath`.
* **Memory** (D8): a chunk plus the longest line, capped, whatever the input.

## Layout

```
contract/           the shared package (S0): rules, fail, out, cli, path, lines,
                    text (text_or_bytes, base64), sha (incremental SHA-256/512),
                    atomic (lock, temporary, rename), place (open beneath
                    --root, following no link), limit, describe
tools/<tool>/       one program per tool (D17)
generated/<tool>/   the embedded manifest -- written by scripts/manifest.py
manifests/          the compiler's authority report per tool, committed
schemas/            one JSON Schema per tool -- written by scripts/schemas.py
tools.toml          the authority ceiling a person writes (D12)
tests/*.ls          unit tests of the contract (`lex-sys test`)
tests/conformance/  the offline gates M1-M9 and D14, as processes
scripts/            manifest.py, schemas.py, variant.py, toolbench.py, site.py
```

## Building and checking

The compiler is the commit `lex-sys.toml` pins; CI builds it from source.

```sh
lex-sys build                          # every tool into build/
lex-sys test                           # the contract's unit tests
python3 scripts/schemas.py --check     # schemas are generated, not edited
python3 scripts/manifest.py --check    # M6: authority = compiler's, embedded, within tools.toml
python3 -m unittest discover -s tests/conformance -v      # M1-M9, D14 (needs jsonschema, jq, strace, cc)
python3 scripts/toolbench.py --self-test                  # S3's own gate
python3 scripts/toolbench.py --tool seek --size 64MiB     # a probe, not a result
python3 scripts/site.py                                   # the Pages site, from build/ into site/
```

After changing a tool, `python3 scripts/manifest.py` regenerates the
manifests and checks the fixed point; `lex-sys build` again embeds them.

## What the gates measured

On the pinned compiler (`066a810`), Linux x86_64, a shared and noisy sandbox.

| Gate | Result |
|---|---|
| M1 schema conformance | a corpus of 58 invocations across all seven tools: every output valid against its schema (Draft 2020-12, `additionalProperties: false` except `error.detail`), every status in the tool's declared table |
| M2 determinism | 765 runs (5 environments × pipe, file and pty sinks): byte-identical to the baseline |
| M3 rules and repairs | 149 fixtures reach all 37 rules; each tool's declared rule list equals what its fixtures reach; 24 `retry` repairs applied by script, all succeed and none adds a flag of role other than `none` (hint soundness 24/24). Actionability (how many errors carry a repair) is reported, not gated, until a baseline exists |
| M4 fault injection | 1,750 seeded cases in CI (N=250 per tool) plus a 1 GiB sparse file; 12,600 more across three other seeds here: **0 traps**, every JSON output valid |
| M5 differential | `seek` = `grep -F -n -b -a [-i]`; `peek` = `sed -n`, `wc -l`; `jsonq` = `jq -c`; `hash` = `sha256sum`/`sha512sum` at every padding boundary and past 64 KiB; `write` = `cp`; `tally` = `sort \| uniq -c \| sort -k1,1nr -k2`: 0 divergences over the seeded and edge corpus |
| M6 authority | the committed record equals a fresh derivation, the binary prints it, it is within `tools.toml`, the fixed point holds, and D13's bridge table is total for every label held |
| M7 mutation | `--dry-run` under `strace`: no mutating call, tree unchanged (with a positive control); every write applied twice, the second `changed:false`; 200 two-writer races, exactly one winner each |
| M8 confinement | `..`, absolute, `//`, `./`, trailing `/`, sibling prefix, empty, 4,097 bytes, non-ASCII, a link to a file and a link to a directory: each a tag or a success, never a trap. **No link reaches outside `--root`**: every tool, readers and writers, through a linked file and a linked directory, is `path.symlink` and leaves the outside untouched (until lex-sys#227 this row asserted the escape); a link in `--root`'s own spelling is followed |
| M9 memory flatness | peak resident memory at 1, 64 and 256 MiB: `seek` 1,724/1,724/1,724 KB, `peek` 1,720/1,656/1,720, `hash` 1,704/1,640/1,704, `tally` 1,728/1,728/1,728 (max/min ≤ 1.10; the gate is 1.5; `/bin/true` measures 1,320 KB the same way). `examples/seek` measured the same way: 4,432 KB at 1 MiB, 197,968 KB at 64 MiB |
| D14 variant | `seek` and `write` built with the root baked in: the authority names the directory (`fs_read("/srv/work")`, and for `write` `dir_write` with no `fs_write`), and every M8 case is a tag, not a trap, though the narrowed `Fs` would trap on any path the validation missed |

**Speed is reported, not gated** (§1.2). One probe, 64 MiB of text,
7 interleaved rounds, minimum, through a pipe (`scripts/toolbench.py`). The
columns are rounds of work: the first version; the same sources after
profiling, on the compiler they were written for (`a18e533`); on `8fc3b3f`,
with lex-sys's `calloc`, `copy_into`, `index_of_byte` and `flush_out` in use;
and now, on the same compiler, with `seek` scanning blocks instead of lines and
`hash`'s rounds reworked (below). The last three columns and the incumbents
were measured on one machine on the same day; the incumbents' times are this
round's:

| Tool | First version | Profiled (`a18e533`) | `8fc3b3f` | Now | Incumbent |
|---|---|---|---|---|---|
| `seek gamma` (about a million matches written) | 1.67 s | 0.76 s | 0.67 s | **0.49 s** | `grep -F -n -b` 0.51 s, `rg` 0.39 s |
| `seek` with no match | 2.7 s | 0.22 s | 0.16 s | **0.04 s** | `grep -F -n -b` 0.04 s |
| `hash` | 0.83 s | 0.65 s | 0.67 s | **0.52 s** | `sha256sum` 0.17 s (OpenSSL's AVX2/BMI2 assembly) |
| `peek --count-lines --lines 1:100` | 0.19 s | 0.17 s | **0.08 s** | 0.08 s | `sed -n 1,100p; wc -l` 0.13 s |
| `tally --field 1` | 0.30 s | 0.23 s | **0.17 s** | 0.16 s | `cut \| sort \| uniq -c \| sort \| head` 0.68 s |
| `jsonq -p /0`, 16 MiB array | 0.41 s | 0.43 s | **0.15 s** | 0.14 s | `jq -c .[0]` 0.73 s |

`seek -i GAMMA`, measured the same way but outside `toolbench.py`: 0.58 s
against `grep -F -n -b -i`'s 0.52 s.

Opening beneath `--root` (D9, `toolbox.place`) costs one `open_dir`, one
`openat` per component and the closes, per file: 2,000 one-line files searched
in one call took 0.132 s with `--root .` against 0.121 s without, about 5.5 µs
a file, and a single 56 MiB file the same either way (minimum of 7).

What the profile (callgrind) found and what changed in this repository: `seek`
searched with `std.bytes.find`, which tried every offset (44% of its time) --
it now uses Boyer-Moore-Horspool with a table built once per file; the line
reader copied every line into a buffer (20%) -- it now answers a view into the
read chunk and copies only a line that spans two reads; each match built a
`json.Writer` and validated UTF-8 by decoding every byte -- a match is now
encoded straight into one reused buffer with an ASCII fast path, and the path
part is escaped once per file. Writing a match in nine `write_bytes` calls
instead of one was tried and was slower (1.02 s). `hash` allocated a 64 KiB
region per block and spent a checked add and a mask on every 32-bit
operation; the schedule is now reused and the rounds use wrapping adds and one
mask per word.

What the profile found that was **not** in this repository, and was fixed in
lex-sys instead (the third column):

* **`jsonq`'s parse tape was zero-filled byte by byte** (24 bytes per byte of
  input, 43% of the instructions). A zero-filled `box_slice` is now `calloc`
  (lex-sys#235, `docs/zeroed-slices.md`): the pages the parser never writes
  are never touched. 0.43 s to 0.15 s, and peak memory from 427,540 KB to
  88,888 KB.
* **Copying bytes was a loop with a bounds check per byte**, a quarter of a
  match-heavy `seek`. lex-sys now has `copy_into`, one `memmove`
  (lex-sys#240, `docs/bulk-copy.md`), and `std.buffer.append` uses it, so
  this repository's own copy (`text.append_bytes`) is deleted.
* **Finding the end of a line was a loop** comparing every byte with `\n`.
  `index_of_byte` is one `memchr` (lex-sys#241, `docs/byte-search.md`); the
  line reader, which `peek`, `tally` and `seek` share, uses it. The three
  changes were measured together, not one at a time.
* **A failed write at exit was invisible.** `flush_out` (lex-sys#232,
  `docs/checked-output.md`) is now called after the last write of every tool;
  it costs nothing measurable.

The fourth column, in this repository again:

* **`seek` scans blocks, not lines.** After the changes above, a search with
  no match was still 93% per-line work: the search of each line, the line
  reader and the loop between them (callgrind). `seek` now reads into a
  buffer of `--max-line-bytes` plus one read, searches all the whole lines in
  it in one pass, and finds a line's start, end and number only around a
  match, counting newlines with `memchr`. Only a block longer than the cap is
  walked line by line, to report the lines over it, as before. The search
  itself runs `memchr` on the needle byte that is rarest *in this file's
  first block* (counted, not guessed: a table of English letter frequencies
  picked `z`, and a corpus full of "zeta" made every `z` a false candidate),
  and falls back to Horspool when candidates come too thick; `-i` stays on
  Horspool. Every gate, M5's differential against `grep` included, is
  unchanged.
* **Writing a match got cheaper.** `text.append_json`'s check for bytes that
  need no escaping is one table load instead of four comparisons, and
  `text.append_nat` writes its digits in one pass instead of two (together
  they were 52% of a match-heavy run).
* **`hash` rotates without a rotate.** lex-sys has no 32-bit type, so a
  32-bit rotation was two shifts, an or and a mask. A word written twice,
  `x << 32 | x`, holds all its rotations: the low 32 bits of `(x << 32 | x) >> n`
  are `x` rotated by `n`, and one doubled word serves all three rotations of a
  Σ. The 64 rounds are also unrolled by eight, so the working variables are
  renamed instead of shuffled (eight moves a round). 112 instructions a byte
  became 81. The rest of the gap to `sha256sum` is OpenSSL's hand-written
  AVX2/BMI2 assembly; closing it needs vector instructions the language does
  not have.

**Corrected.** An earlier version of this table had `jsonq` at 0.08 s against
`jq`'s 1.51 s. `jsonq` was refusing that document (exit 8: it was larger than
`--max-bytes`), so the time measured a refusal. `toolbench.py` now marks a run
invalid when any command exits non-zero.

Startup: 1.76 ms against 1.70 ms for `/usr/bin/true` (300 spawns each).

## The epic, sub-issue by sub-issue

| N | Issue | Here |
|---|---|---|
| 1 | lex-sys#215 checked stdout writes (L1) | **Done** (lex-sys#232, `flush_out`). Every `write_bytes` is checked against its length, and `toolbox.out.flushed` flushes standard output after the last write and reports a failure at any earlier point; either makes the tool say `io.write-failed` on stderr and exit 1. A stream still ends with an `end` record, because a killed process flushes nothing |
| 2 | lex-sys#216 S0 the contract package | **Done**: `contract/`, unit tests in `tests/*.ls` |
| 3 | lex-sys#217 S1a `seek` v1 | **Done** |
| 4 | lex-sys#218 S1b `write` / `replace` | **Done** |
| 5 | lex-sys#219 incremental SHA-256 (L4) | **In-package half done** (`contract/sha.ls`, SHA-256 and SHA-512, checked against `std.crypto` for every length 0-300 in three chunkings and against `sha256sum` past 64 KiB). The `std.crypto` half is a compiler change, not done |
| 6 | lex-sys#220 S2a manifest export and CI gate | **Done**: `scripts/manifest.py`, `tools.toml`, `manifests/`, `generated/` |
| 7 | lex-os#122 S2b fail-closed bridge | **Not done** (lex-os). `test_authority.py` checks that D13's table is total for the labels these tools hold |
| 8 | lex-os#123 `diff` path-prefix narrowing | **Not done** (lex-os) |
| 9 | lex-sys#221 S3 toolbench | **Done**: `scripts/toolbench.py`, self-test in CI |
| 10 | lex-sys#222 `fs_list` / `fs_stat` (L2/L3) | **Not done** (compiler; decision D16 pending) |
| 11 | lex-sys#223 B1 `peek`, `jsonq`, `tally` | **Done** |
| 12 | lex-sys#224 B2 `list` and `hash` | **`hash` done**; `list` waits on #222 |
| 13 | lex-sys#225 D14 variant transform | **Built and tested, no variant shipped**, as D14 recommends: `scripts/variant.py`, `tests/conformance/test_variant.py` |
| 14 | lex-sys#226 canary job in lex-sys CI | **Not done** (lex-sys CI) |
| 15 | lex-sys#227 no-follow open (L6) | **Done** (lex-sys#250 and #254, directory handles). Every tool opens a path under `--root` beneath it with `toolbox.place`; `write` and `replace` create, rename, lock and sync beneath the parent `Dir` and hold no `fs_write`; M8 asserts the refusal |
| 16 | lex-sys#228 S-last agent-in-the-loop evaluation | **Not run**: it needs a model in a loop and a frozen protocol, which this environment does not have |

## Where this differs from the design, and why

* **`replace --expect` is the statement of belief.** D10's "no blind
  overwrite" for `replace` is `--expect N` (default 1): the text must occur
  exactly that often. `--if-sha256` is optional on `replace`, as the D15
  sketch has it.
* **Idempotence is decided from the content.** `write` answers
  `changed:false` when the file already holds the new content, whatever the
  precondition; `replace` when `--old` no longer occurs and `--new` occurs
  `--expect` times. The second is a judgement from the text, and says so.
* **A stale temporary is removed once, under the lock.** D10 says a temporary
  that exists is `conflict.locked`. With the lock held no live writer can own
  it, and refusing forever would need a delete no tool has; `write` removes
  it and goes on (`test_a_stale_temporary_does_not_block_forever`).
* **The lock sidecar stays.** `<path>.lexsys-lock` is left in place: removing
  a lock file another process may be about to lock is how lock files race.
* **`limit.line-too-long` is one error per file**, naming the longest line, so
  its repair (raise `--max-line-bytes` to that length) clears it. The first
  version named the first over-long line, and M3's soundness check would
  have failed on any file with a longer one later.
* **`seek`'s cursor is `--skip N`.** `--max-count` stops after N matches in
  total and answers `next: {"skip": N}`; a following call with `--skip N`
  resumes, deterministically, across files.
* **`internal.invariant` is not in the catalogue.** No tool has a check that
  could emit it, and a tag with no fixture is a hole. **`query.unsupported-syntax`**
  is emitted by `jsonq` for a pointer that looks like a jq filter (`.a[0]`,
  `a | b`), with a `none` repair naming jq.
* **An absolute path under a relative `--root`** cannot be recognised (there
  is no `getcwd`), so it is `path.outside-root`.
* **`seek` does not read standard input**, so its row has no `io_read`.

## What building it found in lex-sys

Each is reproducible from the commit pinned in `lex-sys.toml`; none is worked
around silently.

1. **`Vec[&static [byte]]` does not compile on the LLVM backend**: the
   checker accepts it and `clang` refuses the emitted module (the symbol
   `lexs_std.vec.empty$&static__byte_` is not a valid name). `cli.Parsed`
   keeps argument indices instead of slices for this reason.
2. **A local binding shadows a module-qualified call of the same name.**
   `var size = 0; size = buffer.size(r);` is refused with "`size` is a local
   binding, not a function"; so are `failed = lines.failed(r)`,
   `next = json.at(…)` after `let at`, and others. Met in five source files
   here (`contract/path.ls`, `seek`, `peek`, `jsonq`, `tally`); the locals were
   renamed.
3. **Two root-module test files that each `import std.test` cannot be one
   `[[test]]` set**: "`test` is already bound to another import". Each test
   file is its own set in `lex-sys.toml`.
4. **The design's own probe pitfall, met in this repository's M9**: a child
   forked from the test runner inherits the runner's resident high-water
   mark across `exec`, so every peak first read 24,448 KB. `maxrss.c`
   measures from a small launcher; documented in `test_memory.py`.

## License

EUPL-1.2, as lex-sys.
