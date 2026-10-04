edition 5;

import std.buffer;
import std.bytes;
import std.test;
import toolbox.path;

fn same[&h, &p, &w](heap: &!h Heap, given: &p [byte], want: &w [byte], climbs: bool) -> [heap] int {
    let (b, climbed) = path.lexical(heap, given);
    borrow b as &r in {
        test.assert(bytes.equal(buffer.bytes(r), want));
    }
    test.assert(climbed == climbs);
    buffer.drop(heap, b);
    return 0;
}

pub fn test_lexical_normal_form[&h](heap: &!h Heap) -> [heap] int {
    same(heap, "a/b", "a/b", false);
    same(heap, "a//b/", "a/b", false);
    same(heap, "./a/./b", "a/b", false);
    same(heap, "a/../b", "b", false);
    same(heap, "a//../b", "b", false);
    same(heap, "a/b/../../c", "c", false);
    same(heap, "../x", "x", true);
    same(heap, "/a/../b", "/b", false);
    same(heap, "/../b", "/b", true);
    same(heap, "/", "/", false);
    return 0;
}

pub fn test_dotdot_is_found_only_as_a_component() -> [] int {
    test.assert(path.has_dotdot(".."));
    test.assert(path.has_dotdot("a/../b"));
    test.assert(path.has_dotdot("a//.."));
    test.assert(!path.has_dotdot("a/..b"));
    test.assert(!path.has_dotdot("..."));
    test.assert(!path.has_dotdot("a/b.."));
    return 0;
}

pub fn test_under_respects_component_boundaries() -> [] int {
    test.assert_eq(path.under("/root", "/root/a"), 6);
    test.assert_eq(path.under("/root", "/root"), 5);
    test.assert_eq(path.under("/root", "/rootevil/a"), 0 - 1);
    test.assert_eq(path.under("/root", "/roo"), 0 - 1);
    test.assert_eq(path.under("/", "/x"), 1);
    return 0;
}
