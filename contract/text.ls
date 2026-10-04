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

// Whether every byte is below 128: ASCII, so valid UTF-8 without decoding
// anything. Most lines a tool writes are, and `utf8.is_valid` decodes every
// byte (it was 16% of `seek`'s time on a match-heavy search).
pub fn is_ascii[&d](data: &d [byte]) -> [] bool {
    var i = 0;
    while i < len(data) {
        if int_of(data[i]) >= 128 {
            return false;
        }
        i = i + 1;
    }
    return true;
}

pub fn is_utf8[&d](data: &d [byte]) -> [] bool {
    return is_ascii(data) || utf8.is_valid(data);
}

// A `text_or_bytes` value.
pub fn put[&h, &d](heap: &!h Heap, w: json.Writer, data: &d [byte]) -> [heap] json.Writer {
    if is_utf8(data) {
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

fn hex_digit(v: int) -> [] int {
    let digits = "0123456789abcdef";
    return int_of(digits[v]);
}

// Append `data` to `out` as a JSON `text_or_bytes` value, without a
// `json.Writer`: for a record a tool writes millions of times, where a
// writer per record and a second pass over the bytes were most of the cost.
// The escaping is `std.json`'s (`"`, `\\`, the named controls, `\u00XX` for
// the rest below 32), and bytes of 128 and above are copied as they are,
// which is valid because the text was checked to be UTF-8 first.
pub fn append_json[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte]) -> [heap] buffer.Buffer {
    // The common case, in one pass: printable ASCII with nothing to escape.
    var plain = 0;
    var clean = true;
    while clean && plain < len(data) {
        let c = int_of(data[plain]);
        if c < 32 || c >= 128 || c == '"' || c == '\\' {
            clean = false;
        } else {
            plain = plain + 1;
        }
    }
    if plain == len(data) {
        var q = buffer.push(heap, out, byte_of('"'));
        q = append_bytes(heap, q, data);
        return buffer.push(heap, q, byte_of('"'));
    }
    if !is_utf8(data) {
        var o = buffer.append(heap, out, "{\"b64\":\"");
        o = base64(heap, o, data);
        return buffer.append(heap, o, "\"}");
    }
    var o = buffer.reserve(heap, out, len(data) + 2);
    o = buffer.push(heap, o, byte_of('"'));
    var run = 0;
    var p = 0;
    while p < len(data) {
        let c = int_of(data[p]);
        if c >= 32 && c != '"' && c != '\\' {
            p = p + 1;
        } else {
            o = append_bytes(heap, o, data[run..p]);
            o = buffer.push(heap, o, byte_of('\\'));
            if c == '"' || c == '\\' {
                o = buffer.push(heap, o, byte_of(c));
            } else if c == 10 {
                o = buffer.push(heap, o, byte_of('n'));
            } else if c == 13 {
                o = buffer.push(heap, o, byte_of('r'));
            } else if c == 9 {
                o = buffer.push(heap, o, byte_of('t'));
            } else if c == 8 {
                o = buffer.push(heap, o, byte_of('b'));
            } else if c == 12 {
                o = buffer.push(heap, o, byte_of('f'));
            } else {
                o = buffer.append(heap, o, "u00");
                o = buffer.push(heap, o, byte_of(hex_digit(c >> 4)));
                o = buffer.push(heap, o, byte_of(hex_digit(c & 15)));
            }
            p = p + 1;
            run = p;
        }
    }
    o = append_bytes(heap, o, data[run..p]);
    return buffer.push(heap, o, byte_of('"'));
}

// Append a non-negative integer in decimal, written into reserved room
// rather than one checked push per digit.
pub fn append_nat[&h](heap: &!h Heap, out: buffer.Buffer, n: int) -> [heap] buffer.Buffer {
    var digits = 1;
    var rest = n / 10;
    while rest > 0 {
        digits = digits + 1;
        rest = rest / 10;
    }
    var o = buffer.reserve(heap, out, digits);
    borrow mut o as &!w in {
        let room = buffer.room(w);
        var v = n;
        var i = digits - 1;
        while i >= 0 {
            room[i] = byte_of('0' + v % 10);
            v = v / 10;
            i = i - 1;
        }
        buffer.filled(w, digits);
    }
    return o;
}

// Append `data` to `out`: `std.buffer.append`'s job, with the copy written
// into a slice exactly as long as `data`, so that the bound of every store
// is the bound of the loop and the compiler can drop the per-byte checks.
// (`buffer.append` indexes the whole allocation at `used + i`, which it
// cannot prove in range; it was a quarter of a match-heavy `seek`.)
pub fn append_bytes[&h, &d](heap: &!h Heap, out: buffer.Buffer, data: &d [byte]) -> [heap] buffer.Buffer {
    let n = len(data);
    var o = buffer.reserve(heap, out, n);
    borrow mut o as &!w in {
        let dst = buffer.room(w)[0..n];
        var i = 0;
        while i < n {
            dst[i] = data[i];
            i = i + 1;
        }
        buffer.filled(w, n);
    }
    return o;
}
