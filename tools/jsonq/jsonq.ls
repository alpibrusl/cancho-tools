edition 5;

// `jsonq` -- one JSON Pointer (RFC 6901) into one JSON document, and
// nothing more.
//
//     jsonq [--root DIR] [--pointer /a/0/b] [--keys | --length | --type | --exists]
//           [--max-bytes N] [--format json|text] [FILE | -]
//
// lex-sys `docs/agent-toolbox.md` D15 row 4: strict RFC 8259 with UTF-8
// validation (`std.json`), the parse error's *position* as data, and an
// authority of a read and a heap. It is a strict subset of `jq`, and it
// refuses to grow past it: a filter, a pipe or a function is `jq`'s job.
//
// * The value is written as the document wrote it -- numbers and strings
//   byte for byte from the source, containers compacted -- so nothing is
//   lost in a round trip, a float included.
// * A pointer that names nothing is `query.no-such-path` (exit 3) with the
//   longest prefix that did resolve; `--exists` answers `false` instead.
// * The document is held whole and parsed into a tape of 24 bytes per byte
//   of source (L13), so `--max-bytes` (default 4 MiB, ceiling 32 MiB)
//   bounds the memory at about 25 times itself.

import std.buffer;
import std.bytes;
import std.json;
import toolbox.atomic;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve FILE relative to this directory and refuse paths outside it;pointer|p|any|none||the JSON Pointer (RFC 6901) to look up, empty is the whole document;keys||bool|none||answer the keys of the object at the pointer, in document order;length||bool|none||answer the number of elements, pairs, or bytes of a string at the pointer;type||bool|none||answer only the kind of the value at the pointer;exists||bool|none||answer whether the pointer names anything, true or false, never an error;max-bytes||nat|none|4194304|the largest document accepted (ceiling 33554432), memory is about 25 times this;format||choice:json/text|none|json|json for a program, text for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "jsonq", version: "0.1.0", summary: "Look up one JSON Pointer (RFC 6901) in one strictly parsed JSON document; the value byte for byte, its kind, keys or length, and a parse error's position as data.", usage: "jsonq [--root DIR] [--pointer /a/0/b] [--keys | --length | --type | --exists] [--max-bytes N] [--format json|text] [FILE | -]", output: "document", schema: "jsonq.v1", flags: flag_table(), operands: "FILE|path-read|the document, - or nothing reads standard input", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.conflict;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;limit.input-too-large;parse.json;query.bad-pointer;query.no-such-path;query.wrong-kind;query.unsupported-syntax", limits: "max-bytes|4194304|33554432", reversibility: "reversible-cheap", stdin: "when FILE is - or absent" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn ceiling() -> [] int {
    return 33554432;
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

fn kind_word(k: int) -> [] &static [byte] {
    if k == 0 {
        return "null";
    }
    if k == 1 || k == 2 {
        return "boolean";
    }
    if k == 3 {
        return "integer";
    }
    if k == 4 {
        return "float";
    }
    if k == 5 {
        return "string";
    }
    if k == 6 {
        return "array";
    }
    return "object";
}

// ---- writing a value as the document wrote it ----------------------------

fn raw[&h, &s, &t](heap: &!h Heap, out: buffer.Buffer, src: &s [byte], tape: &t [int], i: int) -> [heap] buffer.Buffer {
    let k = json.kind(tape, i);
    var o = out;
    if k == 0 {
        return buffer.append(heap, o, "null");
    }
    if k == 1 {
        return buffer.append(heap, o, "false");
    }
    if k == 2 {
        return buffer.append(heap, o, "true");
    }
    if k == 3 || k == 4 {
        return buffer.append(heap, o, src[tape[3 * i + 1]..tape[3 * i + 2]]);
    }
    if k == 5 {
        o = buffer.push(heap, o, byte_of('"'));
        o = buffer.append(heap, o, src[tape[3 * i + 1]..tape[3 * i + 2]]);
        return buffer.push(heap, o, byte_of('"'));
    }
    let n = json.count(tape, i);
    var j = i + 1;
    var m = 0;
    if k == 6 {
        o = buffer.push(heap, o, byte_of('['));
        while m < n {
            if m > 0 {
                o = buffer.push(heap, o, byte_of(','));
            }
            o = raw(heap, o, src, tape, j);
            j = json.skip(tape, j);
            m = m + 1;
        }
        return buffer.push(heap, o, byte_of(']'));
    }
    o = buffer.push(heap, o, byte_of('{'));
    while m < n {
        if m > 0 {
            o = buffer.push(heap, o, byte_of(','));
        }
        o = raw(heap, o, src, tape, j);
        o = buffer.push(heap, o, byte_of(':'));
        o = raw(heap, o, src, tape, j + 1);
        j = json.skip(tape, j + 1);
        m = m + 1;
    }
    return buffer.push(heap, o, byte_of('}'));
}

// ---- the pointer ---------------------------------------------------------

// Whether `p` is RFC 6901 syntax: empty, or `/`-led with every `~`
// followed by `0` or `1`.
fn pointer_ok[&p](p: &p [byte]) -> [] bool {
    if len(p) == 0 {
        return true;
    }
    if int_of(p[0]) != '/' {
        return false;
    }
    var i = 0;
    while i < len(p) {
        if int_of(p[i]) == '~' {
            if i + 1 >= len(p) {
                return false;
            }
            let c = int_of(p[i + 1]);
            if c != '0' && c != '1' {
                return false;
            }
        }
        i = i + 1;
    }
    return true;
}

// A segment unescaped: `~1` is `/`, `~0` is `~`.
fn unescape[&h, &s](heap: &!h Heap, segment: &s [byte]) -> [heap] buffer.Buffer {
    var out = buffer.empty(heap, len(segment) + 1);
    var i = 0;
    while i < len(segment) {
        let c = int_of(segment[i]);
        if c == '~' && i + 1 < len(segment) {
            if int_of(segment[i + 1]) == '1' {
                out = buffer.push(heap, out, byte_of('/'));
            } else {
                out = buffer.push(heap, out, byte_of('~'));
            }
            i = i + 2;
        } else {
            out = buffer.push(heap, out, segment[i]);
            i = i + 1;
        }
    }
    return out;
}

// An array index segment: `0`, or digits with no leading zero, at most 18;
// -1 otherwise (including `-`, which names the element after the last).
fn index_of[&s](segment: &s [byte]) -> [] int {
    if len(segment) == 0 || len(segment) > 1 && int_of(segment[0]) == '0' {
        return 0 - 1;
    }
    return cli.parse_nat(segment);
}

// Resolve `pointer` from the root: the node, or -1 with the length of the
// prefix of `pointer` that did resolve.
fn resolve[&h, &s, &t, &p](heap: &!h Heap, src: &s [byte], tape: &t [int], pointer: &p [byte]) -> [heap] (int, int) {
    var node = 0;
    var cursor = 0;
    while cursor < len(pointer) {
        // `pointer[cursor]` is a `/`.
        var end = cursor + 1;
        while end < len(pointer) && int_of(pointer[end]) != '/' {
            end = end + 1;
        }
        let segment = pointer[cursor + 1..end];
        let k = json.kind(tape, node);
        var next = 0 - 1;
        if k == 7 {
            let key = unescape(heap, segment);
            borrow key as &kk in {
                next = json.get(src, tape, node, buffer.bytes(kk));
            }
            buffer.drop(heap, key);
        } else if k == 6 {
            let n = index_of(segment);
            if n >= 0 {
                next = json.at(tape, node, n);
            }
        }
        if next < 0 {
            return (0 - 1, cursor);
        }
        node = next;
        cursor = end;
    }
    return (node, len(pointer));
}

// Line and column (1-based, in bytes) of `offset`.
fn position[&s](src: &s [byte], offset: int) -> [] (int, int) {
    var line = 1;
    var column = 1;
    var i = 0;
    while i < offset && i < len(src) {
        if int_of(src[i]) == 10 {
            line = line + 1;
            column = 1;
        } else {
            column = column + 1;
        }
        i = i + 1;
    }
    return (line, column);
}

// A JSON string holding `data` (or `{"b64":…}`), as a buffer.
fn quoted[&h, &d](heap: &!h Heap, data: &d [byte]) -> [heap] buffer.Buffer {
    var w = json.writer(heap, len(data) + 8);
    w = text.put(heap, w, data);
    return json.finish(w);
}

fn wrong_kind[&h, &p](heap: &!h Heap, e: fail.Errors, pointer: &p [byte], found: &static [byte], wanted: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, "query.wrong-kind", "the value at the pointer is not of a kind this query applies to", "");
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "pointer");
    w = text.put(heap, w, pointer);
    w = json.put_key(heap, w, "kind");
    w = json.put_string(heap, w, found);
    w = json.put_key(heap, w, "wanted");
    w = json.put_string(heap, w, wanted);
    return fail.add(heap, e, w);
}

// Answer the query against a parsed document: the data object and the text
// form.
fn query[&h, &g, &p, &s, &t](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, src: &s [byte], tape: &t [int], errs: fail.Errors) -> [heap, args] (fail.Errors, buffer.Buffer, buffer.Buffer) {
    let table = flag_table();
    let pointer = cli.text(args, parsed, table, "pointer");
    var e = errs;
    var data = buffer.empty(heap, 256);
    var plain = buffer.empty(heap, 64);
    let (node, resolved) = resolve(heap, src, tape, pointer);
    let q = quoted(heap, pointer);
    borrow q as &qq in {
        data = buffer.append(heap, data, "{\"pointer\":");
        data = buffer.append(heap, data, buffer.bytes(qq));
    }
    buffer.drop(heap, q);
    if cli.has(parsed, table, "exists") {
        data = buffer.append(heap, data, ",\"exists\":");
        if node >= 0 {
            data = buffer.append(heap, data, "true}");
            plain = buffer.append(heap, plain, "true\n");
        } else {
            data = buffer.append(heap, data, "false}");
            plain = buffer.append(heap, plain, "false\n");
        }
        return (e, data, plain);
    }
    if node < 0 {
        var w = fail.open(heap, "query.no-such-path", "the pointer names nothing in the document", "query a shorter pointer with --keys or --length to see what is there");
        w = fail.no_repair(heap, w);
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "pointer");
        w = text.put(heap, w, pointer);
        w = json.put_key(heap, w, "resolved");
        w = text.put(heap, w, pointer[0..resolved]);
        w = json.put_key(heap, w, "kind_at_resolved");
        let (last, unused) = resolve(heap, src, tape, pointer[0..resolved]);
        w = json.put_string(heap, w, kind_word(json.kind(tape, last)));
        e = fail.add(heap, e, w);
        buffer.drop(heap, data);
        return (e, buffer.empty(heap, 1), plain);
    }
    let k = json.kind(tape, node);
    data = buffer.append(heap, data, ",\"kind\":\"");
    data = buffer.append(heap, data, kind_word(k));
    data = buffer.append(heap, data, "\"");
    if cli.has(parsed, table, "type") {
        plain = buffer.append(heap, plain, kind_word(k));
        plain = buffer.append(heap, plain, "\n");
    } else if cli.has(parsed, table, "keys") {
        if k != 7 {
            e = wrong_kind(heap, e, pointer, kind_word(k), "object");
        } else {
            data = buffer.append(heap, data, ",\"keys\":[");
            var j = node + 1;
            var m = 0;
            while m < json.count(tape, node) {
                if m > 0 {
                    data = buffer.push(heap, data, byte_of(','));
                }
                data = raw(heap, data, src, tape, j);
                plain = raw(heap, plain, src, tape, j);
                plain = buffer.append(heap, plain, "\n");
                j = json.skip(tape, j + 1);
                m = m + 1;
            }
            data = buffer.append(heap, data, "]");
        }
    } else if cli.has(parsed, table, "length") {
        var n = 0 - 1;
        if k == 6 || k == 7 {
            n = json.count(tape, node);
        } else if k == 5 {
            n = json.string_length(src, tape, node);
        }
        if n < 0 {
            e = wrong_kind(heap, e, pointer, kind_word(k), "array, object or string");
        } else {
            data = buffer.append(heap, data, ",\"length\":");
            data = buffer.push_nat(heap, data, n);
            plain = buffer.push_nat(heap, plain, n);
            plain = buffer.append(heap, plain, "\n");
        }
    } else {
        data = buffer.append(heap, data, ",\"value\":");
        data = raw(heap, data, src, tape, node);
        plain = raw(heap, plain, src, tape, node);
        plain = buffer.append(heap, plain, "\n");
    }
    data = buffer.append(heap, data, "}");
    var failed_now = false;
    borrow e as &er in {
        failed_now = fail.count(er) > 0;
    }
    if failed_now {
        buffer.drop(heap, data);
        return (e, buffer.empty(heap, 1), plain);
    }
    return (e, data, plain);
}

// Parse and query a document held in `src`.
fn answer[&h, &g, &p, &s, &n](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, src: &s [byte], shown: &n [byte], errs: fail.Errors) -> [heap, args] (fail.Errors, buffer.Buffer, buffer.Buffer) {
    var e = errs;
    let tape = box_slice(heap, json.tape_len(src), 0);
    var nodes = 0;
    borrow mut tape as &!tw in {
        nodes = json.parse(src, contents(tw));
    }
    var data = buffer.empty(heap, 1);
    var plain = buffer.empty(heap, 1);
    if nodes < 0 {
        let offset = json.error_position(nodes);
        let (line, column) = position(src, offset);
        var w = fail.open(heap, "parse.json", "the input is not one valid JSON document", "");
        w = fail.no_repair(heap, w);
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "offset");
        w = json.put_int(heap, w, offset);
        w = json.put_key(heap, w, "line");
        w = json.put_int(heap, w, line);
        w = json.put_key(heap, w, "column");
        w = json.put_int(heap, w, column);
        w = json.put_key(heap, w, "reason");
        w = json.put_string(heap, w, json.error_message(json.error_code(nodes)));
        e = fail.add(heap, e, w);
    } else {
        borrow tape as &tr in {
            buffer.drop(heap, data);
            buffer.drop(heap, plain);
            let (queried, d, t) = query(heap, args, parsed, src, contents(tr), e);
            e = queried;
            data = d;
            plain = t;
        }
    }
    unbox_slice(heap, tape);
    return (e, data, plain);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    var modes = 0;
    if cli.has(parsed, table, "keys") {
        modes = modes + 1;
    }
    if cli.has(parsed, table, "length") {
        modes = modes + 1;
    }
    if cli.has(parsed, table, "type") {
        modes = modes + 1;
    }
    if cli.has(parsed, table, "exists") {
        modes = modes + 1;
    }
    if modes > 1 {
        e = flag_problem(heap, e, "args.conflict", "--keys, --length, --type and --exists exclude each other", "ask one question per call", "--keys --length --type --exists");
    }
    let given = cli.text(args, parsed, table, "pointer");
    if len(given) > 0 && (int_of(given[0]) == '.' || bytes.find(given, "|") >= 0) {
        // A jq filter, not a pointer: refused with a tag rather than guessed
        // at, and the repair names the tool that answers it (D15: jsonq
        // refuses to grow into jq).
        var w = fail.open(heap, "query.unsupported-syntax", "this looks like a jq filter; jsonq takes one JSON Pointer and nothing more", "write .a[0].b as the pointer /a/0/b, or use jq for filters");
        w = fail.repair_none(heap, w, "a filter is jq's job; jsonq will not grow to answer it");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "pointer");
        w = text.put(heap, w, given);
        e = fail.add(heap, e, w);
    } else if !pointer_ok(given) {
        var w = fail.open(heap, "query.bad-pointer", "the pointer is not RFC 6901 syntax: empty, or starting with / and with every ~ followed by 0 or 1", "write /a/0/b; ~1 is a / inside a key and ~0 is a ~");
        w = fail.no_repair(heap, w);
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "pointer");
        w = text.put(heap, w, cli.text(args, parsed, table, "pointer"));
        e = fail.add(heap, e, w);
    }
    let most = cli.nat(args, parsed, table, "max-bytes");
    if most > ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-bytes is above the ceiling", "33554432 is the most this tool parses", "--max-bytes");
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) > 1 {
        e = flag_problem(heap, e, "args.too-many-operands", "jsonq reads one document", "query one file per call", "FILE");
    }
    var data = buffer.empty(heap, 1);
    var plain = buffer.empty(heap, 1);
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    if !refused {
        let from_stdin = cli.operand_count(parsed) == 0 || bytes.equal(cli.operand(args, parsed, 0), "-");
        var src = buffer.empty(heap, 4096);
        var named = buffer.empty(heap, 8);
        var readable = true;
        if from_stdin {
            let (read, more) = atomic.read_stdin(heap, io, most, src);
            src = read;
            named = buffer.append(heap, named, "-");
            if more {
                e = fail.simple(heap, e, "limit.input-too-large", "the document on standard input is larger than --max-bytes", "raise --max-bytes, up to 33554432", "path", "-");
                readable = false;
            }
        } else {
            borrow root as &rr in {
                let (target, checked) = path.operand(heap, args, buffer.bytes(rr), cli.operand_index(parsed, 0), e);
                e = checked;
                borrow target as &tp in {
                    named = buffer.append(heap, named, path.shown(tp));
                    if path.ok(tp) {
                        let (read, errno, too_big) = atomic.read_all(heap, place.open_operand(fs, buffer.bytes(rr), path.shown(tp), path.full(tp)), most, src);
                        src = read;
                        if errno != 0 {
                            e = fail.io_error(heap, e, errno, false, path.shown(tp));
                            readable = false;
                        } else if too_big {
                            e = fail.simple(heap, e, "limit.input-too-large", "the document is larger than --max-bytes", "raise --max-bytes, up to 33554432", "path", path.shown(tp));
                            readable = false;
                        }
                    } else {
                        readable = false;
                    }
                }
                path.drop(heap, target);
            }
        }
        if readable {
            borrow src as &s in {
                borrow named as &n in {
                    buffer.drop(heap, data);
                    buffer.drop(heap, plain);
                    let (answered, d, t) = answer(heap, args, parsed, buffer.bytes(s), buffer.bytes(n), e);
                    e = answered;
                    data = d;
                    plain = t;
                }
            }
        }
        buffer.drop(heap, src);
        buffer.drop(heap, named);
    }
    buffer.drop(heap, root);
    var status = 0;
    borrow e as &er in {
        borrow data as &d in {
            borrow plain as &t in {
                status = out.respond(heap, io, "jsonq", "jsonq.v1", "0.1.0", buffer.bytes(d), "", er, text_mode, buffer.bytes(t), 0);
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
