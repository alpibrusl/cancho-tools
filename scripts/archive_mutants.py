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
    # Not mutated, on purpose: `n_in_use == 0` is also caught by `n_block == 0` a few steps later (same answer), and the first
    # `run_total > block_max` only keeps the run's weight from overflowing: the check at the run's end is what bounds a block.
    ("a bzip2 block's CRC-32 is not compared", "bzip2.cho", "                                    if crc != block_crc {", "                                    if false {"),
    ("a bzip2 stream's combined CRC-32 is not compared", "bzip2.cho", "                            } else if stored != combined {", "                            } else if false {"),
    ("bytes after a bzip2 stream are accepted", "bzip2.cho", "                            } else if bits.at < len(src) {", "                            } else if false {"),
    ("a randomised bzip2 block is read", "bzip2.cho", "                            } else if randomised != 0 {", "                            } else if false {"),
    ("the bzip2 table count is not bounded", "bzip2.cho", "                                } else if n_groups < 2 || n_groups > 6 || n_selectors < 1 {", "                                } else if false {"),
    ("a bzip2 code length is not bounded", "bzip2.cho", "                                            } else if curr < 1 || curr > 20 {", "                                            } else if false {"),
    ("bzip2's RUNB counts as RUNA", "bzip2.cho", "run_total = run_total + run_weight * (symbol + 1);", "run_total = run_total + run_weight;"),
    ("a bzip2 block may pass its size", "bzip2.cho", "                                                } else if n_block >= block_max {", "                                                } else if false {"),
    ("a bzip2 origin pointer past the block is used", "bzip2.cho", "                            if status == 0 && (orig_ptr >= n_block || n_block == 0) {", "                            if status == 0 && n_block == 0 {"),
    ("a run of four is not a run to bzip2's last step", "bzip2.cho", "                                    if same == 4 {", "                                    if same == 5 {"),
    ("a bzip2 repeat may pass the output cap", "bzip2.cho", "                                        if made + more <= limit {", "                                        if true {"),
    ("a bzip2 byte may pass the output cap", "bzip2.cho", "                                        if made + 1 <= limit {", "                                        if true {"),
    ("the bzip2 combined CRC does not fold blocks in", "bzip2.cho", "combined = (combined << 1 | combined >> 31) & 0xffffffff ^ crc;", "combined = crc;"),
    ('an xz CRC-32 check is not verified', "xz.cho", '        return flate.crc32(heap, data) == le32(src, at);', '        return flate.crc32(heap, data) == le32(src, at) || true;'),
    ('an xz CRC-64 check is not verified', "xz.cho", '            same = cl == le32(src, at) && ch == le32(src, at + 4);', '            same = true;'),
    ('an xz SHA-256 check is not verified', "xz.cho", '            if digest[i] != src[at + i] {', '            if false {'),
    ("an xz stream header's CRC-32 is not verified", "xz.cho", '    if flate.crc32(heap, src[6..8]) != le32(src, 8) {', '    if false {'),
    ("an xz block header's CRC-32 is not verified", "xz.cho", '            } else if flate.crc32(heap, src[pos..pos + header_size - 4]) != le32(src, pos + header_size - 4) {', '            } else if false {'),
    ('an xz filter other than LZMA2 is read', "xz.cho", '                    } else if id != 0x21 {', '                    } else if false {'),
    ('an xz block with several filters is read', "xz.cho", '                if flags & 3 != 0 {', '                if false {'),
    ("an xz block's padding may be anything", "xz.cho", '                                if int_of(src[data_end + k]) != 0 {', '                                if false {'),
    ("an xz block's sizes in its header are not held to", "xz.cho", '                            if status == 0 && (packed_claim >= 0 && packed_claim != packed || content_claim >= 0 && content_claim != size) {', '                            if false {'),
    ('an xz index with another count of blocks is read', "xz.cho", '        if used == 0 || count != blocks {', '        if used == 0 {'),
    ('an xz index with other sizes is read', "xz.cho", '                    if unpadded != vec.get(sr, 2 * k) || content != vec.get(sr, 2 * k + 1) {', '                    if false {'),
    ("an xz footer's backward size is not held to the index", "xz.cho", '                } else if (le32(src, footer + 4) + 1) * 4 != index_size || ', '                } else if '),
    ('bytes after an xz stream are accepted', "xz.cho", '                } else if footer + 12 != n {', '                } else if false {'),
    ("an xz chunk's end is not held to its sizes", "xz.cho", '    if status == 0 && (done != unpacked || rc[2] != end || rc[1] != 0) {', '    if false {'),
    ('an xz match may reach past the dictionary or its chunk', "xz.cho", '                if rep0 >= since || rep0 >= dict_size || done + run > unpacked {', '                if false {'),
    ("an xz chunk's properties are not bounded", "xz.cho", '                                        if pb > 4 || lc + lp > 4 {', '                                        if false {'),
    ('an xz chunk may pass the output cap', "xz.cho", '                                    } else if made + unpacked > room {', '                                    } else if false {'),
    ('zstd: p >= end', "zstd.cho", '    if p >= end {\n        return (2, p, 0);', '    if false {\n        return (2, p, 0);'),
    ('zstd: p + 2 > end', "zstd.cho", '            if p + 2 > end {\n                return (2, p, 0);', '            if false {\n                return (2, p, 0);'),
    ('zstd: p + 3 > end', "zstd.cho", '            if p + 3 > end {\n                return (2, p, 0);', '            if false {\n                return (2, p, 0);'),
    ('zstd: size > 131072', "zstd.cho", '        if size > 131072 {\n            return (1, p, 0);', '        if false {\n            return (1, p, 0);'),
    ('zstd: p + header + size > end', "zstd.cho", '            if p + header + size > end {\n                return (2, p, 0);', '            if false {\n                return (2, p, 0);'),
    ('zstd: p + header + 1 > end', "zstd.cho", '        if p + header + 1 > end {\n            return (2, p, 0);', '        if false {\n            return (2, p, 0);'),
    ('zstd: p + 3 > end (2)', "zstd.cho", '        if p + 3 > end {\n            return (2, p, 0);', '        if false {\n            return (2, p, 0);'),
    ('zstd: p + 4 > end', "zstd.cho", '        if p + 4 > end {\n            return (2, p, 0);', '        if false {\n            return (2, p, 0);'),
    ('zstd: p + 5 > end', "zstd.cho", '        if p + 5 > end {\n            return (2, p, 0);', '        if false {\n            return (2, p, 0);'),
    ('zstd: regen > 131072', "zstd.cho", '    if regen > 131072 {\n        return (1, p, 0);', '    if false {\n        return (1, p, 0);'),
    ('zstd: section_end > end', "zstd.cho", '    if section_end > end {\n        return (2, p, 0);', '    if false {\n        return (2, p, 0);'),
    ('zstd: state[3] == 0', "zstd.cho", '    } else if state[3] == 0 {\n        return (1, p, 0);', '    } else if false {\n        return (1, p, 0);'),
    ('zstd: huffman_stream(src, at, section_end, regen, lits, 0, hsym, h', "zstd.cho", '        if huffman_stream(src, at, section_end, regen, lits, 0, hsym, hnb, max_bits) != 0 {\n            return (1, p, 0);', '        if false {\n            return (1, p, 0);'),
    ('zstd: at + 6 > section_end', "zstd.cho", '    if at + 6 > section_end {\n        return (1, p, 0);', '    if false {\n        return (1, p, 0);'),
    ('zstd: s4 < 1', "zstd.cho", '    if s4 < 1 {\n        return (1, p, 0);', '    if false {\n        return (1, p, 0);'),
    ('zstd: 3 * seg > regen', "zstd.cho", '    if 3 * seg > regen {\n        return (1, p, 0);', '    if false {\n        return (1, p, 0);'),
    ('zstd: huffman_stream(src, data, data + s1, seg, lits, 0, hsym, hnb', "zstd.cho", '    if huffman_stream(src, data, data + s1, seg, lits, 0, hsym, hnb, max_bits) != 0 || huffman_stream(src, data + s1, data + s1 + s2, seg, lits, seg, hsym, hnb, max_bits) != 0 || huffman_stream(src, data + s1 + s2, data + s1 + s2 + s3, seg, lits, 2 * seg, hsym, hnb, max_bits) != 0 || huffman_stream(src, data + s1 + s2 + s3, section_end, regen - 3 * seg, lits, 3 * seg, hsym, hnb, max_bits) != 0 {\n        return (1, p, 0);', '    if false {\n        return (1, p, 0);'),
    ('zstd: at >= end', "zstd.cho", '    var count = 0;\n    if at >= end {\n        return (2, made);\n    }', '    var count = 0;\n    if false {\n        return (2, made);\n    }'),
    ('zstd: at >= end (2)', "zstd.cho", '        if at >= end {\n            return (2, made);', '        if false {\n            return (2, made);'),
    ('zstd: at + 2 > end', "zstd.cho", '        if at + 2 > end {\n            return (2, made);', '        if false {\n            return (2, made);'),
    ('zstd: at >= end (3)', "zstd.cho", '    }\n    if at >= end {\n        return (2, made);\n    }', '    }\n    if false {\n        return (2, made);\n    }'),
    ('zstd: modes & 3 != 0', "zstd.cho", '    if modes & 3 != 0 {\n        return (1, made);', '    if false {\n        return (1, made);'),
    ('zstd: build_table(norm, len(text), default_accuracy(which), off, s', "zstd.cho", '            if build_table(norm, len(text), default_accuracy(which), off, sym, nbits, base, next) != 0 {\n                return (1, made);', '            if false {\n                return (1, made);'),
    ('zstd: at >= end (4)', "zstd.cho", '            if at >= end {\n                return (2, made);', '            if false {\n                return (2, made);'),
    ('zstd: only > max_symbol(which)', "zstd.cho", '            if only > max_symbol(which) {\n                return (1, made);', '            if false {\n                return (1, made);'),
    ('zstd: al < 0 || build_table(norm, nsym, al, off, sym, nbits, base,', "zstd.cho", '            if al < 0 || build_table(norm, nsym, al, off, sym, nbits, base, next) != 0 {\n                return (1, made);', '            if false {\n                return (1, made);'),
    ('zstd: state[5 + which] == 0', "zstd.cho", '        } else if state[5 + which] == 0 {\n            return (1, made);', '        } else if false {\n            return (1, made);'),
    ('zstd: offset == 0', "zstd.cho", '                if offset == 0 {\n                    return (1, made);', '                if false {\n                    return (1, made);'),
    ('zstd: lit_at + ll > lits_len', "zstd.cho", '        if lit_at + ll > lits_len {\n            return (1, made);', '        if false {\n            return (1, made);'),
    ('zstd: offset > made || offset > window', "zstd.cho", '        if offset > made || offset > window {\n            return (1, made);', '        if false {\n            return (1, made);'),
    ('zstd: pos != 0', "zstd.cho", '    if pos != 0 {\n        return (1, made);', '    if false {\n        return (1, made);'),
    ('zstd: made + (lits_len - lit_at) > room', "zstd.cho", '    if made + (lits_len - lit_at) > room {\n        return (3, made);', '    if false {\n        return (3, made);'),
    ('zstd: p + 3 > len(src)', "zstd.cho", '                                                                if p + 3 > len(src) {\n                                                                    status = 2;', '                                                                if false {\n                                                                    status = 2;'),
    ('zstd: kind == 3 || size > biggest', "zstd.cho", '                                                                    if kind == 3 || size > biggest {\n                                                                        status = 1;', '                                                                    if false {\n                                                                        status = 1;'),
    ('zstd: p + size > len(src)', "zstd.cho", '                                                                        if p + size > len(src) {\n                                                                            status = 2;', '                                                                        if false {\n                                                                            status = 2;'),
    ('zstd: p + 1 > len(src)', "zstd.cho", '                                                                        if p + 1 > len(src) {\n                                                                            status = 2;', '                                                                        if false {\n                                                                            status = 2;'),
    ('zstd: made + size > limit (2)', "zstd.cho", '                                                                        if p + 1 > len(src) {\n                                                                            status = 2;\n                                                                        } else if made + size > limit {\n                                                                            status = 3;\n                                                                        } else {', '                                                                        if p + 1 > len(src) {\n                                                                            status = 2;\n                                                                        } else if false {\n                                                                            status = 3;\n                                                                        } else {'),
    ('zstd: p + size > len(src) (2)', "zstd.cho", '                                                                    } else if p + size > len(src) {\n                                                                        status = 2;', '                                                                    } else if false {\n                                                                        status = 2;'),
    ('a zstd frame with a reserved descriptor bit is read', "zstd.cho", '    if descriptor & 8 != 0 {', '    if false {'),
    ('a zstd frame that needs a dictionary is read', "zstd.cho", '    if id_size > 0 && le_n(src, p, id_size) != 0 {', '    if false {'),
    ('a zstd content size past the cap is not noticed up front', "zstd.cho", '        if content_size < 0 || content_size > limit {', '        if false {'),
    ("a zstd content size that is not the content's is accepted", "zstd.cho", '    if result == 0 && content_size >= 0 && content_size != made {', '    if false {'),
    ('a zstd content checksum is not verified', "zstd.cho", '                same = xxh64(contents(dr)[0..made]) & 0xffffffff == le_n(src, end, 4);', '                same = true;'),
    ('bytes after a zstd frame are accepted', "zstd.cho", '    if result == 0 && end != n {', '    if false {'),
    ('a zstd repeat offset is not shifted when there are no literals', "zstd.cho", '            if ll == 0 {\n                which_rep = which_rep + 1;', '            if false {\n                which_rep = which_rep + 1;'),
    ('a zstd offset of repeat-one-minus-one is the first offset', "zstd.cho", '                offset = state[0] - 1;', '                offset = state[0];'),
    ('zstd writer: a match may reach past the window', "zstd.cho", '        if cand >= 0 && p - cand <= 8388608 && le_n(data, cand, 4) == v {', '        if cand >= 0 && p - cand <= 99999999 && le_n(data, cand, 4) == v {'),
    ('zstd writer: a candidate is not compared', "zstd.cho", '        if cand >= 0 && p - cand <= 8388608 && le_n(data, cand, 4) == v {', '        if cand >= 0 && p - cand <= 8388608 {'),
    ('zstd writer: every position is a match', "zstd.cho", '        if length >= 4 {\n            seqs[n] = p - anchor;', '        if length >= 0 {\n            seqs[n] = p - anchor;'),
    ('zstd writer: an offset value is one off', "zstd.cho", '            seqs[65552 + n] = p - cand + 3;', '            seqs[65552 + n] = p - cand + 2;'),
    ('zstd writer: a run is not a run', "zstd.cho", '            if end - start > 1 && all_same(data, start, end) {', '            if false && all_same(data, start, end) {'),
    ('zstd writer: a block that does not shrink is compressed', "zstd.cho", '                if body_size > 0 && body_size < end - start {', '                if body_size > 0 {'),
    ('zstd writer: the window is smaller', "zstd.cho", '    out = buffer.push(heap, out, byte_of(0x68));', '    out = buffer.push(heap, out, byte_of(0x58));'),
    ('zstd writer: the checksum is another', "zstd.cho", '    let sum = xxh64(data);', '    let sum = xxh64(data) + 1;'),
    ('zstd writer: the content size is cut', "zstd.cho", '        out = buffer.push(heap, out, byte_of(n >> 8 * i & 255));', '        out = buffer.push(heap, out, byte_of(n >> 8 * i & 127));'),
    ('zstd writer: the one-byte literals header runs a byte long', "zstd.cho", '    if lits < 32 {', '    if lits < 33 {'),
    ('zstd writer: the two-byte literals header runs a byte long', "zstd.cho", '    } else if lits < 4096 {', '    } else if lits < 4097 {'),
    ('zstd writer: the one-byte sequence count runs a byte long', "zstd.cho", '    if n < 128 {\n        body = buffer.push(heap, body, byte_of(n));', '    if n < 129 {\n        body = buffer.push(heap, body, byte_of(n));'),
    ('zstd writer: the last block is not known at the size of a block', "zstd.cho", 'if end >= n {', 'if end > n {'),
    ('zstd writer: a state is not looked for in its range', "zstd.cho", '            if target < 0 || base[off + y] <= target && target < base[off + y] + (1 << nbits[off + y]) {', '            if true {'),
    # Not mutated, on purpose (zstd): each of these guards is backed by another check that gives the same answer, and no input was found
    # that tells them apart (the zstd tests try inputs built to).
    #  * the `pos < ...` checks of the sequence bitstream (the end marker, the three states, the extra bits, the state updates): once one is
    #    skipped the next one, or the final `pos != 0`, refuses the stream with the same tag, and a skipped check cannot reach before the
    #    stream because the next check follows within the same sequence;
    #  * `used < 0` after a tree that is not one: it leaves the tables empty and moves the section's layout by a byte, so no frame was found
    #    that is then accepted (it would need streams that are the end marker alone);
    #  * the output-room checks `made + lits_len`, `made + ll + ml` and the first `made + size > limit` (a raw block): they keep memory to
    #    the cap, which a block of at most 128 KiB over the cap cannot show, and the caller refuses the larger output with the same tag
    #    (a raw block's file is larger than its content, so the file is refused first).
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


def build_and_test(only=None):
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
        p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_archive"] + (["-k", only] if only else []), cwd=ROOT / "tests" / "conformance",
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
            verdict, why = build_and_test("zstd" if file == "zstd.cho" else None)
            if verdict == "SURVIVED" and file == "zstd.cho":
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
