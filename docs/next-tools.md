# The next tools: `run`, `move` and `table` (design)

> **Status: designed, nothing built.** `run` and `move` go in this repository,
> which makes ten tools, the ceiling in `tools.toml` (D1), so nothing in the
> ceiling changes. `table` goes in a repository of its own (§5). Every claim
> below is either measured, with where, or marked **open**, with what would
> settle it.

## 1. Why these three

The agent benchmark (`docs/agent-bench.md`) kept every command the `bash` arm ran
(127 runs, 64 of them `bash`, five models, one task set the eight tools were
written against). What the eight tools do not cover:

| command (runs) | what it was for | the gap |
|---|---|---|
| `python3` 2, `perl` 2, `git` 6 | running something | **running a program** |
| `mv` 4 | renaming | **moving or deleting a file** |
| `cut` 8, `awk` 9, `tr` 5 | picking fields, summing | **a table of records** |
| `readlink` 2 | what a link points at | `list` could say (a flag, not a tool) |

and the tools' own scope tasks (`o1`, `o2`) say the same: a regular expression and
running a program. Caveat, as in the benchmark's §8: the tasks were written around
the eight tools, which biases what an agent reaches for. The repository's bar is two
real askers per feature; these are counted from one task set and are a reason to
design, not yet to ship. The adversarial set (§11 there) and real sessions are the
second asker.

## 2. What the platform decides (measured)

* **The ceiling.** `tools.toml` has `max_tools = 10`; `manifest.py --check` fails a
  new `[tool]` past it. Eight tools plus `run` and `move` is ten.
* **A move is one directory.** `dir_rename` is `renameat` on one directory handle
  and `dir_remove` is `unlinkat` (cancho `docs/ROADMAP.md`, #254). So a rename
  and a remove are native; a **cross-directory move is not** without a new
  compiler builtin, and `move` does not pretend to be one.
* **A rename replaces what is there.** POSIX `renameat` silently overwrites an
  existing destination, so a guarded move has to look first (§4). **Open:** a
  race between the look and the rename is closed against the tools (the writers'
  lock sidecar) and not against an arbitrary process; `write` has the same limit.
  The gate in §4 measures it rather than asserting it.
* **`narrow` takes a literal** (cancho `docs/processes.md` §7.1, measured): the
  directory `run` may start programs from is a source literal, baked at build as
  the MCP server's is (`scripts/mcp.py build --bin`).
* **A spawned child starts in the parent's working directory** (cancho
  `docs/processes.md` §9, "the working directory": `addfchdir_np` is the shape and
  *nothing asks yet*). `run` is the asker: a test must run in the workspace.
  **Prerequisite, in cancho, not built.**
* **`std.process.capture` reads one stream** and gives the child's standard error to
  the caller's choice (§7.1 there): a compiler writes its errors to standard error.
  **Prerequisite, in cancho, not built:** capture both, or merge them.
* **The contract refuses a repeated flag** (`contract/cli.cho`), so a filter that
  wants several conditions is one value, not several flags.
* **The tools are integers-only** (`introspect` reports `integers_only`); there is
  no sort and no CSV reader in `std` to reuse, and `std.map` is what `tally` groups
  with.

## 3. `run`: a program, bounded

```
run [--root DIR] [--timeout-ms N] [--max-output N] [--stdin | --stdin-file PATH] -- PROGRAM [ARG...]
```

* **PROGRAM is a name, never a path.** It is looked up in one directory, baked in at
  build (`exec("<dir>")`, the row), and that directory is the allow-list: the
  deployer puts in it, or links into it, what an agent may start (`python3`, `git`,
  `make`, a test runner). A name with a `/` or a `..` is refused
  (`run.program-name`); one not there is `run.program-not-found`. A symbolic link in
  that directory leads out of it by design (cancho `processes.md` §2's known gap):
  the allow-list is what the deployer chose to link.
* **No shell, no environment.** The argument list is exactly the operands after `--`;
  the environment is `PATH=<dir>` and nothing else; standard input is the stdin the
  caller gave, or nothing. The working directory is the root (prerequisite, §2).
* **A deadline and a cap** (`--timeout-ms`, default 30,000; `--max-output`, default
  1 MiB), with `std.process.capture`'s measured behaviour: the child is killed at the
  deadline, or at the first byte past the cap, and reaped on every path.
* **The answer** is one JSON document: `exit`, `signal`, `timed_out`, `truncated`,
  `stdout`, `stderr`, `milliseconds`. The tool exits `0` when it ran the child,
  whatever the child did; a child ended by the tool is `precondition`-class
  (`limit.timeout`, `limit.output-too-large`, exit 8), not a silent success.
* **What `run` does not do: it does not sandbox the child.** It bounds the *invocation*
  (what starts, with which arguments and environment, for how long, with how much
  output) and nothing the child then does. The child has its user's whole
  authority. So the guarantee list is short on purpose: `bounded_memory` and
  nothing about determinism, idempotence or reversibility (`reversibility:
  irreversible`), and `introspect` says so. Containment is the deployment's
  (the identity and preflight of `docs/agent-bench.md` §11.4), and an agent runtime
  that holds `run` has stopped holding the "no tool can execute" property: the
  benchmark must report an arm **with** `run` apart from one without.
* **Authority** (to be derived and held by a test, as the server's is): `exec("<dir>")`,
  `clock`, `poll`, `child_signal`, `pipe_read`, `pipe_write`, and no `fs_*`.
* **Open:** what a `--stdin-file` may name (a path beneath `--root`, opened with
  `toolbox.place`) versus only `--stdin` (the MCP route); the first is convenient
  and the second smaller. The MCP definition would offer `stdin` as a string, as
  `write`'s is.

## 4. `move`: a rename, a tombstone, and nothing across directories

```
move [--root DIR] [--if-sha256 HEX] [--dry-run] PATH NEWNAME          # rename in place
move [--root DIR]  --if-sha256 HEX  [--dry-run] --remove PATH         # tombstone, reversible
move [--root DIR]  --if-sha256 HEX  [--dry-run] --purge  PATH         # delete a tombstone for good
```

* **A rename** moves `PATH` to `NEWNAME`, one component in the **same directory**
  (`path.name-has-separator` otherwise). It never overwrites: an existing
  destination is `precondition.exists` and nothing changes. `--if-sha256` says the
  file is what the caller read (`precondition.hash-mismatch`, exit 5, as `write`'s).
* **A remove is a rename to a tombstone** in the same directory,
  `.<name>.removed-<first eight hex of the content hash>`, and **requires**
  `--if-sha256`: a file is never removed on a guess. It is undone by renaming the
  tombstone back. This is the consequence of §2: there is no trash directory,
  because that is a cross-directory move.
* **A purge deletes only a tombstone** (`precondition.not-a-tombstone` for anything
  else), with its hash. So a live file cannot be deleted for good in one call, and
  the first step is always undoable.
* **Mechanics** are `write`'s: every path opens beneath `--root` following no link
  (`toolbox.place`), the work is done by handle (`dir_rename`, `dir_remove`), under
  the `<path>.lexsys-lock` sidecar, and `--dry-run` reports what would happen with
  exit 9 and changes nothing.
* **Authority** is `write`'s: `dir_read`, `dir_write`, `fs_read("")`, `heap`, the
  console and `args`; no `fs_write`, no network, no clock.
* **Guarantees:** `deterministic`, `atomic` (one `renameat`), `requires_precondition`
  (a remove or purge without `--if-sha256` is refused), `dry_run`, `bounded_memory`.
  **`idempotent`** only as `write` is: a retried rename whose destination already
  holds the file's hash answers `changed: false`.
* **Gates, in the repository's own terms** (M3, M7, M8): every rule has a fixture;
  `--dry-run` under `strace` makes no mutating call; two movers racing for one
  destination, exactly one wins (200 races, as `write`'s); a link, `..`, an absolute
  path and a name with `/` each a tag, never a trap; and the **open** question of §2
  measured: a destination created between the look and the rename by a process that
  does not take the lock.
* **Rules (new):** `precondition.exists`, `precondition.not-a-tombstone`,
  `path.name-has-separator`; the rest are the shared `args.*`, `path.*` and `io.*`.

## 5. `table`: a table of records, in its own repository

A dataframe-style tool: `pick`'s fields, `tally`'s counting, `awk`'s sums, over
CSV, TSV and JSON lines, with named columns. It is **not** pandas: arbitrary pandas
is arbitrary code, and giving that up is what keeps these tools bounded. What it
covers is the declarative core.

* **Operations:** select columns; filter rows; group and aggregate (`count`, `sum`,
  `min`, `max`, `distinct`, and `mean`); sort; top-N with a `next` cursor; and
  `describe` (per column: count, empty, distinct, min, max).
* **Both front ends, one engine.** Typed flags (`--select a,b --where 'status>=400'
  --group status --agg count,sum:bytes --sort -count --top 10`), which fit an MCP
  schema, and one `--query` string (`select status, count(*), sum(bytes) where
  status >= 400 group by status order by 2 desc limit 10`), which people and models
  know. Both compile to **one plan**, and a gate asserts the same plan gives the same
  bytes whichever way it was written; giving both is a conflict (`args.conflict`).
* **The filter grammar** is one value (§2): comparisons `= != < <= > >=`, `contains`,
  `in (...)`, joined by `and`. **No `or`, no expressions, no functions** in v1.
* **Exact arithmetic.** Integers only: `sum`, `min`, `max`, `count` are exact; `mean`
  is a fixed-point decimal with `--scale N` digits (default 3) and a stated
  rounding rule (half to even), computed with integers, so the answer is the same on
  every machine. A column of decimals is read as an exact scaled integer or as a
  string, never a float. **Open:** how decimals in the input are declared.
* **Bounded, like `tally`:** `--max-rows`, `--max-groups` (as `--max-keys`),
  `--max-line-bytes`, each a limit with its own rule and a repair; output is capped
  with `truncated` and `next`.
* **Why a repository of its own.** The ceiling (§2) and the size: a CSV reader, a
  sort and two parsers are a project, not a tool. **Open, and a prerequisite**: it
  needs this repository's `contract/` (argument parsing, errors, path confinement,
  the self-description). A repository consumes another's code through
  `[dependencies.NAME] git = ..., rev = ...` and a published store (cancho
  `docs/package-system.md` §8), as `cancho-hooks` takes `cancho-log`. So `contract/`
  must first be published as a package, from here or as a repository of its own
  that both consume. **A spike settles it before the repository is made:** publish
  `contract/`, build a throwaway program against it with `cancho install`, and
  confirm the tools here still build from the same package.

## 6. In what order

1. **cancho:** a working directory for a spawned child, and standard error beside
   standard output. Each is a small addition to `docs/processes.md` with its own test
   (the section of §9 there it closes).
2. **`move`** here, with no compiler change: the first to be built.
3. **`run`** here, once (1) is merged.
4. **The `contract/` spike**, then the `table` repository.

Each is built the way these tools are: the design above, then conformance (M1 to M9
as they apply), mutants shown killed, `introspect` and the MCP definition generated,
and the authority row derived and held by a test.

## 7. What they do to the benchmark

* **Tasks first, frozen before any run:** a move and a guarded remove (and the
  tombstone's undo), a "run the tests and tell me what failed", and a group-and-sum
  over a CSV. The scope tasks `o1` and `o2` move out of "outside the tools' scope"
  for the arms that now have what they need, and stay there for the rest.
* **The `mcp` arm with `run` is a different arm** from the one without it, and
  §11's adversarial tasks are run both ways: with `run` in the arm the injected
  instruction (a2) has something to execute, which is the honest cost of the tool.
