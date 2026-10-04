edition 5;

import std.buffer;
import std.bytes;
import std.test;
import toolbox.lines;

// No file is opened here; the reader is driven from a buffer the test
// fills by hand through `feed`, which is what `fill_file` does with a
// read. The file-backed path is exercised by the tools' conformance
// tests.
pub fn test_reader_names_its_states() -> [] int {
    test.assert(lines.need() != lines.line());
    test.assert(lines.line() != lines.done());
    test.assert(lines.done() != lines.long());
    return 0;
}

pub fn test_an_empty_reader_needs_input[&h](heap: &!h Heap) -> [heap] int {
    let r = lines.start(heap, 16);
    let (r2, s) = lines.next(heap, r);
    test.assert_eq(s, lines.need());
    lines.drop(heap, r2);
    return 0;
}
