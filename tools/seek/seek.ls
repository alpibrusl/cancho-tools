edition 5;

// `seek` -- literal search over named files, for an agent's own loop.
//
//     seek [--root DIR] [--max-count N] [--skip N] [--max-line-bytes N]
//          [--ascii-case-insensitive] [--require-match] [--format ndjson|text]
//          PATTERN FILE...
//
// The successor of lex-sys `examples/seek/` (lex-sys `docs/agent-tools.md`),
// rebuilt on the toolbox contract (lex-sys `docs/agent-toolbox.md` §3, D15
// row 1). What changed, each a decision of that document:
//
// * NDJSON by default (D2): a `match` record per matching line, a `file`
//   record per file searched, an `error` record per file that could not
//   be, and an `end` record last. A stream with no `end` record was cut
//   short.
// * Zero matches is exit 0 (D4); `--require-match` gives `grep`'s "no match
//   is a failure" on request, as `precondition.no-match`, exit 8.
// * Memory is one chunk plus the longest line, never the file (D8): a line
//   longer than `--max-line-bytes` is `limit.line-too-long` with a repair
//   that raises the cap to the line's length, up to the ceiling.
// * `--max-count` stops after that many matches in total and says
//   `truncated: true` with `next: {"skip": N}`; `--skip N` resumes there.
// * A line that is not UTF-8 is written as `{"b64": …}`, never mangled.
//
// What it is not: a regex search (L8; use `rg`), or faster than `grep`.
//
// Its authority, read from the compiler and embedded by the build, is
// args, dir_read, err_write, file_read, fs_read(""), heap, io_write -- no fs_write,
// no network, no foreign code, no standard input.

import std.buffer;
import std.bytes;
import std.io;
import std.json;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.limit;
import toolbox.lines;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve every FILE relative to this directory and refuse paths outside it;max-count|m|nat|none||stop after this many matches in total, and report truncated with a next cursor;skip||nat|none|0|skip this many matches before reporting any (the next cursor of a truncated run);max-line-bytes||nat|none|1048576|the longest line held, a longer one is limit.line-too-long (ceiling 16777216);ascii-case-insensitive|i|bool|none||fold ASCII letters only when comparing;require-match||bool|none||exit 8 (precondition.no-match) when nothing matched;format||choice:ndjson/text|none|ndjson|ndjson for a program, text (path:line:text) for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "seek", version: "0.1.0", summary: "Search files for a literal string; NDJSON records with tagged errors, bounded memory, and an authority with no write, network or foreign code.", usage: "seek [--root DIR] [--max-count N] [--skip N] [--max-line-bytes N] [--ascii-case-insensitive] [--require-match] [--format ndjson|text] PATTERN FILE...", output: "stream", schema: "seek.v1", flags: flag_table(), operands: "PATTERN|none|the literal bytes to find, not a regular expression;FILE...|path-read|the files to search, in order", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.missing-operand;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long;precondition.no-match", limits: "max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "no" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn ceiling() -> [] int {
    return 16777216;
}

// ---- matching ------------------------------------------------------------

fn lower(c: int) -> [] int {
    if c >= 'A' && c <= 'Z' {
        return c + 32;
    }
    return c;
}

// The Boyer-Moore-Horspool shift for each byte: how far the window may
// move when that byte is under its last position. Built once per file, so
// the search of a line skips up to `len(needle)` bytes at a time instead
// of trying every offset (`std.bytes.find` does; the naive loop was 44% of
// `seek`'s time, measured with callgrind). With `fold`, the table is built
// over ASCII-lowered bytes.
fn shifts[&n, &t](needle: &n [byte], fold: bool, table: &!t [int]) -> [] int {
    let m = len(needle);
    var c = 0;
    while c < 256 {
        table[c] = m;
        c = c + 1;
    }
    var i = 0;
    while i + 1 < m {
        var b = int_of(needle[i]);
        if fold {
            b = lower(b);
            table[upper(b)] = m - 1 - i;
        }
        table[b] = m - 1 - i;
        i = i + 1;
    }
    return 0;
}

fn upper(c: int) -> [] int {
    if c >= 'a' && c <= 'z' {
        return c - 32;
    }
    return c;
}

// The index of the needle byte to look for first: the one that occurs least
// often in `sample` (the file's first block). Measured rather than guessed
// from a table of letter frequencies: such a table called `z` rare, and a
// file full of "zeta" made every `z` a false candidate. Ties go to the
// earlier byte. Counting is one `memchr` per occurrence of each needle byte
// in at most 64 KiB, once per file.
fn rarest[&n, &s](needle: &n [byte], sample: &s [byte]) -> [] int {
    var part = sample;
    if len(part) > 65536 {
        part = sample[0..65536];
    }
    var best = 0;
    var fewest = bytes.count_byte(part, int_of(needle[0]));
    var i = 1;
    while i < len(needle) && fewest > 0 {
        let n = bytes.count_byte(part, int_of(needle[i]));
        if n < fewest {
            best = i;
            fewest = n;
        }
        i = i + 1;
    }
    return best;
}

// Where `needle` next occurs in `text` at or after `from`, or -1, folding
// ASCII letters when `fold`. `text` is a whole block of lines, not one line
// (see `search`): a match that crossed a newline would need the needle to
// hold one, and `search` never asks for such a needle.
//
// Without folding, the candidates are found by `memchr` on the needle's
// rarest byte (`rare`, from `rarest`), which skips text that cannot match at
// libc's speed -- what grep and ripgrep do. When candidates turn out to be
// dense anyway (every byte of the needle is common here), the rest of the
// block is searched with Boyer-Moore-Horspool, whose cost does not depend on
// how common any byte is.
fn find_from[&l, &n, &t](text: &l [byte], from: int, needle: &n [byte], fold: bool, table: &t [int], rare: int) -> [] int {
    let m = len(needle);
    if m == 0 {
        return from;
    }
    if fold || m < 2 {
        return horspool(text, from, needle, fold, table);
    }
    let wanted = needle[rare];
    let stop = len(text) - m + rare + 1;
    var at = from;
    var misses = 0;
    while at + m <= len(text) {
        if misses > 16 && misses * 64 > at - from {
            return horspool(text, at, needle, fold, table);
        }
        let k = index_of_byte(text[at + rare..stop], wanted);
        if k < 0 {
            return 0 - 1;
        }
        let start = at + k;
        var j = 0;
        while j < m && text[start + j] == needle[j] {
            j = j + 1;
        }
        if j == m {
            return start;
        }
        misses = misses + 1;
        at = start + 1;
    }
    return 0 - 1;
}

// Boyer-Moore-Horspool from `from`, with the shift table `shifts` built.
fn horspool[&l, &n, &t](text: &l [byte], from: int, needle: &n [byte], fold: bool, table: &t [int]) -> [] int {
    let m = len(needle);
    let last = m - 1;
    var at = from;
    if !fold {
        let tail = int_of(needle[last]);
        while at + m <= len(text) {
            let c = int_of(text[at + last]);
            if c == tail {
                var j = 0;
                while j < last && text[at + j] == needle[j] {
                    j = j + 1;
                }
                if j == last {
                    return at;
                }
            }
            at = at + table[c];
        }
        return 0 - 1;
    }
    let tail = lower(int_of(needle[last]));
    while at + m <= len(text) {
        let c = int_of(text[at + last]);
        if lower(c) == tail {
            var j = 0;
            while j < last && lower(int_of(text[at + j])) == lower(int_of(needle[j])) {
                j = j + 1;
            }
            if j == last {
                return at;
            }
        }
        at = at + table[c];
    }
    return 0 - 1;
}

// Where the last newline in `data` is, or -1. Read backwards, so its cost is
// the length of the unfinished line after it, not of the block.
fn last_newline[&d](data: &d [byte]) -> [] int {
    var i = len(data) - 1;
    while i >= 0 && int_of(data[i]) != 10 {
        i = i - 1;
    }
    return i;
}

// Where the line holding `at` begins.
fn line_start[&d](data: &d [byte], at: int) -> [] int {
    var i = at;
    while i > 0 && int_of(data[i - 1]) != 10 {
        i = i - 1;
    }
    return i;
}

// Where the line holding `at` ends (its newline, or the end of `data`).
fn line_end[&d](data: &d [byte], at: int) -> [] int {
    let k = index_of_byte(data[at..len(data)], byte_of(10));
    if k < 0 {
        return len(data);
    }
    return at + k;
}

// ---- the run -------------------------------------------------------------

// The state of one invocation, moved through each file.
struct Tally {
    // Matches counted (reported or skipped), and reported.
    found: int,
    reported: int,
    files: int,
    // Output failed; nothing more is written.
    broken: bool,
    // Stopped by --max-count.
    truncated: bool,
    // How many errors have been written as records.
    emitted: int,
}

fn broken(t: Tally) -> [] Tally {
    return Tally { found: t.found, reported: t.reported, files: t.files, broken: true, truncated: t.truncated, emitted: t.emitted };
}

fn truncated(t: Tally) -> [] Tally {
    return Tally { found: t.found, reported: t.reported, files: t.files, broken: t.broken, truncated: true, emitted: t.emitted };
}

fn emitted(t: Tally, n: int) -> [] Tally {
    return Tally { found: t.found, reported: t.reported, files: t.files, broken: t.broken, truncated: t.truncated, emitted: n };
}

fn counted(t: Tally, reported: bool) -> [] Tally {
    var r = t.reported;
    if reported {
        r = r + 1;
    }
    return Tally { found: t.found + 1, reported: r, files: t.files, broken: t.broken, truncated: t.truncated, emitted: t.emitted };
}

fn searched(t: Tally) -> [] Tally {
    return Tally { found: t.found, reported: t.reported, files: t.files + 1, broken: t.broken, truncated: t.truncated, emitted: t.emitted };
}

// Write the errors added since the last call as records (ndjson mode).
fn sync[&h, &i, &e](heap: &!h Heap, io: &!i Io, errs: &e fail.Errors, tally: Tally, text_mode: bool) -> [heap, io_write] Tally {
    var t = tally;
    if !text_mode && !t.broken {
        if !out.error_records(heap, io, errs, t.emitted) {
            t = broken(t);
        }
    }
    return emitted(t, fail.count(errs));
}

// One match record and its newline,
// `{"type":"match","path":…,"line":…,"offset":…,"text":…}`, built into
// `into` and written with one `write_bytes`. `prefix` is the part that does
// not change within a file -- the type and the path, escaped once per file --
// and the buffer is reused for every match: a writer per match was most of a
// match-heavy search's time. The schema test (M1) and the differential test
// against grep (M5) check what this writes like any other output.
fn match_record[&h, &i, &p, &t](heap: &!h Heap, io: &!i Io, into: buffer.Buffer, prefix: &p [byte], number: int, offset: int, line: &t [byte]) -> [heap, io_write] (buffer.Buffer, bool) {
    var b = into;
    borrow mut b as &!w in {
        buffer.clear(w);
    }
    b = buffer.append(heap, b, prefix);
    b = text.append_nat(heap, b, number);
    b = buffer.append(heap, b, ",\"offset\":");
    b = text.append_nat(heap, b, offset);
    b = buffer.append(heap, b, ",\"text\":");
    b = text.append_json(heap, b, line);
    b = buffer.append(heap, b, "}\n");
    var ok = false;
    borrow b as &r in {
        ok = out.emit(io, buffer.bytes(r));
    }
    return (b, ok);
}

fn text_line[&h, &p, &t](heap: &!h Heap, shown: &p [byte], number: int, line: &t [byte]) -> [heap] buffer.Buffer {
    var b = buffer.empty(heap, len(shown) + len(line) + 24);
    b = buffer.append(heap, b, shown);
    b = buffer.push(heap, b, byte_of(':'));
    b = buffer.push_nat(heap, b, number);
    b = buffer.push(heap, b, byte_of(':'));
    return buffer.append(heap, b, line);
}

// Search one open file.
fn search[&h, &g, &p, &f, &i, &n, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, file: &!f File, io: &!i Io, pattern: &n [byte], shown: &s [byte], errs: fail.Errors, tally: Tally) -> [heap, args, file_read, io_write] (fail.Errors, Tally, int) {
    let table = flag_table();
    let fold = cli.has(parsed, table, "ascii-case-insensitive");
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let skip = cli.nat(args, parsed, table, "skip");
    var most = 0 - 1;
    if cli.has(parsed, table, "max-count") {
        most = cli.nat(args, parsed, table, "max-count");
    }
    var e = errs;
    var t = tally;
    var here = 0;
    var overlong = limit.none();
    let skip_table = box_slice(heap, 256, 0);
    borrow mut skip_table as &!tw in {
        shifts(pattern, fold, contents(tw));
    }
    var record = buffer.empty(heap, 256);
    var prefix = buffer.empty(heap, 64);
    prefix = buffer.append(heap, prefix, "{\"type\":\"match\",\"path\":");
    prefix = text.append_json(heap, prefix, shown);
    prefix = buffer.append(heap, prefix, ",\"line\":");
    // The file is scanned a block at a time, not a line at a time: each read
    // fills `buf` after the unfinished line the last block left at its front,
    // and the whole lines in it are searched in one pass. Lines are found only
    // around a match, and counted with `memchr` (`bytes.count_byte`) only so
    // that a match's number is right; a file with no match never has its lines
    // taken apart. (Line by line, the search, the reader and the loop between
    // them were 93% of a search with no match, measured with callgrind.)
    //
    // `buf` holds `cap` bytes of unfinished line plus one read, so a line that
    // fits under --max-line-bytes always fits; one that does not is skipped up
    // to its newline and reported with its length, as before. The allocation is
    // zero-filled, so pages a short-lined file never reaches are never touched
    // (lex-sys `docs/zeroed-slices.md`), and memory stays flat (M9).
    let block = cap + lines.chunk_size();
    let buf = box_slice(heap, block, byte_of(0));
    var filled = 0;
    var base = 0;
    var before = 0;
    var ended = false;
    var errno = 0;
    var read_total = 0;
    var binary = false;
    var skipping = false;
    var skip_len = 0;
    // A needle that holds a newline is in no line.
    let plain = index_of_byte(pattern, byte_of(10)) < 0;
    var rare = 0 - 1;
    borrow prefix as &pre in {
        borrow skip_table as &tr in {
            var going = true;
            while going && !t.broken {
                if !ended && filled < block {
                    var top = filled + lines.chunk_size();
                    if top > block {
                        top = block;
                    }
                    borrow mut buf as &!bw in {
                        let room = contents(bw)[filled..top];
                        match file_read(file, room) {
                            Read::Got(n) => {
                                if !binary && index_of_byte(room[0..n], byte_of(0)) >= 0 {
                                    binary = true;
                                }
                                filled = filled + n;
                                read_total = read_total + n;
                            }
                            Read::End => {
                                ended = true;
                            }
                            Read::Failed(reason) => {
                                ended = true;
                                errno = reason;
                                if errno == 0 {
                                    errno = 5;
                                }
                            }
                        }
                    }
                }
                // `whole`: the bytes of whole lines at the front, to search now. `dropped`:
                // bytes of an over-long line, to discard unsearched.
                var whole = 0;
                var dropped = 0;
                borrow buf as &br in {
                    let data = contents(br)[0..filled];
                    if skipping {
                        let k = index_of_byte(data, byte_of(10));
                        if k < 0 {
                            skip_len = skip_len + filled;
                            dropped = filled;
                            if ended {
                                overlong = limit.more(overlong, before + 1, skip_len);
                                skipping = false;
                            }
                        } else {
                            overlong = limit.more(overlong, before + 1, skip_len + k);
                            skipping = false;
                            before = before + 1;
                            dropped = k + 1;
                        }
                    } else {
                        let nl = last_newline(data);
                        if nl >= 0 {
                            whole = nl + 1;
                        } else if ended {
                            // The last line, with no newline (or nothing at all).
                            whole = filled;
                        } else if filled > cap {
                            skipping = true;
                            skip_len = filled;
                            dropped = filled;
                        }
                    }
                    if whole > 0 {
                        let text_block = data[0..whole];
                        if rare < 0 && len(pattern) > 0 {
                            rare = rarest(pattern, text_block);
                        }
                        // Only a block longer than the cap can hold a line over it.
                        if whole > cap {
                            var at = 0;
                            var number = before;
                            while at < whole {
                                let e = line_end(text_block, at);
                                number = number + 1;
                                if e - at > cap {
                                    overlong = limit.more(overlong, number, e - at);
                                }
                                at = e + 1;
                            }
                        }
                        var seen_at = 0;
                        var seen = before;
                        var from = 0;
                        while plain && from < whole && going && !t.broken {
                            let hit = find_from(text_block, from, pattern, fold, contents(tr), rare);
                            if hit < 0 {
                                from = whole;
                            } else {
                                let s0 = line_start(text_block, hit);
                                let e0 = line_end(text_block, hit);
                                seen = seen + bytes.count_byte(text_block[seen_at..s0], 10);
                                seen_at = s0;
                                if e0 - s0 <= cap {
                                    if most >= 0 && t.reported >= most {
                                        t = truncated(t);
                                        going = false;
                                    } else {
                                        here = here + 1;
                                        let shows = t.found + 1 > skip;
                                        t = counted(t, shows);
                                        if shows {
                                            var wrote = true;
                                            if text_mode {
                                                wrote = out.buffer_line(heap, io, text_line(heap, shown, seen + 1, text_block[s0..e0]));
                                            } else {
                                                let (kept, written) = match_record(heap, io, record, buffer.bytes(pre), seen + 1, base + s0, text_block[s0..e0]);
                                                record = kept;
                                                wrote = written;
                                            }
                                            if !wrote {
                                                t = broken(t);
                                            }
                                        }
                                    }
                                }
                                from = e0 + 1;
                            }
                        }
                        before = seen + bytes.count_byte(text_block[seen_at..whole], 10);
                    }
                }
                let consumed = whole + dropped;
                if consumed > 0 {
                    borrow mut buf as &!bw in {
                        copy_within(contents(bw), 0, consumed, filled - consumed);
                    }
                    filled = filled - consumed;
                    base = base + consumed;
                }
                if ended && filled == 0 && !skipping {
                    going = false;
                }
            }
        }
    }
    unbox_slice(heap, buf);
    let size = read_total;
    unbox_slice(heap, skip_table);
    buffer.drop(heap, record);
    buffer.drop(heap, prefix);
    if overlong.count > 0 {
        e = limit.too_long(heap, e, args, parsed, flag_table(), shown, overlong, cap, ceiling(), "lines longer than --max-line-bytes were skipped; the rest of the file was searched");
        borrow e as &er in {
            t = sync(heap, io, er, t, text_mode);
        }
    }
    if errno != 0 {
        return (e, t, errno);
    }
    t = searched(t);
    if !text_mode && !t.broken {
        var w = json.writer(heap, 128);
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "type");
        w = json.put_string(heap, w, "file");
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "matches");
        w = json.put_int(heap, w, here);
        w = json.put_key(heap, w, "bytes");
        w = json.put_int(heap, w, size);
        w = json.put_key(heap, w, "binary");
        w = json.put_bool(heap, w, binary);
        w = json.put_key(heap, w, "complete");
        w = json.put_bool(heap, w, !t.truncated);
        if !out.close_line(heap, io, w) {
            t = broken(t);
        }
    }
    return (e, t, 0);
}

// Resolve, open and search operand `at`.
fn one_file[&h, &g, &p, &f, &i, &r, &n](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, root: &r [byte], at: int, pattern: &n [byte], errs: fail.Errors, tally: Tally) -> [heap, args, fs_read(""), dir_read, file_read, io_write] (fail.Errors, Tally) {
    let text_mode = bytes.equal(cli.text(args, parsed, flag_table(), "format"), "text");
    var t = tally;
    let (resolved, checked) = path.operand(heap, args, root, at, errs);
    var e = checked;
    borrow resolved as &rp in {
        if path.ok(rp) {
            match place.open_operand(fs, root, path.shown(rp), path.full(rp)) {
                Opened::Failed(reason) => {
                    e = fail.io_error(heap, e, reason, false, path.shown(rp));
                }
                Opened::Ok(opened) => {
                    var file = opened;
                    borrow mut file as &!handle in {
                        let (searched, counted, failed) = search(heap, args, parsed, handle, io, pattern, path.shown(rp), e, t);
                        e = searched;
                        t = counted;
                        if failed != 0 {
                            e = fail.io_error(heap, e, failed, false, path.shown(rp));
                        }
                    }
                    file_close(file);
                }
            }
        }
    }
    path.drop(heap, resolved);
    borrow e as &er in {
        t = sync(heap, io, er, t, text_mode);
    }
    return (e, t);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    var t = Tally { found: 0, reported: 0, files: 0, broken: false, truncated: false, emitted: 0 };

    // The cap is checked against its ceiling where the flag is read, so the
    // reader is never handed one the heap cannot hold.
    if cli.nat(args, parsed, table, "max-line-bytes") > ceiling() {
        var w = fail.open(heap, "args.bad-value", "--max-line-bytes is above the ceiling", "16777216 is the most this tool holds");
        w = fail.no_repair(heap, w);
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "flag");
        w = json.put_string(heap, w, "--max-line-bytes");
        w = json.put_key(heap, w, "ceiling");
        w = json.put_int(heap, w, ceiling());
        e = fail.add(heap, e, w);
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) < 2 {
        var w = fail.open(heap, "args.missing-operand", "seek takes a PATTERN and at least one FILE", "seek PATTERN FILE...");
        w = fail.no_repair(heap, w);
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "operands");
        w = json.put_int(heap, w, cli.operand_count(parsed));
        e = fail.add(heap, e, w);
    }
    borrow e as &er in {
        t = sync(heap, io, er, t, text_mode);
    }

    // A malformed invocation reads nothing (D4: exit 2 means nothing was
    // read).
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    var pattern: &static [byte] = "";
    if cli.operand_count(parsed) > 0 {
        pattern = cli.operand(args, parsed, 0);
    }
    var k = 1;
    while !refused && !t.broken && !t.truncated && k < cli.operand_count(parsed) {
        borrow root as &rr in {
            let (after_file, tally_after) = one_file(heap, args, parsed, fs, io, buffer.bytes(rr), cli.operand_index(parsed, k), pattern, e, t);
            e = after_file;
            t = tally_after;
        }
        k = k + 1;
    }
    buffer.drop(heap, root);

    if !refused && cli.has(parsed, table, "require-match") && t.found == 0 {
        e = fail.simple(heap, e, "precondition.no-match", "--require-match was given and nothing matched", "", "", "");
        borrow e as &er in {
            t = sync(heap, io, er, t, text_mode);
        }
    }

    var status = 0;
    borrow e as &er in {
        status = fail.exit_code(er);
        if text_mode {
            out.say_errors(io, "seek", er);
        } else if !t.broken {
            var w = out.end_open(heap, "seek", "seek.v1", fail.count(er) == 0, fail.count(er) == 0 && !t.truncated);
            w = json.put_key(heap, w, "files");
            w = json.put_int(heap, w, t.files);
            w = json.put_key(heap, w, "matches");
            w = json.put_int(heap, w, t.reported);
            w = json.put_key(heap, w, "errors");
            w = json.put_int(heap, w, fail.count(er));
            w = json.put_key(heap, w, "truncated");
            w = json.put_bool(heap, w, t.truncated);
            w = json.put_key(heap, w, "next");
            if t.truncated {
                w = json.begin_object(heap, w);
                w = json.put_key(heap, w, "skip");
                w = json.put_int(heap, w, t.found);
                w = json.end_object(heap, w);
            } else {
                w = json.put_null(heap, w);
            }
            if !out.close_line(heap, io, w) {
                t = broken(t);
            }
        }
    }
    fail.drop(heap, e);
    if !t.broken && !out.flushed(io) {
        t = broken(t);
    }
    if t.broken {
        out.write_failed(io, "seek");
        return 1;
    }
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
    let which = cli.subcommand(args);
    if which != 0 {
        return describe.answer(heap, io, which, tool(), built());
    }
    let (parsed, e) = cli.parse(heap, args, flag_table(), fail.empty(heap));
    var status = 0;
    borrow parsed as &p in {
        status = body(heap, args, p, fs, io, e);
    }
    cli.drop(heap, parsed);
    return status;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    // No foreign code, no network, no clock: each release is a statement of
    // what this tool will never do, and the authority report checks it.
    release(ffi);
    release(net);
    release(clock);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            borrow fs as &f in {
                borrow mut io as &!i in {
                    status = run(h, g, f, i);
                }
            }
        }
    }
    release(heap);
    release(args);
    release(fs);
    release(io);
    return status;
}
