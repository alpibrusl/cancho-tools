"""Tables, argument lists and the two generated programs behind `test_cli_lookup.py`.

Nothing here is checked in as generated cancho: a table must be a string literal (`&static`), so
the programs are written out, built and run by the test, and thrown away. Everything is seeded; the
same run makes the same tables.

    differential_program(tables)  -- `toolbox.cli` beside `tests/reference/cli_old.cho`, which is
                                     the module as it was before the lookups were made linear
    cost_program(k)               -- one table of k entries and the calls the cost gate counts
"""

import random

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
KINDS = ["bool", "nat", "text", "any", "path", "hex64", "choice:a/b/c", "choice:ndjson/text", "choice:x"]
ROLES = ["none", "path-read", "path-write", "root", "guard"]


def lit(table):
    assert '"' not in table and "\\" not in table and "\n" not in table
    return '"' + table + '"'


def entry(name, short="", kind="bool", role="none", default="", help_text="help"):
    return "|".join([name, short, kind, role, default, help_text])


# Tables that are edge cases by construction. A table is entries joined by `;`, an entry is up to
# six fields joined by `|`; the tool tables never break these rules, but `toolbox.cli` is asked to
# answer for any string, and the old and the new must answer alike.
EDGE = [
    "",                                   # no entries at all
    "a",                                  # one entry that is only a name
    entry("only", "o", "nat", "none", "7", "the only one"),
    ";",                                  # two empty entries
    "a;",                                 # a trailing empty entry
    ";a",                                 # a leading empty entry
    "a;;b",                               # an empty entry between two
    ";;;",                                # four empty entries
    "a|b;c|d|e;f|g|h|i;j|k|l|m|n;o|p|q|r|s|t|u",   # 2..7 fields: missing and extra ones
    "|||||;x|y",                          # empty name, empty everything
    # names that are prefixes of others, and the shorter one listed after the longer one
    ";".join([entry("max-count", "m", "nat", "none", "-1", "stop"), entry("max", "x"), entry("m"), entry("max-"), entry("max-count-extra", "e")]),
    ";".join([entry("abc"), entry("ab"), entry("a"), entry(""), entry("abcd")]),
    # a name that is another's name plus the separator and a field; shorts that are not letters
    ";".join([entry("a", "-"), entry("b", "="), entry("c", "1"), entry("d", "ab"), entry("e", "A"), entry("f", "a"), entry("g", "a")]),
    # every kind, with defaults
    ";".join(entry("k%d" % i, chr(97 + i), k, "none", "d%d" % i, "help %d" % i) for i, k in enumerate(KINDS)),
    # a duplicate name: the first one answers
    ";".join([entry("dup", "d", "nat", "none", "1"), entry("dup", "d", "text", "none", "2"), entry("dupe")]),
    # rule lists of the kind `describe.Tool.extra_rules` holds: a repeat of a shared tag is ignored
    "args.unknown-flag|8|never|a repeat;demo.ragged|8|sometimes|its own;demo.other|3|never|another;path.empty|8|never|a repeat again",
    "demo.ragged|8|sometimes|its own",
    # the real shape of a tool's table
    "max-count|m|nat|none|-1|stop after this many;require-match||bool|none||exit 8 when nothing matched;format||choice:ndjson/text|none|ndjson|output form;root||path|root||the directory paths are relative to",
]


def random_entry(r, names):
    n = r.randint(0, 3)
    pick = r.random()
    if names and pick < 0.25:
        base = r.choice(names)
        name = base[: r.randint(0, len(base))] if r.random() < 0.5 else base + r.choice(ALPHABET)
    else:
        name = "".join(r.choice(ALPHABET) for _ in range(r.randint(1, 14)))
    kind = r.choice(KINDS)
    fields = [
        name,
        r.choice(["", "", chr(97 + r.randrange(26)), chr(97 + r.randrange(26)), "-", "ab"]),
        kind,
        r.choice(ROLES),
        r.choice(["", "", "5", "text", "a", "-1"]),
        " ".join(r.choice(["the", "flag", "of", "a", "tool", "which", "reads", "x"]) for _ in range(r.randint(0, 9))),
    ]
    keep = r.choice([6, 6, 6, 6, 5, 4, 3, 2, 1]) if n == 0 else 6
    return "|".join(fields[:keep]), name


def random_table(r, size):
    names, parts = [], []
    for _ in range(size):
        e, name = random_entry(r, names)
        names.append(name)
        parts.append(e)
    return ";".join(parts)


def tables(seed=20240601):
    r = random.Random(seed)
    out = list(EDGE)
    for size in (1, 2, 3, 5, 8, 13, 21, 34):
        out.append(random_table(r, size))
        out.append(random_table(r, size))
    out.append(random_table(r, 200))
    out.append(random_table(r, 200))
    return out


def names_in(table):
    return [e.split("|")[0] for e in table.split(";")] if table else []


def shorts_in(table):
    return [e.split("|")[1] for e in table.split(";") if e.count("|") >= 1] if table else []


def argvs(table, r, count=24):
    """Argument lists that exercise the parser against this table: every flag by name and by letter,
    typos of names, bundles, `=value`, duplicates, missing values, unknown flags, operands."""
    names = [n for n in names_in(table) if n and "=" not in n]
    shorts = [s for s in shorts_in(table) if len(s) == 1 and s != "-" and s != "="]
    out = [[], ["--"], ["--", "--x"], ["-"], ["--="], ["--=x"], ["-="], ["-x"], ["---"]]
    pool = []
    for n in names:
        pool += [["--" + n], ["--" + n, "5"], ["--" + n + "=5"], ["--" + n + "=a"], ["--" + n + "="], ["--" + n[:-1]], ["--" + n + "x"]]
        if len(n) > 1:
            pool.append(["--" + n[:-1] + "z"])
            pool.append(["--" + n[1:]])
    for s in shorts:
        pool += [["-" + s], ["-" + s, "text"], ["-" + s + "3"], ["-" + s + s], ["-" + s, "-" + s]]
    pool += [["--zzzzzz"], ["-Q"], ["-Q", "--zz=1"], ["operand", "--"], ["a", "b", "-", "c"]]
    for _ in range(count):
        k = r.randint(1, 4)
        argv = []
        for _ in range(k):
            argv += r.choice(pool) if pool else ["--x"]
        out.append(argv)
    return out


def extra_queries(r):
    return ["", "-", "|", ";", "a|b", "a;b", "max", "max-count", "zzzzzzzz", "x", "A", "MAX", "format", "root", "é"] + [
        "".join(r.choice(ALPHABET) for _ in range(r.randint(1, 8))) for _ in range(6)
    ]


def differential_program(table_list):
    """`diffcli ID acc [QUERY...]` compares the table accessors and lookups; `diffcli ID parse [ARG...]`
    compares `parse` and the readers. Exit 0 when old and new agree, otherwise the number of the first
    thing that did not (printed on standard error is not available without a heap-free writer, so the
    number is the message; see CODES)."""
    branches = []
    for i, t in enumerate(table_list):
        branches.append("    if id == %d {\n        return %s;\n    }\n" % (i, lit(t)))
    return PROGRAM_DIFF.replace("@@TABLES@@", "".join(branches))


CODES = {
    1: "entries", 2: "entry", 3: "name_of", 4: "short_of", 5: "kind_of", 6: "role_of", 7: "default_of",
    8: "help_of", 9: "takes_value", 10: "index_of", 11: "nearest", 20: "slots", 21: "operands",
    22: "errors", 23: "has", 24: "text", 25: "nat", 26: "value_index", 27: "value_is_whole",
    30: "no such table", 31: "no mode",
}

PROGRAM_DIFF = r'''edition 5;

import std.buffer;
import std.bytes;
import toolbox.cli;
import toolbox.cli_old;
import toolbox.fail;
import toolbox.rules;
import toolbox.rules_old;

fn table_by(id: int) -> [] &static [byte] {
@@TABLES@@    return "";
}

fn accessors(table: &static [byte]) -> [] int {
    if cli.entries(table) != cli_old.entries(table) {
        return 1;
    }
    let n = cli_old.entries(table);
    var i = 0 - 3;
    while i < n + 3 {
        if !bytes.equal(cli.entry(table, i), cli_old.entry(table, i)) {
            return 2;
        }
        if !bytes.equal(cli.name_of(table, i), cli_old.name_of(table, i)) {
            return 3;
        }
        if !bytes.equal(cli.short_of(table, i), cli_old.short_of(table, i)) {
            return 4;
        }
        if !bytes.equal(cli.kind_of(table, i), cli_old.kind_of(table, i)) {
            return 5;
        }
        if !bytes.equal(cli.role_of(table, i), cli_old.role_of(table, i)) {
            return 6;
        }
        if !bytes.equal(cli.default_of(table, i), cli_old.default_of(table, i)) {
            return 7;
        }
        if !bytes.equal(cli.help_of(table, i), cli_old.help_of(table, i)) {
            return 8;
        }
        if cli.takes_value(table, i) != cli_old.takes_value(table, i) {
            return 9;
        }
        i = i + 1;
    }
    return 0;
}

fn query[&q](table: &static [byte], q: &q [byte]) -> [] int {
    if cli.index_of(table, q) != cli_old.index_of(table, q) {
        return 10;
    }
    if cli.nearest(table, q) != cli_old.nearest(table, q) {
        return 11;
    }
    return 0;
}

// Every name of the table, every prefix of it, the name with a byte added and with its first byte
// replaced: the queries that find a prefix match, a near miss and the exact entry.
fn derived[&h](heap: &!h Heap, table: &static [byte]) -> [heap] int {
    let n = cli_old.entries(table);
    var i = 0;
    while i < n {
        let name = cli_old.name_of(table, i);
        var k = 0;
        while k <= len(name) {
            let st = query(table, name[0..k]);
            if st != 0 {
                return st;
            }
            k = k + 1;
        }
        var longer = buffer.empty(heap, len(name) + 2);
        longer = buffer.append(heap, longer, name);
        longer = buffer.push(heap, longer, byte_of('x'));
        var st = 0;
        borrow longer as &l in {
            st = query(table, buffer.bytes(l));
        }
        buffer.drop(heap, longer);
        if st != 0 {
            return st;
        }
        var changed = buffer.empty(heap, len(name) + 2);
        changed = buffer.push(heap, changed, byte_of('q'));
        if len(name) > 0 {
            changed = buffer.append(heap, changed, name[1..len(name)]);
        }
        borrow changed as &c in {
            st = query(table, buffer.bytes(c));
        }
        buffer.drop(heap, changed);
        if st != 0 {
            return st;
        }
        i = i + 1;
    }
    return 0;
}

fn rule_query[&q](extra: &static [byte], q: &q [byte]) -> [] int {
    if rules.find_in(extra, q) != rules_old.find_in(extra, q) {
        return 13;
    }
    if rules.exit_of_in(extra, q) != rules_old.exit_of_in(extra, q) {
        return 13;
    }
    if rules.find(q) != rules_old.find(q) {
        return 13;
    }
    if rules.exit_of(q) != rules_old.exit_of(q) {
        return 13;
    }
    return 0;
}

// Every tag of `list` (a catalogue or a tool's own rules), every prefix of it, and the tag with a
// byte added, asked of both `find_in`s and both `exit_of_in`s with `extra` as the tool's own rules.
fn rule_names[&h](heap: &!h Heap, extra: &static [byte], list: &static [byte]) -> [heap] int {
    let n = cli_old.entries(list);
    var i = 0;
    while i < n {
        let name = cli_old.name_of(list, i);
        var k = 0;
        while k <= len(name) {
            let st = rule_query(extra, name[0..k]);
            if st != 0 {
                return st;
            }
            k = k + 1;
        }
        var longer = buffer.empty(heap, len(name) + 2);
        longer = buffer.append(heap, longer, name);
        longer = buffer.push(heap, longer, byte_of('x'));
        var st = 0;
        borrow longer as &l in {
            st = rule_query(extra, buffer.bytes(l));
        }
        buffer.drop(heap, longer);
        if st != 0 {
            return st;
        }
        i = i + 1;
    }
    return 0;
}

// `toolbox.rules` against `toolbox.rules_old`, with the table as the tool's own rules.
fn rules_check[&h](heap: &!h Heap, extra: &static [byte]) -> [heap] int {
    if rules.count() != rules_old.count() || rules.count_in(extra) != rules_old.count_in(extra) {
        return 14;
    }
    var i = 0 - 3;
    while i < rules_old.count_in(extra) + 3 {
        if !bytes.equal(rules.entry(i), rules_old.entry(i)) || !bytes.equal(rules.tag_of(i), rules_old.tag_of(i)) {
            return 14;
        }
        if !bytes.equal(rules.repairable_of(i), rules_old.repairable_of(i)) || !bytes.equal(rules.summary_of(i), rules_old.summary_of(i)) {
            return 14;
        }
        if rules.exit_at(i) != rules_old.exit_at(i) {
            return 14;
        }
        if !bytes.equal(rules.entry_in(extra, i), rules_old.entry_in(extra, i)) {
            return 14;
        }
        if !bytes.equal(rules.repairable_in(extra, i), rules_old.repairable_in(extra, i)) || !bytes.equal(rules.summary_in(extra, i), rules_old.summary_in(extra, i)) {
            return 14;
        }
        i = i + 1;
    }
    var st = rule_query(extra, "");
    if st == 0 {
        st = rule_query(extra, "|");
    }
    if st == 0 {
        st = rule_names(heap, extra, rules_old.catalogue());
    }
    if st == 0 {
        st = rule_names(heap, extra, extra);
    }
    return st;
}

fn readers[&g, &a, &b](args: &g Args, table: &static [byte], pn: &a cli.Parsed, po: &b cli_old.Parsed, name: &static [byte]) -> [args] int {
    if cli.has(pn, table, name) != cli_old.has(po, table, name) {
        return 23;
    }
    if !bytes.equal(cli.text(args, pn, table, name), cli_old.text(args, po, table, name)) {
        return 24;
    }
    if cli.nat(args, pn, table, name) != cli_old.nat(args, po, table, name) {
        return 25;
    }
    if cli.value_index(pn, table, name) != cli_old.value_index(po, table, name) {
        return 26;
    }
    if cli.value_is_whole(pn, table, name) != cli_old.value_is_whole(po, table, name) {
        return 27;
    }
    return 0;
}

fn compare[&g, &a, &b, &x, &y](args: &g Args, table: &static [byte], pn: &a cli.Parsed, po: &b cli_old.Parsed, en: &x fail.Errors, eo: &y fail.Errors) -> [args] int {
    let sn = contents(pn.slots);
    let so = contents(po.slots);
    if len(sn) != len(so) {
        return 20;
    }
    var k = 0;
    while k < len(sn) {
        if sn[k] != so[k] {
            return 20;
        }
        k = k + 1;
    }
    if cli.operand_count(pn) != cli_old.operand_count(po) {
        return 21;
    }
    k = 0;
    while k < cli.operand_count(pn) {
        if cli.operand_index(pn, k) != cli_old.operand_index(po, k) {
            return 21;
        }
        k = k + 1;
    }
    if fail.count(en) != fail.count(eo) || !bytes.equal(fail.listed(en), fail.listed(eo)) {
        return 22;
    }
    let n = cli_old.entries(table);
    var i = 0;
    while i < n {
        let st = readers(args, table, pn, po, cli_old.name_of(table, i));
        if st != 0 {
            return st;
        }
        i = i + 1;
    }
    return readers(args, table, pn, po, "no-such-flag");
}

fn parsed[&h, &g](heap: &!h Heap, args: &g Args, table: &static [byte]) -> [heap, args] int {
    let (pn, en) = cli.parse(heap, args, table, fail.empty(heap));
    let (po, eo) = cli_old.parse(heap, args, table, fail.empty(heap));
    var status = 0;
    borrow pn as &a in {
        borrow po as &b in {
            borrow en as &x in {
                borrow eo as &y in {
                    status = compare(args, table, a, b, x, y);
                }
            }
        }
    }
    cli.drop(heap, pn);
    cli_old.drop(heap, po);
    fail.drop(heap, en);
    fail.drop(heap, eo);
    return status;
}

fn run[&h, &g](heap: &!h Heap, args: &g Args) -> [heap, args] int {
    if arg_count(args) < 3 {
        return 31;
    }
    let id = cli.parse_nat(arg(args, 1));
    let table = table_by(id);
    if id < 0 || id > 9999 {
        return 30;
    }
    if bytes.equal(arg(args, 2), "acc") {
        var st = accessors(table);
        if st == 0 {
            st = derived(heap, table);
        }
        var k = 3;
        while st == 0 && k < arg_count(args) {
            st = query(table, arg(args, k));
            k = k + 1;
        }
        return st;
    }
    return parsed(heap, args, table);
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(io);
    release(ffi);
    release(fs);
    release(net);
    release(clock);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            status = run(h, g);
        }
    }
    release(heap);
    release(args);
    return status;
}
'''


def cost_table(k):
    kinds = ["nat", "text", "choice:alpha/beta/gamma", "any"]
    parts = []
    for j in range(k):
        kind = "bool" if j >= k - 4 else kinds[j % 4]
        short = chr(97 + j) if j < 26 else ""
        default = "5" if kind == "nat" else ("alpha" if kind.startswith("choice") else "")
        role = "path-read" if j % 7 == 3 else "none"
        parts.append(entry(cost_name(j), short, kind, role, default, "help text for the flag number %d, which goes on for a while like a real one does" % j))
    return ";".join(parts)


def cost_name(j):
    return "flag-%03d-name" % j


def cost_program(k):
    return PROGRAM_COST.replace("@@TABLE@@", lit(cost_table(k)))


PROGRAM_COST = r'''edition 5;

import std.buffer;
import std.bytes;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;

fn table() -> [] &static [byte] {
    return @@TABLE@@;
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "cost", version: "0.0.1", summary: "A table of many flags.", usage: "cost", output: "document", schema: "cost.v1", flags: table(), operands: "", rules: "args.unknown-flag", extra_rules: "", limits: "", reversibility: "reversible-cheap", stdin: "no", guarantees: "" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: "{}", schema: "{}", compiler: "test" };
}

// `cost MODE REPS [ARG...]`: REPS calls of one kind, and nothing else, so that the count with
// REPS 0 is the program's own and the difference is the calls'. A wrong answer is exit 1.
fn run[&h, &g](heap: &!h Heap, args: &g Args) -> [heap, args] int {
    let mode = arg(args, 1);
    let reps = cli.parse_nat(arg(args, 2));
    let last = cli.entries(table()) - 1;
    var wrong = 0;
    var r = 0;
    if bytes.equal(mode, "index") {
        while r < reps {
            if cli.index_of(table(), "no-such-flag") != 0 - 1 {
                wrong = 1;
            }
            r = r + 1;
        }
    } else if bytes.equal(mode, "index-last") {
        while r < reps {
            if cli.index_of(table(), arg(args, 3)) != last {
                wrong = 1;
            }
            r = r + 1;
        }
    } else if bytes.equal(mode, "kind-last") {
        while r < reps {
            if !bytes.equal(cli.kind_of(table(), last), "bool") {
                wrong = 1;
            }
            r = r + 1;
        }
    } else if bytes.equal(mode, "default-last") {
        while r < reps {
            if len(cli.default_of(table(), last)) != 0 {
                wrong = 1;
            }
            r = r + 1;
        }
    } else if bytes.equal(mode, "nearest") {
        while r < reps {
            if cli.nearest(table(), "zz-zz-zz-zz-zz") != 0 - 1 {
                wrong = 1;
            }
            r = r + 1;
        }
    } else if bytes.equal(mode, "has") || bytes.equal(mode, "text") {
        let (p, e) = cli.parse(heap, args, table(), fail.empty(heap));
        borrow p as &q in {
            while r < reps {
                if bytes.equal(mode, "has") {
                    if cli.has(q, table(), "no-such-flag") {
                        wrong = 1;
                    }
                } else if len(cli.text(args, q, table(), arg(args, 3))) != 0 {
                    wrong = 1;
                }
                r = r + 1;
            }
        }
        cli.drop(heap, p);
        fail.drop(heap, e);
    } else if bytes.equal(mode, "parse") {
        let (p, e) = cli.parse(heap, args, table(), fail.empty(heap));
        cli.drop(heap, p);
        fail.drop(heap, e);
    } else if bytes.equal(mode, "introspect") {
        while r < reps {
            let b = describe.introspect(heap, tool(), built());
            buffer.drop(heap, b);
            r = r + 1;
        }
    } else if bytes.equal(mode, "skill") {
        while r < reps {
            let b = describe.skill(heap, tool(), built());
            buffer.drop(heap, b);
            r = r + 1;
        }
    }
    return wrong;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(io);
    release(ffi);
    release(fs);
    release(net);
    release(clock);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            status = run(h, g);
        }
    }
    release(heap);
    release(args);
    return status;
}
'''
