edition 5;

module toolbox.text;

// `toolbox.text` -- bytes in JSON without loss (D2, L10).
//
// `std.json`'s writer replaces bytes that are not UTF-8 with U+FFFD and
// says nothing (lex-sys `docs/agent-toolbox.md` A.13). A field that can
// hold arbitrary bytes -- a matched line, a file name -- is written here
// instead: as a JSON string when the bytes are valid UTF-8, and as
// `{"b64":"…"}` (RFC 4648 §4, padded) when they are not. The schema calls
// that `text_or_bytes`. The encoder starts from lex-sys
// `examples/base64/base64.ls` and moves to `std` at a second asker.

import std.buffer;
import std.json;
import std.utf8;

fn alphabet() -> [] &static [byte] {
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
}

// `data` as base64, appended to `out`.
pub fn base64[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte]) -> [heap] buffer.Buffer {
    let alpha = alphabet();
    var o = buffer.reserve(heap, out, (len(data) + 2) / 3 * 4);
    var i = 0;
    while i + 3 <= len(data) {
        let n = int_of(data[i]) << 16 | int_of(data[i + 1]) << 8 | int_of(data[i + 2]);
        o = buffer.push(heap, o, alpha[n >> 18 & 63]);
        o = buffer.push(heap, o, alpha[n >> 12 & 63]);
        o = buffer.push(heap, o, alpha[n >> 6 & 63]);
        o = buffer.push(heap, o, alpha[n & 63]);
        i = i + 3;
    }
    let rest = len(data) - i;
    if rest == 1 {
        let n = int_of(data[i]) << 16;
        o = buffer.push(heap, o, alpha[n >> 18 & 63]);
        o = buffer.push(heap, o, alpha[n >> 12 & 63]);
        o = buffer.append(heap, o, "==");
    } else if rest == 2 {
        let n = int_of(data[i]) << 16 | int_of(data[i + 1]) << 8;
        o = buffer.push(heap, o, alpha[n >> 18 & 63]);
        o = buffer.push(heap, o, alpha[n >> 12 & 63]);
        o = buffer.push(heap, o, alpha[n >> 6 & 63]);
        o = buffer.push(heap, o, byte_of('='));
    }
    return o;
}

// A `text_or_bytes` value.
pub fn put[&h, &d](heap: &!h Heap, w: json.Writer, data: &d [byte]) -> [heap] json.Writer {
    if utf8.is_valid(data) {
        return json.put_string(heap, w, data);
    }
    var o = json.begin_object(heap, w);
    o = json.put_key(heap, o, "b64");
    var encoded = buffer.empty(heap, (len(data) + 2) / 3 * 4 + 1);
    encoded = base64(heap, encoded, data);
    borrow encoded as &e in {
        o = json.put_string(heap, o, buffer.bytes(e));
    }
    buffer.drop(heap, encoded);
    return json.end_object(heap, o);
}

// Whether `data` holds a NUL byte: the binary test `grep` uses, and the
// one the tools report as `binary`.
pub fn has_nul[&d](data: &d [byte]) -> [] bool {
    var i = 0;
    while i < len(data) {
        if int_of(data[i]) == 0 {
            return true;
        }
        i = i + 1;
    }
    return false;
}
