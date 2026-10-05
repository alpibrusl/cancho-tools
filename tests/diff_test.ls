edition 5;

import std.buffer;
import std.bytes;
import std.test;
import toolbox.diff;

fn same[&h, &a, &b, &w](heap: &!h Heap, before: &a [byte], after: &b [byte], most: int, want: &w [byte], cut: bool) -> [heap] int {
    let (got, truncated) = diff.hunks(heap, before, after, most);
    borrow got as &g in {
        test.assert(bytes.equal(buffer.bytes(g), want));
    }
    test.assert(truncated == cut);
    buffer.drop(heap, got);
    return 0;
}

pub fn test_nothing_changes[&h](heap: &!h Heap) -> [heap] int {
    same(heap, "", "", 10, "[]", false);
    same(heap, "a\nb\n", "a\nb\n", 10, "[]", false);
    same(heap, "a\nb", "a\nb", 10, "[]", false);
    return 0;
}

pub fn test_one_line_in_the_middle[&h](heap: &!h Heap) -> [heap] int {
    same(heap, "a\nb\nc\n", "a\nB\nc\n", 10, "[{\"old_start\":2,\"new_start\":2,\"removed_count\":1,\"added_count\":1,\"removed\":[\"b\\n\"],\"added\":[\"B\\n\"]}]", false);
    return 0;
}

pub fn test_insertion_and_deletion[&h](heap: &!h Heap) -> [heap] int {
    same(heap, "a\nc\n", "a\nb\nc\n", 10, "[{\"old_start\":2,\"new_start\":2,\"removed_count\":0,\"added_count\":1,\"removed\":[],\"added\":[\"b\\n\"]}]", false);
    same(heap, "a\nb\nc\n", "a\nc\n", 10, "[{\"old_start\":2,\"new_start\":2,\"removed_count\":1,\"added_count\":0,\"removed\":[\"b\\n\"],\"added\":[]}]", false);
    // A repeated line: the suffix may not reach back into the prefix.
    same(heap, "a\n", "a\na\n", 10, "[{\"old_start\":2,\"new_start\":2,\"removed_count\":0,\"added_count\":1,\"removed\":[],\"added\":[\"a\\n\"]}]", false);
    return 0;
}

pub fn test_create_and_last_line[&h](heap: &!h Heap) -> [heap] int {
    same(heap, "", "x\ny", 10, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":0,\"added_count\":2,\"removed\":[],\"added\":[\"x\\n\",\"y\"]}]", false);
    // A newline added at the end changes the last line.
    same(heap, "a\nx", "a\nx\n", 10, "[{\"old_start\":2,\"new_start\":2,\"removed_count\":1,\"added_count\":1,\"removed\":[\"x\"],\"added\":[\"x\\n\"]}]", false);
    // A change within a line takes the whole line.
    same(heap, "abc", "abd", 10, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":1,\"added_count\":1,\"removed\":[\"abc\"],\"added\":[\"abd\"]}]", false);
    return 0;
}

pub fn test_lines_are_bounded[&h](heap: &!h Heap) -> [heap] int {
    same(heap, "a\nb\n", "c\nd\n", 3, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":2,\"added_count\":2,\"removed\":[\"a\\n\",\"b\\n\"],\"added\":[\"c\\n\"]}]", true);
    same(heap, "a\nb\n", "c\nd\n", 1, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":2,\"added_count\":2,\"removed\":[\"a\\n\"],\"added\":[]}]", true);
    same(heap, "a\n", "b\n", 0, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":1,\"added_count\":1,\"removed\":[],\"added\":[]}]", true);
    // Cut in the removed lines with nothing added is still cut.
    same(heap, "a\nb\n", "", 1, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":2,\"added_count\":0,\"removed\":[\"a\\n\"],\"added\":[]}]", true);
    return 0;
}

// A removed line past the byte budget is cut, and no added line is shown
// after it, however short.
pub fn test_bytes_are_bounded[&h](heap: &!h Heap) -> [heap] int {
    var huge = buffer.empty(heap, diff.budget() + 2);
    var i = 0;
    while i <= diff.budget() {
        huge = buffer.push(heap, huge, byte_of('x'));
        i = i + 1;
    }
    huge = buffer.push(heap, huge, byte_of('\n'));
    borrow huge as &u in {
        same(heap, buffer.bytes(u), "y\n", 10, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":1,\"added_count\":1,\"removed\":[],\"added\":[]}]", true);
    }
    buffer.drop(heap, huge);
    return 0;
}

pub fn test_bytes_that_are_not_utf8[&h](heap: &!h Heap) -> [heap] int {
    var odd = buffer.empty(heap, 4);
    odd = buffer.push(heap, odd, byte_of(255));
    odd = buffer.push(heap, odd, byte_of('\n'));
    borrow odd as &o in {
        same(heap, "", buffer.bytes(o), 10, "[{\"old_start\":1,\"new_start\":1,\"removed_count\":0,\"added_count\":1,\"removed\":[],\"added\":[{\"b64\":\"/wo=\"}]}]", false);
    }
    buffer.drop(heap, odd);
    return 0;
}
