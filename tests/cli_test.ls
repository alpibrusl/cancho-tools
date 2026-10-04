edition 5;

import std.test;
import toolbox.cli;
import toolbox.rules;

fn table() -> [] &static [byte] {
    return "max-count|m|nat|none|-1|stop after this many;require-match||bool|none||exit 8 when nothing matched;format||choice:ndjson/text|none|ndjson|output form;root||path|root||the directory paths are relative to";
}

pub fn test_table_fields_read_back() -> [] int {
    test.assert_eq(cli.entries(table()), 4);
    test.assert(cli.index_of(table(), "require-match") == 1);
    test.assert(cli.index_of(table(), "require") == 0 - 1);
    test.assert(!cli.takes_value(table(), 1));
    test.assert(cli.takes_value(table(), 0));
    return 0;
}

pub fn test_nat_refuses_what_could_overflow() -> [] int {
    test.assert_eq(cli.parse_nat("0"), 0);
    test.assert_eq(cli.parse_nat("123456789012345678"), 123456789012345678);
    test.assert_eq(cli.parse_nat("1234567890123456789"), 0 - 1);
    test.assert_eq(cli.parse_nat(""), 0 - 1);
    test.assert_eq(cli.parse_nat("-1"), 0 - 1);
    test.assert_eq(cli.parse_nat("1x"), 0 - 1);
    return 0;
}

pub fn test_values_are_checked_by_kind() -> [] int {
    test.assert(cli.valid("choice:ndjson/text", "text"));
    test.assert(!cli.valid("choice:ndjson/text", "json"));
    test.assert(!cli.valid("choice:ndjson/text", "tex"));
    test.assert(cli.valid("nat", "12"));
    test.assert(!cli.valid("text", ""));
    return 0;
}

pub fn test_nearest_suggests_only_what_is_safe() -> [] int {
    // One letter off a `none` flag: suggested.
    test.assert(cli.nearest(table(), "max-cont") == 0);
    test.assert(cli.nearest(table(), "requre-match") == 1);
    // `root` has role `root`: never suggested, however near (D6 rule 1).
    test.assert(cli.nearest(table(), "rot") == 0 - 1);
    // Nothing near.
    test.assert(cli.nearest(table(), "recursive") == 0 - 1);
    return 0;
}

pub fn test_every_rule_has_an_exit_code_in_the_d4_table() -> [] int {
    var i = 0;
    while i < rules.count() {
        let code = rules.exit_at(i);
        test.assert(code == 1 || code == 2 || code == 3 || code == 4 || code == 5 || code == 8);
        test.assert(rules.find(rules.tag_of(i)) == i);
        i = i + 1;
    }
    test.assert_eq(rules.exit_of("args.unknown-flag"), 2);
    test.assert_eq(rules.exit_of("io.not-found"), 3);
    test.assert_eq(rules.exit_of("no.such-rule"), 1);
    return 0;
}
