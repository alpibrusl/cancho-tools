#!/usr/bin/env python3
"""Mutation check of the table scans (`toolbox.cli`, `toolbox.rules`, and the loops of `toolbox.describe`
that read a table): each mutant is one of those files with one deliberate defect, made in a COPY of
the repository's `contract/` and `tests/`, so the working tree is never touched.

A mutant is killed when the contract no longer builds, or when `tests/conformance/test_cli_lookup.py`
fails: its Describe class (`introspect` and `skill` against an oracle written in the test), its
Differential class (old against new over many tables), or its Cost class (callgrind instruction counts,
which is the only gate a mutant that goes back to scanning the table from the start can fail, since it
answers the same). The gates run cheapest first and the first to fail kills.

    python3 scripts/cli_mutants.py --check           # every mutant; exit 1 if one survives
    python3 scripts/cli_mutants.py [name-substring ...]

`--check` also fails when a mutant listed in EQUIVALENT is killed (its proof is stale), when the
unmutated copy does not pass, and when a mutant's site is not found exactly once.

Run where the pinned compiler is (`cancho` on PATH, or $CANCHO).
"""
import concurrent.futures
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPILER = os.environ.get("CANCHO", "cancho")

# (file, name, the text replaced, its replacement[, which occurrence]): without the last, `old` occurs exactly once in its file.
MUTANTS = [
    # toolbox.cli: the cursor and the field scan
    ("cli", "entry_end: one past the separator", "    return at + next;\n}", "    return at + next + 1;\n}"),
    ("cli", "entry_end: the last entry ends one early", "    if next < 0 {\n        return len(table);\n    }\n    return at + next;", "    if next < 0 {\n        return len(table) - 1;\n    }\n    return at + next;"),
    ("cli", "entry_end: splits on the field separator", "let next = index_of_byte(table[at..len(table)], byte_of(';'));\n    if next < 0 {\n        return len(table);\n    }\n    return at + next;", "let next = index_of_byte(table[at..len(table)], byte_of('|'));\n    if next < 0 {\n        return len(table);\n    }\n    return at + next;"),
    ("cli", "entry_end: an offset before the table is scanned", "    if at < 0 || at >= len(table) {\n        return len(table);\n    }\n    let next", "    if at >= len(table) {\n        return len(table);\n    }\n    let next"),
    ("cli", "field_of: field 0 is the first", "    if n < 1 {\n        return whole[0..0];\n    }\n    var at = 0;\n    var seen = 1;", "    var at = 0;\n    var seen = 1;"),
    ("cli", "field_of: the field index is off by one", "    var at = 0;\n    var seen = 1;\n    while seen < n {", "    var at = 0;\n    var seen = 0;\n    while seen < n {"),
    ("cli", "field_of: splits on the entry separator", "        let next = index_of_byte(whole[at..len(whole)], byte_of('|'));\n        if next < 0 {\n            return whole[0..0];\n        }", "        let next = index_of_byte(whole[at..len(whole)], byte_of(';'));\n        if next < 0 {\n            return whole[0..0];\n        }"),
    ("cli", "field_of: the field keeps its separator", "    return whole[at..at + next];\n}\n\n// Entry `i`", "    return whole[at..at + next + 1];\n}\n\n// Entry `i`"),
    ("cli", "field_of: the last field loses a byte", "        return whole[at..len(whole)];\n    }\n    return whole[at..at + next];", "        return whole[at..len(whole) - 1];\n    }\n    return whole[at..at + next];"),
    ("cli", "field_of: a missing field is the last one", "        if next < 0 {\n            return whole[0..0];\n        }\n        at = at + next + 1;\n        seen = seen + 1;", "        if next < 0 {\n            return whole[at..len(whole)];\n        }\n        at = at + next + 1;\n        seen = seen + 1;"),
    ("cli", "entry: entry 0 is empty", "    if i < 0 {\n        return table[0..0];\n    }\n    var at = 0;\n    var k = 0;", "    if i < 1 {\n        return table[0..0];\n    }\n    var at = 0;\n    var k = 0;"),
    ("cli", "entry: a negative index is not refused", "    if i < 0 {\n        return table[0..0];\n    }\n    var at = 0;\n    var k = 0;", "    var at = 0;\n    var k = 0;"),
    ("cli", "entry: hops onto the separator", "        at = at + next + 1;\n        k = k + 1;\n    }\n    return table[at..entry_end(table, at)];", "        at = at + next;\n        k = k + 1;\n    }\n    return table[at..entry_end(table, at)];"),
    ("cli", "entry: one hop too many", "    while k < i {\n        let next = index_of_byte(table[at..len(table)], byte_of(';'));", "    while k <= i {\n        let next = index_of_byte(table[at..len(table)], byte_of(';'));"),
    ("cli", "entry: one hop too few", "    while k < i {\n        let next = index_of_byte(table[at..len(table)], byte_of(';'));", "    while k + 1 < i {\n        let next = index_of_byte(table[at..len(table)], byte_of(';'));"),
    ("cli", "entry: past the end answers the last entry", "        if next < 0 {\n            return table[0..0];\n        }\n        at = at + next + 1;\n        k = k + 1;\n    }\n    return table[at..entry_end(table, at)];", "        if next < 0 {\n            return table[at..len(table)];\n        }\n        at = at + next + 1;\n        k = k + 1;\n    }\n    return table[at..entry_end(table, at)];"),
    ("cli", "short_of reads the kind", "pub fn short_of(table: &static [byte], i: int) -> [] &static [byte] {\n    return field_of(entry(table, i), 2);", "pub fn short_of(table: &static [byte], i: int) -> [] &static [byte] {\n    return field_of(entry(table, i), 3);"),
    ("cli", "default_of reads the help", "pub fn default_of(table: &static [byte], i: int) -> [] &static [byte] {\n    return field_of(entry(table, i), 5);", "pub fn default_of(table: &static [byte], i: int) -> [] &static [byte] {\n    return field_of(entry(table, i), 6);"),
    # toolbox.cli: the name match
    ("cli", "is_named: a prefix of the name is a match", "    return len(whole) == len(name) || int_of(whole[len(name)]) == '|';", "    return true;"),
    ("cli", "is_named: an entry that is only the name is no match", "    return len(whole) == len(name) || int_of(whole[len(name)]) == '|';", "    return len(whole) > len(name) && int_of(whole[len(name)]) == '|';"),
    ("cli", "is_named: an entry the same length as the name is refused", "    if len(whole) < len(name) {\n        return false;\n    }", "    if len(whole) <= len(name) {\n        return false;\n    }"),
    ("cli", "is_named: any byte ends the name", "    return len(whole) == len(name) || int_of(whole[len(name)]) == '|';", "    return true && len(whole) >= len(name);"),
    ("cli", "is_named: the separator is the entry separator", "int_of(whole[len(name)]) == '|';", "int_of(whole[len(name)]) == ';';"),
    ("cli", "index_of: a name with a field separator can be found", "    if len(table) == 0 || index_of_byte(name, byte_of('|')) >= 0 {\n        return 0 - 1;\n    }\n    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named", "    if len(table) == 0 {\n        return 0 - 1;\n    }\n    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named"),
    ("cli", "index_of: the last (empty) entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named", "    var at = 0;\n    var i = 0;\n    while at < len(table) {\n        let end = entry_end(table, at);\n        if is_named"),
    ("cli", "index_of: the last entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named", "    var at = 0;\n    var i = 0;\n    while entry_end(table, at) < len(table) {\n        let end = entry_end(table, at);\n        if is_named"),
    ("cli", "index_of: the first entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named", "    var at = entry_end(table, 0) + 1;\n    var i = 1;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named"),
    ("cli", "index_of: every other entry is skipped", "        if is_named(table[at..end], name) {\n            return i;\n        }\n        at = end + 1;\n        i = i + 1;", "        if is_named(table[at..end], name) {\n            return i;\n        }\n        at = end + 2;\n        i = i + 1;"),
    ("cli", "index_of: the index counts from 1", "    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named", "    var at = 0;\n    var i = 1;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        if is_named"),
    ("cli", "index_of: the index does not advance", "        if is_named(table[at..end], name) {\n            return i;\n        }\n        at = end + 1;\n        i = i + 1;", "        if is_named(table[at..end], name) {\n            return i;\n        }\n        at = end + 1;"),
    ("cli", "index_of: an empty table has an entry", "    if len(table) == 0 || index_of_byte(name, byte_of('|')) >= 0 {", "    if index_of_byte(name, byte_of('|')) >= 0 {"),
    ("cli", "index_of: reads the table from the start for each entry (as it used to)", "        if is_named(table[at..end], name) {\n            return i;\n        }\n        at = end + 1;\n        i = i + 1;\n    }\n    return 0 - 1;\n}\n\nfn index_of_short", "        if is_named(bytes.field(table, ';', i + 1), name) {\n            return i;\n        }\n        at = end + 1;\n        i = i + 1;\n    }\n    return 0 - 1;\n}\n\nfn index_of_short"),
    # toolbox.cli: the short letter
    ("cli", "index_of_short: the field is the kind", "        let s = field_of(table[at..end], 2);", "        let s = field_of(table[at..end], 3);"),
    ("cli", "index_of_short: a longer short matches its first letter", "        if len(s) == 1 && int_of(s[0]) == letter {", "        if len(s) >= 1 && int_of(s[0]) == letter {"),
    ("cli", "index_of_short: the last entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        let s", "    var at = 0;\n    var i = 0;\n    while entry_end(table, at) < len(table) {\n        let end = entry_end(table, at);\n        let s"),
    ("cli", "index_of_short: the first entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        let s", "    var at = entry_end(table, 0) + 1;\n    var i = 1;\n    while at <= len(table) {\n        let end = entry_end(table, at);\n        let s"),
    ("cli", "index_of_short: the index does not advance", "        if len(s) == 1 && int_of(s[0]) == letter {\n            return i;\n        }\n        at = end + 1;\n        i = i + 1;", "        if len(s) == 1 && int_of(s[0]) == letter {\n            return i;\n        }\n        at = end + 1;"),
    # toolbox.cli: choices and the nearest name
    ("cli", "in_choice: the empty last choice is not a choice", "    var at = 0;\n    while at <= len(list) {\n        let next", "    var at = 0;\n    while at < len(list) {\n        let next"),
    ("cli", "in_choice: a choice keeps its separator", "            end = at + next;\n        }\n        if bytes.equal(list[at..end], value)", "            end = at + next + 1;\n        }\n        if bytes.equal(list[at..end], value)"),
    ("cli", "in_choice: splits on the field separator", "let next = index_of_byte(list[at..len(list)], byte_of('/'));", "let next = index_of_byte(list[at..len(list)], byte_of('|'));"),
    ("cli", "in_choice: the last choice is not looked at", "        if bytes.equal(list[at..end], value) {\n            return true;\n        }\n        at = end + 1;", "        if end < len(list) && bytes.equal(list[at..end], value) {\n            return true;\n        }\n        at = end + 1;"),
    ("cli", "nearest: the role is the kind", "            if bytes.equal(field_of(whole, 4), \"none\") {", "            if bytes.equal(field_of(whole, 3), \"none\") {"),
    ("cli", "nearest: the name is the short", "                let d = distance(name, field_of(whole, 1));", "                let d = distance(name, field_of(whole, 2));"),
    ("cli", "nearest: every other entry is skipped", "            at = end + 1;\n            i = i + 1;\n        }\n    }\n    if tied {", "            at = end + 2;\n            i = i + 1;\n        }\n    }\n    if tied {"),
    ("cli", "nearest: the index does not advance", "            at = end + 1;\n            i = i + 1;\n        }\n    }\n    if tied {", "            at = end + 1;\n        }\n    }\n    if tied {"),
    ("cli", "nearest: the last entry is not looked at", "        while at <= len(table) {\n            let end = entry_end(table, at);\n            let whole", "        while entry_end(table, at) < len(table) {\n            let end = entry_end(table, at);\n            let whole"),
    ("cli", "nearest: a tie is not a tie", "                } else if d == best_d {\n                    tied = true;\n                }", "                } else if d == best_d {\n                    tied = false;\n                }"),
    # toolbox.rules
    ("rules", "entry_at: one hop too many", "    while k < i {\n        let next = index_of_byte(list[at..len(list)], byte_of(';'));", "    while k <= i {\n        let next = index_of_byte(list[at..len(list)], byte_of(';'));"),
    ("rules", "entry_at: hops onto the separator", "        at = at + next + 1;\n        k = k + 1;\n    }\n    let next = index_of_byte(list[at..len(list)], byte_of(';'));", "        at = at + next;\n        k = k + 1;\n    }\n    let next = index_of_byte(list[at..len(list)], byte_of(';'));"),
    ("rules", "entry_at: entry 0 is empty", "    if i < 0 {\n        return list[0..0];\n    }", "    if i < 1 {\n        return list[0..0];\n    }"),
    ("rules", "entry_at: past the end answers the last entry", "        if next < 0 {\n            return list[0..0];\n        }\n        at = at + next + 1;\n        k = k + 1;", "        if next < 0 {\n            return list[at..len(list)];\n        }\n        at = at + next + 1;\n        k = k + 1;"),
    ("rules", "entry_at: the entry keeps its separator", "    return list[at..at + next];\n}\n\n// Field `n`", "    return list[at..at + next + 1];\n}\n\n// Field `n`"),
    ("rules", "field_of: the field index is off by one", "    var at = 0;\n    var seen = 1;\n    while seen < n {", "    var at = 0;\n    var seen = 2;\n    while seen < n {"),
    ("rules", "field_of: splits on the entry separator", "        let next = index_of_byte(whole[at..len(whole)], byte_of('|'));\n        if next < 0 {\n            return whole[0..0];\n        }", "        let next = index_of_byte(whole[at..len(whole)], byte_of(';'));\n        if next < 0 {\n            return whole[0..0];\n        }"),
    ("rules", "field_of: the last field loses a byte", "        return whole[at..len(whole)];\n    }\n    return whole[at..at + next];", "        return whole[at..len(whole) - 1];\n    }\n    return whole[at..at + next];"),
    ("rules", "scan_for: the last (empty) entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(list) {", "    var at = 0;\n    var i = 0;\n    while at < len(list) {"),
    ("rules", "scan_for: every other entry is skipped", "        at = end + 1;\n        i = i + 1;\n    }\n    return 0 - 1;\n}\n\npub fn count", "        at = end + 2;\n        i = i + 1;\n    }\n    return 0 - 1;\n}\n\npub fn count"),
    ("rules", "scan_for: the index does not advance", "        at = end + 1;\n        i = i + 1;\n    }\n    return 0 - 1;\n}\n\npub fn count", "        at = end + 1;\n    }\n    return 0 - 1;\n}\n\npub fn count"),
    ("rules", "scan_for: the last entry is not looked at", "    var at = 0;\n    var i = 0;\n    while at <= len(list) {", "    var at = 0;\n    var i = 0;\n    while index_of_byte(list[at..len(list)], byte_of(';')) >= 0 {"),
    ("rules", "find_in: a rule of the tool's own is numbered from 0", "    return count() + own;", "    return own;"),
    ("rules", "find_in: a repeat of a shared tag wins", "    if shared >= 0 || len(extra) == 0 {\n        return shared;\n    }", "    if len(extra) == 0 {\n        return shared;\n    }"),
    ("rules", "find_in: the tool's own rules are not looked at", "    let own = scan_for(extra, tag);", "    let own = 0 - 1;"),
    ("rules", "entry_in: a rule of the tool's own is read one off", "    return entry_at(extra, i - shared);", "    return entry_at(extra, i - shared + 1);"),
    ("rules", "find: reads the tag from the second field", "    return scan_for(catalogue(), tag);\n}\n\npub fn exit_at", "    return scan_for(catalogue(), tag) + 1;\n}\n\npub fn exit_at"),
    ("rules", "exit_in_entry: the exit is in the first field", "    let text = field_of(whole, 2);", "    let text = field_of(whole, 1);"),
    # toolbox.describe: the loops that read a table
    ("describe", "introspect: the help is the default", "        w = json.put_string(heap, w, cli.field_of(whole, 6));", "        w = json.put_string(heap, w, cli.field_of(whole, 5));"),
    ("describe", "introspect: the kind is the role", "        w = json.put_string(heap, w, cli.field_of(whole, 3));\n        w = json.put_key(heap, w, \"role\");", "        w = json.put_string(heap, w, cli.field_of(whole, 4));\n        w = json.put_key(heap, w, \"role\");"),
    ("describe", "introspect: every other flag is skipped", "        w = json.end_object(heap, w);\n        at = at + len(whole) + 1;\n        i = i + 1;", "        w = json.end_object(heap, w);\n        at = at + len(whole) + 2;\n        i = i + 1;"),
    ("describe", "introspect: the last flag is not written", "    let flag_count = cli.entries(tool.flags);\n    while i < flag_count {\n        // One pass", "    let flag_count = cli.entries(tool.flags) - 1;\n    while i < flag_count {\n        // One pass"),
    ("describe", "introspect: the flags are counted from the second", "    var i = 0;\n    var at = 0;\n    let flag_count = cli.entries(tool.flags);\n    while i < flag_count {\n        // One pass", "    var i = 1;\n    var at = 0;\n    let flag_count = cli.entries(tool.flags);\n    while i < flag_count {\n        // One pass"),
    ("describe", "introspect: an empty default is written as a string", "        let d = cli.field_of(whole, 5);\n        if len(d) == 0 {\n            w = json.put_null(heap, w);", "        let d = cli.field_of(whole, 5);\n        if len(d) == 1000 {\n            w = json.put_null(heap, w);"),
    ("describe", "skill: a flag that takes no value is shown with one", "        if !bytes.equal(cli.field_of(whole, 3), \"bool\") {", "        if true {"),
    ("describe", "skill: the short is the kind", "        let short = cli.field_of(whole, 2);\n        if len(short) > 0 {", "        let short = cli.field_of(whole, 3);\n        if len(short) > 0 {"),
    ("describe", "skill: every other flag is skipped", "        b = md(heap, b, \"\\n\");\n        at = at + len(whole) + 1;", "        b = md(heap, b, \"\\n\");\n        at = at + len(whole) + 2;"),
    ("describe", "skill: the last flag is not written", "    let flag_count = cli.entries(tool.flags);\n    while i < flag_count {\n        let whole = tool.flags[at..cli.entry_end(tool.flags, at)];\n        b = md", "    let flag_count = cli.entries(tool.flags) - 1;\n    while i < flag_count {\n        let whole = tool.flags[at..cli.entry_end(tool.flags, at)];\n        b = md"),
    ("describe", "emits: the last rule is not looked at", "    var at = 0;\n    while at <= len(tool.rules) {\n        let end = cli.entry_end(tool.rules, at);", "    var at = 0;\n    while cli.entry_end(tool.rules, at) < len(tool.rules) {\n        let end = cli.entry_end(tool.rules, at);"),
    ("describe", "emits: every other rule is skipped", "        at = end + 1;\n    }\n    return false;", "        at = end + 2;\n    }\n    return false;"),
    ("describe", "emits: a tool with no rules emits", "    if len(tool.rules) == 0 {\n        return false;\n    }", "    if len(tool.rules) == 0 {\n        return true;\n    }"),
    ("describe", "put_list: the last item is not written", "        while at <= len(list) {\n            let end = cli.entry_end(list, at);", "        while cli.entry_end(list, at) < len(list) {\n            let end = cli.entry_end(list, at);"),
    ("describe", "introspect: every other rule is skipped", "        let rule_end = cli.entry_end(tool.rules, from);\n        let tag = tool.rules[from..rule_end];\n        from = rule_end + 1;\n        let at = rules.find_in(tool.extra_rules, tag);\n        w = json.begin_object(heap, w);", "        let rule_end = cli.entry_end(tool.rules, from);\n        let tag = tool.rules[from..rule_end];\n        from = rule_end + 2;\n        let at = rules.find_in(tool.extra_rules, tag);\n        w = json.begin_object(heap, w);"),
    ("describe", "skill: every other rule is skipped", "        let rule_end = cli.entry_end(tool.rules, from);\n        let tag = tool.rules[from..rule_end];\n        from = rule_end + 1;\n        let at = rules.find_in(tool.extra_rules, tag);\n        b = md", "        let rule_end = cli.entry_end(tool.rules, from);\n        let tag = tool.rules[from..rule_end];\n        from = rule_end + 2;\n        let at = rules.find_in(tool.extra_rules, tag);\n        b = md"),
]

MUTANTS = [(w, "%s %s" % (w, n), *rest) for (w, n, *rest) in MUTANTS]

# Mutants that survive, and why. A name here that is killed fails `--check`: the proof is stale.
EQUIVALENT = {
}

# Gates, cheapest first; the first to fail kills.
GATES = [
    ("describe", ["test_cli_lookup.Describe"]),
    ("differential", ["test_cli_lookup.Differential"]),
    ("cost", ["test_cli_lookup.Cost"]),
]


def run_gates(tree):
    sources = sorted(str(p) for p in (tree / "contract").glob("*.cho"))
    probe = subprocess.run([COMPILER, "check", *sources, str(tree / "tests" / "extension" / "ext.cho"), "--std"], cwd=tree, capture_output=True, text=True, timeout=900)
    if probe.returncode:
        return "killed", "does not build: " + (probe.stdout + probe.stderr).strip().splitlines()[-1][:90]
    for gate, tests in GATES:
        p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-f", *tests], cwd=tree / "tests" / "conformance", capture_output=True, text=True, timeout=3600)
        if p.returncode:
            return "killed", gate
    return "SURVIVED", ""


def copy_tree():
    tmp = tempfile.mkdtemp(prefix="cli-mutants-")
    for d in ("contract", "tests"):
        shutil.copytree(ROOT / d, pathlib.Path(tmp) / d, ignore=shutil.ignore_patterns("__pycache__"))
    return pathlib.Path(tmp)


def one(m):
    which, name, old, new, *nth = m
    tree = copy_tree()
    try:
        path = tree / "contract" / (which + ".cho")
        text = path.read_text()
        if text.count(old) != 1:
            return name, "BAD SITE", "the site occurs %d times" % text.count(old)
        path.write_text(text.replace(old, new))
        verdict, why = run_gates(tree)
        return name, verdict, why
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check = "--check" in sys.argv[1:]
    chosen = [m for m in MUTANTS if check or not args or any(a in m[1] for a in args)]
    names = [m[1] for m in MUTANTS]
    if len(set(names)) != len(names):
        print("!! two mutants have the same name")
        return 1
    tree = copy_tree()
    try:
        verdict, why = run_gates(tree)
    finally:
        shutil.rmtree(tree, ignore_errors=True)
    print("unmutated:", "pass" if verdict == "SURVIVED" else "FAILS (%s)" % why, flush=True)
    if verdict != "SURVIVED":
        return 1
    jobs = int(os.environ.get("JOBS", "3"))
    bad = []
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for name, verdict, why in pool.map(one, chosen):
            expected = name in EQUIVALENT
            line = "%-9s %s  [%s]" % (verdict, name, why)
            print(line, flush=True)
            if verdict == "BAD SITE" or (verdict == "SURVIVED") != expected:
                bad.append(name)
    print("%d mutants, %d wrong (killed: %d, survived and proved equivalent: %d)" % (len(chosen), len(bad), len(chosen) - len(bad) - len([n for n in chosen if n[1] in EQUIVALENT]), len([n for n in chosen if n[1] in EQUIVALENT])))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
