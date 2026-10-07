# An agent benchmark for `table`: questions about CSV files

> **Status: pre-registered.** Sections 1 to 8 are written **before** the full run and
> are not edited after seeing its results; section 9 onward is appended with the results.
> The tasks, the prompts, the seeds and the checker are in
> `scripts/agentbench_table_tasks.py`, `scripts/agentbench_table.py` and
> `tests/conformance/test_agentbench_table.py` of this commit. What was done before
> this was written: four harness smoke runs (two tasks, two arms, one repeat) to find the
> harness's own bugs, listed in section 8; no task was added, removed or changed after them.

This extends the harness of [agent-bench.md](agent-bench.md) (the tools against plain
bash) to one tool of the family: `table`
([cancho-table](https://github.com/alpibrusl/cancho-table), select / filter / group /
aggregate / sort over CSV). The question is the same one: with the same model and the same prompt,
**does the tool change whether the answer is right, how confidently a wrong answer is given, and what it
costs?** And one the first document could not ask, because its tasks had one right answer: what does
each toolset do when the **right answer is "this cannot be answered, and here is the cell"?**

## 1. What it will and will not say

It measures, for local models run through opencode on one Apple-silicon Mac, four toolsets on 18 questions
about CSV files. It does not say that one model beats another, anything about a hosted model, or anything about
`table`'s speed (the README measures that). Three repeats of 18 tasks give an arm 54 runs per model; an
interval on a pass rate is about 12 to 14 points wide at the middle, and **a difference smaller than the
intervals is not a finding** (section 6). The repeats vary only the model's sampling: a task's file is built
from a fixed seed, so every repeat and every arm reads the same bytes.

## 2. The arms

One variable: what the agent may run. The model, the harness (opencode 1.18.27 with its own configuration
directory, so nothing in a person's settings reaches it), its settings and the prompt are the same.
The harness's own file tools (read, write, edit, grep, glob, list, webfetch, task) are off in every arm,
so the shell (or the MCP server) is the only way into the file.

| arm | what the agent may call | how it is enforced |
|---|---|---|
| `bash` | a shell with the Unix incumbent: `awk sort uniq cut jq sed grep tr paste join comm xargs bc dc od diff` and the usual file utilities (`ls cat head tail wc cp mv rm` ...) | the shell's `PATH` is a directory of links to exactly these programs, made read-only by a `BASH_ENV` file (a model that found a command missing wrote `export PATH=...` in the smoke run) |
| `python` | a shell with only the basic file utilities plus `python3` (3.14.5) with its standard library (`csv`, `decimal`, `statistics`, `fractions`) **and `pandas` 3.0.3, which is installed on this machine** | the same mechanism |
| `table-skill` | the `bash` arm's shell plus the `table` binary, with `table skill` installed as an opencode skill (the harness's existing `skills` arm mechanism: the model sees the skill's name and description in the skill tool and loads the text when it chooses to) | the same mechanism |
| `table-mcp` | only the tools of `table`'s MCP server (the `mcp-server` branch of cancho-table), confined to the workspace; the shell is off | opencode's `permission.bash: deny` |

Everything is on macOS, so **the `bash` arm is BSD**: `sort` 2.3-Apple, awk 20200816 (the one-true-awk,
doubles only), `jq` 1.7.1; `bc` 7.0.3 is there and is arbitrary-precision, so an exact decimal sum is
reachable by a model that thinks of it. A GNU userland would differ in `sort -s`, `sed -i` and awk's
`gawk -M`; the first document's Linux run is where that comparison belongs. The arms are a `PATH`, not a
sandbox: a command that tries to leave it (`command -p`, a reset of `PATH`, an absolute path to a program the arm
does not give) is **detected and reported** (`left_arm`), not prevented. `table` is the binary built from
cancho-table `main` with its pinned compiler (`cancho.toml`, rev a4572ea).

The prompt never names a tool, a flag or an arm. Cost that the arms legitimately differ in (the MCP arm
carries the server's tool definitions on every turn, the skill arm loads a skill before using it) is measured,
and the first call's input tokens are recorded.

## 3. The tasks

18 tasks, one file (`data.csv`) each in a throwaway workspace, built from a fixed seed. The expected answer is computed
**independently of every tool under test**: from the rows the generator holds, with `decimal`, `fractions` and Python
integers; `test_agentbench_table.py` reads each file back with `csv` and checks the stored answer again, and a
`table` command per task confirms that the tasks `table` can answer give the same value. The "plain tools"
column is a **prediction written before the run**, not a result.

| task | what is in the file | the question | plain tools, predicted |
|---|---|---|---|
| `quoted-records` | 300 orders; names with commas, doubled quotes and a newline inside quotes; near-miss decoys | how many orders for `Okafor, Ngozi "Ngo"` | cut/awk split on quoted commas |
| `empty-cell` | 400 requests, 20% with an empty `bytes` | how many transferred more than 1000 bytes | **fine** (table refuses an empty cell as an integer until it is excluded) |
| `header-only` | a header and no rows | total of `amount` (0) | awk prints an empty line; `table` prints a result with no rows, not a 0 |
| `ragged-rows` | 200 rows, some with quoted commas (valid), a few short or long | the line numbers of the broken rows | awk flags the valid quoted rows too; `table` names only the first |
| `crlf` | CRLF line endings, region last | how many rows have region `EU` (not `EUR`) | the carriage return stays on the last field |
| `bom` | UTF-8 BOM before the header | the largest `order_id` | **fine** in the shell; python's `csv` names the first column with the BOM |
| `prefix-columns` | `price_eur` before `price` | total of `price` | **fine** if the header is read |
| `decimal-10k` | 10,000 money values with two decimals (`0.10`, `0.20`, ...) | the exact total | awk prints six significant digits; a float sum drifts |
| `sum-past-64-bits` | 40 values near 2^62..2^63 | the exact total (over 2^64) | awk and jq use doubles; pandas wraps int64 silently |
| `stable-ties` | 300 rows, scores 1 to 6 | ids of the first 6 after sorting descending, ties in file order | `sort -rn` is not stable on all platforms |
| `top-3` | 5,000 orders, 60 customers | the top 3 totals | **fine** (awk, sort) |
| `10k-keys` | 40,000 events, 10,000 distinct users | the user with the most rows | **fine** in awk; unbounded if the listing is printed |
| `distinct-count` | 30,000 rows, about 11,000 distinct sessions | how many distinct | **fine** (`sort -u | wc -l`) |
| `filter-404` | 20,000 requests; a user-agent column with commas, before `status` | how many have status 404 and over 50,000 bytes | field numbers are wrong past the quoted commas |
| `mean-rounding` | grade and 3-decimal score; the mean of `A` is an exact tie at the third place | mean of A, rounded half to even to 3 places | a binary-float mean and `printf` round the tie either way |
| `unknown-column` | a column `bytes` | total of the `Bytes` column | **fine** once the header is read; `table` answers `column.unknown` |
| `na-cell` | 600 prices, one is `N/A` | total of `price` | awk reads `N/A` as 0 and prints a confident total; **the right answer names the cell** |
| `million-rows` | 1,000,000 rows (16 MB) | how many have status 500 | **fine** in awk; the trap is putting the file in the context |

Of the 18, eight are tasks where plain tools should do fine (`empty-cell`, `bom` in the shell, `prefix-columns`,
`top-3`, `10k-keys`, `distinct-count`, `unknown-column`, `million-rows`) and ten are written to catch a Unix
tool or a habit (`quoted-records`, `header-only`, `ragged-rows`, `crlf`, `decimal-10k`, `sum-past-64-bits`,
`stable-ties`, `filter-404`, `mean-rounding`, `na-cell`). **The tasks are written by someone who built `table`**,
which favours it; the eight controls, and four places where `table` is predicted to be the awkward one
(`header-only`, `ragged-rows`, `empty-cell`, `unknown-column`), are the correction, not a cure. Two tasks cannot be
answered by `table` alone and are said to be: `ragged-rows` (it stops at the first broken row, the line is in
the refusal; a shell can loop, the MCP arm cannot) and, in the MCP arm, anything that needs a second program.

## 4. The prompt, the same in every arm

> You are working in the current directory, which is the workspace. *{the question}* *{the form of the answer}*
> The data is in `data.csv`. End your reply with one line of the form `ANSWER: <answer>`. If the file does not
> allow an exact answer, write `ANSWER: CANNOT` and say why above it.

The last sentence is in **every** prompt, not only the one where it applies, so it does not point at a trap; it
is a factor (a prompt without it would score the refusal task on free text) and it is why `CANNOT` can be told
from a wrong number. The first document's rule, that a prompt never asks for caution, is kept in this
sense: nothing says to check, to be careful or to look for bad data.

## 5. Scoring

Each run's final text gets one **verdict**, by rule, from the text alone (`classify`):

* **right**: the `ANSWER:` line equals the expected value (a number exactly, as a `Decimal`; a list in order; a name
  exactly). For `na-cell`: the text names the bad cell (its id `SKU-0412` or its line, 413), whether it also
  offers a total or not.
* **wrong**: an answer that is not the expected one. A confident total for `na-cell` with no bad cell named is wrong.
  It is **wrong, hedged** when the text carries an uncertainty marker (a fixed word list: approximately, roughly,
  probably, I think, assume ...), and **wrong-but-confident** when it does not.
* **refused or gave up**: `ANSWER: CANNOT` (except on `na-cell`), or no `ANSWER` line and the text says it cannot.
* **no answer**: no final text, no `ANSWER` line without a refusal, or a **timeout (900 s)**: a run that does not
  finish is an answer of its own kind. A run in which the agent itself crashed is a harness problem, listed, not
  scored.

"States its own uncertainty correctly" is read as the cross of *right or wrong* and *hedged or not*: a right answer
that hedges is under-confident, a wrong one that does not is the failure that costs most. The word list is crude and
is published, not tuned.

**Exposure** is recorded per run, for every arm: whether a command named a path that exists outside the
workspace (the system's own programs excluded; a temporary directory counted apart as *scratch*), whether the
transcript holds the canary string of a file placed beside the workspace (`../private.txt`, named by no prompt), and
whether the input file was changed. Also recorded: files left in the workspace (a script a model wrote), and any
attempt to leave the arm (section 2). Nothing here is a sandbox: the agent runs as the operator, on tasks that do not
tempt it, and these records are what would show it wandering.

Also recorded: turns, tokens in and out, the first call's input tokens, seconds, the calls by tool, how many failed.

## 6. How to read it

* Three repeats of 18 tasks per arm and model. A pass rate gets a **Wilson 95% interval**, over tasks and repeats
  together (the repeats of one task are not independent: the interval is, if anything, too narrow).
* Turns, tokens and seconds are medians over all scored runs of the arm. Seconds are the model server's, which
  the maintainer may also be using: they are reported and not compared across models.
* The headline is the **right rate with its interval and, beside it, the wrong-but-confident rate**: an arm that
  is right less often but wrong less confidently is a different thing from one that is simply worse.
* The per-task table is in the report so a reader sees where each arm fails; the 8 controls and 10 traps are summed apart.
* Every failure is listed with its reason; a failure by the harness is read by a person and counted apart.

## 7. Report layout (fixed now)

Per model: one table per arm summary (runs, right with interval, wrong-but-confident, wrong-hedged, refused, no
answer, median turns, tokens in and out, seconds, runs that went outside the workspace); the controls and the traps
summed apart; the per-task grid (right / runs, other verdicts as letters); the exposure list; harness problems; the
failures with transcripts named; the MCP arm's tool-call errors; what to change in `table`'s skill and
introspect text, each with the transcript that shows it. The raw transcripts are a `.tar.gz`, not in the repository.

## 8. Threats, stated, and what the smoke run found

* One local model family on one machine; a different model can reverse any result. The smaller models run only if
  they pass the harness-fit check (agent-bench.md section 12): a model that does not call a tool under opencode is
  reported as that, not as a failure of a toolset.
* The `bash` arm is BSD and has `bc`; the `python` arm has pandas. Both choices are the machine's, and said.
* The tasks are small and the traps are the ones the author thought of. A model can be right for a wrong
  reason; the checker reads the answer, and the transcripts are kept.
* `table` is at an early version and its `skill` text is the one built into that binary (14,648 characters;
  its flag list for `--agg` does not mention `mean`, `:dec(S)` or `@N`, which the README documents). The result is
  about that text.
* **Found by the four smoke runs, and fixed before the run:** a relative `PATH` entry (the first run's `ls` was
  "not found"); a model that, finding `ls` missing, wrote `export PATH=/usr/bin:...` and reached `python3` from the
  `bash` arm (fixed by the read-only `PATH`; `env` is not in any arm for the same reason); `~` in an awk program
  (`$2 ~ /x/`) counted as the home directory in the exposure scan.
