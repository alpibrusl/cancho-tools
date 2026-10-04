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
// args, err_write, file_read, fs_read(""), heap, io_write -- no fs_write,
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
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve every FILE relative to this directory and refuse paths outside it;max-count|m|nat|none||stop after this many matches in total, and report truncated with a next cursor;skip||nat|none|0|skip this many matches before reporting any (the next cursor of a truncated run);max-line-bytes||nat|none|1048576|the longest line held; a longer one is limit.line-too-long (ceiling 16777216);ascii-case-insensitive|i|bool|none||fold ASCII letters only when comparing;require-match||bool|none||exit 8 (precondition.no-match) when nothing matched;format||choice:ndjson/text|none|ndjson|ndjson for a program, text (path:line:text) for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "seek", version: "0.1.0", summary: "Search files for a literal string; NDJSON records with tagged errors, bounded memory, and an authority with no write, network or foreign code.", usage: "seek [--root DIR] [--max-count N] [--skip N] [--max-line-bytes N] [--ascii-case-insensitive] [--require-match] [--format ndjson|text] PATTERN FILE...", output: "stream", schema: "seek.v1", flags: flag_table(), operands: "PATTERN|none|the literal bytes to find; not a regular expression;FILE...|path-read|the files to search, in order", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.missing-operand;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long;precondition.no-match", limits: "max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "no" };
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

// Whether `needle` occurs in `line`, folding ASCII letters when `fold`.
fn contains[&l, &n](line: &l [byte], needle: &n [byte], fold: bool) -> [] bool {
    if !fold {
        return bytes.find(line, needle) >= 0;
    }
    if len(needle) == 0 {
        return true;
    }
    var i = 0;
    while i + len(needle) <= len(line) {
        var j = 0;
        while j < len(needle) && lower(int_of(line[i + j])) == lower(int_of(needle[j])) {
            j = j + 1;
        }
        if j == len(needle) {
            return true;
        }
        i = i + 1;
    }
    return false;
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

fn match_record[&h, &p, &t](heap: &!h Heap, shown: &p [byte], number: int, offset: int, line: &t [byte]) -> [heap] buffer.Buffer {
    var w = json.writer(heap, len(line) + 96);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "match");
    w = json.put_key(heap, w, "path");
    w = text.put(heap, w, shown);
    w = json.put_key(heap, w, "line");
    w = json.put_int(heap, w, number);
    w = json.put_key(heap, w, "offset");
    w = json.put_int(heap, w, offset);
    w = json.put_key(heap, w, "text");
    w = text.put(heap, w, line);
    w = json.end_object(heap, w);
    return json.finish(w);
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
    var r = lines.start(heap, cap);
    var going = true;
    while going && !t.broken {
        let (next, status) = lines.next(heap, r);
        r = next;
        if status == lines.need() {
            r = lines.fill_file(r, file);
        } else if status == lines.done() {
            going = false;
        } else if status == lines.long() {
            borrow r as &rr in {
                overlong = limit.more(overlong, lines.number(rr), lines.length(rr));
            }
        } else {
            var hit = false;
            borrow r as &rr in {
                hit = contains(lines.text(rr), pattern, fold);
            }
            if hit {
                if most >= 0 && t.reported >= most {
                    t = truncated(t);
                    going = false;
                } else {
                    here = here + 1;
                    let shows = t.found + 1 > skip;
                    t = counted(t, shows);
                    if shows {
                        var wrote = true;
                        borrow r as &rr in {
                            if text_mode {
                                wrote = out.buffer_line(heap, io, text_line(heap, shown, lines.number(rr), lines.text(rr)));
                            } else {
                                wrote = out.buffer_line(heap, io, match_record(heap, shown, lines.number(rr), lines.offset(rr), lines.text(rr)));
                            }
                        }
                        if !wrote {
                            t = broken(t);
                        }
                    }
                }
            }
        }
    }
    var errno = 0;
    var size = 0;
    var binary = false;
    borrow r as &rr in {
        errno = lines.failed(rr);
        size = lines.bytes_read(rr);
        binary = lines.saw_nul(rr);
    }
    lines.drop(heap, r);
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
fn one_file[&h, &g, &p, &f, &i, &r, &n](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, root: &r [byte], at: int, pattern: &n [byte], errs: fail.Errors, tally: Tally) -> [heap, args, fs_read(""), file_read, io_write] (fail.Errors, Tally) {
    let text_mode = bytes.equal(cli.text(args, parsed, flag_table(), "format"), "text");
    var t = tally;
    let (resolved, checked) = path.operand(heap, args, root, at, errs);
    var e = checked;
    borrow resolved as &rp in {
        if path.ok(rp) {
            match open_read(fs, path.full(rp)) {
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

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), file_read, io_write, err_write] int {
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
    if t.broken {
        out.write_failed(io, "seek");
        return 1;
    }
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), file_read, io_write, err_write] int {
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
