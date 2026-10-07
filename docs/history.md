# How cancho-tools got here

The [README](../README.md) says what the tools are and how to use them. This
file keeps the record: what each gate measured, how performance moved and
why, how the epic's sub-issues were closed, where the build departs from the
design, and what building it found in the compiler. Section numbers (§, D, M,
L, S) refer to cancho
[`docs/agent-toolbox.md`](https://github.com/alpibrusl/cancho/blob/main/docs/agent-toolbox.md).

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
| M8 confinement | `..`, absolute, `//`, `./`, trailing `/`, sibling prefix, empty, 4,097 bytes, non-ASCII, a link to a file and a link to a directory: each a tag or a success, never a trap. **No link reaches outside `--root`**: every tool, readers and writers, through a linked file and a linked directory, is `path.symlink` and leaves the outside untouched (until cancho#227 this row asserted the escape); a link in `--root`'s own spelling is followed |
| M9 memory flatness | peak resident memory at 1, 64 and 256 MiB: `seek` 1,724/1,724/1,724 KB, `peek` 1,720/1,656/1,720, `hash` 1,704/1,640/1,704, `tally` 1,728/1,728/1,728 (max/min ≤ 1.10; the gate is 1.5; `/bin/true` measures 1,320 KB the same way). `examples/seek` measured the same way: 4,432 KB at 1 MiB, 197,968 KB at 64 MiB |
| D14 variant | `seek` and `write` built with the root baked in: the authority names the directory (`fs_read("/srv/work")`, and for `write` `dir_write` with no `fs_write`), and every M8 case is a tag, not a trap, though the narrowed `Fs` would trap on any path the validation missed |

## Performance, round by round

**Speed is reported, not gated** (§1.2). One probe, 64 MiB of text,
7 interleaved rounds, minimum, through a pipe (`scripts/toolbench.py`). The
columns are rounds of work: the first version; the same sources after
profiling, on the compiler they were written for (`a18e533`); on `8fc3b3f`,
with cancho's `calloc`, `copy_into`, `index_of_byte` and `flush_out` in use;
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
cancho instead (the third column):

* **`jsonq`'s parse tape was zero-filled byte by byte** (24 bytes per byte of
  input, 43% of the instructions). A zero-filled `box_slice` is now `calloc`
  (cancho#235, `docs/zeroed-slices.md`): the pages the parser never writes
  are never touched. 0.43 s to 0.15 s, and peak memory from 427,540 KB to
  88,888 KB.
* **Copying bytes was a loop with a bounds check per byte**, a quarter of a
  match-heavy `seek`. cancho now has `copy_into`, one `memmove`
  (cancho#240, `docs/bulk-copy.md`), and `std.buffer.append` uses it, so
  this repository's own copy (`text.append_bytes`) is deleted.
* **Finding the end of a line was a loop** comparing every byte with `\n`.
  `index_of_byte` is one `memchr` (cancho#241, `docs/byte-search.md`); the
  line reader, which `peek`, `tally` and `seek` share, uses it. The three
  changes were measured together, not one at a time.
* **A failed write at exit was invisible.** `flush_out` (cancho#232,
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
* **`hash` rotates without a rotate.** cancho has no 32-bit type, so a
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
| 1 | cancho#215 checked stdout writes (L1) | **Done** (cancho#232, `flush_out`). Every `write_bytes` is checked against its length, and `toolbox.out.flushed` flushes standard output after the last write and reports a failure at any earlier point; either makes the tool say `io.write-failed` on stderr and exit 1. A stream still ends with an `end` record, because a killed process flushes nothing |
| 2 | cancho#216 S0 the contract package | **Done**: `contract/`, unit tests in `tests/*.cho` |
| 3 | cancho#217 S1a `seek` v1 | **Done** |
| 4 | cancho#218 S1b `write` / `replace` | **Done** |
| 5 | cancho#219 incremental SHA-256 (L4) | **In-package half done** (`contract/sha.cho`, SHA-256 and SHA-512, checked against `std.crypto` for every length 0-300 in three chunkings and against `sha256sum` past 64 KiB). The `std.crypto` half is a compiler change, not done |
| 6 | cancho#220 S2a manifest export and CI gate | **Done**: `scripts/manifest.py`, `tools.toml`, `manifests/`, `generated/` |
| 7 | lex-os#122 S2b fail-closed bridge | **Not done** (lex-os). `test_authority.py` checks that D13's table is total for the labels these tools hold |
| 8 | lex-os#123 `diff` path-prefix narrowing | **Not done** (lex-os) |
| 9 | cancho#221 S3 toolbench | **Done**: `scripts/toolbench.py`, self-test in CI |
| 10 | cancho#222 `fs_list` / `fs_stat` (L2/L3) | **Not done** (compiler; decision D16 pending) |
| 11 | cancho#223 B1 `peek`, `jsonq`, `tally` | **Done** |
| 12 | cancho#224 B2 `list` and `hash` | **`hash` done**; `list` waits on #222 |
| 13 | cancho#225 D14 variant transform | **Built and tested, no variant shipped**, as D14 recommends: `scripts/variant.py`, `tests/conformance/test_variant.py` |
| 14 | cancho#226 canary job in cancho CI | **Not done** (cancho CI) |
| 15 | cancho#227 no-follow open (L6) | **Done** (cancho#250 and #254, directory handles). Every tool opens a path under `--root` beneath it with `toolbox.place`; `write` and `replace` create, rename, lock and sync beneath the parent `Dir` and hold no `fs_write`; M8 asserts the refusal |
| 16 | cancho#228 S-last agent-in-the-loop evaluation | **Not run**: it needs a model in a loop and a frozen protocol, which this environment does not have |

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

## What building it found in cancho

Each is reproducible from the commit pinned in `cancho.toml`; none is worked
around silently.

1. **`Vec[&static [byte]]` does not compile on the LLVM backend**: the
   checker accepts it and `clang` refuses the emitted module (the symbol
   `lexs_std.vec.empty$&static__byte_` is not a valid name). `cli.Parsed`
   keeps argument indices instead of slices for this reason.
2. **A local binding shadows a module-qualified call of the same name.**
   `var size = 0; size = buffer.size(r);` is refused with "`size` is a local
   binding, not a function"; so are `failed = lines.failed(r)`,
   `next = json.at(…)` after `let at`, and others. Met in five source files
   here (`contract/path.cho`, `seek`, `peek`, `jsonq`, `tally`); the locals were
   renamed.
3. **Two root-module test files that each `import std.test` cannot be one
   `[[test]]` set**: "`test` is already bound to another import". Each test
   file is its own set in `cancho.toml`.
4. **The design's own probe pitfall, met in this repository's M9**: a child
   forked from the test runner inherits the runner's resident high-water
   mark across `exec`, so every peak first read 24,448 KB. `maxrss.c`
   measures from a small launcher; documented in `test_memory.py`.
5. **`file_size` on a directory is the filesystem's answer.** It is
   `lseek(SEEK_END)`: ext4 and APFS answer a number, tmpfs
   (`dcache_dir_lseek`) answers `EINVAL`. `peek` sized the file before its
   first read, so on a machine whose `/tmp` is tmpfs a directory was
   `io.read-failed` rather than `io.is-a-directory`, and CI (ext4) did not
   see it. There is no `fstat` on a `File`; `place.file_length` asks the
   handle with a one-byte `pread` first, which answers `EISDIR` everywhere,
   and M3 runs a second time with its tree on `/dev/shm` when that is tmpfs.
6. **`move` could not refuse an existing name atomically.** Its look-then-`dir_rename`
   lost the file of a process that took no lock in 55 of 20,000 random-arrival
   trials and 100 of 100 delayed ones (`scripts/move_race.py`). cancho gained
   `dir_rename_new` (edition 7: `renameat2` `RENAME_NOREPLACE`, `renameatx_np`
   `RENAME_EXCL`; PR #351), `move` uses it, and the same trials lose nothing.
   `write --create` has the same look-then-replace shape (`toolbox.atomic` renames
   its temporary over the name) and is not changed yet.
7. **`write --create` had the same hole as `move`.** Its look-then-`dir_rename` of the temporary over the
   path lost the file of a process that took no lock in 411 of 20,000 random arrivals on macOS, 107 on Linux
   x86-64 and 100 of 100 delayed ones (`scripts/write_race.py`). `toolbox.atomic` gained `create`
   (`dir_rename_new`), `--create` uses it, the same trials lose nothing, and `io.rename-unsupported` moved from
   `move`'s own rules into the shared catalogue (39 rules) because two tools now raise it. `atomic.cho` is
   edition 7 for that, so the published `toolbox.atomic` needs a compiler with `dir_rename_new`.

