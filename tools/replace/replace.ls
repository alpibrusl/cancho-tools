edition 6;

// `replace` -- replace exact text in a file, atomically, only when it
// occurs as often as the caller says.
//
//     replace [--root DIR] --old TEXT --new TEXT [--expect N] [--if-sha256 HEX]
//             [--dry-run] [--max-bytes N] [--max-diff-lines N]
//             [--format json|text] PATH
//
// lex-sys `docs/agent-toolbox.md` D10 and D15 row 2, beside `write`. The
// statement of belief here is `--expect` (default 1): `--old` must occur
// exactly that many times, or nothing is written and the answer is
// `precondition.count-mismatch` with the count found. `--if-sha256` adds
// the whole-file check when the caller has it.
//
// **Idempotent.** When `--old` no longer occurs and `--new` occurs exactly
// `--expect` times, the replacement is taken to have been applied already:
// `changed: false`, exit 0, nothing written. That is a judgement from the
// text, not a record of a previous run -- a file that came to hold `--new`
// some other way answers the same -- and it is checked before
// `--if-sha256`, because a retried replace that did land changed the hash.
//
// The write is `write`'s: under the sidecar lock, a temporary synced and
// renamed over the file (`toolbox.atomic`). The file is held whole, so
// `--max-bytes` bounds it.
//
// The answer carries `diff`, the lines that change (`toolbox.changes`), with
// `--dry-run` in `planned_actions` too, so an agent sees what a replace
// does before it does it; `--max-diff-lines` bounds it.

import std.buffer;
import std.bytes;
import std.json;
import toolbox.atomic;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.diff;
import toolbox.fail;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve PATH relative to this directory and refuse paths outside it;old||text|none||the exact bytes to find (not a pattern);new||any|none||the bytes to put in their place (may be empty);expect||nat|none|1|how many times --old must occur, any other count writes nothing;if-sha256||hex64|guard||the file's current content must also hash to this SHA-256;dry-run||bool|guard||check everything and report planned_actions, exit 9, write nothing;max-bytes||nat|none|67108864|the largest file this will hold (ceiling 1073741824);max-diff-lines||nat|none|200|the most changed lines the diff shows (ceiling 100000);format||choice:json/text|none|json|json for a program, text for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "replace", version: "0.1.0", summary: "Replace exact text in a file atomically, only when it occurs --expect times (and, with --if-sha256, only when the file is what the caller read); idempotent, locked, with --dry-run.", usage: "replace [--root DIR] --old TEXT --new TEXT [--expect N] [--if-sha256 HEX] [--dry-run] [--max-bytes N] [--max-diff-lines N] [--format json|text] PATH", output: "document", schema: "replace.v1", flags: flag_table(), operands: "PATH|path-write|1|1|the file to edit", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.required-flag;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;io.write-failed;limit.input-too-large;precondition.hash-mismatch;precondition.count-mismatch;conflict.locked", extra_rules: "", limits: "max-bytes|67108864|1073741824;max-diff-lines|200|100000", reversibility: "irreversible-bounded", stdin: "no", guarantees: "deterministic;idempotent;atomic;requires_precondition;dry_run" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn ceiling() -> [] int {
    return 1073741824;
}

fn diff_ceiling() -> [] int {
    return 100000;
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

struct Outcome {
    changed: bool,
    planned: bool,
    replacements: int,
    bytes: int,
    diff_truncated: bool,
}

// The hashes before and after, and the diff between, kept for the answer.
res struct Hashes {
    before: buffer.Buffer,
    after: buffer.Buffer,
    changes: buffer.Buffer,
}

fn no_hashes[&h](heap: &!h Heap) -> [heap] Hashes {
    var none = buffer.empty(heap, 2);
    none = buffer.append(heap, none, "[]");
    return Hashes { before: buffer.empty(heap, 1), after: buffer.empty(heap, 1), changes: none };
}

fn drop_hashes[&h](heap: &!h Heap, x: Hashes) -> [heap] int {
    let Hashes { before, after, changes } = x;
    buffer.drop(heap, before);
    buffer.drop(heap, changes);
    return buffer.drop(heap, after);
}

// Read, decide, write. Called with the lock held, or for a dry run
// without one.
fn apply[&h, &g, &p, &f, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, dir: &f Dir, name: &s [byte], shown: &s [byte], errs: fail.Errors) -> [heap, args, dir_read, file_read, dir_write, file_write] (fail.Errors, Outcome, Hashes) {
    let table = flag_table();
    let old = cli.text(args, parsed, table, "old");
    let new = cli.text(args, parsed, table, "new");
    let expect = cli.nat(args, parsed, table, "expect");
    var e = errs;
    var o = Outcome { changed: false, planned: false, replacements: 0, bytes: 0, diff_truncated: false };
    var hashes = no_hashes(heap);
    let (current, errno, too_big) = atomic.read_all(heap, dir_open_read(dir, name), cli.nat(args, parsed, table, "max-bytes"), buffer.empty(heap, 4096));
    if errno != 0 {
        e = fail.io_error(heap, e, errno, false, shown);
    } else if too_big {
        e = fail.simple(heap, e, "limit.input-too-large", "the file is larger than --max-bytes", "raise --max-bytes, up to 1073741824", "path", shown);
    } else {
        borrow current as &c in {
            let have = buffer.bytes(c);
            let Hashes { before, after, changes } = hashes;
            buffer.drop(heap, before);
            var listed = changes;
            let had = atomic.sha256_hex(heap, have);
            let found = atomic.occurrences(have, old);
            var go = false;
            var next = after;
            borrow had as &b in {
                if found == 0 && expect > 0 && atomic.occurrences(have, new) == expect && !bytes.equal(old, new) {
                    // Already applied (see the header).
                    buffer.drop(heap, next);
                    next = buffer.empty(heap, 64);
                    next = buffer.append(heap, next, buffer.bytes(b));
                    o = Outcome { changed: false, planned: false, replacements: 0, bytes: len(have), diff_truncated: false };
                } else if cli.has(parsed, table, "if-sha256") && !bytes.equal(buffer.bytes(b), cli.text(args, parsed, table, "if-sha256")) {
                    var w = fail.open(heap, "precondition.hash-mismatch", "the file does not hold the content --if-sha256 named", "re-read the file, then decide");
                    w = fail.repair_none(heap, w, "the file changed since it was read; re-read, then decide");
                    w = fail.detail_open(heap, w);
                    w = json.put_key(heap, w, "path");
                    w = text.put(heap, w, shown);
                    w = json.put_key(heap, w, "expected_sha256");
                    w = json.put_string(heap, w, cli.text(args, parsed, table, "if-sha256"));
                    w = json.put_key(heap, w, "actual_sha256");
                    w = json.put_string(heap, w, buffer.bytes(b));
                    e = fail.add(heap, e, w);
                } else if found != expect {
                    var w = fail.open(heap, "precondition.count-mismatch", "--old occurs a different number of times than --expect says", "make --old longer so it is unique, or pass --expect with the count found");
                    w = fail.repair_none(heap, w, "which occurrences to change is the caller's decision");
                    w = fail.detail_open(heap, w);
                    w = json.put_key(heap, w, "path");
                    w = text.put(heap, w, shown);
                    w = json.put_key(heap, w, "expected");
                    w = json.put_int(heap, w, expect);
                    w = json.put_key(heap, w, "found");
                    w = json.put_int(heap, w, found);
                    e = fail.add(heap, e, w);
                } else {
                    go = true;
                }
            }
            if go {
                let result = atomic.replaced(heap, have, old, new);
                borrow result as &r in {
                    let content = buffer.bytes(r);
                    buffer.drop(heap, next);
                    next = atomic.sha256_hex(heap, content);
                    let (lines, cut) = diff.hunks(heap, have, content, cli.nat(args, parsed, table, "max-diff-lines"));
                    buffer.drop(heap, listed);
                    listed = lines;
                    o = Outcome { changed: false, planned: false, replacements: found, bytes: len(content), diff_truncated: cut };
                    if cli.has(parsed, table, "dry-run") {
                        o = Outcome { changed: false, planned: true, replacements: found, bytes: len(content), diff_truncated: cut };
                    } else {
                        var failed_errno = 0;
                        var step = 0;
                        borrow next as &a in {
                            let temp = atomic.temp_path(heap, name, buffer.bytes(a));
                            borrow temp as &t in {
                                let (errno2, step2) = atomic.replace(dir, name, buffer.bytes(t), content);
                                failed_errno = errno2;
                                step = step2;
                            }
                            buffer.drop(heap, temp);
                        }
                        if failed_errno == 0 {
                            atomic.sync_parent(dir);
                            o = Outcome { changed: true, planned: false, replacements: found, bytes: len(content), diff_truncated: cut };
                        } else if step == 1 && failed_errno == 17 {
                            e = fail.simple(heap, e, "conflict.locked", "the temporary file could not be created; another writer may hold it", "retry later", "path", shown);
                        } else {
                            var w = fail.open(heap, fail.io_rule(failed_errno, true), "the replacement could not be written; the file is as it was", "");
                            w = fail.no_repair(heap, w);
                            w = fail.detail_open(heap, w);
                            w = json.put_key(heap, w, "path");
                            w = text.put(heap, w, shown);
                            w = json.put_key(heap, w, "errno");
                            w = json.put_int(heap, w, failed_errno);
                            e = fail.add(heap, e, w);
                        }
                    }
                }
                buffer.drop(heap, result);
            }
            hashes = Hashes { before: had, after: next, changes: listed };
        }
    }
    buffer.drop(heap, current);
    return (e, o, hashes);
}

fn locked[&h, &g, &p, &f, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, dir: &f Dir, name: &s [byte], shown: &s [byte], errs: fail.Errors) -> [heap, args, dir_read, file_read, dir_write, file_write] (fail.Errors, Outcome, Hashes) {
    if cli.has(parsed, flag_table(), "dry-run") {
        return apply(heap, args, parsed, dir, name, shown, errs);
    }
    var e = errs;
    var o = Outcome { changed: false, planned: false, replacements: 0, bytes: 0, diff_truncated: false };
    var hashes = no_hashes(heap);
    match atomic.acquire(heap, dir, name) {
        Opened::Failed(reason) => {
            if atomic.would_block(reason) {
                var w = fail.open(heap, "conflict.locked", "another writer holds this file's lock", "retry after it finishes, then re-check the file");
                w = fail.repair_none(heap, w, "another writer is changing this file; re-read it after that writer finishes");
                w = fail.detail_open(heap, w);
                w = json.put_key(heap, w, "path");
                w = text.put(heap, w, shown);
                e = fail.add(heap, e, w);
            } else {
                e = fail.io_error(heap, e, reason, true, shown);
            }
        }
        Opened::Ok(lock) => {
            drop_hashes(heap, hashes);
            let (applied, outcome, had) = apply(heap, args, parsed, dir, name, shown, e);
            e = applied;
            o = outcome;
            hashes = had;
            // Closing the descriptor releases the lock.
            file_close(lock);
        }
    }
    return (e, o, hashes);
}

// Open the directory that holds the file -- beneath --root when one is
// given (`toolbox.place`) -- and take the lock and apply beneath it, so no
// step follows a link in the file's own name.
fn beneath[&h, &g, &p, &f, &r, &s](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), root: &r [byte], full: &s [byte], shown: &s [byte], errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, dir_write, file_write] (fail.Errors, Outcome, Hashes) {
    var e = errs;
    let name = place.leaf(full);
    if place.is_directory_name(name) {
        e = fail.io_error(heap, e, 21, false, shown);
        return (e, Outcome { changed: false, planned: false, replacements: 0, bytes: 0, diff_truncated: false }, no_hashes(heap));
    }
    match place.parent(fs, root, shown, full) {
        DirOpened::Failed(reason) => {
            e = fail.io_error(heap, e, reason, false, shown);
            return (e, Outcome { changed: false, planned: false, replacements: 0, bytes: 0, diff_truncated: false }, no_hashes(heap));
        }
        DirOpened::Ok(opened) => {
            var dir = opened;
            var o = Outcome { changed: false, planned: false, replacements: 0, bytes: 0, diff_truncated: false };
            var hashes = no_hashes(heap);
            borrow dir as &d in {
                drop_hashes(heap, hashes);
                let (applied, outcome, had) = locked(heap, args, parsed, d, name, shown, e);
                e = applied;
                o = outcome;
                hashes = had;
            }
            dir_close(dir);
            return (e, o, hashes);
        }
    }
}

fn data[&h, &p, &q](heap: &!h Heap, shown: &p [byte], o: Outcome, x: &q Hashes) -> [heap] buffer.Buffer {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "path");
    w = text.put(heap, w, shown);
    w = json.put_key(heap, w, "changed");
    w = json.put_bool(heap, w, o.changed);
    w = json.put_key(heap, w, "replacements");
    w = json.put_int(heap, w, o.replacements);
    w = json.put_key(heap, w, "bytes");
    w = json.put_int(heap, w, o.bytes);
    w = json.put_key(heap, w, "before_sha256");
    w = json.put_string(heap, w, buffer.bytes(x.before));
    w = json.put_key(heap, w, "after_sha256");
    w = json.put_string(heap, w, buffer.bytes(x.after));
    w = json.put_key(heap, w, "diff");
    w = json.put_fragment(heap, w, buffer.bytes(x.changes));
    w = json.put_key(heap, w, "diff_truncated");
    w = json.put_bool(heap, w, o.diff_truncated);
    w = json.end_object(heap, w);
    return json.finish(w);
}

fn planned[&h, &p, &q](heap: &!h Heap, shown: &p [byte], o: Outcome, x: &q Hashes) -> [heap] buffer.Buffer {
    var b = buffer.empty(heap, 256);
    b = buffer.append(heap, b, ",\"dry_run\":true,\"planned_actions\":[");
    if o.planned {
        var w = json.writer(heap, 256);
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "op");
        w = json.put_string(heap, w, "replace");
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "replacements");
        w = json.put_int(heap, w, o.replacements);
        w = json.put_key(heap, w, "before_sha256");
        w = json.put_string(heap, w, buffer.bytes(x.before));
        w = json.put_key(heap, w, "after_sha256");
        w = json.put_string(heap, w, buffer.bytes(x.after));
        w = json.put_key(heap, w, "bytes");
        w = json.put_int(heap, w, o.bytes);
        w = json.put_key(heap, w, "diff");
        w = json.put_fragment(heap, w, buffer.bytes(x.changes));
        w = json.put_key(heap, w, "diff_truncated");
        w = json.put_bool(heap, w, o.diff_truncated);
        w = json.end_object(heap, w);
        let one = json.finish(w);
        borrow one as &r in {
            b = buffer.append(heap, b, buffer.bytes(r));
        }
        buffer.drop(heap, one);
    }
    return buffer.append(heap, b, "]");
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, dir_write, file_write, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    if !cli.has(parsed, table, "old") {
        e = flag_problem(heap, e, "args.required-flag", "replace needs --old, the text to find", "", "--old");
    }
    if !cli.has(parsed, table, "new") {
        e = flag_problem(heap, e, "args.required-flag", "replace needs --new, the text to put in its place", "pass --new '' to delete the text", "--new");
    }
    if cli.nat(args, parsed, table, "max-bytes") > ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-bytes is above the ceiling", "1073741824 is the most this tool holds", "--max-bytes");
    }
    if cli.nat(args, parsed, table, "max-diff-lines") > diff_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-diff-lines is above the ceiling", "100000 is the most this tool shows", "--max-diff-lines");
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) == 0 {
        e = flag_problem(heap, e, "args.missing-operand", "replace takes the PATH to edit", "replace ... PATH", "PATH");
    } else if cli.operand_count(parsed) > 1 {
        e = flag_problem(heap, e, "args.too-many-operands", "replace takes one PATH", "edit one file per call", "PATH");
    }
    var o = Outcome { changed: false, planned: false, replacements: 0, bytes: 0, diff_truncated: false };
    var hashes = no_hashes(heap);
    var shown_copy = buffer.empty(heap, 64);
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    if !refused {
        borrow root as &rr in {
            let (target, checked) = path.operand(heap, args, buffer.bytes(rr), cli.operand_index(parsed, 0), e);
            e = checked;
            borrow target as &tp in {
                shown_copy = buffer.append(heap, shown_copy, path.shown(tp));
                if path.ok(tp) {
                    drop_hashes(heap, hashes);
                    let (applied, outcome, had) = beneath(heap, args, parsed, fs, buffer.bytes(rr), path.full(tp), path.shown(tp), e);
                    e = applied;
                    o = outcome;
                    hashes = had;
                }
            }
            path.drop(heap, target);
        }
    }
    buffer.drop(heap, root);
    var status = 0;
    borrow e as &er in {
        borrow shown_copy as &sc in {
            borrow hashes as &x in {
                var payload = buffer.empty(heap, 1);
                var extra = buffer.empty(heap, 1);
                var line = buffer.empty(heap, 64);
                if fail.count(er) == 0 {
                    buffer.drop(heap, payload);
                    payload = data(heap, buffer.bytes(sc), o, x);
                    if cli.has(parsed, table, "dry-run") {
                        buffer.drop(heap, extra);
                        extra = planned(heap, buffer.bytes(sc), o, x);
                        line = buffer.append(heap, line, "dry run: would replace ");
                    } else if o.changed {
                        line = buffer.append(heap, line, "replaced ");
                    } else {
                        line = buffer.append(heap, line, "unchanged (already applied) ");
                    }
                    line = buffer.push_nat(heap, line, o.replacements);
                    line = buffer.append(heap, line, " in ");
                    line = buffer.append(heap, line, buffer.bytes(sc));
                    line = buffer.append(heap, line, "\n");
                }
                var success = 0;
                if cli.has(parsed, table, "dry-run") {
                    success = 9;
                }
                borrow payload as &d in {
                    borrow extra as &xx in {
                        borrow line as &l in {
                            status = out.respond(heap, io, "replace", "replace.v1", "0.1.0", buffer.bytes(d), buffer.bytes(xx), er, text_mode, buffer.bytes(l), success);
                        }
                    }
                }
                buffer.drop(heap, payload);
                buffer.drop(heap, extra);
                buffer.drop(heap, line);
            }
        }
    }
    buffer.drop(heap, shown_copy);
    drop_hashes(heap, hashes);
    fail.drop(heap, e);
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, dir_write, file_write, io_write, err_write] int {
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
