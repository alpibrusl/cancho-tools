# seek --recursive

## Why

`seek PATTERN FILE...` took files only. A directory was `io.is-a-directory`. In an agent run where the tools were the only
file tools, the two valid runs on a 6,000-file tree made about 40 calls each (one in the run read: 26 `seek` and 7 `list`),
neither found the answer, and each cost about $0.37, where an agent with `grep -r` answered in a few calls for about
$0.016. Two runs is a pilot, not a rate. The tool had no way to say "everything under here".

## What it does

`seek --recursive PATTERN DIR` (or `-r`) searches every regular file under each directory operand, depth first, names in
bytewise order (`std.dirs.list`), so the order is the same on every machine. A file operand is searched as before.

* **Nothing is followed.** Each directory is entered with `dir_enter` and each file opened with `dir_open_read`, which
  pass `O_NOFOLLOW`. A link, a device, a socket and a directory deeper than 64 levels are skipped, and counted in the
  end record's `skipped`. What a link points at is never read.
* **Output is bounded.** In this mode a `file` record is written only for a file with a match: a tree of thousands of
  files with one match gives a match record, one file record and the end record. `--max-count` and `--skip` work across
  files as before (`next: {"skip": N}`).
* **The walk is bounded.** `--max-files` (default 100000, ceiling 10000000) ends it with `limit.too-many-files` (exit 8)
  and the count of files searched; so does a single directory with more than 100000 entries.
* **Without the flag nothing changes**: byte for byte, and a directory is still `io.is-a-directory`. `skipped` is in the
  end record only with `--recursive`.

## Measured

* `tests/conformance/test_seek_recursive.py` (10 tests): 60 random trees against an independent Python walk (same paths,
  lines, offsets, text); links to files, directories and nowhere are skipped and the secret behind them never appears;
  the file cap, the cursor across files, the depth cap, one record for a 200-file tree with one match. Three planted bugs
  (the depth cap raised, a file record always written, a link searched) are each caught.
* On a 6,200-file tree (21 MB), `seek -r` answers in 0.5 s on the machine that wrote this.

## Not done

* No `--include` or glob: a pattern is a literal and a file filter is a separate question.
* The `skipped` count does not say which entries were skipped. `list` does.
