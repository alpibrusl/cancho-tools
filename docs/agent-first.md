# Agent-first: what is checked, and what is not

"Designed for agents" is easy to claim and hard to check, so this page lists the properties that are checked by a test
that runs every binary (`tests/conformance/test_agent_first.py`), the ones checked elsewhere, and the ones that are not
checked at all. It does not claim that agents do better with these tools than with the incumbents; that needs an
agent in the loop ([cancho#228](https://github.com/alpibrusl/cancho/issues/228)) and has not been run.

## Checked by `test_agent_first.py`, on every tool

* **Complete self-description.** `introspect` carries the tool, version, summary, usage, output kind, schemas, flags,
  operands, exit codes, rules, limits, authority, reversibility and guarantees; every flag and operand has help, every
  rule a summary and a `repairable` class; `skill` names every flag and operand.
* **A refusal is data.** A bad invocation answers one JSON record per line on standard output, nothing on standard
  error, no terminal escapes; the record names a rule that is in the tool's own table, the exit status is the one the
  table promises, and it carries `code`, `rule`, `message`, `hint`, `repair` and `detail`.
* **Every refusal says what to do.** The `hint` is never empty: a source scan fails on any refusal written with an
  empty hint, so a new refusal cannot be added without advice. (The scan found sixteen when it was first run, in `hash`,
  `write`, `replace`, `move`, `jsonq`, `run` and the shared `contract/`; they are fixed.)
* **Same bytes everywhere.** A refusal is byte-identical under a foreign locale, time zone, terminal width and colour
  settings. No tool reads a terminal or waits for one.
* **A host can gate on what a tool may do.** The MCP server's `tools/list` carries `readOnlyHint`, `destructiveHint`,
  `idempotentHint` and `openWorldHint` derived from `introspect` ([mcp.md](mcp.md) section 3).

## Checked elsewhere

| property | where |
|---|---|
| every answer validates against its published schema | `test_schema.py` (M1) |
| the same input gives the same bytes | `test_determinism.py` (M2) |
| every rule has a fixture that reaches it; a `retry` repair works when applied and takes no authority | `test_rules.py` (M3) |
| the operating system failing mid-operation is a tagged refusal, not a crash | `test_faults.py` (M4) |
| the authority row is the compiler's and within the ceiling | `test_authority.py`, `manifest.py --check` (M6) |
| writes are atomic, guarded and refuse what they should | `test_guarantees.py`, `test_mutation.py`, `test_confinement.py` |
| memory is bounded where the tool says so | `test_memory.py` (M9) |
| the MCP server serves exactly the CLI's bytes, and refuses what is not on its list | `test_mcp.py` |

## Not checked

* That a model **chooses** the right tool or flag from the description. The descriptions are the `summary` lines and
  the per-flag `help`; whether they are good enough is an empirical question.
* That the **repair** in a refusal is the right one for the agent's goal, only that applying it works and takes no new
  authority (M3). Many refusals carry `repair: null` and a hint instead.
* That the output is **small enough for a context window**. Caps are declared (`limits`) and truncation is flagged,
  but what a caller can afford is the caller's.
* Locale or terminal behaviour of tools beyond a bad-flag refusal (the byte-identity test uses that invocation because
  it needs no fixture).

## Adding a tool

A new tool is agent-first when `test_agent_first.py` passes for it with no exception: add it to `EVERY` in
`tests/conformance/harness.py`, give every flag and operand `help`, give every refusal a hint, and `introspect` will
carry the rest. A tool outside this repository (`cancho-fetch`) is held to the same list.
