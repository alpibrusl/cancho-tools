edition 7;

import std.buffer;
import std.bytes;
import std.io;
import std.json;
import std.process;
import mcp.tools;

// The MCP server (#10, docs/mcp.md): one stdio connection, newline-delimited
// JSON-RPC 2.0, and the eight tools as MCP tools.
//
//     mcp --root DIR [--timeout-ms N] [--max-output N]
//
// What it may do is what its row says: start the binaries in the one
// directory its `Exec` is narrowed to, read its input, write its output and
// error, and keep a clock for the deadline. It holds no `Fs` and no `Net`, and
// reads no file itself; the tools read, beneath the `--root` it hands them.
//
// A call is checked against the tables `scripts/mcp.py` generates from each
// tool's `introspect` (generated/mcp/tools.ls), and becomes a fixed argv:
// the binary by name, `--root=DIR` first and once, each flag as one
// `--name=value` argument, then `--` and the operands (§1). The directory
// below is the default; `scripts/mcp.py build --bin DIR` writes another into
// a copy and builds that, because `narrow` takes a literal (§5).

// ---- the server's own command line -----------------------------------------

// A decimal of at most twelve digits, or -1.
fn nat_of[&t](text: &t [byte]) -> [] int {
    if len(text) == 0 || len(text) > 12 {
        return 0 - 1;
    }
    var n = 0;
    var i = 0;
    while i < len(text) {
        let d = bytes.digit_of(int_of(text[i]));
        if d < 0 {
            return 0 - 1;
        }
        n = n * 10 + d;
        i = i + 1;
    }
    return n;
}

pub struct Config {
    root: &static [byte],
    timeout: int,
    most: int,
}

// The configuration, or a reason it is not one. `--root` is required, an
// absolute directory, and is the only one any tool is given.
fn configure[&g](g: &g Args) -> [args] (Config, &static [byte]) {
    var root: &static [byte] = "";
    var timeout = 30000;
    var most = 16777216;
    let refused = Config { root: "", timeout: 0, most: 0 };
    var k = 1;
    while k < arg_count(g) {
        let flag = arg(g, k);
        if k + 1 >= arg_count(g) {
            return (refused, "every flag takes a value");
        }
        let value = arg(g, k + 1);
        if bytes.equal(flag, "--root") {
            if len(value) == 0 || value[0] != byte_of('/') {
                return (refused, "--root must be an absolute directory");
            }
            root = value;
        } else if bytes.equal(flag, "--timeout-ms") {
            timeout = nat_of(value);
            if timeout <= 0 {
                return (refused, "--timeout-ms must be a positive number of milliseconds");
            }
        } else if bytes.equal(flag, "--max-output") {
            most = nat_of(value);
            if most <= 0 {
                return (refused, "--max-output must be a positive number of bytes");
            }
        } else {
            return (refused, "the flags are --root, --timeout-ms and --max-output");
        }
        k = k + 2;
    }
    if len(root) == 0 {
        return (refused, "--root DIR is required");
    }
    return (Config { root: root, timeout: timeout, most: most }, "");
}

// ---- reading and answering --------------------------------------------------

// The longest request line read; a longer one is read to its end, dropped and
// answered as an invalid request.
fn longest_line() -> [] int {
    return 4194304;
}

// One line of standard input without its `\n`: `0` for a line, `1` at the end
// of input with nothing read, `2` for a line past `longest_line`.
fn read_line[&h, &i](heap: &!h Heap, io: &!i Io) -> [heap, io_read] (buffer.Buffer, int) {
    var line = buffer.empty(heap, 256);
    var n = 0;
    var over = false;
    while true {
        let c = getchar(io);
        if c < 0 {
            if n == 0 && !over {
                return (line, 1);
            }
            if over {
                return (line, 2);
            }
            return (line, 0);
        }
        if c == 10 {
            if over {
                return (line, 2);
            }
            return (line, 0);
        }
        if !over {
            if n >= longest_line() {
                over = true;
            } else {
                line = buffer.push(heap, line, byte_of(c));
                n = n + 1;
            }
        }
    }
    return (line, 0);
}

// A string node, decoded, in a box the caller ends.
fn decoded[&h, &s, &t](heap: &!h Heap, src: &s [byte], tape: &t [int], i: int) -> [heap] Box[[byte]] {
    var out = box_slice(heap, json.string_length(src, tape, i), byte_of(0));
    borrow mut out as &!o in {
        json.string_into(src, tape, i, contents(o));
    }
    return out;
}

// The request's `id` as it came: a number or a string, and `null` for none.
fn put_id[&h, &s, &t](heap: &!h Heap, w: json.Writer, src: &s [byte], tape: &t [int], id: int) -> [heap] json.Writer {
    if json.is_int(tape, id) && json.fits_int(src, tape, id) {
        return json.put_int(heap, w, json.to_int(src, tape, id));
    }
    if json.is_string(tape, id) {
        let text = decoded(heap, src, tape, id);
        var out = w;
        borrow text as &tr in {
            out = json.put_string(heap, out, contents(tr));
        }
        unbox_slice(heap, text);
        return out;
    }
    return json.put_null(heap, w);
}

// An answer's opening: the version and the request's id.
fn opened[&h, &s, &t](heap: &!h Heap, src: &s [byte], tape: &t [int], id: int) -> [heap] json.Writer {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "jsonrpc");
    w = json.put_string(heap, w, "2.0");
    w = json.put_key(heap, w, "id");
    return put_id(heap, w, src, tape, id);
}

// Write an answer as one line and flush it: a client reading a pipe sees
// nothing that is still in the buffer.
fn send[&h, &i](heap: &!h Heap, io: &!i Io, w: json.Writer) -> [heap, io_write] int {
    let out = json.finish(w);
    borrow out as &o in {
        write_bytes(io, buffer.bytes(o));
    }
    putchar(io, 10);
    var status = 0;
    match flush_out(io) {
        Done::Ok(n) => {
        }
        Done::Failed(e) => {
            status = e;
        }
    }
    buffer.drop(heap, out);
    return status;
}

fn refuse[&h, &i, &s, &t](heap: &!h Heap, io: &!i Io, src: &s [byte], tape: &t [int], id: int, code: int, message: &static [byte]) -> [heap, io_write] int {
    var w = opened(heap, src, tape, id);
    w = json.put_key(heap, w, "error");
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "code");
    w = json.put_int(heap, w, code);
    w = json.put_key(heap, w, "message");
    w = json.put_string(heap, w, message);
    w = json.end_object(heap, w);
    w = json.end_object(heap, w);
    return send(heap, io, w);
}

// An error with no request to take an id from: `id` is `null`.
fn refuse_bare[&h, &i](heap: &!h Heap, io: &!i Io, code: int, message: &static [byte]) -> [heap, io_write] int {
    let none = box_slice(heap, 3, 0);
    var status = 0;
    borrow none as &n in {
        status = refuse(heap, io, "", contents(n), 0 - 1, code, message);
    }
    unbox_slice(heap, none);
    return status;
}

// ---- one call ----------------------------------------------------------------

// Why a call's arguments were refused, as JSON-RPC's invalid params.
fn problem(code: int) -> [] &static [byte] {
    if code == 1 {
        return "arguments is not an object";
    }
    if code == 2 {
        return "an argument the tool does not take; the tool's inputSchema lists them";
    }
    if code == 3 {
        return "an argument of the wrong type; the tool's inputSchema gives each one's";
    }
    if code == 4 {
        return "an argument contains a NUL byte, which would arrive as two";
    }
    return "invalid arguments";
}

// Is `key` (a string node) one of the tool's properties?
fn known[&s, &t](src: &s [byte], tape: &t [int], tool: int, key: int) -> [] bool {
    let flags = tools.flags(tool);
    var k = 0;
    while k < bytes.count_byte(flags, ';') + 1 && len(flags) > 0 {
        if json.string_equals(src, tape, key, bytes.field(bytes.field(flags, ';', k + 1), '|', 1)) {
            return true;
        }
        k = k + 1;
    }
    let operands = tools.operands(tool);
    k = 0;
    while k < bytes.count_byte(operands, ';') + 1 && len(operands) > 0 {
        if json.string_equals(src, tape, key, bytes.field(bytes.field(operands, ';', k + 1), '|', 1)) {
            return true;
        }
        k = k + 1;
    }
    return len(tools.stdin(tool)) > 0 && json.string_equals(src, tape, key, "stdin");
}

// Add `prefix` and the string at node `i` as one entry: `0`, or `4` for a
// NUL in it.
fn add_text[&h, &s, &t](heap: &!h Heap, entries: process.Argv, prefix: &static [byte], src: &s [byte], tape: &t [int], i: int) -> [heap] (process.Argv, int) {
    let text = decoded(heap, src, tape, i);
    var entry = buffer.empty(heap, len(prefix) + 16);
    entry = buffer.append(heap, entry, prefix);
    borrow text as &tr in {
        entry = buffer.append(heap, entry, contents(tr));
    }
    unbox_slice(heap, text);
    var result = 0;
    var out = entries;
    borrow entry as &er in {
        let (added, refused) = process.add(heap, out, buffer.bytes(er));
        out = added;
        if refused != 0 {
            result = 4;
        }
    }
    buffer.drop(heap, entry);
    return (out, result);
}

// The argv for tool `tool` with the call's `arguments` node, in the tables'
// order whatever order the keys came in: `0`, or a `problem` code.
fn argv_for[&h, &s, &t](heap: &!h Heap, src: &s [byte], tape: &t [int], tool: int, given: int, root: &static [byte]) -> [heap] (process.Argv, int) {
    var entries = process.argv(heap, 256);
    if given >= 0 && !json.is_object(tape, given) {
        return (entries, 1);
    }
    // Every key is one the tool takes.
    if given >= 0 {
        var j = given + 1;
        var left = json.count(tape, given);
        while left > 0 {
            if !known(src, tape, tool, j) {
                return (entries, 2);
            }
            j = json.skip(tape, j + 1);
            left = left - 1;
        }
    }
    // The server's root, first and once.
    var with_root = buffer.empty(heap, len(root) + 8);
    with_root = buffer.append(heap, with_root, "--root=");
    with_root = buffer.append(heap, with_root, root);
    borrow with_root as &wr in {
        let (added, refused) = process.add(heap, entries, buffer.bytes(wr));
        entries = added;
    }
    buffer.drop(heap, with_root);

    let flags = tools.flags(tool);
    var k = 0;
    while len(flags) > 0 && k < bytes.count_byte(flags, ';') + 1 {
        let row = bytes.field(flags, ';', k + 1);
        let v = json.get(src, tape, given, bytes.field(row, '|', 1));
        let flag = bytes.field(row, '|', 2);
        let kind = bytes.field(row, '|', 3);
        if v >= 0 {
            if bytes.equal(kind, "bool") {
                if !json.is_bool(tape, v) {
                    return (entries, 3);
                }
                if json.to_bool(tape, v) {
                    let (added, refused) = process.add(heap, entries, flag);
                    entries = added;
                }
            } else if bytes.equal(kind, "nat") {
                if !json.is_int(tape, v) || !json.fits_int(src, tape, v) || json.to_int(src, tape, v) < 0 {
                    return (entries, 3);
                }
                var entry = buffer.empty(heap, len(flag) + 24);
                entry = buffer.append(heap, entry, flag);
                entry = buffer.push(heap, entry, byte_of('='));
                entry = buffer.push_nat(heap, entry, json.to_int(src, tape, v));
                borrow entry as &er in {
                    let (added, refused) = process.add(heap, entries, buffer.bytes(er));
                    entries = added;
                }
                buffer.drop(heap, entry);
            } else {
                if !json.is_string(tape, v) {
                    return (entries, 3);
                }
                var prefix = buffer.empty(heap, len(flag) + 1);
                prefix = buffer.append(heap, prefix, flag);
                prefix = buffer.push(heap, prefix, byte_of('='));
                let text = decoded(heap, src, tape, v);
                borrow text as &tr in {
                    prefix = buffer.append(heap, prefix, contents(tr));
                }
                unbox_slice(heap, text);
                var refused = 0;
                borrow prefix as &pr in {
                    let (added, r) = process.add(heap, entries, buffer.bytes(pr));
                    entries = added;
                    refused = r;
                }
                buffer.drop(heap, prefix);
                if refused != 0 {
                    return (entries, 4);
                }
            }
        }
        k = k + 1;
    }
    if bytes.equal(tools.stdin(tool), "flag") && json.get(src, tape, given, "stdin") >= 0 {
        let (added, refused) = process.add(heap, entries, "--stdin");
        entries = added;
    }

    // Operands after `--`: none of them can be taken for a flag.
    let (fenced, refused) = process.add(heap, entries, "--");
    entries = fenced;
    let operands = tools.operands(tool);
    k = 0;
    while len(operands) > 0 && k < bytes.count_byte(operands, ';') + 1 {
        let row = bytes.field(operands, ';', k + 1);
        let v = json.get(src, tape, given, bytes.field(row, '|', 1));
        if v >= 0 {
            if bytes.equal(bytes.field(row, '|', 3), "1") {
                if !json.is_string(tape, v) {
                    return (entries, 3);
                }
                let (added, r) = add_text(heap, entries, "", src, tape, v);
                entries = added;
                if r != 0 {
                    return (entries, r);
                }
            } else {
                if !json.is_array(tape, v) {
                    return (entries, 3);
                }
                var n = 0;
                while n < json.count(tape, v) {
                    let item = json.at(tape, v, n);
                    if !json.is_string(tape, item) {
                        return (entries, 3);
                    }
                    let (added, r) = add_text(heap, entries, "", src, tape, item);
                    entries = added;
                    if r != 0 {
                        return (entries, r);
                    }
                    n = n + 1;
                }
            }
        }
        k = k + 1;
    }
    return (entries, 0);
}

// Is `out` one JSON object, so it can be `structuredContent` as well as text?
// `put_fragment` traps on anything else, and a tool's output is not the
// server's to trust that far.
fn one_object[&h, &o](heap: &!h Heap, out: &o [byte]) -> [heap] bool {
    let tape = box_slice(heap, json.tape_len(out) + 3, 0);
    var whole = false;
    borrow mut tape as &!tw in {
        let nodes = json.parse(out, contents(tw));
        whole = nodes > 0 && json.is_object(contents(tw), 0);
    }
    unbox_slice(heap, tape);
    return whole;
}

// The result for a capture that ran: the tool's output as text, as structured
// content when it is one document, and its exit code. `isError` is any exit
// but 0 and 9 (a completed `--dry-run`, `ok: true`).
fn answered[&h, &i, &s, &t, &o](heap: &!h Heap, io: &!i Io, src: &s [byte], tape: &t [int], id: int, tool: int, out: &o [byte], code: int) -> [heap, io_write] int {
    var w = opened(heap, src, tape, id);
    w = json.put_key(heap, w, "result");
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "content");
    w = json.begin_array(heap, w);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "text");
    w = json.put_key(heap, w, "text");
    w = json.put_string(heap, w, out);
    w = json.end_object(heap, w);
    w = json.end_array(heap, w);
    if bytes.equal(tools.output(tool), "document") && one_object(heap, out) {
        // The tool ends its document with a newline, and `put_fragment`
        // splices what it is given: a newline inside a response would end
        // the line the client is reading, so the fragment stops before it.
        var end = len(out);
        while end > 0 && (out[end - 1] == byte_of(10) || out[end - 1] == byte_of(13) || out[end - 1] == byte_of(32)) {
            end = end - 1;
        }
        w = json.put_key(heap, w, "structuredContent");
        w = json.put_fragment(heap, w, out[0..end]);
    }
    w = json.put_key(heap, w, "isError");
    w = json.put_bool(heap, w, code != 0 && code != 9);
    w = json.put_key(heap, w, "_meta");
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "exit_code");
    w = json.put_int(heap, w, code);
    w = json.end_object(heap, w);
    w = json.end_object(heap, w);
    w = json.end_object(heap, w);
    return send(heap, io, w);
}

// The result when the server ended the tool, or could not start it: an error
// record in the tools' own shape, with the server's rule.
fn ended[&h, &i, &s, &t](heap: &!h Heap, io: &!i Io, src: &s [byte], tape: &t [int], id: int, rule: &static [byte], message: &static [byte], errno: int) -> [heap, io_write] int {
    var record = json.writer(heap, 256);
    record = json.begin_object(heap, record);
    record = json.put_key(heap, record, "ok");
    record = json.put_bool(heap, record, false);
    record = json.put_key(heap, record, "error");
    record = json.begin_object(heap, record);
    record = json.put_key(heap, record, "rule");
    record = json.put_string(heap, record, rule);
    record = json.put_key(heap, record, "message");
    record = json.put_string(heap, record, message);
    record = json.put_key(heap, record, "errno");
    record = json.put_int(heap, record, errno);
    record = json.end_object(heap, record);
    record = json.end_object(heap, record);
    let text = json.finish(record);

    var w = opened(heap, src, tape, id);
    w = json.put_key(heap, w, "result");
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "content");
    w = json.begin_array(heap, w);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "text");
    w = json.put_key(heap, w, "text");
    borrow text as &tx in {
        w = json.put_string(heap, w, buffer.bytes(tx));
    }
    buffer.drop(heap, text);
    w = json.end_object(heap, w);
    w = json.end_array(heap, w);
    w = json.put_key(heap, w, "isError");
    w = json.put_bool(heap, w, true);
    w = json.end_object(heap, w);
    w = json.end_object(heap, w);
    return send(heap, io, w);
}

fn call[&h, &x, &c, &i, &s, &t](heap: &!h Heap, exec: &x Exec("/opt/lexsys-tools/bin"), clock: &c Clock, io: &!i Io, src: &s [byte], tape: &t [int], id: int, config: Config) -> [heap, exec("/opt/lexsys-tools/bin"), clock, poll, io_write] int {
    let params = json.get(src, tape, 0, "params");
    let asked = json.get(src, tape, params, "name");
    var tool = 0 - 1;
    var k = 0;
    while k < tools.count() {
        if json.string_equals(src, tape, asked, tools.name(k)) {
            tool = k;
        }
        k = k + 1;
    }
    if tool < 0 {
        return refuse(heap, io, src, tape, id, 0 - 32602, "unknown tool; tools/list names the eight");
    }
    let (entries, why) = argv_for(heap, src, tape, tool, json.get(src, tape, params, "arguments"), config.root);
    if why != 0 {
        process.drop(heap, entries);
        return refuse(heap, io, src, tape, id, 0 - 32602, problem(why));
    }

    let given = json.get(src, tape, json.get(src, tape, params, "arguments"), "stdin");
    var input = box_slice(heap, 0, byte_of(0));
    if given >= 0 {
        if !json.is_string(tape, given) {
            process.drop(heap, entries);
            unbox_slice(heap, input);
            return refuse(heap, io, src, tape, id, 0 - 32602, problem(3));
        }
        unbox_slice(heap, input);
        input = decoded(heap, src, tape, given);
    }

    var status = 0;
    match process.channels() {
        process.Channels::Failed(e) => {
            status = ended(heap, io, src, tape, id, "mcp.spawn", "the channels to the tool could not be opened", e);
        }
        process.Channels::Ok(to_child, child_in, from_child, child_out) => {
            var spawned = false;
            borrow entries as &l in {
                match exec_spawn(exec, tools.path(tool), process.list(l), "", Stdio::Pipe(child_in), Stdio::Pipe(child_out), Stdio::Null) {
                    Spawned::Failed(e) => {
                        process.close(to_child, from_child);
                        status = ended(heap, io, src, tape, id, "mcp.spawn", "the tool could not be started", e);
                    }
                    Spawned::Ok(child) => {
                        borrow input as &ir in {
                            let (out, ran) = process.capture(heap, clock, child, to_child, from_child, contents(ir), config.most, config.timeout);
                            borrow out as &o in {
                                match ran {
                                    process.Ran::Code(n) => {
                                        status = answered(heap, io, src, tape, id, tool, buffer.bytes(o), n);
                                    }
                                    process.Ran::Signaled(bit) => {
                                        status = ended(heap, io, src, tape, id, "mcp.signaled", "the tool was ended by a signal", bit);
                                    }
                                    process.Ran::TimedOut => {
                                        status = ended(heap, io, src, tape, id, "mcp.timeout", "the tool ran past --timeout-ms and was killed", 0);
                                    }
                                    process.Ran::TooMuch => {
                                        status = ended(heap, io, src, tape, id, "mcp.output-too-large", "the tool wrote more than --max-output and was killed", 0);
                                    }
                                    process.Ran::Failed(e) => {
                                        status = ended(heap, io, src, tape, id, "mcp.spawn", "the tool could not be watched", e);
                                    }
                                }
                            }
                            buffer.drop(heap, out);
                        }
                    }
                }
            }
        }
    }
    unbox_slice(heap, input);
    process.drop(heap, entries);
    return status;
}

// ---- the loop ----------------------------------------------------------------

// Answer one request line. A notification (no `id`) is never answered.
fn handle[&h, &x, &c, &i, &s](heap: &!h Heap, exec: &x Exec("/opt/lexsys-tools/bin"), clock: &c Clock, io: &!i Io, src: &s [byte], config: Config) -> [heap, exec("/opt/lexsys-tools/bin"), clock, poll, io_write] int {
    var tape = box_slice(heap, json.tape_len(src) + 3, 0);
    var status = 0;
    borrow mut tape as &!tw in {
        let nodes = json.parse(src, contents(tw));
        let t = contents(tw);
        if nodes <= 0 {
            status = refuse(heap, io, src, t, 0 - 1, 0 - 32700, "the line is not one JSON value");
        } else if !json.is_object(t, 0) || !json.string_equals(src, t, json.get(src, t, 0, "jsonrpc"), "2.0") {
            status = refuse(heap, io, src, t, json.get(src, t, 0, "id"), 0 - 32600, "not a JSON-RPC 2.0 request");
        } else {
            let id = json.get(src, t, 0, "id");
            let method = json.get(src, t, 0, "method");
            if id >= 0 {
                if json.string_equals(src, t, method, "initialize") {
                    var w = opened(heap, src, t, id);
                    w = json.put_key(heap, w, "result");
                    w = json.begin_object(heap, w);
                    w = json.put_key(heap, w, "protocolVersion");
                    w = json.put_string(heap, w, "2025-06-18");
                    w = json.put_key(heap, w, "capabilities");
                    w = json.put_fragment(heap, w, "{\"tools\":{\"listChanged\":false}}");
                    w = json.put_key(heap, w, "serverInfo");
                    w = json.put_fragment(heap, w, "{\"name\":\"lexsys-tools\",\"version\":\"0.1.0\"}");
                    w = json.end_object(heap, w);
                    w = json.end_object(heap, w);
                    status = send(heap, io, w);
                } else if json.string_equals(src, t, method, "ping") {
                    var w = opened(heap, src, t, id);
                    w = json.put_key(heap, w, "result");
                    w = json.put_fragment(heap, w, "{}");
                    w = json.end_object(heap, w);
                    status = send(heap, io, w);
                } else if json.string_equals(src, t, method, "tools/list") {
                    var w = opened(heap, src, t, id);
                    w = json.put_key(heap, w, "result");
                    w = json.put_fragment(heap, w, tools.list_result());
                    w = json.end_object(heap, w);
                    status = send(heap, io, w);
                } else if json.string_equals(src, t, method, "tools/call") {
                    status = call(heap, exec, clock, io, src, t, id, config);
                } else {
                    status = refuse(heap, io, src, t, id, 0 - 32601, "the methods are initialize, ping, tools/list and tools/call");
                }
            }
        }
    }
    unbox_slice(heap, tape);
    return status;
}

fn serve[&h, &x, &c, &i](heap: &!h Heap, exec: &x Exec("/opt/lexsys-tools/bin"), clock: &c Clock, io: &!i Io, config: Config) -> [heap, exec("/opt/lexsys-tools/bin"), clock, poll, io_read, io_write] int {
    while true {
        let (line, read) = read_line(heap, io);
        if read == 1 {
            buffer.drop(heap, line);
            return 0;
        }
        var status = 0;
        if read == 2 {
            status = refuse_bare(heap, io, 0 - 32600, "the request line is longer than 4 MiB");
        } else {
            borrow line as &l in {
                status = handle(heap, exec, clock, io, buffer.bytes(l), config);
            }
        }
        buffer.drop(heap, line);
        if status != 0 {
            // The client is gone or its pipe failed; there is no one to answer.
            return 0;
        }
    }
    return 0;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock, signals, exec } = split(world);
    release(ffi);
    release(fs);
    release(net);
    release(signals);
    let bin = narrow(exec, "/opt/lexsys-tools/bin");
    var console = io;
    var h = heap;
    var status = 0;
    borrow args as &g in {
        let (config, why) = configure(g);
        borrow mut console as &!i in {
            if len(why) > 0 {
                io.error_all(i, "mcp: ");
                io.error_all(i, why);
                io.error_all(i, "\n");
                status = 2;
            } else {
                borrow bin as &x in {
                    borrow clock as &c in {
                        borrow mut h as &!hh in {
                            status = serve(hh, x, c, i, config);
                        }
                    }
                }
            }
        }
    }
    release(bin);
    release(clock);
    release(console);
    release(h);
    release(args);
    return status;
}
