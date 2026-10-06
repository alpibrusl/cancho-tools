edition 6;

// A tool with a rule of its own, for `tests/conformance/test_extension.py`: the exit
// code, the code name, `introspect` and the skill all take it from `extra_rules`.

import std.buffer;
import std.bytes;
import std.json;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.out;
import toolbox.rules;

fn extra() -> [] &static [byte] {
    return "demo.ragged|8|sometimes|a row with the wrong number of fields;demo.other|3|never|something that is not there;path.empty|8|never|a repeat of a shared tag, which is ignored";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "ext", version: "0.0.1", summary: "A test tool.", usage: "ext [which]", output: "document", schema: "ext.v1", flags: "", operands: "", rules: "demo.ragged;demo.other;args.unknown-flag", extra_rules: extra(), limits: "", reversibility: "reversible-cheap", stdin: "no", guarantees: "" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: "{}", schema: "{}", compiler: "test" };
}

fn run[&h, &g, &i](heap: &!h Heap, args: &g Args, io: &!i Io) -> [heap, args, io_write, err_write] int {
    let which = cli.subcommand(args);
    if which != 0 {
        return describe.answer(heap, io, which, tool(), built());
    }
    var e = fail.empty_in(heap, extra());
    // `ext shared`: a shared tag first. `extra` repeats it with another exit, which is ignored.
    if arg_count(args) == 2 && bytes.equal(arg(args, 1), "shared") {
        e = fail.simple(heap, e, "path.empty", "an empty path", "", "path", "");
    }
    // A rule of the tool's own, then a shared one: the first decides the exit.
    var w = fail.open_in(heap, extra(), "demo.ragged", "row 3 has 2 fields, not 3", "fix the row");
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    e = fail.add(heap, e, w);
    if arg_count(args) < 2 {
        e = fail.simple(heap, e, "path.empty", "an empty path", "", "path", "");
    }
    var status = 0;
    borrow e as &er in {
        status = out.respond(heap, io, "ext", "ext.v1", "0.0.1", "{}", "", er, false, "", 0);
    }
    fail.drop(heap, e);
    return status;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock, signals } = split(world);
    release(ffi);
    release(fs);
    release(net);
    release(clock);
    release(signals);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            borrow mut io as &!i in {
                status = run(h, g, i);
            }
        }
    }
    release(heap);
    release(args);
    release(io);
    return status;
}
