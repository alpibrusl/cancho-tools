edition 5;

module toolbox.limit;

// `toolbox.limit` -- the one limit error three tools share: lines longer
// than `--max-line-bytes` (D8).
//
// One error per file, after the file, naming how many lines there were,
// the first, and the longest. The repair raises the cap to the longest, so
// applying it clears this error for this file (D6 rule 3: a hint raising a
// limit names the size it saw and never passes the ceiling). It replaces
// only the number when the cap was given as a whole argument
// (`--max-line-bytes N`), and adds the flag when the cap was the default;
// the flag has role `none`, so the repair takes no authority.

import std.buffer;
import std.json;
import toolbox.cli;
import toolbox.fail;
import toolbox.text;

pub struct Long {
    count: int,
    first: int,
    longest: int,
}

pub fn none() -> [] Long {
    return Long { count: 0, first: 0, longest: 0 };
}

// One more over-long line, number `n`, of `size` bytes.
pub fn more(l: Long, n: int, size: int) -> [] Long {
    var first = l.first;
    if l.count == 0 {
        first = n;
    }
    var longest = l.longest;
    if size > longest {
        longest = size;
    }
    return Long { count: l.count + 1, first: first, longest: longest };
}

pub fn too_long[&h, &g, &p, &s, &m](heap: &!h Heap, e: fail.Errors, args: &g Args, parsed: &p cli.Parsed, table: &static [byte], shown: &s [byte], l: Long, cap: int, ceiling: int, message: &m [byte]) -> [heap, args] fail.Errors {
    var w = fail.open(heap, "limit.line-too-long", message, "raise --max-line-bytes, up to the ceiling introspect names");
    let at = cli.value_index(parsed, table, "max-line-bytes");
    var n = buffer.empty(heap, 20);
    n = buffer.push_nat(heap, n, l.longest);
    if l.longest <= ceiling && at >= 0 && cli.value_is_whole(parsed, table, "max-line-bytes") {
        borrow n as &r in {
            w = fail.retry_replacing(heap, w, args, at, buffer.bytes(r));
        }
    } else if l.longest <= ceiling && at < 0 {
        var o = fail.retry_open(heap, w);
        var i = 0;
        while i < arg_count(args) {
            o = json.put_string(heap, o, arg(args, i));
            if i == 0 {
                o = json.put_string(heap, o, "--max-line-bytes");
                borrow n as &r in {
                    o = json.put_string(heap, o, buffer.bytes(r));
                }
            }
            i = i + 1;
        }
        w = fail.retry_close(heap, o);
    } else {
        w = fail.repair_none(heap, w, "the longest line is longer than the ceiling this tool can hold");
    }
    buffer.drop(heap, n);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "path");
    w = text.put(heap, w, shown);
    w = json.put_key(heap, w, "lines");
    w = json.put_int(heap, w, l.count);
    w = json.put_key(heap, w, "first_line");
    w = json.put_int(heap, w, l.first);
    w = json.put_key(heap, w, "longest");
    w = json.put_int(heap, w, l.longest);
    w = json.put_key(heap, w, "limit");
    w = json.put_int(heap, w, cap);
    return fail.add(heap, e, w);
}
