#!/usr/bin/env python3
"""Mutation check of `move` (docs/next-tools.md §4): each mutant is tools/move/move.cho with
one deliberate defect, rebuilt, and run against `test_move` and `test_rules`. A mutant is
killed when a test fails. The file is restored after every mutant, whatever happens.

    python3 scripts/move_mutants.py [name-substring ...]

Run where the pinned compiler is (`cancho` on PATH, or CANCHO), on Linux with strace: the replacing
rename, the unmapped EEXIST and the unmapped 95 are killed by tests that inject the kernel's answer or
delay the rename under strace, and the unmapped 45 (macOS's ENOTSUP) only by `UnsupportedFilesystem`,
with CANCHO_RENAME_UNSUPPORTED_DIR naming a directory on ExFAT (a disk image on a Mac). Exit status 1 if one survives.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "move" / "move.cho"
COMPILER = os.environ.get("CANCHO", "cancho")

# (name, the text replaced, its replacement): each `old` occurs exactly once.
MUTANTS = [
    ("the look no longer refuses an existing destination (a dry run would plan it)", "            } else if dst_errno == 0 {\n                e = name_taken(", "            } else if dst_errno == 99 {\n                e = name_taken("),
    ("a stale hash is accepted", "if !bytes.equal(buffer.bytes(hb), cli.text(args, parsed, table, \"if-sha256\")) {\n                        var w = fail.open(heap, \"precondition.hash-mismatch\"", "if false && !bytes.equal(buffer.bytes(hb), cli.text(args, parsed, table, \"if-sha256\")) {\n                        var w = fail.open(heap, \"precondition.hash-mismatch\""),
    ("a retry is 'landed' whatever the destination holds", "landed = bytes.equal(buffer.bytes(tb), cli.text(args, parsed, table, \"if-sha256\"));", "landed = true;"),
    ("a retry that had landed is never recognised", "if guarded && dst_errno == 0 && dst_kind == dirs.kind_file() {", "if false && guarded && dst_errno == 0 && dst_kind == dirs.kind_file() {"),
    ("a dry run renames", "    if go && dry {\n        o = Outcome { changed: false, planned: true, found: o.found };\n    } else if go {", "    if go && dry && false {\n        o = Outcome { changed: false, planned: true, found: o.found };\n    } else if go {"),
    # Skipping a lock and carrying on (a lock on an unrelated name), not failing it: the race gates must see the difference.
    ("the destination is not locked", "                match atomic.acquire(heap, dir, newname) {", "                match atomic.acquire(heap, dir, \"lock-elsewhere\") {"),
    ("a '/' is allowed in a new name", "    if bytes.count_byte(name, '/') > 0 {\n        return true;\n    }", "    if false && bytes.count_byte(name, '/') > 0 {\n        return true;\n    }"),
    ("a lock sidecar's name is allowed", "    if len(name) >= 12 && bytes.equal(name[len(name) - 12..len(name)], \".lexsys-lock\") {", "    if false && len(name) >= 12 && bytes.equal(name[len(name) - 12..len(name)], \".lexsys-lock\") {"),
    ("an over-long name is allowed", "if len(name) == 0 || len(name) > 243 {", "if len(name) == 0 || len(name) > 255 {"),
    ("the source is not locked", "    match atomic.acquire(heap, dir, name) {\n        Opened::Failed(reason) => {\n            if atomic.would_block(reason) {\n                e = conflict_locked(heap, e, from_shown);", "    match atomic.acquire(heap, dir, \"lock-elsewhere-too\") {\n        Opened::Failed(reason) => {\n            if atomic.would_block(reason) {\n                e = conflict_locked(heap, e, from_shown);"),
    ("anything can be given a hash", "    } else if guarded && src_kind != dirs.kind_file() {", "    } else if guarded && src_kind == 99 {"),
    ("a rename to the same name is a conflict", "            if bytes.equal(name, newname) {\n                // Already where it is to be: nothing to do.\n            } else if dst_errno == 0 {", "            if dst_errno == 0 {"),
    ("--remove needs no hash", "        if !cli.has(parsed, table, \"if-sha256\") {\n            e = flag_problem(heap, e, \"args.required-flag\"", "        if false && !cli.has(parsed, table, \"if-sha256\") {\n            e = flag_problem(heap, e, \"args.required-flag\""),
    ("--remove accepts a NEWNAME", "        } else if cli.operand_count(parsed) > 1 {\n            e = flag_problem(heap, e, \"args.too-many-operands\", \"move --remove", "        } else if cli.operand_count(parsed) > 9 {\n            e = flag_problem(heap, e, \"args.too-many-operands\", \"move --remove"),
    ("the tombstone does not carry the hash", "    return buffer.append(heap, b, hex[0..8]);", "    return buffer.append(heap, b, \"00000000\");"),
    ("a dry run names its plan a move", "op = \"remove\";", "op = \"move\";"),
    ("a too-long tombstone name is allowed", "                            if bad_name(buffer.bytes(cb)) {", "                            if false && bad_name(buffer.bytes(cb)) {"),
    ("the parent directory is not synced", "            atomic.sync_parent(dir);\n            o = Outcome { changed: true, planned: false, found: o.found };", "            o = Outcome { changed: true, planned: false, found: o.found };"),
    # The kernel closes the race (`dir_rename_new`): each of these puts a hole back.
    ("the rename is the replacing one (dir_rename)", "match dir_rename_new(dir, name, newname) {", "match dir_rename(dir, name, newname) {"),
    ("the kernel's EEXIST is taken for a success", "            Done::Failed(reason) => {\n                failed = reason;\n                if failed == 0 {", "            Done::Failed(reason) => {\n                failed = reason;\n                if reason == 17 {\n                    failed = 0;\n                }\n                if failed == 0 {"),
    ("the kernel's EEXIST is not the conflict", "        if failed == 17 {", "        if failed == 99 {"),
    ("an unsupported filesystem's errno 95 is not mapped", "        } else if failed == 95 || failed == 45 {", "        } else if failed == 99 || failed == 45 {"),
    ("an unsupported filesystem's errno 45 is not mapped", "        } else if failed == 95 || failed == 45 {", "        } else if failed == 95 || failed == 99 {"),
    ("an unsupported filesystem is taken for a success", "            Done::Failed(reason) => {\n                failed = reason;\n                if failed == 0 {", "            Done::Failed(reason) => {\n                failed = reason;\n                if reason == 95 {\n                    failed = 0;\n                }\n                if failed == 0 {"),
]

original = SOURCE.read_text()


def restore(*_):
    SOURCE.write_text(original)


def build_and_test():
    b = subprocess.run([COMPILER, "build", "--bin", "move"], cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "does not build", b.stderr.strip()[:140]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_move", "test_rules"], cwd=ROOT / "tests" / "conformance",
                       capture_output=True, text=True, timeout=900)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main():
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    wanted = sys.argv[1:]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[0] for w in wanted)]
    verdict, _ = build_and_test()
    print("unmutated:", "pass" if verdict == "SURVIVED" else verdict, flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for name, old, new in chosen:
        if original.count(old) != 1:
            print("!! %s: the site occurs %d times" % (name, original.count(old)))
            return 1
        try:
            SOURCE.write_text(original.replace(old, new, 1))
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-9s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict != "killed":
            survivors.append(name)
    assert SOURCE.read_text() == original
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
