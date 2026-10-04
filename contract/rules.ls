edition 5;

module toolbox.rules;

// `toolbox.rules` -- the rule catalogue every tool shares (D5), and the
// exit codes each rule maps to (D4).
//
// A tag names the rule a reader would look up, not the sentence: the
// prose in a message may change, the tag never does, and a rule that
// splits gets siblings rather than repurposing its parent. Every tag here
// has a fixture that reaches it (`tests/conformance/test_rules.py`), and
// the set of fixtures is checked equal to this catalogue.
//
// One table, read by the code that maps a tag to its exit code *and* by
// `introspect`, so the two cannot disagree. It is one line because a
// string literal may not span lines; entries are `;`-separated and fields
// `|`-separated: tag, exit code, repairable (`always`, `sometimes`,
// `never`), and a one-line summary.

import std.bytes;

pub fn catalogue() -> [] &static [byte] {
    return "args.unknown-flag|2|sometimes|a flag that is not in the tool's table;args.missing-value|2|never|a flag that takes a value was given none;args.bad-value|2|never|a flag's value is not of the flag's kind;args.unexpected-value|2|never|a flag that takes no value was given one;args.duplicate-flag|2|never|the same flag was given twice;args.conflict|2|never|two flags that exclude each other were both given;args.missing-operand|2|never|an operand the tool needs was not given;args.too-many-operands|2|never|more operands than the tool takes;path.empty|2|never|an empty path;path.dotdot|2|sometimes|a path with a `..` component;path.absolute|2|always|an absolute path where one relative to --root is wanted;path.outside-root|4|never|an absolute path that is not under --root;path.too-long|2|never|a path longer than 4096 bytes;io.not-found|3|never|a named input does not exist;io.not-a-directory|3|never|a path runs through something that is not a directory;io.is-a-directory|2|never|a directory where a file is wanted;io.permission-denied|4|never|the operating system refused access;io.read-failed|1|never|the operating system failed a read;io.write-failed|1|never|the operating system failed a write, or a write was short;limit.line-too-long|8|sometimes|a line longer than --max-line-bytes;limit.input-too-large|8|sometimes|an input longer than --max-bytes;limit.too-many-keys|8|sometimes|more distinct keys than --max-keys;precondition.required|2|never|a write that states no belief about the file;precondition.hash-mismatch|5|never|the file's hash is not the one --if-sha256 named;precondition.content-mismatch|5|never|the new content's hash is not the one --content-sha256 named;precondition.count-mismatch|5|never|the text occurs a different number of times than --expect said;precondition.no-match|8|never|--require-match was given and nothing matched;precondition.hash-failed|8|never|--verify was given and the digest differs;conflict.exists|5|never|--create was given and the path exists;conflict.locked|5|never|another writer holds the path's lock;parse.json|8|never|the input is not one valid JSON document;query.bad-pointer|2|never|a JSON Pointer that is not RFC 6901 syntax;query.no-such-path|3|never|a JSON Pointer that names nothing in the document;query.wrong-kind|8|never|the value at the pointer is not of the kind the query needs;query.unsupported-syntax|2|never|a query this tool refuses to grow to answer;internal.invariant|1|never|the tool caught a bug in itself";
}

pub fn count() -> [] int {
    return bytes.count_byte(catalogue(), ';') + 1;
}

// Entry `i` (0-based), whole.
pub fn entry(i: int) -> [] &static [byte] {
    return bytes.field(catalogue(), ';', i + 1);
}

pub fn tag_of(i: int) -> [] &static [byte] {
    return bytes.field(entry(i), '|', 1);
}

pub fn repairable_of(i: int) -> [] &static [byte] {
    return bytes.field(entry(i), '|', 3);
}

pub fn summary_of(i: int) -> [] &static [byte] {
    return bytes.field(entry(i), '|', 4);
}

// The entry for `tag`, or -1.
pub fn find[&t](tag: &t [byte]) -> [] int {
    var i = 0;
    let n = count();
    while i < n {
        if bytes.equal(tag_of(i), tag) {
            return i;
        }
        i = i + 1;
    }
    return 0 - 1;
}

pub fn exit_at(i: int) -> [] int {
    let text = bytes.field(entry(i), '|', 2);
    var n = 0;
    var k = 0;
    while k < len(text) {
        n = n * 10 + bytes.digit_of(int_of(text[k]));
        k = k + 1;
    }
    return n;
}

// The exit code for `tag`. A tag that is not in the catalogue is the
// tool's own bug, and answers 1 (GENERAL_ERROR) rather than a trap; the
// fixture-equality test is what keeps it from shipping.
pub fn exit_of[&t](tag: &t [byte]) -> [] int {
    let i = find(tag);
    if i < 0 {
        return 1;
    }
    return exit_at(i);
}

// ACLI's name for an exit code (`acli-0.5.0` `ExitCode`).
pub fn code_name(exit: int) -> [] &static [byte] {
    if exit == 0 {
        return "SUCCESS";
    }
    if exit == 1 {
        return "GENERAL_ERROR";
    }
    if exit == 2 {
        return "INVALID_ARGS";
    }
    if exit == 3 {
        return "NOT_FOUND";
    }
    if exit == 4 {
        return "PERMISSION_DENIED";
    }
    if exit == 5 {
        return "CONFLICT";
    }
    if exit == 6 {
        return "TIMEOUT";
    }
    if exit == 7 {
        return "UPSTREAM_ERROR";
    }
    if exit == 8 {
        return "PRECONDITION_FAILED";
    }
    if exit == 9 {
        return "DRY_RUN";
    }
    return "GENERAL_ERROR";
}

// The D4 meaning of each code, for `introspect` and `skill`.
pub fn code_meaning(exit: int) -> [] &static [byte] {
    if exit == 0 {
        return "what was asked was done; zero matches is success";
    }
    if exit == 1 {
        return "the operating system failed mid-operation, or the tool caught a bug in itself";
    }
    if exit == 2 {
        return "the invocation is malformed or names something of the wrong kind; nothing was read";
    }
    if exit == 3 {
        return "a named input does not exist";
    }
    if exit == 4 {
        return "the operating system refused, or the tool's own confinement did";
    }
    if exit == 5 {
        return "state is not what the caller said it would be";
    }
    if exit == 8 {
        return "the answer is no, or a limit was reached";
    }
    if exit == 9 {
        return "a --dry-run completed; planned_actions lists what an apply would do";
    }
    return "";
}
