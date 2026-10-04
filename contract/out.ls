edition 5;

module toolbox.out;

// `toolbox.out` -- what a tool writes, and the check that it was written.
//
// lex-sys `docs/agent-toolbox.md` §2.1 (L1): `write_bytes` answers the
// count stdio *buffered*, and a failed flush at exit is invisible. What a
// program can see is a short count once the buffer fills, so every write
// here is checked against the length it was handed, and a short one makes
// the tool stop with `io.write-failed` (on standard error, the one place
// left to say it). The last buffer's worth can still be lost where nothing
// can look; that is why a stream ends with an `end` record and a reader
// that does not see one must treat the stream as truncated (D2).
//
// The envelope (D3) is ACLI's without `meta.duration_ms`, which would make
// two runs differ, and with `schema`, `error.rule`, `error.repair` and
// `error.detail` added. A stream is not wrapped: its `end` record is its
// envelope.

import std.buffer;
import std.io;
import std.json;
import toolbox.fail;

// Write all of `data`; false when the stream took less.
pub fn emit[&i, &d](io: &!i Io, data: &d [byte]) -> [io_write] bool {
    if len(data) == 0 {
        return true;
    }
    return write_bytes(io, data) == len(data);
}

// `data` and a newline, checked.
pub fn line[&i, &d](io: &!i Io, data: &d [byte]) -> [io_write] bool {
    let a = emit(io, data);
    let b = emit(io, "\n");
    return a && b;
}

// The last-resort sentence when standard output itself failed (D5: the
// only thing a JSON-mode tool writes to standard error besides
// `internal.*`).
pub fn write_failed[&i](io: &!i Io, tool: &static [byte]) -> [err_write] int {
    io.error_all(io, tool);
    return io.error_all(io, ": io.write-failed: standard output took fewer bytes than were written\n");
}

// The sentences of `--format text` mode's errors, on standard error.
pub fn say_errors[&i, &e](io: &!i Io, tool: &static [byte], errs: &e fail.Errors) -> [err_write] int {
    let text = fail.sentences(errs);
    var at = 0;
    while at < len(text) {
        var end = at;
        while end < len(text) && int_of(text[end]) != 10 {
            end = end + 1;
        }
        io.error_all(io, tool);
        io.error_all(io, ": ");
        io.error_all(io, text[at..end]);
        io.error_all(io, "\n");
        at = end + 1;
    }
    return fail.count(errs);
}

fn put_bool[&h](heap: &!h Heap, b: buffer.Buffer, value: bool) -> [heap] buffer.Buffer {
    if value {
        return buffer.append(heap, b, "true");
    }
    return buffer.append(heap, b, "false");
}

// A document tool's one line (D2, D3):
//
//     {"ok":…,"command":…,"schema":…[,"data":DATA][EXTRA][,"error":FIRST,"errors":[…]],"meta":{"version":…}}
//
// `data` is a finished JSON value, or empty for none; `extra` is a run of
// `,"key":value` pairs (a dry run's `dry_run` and `planned_actions`), or
// empty. `ok` is false exactly when there are errors.
pub fn document[&h, &d, &x, &e](heap: &!h Heap, command: &static [byte], schema: &static [byte], version: &static [byte], data: &d [byte], extra: &x [byte], errs: &e fail.Errors) -> [heap] buffer.Buffer {
    var b = buffer.empty(heap, 128 + len(data) + len(extra) + len(fail.listed(errs)) * 2);
    b = buffer.append(heap, b, "{\"ok\":");
    b = put_bool(heap, b, fail.count(errs) == 0);
    b = buffer.append(heap, b, ",\"command\":\"");
    b = buffer.append(heap, b, command);
    b = buffer.append(heap, b, "\",\"schema\":\"");
    b = buffer.append(heap, b, schema);
    b = buffer.append(heap, b, "\"");
    if len(data) > 0 {
        b = buffer.append(heap, b, ",\"data\":");
        b = buffer.append(heap, b, data);
    }
    b = buffer.append(heap, b, extra);
    if fail.count(errs) > 0 {
        b = buffer.append(heap, b, ",\"error\":");
        b = buffer.append(heap, b, fail.first(errs));
        b = buffer.append(heap, b, ",\"errors\":[");
        b = buffer.append(heap, b, fail.listed(errs));
        b = buffer.append(heap, b, "]");
    }
    b = buffer.append(heap, b, ",\"meta\":{\"version\":\"");
    b = buffer.append(heap, b, version);
    b = buffer.append(heap, b, "\"}}");
    return b;
}

// A stream's error record: `{"type":"error","error":{…}}`.
pub fn error_record[&h, &d](heap: &!h Heap, error: &d [byte]) -> [heap] buffer.Buffer {
    var b = buffer.empty(heap, len(error) + 32);
    b = buffer.append(heap, b, "{\"type\":\"error\",\"error\":");
    b = buffer.append(heap, b, error);
    return buffer.append(heap, b, "}");
}

// Errors `from` onwards as records, one line each; false when a write
// fell short. A stream tool calls this after anything that can add one,
// with the count it had already written.
pub fn error_records[&h, &i, &e](heap: &!h Heap, io: &!i Io, errs: &e fail.Errors, from: int) -> [heap, io_write] bool {
    var k = from;
    var ok = true;
    while k < fail.count(errs) && ok {
        let record = error_record(heap, fail.nth(errs, k));
        ok = buffer_line(heap, io, record);
        k = k + 1;
    }
    return ok;
}

// The start of a stream's `end` record:
// `{"type":"end","ok":…,"command":…,"schema":…,"complete":…` -- the caller
// adds its counts, closes the object and writes it as the last line.
// `complete` is false on any error path and after any limit stop, so a
// reader can tell a finished stream from one that stopped early.
pub fn end_open[&h](heap: &!h Heap, command: &static [byte], schema: &static [byte], ok: bool, complete: bool) -> [heap] json.Writer {
    var w = json.writer(heap, 128);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "end");
    w = json.put_key(heap, w, "ok");
    w = json.put_bool(heap, w, ok);
    w = json.put_key(heap, w, "command");
    w = json.put_string(heap, w, command);
    w = json.put_key(heap, w, "schema");
    w = json.put_string(heap, w, schema);
    w = json.put_key(heap, w, "complete");
    return json.put_bool(heap, w, complete);
}

// Close a writer's last object, write it as one line and free it.
pub fn close_line[&h, &i](heap: &!h Heap, io: &!i Io, w: json.Writer) -> [heap, io_write] bool {
    let closed = json.end_object(heap, w);
    let b = json.finish(closed);
    var ok = false;
    borrow b as &r in {
        ok = line(io, buffer.bytes(r));
    }
    buffer.drop(heap, b);
    return ok;
}

// Write a finished buffer as one line and free it.
pub fn buffer_line[&h, &i](heap: &!h Heap, io: &!i Io, b: buffer.Buffer) -> [heap, io_write] bool {
    var ok = false;
    borrow b as &r in {
        ok = line(io, buffer.bytes(r));
    }
    buffer.drop(heap, b);
    return ok;
}
