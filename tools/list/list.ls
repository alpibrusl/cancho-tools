edition 6;

// `list` -- the entries beneath a directory, as NDJSON: `ls`, `find` and
// `tree` for an agent.
//
//     list [--root DIR] [--depth N] [--max-entries N] [--skip N] [--long]
//          [--format ndjson|text] [DIR...]
//
// lex-sys `docs/agent-toolbox.md` D15 row 6, on `docs/directory-listing.md`'s
// primitives (#222):
//
// * **Sorted, and the same every time.** Each directory's names are sorted
//   bytewise (`std.dirs.list`), and the walk is depth-first in that order,
//   so two runs over one tree print the same bytes.
// * **Bounded.** `--depth` (default 1: the directory's own entries) and
//   `--max-entries` (default 10000) cap the walk; a capped run says
//   `truncated` and answers `next: {"skip": N}`, which a following call
//   passes as `--skip N` to resume, as `seek`'s cursor does.
// * **No link is followed.** A link is listed with kind `link` and never
//   entered, under `--root` or not; beneath `--root` every directory is
//   entered with `dir_enter`, which refuses one (`toolbox.place`).
// * **No verbs.** There is no `-exec` and no `-delete`, the two things a
//   supervisor most wants absent from a lister's row: the row is reading
//   beneath directories and nothing else -- not even `file_read`, since a
//   lister never opens a file.
// * `--long` adds each entry's size and modification time (`dir_stat`,
//   which never follows a link either).
//
// A directory that cannot be read is an error record for its path, and the
// walk goes on, as `find` does; the exit code is the first error's.

import std.buffer;
import std.bytes;
import std.dirs;
import std.json;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve every DIR relative to this directory and refuse paths outside it;depth|d|nat|none|1|how many levels to descend: 1 is the directory's own entries (ceiling 64);max-entries|n|nat|none|10000|stop after this many entries in total, and report truncated with a next cursor (ceiling 1000000);skip||nat|none|0|skip this many entries before reporting any (the next cursor of a truncated run);long|l|bool|none||add each entry's size in bytes and modification time in seconds;format||choice:ndjson/text|none|ndjson|ndjson for a program, text (one path a line, a directory ending in /) for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "list", version: "0.1.0", summary: "The entries beneath a directory, sorted bytewise, depth-bounded and capped, as NDJSON records; links listed and never followed, and no verb but reading.", usage: "list [--root DIR] [--depth N] [--max-entries N] [--skip N] [--long] [--format ndjson|text] [DIR...]", output: "stream", schema: "list.v1", flags: flag_table(), operands: "DIR...|path-read|0||the directories to list, in order (none: the root, or else the working directory)", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.permission-denied", limits: "depth|1|64;max-entries|10000|1000000", reversibility: "reversible-cheap", stdin: "no", guarantees: "deterministic;idempotent" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn most_depth() -> [] int {
    return 64;
}

fn most_entries() -> [] int {
    return 1000000;
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

fn kind_name(kind: int) -> [] &static [byte] {
    if kind == dirs.kind_file() {
        return "file";
    }
    if kind == dirs.kind_directory() {
        return "directory";
    }
    if kind == dirs.kind_link() {
        return "link";
    }
    if kind == dirs.kind_other() {
        return "other";
    }
    return "unknown";
}

// What a walk has done so far.
struct Walk {
    // Entries seen, skipped ones included: the cursor counts these.
    seen: int,
    // Entries reported.
    reported: int,
    truncated: bool,
    broken: bool,
    // Error records already written.
    emitted: int,
}

// What one call's flags say.
struct Plan {
    depth: int,
    most: int,
    skip: int,
    long: bool,
    text_mode: bool,
}

fn sync[&h, &i, &e](heap: &!h Heap, io: &!i Io, errs: &e fail.Errors, w: Walk, text_mode: bool) -> [heap, io_write] Walk {
    var broken = w.broken;
    if !text_mode && !broken {
        if !out.error_records(heap, io, errs, w.emitted) {
            broken = true;
        }
    }
    return Walk { seen: w.seen, reported: w.reported, truncated: w.truncated, broken: broken, emitted: fail.count(errs) };
}

// `prefix/name`, or `name` when the prefix is `.`.
fn joined[&h, &p, &n](heap: &!h Heap, prefix: &p [byte], name: &n [byte]) -> [heap] buffer.Buffer {
    var b = buffer.empty(heap, len(prefix) + len(name) + 2);
    if !bytes.equal(prefix, ".") {
        b = buffer.append(heap, b, prefix);
        b = buffer.push(heap, b, byte_of('/'));
    }
    return buffer.append(heap, b, name);
}

// One entry's record (or line).
fn report[&h, &i, &p](heap: &!h Heap, io: &!i Io, plan: Plan, shown: &p [byte], kind: int, depth: int, size: int, mtime: int) -> [heap, io_write] bool {
    if plan.text_mode {
        var b = buffer.empty(heap, len(shown) + 32);
        if plan.long {
            b = buffer.push_nat(heap, b, size);
            b = buffer.push(heap, b, byte_of(9));
            b = buffer.push_nat(heap, b, mtime);
            b = buffer.push(heap, b, byte_of(9));
        }
        b = buffer.append(heap, b, shown);
        if kind == dirs.kind_directory() {
            b = buffer.push(heap, b, byte_of('/'));
        }
        return out.buffer_line(heap, io, b);
    }
    var w = json.writer(heap, 128 + len(shown));
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "entry");
    w = json.put_key(heap, w, "path");
    w = text.put(heap, w, shown);
    w = json.put_key(heap, w, "kind");
    w = json.put_string(heap, w, kind_name(kind));
    w = json.put_key(heap, w, "depth");
    w = json.put_int(heap, w, depth);
    if plan.long {
        w = json.put_key(heap, w, "size");
        w = json.put_int(heap, w, size);
        w = json.put_key(heap, w, "mtime");
        w = json.put_int(heap, w, mtime);
    }
    return out.close_line(heap, io, w);
}

// Walk `dir`, whose entries are shown under `prefix`, at `depth` (1 for an
// operand's own entries).
fn walk[&h, &i, &d, &p](heap: &!h Heap, io: &!i Io, plan: Plan, dir: &d Dir, prefix: &p [byte], depth: int, errs: fail.Errors, state: Walk) -> [heap, dir_read, io_write] (fail.Errors, Walk) {
    var e = errs;
    var w = state;
    // Never more names than the run could still report, plus one to know it
    // was cut.
    let names = dirs.list(heap, dir, plan.skip + plan.most + 1);
    borrow names as &n in {
        if dirs.failed(n) != 0 {
            e = fail.io_error(heap, e, dirs.failed(n), false, prefix);
        }
        var k = 0;
        while k < dirs.count(n) && !w.truncated && !w.broken {
            let name = dirs.name(n, k);
            var kind = dirs.kind(n, k);
            var size = 0;
            var mtime = 0;
            // A kind the listing did not know, or a long listing, asks
            // `dir_stat`, which never follows a link.
            if plan.long || kind == dirs.kind_unknown() {
                match dir_stat(dir, name) {
                    DirStat::Ok(found, bytes_held, seconds) => {
                        kind = found;
                        size = bytes_held;
                        mtime = seconds;
                    }
                    DirStat::Failed(reason) => {
                    }
                }
            }
            let shown = joined(heap, prefix, name);
            borrow shown as &s in {
                w = Walk { seen: w.seen + 1, reported: w.reported, truncated: w.truncated, broken: w.broken, emitted: w.emitted };
                if w.seen > plan.skip {
                    if w.reported >= plan.most {
                        w = Walk { seen: w.seen - 1, reported: w.reported, truncated: true, broken: w.broken, emitted: w.emitted };
                    } else {
                        let wrote = report(heap, io, plan, buffer.bytes(s), kind, depth, size, mtime);
                        w = Walk { seen: w.seen, reported: w.reported + 1, truncated: w.truncated, broken: !wrote, emitted: w.emitted };
                    }
                }
                if !w.truncated && !w.broken && kind == dirs.kind_directory() && depth < plan.depth {
                    match dir_enter(dir, name) {
                        DirOpened::Ok(opened) => {
                            var sub = opened;
                            borrow sub as &u in {
                                let (e2, w2) = walk(heap, io, plan, u, buffer.bytes(s), depth + 1, e, w);
                                e = e2;
                                w = w2;
                            }
                            dir_close(sub);
                        }
                        DirOpened::Failed(reason) => {
                            e = fail.io_error(heap, e, reason, false, buffer.bytes(s));
                        }
                    }
                }
                borrow e as &er in {
                    w = sync(heap, io, er, w, plan.text_mode);
                }
            }
            buffer.drop(heap, shown);
            k = k + 1;
        }
        if dirs.truncated(n) && !w.truncated {
            // More names than the run could report: the cursor resumes at the
            // first one not seen.
            w = Walk { seen: w.seen, reported: w.reported, truncated: true, broken: w.broken, emitted: w.emitted };
        }
    }
    dirs.drop(heap, names);
    return (e, w);
}

// List one operand: open it as a directory beneath the root and walk it.
fn operand[&h, &f, &i, &r, &s, &q](heap: &!h Heap, fs: &f Fs(""), io: &!i Io, plan: Plan, root: &r [byte], rel: &s [byte], full: &q [byte], errs: fail.Errors, state: Walk) -> [heap, fs_read(""), dir_read, io_write] (fail.Errors, Walk) {
    var e = errs;
    var w = state;
    match place.open_directory(fs, root, rel, full) {
        DirOpened::Ok(opened) => {
            var dir = opened;
            borrow dir as &d in {
                let (e2, w2) = walk(heap, io, plan, d, rel, 1, e, w);
                e = e2;
                w = w2;
            }
            dir_close(dir);
        }
        DirOpened::Failed(reason) => {
            e = fail.io_error(heap, e, reason, false, rel);
        }
    }
    borrow e as &er in {
        w = sync(heap, io, er, w, plan.text_mode);
    }
    return (e, w);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    let depth = cli.nat(args, parsed, table, "depth");
    let most = cli.nat(args, parsed, table, "max-entries");
    if depth < 1 || depth > most_depth() {
        e = flag_problem(heap, e, "args.bad-value", "--depth is from 1 to 64", "1 lists the directory's own entries", "--depth");
    }
    if most < 1 || most > most_entries() {
        e = flag_problem(heap, e, "args.bad-value", "--max-entries is from 1 to 1000000", "page with --skip instead", "--max-entries");
    }
    let plan = Plan { depth: depth, most: most, skip: cli.nat(args, parsed, table, "skip"), long: cli.has(parsed, table, "long"), text_mode: text_mode };
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    var w = Walk { seen: 0, reported: 0, truncated: false, broken: false, emitted: 0 };
    borrow e as &er in {
        w = sync(heap, io, er, w, text_mode);
    }
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    borrow root as &rr in {
        let r = buffer.bytes(rr);
        if !refused && cli.operand_count(parsed) == 0 {
            // No DIR: the root itself, or the working directory.
            var full: &static [byte] = ".";
            let (e2, w2) = operand(heap, fs, io, plan, r, ".", full, e, w);
            e = e2;
            w = w2;
        }
        var k = 0;
        while !refused && !w.broken && !w.truncated && k < cli.operand_count(parsed) {
            let (resolved, checked) = path.operand(heap, args, r, cli.operand_index(parsed, k), e);
            e = checked;
            borrow resolved as &rp in {
                if path.ok(rp) {
                    let (e2, w2) = operand(heap, fs, io, plan, r, path.shown(rp), path.full(rp), e, w);
                    e = e2;
                    w = w2;
                }
            }
            path.drop(heap, resolved);
            borrow e as &er in {
                w = sync(heap, io, er, w, text_mode);
            }
            k = k + 1;
        }
    }
    buffer.drop(heap, root);
    var status = 0;
    var broken = w.broken;
    borrow e as &er in {
        status = fail.exit_code(er);
        if text_mode {
            out.say_errors(io, "list", er);
        } else if !broken {
            var j = out.end_open(heap, "list", "list.v1", fail.count(er) == 0, fail.count(er) == 0 && !w.truncated);
            j = json.put_key(heap, j, "entries");
            j = json.put_int(heap, j, w.reported);
            j = json.put_key(heap, j, "errors");
            j = json.put_int(heap, j, fail.count(er));
            j = json.put_key(heap, j, "truncated");
            j = json.put_bool(heap, j, w.truncated);
            j = json.put_key(heap, j, "next");
            if w.truncated {
                j = json.begin_object(heap, j);
                j = json.put_key(heap, j, "skip");
                j = json.put_int(heap, j, w.seen);
                j = json.end_object(heap, j);
            } else {
                j = json.put_null(heap, j);
            }
            if !out.close_line(heap, io, j) {
                broken = true;
            }
        }
    }
    fail.drop(heap, e);
    if !broken && !out.flushed(io) {
        broken = true;
    }
    if broken {
        out.write_failed(io, "list");
        return 1;
    }
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, io_write, err_write] int {
    let which = cli.subcommand(args);
    if which != 0 {
        return describe.answer(heap, io, which, tool(), built());
    }
    let (parsed, e) = cli.parse(heap, args, flag_table(), fail.empty(heap));
    var status = 0;
    borrow parsed as &p in {
        status = body(heap, args, p, fs, io, e);
    }
    cli.drop(heap, parsed);
    return status;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock, signals } = split(world);
    // No foreign code, no network, no clock, no signals.
    release(ffi);
    release(net);
    release(clock);
    release(signals);
    var status = 0;
    borrow mut heap as &!h in {
        borrow args as &g in {
            borrow fs as &f in {
                borrow mut io as &!i in {
                    status = run(h, g, f, i);
                }
            }
        }
    }
    release(heap);
    release(args);
    release(fs);
    release(io);
    return status;
}
