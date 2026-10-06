edition 5;

// `tally` -- count distinct lines (or one field of each line) and report
// the most frequent, in one bounded, deterministic pass.
//
//     tally [--root DIR] [--field N [--delim D]] [--top N] [--max-keys N]
//           [--max-line-bytes N] [--format json|text] [FILE...]
//
// lex-sys `docs/agent-toolbox.md` D15 row 5: `sort | uniq -c | sort -rn |
// head` is four processes whose order can depend on the locale. Here the
// order is fixed: count descending, then key bytewise ascending, so a tie
// is broken the same way on every machine. No FILE reads standard input.
//
// * `--max-keys` (default 100000, ceiling 1000000) bounds the state; one
//   more distinct key is `limit.too-many-keys`, exit 8, and the count
//   stops there -- never a trap, never a silent drop.
// * A line without the `--field` asked for is not a key; it is counted in
//   `skipped`.
// * Keys that are not UTF-8 are written as `{"b64": …}`.
//
// The seed of the hash map is a constant: there is no randomness without
// a capability, and the map's order does not depend on it anyway (it
// iterates in insertion order, lex-sys `std/map.ls`). A hostile key set
// can make it slow, not wrong.

import std.buffer;
import std.bytes;
import std.json;
import std.map;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.limit;
import toolbox.lines;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.sort;
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve every FILE relative to this directory and refuse paths outside it;field|f|nat|none||count field N (1-based) of each line instead of the whole line;delim|d|text|none|\t|the one byte that separates fields (default tab);top||nat|none|10|how many of the most frequent keys to report (ceiling 1000000);max-keys||nat|none|100000|the most distinct keys held, one more is limit.too-many-keys (ceiling 1000000);max-line-bytes||nat|none|1048576|the longest line counted, a longer one is limit.line-too-long (ceiling 16777216);format||choice:json/text|none|json|json for a program, text (count, tab, key) for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "tally", version: "0.1.0", summary: "Count distinct lines or fields and report the most frequent, ordered by count then bytewise key; one bounded pass, the same answer on every machine.", usage: "tally [--root DIR] [--field N [--delim D]] [--top N] [--max-keys N] [--max-line-bytes N] [--format json|text] [FILE...]", output: "document", schema: "tally.v1", flags: flag_table(), operands: "FILE...|path-read|0||the files to count, in order, none reads standard input", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.duplicate-flag;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.line-too-long;limit.too-many-keys", extra_rules: "", limits: "top|10|1000000;max-keys|100000|1000000;max-line-bytes|1048576|16777216", reversibility: "reversible-cheap", stdin: "when no FILE is given", guarantees: "deterministic;idempotent;bounded_memory" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn key_ceiling() -> [] int {
    return 1000000;
}

fn line_ceiling() -> [] int {
    return 16777216;
}

fn seed() -> [] int {
    return 0x5eed;
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

struct Count {
    total: int,
    skipped: int,
    full: bool,
}

// The key of a line: the whole line, or field `which` split on `delim`;
// `found` false when the line has no such field.
fn key_of[&l](line: &l [byte], which: int, delim: int) -> [] (&l [byte], bool) {
    if which <= 0 {
        return (line, true);
    }
    if which > 1 && bytes.count_byte(line, delim) < which - 1 {
        return (line[0..0], false);
    }
    return (bytes.field(line, delim, which), true);
}

// Count one key.
fn count_key[&h, &k](heap: &!h Heap, m: map.Map[int], key: &k [byte], most: int) -> [heap] (map.Map[int], bool) {
    var e = 0 - 1;
    var held = 0;
    var had = 0;
    borrow m as &r in {
        e = map.find(r, key);
        held = map.size(r);
        if e >= 0 {
            had = map.value_at(r, e);
        }
    }
    if e >= 0 {
        var counted = m;
        borrow mut counted as &!w in {
            map.set_value_at(w, e, had + 1);
        }
        return (counted, true);
    }
    if held >= most {
        return (m, false);
    }
    return (map.put(heap, m, key, 1), true);
}

// Count the lines from an open file (or, with `file` absent, standard
// input -- see `count_stdin`).
fn count_file[&h, &g, &p, &f, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, file: &!f File, shown: &s [byte], m: map.Map[int], c: Count, errs: fail.Errors) -> [heap, args, file_read] (map.Map[int], Count, fail.Errors) {
    let table = flag_table();
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let field = field_of(args, parsed);
    let delim = int_of(cli.text(args, parsed, table, "delim")[0]);
    let most = cli.nat(args, parsed, table, "max-keys");
    var counts = m;
    var t = c;
    var e = errs;
    var overlong = limit.none();
    var r = lines.start(heap, cap);
    var going = true;
    while going {
        let (stepped, status) = lines.next(heap, r);
        r = stepped;
        if status == lines.need() {
            r = lines.fill_file(r, file);
        } else if status == lines.done() {
            going = false;
        } else if status == lines.long() {
            borrow r as &rr in {
                overlong = limit.more(overlong, lines.number(rr), lines.length(rr));
            }
        } else {
            var room = true;
            var found = true;
            borrow r as &rr in {
                let (key, present) = key_of(lines.text(rr), field, delim);
                found = present;
                if present {
                    let (after, fits) = count_key(heap, counts, key, most);
                    counts = after;
                    room = fits;
                }
            }
            if !found {
                t = Count { total: t.total, skipped: t.skipped + 1, full: t.full };
            } else if room {
                t = Count { total: t.total + 1, skipped: t.skipped, full: t.full };
            } else {
                t = Count { total: t.total, skipped: t.skipped, full: true };
                going = false;
            }
        }
    }
    var errno = 0;
    borrow r as &rr in {
        errno = lines.failed(rr);
    }
    lines.drop(heap, r);
    if overlong.count > 0 {
        e = limit.too_long(heap, e, args, parsed, table, shown, overlong, cap, line_ceiling(), "lines longer than --max-line-bytes were not counted; the rest of the input was");
    }
    if errno != 0 {
        e = fail.io_error(heap, e, errno, false, shown);
    }
    return (counts, t, e);
}

// The same walk over standard input.
fn count_stdin[&h, &g, &p, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, io: &!i Io, m: map.Map[int], c: Count, errs: fail.Errors) -> [heap, args, io_read] (map.Map[int], Count, fail.Errors) {
    let table = flag_table();
    let cap = cli.nat(args, parsed, table, "max-line-bytes");
    let field = field_of(args, parsed);
    let delim = int_of(cli.text(args, parsed, table, "delim")[0]);
    let most = cli.nat(args, parsed, table, "max-keys");
    var counts = m;
    var t = c;
    var e = errs;
    var overlong = limit.none();
    var r = lines.start(heap, cap);
    var going = true;
    while going {
        let (stepped, status) = lines.next(heap, r);
        r = stepped;
        if status == lines.need() {
            r = lines.fill_stdin(heap, r, io);
        } else if status == lines.done() {
            going = false;
        } else if status == lines.long() {
            borrow r as &rr in {
                overlong = limit.more(overlong, lines.number(rr), lines.length(rr));
            }
        } else {
            var room = true;
            var found = true;
            borrow r as &rr in {
                let (key, present) = key_of(lines.text(rr), field, delim);
                found = present;
                if present {
                    let (after, fits) = count_key(heap, counts, key, most);
                    counts = after;
                    room = fits;
                }
            }
            if !found {
                t = Count { total: t.total, skipped: t.skipped + 1, full: t.full };
            } else if room {
                t = Count { total: t.total + 1, skipped: t.skipped, full: t.full };
            } else {
                t = Count { total: t.total, skipped: t.skipped, full: true };
                going = false;
            }
        }
    }
    lines.drop(heap, r);
    if overlong.count > 0 {
        e = limit.too_long(heap, e, args, parsed, table, "-", overlong, cap, line_ceiling(), "lines longer than --max-line-bytes were not counted; the rest of the input was");
    }
    return (counts, t, e);
}

fn field_of[&g, &p](args: &g Args, parsed: &p cli.Parsed) -> [args] int {
    if !cli.has(parsed, flag_table(), "field") {
        return 0;
    }
    return cli.nat(args, parsed, flag_table(), "field");
}

// Whether entry `a` goes before entry `b`: more frequent first, then the
// key bytewise.
fn before[&m](m: &m map.Map[int], a: int, b: int) -> [] bool {
    let ca = map.value_at(m, a);
    let cb = map.value_at(m, b);
    if ca != cb {
        return ca > cb;
    }
    return bytes.compare(map.key_at(m, a), map.key_at(m, b)) < 0;
}

// The answer: `{"total", "distinct", "skipped", "top": [...], "truncated"}`
// and its text form.
fn report[&h, &m](heap: &!h Heap, m: &m map.Map[int], c: Count, top: int) -> [heap] (buffer.Buffer, buffer.Buffer) {
    let n = map.size(m);
    let order = box_slice(heap, n + 1, 0);
    let spare = box_slice(heap, n + 1, 0);
    borrow mut order as &!ow in {
        borrow mut spare as &!sw in {
            let o = contents(ow);
            var k = 0;
            var e = 0;
            while e < map.entries(m) {
                if map.is_live(m, e) {
                    o[k] = e;
                    k = k + 1;
                }
                e = e + 1;
            }
            sort.by_map(m, o, contents(sw), n, before);
        }
    }
    unbox_slice(heap, spare);
    var shown = top;
    if shown > n {
        shown = n;
    }
    var w = json.writer(heap, 256 + shown * 32);
    var plain = buffer.empty(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "total");
    w = json.put_int(heap, w, c.total);
    w = json.put_key(heap, w, "distinct");
    w = json.put_int(heap, w, n);
    w = json.put_key(heap, w, "skipped");
    w = json.put_int(heap, w, c.skipped);
    w = json.put_key(heap, w, "top");
    w = json.begin_array(heap, w);
    borrow order as &or in {
        let o = contents(or);
        var i = 0;
        while i < shown {
            w = json.begin_object(heap, w);
            w = json.put_key(heap, w, "key");
            w = text.put(heap, w, map.key_at(m, o[i]));
            w = json.put_key(heap, w, "count");
            w = json.put_int(heap, w, map.value_at(m, o[i]));
            w = json.end_object(heap, w);
            plain = buffer.push_nat(heap, plain, map.value_at(m, o[i]));
            plain = buffer.push(heap, plain, byte_of(9));
            plain = buffer.append(heap, plain, map.key_at(m, o[i]));
            plain = buffer.push(heap, plain, byte_of(10));
            i = i + 1;
        }
    }
    unbox_slice(heap, order);
    w = json.end_array(heap, w);
    w = json.put_key(heap, w, "truncated");
    w = json.put_bool(heap, w, shown < n);
    w = json.end_object(heap, w);
    return (json.finish(w), plain);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    if cli.has(parsed, table, "field") && cli.nat(args, parsed, table, "field") < 1 {
        e = flag_problem(heap, e, "args.bad-value", "--field counts from 1", "--field 1 is the first field", "--field");
    }
    if len(cli.text(args, parsed, table, "delim")) != 1 {
        e = flag_problem(heap, e, "args.bad-value", "--delim is one byte", "--delim , or --delim ':'", "--delim");
    }
    if cli.nat(args, parsed, table, "top") > key_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--top is above the ceiling", "1000000 is the most this tool reports", "--top");
    }
    if cli.nat(args, parsed, table, "max-keys") > key_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-keys is above the ceiling", "1000000 is the most this tool holds", "--max-keys");
    }
    if cli.nat(args, parsed, table, "max-line-bytes") > line_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-line-bytes is above the ceiling", "16777216 is the most this tool holds", "--max-line-bytes");
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    var counts = map.empty(heap, 64, 0, seed());
    var c = Count { total: 0, skipped: 0, full: false };
    if !refused {
        if cli.operand_count(parsed) == 0 {
            let (m2, c2, e2) = count_stdin(heap, args, parsed, io, counts, c, e);
            counts = m2;
            c = c2;
            e = e2;
        }
        var k = 0;
        while k < cli.operand_count(parsed) && !c.full {
            borrow root as &rr in {
                let (target, checked) = path.operand(heap, args, buffer.bytes(rr), cli.operand_index(parsed, k), e);
                e = checked;
                borrow target as &tp in {
                    if path.ok(tp) {
                        match place.open_operand(fs, buffer.bytes(rr), path.shown(tp), path.full(tp)) {
                            Opened::Failed(reason) => {
                                e = fail.io_error(heap, e, reason, false, path.shown(tp));
                            }
                            Opened::Ok(opened) => {
                                var file = opened;
                                borrow mut file as &!handle in {
                                    let (m2, c2, e2) = count_file(heap, args, parsed, handle, path.shown(tp), counts, c, e);
                                    counts = m2;
                                    c = c2;
                                    e = e2;
                                }
                                file_close(file);
                            }
                        }
                    }
                }
                path.drop(heap, target);
            }
            k = k + 1;
        }
        if c.full {
            var w = fail.open(heap, "limit.too-many-keys", "more distinct keys than --max-keys; counting stopped there", "raise --max-keys, up to 1000000, or count a field with fewer values");
            w = fail.repair_none(heap, w, "how many distinct keys the input has is not known until it is all read");
            w = fail.detail_open(heap, w);
            w = json.put_key(heap, w, "limit");
            w = json.put_int(heap, w, cli.nat(args, parsed, table, "max-keys"));
            w = json.put_key(heap, w, "counted_lines");
            w = json.put_int(heap, w, c.total);
            e = fail.add(heap, e, w);
        }
    }
    buffer.drop(heap, root);
    var data = buffer.empty(heap, 1);
    var plain = buffer.empty(heap, 1);
    if !refused {
        borrow counts as &m in {
            buffer.drop(heap, data);
            buffer.drop(heap, plain);
            let (d, t) = report(heap, m, c, cli.nat(args, parsed, table, "top"));
            data = d;
            plain = t;
        }
    }
    map.drop(heap, counts);
    var status = 0;
    borrow e as &er in {
        borrow data as &d in {
            borrow plain as &t in {
                status = out.respond(heap, io, "tally", "tally.v1", "0.1.0", buffer.bytes(d), "", er, text_mode, buffer.bytes(t), 0);
            }
        }
    }
    buffer.drop(heap, data);
    buffer.drop(heap, plain);
    fail.drop(heap, e);
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, io_read, io_write, err_write] int {
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
