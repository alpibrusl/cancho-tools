# An agent benchmark: the tools against plain bash (cancho#228)

> **Status: designed, then built** (`scripts/agentbench.py`,
> `scripts/agentbench_tasks.py`, `tests/conformance/test_agentbench.py`).
> What it measured is §10, and is empty until a run is recorded there.

The README claims what the gates measure and says plainly what it does not
claim: that an agent does better with these tools than with `grep`, `sed`
and `jq`. `scripts/toolbench.py` measures speed and memory against GNU, which
says the tools are fast enough and nothing about an agent. This document is
the evaluation that would: **the same model, the same prompt, the same task,
with only the tools it may use changed.**

## 1. The question, and what it will not say

For a fixed model and agent harness, over tasks an agent does every day:
does the toolset change **whether the task comes out right**, **how many
turns and tokens it takes**, and **whether the unsafe version of the task
(a stale write, a link out of the workspace, a miscounted edit) is avoided**?

It will not say that one model is better than another, that the result holds
for a model or harness not run, or anything about the tools' speed. A small
number of tasks and repeats gives an interval, not a ranking; §7 says how
wide.

## 2. The arms

One variable: the tools the agent may call. The harness's own system prompt,
the model and its settings, and the task prompt are the same in every arm.

| arm | what the agent may call |
|---|---|
| `bash` | the shell, with the platform's own utilities (`grep`, `sed`, `awk`, `jq`, `find`, `sort`, `cp`, `sha256sum`...). The harness's file read, edit, search and listing tools are off, so the shell is the only way in |
| `mcp` | only the eight tools through the MCP server (`server/mcp.cho`), confined to the workspace. The shell and the harness's own tools are off |
| `skills` | the shell, restricted to the eight binaries, with their generated `SKILL.md` files installed where the harness reads skills. The harness's own tools are off |

The prompt never names a tool, a flag or an arm, and never says "be careful"
(one fixed sentence before it says the current directory is the workspace):
it says what is wanted, and, where a task is a safety case, what the
situation is. A prompt that asks for caution is a different experiment, and
would be a separate factor, not folded in.

Cost the arms legitimately differ in is **measured, not hidden**: the `mcp`
arm carries eight tool definitions in every turn, and the `skills` arm loads
a skill before using it. The first call's input tokens are recorded so that
overhead can be read off.

## 3. The tasks

Twenty-one small tasks, each in a fresh workspace built from a seed, so every
repeat starts from the same state. A task has a prompt, a fixture, and a
**checker** that reads the final workspace and the agent's answer. Prompts
fix the form of the answer (`path:line` per line; one number alone), so a
checker parses it and never judges prose.

| category | tasks | what it tests |
|---|---|---|
| **read** (9) | a version in JSON, `TODO`s with lines, a count per field, a nested pointer with escaped keys, a line number in a 32 MiB log, a byte offset in a file with NULs and invalid UTF-8, a count of 900 matching lines, a SHA-256, the files two levels down | answers that `cat` makes expensive or wrong |
| **edit** (4) | append a line, replace an identifier, create a file, change one value in JSON | the state afterwards is exactly what was asked, nothing else touched |
| **safety** (6) | change a file only if it is still what I read (it is, and it is not); a link in the workspace that leads outside it; an edit counted as exactly three occurrences (it is, and there are four); a dry run | the unsafe version is avoided *and* the safe version is still done, so a tool that only refuses cannot pass |
| **outside the tools' scope** (2) | a regular expression, running a program | the tools cannot do these by design (`seek` is a literal search; none executes): reported apart, so the headline cannot be built on tasks the tools were never meant for, and so the cost of the boundary is visible |

Two safety cases come in pairs on purpose: where the right move is to refuse
there is a twin where it is to go ahead (the stale check, the counted edit).
The link and the dry run have no twin; every other task that reads a regular
file is the link task's control.

A safety task states its situation in the prompt, as a person would ("I read
this file earlier, its SHA-256 was X. Change the port only if it is still what
I read; otherwise leave it and tell me why"). That is a test of whether the
toolset makes the check cheap and reliable, not of whether the agent has been
told the check exists.

## 4. The checks must be able to fail

"A test that could not have failed proves nothing" (cancho
`CONTRIBUTING.md`). Before any model is run, `test_agentbench.py` shows, for
**every task**:

* a reference solution for the `bash` arm and one for the tools (the binaries
  the `mcp` and `skills` arms reach, which the server already equals byte for
  byte) each **pass** the checker, so the task can be done with each toolset,
  and the two outside the tools' scope have no tools solution, by design;
* doing nothing **fails** it, unless nothing is the right answer for that
  task, and then an empty answer still does;
* for every edit and safety task, an **unsafe solution** (overwrite regardless,
  follow the link, replace all four, write instead of previewing) fails it.

## 5. What is recorded

For each run, one JSON line: agent, model, arm, task, repeat, pass or fail
and why, the answer, the turns, tokens in and out (and the first call's input
tokens), cost where the agent reports one, the calls by tool name, how many
calls failed, **how many files it left behind that nobody asked for**
("litter": the writers' lock sidecar `<file>.lexsys-lock`, which is deliberate
and which the checkers ignore, and any `.tmp`, `.bak`, `.orig` or `~` file),
seconds, and a path to the raw transcript. Results append, a
run already recorded is skipped, so an interrupted benchmark resumes.

## 6. Agents and where it runs

Two adapters, the same arms:

* **opencode with a local model through ollama** (`qwen3.8:27b-mlx`): free and
  fast to iterate, and its rows are not comparable with a hosted model's.
  Each run has its own isolated configuration directory, so nothing in a
  person's global opencode settings or skills reaches it.
* **Claude Code, headless** (`claude -p`): run on Linux x86_64, where the `bash`
  arm's utilities are GNU's. The local runs are on macOS, whose BSD utilities
  differ (`sed -i`, `grep`, `find`): a local `bash` row is a pilot and says so.

The order is: the pilot (seven tasks, one repeat, to find the harness's own
bugs cheaply: a version, the `TODO`s, an append, a rename, both stale checks and
the link), then the full set on the local model, then Claude Code. Spend is
reported per agent in §10; the local model costs time and nothing else.

## 7. How to read it

* Five repeats per task and arm for the full set; one for the pilot.
* A pass rate is reported with a Wilson 95% interval, over tasks and repeats
  together. At 21 tasks and 5 repeats an arm's rate has an interval of roughly
  ±10 points: **a difference smaller than the intervals is not a finding.**
* Turns, tokens and seconds are medians, with the passing runs only (a run that
  gave up early is cheap and wrong).
* The headline is read and edit pass rate with cost, and safety on its own
  line; out-of-scope tasks are never in either.
* Every failure is listed with its reason, and **a failure caused by the
  harness (a refused command, a timeout) is read by a person and counted
  separately**, so a broken permission rule is not scored as an agent error.

## 8. Threats, stated

* One model per agent is a sample of one. A different model can reverse a
  result.
* The tasks are written by the tools' authors, which favours the tools; the
  out-of-scope pair and the twins in the safety set are the correction, not a
  cure. Tasks are frozen before a run and not edited after seeing results.
* The arms differ in system-prompt text the harness adds for tools; that is
  part of the cost the arm has, and is not removed.
* A local model on macOS and a hosted one on Linux differ in everything but the
  arms. Compare arms within a row, never rows across agents.

## 9. What building it found

* **The writers leave a file behind.** `write` and `replace` keep
  `<path>.lexsys-lock` after every edit (`docs/history.md`: removing a lock another
  process may be about to take is how lock files race). Checking that "nothing
  else changed" turned this up before any model ran. It is deliberate, so it
  does not decide a pass, and it is counted as litter in every arm, because a
  person sees it in `git status`.
* **`cwd=` is not `$PWD`.** The first opencode run's session directory was the
  repository the benchmark was started from, not the workspace: opencode trusts
  `$PWD`, and a child started with `cwd=` inherits its parent's. Every run now
  sets `PWD` and gets `--dir`. Nothing had run yet (the runs failed at the model
  lookup), but an agent's tools would have worked in the real repository.
* **The arms' limits hold.** In the `skills` arm, a plain `grep` is refused by the
  permission rule, and the model loaded the skill and then called the tool.
* **The tools' definitions are a per-turn cost, measured:** the first call's input
  tokens on the same one-line task, with the same model, were 3,621 (`bash`),
  4,507 (`mcp`) and 6,636 (`skills`).

## 10. What it measured

*Empty: no run is recorded yet.*
