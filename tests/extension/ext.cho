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
import toolbox.sort;

fn extra() -> [] &static [byte] {
    return "demo.ragged|8|sometimes|a row with the wrong number of fields;demo.other|3|never|something that is not there;path.empty|8|never|a repeat of a shared tag, which is ignored";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "ext", version: "0.0.1", summary: "A test tool.", usage: "ext [which]", output: "document", schema: "ext.v1", flags: "", operands: "", rules: "demo.ragged;demo.other;args.unknown-flag", extra_rules: extra(), limits: "", reversibility: "reversible-cheap", stdin: "no", guarantees: "" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: "{}", schema: "{}", compiler: "test" };
}

// A comparator with a context: the keys themselves, odd ones first.
fn odd_first[&t](keys: &t [int], a: int, b: int) -> [] bool {
    return keys[a] % 2 == 1 && keys[b] % 2 != 1;
}

// `ext sort|sort-desc|sort-odd N...`: the numbers' indices in sorted order, as data.
fn sorted_data[&h, &g](heap: &!h Heap, args: &g Args, how: int) -> [heap, args] buffer.Buffer {
    let n = arg_count(args) - 2;
    let keys = box_slice(heap, n + 1, 0);
    let order = box_slice(heap, n + 1, 0);
    let spare = box_slice(heap, n + 1, 0);
    var w = json.writer(heap, 64);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "order");
    w = json.begin_array(heap, w);
    borrow mut keys as &!kw in {
        borrow mut order as &!ow in {
            borrow mut spare as &!sw in {
                let k = contents(kw);
                let o = contents(ow);
                var i = 0;
                while i < n {
                    k[i] = cli.parse_nat(arg(args, i + 2));
                    i = i + 1;
                }
                sort.identity(o, n);
                if how == 2 {
                    sort.by(k, o, contents(sw), n, odd_first);
                } else {
                    sort.by_keys(k, o, contents(sw), n, how == 1);
                }
                i = 0;
                while i < n {
                    w = json.put_int(heap, w, o[i]);
                    i = i + 1;
                }
            }
        }
    }
    unbox_slice(heap, keys);
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    w = json.end_array(heap, w);
    w = json.end_object(heap, w);
    return json.finish(w);
}

// `ext choose` and `ext detail ARG`: the helpers of `toolbox.fail` that build a choose repair and a detail.
fn helper_error[&h, &g](heap: &!h Heap, e: fail.Errors, args: &g Args, which: &static [byte]) -> [heap, args] fail.Errors {
    var w = fail.open_in(heap, extra(), "demo.ragged", "row 3 has 2 fields, not 3", "fix the row");
    if bytes.equal(which, "choose") {
        w = fail.choose_open(heap, w);
        w = fail.choose_option_replacing(heap, w, args, 0, "one");
        w = fail.choose_option_open(heap, w);
        w = fail.choose_arg(heap, w, "ext");
        w = fail.choose_arg(heap, w, "two \"quoted\"");
        w = fail.choose_option_close(heap, w);
        w = fail.choose_close(heap, w);
        w = fail.detail_open(heap, w);
    } else {
        w = fail.no_repair(heap, w);
        w = fail.detail_open(heap, w);
        w = fail.detail_str(heap, w, "name", "plain");
        w = fail.detail_int(heap, w, "row", 3);
        w = fail.detail_int(heap, w, "negative", 0 - 7);
        w = fail.detail_bool(heap, w, "ragged", true);
        var shown: &static [byte] = "";
        if arg_count(args) > 2 {
            shown = arg(args, 2);
        }
        w = fail.detail_text(heap, w, "path", shown);
    }
    return fail.add(heap, e, w);
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
    if arg_count(args) >= 2 && (bytes.equal(arg(args, 1), "choose") || bytes.equal(arg(args, 1), "detail")) {
        e = helper_error(heap, e, args, arg(args, 1));
        var st = 0;
        borrow e as &er in {
            st = out.respond(heap, io, "ext", "ext.v1", "0.0.1", "{}", "", er, false, "", 0);
        }
        fail.drop(heap, e);
        return st;
    }
    if arg_count(args) >= 2 && (bytes.equal(arg(args, 1), "sort") || bytes.equal(arg(args, 1), "sort-desc") || bytes.equal(arg(args, 1), "sort-odd")) {
        var how = 0;
        if bytes.equal(arg(args, 1), "sort-desc") {
            how = 1;
        } else if bytes.equal(arg(args, 1), "sort-odd") {
            how = 2;
        }
        let data = sorted_data(heap, args, how);
        var st = 0;
        borrow data as &dr in {
            borrow e as &er in {
                st = out.respond(heap, io, "ext", "ext.v1", "0.0.1", buffer.bytes(dr), "", er, false, "", 0);
            }
        }
        buffer.drop(heap, data);
        fail.drop(heap, e);
        return st;
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
