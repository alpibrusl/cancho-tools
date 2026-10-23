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
    ("a dry run writes", "archive.cho", "                    if dry {\n                        let digest = atomic.sha256_hex(heap, content);", "                    if false {\n                        let digest = atomic.sha256_hex(heap, content);"),
    ("the rename may replace", "archive.cho", "            match dir_rename_new(dir, temp, name) {", "            match dir_rename(dir, temp, name) {"),
    ("a stale temporary is taken for the caller's own", "archive.cho", "    if step == 1 && errno == 17 {", "    if step == 1 && errno == 99 {"),
    ("--into is ignored", "archive.cho", "    return joined(heap, into, name);", "    return joined(heap, \".\", name);"),
    ("an existing archive is overwritten by create", "archive.cho", "                            DirStat::Ok(kind, size, mtime) => {\n                                e = fail.simple(heap, e, \"conflict.exists\", \"the archive exists already", "                            DirStat::Ok(kind, size, mtime) => {\n                                e = fail.simple(heap, e, \"conflict.nothing\", \"the archive exists already"),
    ("a link in a tree is archived as nothing", "archive.cho", "    if kind != dirs.kind_directory() {\n        e = refuse(heap, e, \"archive.entry-kind\"", "    if kind != dirs.kind_directory() && false {\n        e = refuse(heap, e, \"archive.entry-kind\""),
    ("the executable bit is lost", "archive.cho", "                                if bits & 0x40 != 0 {", "                                if bits & 0x40 != 99 {"),
    ("extract drops the execute bit (§13)", "archive.cho", "    if entry_mode & 64 != 0 {", "    if false {"),
    ("extract makes every file executable", "archive.cho", "    if entry_mode & 64 != 0 {", "    if entry_mode & 64 != 99 {"),
    ("a file is made with the creation mode, not the entry's", "archive.cho", "    match dir_open_new_mode(dir, temp, mode) {", "    match dir_open_new_mode(dir, temp, 420) {"),
    ("a setuid, setgid or sticky entry is extracted", "archive.cho", "            if tar.mode(entries, k) & special_bits() != 0 {", "            if false && tar.mode(entries, k) & special_bits() != 0 {"),
    ("the sticky and setgid bits are not special", "archive.cho", "fn special_bits() -> [] int {\n    return 3584;", "fn special_bits() -> [] int {\n    return 2048;"),
    ("--into is made when it is missing", "archive.cho", "    if below > 0 {\n        match place.open_directory(fs, root, into, into) {", "    if below > 1000000 {\n        match place.open_directory(fs, root, into, into) {"),
    ("a made directory has the wrong mode", "archive.cho", "dir_mkdir(d, place.leaf(rel), mode_executable())", "dir_mkdir(d, place.leaf(rel), 448)"),
    ("a file where a directory is needed is not noticed", "archive.cho", "                    if reason == 20 {\n                        let (after_seen, already) = was_seen(seen, wanted[0..i]);", "                    if reason == 99 {\n                        let (after_seen, already) = was_seen(seen, wanted[0..i]);"),
    ("a name that is a file and a directory is allowed", "archive.cho", "                    if is_file {\n                        let (after_clashes, told)", "                    if false && is_file {\n                        let (after_clashes, told)"),
    ("a dry run makes directories", "archive.cho", "                if dry {\n                    match place.open_directory(fs, root, wanted[0..at], wanted[0..at]) {", "                if false {\n                    match place.open_directory(fs, root, wanted[0..at], wanted[0..at]) {"),
    ("a selector selects names it is only a prefix of", "select.cho", "    return len(name) == len(sel) || int_of(name[len(sel)]) == '/';", "    return true;"),
    ("`.` selects nothing", "select.cho", "    if bytes.equal(sel, \".\") {\n        return true;", "    if bytes.equal(sel, \".\") {\n        return false;"),
    ("extract writes entries that were not asked for", "archive.cho", "        if select.matches(sel, name) {\n            let target = destination(heap, into, name);", "        if true {\n            let target = destination(heap, into, name);"),
    ("extract checks entries that were not asked for", "archive.cho", "        } else if !select.matches(sel, name) {", "        } else if false {"),
    ("an unsafe name is excused when it was not asked for", "archive.cho", "        if tar.unsafe_at(entries, k) {\n            e = refuse(heap, e, \"archive.unsafe-name\", \"an entry's name", "        if tar.unsafe_at(entries, k) && select.matches(sel, name) {\n            e = refuse(heap, e, \"archive.unsafe-name\", \"an entry's name"),
    ("a selector that selects nothing is not reported", "archive.cho", "        if select.is_unmatched(sel, k) {", "        if false && select.is_unmatched(sel, k) {"),
    ("list prints entries that were not asked for", "archive.cho", "        if select.note(sel, tar.name(entries, k)) {\n            var mode", "        if select.note(sel, tar.name(entries, k)) || true {\n            var mode"),
    ("a selector keeps its trailing slash", "archive.cho", "            let clean = tar.normalised(heap, given);", "            let clean = buffer.append(heap, buffer.empty(heap, len(given) + 1), given);"),
    ("an unsafe selector is accepted", "archive.cho", "        } else if tar.unsafe_name(given) {", "        } else if false {"),
    ("a zip member's CRC-32 is not compared", "zip.cho", "        if size != want || crc != vec.get(members.crc, k) {", "        if size != want {"),
    ("a zip member's length is not compared", "zip.cho", "        if size != want || crc != vec.get(members.crc, k) {", "        if crc != vec.get(members.crc, k) {"),
    ("an encrypted zip member is read", "zip.cho", "flags & 0x41 != 0", "flags & 0x40 != 0"),
    ("every zip method is read", "zip.cho", "|| how != 0 && how != 8 {", "|| false {"),
    ("a zip64 end record is read", "zip.cho", "|| total == 0xffff || cd_size == 0xffffffff || cd_at == 0xffffffff {", "|| false {"),
    ("a zip on several disks is read", "zip.cho", "    if disk != 0 || cd_disk != 0 || here != total {", "    if false {"),
    ("a zip's local header is not checked", "zip.cho", "    if !have(data, local, 30) || le32(data, local) != 0x04034b50 {", "    if !have(data, local, 30) {"),
    ("a zip's local extra field is ignored", "zip.cho", "le16(data, local + 26) + le16(data, local + 28)", "le16(data, local + 26)"),
    ("the zip's Unix mode is ignored", "zip.cho", "                if unix {\n                    mode = attributes >> 16 & 0xfff;", "                if false {\n                    mode = attributes >> 16 & 0xfff;"),
    ("a zip directory is not known by its trailing slash", "zip.cho", "int_of(name[name_len - 1]) == '/' ||", "false ||"),
    ("the zip's end record is only looked for near the end", "zip.cho", "    let floor = len(data) - 22 - 65535;", "    let floor = len(data) - 22 - 100;"),
    ("a zip member is deflated even when that is bigger", "zip.cho", "                smaller = buffer.size(pr) < size;", "                smaller = true;"),
    ("a zip member is never deflated", "zip.cho", "                smaller = buffer.size(pr) < size;", "                smaller = false;"),
    ("a zip's time is not fixed", "zip.cho", "        out = put16(heap, out, 0x21);", "        out = put16(heap, out, 0x22);"),
    ("a zip's entry count is not limited", "zip.cho", "    if n > 65534 {", "    if n > 65534000 {"),
    ("a bzip2 block's CRC-32 is not compared", "bzip2.cho", "                                    if crc != block_crc {", "                                    if false {"),
    ("a bzip2 stream's combined CRC-32 is not compared", "bzip2.cho", "                            } else if stored != combined {", "                            } else if false {"),
    ("bytes after a bzip2 stream are accepted", "bzip2.cho", "                            } else if bits.at < len(src) {", "                            } else if false {"),
    ("a randomised bzip2 block is read", "bzip2.cho", "                            } else if randomised != 0 {", "                            } else if false {"),
    ("a bzip2 block without a byte in use is read", "bzip2.cho", "                                } else if n_in_use == 0 {", "                                } else if false {"),
    ("the bzip2 table count is not bounded", "bzip2.cho", "                                } else if n_groups < 2 || n_groups > 6 || n_selectors < 1 {", "                                } else if false {"),
    ("a bzip2 code length is not bounded", "bzip2.cho", "                                            } else if curr < 1 || curr > 20 {", "                                            } else if false {"),
    ("bzip2's RUNB counts as RUNA", "bzip2.cho", "run_total = run_total + run_weight * (symbol + 1);", "run_total = run_total + run_weight;"),
    ("a bzip2 run may pass the block", "bzip2.cho", "                                                if n_block + run_total > block_max {", "                                                if false {"),
    ("a bzip2 block may pass its size", "bzip2.cho", "                                                } else if n_block >= block_max {", "                                                } else if false {"),
    ("a bzip2 origin pointer past the block is used", "bzip2.cho", "                            if status == 0 && (orig_ptr >= n_block || n_block == 0) {", "                            if status == 0 && n_block == 0 {"),
    ("a run of four is not a run to bzip2's last step", "bzip2.cho", "                                    if same == 4 {", "                                    if same == 5 {"),
    ("a bzip2 repeat may pass the output cap", "bzip2.cho", "                                        if made + more <= limit {", "                                        if true {"),
    ("a bzip2 byte may pass the output cap", "bzip2.cho", "                                        if made + 1 <= limit {", "                                        if true {"),
    ("the bzip2 combined CRC does not fold blocks in", "bzip2.cho", "combined = (combined << 1 | combined >> 31) & 0xffffffff ^ crc;", "combined = crc;"),
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
