# move --across

## Why

`move PATH NEWNAME` renames in place and refuses a NEWNAME with a `/` (`path.bad-name`): `dir_rename_new` works on one
directory handle. In an agent run where the cancho tools were the only file tools, "move everything in `inbox/` into
`archive/`" had no single call. In the two valid runs where only these tools were available, the agents fetched the
existing destination's hash and overwrote it with `write --if-sha256`, on purpose (one said so in its answer, one did
not); in the two runs that also had Bash, they tried `move`, found it could not cross, and fell back to `mv`, which
overwrote silently. Four runs is a pilot, not a rate. The point is that the guard made the overwrite explicit and did
not stop it, because there was no safe way to do what was asked.

## What it does

`move --across [--max-bytes N] [--if-sha256 HEX] [--dry-run] PATH DESTPATH` puts a regular file at DESTPATH, in any
directory beneath `--root`, in two steps:

1. **Copy**, with the no-replace create `write --create` uses (`atomic.create`: temporary file, fsync, then
   `dir_rename_new` in the destination directory). The kernel refuses a name that exists, even one created a moment ago
   by a process that takes no lock.
2. **Tombstone the source**: rename it to `.NAME.removed-HASH8` in its own directory, as `move --remove` does. Nothing is
   deleted; moving the tombstone back undoes the whole thing but for the destination copy.

The answer names what was left: `data.tombstone`.

## Decisions

* **Not atomic, and it says so.** If step 2 fails after step 1, both files exist and nothing is lost. The error says so
  and gives the command that finishes it (`move --remove --if-sha256 HASH PATH`). A true atomic cross-directory rename
  needs a two-handle builtin in the compiler (`dir_rename_new` takes one `Dir`); this does not pretend to be that.
* **Any existing destination is `conflict.exists`, whatever it holds.** There is no "the copy already landed, finish
  the move" shortcut: a file moved onto itself would then be tombstoned and lost.
* **A regular file only.** A directory has no content to copy, a link is not followed, and opening a FIFO or a device
  would wait for a peer. File modes, owners and times are not preserved: the copy is a new file.
* **Bounded.** The file is read whole, up to `--max-bytes` (default 16 MiB, ceiling 1 GiB); more is
  `limit.input-too-large` before anything is written.
* **Same guards as `move`.** Both names are locked (each in its own directory), `--if-sha256` says the file is what the
  caller read, `--dry-run` writes nothing and reports the conflict it would hit, and no link in either path is followed.
* `--across` and `--remove` exclude each other (`args.conflict`).
* `path.bad-name` now says how to cross: `move --across PATH DIR/NAME`.

## Measured

* `tests/conformance/test_move_across.py` (14 tests): 42 contents-by-names combinations leave the destination with the
  bytes, the source as a tombstone with the bytes, and nothing else changed; 25 random existing destinations (same bytes
  or different) are refused with both files untouched; 6 rounds of 8 movers racing for one destination give exactly one
  winner and lose nothing; links, directories, `..`, absolute paths, a wrong hash, a dry run and an unwritable
  destination leave the tree as it was. Four planted bugs (no conflict check in a dry run, the copy made in the wrong
  directory, no tombstone, the hash guard ignored) are each caught.

## Not done, and not claimed

* **A crash between the two steps** is reasoned about, not tested: the copy exists and the source is whole, so nothing is
  lost, but no test kills the process there.
* **The failure of step 2 after a good step 1** (`copied_not_removed`) has no test either: the conditions that make a
  rename fail without making the earlier copy fail are not available to a test on this machine.
* A directory cannot be moved across; use `archive` or move its files.
