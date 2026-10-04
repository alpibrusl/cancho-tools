edition 5;

// `peek` -- read a range of a file: lines with their numbers and offsets,
// or bytes, with the file's size, a binary flag and a cursor to the rest.
//
//     peek [--root DIR] [--lines A:B | --bytes A:B] [--max-bytes N]
//          [--max-line-bytes N] [--count-lines] [--format json|text] PATH
//
// lex-sys `docs/agent-toolbox.md` D15 row 3: `head`, `tail`, `sed -n a,bp`,
// `cat -n` and `wc -l` folded into the call an agent makes most often.
//
// * `--lines A:B` is 1-based and inclusive (`A:` is "to the end, within
//   the budget"); `--bytes A:B` is 0-based and end-exclusive, as offsets
//   are. The default is `--lines 1:100`.
// * `--max-bytes` is the budget for text returned (default 65536). Stopping
//   for it is a result, not an error: `truncated: true` and `next` names
//   where a following call starts (D8).
// * A line longer than `--max-line-bytes` comes back as `{"n", "offset",
//   "text": null, "length"}` and an error `limit.line-too-long`, never
//   clipped.
// * `--count-lines` reads to the end and reports `total_lines`; without it
//   the file is read only as far as the range needs.
//
// Memory is a chunk, the longest line, and the budget.

import std.buffer;
import std.bytes;
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
    return "root||path|root||resolve PATH relative to this directory and refuse paths outside it;lines|n|text|none|1:100|the lines A:B to return, 1-based and inclusive; A: runs to the end of the file or the budget;bytes|c|text|none||the bytes A:B to return, 0-based and end-exclusive;max-bytes||nat|none|65536|the most text returned; past it the answer is truncated with a next cursor (ceiling 16777216);max-line-bytes||nat|none|1048576|the longest line returned whole; a longer one is limit.line-too-long (ceiling 16777216);count-lines||bool|none||read to the end and report total_lines;format||choice:json/text|none|json|json for a program, text (cat -n) for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "peek", version: "0.1.0", summary: "Read a range of a file -- lines with numbers and offsets, or bytes -- with its size, a binary flag and a cursor to the rest; head, tail, sed -n, cat -n and wc -l in one call.", usage: "peek [--root DIR] [--lines A:B | --bytes A:B] [--max-bytes N] [--max-line-bytes N] [--count-lines] [--format json|text] PATH", output: "document", schema: "peek.v1", flags: flag_table(), operands: "PATH|path-read|the file to read", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.conflict;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long", limits: "max-bytes|65536|16777216;max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "no" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn ceiling() -> [] int {
    return 16777216;
}

// `A:B` or `A:` as `(A, B)`, with B = -1 for an open end; `(-1, -1)` for
// anything else. `lines` wants `A >= 1`; both want `B >= A`.
fn range[&t](text: &t [byte], lines_mode: bool) -> [] (int, int) {
    let colon = bytes.find(text, ":");
    if colon < 0 {
        return (0 - 1, 0 - 1);
    }
    let a = cli.parse_nat(text[0..colon]);
    var b = 0 - 1;
    if colon + 1 < len(text) {
        b = cli.parse_nat(text[colon + 1..len(text)]);
        if b < 0 {
            return (0 - 1, 0 - 1);
        }
    }
    if a < 0 || lines_mode && a < 1 || b >= 0 && b < a {
        return (0 - 1, 0 - 1);
    }
    return (a, b);
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

// What the read found, beside the lines themselves.
struct Seen {
    size: int,
    first: int,
    last: int,
    eof: bool,
    truncated: bool,
    next: int,
    total: int,
    long_count: int,
    long_first: int,
    long_most: int,
    binary: bool,
}

fn put_seen[&h](heap: &!h Heap, w: json.Writer, s: Seen, lines_mode: bool, counted: bool) -> [heap] json.Writer {
    var o = json.put_key(heap, w, "eof");
    o = json.put_bool(heap, o, s.eof);
    o = json.put_key(heap, o, "truncated");
    o = json.put_bool(heap, o, s.truncated);
    o = json.put_key(heap, o, "next");
    if s.next >= 0 {
        o = json.begin_object(heap, o);
        if lines_mode {
            o = json.put_key(heap, o, "line");
        } else {
            o = json.put_key(heap, o, "byte");
        }
        o = json.put_int(heap, o, s.next);
        o = json.end_object(heap, o);
    } else {
        o = json.put_null(heap, o);
    }
    o = json.put_key(heap, o, "total_lines");
    if counted {
        o = json.put_int(heap, o, s.total);
    } else {
        o = json.put_null(heap, o);
    }
    return o;
}

// Lines `from..to` (to -1: open) of an open file, within `budget` bytes of
// text. Answers the `lines` array's members as JSON, the text form, and
// what was seen.
fn read_lines[&h, &f](heap: &!h Heap, file: &!f File, from: int, to: int, budget: int, cap: int, count_all: bool, size: int) -> [heap, file_read] (buffer.Buffer, buffer.Buffer, Seen, int) {
    var s = Seen { size: size, first: from, last: from - 1, eof: false, truncated: false, next: 0 - 1, total: 0, long_count: 0, long_first: 0, long_most: 0, binary: false };
    var items = json.writer(heap, 1024);
    items = json.begin_array(heap, items);
    var plain = buffer.empty(heap, 1024);
    var used = 0;
    var r = lines.start(heap, cap);
    var going = true;
    var past = false;
    while going {
        let (stepped, status) = lines.next(heap, r);
        r = stepped;
        if status == lines.need() {
            r = lines.fill_file(r, file);
        } else if status == lines.done() {
            s = Seen { size: s.size, first: s.first, last: s.last, eof: true, truncated: s.truncated, next: s.next, total: s.total, long_count: s.long_count, long_first: s.long_first, long_most: s.long_most, binary: s.binary };
            going = false;
        } else {
            var n = 0;
            var size_here = 0;
            var line_at = 0;
            borrow r as &rr in {
                n = lines.number(rr);
                size_here = lines.length(rr);
                line_at = lines.offset(rr);
            }
            let wanted = n >= from && (to < 0 || n <= to) && !past;
            if wanted && used + size_here > budget && n > from {
                // Out of budget: stop here, and say where to go on. The first
                // line is always returned whole (or reported too long), so a
                // budget smaller than one line still makes progress.
                past = true;
                s = Seen { size: s.size, first: s.first, last: s.last, eof: false, truncated: true, next: n, total: s.total, long_count: s.long_count, long_first: s.long_first, long_most: s.long_most, binary: s.binary };
            } else if wanted {
                items = json.begin_object(heap, items);
                items = json.put_key(heap, items, "n");
                items = json.put_int(heap, items, n);
                items = json.put_key(heap, items, "offset");
                items = json.put_int(heap, items, line_at);
                items = json.put_key(heap, items, "text");
                if status == lines.long() {
                    items = json.put_null(heap, items);
                    items = json.put_key(heap, items, "length");
                    items = json.put_int(heap, items, size_here);
                    var lf = s.long_first;
                    if s.long_count == 0 {
                        lf = n;
                    }
                    var lm = s.long_most;
                    if size_here > lm {
                        lm = size_here;
                    }
                    s = Seen { size: s.size, first: s.first, last: n, eof: s.eof, truncated: s.truncated, next: s.next, total: s.total, long_count: s.long_count + 1, long_first: lf, long_most: lm, binary: s.binary };
                } else {
                    borrow r as &rr in {
                        items = text.put(heap, items, lines.text(rr));
                        plain = buffer.push_nat(heap, plain, n);
                        plain = buffer.push(heap, plain, byte_of(9));
                        plain = buffer.append(heap, plain, lines.text(rr));
                        plain = buffer.push(heap, plain, byte_of(10));
                    }
                    used = used + size_here;
                    s = Seen { size: s.size, first: s.first, last: n, eof: s.eof, truncated: s.truncated, next: s.next, total: s.total, long_count: s.long_count, long_first: s.long_first, long_most: s.long_most, binary: s.binary };
                }
                items = json.end_object(heap, items);
            }
            if to >= 0 && n >= to && !count_all {
                going = false;
                // Whether this was the last line is known only by looking.
                let (after, status2) = lines.next(heap, r);
                r = after;
                var more = status2;
                if more == lines.need() {
                    r = lines.fill_file(r, file);
                    let (after2, status3) = lines.next(heap, r);
                    r = after2;
                    more = status3;
                }
                if more == lines.done() {
                    s = Seen { size: s.size, first: s.first, last: s.last, eof: true, truncated: s.truncated, next: s.next, total: s.total, long_count: s.long_count, long_first: s.long_first, long_most: s.long_most, binary: s.binary };
                } else if !s.truncated {
                    s = Seen { size: s.size, first: s.first, last: s.last, eof: false, truncated: s.truncated, next: n + 1, total: s.total, long_count: s.long_count, long_first: s.long_first, long_most: s.long_most, binary: s.binary };
                }
            } else if past && !count_all {
                going = false;
            }
        }
    }
    var errno = 0;
    borrow r as &rr in {
        errno = lines.failed(rr);
        s = Seen { size: s.size, first: s.first, last: s.last, eof: s.eof, truncated: s.truncated, next: s.next, total: lines.number(rr), long_count: s.long_count, long_first: s.long_first, long_most: s.long_most, binary: lines.saw_nul(rr) };
    }
    lines.drop(heap, r);
    items = json.end_array(heap, items);
    return (json.finish(items), plain, s, errno);
}

// Bytes `from..to` (to -1: to the end) through `file_pread`, within the
// budget.
fn read_bytes[&h, &f](heap: &!h Heap, file: &!f File, from: int, to: int, budget: int, size: int) -> [heap, file_read] (buffer.Buffer, Seen, int) {
    var end = size;
    if to >= 0 && to < end {
        end = to;
    }
    var truncated = false;
    if end - from > budget {
        end = from + budget;
        truncated = true;
    }
    var want = end - from;
    if want < 0 {
        want = 0;
    }
    var got = buffer.empty(heap, want + 1);
    var errno = 0;
    var at = from;
    var going = want > 0;
    while going {
        var n = 0;
        borrow mut got as &!g in {
            let room = buffer.room(g);
            match file_pread(file, at, room[0..end - at]) {
                Read::Got(k) => {
                    buffer.filled(g, k);
                    n = k;
                }
                Read::End => {
                    going = false;
                }
                Read::Failed(reason) => {
                    errno = reason;
                    if errno == 0 {
                        errno = 5;
                    }
                    going = false;
                }
            }
        }
        at = at + n;
        if at >= end {
            going = false;
        }
    }
    var next = 0 - 1;
    if truncated {
        next = at;
    }
    var binary = false;
    borrow got as &g in {
        binary = text.has_nul(buffer.bytes(g));
    }
    let s = Seen { size: size, first: from, last: at, eof: at >= size, truncated: truncated, next: next, total: 0, long_count: 0, long_first: 0, long_most: 0, binary: binary };
    return (got, s, errno);
}

// Read the range from an open file; answers the data object and the text
// form.
fn read[&h, &g, &p, &f, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, file: &!f File, shown: &s [byte], errs: fail.Errors) -> [heap, args, file_read] (fail.Errors, buffer.Buffer, buffer.Buffer) {
    let table = flag_table();
    var e = errs;
    var size = 0;
    var errno = 0;
    match file_size(file) {
        Done::Ok(n) => {
            size = n;
        }
        Done::Failed(reason) => {
            errno = reason;
        }
    }
    let lines_mode = !cli.has(parsed, table, "bytes");
    let budget = cli.nat(args, parsed, table, "max-bytes");
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    var w = json.writer(heap, 1024);
    var plain = buffer.empty(heap, 1);
    if errno != 0 {
        e = fail.io_error(heap, e, errno, false, shown);
        return (e, json.finish(w), plain);
    }
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "path");
    w = text.put(heap, w, shown);
    w = json.put_key(heap, w, "size");
    w = json.put_int(heap, w, size);
    if lines_mode {
        let (from, to) = range(cli.text(args, parsed, table, "lines"), true);
        let (items, text_form, seen, failed_errno) = read_lines(heap, file, from, to, budget, cap, cli.has(parsed, table, "count-lines"), size);
        buffer.drop(heap, plain);
        plain = text_form;
        if failed_errno != 0 {
            e = fail.io_error(heap, e, failed_errno, false, shown);
        } else if seen.long_count > 0 {
            e = limit.too_long(heap, e, args, parsed, table, shown, limit.Long { count: seen.long_count, first: seen.long_first, longest: seen.long_most }, cap, ceiling(), "lines longer than --max-line-bytes are returned with text null and their length");
        }
        w = json.put_key(heap, w, "binary");
        w = json.put_bool(heap, w, seen.binary);
        w = json.put_key(heap, w, "mode");
        w = json.put_string(heap, w, "lines");
        w = json.put_key(heap, w, "from");
        w = json.put_int(heap, w, seen.first);
        w = json.put_key(heap, w, "to");
        w = json.put_int(heap, w, seen.last);
        w = json.put_key(heap, w, "lines");
        borrow items as &it in {
            w = json.put_fragment(heap, w, buffer.bytes(it));
        }
        buffer.drop(heap, items);
        w = put_seen(heap, w, seen, true, cli.has(parsed, table, "count-lines"));
    } else {
        let (from, to) = range(cli.text(args, parsed, table, "bytes"), false);
        let (got, seen, failed_errno) = read_bytes(heap, file, from, to, budget, size);
        if failed_errno != 0 {
            e = fail.io_error(heap, e, failed_errno, false, shown);
        }
        w = json.put_key(heap, w, "binary");
        w = json.put_bool(heap, w, seen.binary);
        w = json.put_key(heap, w, "mode");
        w = json.put_string(heap, w, "bytes");
        w = json.put_key(heap, w, "from");
        w = json.put_int(heap, w, seen.first);
        w = json.put_key(heap, w, "to");
        w = json.put_int(heap, w, seen.last);
        w = json.put_key(heap, w, "text");
        borrow got as &g in {
            w = text.put(heap, w, buffer.bytes(g));
            plain = buffer.append(heap, plain, buffer.bytes(g));
        }
        buffer.drop(heap, got);
        w = put_seen(heap, w, seen, false, false);
    }
    w = json.end_object(heap, w);
    return (e, json.finish(w), plain);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), file_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    if cli.has(parsed, table, "lines") && cli.has(parsed, table, "bytes") {
        e = flag_problem(heap, e, "args.conflict", "--lines and --bytes exclude each other", "ask for lines or for bytes", "--lines --bytes");
    }
    if cli.has(parsed, table, "bytes") {
        let (a, b) = range(cli.text(args, parsed, table, "bytes"), false);
        if a < 0 {
            e = flag_problem(heap, e, "args.bad-value", "--bytes wants A:B or A:, 0-based, with B at least A", "--bytes 0:4096", "--bytes");
        }
    } else {
        let (a, b) = range(cli.text(args, parsed, table, "lines"), true);
        if a < 0 {
            e = flag_problem(heap, e, "args.bad-value", "--lines wants A:B or A:, 1-based, with B at least A", "--lines 1:100", "--lines");
        }
    }
    if cli.nat(args, parsed, table, "max-bytes") > ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-bytes is above the ceiling", "16777216 is the most this tool returns", "--max-bytes");
    }
    if cli.nat(args, parsed, table, "max-line-bytes") > ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-line-bytes is above the ceiling", "16777216 is the most this tool holds", "--max-line-bytes");
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) == 0 {
        e = flag_problem(heap, e, "args.missing-operand", "peek takes the PATH to read", "peek PATH", "PATH");
    } else if cli.operand_count(parsed) > 1 {
        e = flag_problem(heap, e, "args.too-many-operands", "peek takes one PATH", "read one file per call", "PATH");
    }
    var payload = buffer.empty(heap, 1);
    var plain = buffer.empty(heap, 1);
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    if !refused {
        borrow root as &rr in {
            let (target, checked) = path.operand(heap, args, buffer.bytes(rr), cli.operand_index(parsed, 0), e);
            e = checked;
            borrow target as &tp in {
                if path.ok(tp) {
                    match open_read(fs, path.full(tp)) {
                        Opened::Failed(reason) => {
                            e = fail.io_error(heap, e, reason, false, path.shown(tp));
                        }
                        Opened::Ok(opened) => {
                            var file = opened;
                            borrow mut file as &!handle in {
                                let (after_read, data, text_form) = read(heap, args, parsed, handle, path.shown(tp), e);
                                e = after_read;
                                buffer.drop(heap, payload);
                                payload = data;
                                buffer.drop(heap, plain);
                                plain = text_form;
                            }
                            file_close(file);
                        }
                    }
                }
            }
            path.drop(heap, target);
        }
    }
    buffer.drop(heap, root);
    var status = 0;
    borrow e as &er in {
        borrow payload as &d in {
            borrow plain as &t in {
                // The data stands even beside an error about over-long lines:
                // what was read is still the answer, and `ok` says not all
                // of it was.
                var data = buffer.bytes(d);
                if len(data) > 0 && int_of(data[0]) != '{' {
                    data = data[0..0];
                }
                status = out.respond(heap, io, "peek", "peek.v1", "0.1.0", data, "", er, text_mode, buffer.bytes(t), 0);
            }
        }
    }
    buffer.drop(heap, payload);
    buffer.drop(heap, plain);
    fail.drop(heap, e);
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
    // No foreign code, no network, no clock.
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
