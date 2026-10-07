# The next tools: `run` (design), and where `move` and `table` went

> **Status, 2026-10-07: `move` is built and merged; `table` is built in its own repository;
> `run` is designed, its two compiler prerequisites are merged, and it is not built.**
> The live part of this document is §3. Sections 4 and 5 are kept, with their measured history and
> with the status statements brought up to date (each says which PR is the evidence); the
> section numbers are unchanged because source comments and tests cite them (`tools/move/move.cho`,
> `scripts/move_race.py`, `scripts/package.py`, `tests/conformance/test_move.py`).
> A claim below is either measured, with where, or marked **open**, with what would settle it.
> **Ported** from the draft PR #23 (written before the rename to cancho); what was corrected in the
> port is listed in §9, so the history in the file is not rewritten silently.

## 0. What D15 already decided, and what this does to it

cancho `docs/agent-toolbox.md` D15 is the project's recorded verdict on the tool
set, and **this document was first written without reading it**. It rules on all
three:

| D15 says | this design | resolution |
|---|---|---|
| **"No tool deletes."** A delete is lex-os's `irreversible-consequential` class, which in a no-human system "must be absent from the grant entirely". | `move --remove` and `--purge` | **`--purge` is dropped.** `--remove` is a rename to a tombstone: the content is kept and it is undone by renaming back, so it is reversible and not the class D15 keeps out. Tombstones accumulate; clearing them is a person's job. **Merged:** the amendment is in cancho `docs/agent-toolbox.md` (cancho#298, "D15: a tombstone is a rename, not a delete"), and `move --remove` is cancho-tools#25. |
| "**No process spawning** ... the language has no `exec` builtin, so `find -exec` and `xargs` are not a thing the toolbox can be tempted into." | `run` | **The premise has changed, the principle has not been revisited.** The language has `Exec` since cancho slice 1 (cancho#274). `run` is allowed to exist only as a tool that **declares** `irreversible-consequential` honestly, so a grant that must not hold it (a no-human supervisor) refuses it by its report, as D15 intends for that class. It is an opt-in tool, not part of the default set an agent is handed (§3). **Not yet amended in cancho:** D15's text still says the language has no `exec`; the amendment goes in with the PR that builds `run` (§6). |
| `cut`/`sort`/`uniq` "**not worth it** ... agents transform text in their own language. `tally` is the one aggregate with a real gain." | `table` | **A reversal, and the weakest of the three, made anyway.** The condition stated in the draft was "built only if an independent asker appears". `table` was built in its own repository without that condition having been recorded as met; the maintainer should say whether D15's verdict is amended (§5, §8). Nothing in this repository changes either way. |
| `diff` deferred; regex `seek` declined (L8, "`std` — large"). | neither is proposed | unchanged. |

## 1. Why these three

The agent benchmark (`docs/agent-bench.md`, on the branch of cancho-tools#35, not yet on
main) kept every command the `bash` arm ran
(127 runs, 64 of them `bash`, five models, one task set the eight tools were
written against). What the eight tools do not cover:

| command (runs) | what it was for | the gap | now |
|---|---|---|---|
| `python3` 2, `perl` 2, `git` 6 | running something | **running a program** | `run`: designed, not built (§3) |
| `mv` 4 | renaming | **moving or deleting a file** | `move`: built (§4) |
| `cut` 8, `awk` 9, `tr` 5 | picking fields, summing | **a table of records** | `table`: built, own repository (§5) |
| `readlink` 2 | what a link points at | `list` could say (a flag, not a tool) | unchanged |

and the tools' own scope tasks (`o1`, `o2`) say the same: a regular expression and
running a program. Caveat, as in the benchmark's §8: the tasks were written around
the eight tools, which biases what an agent reaches for. The repository's bar is two
real askers per feature; these were counted from one task set and are a reason to
design, not yet to ship. The adversarial set (§11 there) and real sessions are the
second asker. For `run` that bar is still the open one (§3, question 1).

## 2. What the platform decides (measured)

* **The ceiling.** `tools.toml` has `max_tools = 10`; `manifest.py --check` fails a
  new `[tool]` past it. Today there are **nine** (`seek`, `write`, `replace`, `peek`,
  `jsonq`, `tally`, `hash`, `list`, `move`), so `run` would be the tenth. `table` is
  outside the count (its own repository, README "a tenth tool, built outside this
  repository"): whether the ceiling then still leaves room for `run` is for the maintainer
  to read, and it does not need a change as long as `table` is not counted here.
  The draft counted "eight plus `run` and `move`"; `move` has since taken the ninth place.
* **A move is one directory.** `dir_rename` is `renameat` on one directory handle
  and `dir_remove` is `unlinkat` (cancho `docs/ROADMAP.md`, #254). So a rename
  and a remove are native; a **cross-directory move is not** without a new
  compiler builtin, and `move` does not pretend to be one.
* **A rename replaces what is there, unless it is the no-replace one.** POSIX
  `renameat` silently overwrites an existing destination.
  *History (kept, measured):* with `dir_rename`, a guarded move had to look first, and the look and
  the rename were two calls, so a process that took no lock could create the destination
  between them. Measured (`move`, `scripts/move_race.py`, cancho-tools#24): a creator
  arriving at a random moment during the move lost its file in **55 of 20,000** trials
  (0.28%; 34 of 20,000 on the Linux x86-64 box), and in **100 of 100** with the rename delayed 30 ms
  under `strace`. At that point the limit was real: the lock stopped other toolbox processes
  and nothing else, and closing it needed a no-replace rename that `dir_rename` did not offer.
  **Closed (cancho-tools#32):** `move` renames with `dir_rename_new` (cancho#351:
  `renameat2(RENAME_NOREPLACE)` on Linux, `renameatx_np(RENAME_EXCL)` on macOS), so the
  check and the rename are one kernel call. The same script, 20,000 random arrivals: **0
  lost** on Linux x86-64 (34 before) and on macOS arm64 (271 before in that run; 55 in the earlier
  one); the delayed case, 100 trials: 0 lost, 100 `conflict.exists`, the creator's file intact. A
  filesystem that cannot refuse atomically (NFS, ntfs-3g, ExFAT) is `io.rename-unsupported`
  (exit 8), with no fallback to a replacing rename. **`write --create` had the same shape and is
  closed the same way (cancho-tools#34, `atomic.create` in `contract/atomic.cho`):** `write_race.py`,
  20,000 random arrivals, 0 lost on Linux (107 before) and macOS (411 before). What stays: a lock-free
  process can still move or delete the *source* between the look and the rename (the rename then
  fails `io.not-found` and nothing is replaced), and `--if-sha256` replacing an existing file is
  still the plain `renameat` under an advisory lock, by design.
* **A name has to leave room for its lock.** The sidecar `<name>.lexsys-lock` is 12
  bytes longer than the name, so a name over 243 bytes cannot be locked; cancho's
  `dir_open_append` answers `EINVAL` for it, not the kernel's `ENAMETOOLONG`. `move`
  refuses such a name with a rule (`path.bad-name`, or `path.too-long` for the source);
  `write` has the same limit and a generic failure for it. (The suffix on disk is still
  `lexsys-`, not `cancho-`: see §8, item 3.)
* **`narrow` takes a literal** (cancho `docs/processes.md` §7.1, measured): the
  directory `run` may start programs from is a source literal, baked at build as
  the MCP server's is (`scripts/mcp.py build --bin`). Still true; cancho `processes.md` §9
  keeps "a bound chosen at deployment" open for `Exec` and `Fs` together.
* **A spawned child's working directory: built.** The draft listed this as a prerequisite not
  built. `exec_spawn_in(&Exec, &Dir, ...)` starts the child in the directory a `Dir` holds
  (`addfchdir_np`, before the `closefrom`); measured on macOS 26.2 and Linux 7.0
  (cancho `processes.md` §4.10). **Merged: cancho#292.**
* **`std.process.capture_both` reads standard error beside standard output: built.** The draft
  listed it as a prerequisite not built. Both channels on one poller (a megabyte of errors before
  any output does not stall), each with its own bound, one deadline, both buffers returned on a
  deadline (cancho `processes.md` §7.2). **Merged: cancho#292** (slice 5 there).
  Measured there and relevant here: the order between the two streams is **not kept**, and
  `TooMuch` does not say which stream overran (a caller compares sizes with its bounds).
* **The contract refuses a repeated flag** (`contract/cli.cho`), so a filter that
  wants several conditions is one value, not several flags.
* **The tools are integers-only** (`introspect` reports `integers_only`); there is
  no sort and no CSV reader in `std` to reuse, and `std.map` is what `tally` groups
  with. (`contract/sort.cho`, a stable merge sort, has since been added: cancho-tools#29.)

## 3. `run`: a program, bounded (the live part)

```
run [--root DIR] [--timeout-ms N] [--max-output N] [--stdin | --stdin-file PATH] -- PROGRAM [ARG...]
```

### 3.1 What is decided (in this design; the maintainer has not reviewed it)

* **PROGRAM is a name, never a path.** It is looked up in one directory, baked in at
  build (`exec("<dir>")`, the row), and that directory is the allow-list: the
  deployer puts in it, or links into it, what an agent may start (`python3`, `git`,
  `make`, a test runner). A name with a `/` or a `..` is refused
  (`run.program-name`); one not there is `run.program-not-found`. A symbolic link in
  that directory leads out of it by design (cancho `processes.md` §2's known gap):
  the allow-list is what the deployer chose to link. The program is started by its
  absolute path inside the narrowed directory, because a relative path under a narrowed
  `Exec` traps (cancho `processes.md` §8, slice 5, "Found").
* **No shell, no environment.** The argument list is exactly the operands after `--`;
  the environment is `PATH=<dir>` and nothing else; standard input is the stdin the
  caller gave, or nothing. The working directory is the root (`exec_spawn_in`, now merged).
* **A deadline and a cap** (`--timeout-ms`, default 30,000; `--max-output`, default
  1 MiB), with `std.process.capture_both`'s measured behaviour: the child is killed at the
  deadline, or at the first byte past a bound, and reaped on every path.
  `capture_both` bounds each channel on its own, so `--max-output` is **per stream** (at most
  twice that is kept); and since the answer to an overrun does not say which stream it was,
  `run` compares the two sizes to its bound to set `truncated` per stream.
* **The answer** is one JSON document: `exit`, `signal`, `timed_out`, `truncated`,
  `stdout`, `stderr`, `milliseconds`. The tool exits `0` when it ran the child,
  whatever the child did; a child ended by the tool is `precondition`-class
  (`limit.timeout`, `limit.output-too-large`, exit 8), not a silent success. The two streams are
  separate fields; their interleaving is not reported, because it is not kept (§2).
* **What `run` does not do: it does not sandbox the child.** It bounds the *invocation*
  (what starts, with which arguments and environment, for how long, with how much
  output) and nothing the child then does. The child has its user's whole
  authority. So the guarantee list is short on purpose: `bounded_memory` and
  nothing about determinism, idempotence or reversibility (`reversibility:
  irreversible-consequential`, D15's class, §0), and `introspect` says so. Containment is the deployment's
  (the identity and preflight of `docs/agent-bench.md` §11.4), and an agent runtime
  that holds `run` has stopped holding the "no tool can execute" property.
* **Opt-in, not in the default set.** Because it declares the class D15 keeps out of a
  no-human grant, `run` is built and shipped as a tool a deployment chooses to
  install and the MCP server chooses to list (a build flag of `scripts/mcp.py`), not
  one every agent is handed; the nine others are unchanged.

### 3.2 What blocks it

**In cancho: nothing known.** The two prerequisites the draft named are merged (cancho#292:
`exec_spawn_in` and `capture_both`), on top of `Exec`, the pipes and the poller (#274, #275), `std.process.capture`
(#276, the MCP server's own use, cancho-tools#17) and close-on-exec on every descriptor (#271).
Checked against cancho `docs/processes.md` (§4.10, §7, §7.2, §8 slice 5, §9) and
`gh pr list -R alpibrusl/cancho --state merged --search process`. What cancho's §9 still lists open
does not block `run`:

| open in cancho `processes.md` §9 | effect on `run` |
|---|---|
| a bound chosen at deployment | the programs directory is baked at build (as the MCP server's); a deployment that moves it rebuilds `run` |
| `Stdio::Merge` (needs an edition) | none: `capture_both` serves the asker; stdout and stderr are separate fields |
| children of a parent that dies (`PR_SET_PDEATHSIG`, Linux only) | a killed `run` could orphan a child; the deadline and the reap cover every path of a live `run` only. **Open:** whether that is acceptable (§3.4, question 6) |
| spawning beneath a `Dir`, a working directory that is a path | none: the working directory is the root `Dir` |
| WASI: `exec_spawn_in` is refused (cancho#320) | none: these tools are native only |

**In this repository** (found when this section was re-read against the current sources; the draft
did not see them):

1. **The authority row is not the draft's.** The draft said "`exec("<dir>")`, `clock`, `poll`,
   `child_signal`, `pipe_read`, `pipe_write`, and no `fs_*`". Two of those are wrong for a
   *tool* here: (a) `exec_spawn_in` takes a `Dir`, which comes from an `Fs` the program holds
   (cancho `processes.md` §4.10), so `run` needs `fs_read("")` and `dir_read`-class labels to open
   `--root` (the exact set is to be derived, not guessed); (b) `clock` is needed for the
   deadline, and **`scripts/manifest.py` forbids `clock` to every tool** (`FORBIDDEN = {"ffi", "net_out",
   "net_in", "clock"}`; `tools.toml` says the same). The MCP server holds `clock` for the same reason
   but is not a `[tool]`. So `run` cannot be built under the present policy without a decision (§3.4, question 2).
2. **A `[run]` ceiling in `tools.toml`** would be the reviewable place for whatever is decided in 1.

### 3.3 The shape of the work, once unblocked

Authority (derived and held by a test, as the server's is): `exec("<dir>")`, `poll`,
`child_signal`, `pipe_read`, `pipe_write`, `args`, `heap`, `err_write`, `io_write`, the `Fs` labels of
3.2 (1a), and `clock` if question 2 is answered yes. Built the way these tools are: the design above,
then conformance (M1 to M9 as they apply), mutants shown killed, `introspect` and the MCP definition
generated, the authority row held by a test. Gates specific to `run`: the argv reaches the child
byte for byte (no shell: a `;` or `$(...)` is one byte of one argument, as the server's tests do); the
environment is exactly `PATH=<dir>`; the child's `pwd` is the root; a flood of 1 MiB on each stream
under the cap and a deadline each end the child and leave no zombie; and `--dry-run` is not offered
(a program run is not a plan).

### 3.4 Open questions for the maintainer

1. **Is there a second asker?** The repository's bar is two real askers. The draft's evidence is one
   task set (`python3` 2, `perl` 2, `git` 6 of 127 runs, scope tasks `o1`/`o2`). Real sessions or the
   adversarial set would settle it; until then `run` is a design.
2. **`clock` for a tool.** The deadline needs a `Clock`; the policy forbids `clock` to every tool. Options:
   allow it for `run` alone (a `tools.toml` row and a reason), or deadline by an external timer (a
   second process; not obviously smaller). The same question decides whether `run` is a tool or a
   mode of the MCP server, which already holds `clock`.
3. **The D15 reading.** Is an opt-in tool that declares `irreversible-consequential` honestly the
   intended resolution of "no process spawning"? The maintainer approved the tombstone reading
   (cancho#298); nothing is recorded for this one. If yes, D15's text is amended with the PR that
   builds `run`.
4. **`--stdin-file`.** Whether it may name a path beneath `--root` (opened with `toolbox.place`) or
   only `--stdin` is offered (the MCP route; the MCP definition would give `stdin` as a string, as
   `write`'s is). The first is convenient and the second smaller.
5. **The cap.** Per stream (as `capture_both` bounds it; at most twice the figure is kept) or in total?
   The draft said one cap; the primitive is two.
6. **Orphans.** A `run` that is itself killed leaves its child running (`PR_SET_PDEATHSIG` is
   Linux-only and is open in cancho `processes.md` §9). Acceptable, or a prerequisite?
7. **The ceiling.** Nine tools are here; `table` is counted as outside. Does `run` take the tenth place,
   or is the ceiling to be read as including `table` (then it is full)?
8. **Where the programs directory comes from.** A literal baked at build (the server's way) means each
   deployment builds `run`. Wait for cancho's "bound chosen at deployment", or accept the build?

**Answered by the maintainer (2026-10-07).**

* **Question 2, accepted:** `run` may hold `clock`, as an explicit exception in `tools.toml` with its
  reason, for the deadline. No other tool may; the manifest policy for every other tool is unchanged.
* **Question 3, accepted:** an opt-in tool that declares `irreversible-consequential` honestly is the
  intended resolution of "no process spawning". D15's text is amended with the PR that builds `run`.
* **Question 1 and the status of `run`: held.** `run` is **not** to be built yet. It waits for a second
  asker (question 1); until then it stays a design. Questions 4 to 8 are not decided and are asked
  again when `run` is taken up.

### 3.5 What it does to the benchmark

* **Tasks first, frozen before any run:** a "run the tests and tell me what failed" (and the two
  scope tasks `o1`, `o2`, which move out of "outside the tools' scope" for the arms that now
  have what they need, and stay there for the rest). The move and the table tasks of the draft are
  now the benchmark's to add: cancho-tools#36 (open) is the table benchmark, stacked on #35.
* **The `mcp` arm with `run` is a different arm** from the one without it, and
  §11's adversarial tasks are run both ways: with `run` in the arm the injected
  instruction (a2) has something to execute, which is the honest cost of the tool. Whatever is
  reported, an arm with `run` is reported apart from one without, because the "no tool can
  execute" property is what it spends.

### 3.6 Opt-in and the class, once more

`run` declares `irreversible-consequential`. A deployment that must not hold that class (D15) leaves
it out of the build and out of the server's list; `introspect` says why, and the authority row
(`exec("<dir>")`) is what a supervisor reads. The default set (nine tools here, `table` beside them)
holds no `exec`.

## 4. `move`: a rename, a tombstone, and nothing across directories

> **Built and merged: cancho-tools#24 (rename), #25 (`--remove`, the tombstone), #32 (the rename race
> closed with `dir_rename_new`: 0 lost in 20,000 trials on Linux and macOS, §2).** The maintainer approved
> the reading of D15 in §0; the amendment is cancho#298. The tombstone is `.NAME.removed-<first 8 hex of the hash>`,
> the hash is required, there is no purge. What follows records the design; the rename-only remarks are
> the first build's. The mutants found two defects the design did not foresee: `--if-sha256` on a link was
> reported as a directory, and on a FIFO the hash would have blocked on `open`, so a
> hash is now asked only of a regular file and every other kind is refused before any open.

```
move [--root DIR] [--if-sha256 HEX] [--dry-run] PATH NEWNAME          # rename in place
move [--root DIR]  --if-sha256 HEX  [--dry-run] --remove PATH         # tombstone, reversible
```

* **A rename** moves `PATH` to `NEWNAME`, one component in the **same directory**
  (`path.name-has-separator` otherwise). It never overwrites: an existing
  destination is `conflict.exists` and nothing changes. `--if-sha256` says the
  file is what the caller read (`precondition.hash-mismatch`, exit 5, as `write`'s).
* **A remove is a rename to a tombstone** in the same directory,
  `.<name>.removed-<first eight hex of the content hash>`, and **requires**
  `--if-sha256`: a file is never removed on a guess. It is undone by renaming the
  tombstone back. This is the consequence of §2: there is no trash directory,
  because that is a cross-directory move.
* **There is no purge.** A tool that deletes for good is D15's `irreversible-consequential`
  (§0), so the tombstone is the end of what `move` does, and **a remove is always
  undoable**: `move .name.removed-… name`. The cost is that tombstones stay until a
  person clears them.
* **Mechanics** are `write`'s: every path opens beneath `--root` following no link
  (`toolbox.place`), the work is done by handle (`dir_rename_new`, `dir_remove`), under
  the `<path>.lexsys-lock` sidecar, and `--dry-run` reports what would happen with
  exit 9 and changes nothing.
* **Authority** is `write`'s: `dir_read`, `dir_write`, `fs_read("")`, `heap`, the
  console and `args`; no `fs_write`, no network, no clock (`tools.toml` `[move]`).
* **Guarantees:** `deterministic`, `atomic` (one `renameat2`), `requires_precondition`
  (a remove without `--if-sha256` is refused), `dry_run`, `bounded_memory`.
  **Reversibility: `irreversible-bounded`**, `write`'s class, and never
  `irreversible-consequential` (cancho `docs/agent-toolbox.md`, as amended). **Open:** a rename is
  undone by renaming back, which could earn `reversible-cheap`; the classes are lex-os's and this
  repository does not own their reading, so the conservative one is used until the maintainers say
  otherwise. **`idempotent`** only as `write` is: a retried rename whose destination already
  holds the file's hash answers `changed: false`.
* **Gates, in the repository's own terms** (M3, M7, M8): every rule has a fixture;
  `--dry-run` under `strace` makes no mutating call; two movers racing for one
  destination, exactly one wins (200 races, as `write`'s); a link, `..`, an absolute
  path and a name with `/` each a tag, never a trap; and the race of §2 measured
  (`scripts/move_race.py`, now a regression test that fails on the replacing rename;
  `scripts/move_mutants.py`: 24 of 24 killed after cancho-tools#34).
* **Rules:** `conflict.exists` (the draft called it `precondition.exists`), `path.name-has-separator`,
  `io.rename-unsupported` (in the shared catalogue since cancho-tools#34, 39 rules); the rest are the shared `args.*`, `path.*` and `io.*`.

## 5. `table`: built, in its own repository

**https://github.com/alpibrusl/cancho-table** (README, `docs/`). The design that stood here is
replaced by that repository's documents, which are far beyond it; this section only says what exists
and where the decisions live, and keeps the records that belong to this repository.

**What it has now** (cancho-table README; PRs #1 to #29): select columns (`--select`, #2), filter
(`--where`, #3), group and aggregate (`--group`, `--agg`: count, sum, min, max, distinct, mean, #3),
sort (`--sort` of groups, `--order-by` of rows with a bounded top-N, #15, #16), **exact numbers**
(`:int`, `:dec(S)`, `:float` in filters, keys, sorts and every aggregate, #17, #18, #22, #27, #28; design
in `docs/numbers.md`), a **type report** (`--report types`, #29), an **MCP server** with one tool
derived from `introspect` (#23), a **parallel scan** whose answer is the sequential one byte for byte
(`--threads N`, #5), and a self-describing contract (`introspect`, `skill`, #24). Refusals are tagged
and carry a repair.

**Where to read the rest, instead of repeating it here:**

* `docs/backlog.md` (https://github.com/alpibrusl/cancho-table/blob/main/docs/backlog.md): what it does
  not do yet, in order, and the DuckDB yardstick.
* `docs/readers.md` (https://github.com/alpibrusl/cancho-table/blob/main/docs/readers.md): standard
  input, **JSON lines**, the reader interface, JSON-lines output.
* `docs/query.md` (https://github.com/alpibrusl/cancho-table/blob/main/docs/query.md): `--query` (the SQL
  subset that translates to the flags), `--explain`, the surface.
* Benchmarks, current numbers: https://alpibrusl.github.io/cancho-table/benchmarks.html

**The format stance (decided; was "stance" in the draft).** The engine is tabular and the input is a
reader, so a format is an addition and not a new tool. CSV and TSV are built. JSON lines, standard
input, and `--query` are **designed and decided** in `docs/readers.md` and `docs/query.md` (cancho-table#25,
docs and spikes; the build follows those documents' own stages). A record-reader *trait* was decided
against there, with a measured reason (a branch in the shared loop cost 15 percent): the interface is the
engine's existing boundary, one loop per format. **Parquet: not now,** for the reasons the draft gave
(a Thrift decoder, codecs that `std` does not have, a column-wise engine, which is cancho
`docs/parallelism.md` §6, not a reader); if a user has a file they need: a read-only subset, every other
encoding or codec a tagged refusal, each piece with its own gate. Not before.

### 5.1 The contract packaging spike: done, and shipped

The draft's question was whether `table` could be a repository of its own on this repository's
`contract/`. Record of the spike (kept):

* **Prerequisite, found by the spike: cancho#299** (merged). `cancho vcs publish` refused `contract/sha.cho`
  (four `[int]` statics) because a static's signature hash left out its name, so two statics of one type
  collided in the store. The fix moves the identity of a static once; this repository's pin moved for it
  (cancho-tools#26, "statics publish into a package").
* **What was done**: `cancho vcs publish --std --store .cancho-vcs --dir contract` made per-module stores,
  committed to a throwaway git repository; a consumer named each module it imports as
  `[dependencies.NAME] git, rev, path = ".cancho-vcs/toolbox.NAME"` and `cancho install` fetched them. A
  one-line program built against `toolbox.rules`, and the real `move` built and ran from the package with the
  same authority report as from the in-tree sources (nine effects, 233 functions, `bounded: true`).
* **The cost: one dependency line per module a tool imports directly**, not one per package, because the
  unit of a store is a module. `toolbox.built` is not in the package and must not be: it is each tool's own
  generated module.

**Decided and done:** the package lives here. `scripts/package.py` publishes `contract/` as `.cancho-vcs/`
(14 per-module stores today), committed, and CI checks it matches `vcs publish --dir contract` (cancho-tools#27,
"Publish contract/ as a package"; `scripts/package.py --check`), so there is one source of truth and no new
repository. cancho-table's `cancho.toml` names nine modules (`cli`, `describe`, `fail`, `limit`, `lines`,
`out`, `path`, `place`, `text`) with this repository as `git` and a commit as `rev`; its first PR (#1) is the
skeleton built on it. The contract grew for it: `extra_rules` (a tool's own rules, cancho-tools#28) and the fail
helpers and a stable merge sort (#29).

### 5.2 Performance against `csvtk`: a dated record

Kept as measured at the time of the draft (2026-10-06); **the current numbers are on the benchmarks
page above**, not here. The README's table then had `seek` at 0.49 s against `grep -F -n -b` 0.51 s and
only `hash` behind (0.52 s against `sha256sum` 0.17 s, OpenSSL's assembly; still the README's figures today).
`csvtk` is multi-threaded Go, which looked like the difference from `grep`, so it was measured (csvtk 0.38.0
from Homebrew, an Apple-silicon Mac with 16 cores at load average about 4.5 from other work, a generated
1,000,000-row, 31.7 MB file with a quoted column, minimum of 5 runs):

| `csvtk` | `-j 1` | `-j 4` | default (`-j` = cores) |
|---|---:|---:|---:|
| `cut -f status,bytes` | 0.182 s | 0.202 s | 0.200 s |
| `filter -f 'bytes>50000'` | 0.283 s | 0.251 s | 0.255 s |
| `freq -f status` | 0.181 s | 0.205 s | 0.214 s |

**Its threads do not help on these three**: one thread is as fast as sixteen, so the figure to match was
the single-thread one. Not measured then: wider files, a sort, a join. **What happened since** (cancho-table,
not re-measured here): a parallel scan was built (`--threads`, #5), and DuckDB was added as a yardstick
(`docs/backlog.md`, #4). The draft's threads paragraph (T0 to T3 built; no atomics or channels, so
partial results merge in order through the job struct; a quoted newline makes a split at an arbitrary byte
unsafe, so a worker resynchronises; "gated at more than 1.5x at 4 threads and the same bytes") became
cancho-table `docs/parallel.md`; read the numbers there.

The draft's gates for `table` stand as practice, and are cancho-table's now: linear time and bounded
memory on the largest input accepted; a ratio against the incumbent (about 2x worse is a defect to work on);
RFC 4180 quoting, embedded newlines and BOM, correctness before speed.

## 6. In what order (as it went)

1. **cancho:** a working directory for a spawned child, and standard error beside standard output:
   **done** (cancho#292).
2. **`move`** here, with no compiler change: **done** (#24, #25), then closed against the rename race with
   one compiler builtin (`dir_rename_new`, cancho#351; cancho-tools#32), and `write --create` the same way (#34).
3. **The `contract/` spike**, then the `table` repository: **done** (§5.1; cancho-table#1 onward).
4. **`run`** here: **held** (maintainer, 2026-10-07): questions 2 and 3 of §3.4 are accepted, and it waits for a second asker (question 1).

The D15 amendments (§0): "no tool deletes" gained "a tombstone is a rename" (**merged, cancho#298**). "No
process spawning" gains `run` as an opt-in tool of the `irreversible-consequential` class **with the PR
that builds `run`**. The `cut`/`sort`/`uniq` verdict is to be amended now that `table` exists: **not yet
done; for the maintainer** (§8).

Each is built the way these tools are: the design above, then conformance (M1 to M9
as they apply), mutants shown killed, `introspect` and the MCP definition generated,
and the authority row derived and held by a test.

## 7. What they did and will do to the benchmark

* `move` and `table` tasks: cancho-tools#35 (the benchmark, ported to the renamed repository) and #36 (the
  `table` questions, stacked on it) are open. Tasks are frozen before any run.
* `run`: §3.5.

## 8. What to build next in cancho-tools

In this order of value to cost, the first being the only new tool:

1. **`run`** (§3): **held** until there is a second asker (§3.4, question 1); questions 2 and 3 are accepted.
2. **Share the MCP server with cancho-table's copy.** `server/mcp.cho` is 1,022 lines here; cancho-table's
   `server/mcp.cho` is a copy with one tool in place of nine (1,094 lines there). By the measure taken
   when this was written, about 700 of 1,000 lines are generic (JSON-RPC framing, schema walking, the
   argv builder, the capture and the refusals) and the rest is the tool table. Two copies are two places to fix a defect. Options to settle: publish the generic
   part in the `contract/` package (the cost of §5.1: one module, one dependency line), or as a
   package of its own. Not measured here beyond that figure; the split needs a pass over both files.
3. **Rename the on-disk `.lexsys-lock` / `.lexsys-tmp` suffixes to `.cancho-lock` / `.cancho-tmp`.**
   cancho-tools#30 renamed the repository's text but left the sidecar and temporary suffixes (`contract/atomic.cho`,
   `tools/move/move.cho`, tests, scripts) as they were, which is why this document says `.lexsys-lock`.
   **This is a behaviour change, not a rename of words:** a lock file created by the old binary and one created
   by the new are different names, so during a mixed deployment two processes (one old, one new) hold
   different locks on one file and the guarantee "of two racing writers exactly one wins" is not kept
   across them; leftover `.lexsys-tmp` files are not recognised as stale by the new binary; the name limit
   moves with the suffix length (12 bytes today: `.lexsys-lock` and `.cancho-lock` are both 12, so the 243-byte
   limit is unchanged). Needs its own PR with a gate for the mixed case, and a note in `docs/reference.md`.
4. **The contract improvements found by building `table`** (from cancho-table's skill and adversarial work, #6 and #24, and its `docs/backlog.md`; each is a change to `contract/`, so the package is regenerated and the
   consumers' pins move):
   * `describe.Tool` forms and examples as data, not literals: `usage` is a literal that cannot be generated
     from the flag table, so `table` tests it against the table instead (cancho-table#24); per-rule hints.
   * `skill` printing the operands (the positional arguments), as it prints the flags.
   * per-format output: a tool that prints JSON or CSV or text says so in `introspect`, not in prose.
   * an optional ceiling for limits: a deployment lowers a limit but cannot raise it past the tool's.
   * **a shorter skill.** `table skill` was 24,638 bytes after cancho-table#24 (14,648 before), because the
     summary appears twice in it; the duplicated summary and the authority block can go (measured in that PR;
     the same shape is in every tool's `skill` here, which has not been measured).
   * hints for the shared rules (`args.*`, `path.*`, `io.*`), so a tool does not write its own.
5. Documents that follow from the above: amend `docs/agent-toolbox.md` D15's `cut`/`sort`/`uniq` row (§6);
   merge #35 so the references to `docs/agent-bench.md` resolve on main.

## 9. What the port corrected (so the history is visible)

The draft (PR #23, branch `next-tools-design`) said, and this version changes:

* Status "designed, nothing built" and "`run` and `move` go in this repository, which makes ten tools":
  `move` is built and merged; nine tools are here; `run` would be the tenth.
* `precondition.exists` for the rename refusal: the rule is `conflict.exists` (cancho-tools reference.md; #24, #25).
* The 0.28% race as "the limit is real ... filed": superseded by cancho-tools#32 (history kept in §2).
* "`write` ... a generic failure" and the race for `write --create`: closed by #34.
* The `run` prerequisites "in cancho, not built": merged, cancho#292.
* The `run` authority "and no `fs_*`" and `clock`: wrong against `exec_spawn_in` and `scripts/manifest.py` (§3.2).
* `table` "the repository is not created" and "built only if an independent asker appears": built (§5); the
  condition is flagged in §0.
* The spike's "pinned compiler `fe32ac2` has the bug": the pin moved (cancho-tools#26).
* The rename conventions (lex-sys/lexsys to cancho, `.ls` to `.cho`, `lexsys-tools` to `cancho-tools`,
  `lexsys-table` to `cancho-table`, `LEX_SYS` to `CANCHO`), after cancho#347's `scripts/rename_to_cancho.py`,
  with one exception on purpose: the on-disk suffixes `.lexsys-lock` / `.lexsys-tmp` are what the binaries
  write today, so they stay in this document (§8, item 3). A mechanical rename of the text would have
  made the document claim a behaviour the code does not have.
