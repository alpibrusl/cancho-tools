#!/usr/bin/env python3
"""Mutation check of `archive` (docs/archive.md section 7, gate G10): each mutant is one of the
tool's sources with one deliberate defect, rebuilt, and run against `test_archive`. A mutant is
killed when a test fails (or the tests hang or the build is refused). The file is restored after
every mutant, whatever happens.

    python3 scripts/archive_mutants.py [name-substring ...]

Run where the pinned compiler is (`cancho` on PATH, or CANCHO), on Linux with GNU tar, gzip and
strace. Exit status 1 if one survives.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIR = ROOT / "tools" / "archive"
COMPILER = os.environ.get("CANCHO", "cancho")

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    ("an unsafe name is extracted", "archive.cho", "        if tar.unsafe_at(entries, k) {\n            e = refuse(heap, e, \"archive.unsafe-name\"", "        if false && tar.unsafe_at(entries, k) {\n            e = refuse(heap, e, \"archive.unsafe-name\""),
    ("a link is extracted as if it were a file", "archive.cho", "        } else if kind != tar.kind_file() && kind != tar.kind_directory() {", "        } else if false {"),
    ("two entries of one name are allowed", "archive.cho", "                if twice {", "                if false && twice {"),
    ("an existing file is not noticed in the plan", "archive.cho", "                    DirStat::Ok(kind, size, mtime) => {\n                        e = fail.simple(heap, e, \"conflict.exists\", \"the file exists already; archive never replaces a file\", \"move it aside with `move`, or extract elsewhere", "                    DirStat::Ok(kind, size, mtime) => {\n                        e = fail.simple(heap, e, \"conflict.nothing\", \"the file exists already; archive never replaces a file\", \"move it aside with `move`, or extract elsewhere"),
    ("the plan does not stop the writing", "archive.cho", "    while clean && k < tar.count(entries) && !count.broken {", "    while k < tar.count(entries) && !count.broken {"),
    ("a dry run writes", "archive.cho", "                if dry {\n                    let digest = atomic.sha256_hex(heap, content);", "                if false {\n                    let digest = atomic.sha256_hex(heap, content);"),
    ("the rename may replace", "archive.cho", "            match dir_rename_new(dir, temp, name) {", "            match dir_rename(dir, temp, name) {"),
    ("a stale temporary is taken for the caller's own", "archive.cho", "    if step == 1 && errno == 17 {", "    if step == 1 && errno == 99 {"),
    ("--into is ignored", "archive.cho", "    return joined(heap, into, name);", "    return joined(heap, \".\", name);"),
    ("an existing archive is overwritten by create", "archive.cho", "                            DirStat::Ok(kind, size, mtime) => {\n                                e = fail.simple(heap, e, \"conflict.exists\", \"the archive exists already", "                            DirStat::Ok(kind, size, mtime) => {\n                                e = fail.simple(heap, e, \"conflict.nothing\", \"the archive exists already"),
    ("a link in a tree is archived as nothing", "archive.cho", "    if kind != dirs.kind_directory() {\n        e = refuse(heap, e, \"archive.entry-kind\"", "    if kind != dirs.kind_directory() && false {\n        e = refuse(heap, e, \"archive.entry-kind\""),
    ("the executable bit is lost", "archive.cho", "                                if bits & 0x40 != 0 {", "                                if bits & 0x40 != 99 {"),
    ("a file past --max-bytes is read whole", "archive.cho", "        if size + state.held + 1024 > most_bytes {", "        if size + state.held + 1024 > 99 * most_bytes {"),
    ("the gzip CRC is not compared", "flate.cho", "    if crc != le32(src, next) {", "    if false && crc != le32(src, next) {"),
    ("the gzip length is not compared", "flate.cho", "    if size & 0xffffffff != le32(src, next + 4) {", "    if false && size & 0xffffffff != le32(src, next + 4) {"),
    ("a second gzip member is accepted", "flate.cho", "    if next + 8 < n {\n        return Gunzipped { data: out, status: 15", "    if false && next + 8 < n {\n        return Gunzipped { data: out, status: 15"),
    ("a match may pass the output cap", "flate.cho", "                } else if made + length > limit {", "                } else if false {"),
    ("a stored block may pass the output cap", "flate.cho", "                                } else if made + size > limit {", "                                } else if false {"),
    ("a distance may reach before the start", "flate.cho", "                } else if distance > made {", "                } else if false {"),
    ("the header counts are not bounded", "flate.cho", "    if nlen > 286 || ndist > 30 {", "    if false {"),
    ("an over-subscribed or incomplete code is accepted", "flate.cho", "    return answer > 0 && n - table[0] - table[1] == 0;", "    return true;"),
    ("a tar header's checksum is not checked", "tar.cho", "        } else if !checksum_ok(data, at) {", "        } else if false {"),
    ("a size past the data is not noticed", "tar.cho", "                if size > len(data) || body_at + (advance - 512) > len(data) {", "                if false {"),
    ("`..` is a plain component", "tar.cho", "            if i - start == 2 && int_of(name[start]) == '.' && int_of(name[start + 1]) == '.' {", "            if i - start == 3 && int_of(name[start]) == '.' && int_of(name[start + 1]) == '.' {"),
    ("an absolute name is a plain name", "tar.cho", "    if len(name) > 0 && int_of(name[0]) == '/' {\n        return true;\n    }", "    if false {\n        return true;\n    }"),
    ("the archive's time is the files'", "tar.cho", "    h = put_octal(heap, h, 0, 11);\n    h = buffer.push(heap, h, byte_of(0));\n    // The checksum, as eight spaces", "    h = put_octal(heap, h, 1, 11);\n    h = buffer.push(heap, h, byte_of(0));\n    // The checksum, as eight spaces"),
    ("the archive has no end marker", "tar.cho", "    return pad_to(heap, o, used + 1024, used);", "    return pad_to(heap, o, used + 512, used);"),
    ("a pax size is ignored", "tar.cho", "                    if pending_size >= 0 && has_body(flag) {", "                    if false && pending_size >= 0 && has_body(flag) {"),
    ("the entry cap is not applied", "tar.cho", "                    } else if seen >= most {", "                    } else if false {"),
]

originals = {}


def restore(*_):
    for name, text in originals.items():
        (DIR / name).write_text(text)


def build_and_test():
    b = subprocess.run([COMPILER, "build", "--bin", "archive"], cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "killed", "does not build: " + b.stderr.strip()[:100]
    # The cancho unit tests first: they are quick and kill what the CLI cannot reach (a limit passed directly).
    for unit, source in (("flate", "tests/flate_test.cho"), ("tar", "tests/tar_test.cho")):
        files = sorted(str(f) for f in (ROOT / "contract").glob("*.cho")) + [str(DIR / (unit + ".cho")), str(ROOT / source)]
        u = subprocess.run([COMPILER, "test", *files, "--std"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        if u.returncode:
            names = [l.split(" ")[1] for l in u.stdout.splitlines() if l.startswith("test ") and l.endswith("FAILED")]
            return "killed", "unit: " + ", ".join(names[:3] or ["did not build"])
    try:
        p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_archive"], cwd=ROOT / "tests" / "conformance",
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "killed", "the tests hung"
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main():
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    for name in {m[1] for m in MUTANTS}:
        originals[name] = (DIR / name).read_text()
    wanted = sys.argv[1:]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[0] for w in wanted)]
    verdict, _ = build_and_test()
    print("unmutated:", "pass" if verdict == "SURVIVED" else verdict, flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for name, file, old, new in chosen:
        if originals[file].count(old) != 1:
            print("!! %s: the site occurs %d times in %s" % (name, originals[file].count(old), file))
            return 1
        try:
            (DIR / file).write_text(originals[file].replace(old, new, 1))
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-9s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict != "killed":
            survivors.append(name)
    restore()
    # The binary left in build/ must be the real one, not the last mutant's.
    subprocess.run([COMPILER, "build", "--bin", "archive"], cwd=ROOT, capture_output=True, text=True)
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
