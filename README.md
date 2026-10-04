# lexsys-tools

An agent toolbox in [lex-sys](https://github.com/alpibrusl/lex-sys): seven
small unix-like tools with a JSON contract, rule-tagged errors with a
machine-applicable repair, and an authority the compiler derives and the
build embeds. The design is lex-sys
[`docs/agent-toolbox.md`](https://github.com/alpibrusl/lex-sys/blob/main/docs/agent-toolbox.md)
(decisions D1–D18, gates M1–M9, slices S0–S-last); the epic is
[lex-sys#214](https://github.com/alpibrusl/lex-sys/issues/214). This
repository is D18's separate repository.

**What this repository does not claim.** Nothing here says these tools are
"more agent-friendly" than the incumbents, or that they improve task success.
That needs the agent-in-the-loop evaluation (§7.2, lex-sys#228), which has not
been run. What is claimed below is what the offline gates measure, and only
those (§7.3). Every tool's `introspect` says the same in its `evidence` field.

## The tools

| Tool | Does | Output | Authority (derived by `lex-sys authority`) |
|---|---|---|---|
| `seek` | literal search over files (`grep -F`) | NDJSON stream | `args, err_write, file_read, fs_read(""), heap, io_write` |
| `write` | replace a file atomically, only with `--create` or `--if-sha256`; idempotent; `--dry-run` | document | `args, err_write, file_read, file_write, fs_read(""), fs_write(""), heap, io_read, io_write` |
| `replace` | replace exact text `--expect` times, atomically; `--dry-run` | document | `args, err_write, file_read, file_write, fs_read(""), fs_write(""), heap, io_write` |
| `peek` | a line or byte range with numbers, offsets, size, a cursor (`head`/`tail`/`sed -n`/`cat -n`/`wc -l`) | document | `args, err_write, file_read, fs_read(""), heap, io_write` |
| `jsonq` | one RFC 6901 pointer into one strict JSON document | document | `args, err_write, file_read, fs_read(""), heap, io_read, io_write` |
| `tally` | distinct lines or fields, counted, ordered by count then bytes (`sort \| uniq -c \| sort -rn`) | document | `args, err_write, file_read, fs_read(""), heap, io_read, io_write` |
| `hash` | SHA-256/512 of files of any size, `--verify` | NDJSON stream | `args, err_write, file_read, fs_read(""), heap, io_write` |

No tool holds `ffi`, a network label or a clock; every report is
`bounded: true`. The five read-only tools hold no `fs_write`. The rows name
*kinds* of access, not *extent*: a path from the command line is checked at
run time, so the row says `fs_read("")` (§0 amendment 2). Extent comes from
`--root` (lexical, D9), from a build with the root baked in (D14,
`scripts/variant.py`, whose row then reads `fs_read("/srv/work")`), or from
the perimeter.

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
  36 rules in one catalogue (`contract/rules.ls`); every independent error is
  reported, the first decides the exit code.
* **Repairs** (D6): `null`, `{"kind":"none","reason"}`, or
  `{"kind":"retry","argv":[…]}` that a script can run as it stands. A repair
  never adds a flag whose role is not `none` (no `--root`, no write flag, no
  `--create`, never removes `--dry-run`).
* **Mutation** (D10): no blind overwrite; idempotent; a sidecar `flock` on
  `<path>.lexsys-lock` held across check and rename; a synced temporary
  renamed over the target; `--dry-run` makes no mutating system call.
* **Memory** (D8): a chunk plus the longest line, capped, whatever the input.

## Layout

```
contract/           the shared package (S0): rules, fail, out, cli, path, lines,
                    text (text_or_bytes, base64), sha (incremental SHA-256/512),
                    atomic (lock, temporary, rename), limit, describe
tools/<tool>/       one program per tool (D17)
generated/<tool>/   the embedded manifest -- written by scripts/manifest.py
manifests/          the compiler's authority report per tool, committed
schemas/            one JSON Schema per tool -- written by scripts/schemas.py
tools.toml          the authority ceiling a person writes (D12)
tests/*.ls          unit tests of the contract (`lex-sys test`)
tests/conformance/  the offline gates M1-M9 and D14, as processes
scripts/            manifest.py, schemas.py, variant.py, toolbench.py
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
```

After changing a tool, `python3 scripts/manifest.py` regenerates the
manifests and checks the fixed point; `lex-sys build` again embeds them.

## What the gates measured

On the pinned compiler (`a18e533`), Linux x86_64, a shared and noisy sandbox.

| Gate | Result |
|---|---|
| M1 schema conformance | a corpus of 60+ invocations across all tools: every output valid against its schema (Draft 2020-12, `additionalProperties: false` except `error.detail`), every status in the tool's declared table |
| M2 determinism | 765 runs (5 environments × pipe, file and pty sinks): byte-identical to the baseline |
| M3 rules and repairs | 142 fixtures reach all 36 rules; each tool's declared rule list equals what its fixtures reach; 24 `retry` repairs applied by script, all succeed and none adds a flag of role other than `none` (hint soundness 24/24). Actionability (how many errors carry a repair) is reported, not gated, until a baseline exists |
| M4 fault injection | 1,750 seeded cases in CI (N=250 per tool) plus a 1 GiB sparse file; 12,600 more across three other seeds here: **0 traps**, every JSON output valid |
| M5 differential | `seek` = `grep -F -n -b -a [-i]`; `peek` = `sed -n`, `wc -l`; `jsonq` = `jq -c`; `hash` = `sha256sum`/`sha512sum` at every padding boundary and past 64 KiB; `write` = `cp`; `tally` = `sort \| uniq -c \| sort -k1,1nr -k2`: 0 divergences over the seeded and edge corpus |
| M6 authority | the committed record equals a fresh derivation, the binary prints it, it is within `tools.toml`, the fixed point holds, and D13's bridge table is total for every label held |
| M7 mutation | `--dry-run` under `strace`: no mutating call, tree unchanged (with a positive control); every write applied twice, the second `changed:false`; 200 two-writer races, exactly one winner each |
| M8 confinement | `..`, absolute, `//`, `./`, trailing `/`, sibling prefix, empty, 4,097 bytes, non-ASCII: each a tag or a success, never a trap; **the symlink escape is asserted** (a link inside `--root` is followed), so it flips when lex-sys#227 lands |
| M9 memory flatness | peak resident memory at 1, 64 and 256 MiB: `seek` 1,572/1,572/1,572 KB, `peek` 1,572, `hash` 1,564, `tally` 1,580 (max/min ≤ 1.01; the gate is 1.5). `examples/seek` measured the same way: 4,432 KB at 1 MiB, 197,968 KB at 64 MiB |
| D14 variant | `seek` and `write` built with the root baked in: the authority names the directory, and every M8 case is a tag, not a trap, though the narrowed `Fs` would trap on any path the validation missed |

**Speed is reported, not gated** (§1.2). One probe, 64 MiB, 7 interleaved
rounds, minimum: `seek` 1.67 s against `grep -F -n -b` 0.48 s and `rg` 0.36 s
(about a million matches written); `hash` 0.83 s against `sha256sum` 0.18 s;
`peek --count-lines` 0.19 s against `sed -n 1,100p; wc -l` 0.12 s; `tally` on
a field 0.30 s against `cut | sort | uniq -c | sort | head` 0.71 s; `jsonq` on
a 32 MiB array 0.08 s against `jq` 1.51 s. Startup: 1.76 ms against 1.70 ms
for `/usr/bin/true` (300 spawns each).

## The epic, sub-issue by sub-issue

| N | Issue | Here |
|---|---|---|
| 1 | lex-sys#215 checked stdout writes (L1) | **Not done** (compiler). Mitigated as D2 says: every `write_bytes` is checked against its length, a short one stops the tool with `io.write-failed` on stderr and exit 1, and a stream's `end` record is the evidence nothing was lost. The last buffer's worth can still be lost invisibly until L1 lands |
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
| 15 | lex-sys#227 no-follow open (L6) | **Not done** (compiler); M8 pins the escape |
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
   `next = json.at(…)` after `let at`, and others. Met eleven times here; the
   locals were renamed.
3. **Two root-module test files that each `import std.test` cannot be one
   `[[test]]` set**: "`test` is already bound to another import". Each test
   file is its own set in `lex-sys.toml`.
4. **The design's own probe pitfall, met in this repository's M9**: a child
   forked from the test runner inherits the runner's resident high-water
   mark across `exec`, so every peak first read 24,448 KB. `maxrss.c`
   measures from a small launcher; documented in `test_memory.py`.

## License

EUPL-1.2, as lex-sys.
