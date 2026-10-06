edition 5;

module toolbox.describe;

// `toolbox.describe` -- `tool introspect` and `tool skill`, generated from
// the same tables that drive the tool (D11).
//
// Nothing here is written twice: the flags come from the table the parser
// reads (`toolbox.cli`), the rules and their exit codes from the shared
// catalogue (`toolbox.rules`), and the authority from the compiler's own
// report, embedded by the build (D12, `scripts/manifest.py`). The ACLI
// SDK's skill generator is not used: it hard-codes an exit-code table that
// does not describe a tool (lex-sys `docs/agent-cli.md` §3).

import std.buffer;
import std.bytes;
import std.io;
import std.json;
import toolbox.cli;
import toolbox.out;
import toolbox.rules;

// What a tool says about itself, beside its flag table. Every field is a
// literal in the tool's source; the lists are `;`-separated, their fields
// `|`-separated, as in the flag table.
pub struct Tool {
    name: &static [byte],
    version: &static [byte],
    summary: &static [byte],
    usage: &static [byte],
    // `document` or `stream`.
    output: &static [byte],
    schema: &static [byte],
    flags: &static [byte],
    // `name|role|min|max|help`, in order; `max` empty is unbounded.
    operands: &static [byte],
    // The tags this tool can emit.
    rules: &static [byte],
    // `name|default|ceiling`.
    limits: &static [byte],
    // lex-os's reversibility class (D10).
    reversibility: &static [byte],
    // Whether the tool reads standard input, for the skill's prose.
    stdin: &static [byte],
    // The guarantees this tool makes, `;`-separated, from `guarantee_keys`:
    // each one named is `true` in `introspect` and backed by a conformance
    // gate (tests/conformance/test_guarantees.py), every other one `false`.
    guarantees: &static [byte],
}

// What the build embeds (D12): the compiler's authority report for this
// tool, the tool's JSON Schema, and the compiler revision.
pub struct Built {
    authority: &static [byte],
    schema: &static [byte],
    compiler: &static [byte],
}

fn put_list[&h](heap: &!h Heap, w: json.Writer, list: &static [byte]) -> [heap] json.Writer {
    var o = json.begin_array(heap, w);
    if len(list) > 0 {
        let n = bytes.count_byte(list, ';') + 1;
        var i = 1;
        while i <= n {
            o = json.put_string(heap, o, bytes.field(list, ';', i));
            i = i + 1;
        }
    }
    return json.end_array(heap, o);
}

fn count(list: &static [byte]) -> [] int {
    if len(list) == 0 {
        return 0;
    }
    return bytes.count_byte(list, ';') + 1;
}

fn item(list: &static [byte], i: int, f: int) -> [] &static [byte] {
    return bytes.field(bytes.field(list, ';', i + 1), '|', f);
}

// Every guarantee `introspect` reports, in order, with what it means.
fn guarantee_keys() -> [] &static [byte] {
    return "deterministic|the same input gives byte-identical output, through a pipe, a file or a terminal (M2);idempotent|running it twice leaves the same state as once (M7 for the writers, and a reader changes nothing);atomic|a reader never sees a partial target: the new content is a synced temporary renamed over it (M7);requires_precondition|it refuses to change a file without a stated belief about it: --create, --if-sha256 or --expect (M3);dry_run|--dry-run reports what an apply would do and makes no mutating system call (M7, under strace);bounded_memory|peak memory does not grow with the input's size (M9)";
}

// Whether `tool` names guarantee `key`.
fn guarantees_has(tool: Tool, key: &static [byte]) -> [] bool {
    var i = 0;
    while i < count(tool.guarantees) {
        if bytes.equal(item(tool.guarantees, i, 1), key) {
            return true;
        }
        i = i + 1;
    }
    return false;
}

// Whether this tool can exit with `code`: 0 always, 9 for a tool that
// dry-runs, otherwise a code one of its rules maps to.
fn emits(tool: Tool, code: int) -> [] bool {
    if code == 0 {
        return true;
    }
    if code == 9 {
        return bytes.find(tool.flags, "dry-run|") >= 0;
    }
    var i = 0;
    while i < count(tool.rules) {
        if rules.exit_of(bytes.field(tool.rules, ';', i + 1)) == code {
            return true;
        }
        i = i + 1;
    }
    return false;
}

pub fn introspect[&h](heap: &!h Heap, tool: Tool, built: Built) -> [heap] buffer.Buffer {
    var w = json.writer(heap, 8192);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "tool");
    w = json.put_string(heap, w, tool.name);
    w = json.put_key(heap, w, "version");
    w = json.put_string(heap, w, tool.version);
    w = json.put_key(heap, w, "compiler");
    w = json.put_string(heap, w, built.compiler);
    w = json.put_key(heap, w, "summary");
    w = json.put_string(heap, w, tool.summary);
    w = json.put_key(heap, w, "usage");
    w = json.put_string(heap, w, tool.usage);
    w = json.put_key(heap, w, "output");
    w = json.put_string(heap, w, tool.output);
    w = json.put_key(heap, w, "schemas");
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, tool.schema);
    w = json.put_fragment(heap, w, built.schema);
    w = json.end_object(heap, w);

    w = json.put_key(heap, w, "flags");
    w = json.begin_array(heap, w);
    var i = 0;
    while i < cli.entries(tool.flags) {
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "name");
        var dashed = buffer.empty(heap, 32);
        dashed = buffer.append(heap, dashed, "--");
        dashed = buffer.append(heap, dashed, cli.name_of(tool.flags, i));
        borrow dashed as &d in {
            w = json.put_string(heap, w, buffer.bytes(d));
        }
        buffer.drop(heap, dashed);
        w = json.put_key(heap, w, "short");
        let short = cli.short_of(tool.flags, i);
        if len(short) == 0 {
            w = json.put_null(heap, w);
        } else {
            var s = buffer.empty(heap, 2);
            s = buffer.push(heap, s, byte_of('-'));
            s = buffer.append(heap, s, short);
            borrow s as &d in {
                w = json.put_string(heap, w, buffer.bytes(d));
            }
            buffer.drop(heap, s);
        }
        w = json.put_key(heap, w, "kind");
        w = json.put_string(heap, w, cli.kind_of(tool.flags, i));
        w = json.put_key(heap, w, "role");
        w = json.put_string(heap, w, cli.role_of(tool.flags, i));
        w = json.put_key(heap, w, "default");
        let d = cli.default_of(tool.flags, i);
        if len(d) == 0 {
            w = json.put_null(heap, w);
        } else {
            w = json.put_string(heap, w, d);
        }
        w = json.put_key(heap, w, "help");
        w = json.put_string(heap, w, cli.help_of(tool.flags, i));
        w = json.end_object(heap, w);
        i = i + 1;
    }
    w = json.end_array(heap, w);

    w = json.put_key(heap, w, "operands");
    w = json.begin_array(heap, w);
    i = 0;
    while i < count(tool.operands) {
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "name");
        w = json.put_string(heap, w, item(tool.operands, i, 1));
        w = json.put_key(heap, w, "role");
        w = json.put_string(heap, w, item(tool.operands, i, 2));
        // How many times it may be given: what the tool's parser accepts,
        // and what a schema derived from this needs (lexsys-tools#10,
        // docs/mcp.md §3). `max` empty is unbounded, `null` here.
        w = json.put_key(heap, w, "min");
        w = json.put_int(heap, w, cli.parse_nat(item(tool.operands, i, 3)));
        w = json.put_key(heap, w, "max");
        let most = item(tool.operands, i, 4);
        if len(most) == 0 {
            w = json.put_null(heap, w);
        } else {
            w = json.put_int(heap, w, cli.parse_nat(most));
        }
        w = json.put_key(heap, w, "help");
        w = json.put_string(heap, w, item(tool.operands, i, 5));
        w = json.end_object(heap, w);
        i = i + 1;
    }
    w = json.end_array(heap, w);

    w = json.put_key(heap, w, "exit_codes");
    w = json.begin_array(heap, w);
    var code = 0;
    while code <= 9 {
        if emits(tool, code) {
            w = json.begin_object(heap, w);
            w = json.put_key(heap, w, "code");
            w = json.put_int(heap, w, code);
            w = json.put_key(heap, w, "name");
            w = json.put_string(heap, w, rules.code_name(code));
            w = json.put_key(heap, w, "meaning");
            w = json.put_string(heap, w, rules.code_meaning(code));
            w = json.end_object(heap, w);
        }
        code = code + 1;
    }
    w = json.end_array(heap, w);

    w = json.put_key(heap, w, "rules");
    w = json.begin_array(heap, w);
    i = 0;
    while i < count(tool.rules) {
        let tag = bytes.field(tool.rules, ';', i + 1);
        let at = rules.find(tag);
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "rule");
        w = json.put_string(heap, w, tag);
        w = json.put_key(heap, w, "exit");
        w = json.put_int(heap, w, rules.exit_of(tag));
        w = json.put_key(heap, w, "repairable");
        if at >= 0 {
            w = json.put_string(heap, w, rules.repairable_of(at));
        } else {
            w = json.put_string(heap, w, "never");
        }
        w = json.put_key(heap, w, "summary");
        if at >= 0 {
            w = json.put_string(heap, w, rules.summary_of(at));
        } else {
            w = json.put_string(heap, w, "");
        }
        w = json.end_object(heap, w);
        i = i + 1;
    }
    w = json.end_array(heap, w);

    w = json.put_key(heap, w, "limits");
    w = json.begin_array(heap, w);
    i = 0;
    while i < count(tool.limits) {
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "name");
        w = json.put_string(heap, w, item(tool.limits, i, 1));
        w = json.put_key(heap, w, "default");
        w = json.put_int(heap, w, cli.parse_nat(item(tool.limits, i, 2)));
        w = json.put_key(heap, w, "ceiling");
        w = json.put_int(heap, w, cli.parse_nat(item(tool.limits, i, 3)));
        w = json.end_object(heap, w);
        i = i + 1;
    }
    w = json.end_array(heap, w);

    w = json.put_key(heap, w, "confinement");
    w = json.put_string(heap, w, "beneath");
    w = json.put_key(heap, w, "confinement_note");
    w = json.put_string(heap, w, "with --root, paths are checked lexically against it and then opened beneath it one component at a time, following no symbolic link (path.symlink), so nothing outside the root is reachable through one; without it, a reader opens the path as given, links and all, and a writer still follows no link in the file's own name");
    w = json.put_key(heap, w, "reads_environment");
    w = json.put_bool(heap, w, false);
    w = json.put_key(heap, w, "reads_clock");
    w = json.put_bool(heap, w, false);
    w = json.put_key(heap, w, "integers_only");
    w = json.put_bool(heap, w, true);
    w = json.put_key(heap, w, "authority");
    w = json.put_fragment(heap, w, built.authority);
    w = json.put_key(heap, w, "not_narrowable");
    w = put_list(heap, w, "path extent: a path from the command line is checked at run time, so the row says fs_read(\"\");what a symlink reaches;what standard output carries;resource use beyond the tool's own caps (memory, wall time);per-invocation behaviour such as --dry-run, which the row cannot see");
    w = json.put_key(heap, w, "reversibility");
    w = json.put_string(heap, w, tool.reversibility);
    w = json.put_key(heap, w, "guarantees");
    w = json.begin_object(heap, w);
    var g = 0;
    while g < count(guarantee_keys()) {
        w = json.put_key(heap, w, item(guarantee_keys(), g, 1));
        w = json.put_bool(heap, w, guarantees_has(tool, item(guarantee_keys(), g, 1)));
        g = g + 1;
    }
    w = json.put_key(heap, w, "concurrency");
    if guarantees_has(tool, "atomic") {
        w = json.put_string(heap, w, "locked: of writers racing with the same precondition, exactly one wins and the others are refused (M7)");
    } else {
        w = json.put_string(heap, w, "read-only: it never writes, so any number may run at once");
    }
    w = json.end_object(heap, w);
    w = json.put_key(heap, w, "evidence");
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "offline");
    w = put_list(heap, w, "M1 schema conformance;M2 determinism;M3 rule coverage and hint soundness;M4 fault injection;M5 differential against GNU;M6 authority;M7 mutation behaviour;M8 confinement;M9 memory flatness");
    w = json.put_key(heap, w, "offline_note");
    w = json.put_string(heap, w, "each gate is a test in lexsys-tools/tests/conformance, run by CI on the commit this binary was built from");
    w = json.put_key(heap, w, "agent_in_the_loop");
    w = json.put_string(heap, w, "not run: no claim that this tool is more agent-friendly than the incumbents is made (lex-sys docs/agent-toolbox.md section 7.2)");
    w = json.end_object(heap, w);
    w = json.end_object(heap, w);
    return json.finish(w);
}

fn md[&h](heap: &!h Heap, b: buffer.Buffer, text: &static [byte]) -> [heap] buffer.Buffer {
    return buffer.append(heap, b, text);
}

// The agentskills.io `SKILL.md`, from the same data.
pub fn skill[&h](heap: &!h Heap, tool: Tool, built: Built) -> [heap] buffer.Buffer {
    var b = buffer.empty(heap, 4096);
    b = md(heap, b, "---\nname: ");
    b = md(heap, b, tool.name);
    b = md(heap, b, "\ndescription: ");
    b = md(heap, b, tool.summary);
    b = md(heap, b, "\n---\n\n# ");
    b = md(heap, b, tool.name);
    b = md(heap, b, " ");
    b = md(heap, b, tool.version);
    b = md(heap, b, "\n\n");
    b = md(heap, b, tool.summary);
    b = md(heap, b, "\n\n```\n");
    b = md(heap, b, tool.usage);
    b = md(heap, b, "\n```\n\n## Output\n\n");
    if bytes.equal(tool.output, "stream") {
        b = md(heap, b, "NDJSON: one JSON object per line, schema `");
        b = md(heap, b, tool.schema);
        b = md(heap, b, "`. The last line is a record with `\"type\":\"end\"`; **a stream without one was cut short and must be treated as truncated**. `complete` is false when the tool stopped early. Errors are records with `\"type\":\"error\"`.\n");
    } else {
        b = md(heap, b, "One JSON object on one line, schema `");
        b = md(heap, b, tool.schema);
        b = md(heap, b, "`: `{ok, command, schema, data, error, errors, meta}`. `ok` is false exactly when `errors` is non-empty; `error` is the first.\n");
    }
    b = md(heap, b, "\nAn error is `{code, rule, message, hint, repair, detail}`. Match on `rule`, not on `message`. When `repair` is `{\"kind\":\"retry\",\"argv\":[...]}` the argv can be run as it stands; it never widens what the tool may touch.\n\n`--format text` is for a person and is lossy; do not parse it.\n\n## Flags\n\n");
    var i = 0;
    while i < cli.entries(tool.flags) {
        b = md(heap, b, "- `--");
        b = md(heap, b, cli.name_of(tool.flags, i));
        if cli.takes_value(tool.flags, i) {
            b = md(heap, b, " <");
            b = md(heap, b, cli.kind_of(tool.flags, i));
            b = md(heap, b, ">");
        }
        b = md(heap, b, "`");
        let short = cli.short_of(tool.flags, i);
        if len(short) > 0 {
            b = md(heap, b, " (`-");
            b = md(heap, b, short);
            b = md(heap, b, "`)");
        }
        b = md(heap, b, ": ");
        b = md(heap, b, cli.help_of(tool.flags, i));
        let d = cli.default_of(tool.flags, i);
        if len(d) > 0 {
            b = md(heap, b, " Default `");
            b = md(heap, b, d);
            b = md(heap, b, "`.");
        }
        b = md(heap, b, "\n");
        i = i + 1;
    }
    b = md(heap, b, "\nAn unknown flag is refused (`args.unknown-flag`), never ignored. POSIX spellings are not promised.\n\n## Guarantees\n\n");
    var g = 0;
    while g < count(guarantee_keys()) {
        if guarantees_has(tool, item(guarantee_keys(), g, 1)) {
            b = md(heap, b, "- **");
            b = md(heap, b, item(guarantee_keys(), g, 1));
            b = md(heap, b, "**: ");
            b = md(heap, b, item(guarantee_keys(), g, 2));
            b = md(heap, b, "\n");
        }
        g = g + 1;
    }
    if guarantees_has(tool, "atomic") {
        b = md(heap, b, "- **concurrency**: locked; of writers racing with the same precondition, exactly one wins and the others are refused, so re-read and retry.\n");
    } else {
        b = md(heap, b, "- **concurrency**: read-only; it never writes.\n");
    }
    b = md(heap, b, "\n## Exit codes\n\n");
    var code = 0;
    while code <= 9 {
        if emits(tool, code) {
            b = md(heap, b, "- `");
            b = buffer.push_nat(heap, b, code);
            b = md(heap, b, "` ");
            b = md(heap, b, rules.code_name(code));
            b = md(heap, b, ": ");
            b = md(heap, b, rules.code_meaning(code));
            b = md(heap, b, "\n");
        }
        code = code + 1;
    }
    b = md(heap, b, "- `132`: the tool trapped. A bug in the tool; there is no JSON.\n- `141`: the reader closed the pipe.\n\n## Rules\n\n");
    i = 0;
    while i < count(tool.rules) {
        let tag = bytes.field(tool.rules, ';', i + 1);
        let at = rules.find(tag);
        b = md(heap, b, "- `");
        b = md(heap, b, tag);
        b = md(heap, b, "` (exit ");
        b = buffer.push_nat(heap, b, rules.exit_of(tag));
        b = md(heap, b, ")");
        if at >= 0 {
            b = md(heap, b, ": ");
            b = md(heap, b, rules.summary_of(at));
        }
        b = md(heap, b, "\n");
        i = i + 1;
    }
    b = md(heap, b, "\n## Authority\n\nWhat the compiler says this program can reach (`lex-sys authority`), embedded at build time:\n\n```json\n");
    b = md(heap, b, built.authority);
    b = md(heap, b, "\n```\n\nThe row names *kinds* of access, not *extent*: a path from the command line is checked at run time, so it reads `fs_read(\"\")`. Symlinks are followed.\n");
    return b;
}

// Answer a subcommand (1 introspect, 2 skill) on standard output; the exit
// code: 0, or 1 when the write fell short.
pub fn answer[&h, &i](heap: &!h Heap, io: &!i Io, which: int, tool: Tool, built: Built) -> [heap, io_write] int {
    var b = buffer.empty(heap, 1);
    buffer.drop(heap, b);
    if which == 1 {
        b = introspect(heap, tool, built);
        let ok = out.buffer_line(heap, io, b);
        if ok {
            return 0;
        }
        return 1;
    }
    b = skill(heap, tool, built);
    var ok = false;
    borrow b as &r in {
        ok = out.emit(io, buffer.bytes(r));
    }
    buffer.drop(heap, b);
    if ok {
        return 0;
    }
    return 1;
}
