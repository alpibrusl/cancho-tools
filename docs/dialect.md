# dialect: read an export of unknown format, and clean it

`dialect` is the twelfth tool (the ceiling D1 allows). It answers the first question an agent has about
a delimited text file it was handed, and rewrites the file into the one dialect the other tools read.

## Why

A run of Haiku with the cancho tools and no help failed to serve a model from a `;`-separated export
with CRLF line ends, a decimal-comma column, `NA` cells and a `won`/`lost` label (0 of 3). The tools
were not at fault in the way the agents thought: `ml` reads comma-separated numbers only, `table`
reads `;` but prints it back unchanged, and nothing converted a decimal comma. The conversion was one
`tr | awk` line, and the agents wrote Python wrappers instead. With that line given in the system prompt
the same model passed 10 of 10 (average 11.6 turns, $0.027), against 15 turns and $0.061 for Python with
pandas. The line is the part this tool takes over, so that it is found by asking the tool, not by
knowing it.

## What it does

    dialect [--root DIR] [--sample-rows N] [--max-bytes N] [--max-columns N] [--format json|text] sniff FILE
    dialect [--root DIR] [--delimiter D] [--decimal auto|.|,] [--na LIST] [--map COLUMN=a:1/b:0]
            [--max-bytes N] [--max-columns N] --format text normalize FILE > clean.csv

`sniff` reports the delimiter and how sure that is, the delimiters that also fit, the line ending, a
byte order mark, and per column a kind (integer, number, date, text, empty), a decimal mark, the missing
cells and the marker, and up to four values for a column that is not numeric. It then names the columns
that hold exactly two values (label candidates, with a `--map` template), the text columns that hold a
missing marker (for `--na`), and the `normalize` line that follows. It never edits anything.

`normalize` writes comma separated CSV with LF line ends, dot decimals, empty cells for missing values,
and a recoded label. With `--format text` the standard output is the cleaned file; with `--format json`
it is a report of what changed. The tool holds no write authority: the compiler's report for it is
`args, dir_read, err_write, file_read, fs_read, heap, io_write`.

## Decisions

* **Per-column decimal marks.** A file may mix them across columns (the motivating export does), so a
  column is decimal-comma when every number in it has a comma. A column with both marks is
  `dialect.mixed-decimal`; the tool does not guess a thousands mark (`1,234` is 1.234).
* **Missing markers are blanked where it is safe.** In a numeric column, `NA`, `N/A`, `NaN`, `null`,
  `None`, `?`, `-` become empty. In a text column only what `--na` names does: `NA` may be a country.
* **A label is recoded only on request**, and a value the map does not name is refused, never passed on.
* **A record is a line.** A quoted field with a line break is not supported; a quote that does not close
  is `dialect.bad-quote`.
* **Delimiter choice.** Among `,` `;` tab `|` the one that splits at least 90 percent of the first
  `--sample-rows` lines into the header's number of fields, at least two, with the most fields. Confidence
  is `high` when no other fits, `medium` when another fits with fewer fields, `low` when another fits
  with as many, `none` when none fits (then `,` and one field are reported, not a refusal).
* **Memory** is the file (up to `--max-bytes`, default 64 MiB), a fixed profile per column, and the
  cleaned copy. It is therefore not claimed as `bounded_memory` the way `tally` is.

## Measured

* 21 conformance tests (`tests/conformance/test_dialect.py`), among them 150 random tables written in
  random dialects (delimiter, line ends, quoting, decimal marks, markers, a byte order mark) that must
  come back as drawn, and 100 that `sniff` must classify. Two planted bugs (a decimal comma left
  unconverted; the steadiness threshold lowered) are both caught.
* On the motivating export (3000 rows) `normalize` is byte-identical to the `tr | awk` line it replaces,
  and `ml fit` on it gives the model that scores AUC 0.822 on 400 held-out rows, as scikit-learn does.

## Not done, and not claimed

* **The reader-level flags.** `--delimiter`, `--decimal` and numeric `--na-values` in `ml`, `stats` and
  `table` themselves are not built. They change the pinned `reader` package that `ml` and `stats`
  compile, so they need that package republished and the pins moved. Until then, `dialect normalize`
  is a step before them.
* **No `--output FILE`.** The cleaned file is the standard output of `--format text`; the shell writes
  it. A writing mode would add `file_write` to the authority and is left out on purpose.
* **Agents have not been measured with `dialect` yet.** The 10 of 10 above is with the awk line in the
  prompt. Whether an agent that is only told to run `dialect sniff` first does as well is the next run.
* The conformance corpus of the repo (`test_rules.py`) has one fixture that is Linux-only
  (`/proc/self/mem`) and fails for `dialect` on macOS as it does for the other tools.
