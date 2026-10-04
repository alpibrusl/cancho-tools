edition 5;

module toolbox.diff;

// `toolbox.diff` -- what a write or a replace changes, as lines (issue #8).
//
// A hash says *that* the content changes; an agent deciding whether to go
// ahead with a `--dry-run` wants *what*. This is the one hunk between the
// common leading lines and the common trailing lines of the content before
// and after:
//
//     [{"old_start": 3, "new_start": 3,
//       "removed_count": 1, "added_count": 2,
//       "removed": ["b\n"], "added": ["B\n", "B2\n"]}]
//
// One hunk, not a minimal edit script: an edit an agent makes is usually
// one place, and one hunk is linear time and memory with no table. Two
// edits far apart come out as one hunk spanning both, which is correct and
// larger than it needs to be. `[]` when nothing changes.
//
// A line is its bytes up to and including its `\n`; the last may lack one,
// and that is visible (`"x"` against `"x\n"`). `old_start` is the 1-based
// line of the first removed line, and of the first added one in the new
// content: the same number, since the lines before it are common. Applying
// the hunk to the old lines -- keep `old_start - 1`, then `added`, then
// what follows the `removed_count` removed -- gives the new content exactly,
// which `tests/conformance` checks.
//
// Bounded: the counts are always whole, but the lines themselves stop at
// `most` lines or `budget()` bytes, whichever comes first, and the answer
// says so (`diff_truncated`). Lines go out as `text_or_bytes`
// (`toolbox.text`), so content that is not UTF-8 is not lost.

import std.buffer;
import std.json;
import toolbox.text;

// The most bytes of lines one diff carries, whatever `most` says: a file
// that is one 64 MiB line would otherwise be 64 MiB of answer.
pub fn budget() -> [] int {
    return 1048576;
}

// Where the common leading lines end: an offset that starts a line in
// both, at or before the first byte that differs.
fn prefix[&a, &b](before: &a [byte], after: &b [byte]) -> [] int {
    var i = 0;
    while i < len(before) && i < len(after) && before[i] == after[i] {
        i = i + 1;
    }
    if i == len(before) && i == len(after) {
        return i;
    }
    while i > 0 && int_of(before[i - 1]) != '\n' {
        i = i - 1;
    }
    return i;
}

// Whether `at` starts a line, given that the common prefix ends at `p`.
fn starts_line[&d](data: &d [byte], at: int, p: int) -> [] bool {
    return at == p || int_of(data[at - 1]) == '\n';
}

// How many trailing bytes are common lines: no further back than `p` in
// either, and starting a line in both.
fn suffix[&a, &b](before: &a [byte], after: &b [byte], p: int) -> [] int {
    var room = len(before) - p;
    if len(after) - p < room {
        room = len(after) - p;
    }
    var j = 0;
    while j < room && before[len(before) - 1 - j] == after[len(after) - 1 - j] {
        j = j + 1;
    }
    while j > 0 && !(starts_line(before, len(before) - j, p) && starts_line(after, len(after) - j, p)) {
        j = j - 1;
    }
    return j;
}

// The number of lines in `data`: its `\n`s, and one more for a last line
// without one.
fn line_count[&d](data: &d [byte]) -> [] int {
    var n = 0;
    var i = 0;
    while i < len(data) {
        if int_of(data[i]) == '\n' {
            n = n + 1;
        }
        i = i + 1;
    }
    if len(data) > 0 && int_of(data[len(data) - 1]) != '\n' {
        n = n + 1;
    }
    return n;
}

// The lines of `data` as an array, while `room` lines and `bytes` bytes
// last. Answers the writer, the lines and bytes left, and whether any line
// was left out.
fn put_lines[&h, &d](heap: &!h Heap, w: json.Writer, data: &d [byte], room: int, bytes: int) -> [heap] (json.Writer, int, int, bool) {
    var o = json.begin_array(heap, w);
    var lines = room;
    var left = bytes;
    var cut = false;
    var start = 0;
    while start < len(data) && !cut {
        var end = start;
        while end < len(data) && int_of(data[end]) != '\n' {
            end = end + 1;
        }
        if end < len(data) {
            end = end + 1;
        }
        if lines == 0 || end - start > left {
            cut = true;
        } else {
            o = text.put(heap, o, data[start..end]);
            lines = lines - 1;
            left = left - (end - start);
            start = end;
        }
    }
    o = json.end_array(heap, o);
    return (o, lines, left, cut);
}

// The diff from `before` to `after`, as a JSON array, carrying at most
// `most` lines. Answers the array and whether lines were left out of it.
pub fn hunks[&h, &a, &b](heap: &!h Heap, before: &a [byte], after: &b [byte], most: int) -> [heap] (buffer.Buffer, bool) {
    var w = json.writer(heap, 256);
    w = json.begin_array(heap, w);
    let p = prefix(before, after);
    var cut = false;
    if !(p == len(before) && p == len(after)) {
        let s = suffix(before, after, p);
        let removed = before[p..len(before) - s];
        let added = after[p..len(after) - s];
        let first = line_count(before[0..p]) + 1;
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "old_start");
        w = json.put_int(heap, w, first);
        w = json.put_key(heap, w, "new_start");
        w = json.put_int(heap, w, first);
        w = json.put_key(heap, w, "removed_count");
        w = json.put_int(heap, w, line_count(removed));
        w = json.put_key(heap, w, "added_count");
        w = json.put_int(heap, w, line_count(added));
        w = json.put_key(heap, w, "removed");
        let (w2, lines, left, cut1) = put_lines(heap, w, removed, most, budget());
        w = json.put_key(heap, w2, "added");
        // The lines shown are a prefix of the removed ones, then of the
        // added ones: once the removed are cut, no added line is shown.
        var room = lines;
        if cut1 {
            room = 0;
        }
        let (w3, unused_lines, unused_left, cut2) = put_lines(heap, w, added, room, left);
        w = json.end_object(heap, w3);
        cut = cut1 || cut2;
    }
    w = json.end_array(heap, w);
    return (json.finish(w), cut);
}
