edition 6;

// `write` -- replace a file's content atomically, and only when the caller
// has said what it believes is there.
//
//     write [--root DIR] (--create | --if-sha256 HEX) [--content-sha256 HEX]
//           [--dry-run] [--max-bytes N] [--max-diff-lines N]
//           (--stdin | --content-file PATH) [--format json|text] PATH
//
// lex-sys `docs/agent-toolbox.md` D10 and D15 row 2: the one place no
// incumbent has an equivalent -- `sed -i`, `tee` and `>` are all blind.
//
// * **No blind overwrite.** `--create` (the path must not exist) or
//   `--if-sha256 HEX` (its content must hash to HEX). Neither is
//   `precondition.required`: the safety is that the agent states its
//   belief about the file and the tool checks it.
// * **Idempotent.** When the file already holds exactly the new content,
//   the answer is `changed: false`, exit 0, whatever the precondition --
//   so a retried write that had in fact landed is not reported as a
//   conflict.
// * **Atomic and locked.** A sibling temporary is written, synced and
//   renamed over the destination while a `flock` on `<path>.lexsys-lock`
//   is held from before the check until after the rename
//   (`toolbox.atomic`).
// * **`--dry-run`** exits 9 with `planned_actions` and makes no mutating
//   call: no lock file, no temporary, no rename. That is a property of
//   this code and of `tests/conformance/test_mutation.py` (strace), not of
//   the type system: the row is the program's, not the invocation's
//   (§2.4), so a dry run reports `dir_write` like a real one.
// * **A diff.** The answer carries `diff`, the lines that change
//   (`toolbox.diff`), and with `--dry-run` so does `planned_actions`, so an
//   agent sees what it is about to replace. The old content is held to
//   compute it, up to `--max-bytes`; past that it is hashed as a stream as
//   before and `diff` is null. `--max-diff-lines` bounds the lines shown.
//
// No tool deletes (D15). The only removal here is of this tool's own
// temporary, after a failed write.

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
    return "root||path|root||resolve PATH and --content-file relative to this directory and refuse paths outside it;create||bool|guard||the path must not exist yet (if it holds exactly the new content, nothing is written and changed is false);if-sha256||hex64|guard||the file's current content must hash to this SHA-256;content-sha256||hex64|none||the new content must hash to this SHA-256, checked before anything is written;dry-run||bool|guard||check everything and report planned_actions, exit 9, write nothing;stdin||bool|none||read the new content from standard input;content-file||path|path-read||read the new content from this file;max-bytes||nat|none|67108864|the largest new content accepted, and the largest old content diffed (ceiling 1073741824);max-diff-lines||nat|none|200|the most changed lines the diff shows (ceiling 100000);format||choice:json/text|none|json|json for a program, text for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "write", version: "0.1.0", summary: "Replace a file atomically, only if it holds what the caller says it holds (--if-sha256) or does not exist (--create); idempotent, locked, with --dry-run.", usage: "write [--root DIR] (--create | --if-sha256 HEX) [--content-sha256 HEX] [--dry-run] [--max-bytes N] [--max-diff-lines N] (--stdin | --content-file PATH) [--format json|text] PATH", output: "document", schema: "write.v1", flags: flag_table(), operands: "PATH|path-write|1|1|the file to create or replace", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.unexpected-value;args.duplicate-flag;args.conflict;args.required-flag;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;io.write-failed;limit.input-too-large;precondition.required;precondition.hash-mismatch;precondition.content-mismatch;conflict.exists;conflict.locked", limits: "max-bytes|67108864|1073741824;max-diff-lines|200|100000", reversibility: "irreversible-bounded", stdin: "with --stdin", guarantees: "deterministic;idempotent;atomic;requires_precondition;dry_run" };
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

// What happened, for the data object and the text line.
struct Outcome {
    changed: bool,
    created: bool,
    planned: bool,
    bytes: int,
    diff_truncated: bool,
}

fn data[&h, &p, &b, &a, &d](heap: &!h Heap, shown: &p [byte], o: Outcome, before: &b [byte], after: &a [byte], changes: &d [byte]) -> [heap] buffer.Buffer {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "path");
    w = text.put(heap, w, shown);
    w = json.put_key(heap, w, "changed");
    w = json.put_bool(heap, w, o.changed);
    w = json.put_key(heap, w, "created");
    w = json.put_bool(heap, w, o.created);
    w = json.put_key(heap, w, "bytes");
    w = json.put_int(heap, w, o.bytes);
    w = json.put_key(heap, w, "before_sha256");
    if len(before) == 0 {
        w = json.put_null(heap, w);
    } else {
        w = json.put_string(heap, w, before);
    }
    w = json.put_key(heap, w, "after_sha256");
    w = json.put_string(heap, w, after);
    w = json.put_key(heap, w, "diff");
    w = json.put_fragment(heap, w, changes);
    w = json.put_key(heap, w, "diff_truncated");
    w = json.put_bool(heap, w, o.diff_truncated);
    w = json.end_object(heap, w);
    return json.finish(w);
}

fn planned[&h, &p, &b, &a, &d](heap: &!h Heap, shown: &p [byte], o: Outcome, before: &b [byte], after: &a [byte], changes: &d [byte]) -> [heap] buffer.Buffer {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "dry_run");
    w = json.put_bool(heap, w, true);
    w = json.put_key(heap, w, "planned_actions");
    w = json.begin_array(heap, w);
    if o.planned {
        w = json.begin_object(heap, w);
        w = json.put_key(heap, w, "op");
        if o.created {
            w = json.put_string(heap, w, "create");
        } else {
            w = json.put_string(heap, w, "replace");
        }
        w = json.put_key(heap, w, "path");
        w = text.put(heap, w, shown);
        w = json.put_key(heap, w, "before_sha256");
        if len(before) == 0 {
            w = json.put_null(heap, w);
        } else {
            w = json.put_string(heap, w, before);
        }
        w = json.put_key(heap, w, "after_sha256");
        w = json.put_string(heap, w, after);
        w = json.put_key(heap, w, "bytes");
        w = json.put_int(heap, w, o.bytes);
        w = json.put_key(heap, w, "diff");
        w = json.put_fragment(heap, w, changes);
        w = json.put_key(heap, w, "diff_truncated");
        w = json.put_bool(heap, w, o.diff_truncated);
        w = json.end_object(heap, w);
    }
    w = json.end_array(heap, w);
    w = json.end_object(heap, w);
    // The members, without the braces: they are spliced into the envelope
    // beside `data`.
    let whole = json.finish(w);
    var inner = buffer.empty(heap, 256);
    borrow whole as &r in {
        let b = buffer.bytes(r);
        inner = buffer.push(heap, inner, byte_of(','));
        inner = buffer.append(heap, inner, b[1..len(b) - 1]);
    }
    buffer.drop(heap, whole);
    return inner;
}

// The precondition, checked against the file as it is now, and the write.
// Called with the lock held, or for a dry run without one.
fn apply[&h, &g, &p, &f, &s, &c, &a](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, dir: &f Dir, name: &s [byte], shown: &s [byte], content: &c [byte], after: &a [byte], errs: fail.Errors) -> [heap, args, dir_read, file_read, dir_write, file_write] (fail.Errors, Outcome, buffer.Buffer, buffer.Buffer) {
    let table = flag_table();
    var e = errs;
    var o = Outcome { changed: false, created: false, planned: false, bytes: len(content), diff_truncated: false };
    // The old content, held for the diff; past --max-bytes, hashed as a
    // stream instead (see the header).
    let (current, read_errno, too_big) = atomic.read_all(heap, dir_open_read(dir, name), cli.nat(args, parsed, table, "max-bytes"), buffer.empty(heap, 4096));
    var before = buffer.empty(heap, 64);
    var errno = read_errno;
    var held = 0;
    if too_big {
        buffer.drop(heap, before);
        let (streamed, stream_errno, stream_size) = atomic.hash_file(heap, dir_open_read(dir, name));
        before = streamed;
        errno = stream_errno;
        held = stream_size;
    } else if read_errno == 0 {
        borrow current as &k in {
            buffer.drop(heap, before);
            before = atomic.sha256_hex(heap, buffer.bytes(k));
            held = buffer.size(k);
        }
    }
    let dry = cli.has(parsed, table, "dry-run");
    let creating = cli.has(parsed, table, "create");
    var go = false;
    borrow before as &b in {
        let have = buffer.bytes(b);
        if errno == 2 {
            if creating {
                go = true;
                o = Outcome { changed: false, created: true, planned: false, bytes: len(content), diff_truncated: false };
            } else {
                var w = fail.open(heap, "io.not-found", "no such file, and --if-sha256 says one was expected", "use --create to write a new file");
                w = fail.no_repair(heap, w);
                w = fail.detail_open(heap, w);
                w = json.put_key(heap, w, "path");
                w = text.put(heap, w, shown);
                w = json.put_key(heap, w, "errno");
                w = json.put_int(heap, w, 2);
                e = fail.add(heap, e, w);
            }
        } else if errno != 0 {
            e = fail.io_error(heap, e, errno, false, shown);
        } else if bytes.equal(have, after) {
            // Already applied: same final state, nothing to do (D10).
        } else if creating {
            var w = fail.open(heap, "conflict.exists", "--create was given and the path exists with other content", "read the file, then write with --if-sha256 and its current hash");
            w = fail.repair_none(heap, w, "the file exists; whether to replace it is a decision, not a retry");
            w = fail.detail_open(heap, w);
            w = json.put_key(heap, w, "path");
            w = text.put(heap, w, shown);
            w = json.put_key(heap, w, "actual_sha256");
            w = json.put_string(heap, w, have);
            e = fail.add(heap, e, w);
        } else if !bytes.equal(have, cli.text(args, parsed, table, "if-sha256")) {
            var w = fail.open(heap, "precondition.hash-mismatch", "the file does not hold the content --if-sha256 named", "re-read the file, then decide");
            w = fail.repair_none(heap, w, "the file changed since it was read; re-read, then decide");
            w = fail.detail_open(heap, w);
            w = json.put_key(heap, w, "path");
            w = text.put(heap, w, shown);
            w = json.put_key(heap, w, "expected_sha256");
            w = json.put_string(heap, w, cli.text(args, parsed, table, "if-sha256"));
            w = json.put_key(heap, w, "actual_sha256");
            w = json.put_string(heap, w, have);
            w = json.put_key(heap, w, "size");
            w = json.put_int(heap, w, held);
            e = fail.add(heap, e, w);
        } else {
            go = true;
        }
    }
    var changes = buffer.empty(heap, 4);
    if go && too_big {
        changes = buffer.append(heap, changes, "null");
        o = Outcome { changed: false, created: o.created, planned: false, bytes: len(content), diff_truncated: true };
    } else if go {
        buffer.drop(heap, changes);
        var cut = false;
        borrow current as &k in {
            let (lines, truncated) = diff.hunks(heap, buffer.bytes(k), content, cli.nat(args, parsed, table, "max-diff-lines"));
            changes = lines;
            cut = truncated;
        }
        o = Outcome { changed: false, created: o.created, planned: false, bytes: len(content), diff_truncated: cut };
    } else {
        changes = buffer.append(heap, changes, "[]");
    }
    buffer.drop(heap, current);
    if go && dry {
        o = Outcome { changed: false, created: o.created, planned: true, bytes: len(content), diff_truncated: o.diff_truncated };
    } else if go {
        let temp = atomic.temp_path(heap, name, after);
        var failed = 0;
        var step = 0;
        borrow temp as &t in {
            let (errno2, step2) = atomic.replace(dir, name, buffer.bytes(t), content);
            failed = errno2;
            step = step2;
        }
        buffer.drop(heap, temp);
        if failed != 0 {
            if step == 1 && failed == 17 {
                e = fail.simple(heap, e, "conflict.locked", "the temporary file could not be created; another writer may hold it", "retry later", "path", shown);
            } else {
                var w = fail.open(heap, fail.io_rule(failed, true), "the replacement could not be written; the file is as it was", "");
                w = fail.no_repair(heap, w);
                w = fail.detail_open(heap, w);
                w = json.put_key(heap, w, "path");
                w = text.put(heap, w, shown);
                w = json.put_key(heap, w, "errno");
                w = json.put_int(heap, w, failed);
                w = json.put_key(heap, w, "step");
                if step == 1 {
                    w = json.put_string(heap, w, "create-temporary");
                } else if step == 2 {
                    w = json.put_string(heap, w, "write");
                } else if step == 3 {
                    w = json.put_string(heap, w, "sync");
                } else {
                    w = json.put_string(heap, w, "rename");
                }
                e = fail.add(heap, e, w);
            }
        } else {
            atomic.sync_parent(dir);
            o = Outcome { changed: true, created: o.created, planned: false, bytes: len(content), diff_truncated: o.diff_truncated };
        }
    }
    return (e, o, before, changes);
}

// Take the lock and apply, or apply without one for a dry run.
fn locked[&h, &g, &p, &f, &s, &c, &a](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, dir: &f Dir, name: &s [byte], shown: &s [byte], content: &c [byte], after: &a [byte], errs: fail.Errors) -> [heap, args, dir_read, file_read, dir_write, file_write] (fail.Errors, Outcome, buffer.Buffer, buffer.Buffer) {
    if cli.has(parsed, flag_table(), "dry-run") {
        return apply(heap, args, parsed, dir, name, shown, content, after, errs);
    }
    var e = errs;
    var o = Outcome { changed: false, created: false, planned: false, bytes: len(content), diff_truncated: false };
    var before = buffer.empty(heap, 1);
    var changes = buffer.empty(heap, 1);
    match atomic.acquire(heap, dir, name) {
        Opened::Failed(reason) => {
            if atomic.would_block(reason) {
                var w = fail.open(heap, "conflict.locked", "another writer holds this file's lock", "retry after it finishes, then re-check the file's hash");
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
            buffer.drop(heap, before);
            buffer.drop(heap, changes);
            let (applied, outcome, had, lines) = apply(heap, args, parsed, dir, name, shown, content, after, e);
            e = applied;
            o = outcome;
            before = had;
            changes = lines;
            // Closing the descriptor releases the lock.
            file_close(lock);
        }
    }
    return (e, o, before, changes);
}

// Open the directory that holds the file -- beneath --root when one is
// given (`toolbox.place`) -- and take the lock and apply beneath it, so no
// step follows a link in the file's own name.
fn beneath[&h, &g, &p, &f, &r, &s, &c, &a](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), root: &r [byte], full: &s [byte], shown: &s [byte], content: &c [byte], after: &a [byte], errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, dir_write, file_write] (fail.Errors, Outcome, buffer.Buffer, buffer.Buffer) {
    var e = errs;
    let name = place.leaf(full);
    if place.is_directory_name(name) {
        e = fail.io_error(heap, e, 21, false, shown);
        return (e, Outcome { changed: false, created: false, planned: false, bytes: len(content), diff_truncated: false }, buffer.empty(heap, 1), buffer.empty(heap, 1));
    }
    match place.parent(fs, root, shown, full) {
        DirOpened::Failed(reason) => {
            e = fail.io_error(heap, e, reason, false, shown);
            return (e, Outcome { changed: false, created: false, planned: false, bytes: len(content), diff_truncated: false }, buffer.empty(heap, 1), buffer.empty(heap, 1));
        }
        DirOpened::Ok(opened) => {
            var dir = opened;
            var o = Outcome { changed: false, created: false, planned: false, bytes: len(content), diff_truncated: false };
            var before = buffer.empty(heap, 1);
            var changes = buffer.empty(heap, 1);
            borrow dir as &d in {
                buffer.drop(heap, before);
                buffer.drop(heap, changes);
                let (applied, outcome, had, lines) = locked(heap, args, parsed, d, name, shown, content, after, e);
                e = applied;
                o = outcome;
                before = had;
                changes = lines;
            }
            dir_close(dir);
            return (e, o, before, changes);
        }
    }
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, dir_write, file_write, io_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    let creating = cli.has(parsed, table, "create");
    let guarded = cli.has(parsed, table, "if-sha256");
    if creating && guarded {
        e = flag_problem(heap, e, "args.conflict", "--create and --if-sha256 exclude each other", "--create for a new file, --if-sha256 for an existing one", "--create --if-sha256");
    } else if !creating && !guarded {
        var w = fail.open(heap, "precondition.required", "a write must say what it expects to find: --create or --if-sha256", "read the file and pass --if-sha256 with its hash, or --create for a new file");
        w = fail.repair_none(heap, w, "the tool cannot know what the caller believes about the file; reading it is the caller's step");
        w = fail.detail_open(heap, w);
        w = json.put_key(heap, w, "flags");
        w = json.put_string(heap, w, "--create | --if-sha256");
        e = fail.add(heap, e, w);
    }
    let from_stdin = cli.has(parsed, table, "stdin");
    let from_file = cli.has(parsed, table, "content-file");
    if from_stdin && from_file {
        e = flag_problem(heap, e, "args.conflict", "--stdin and --content-file exclude each other", "give the content one way", "--stdin --content-file");
    } else if !from_stdin && !from_file {
        e = flag_problem(heap, e, "args.required-flag", "the new content must come from --stdin or --content-file", "", "--stdin | --content-file");
    }
    let most = cli.nat(args, parsed, table, "max-bytes");
    if most > ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-bytes is above the ceiling", "1073741824 is the most this tool holds", "--max-bytes");
    }
    if cli.nat(args, parsed, table, "max-diff-lines") > diff_ceiling() {
        e = flag_problem(heap, e, "args.bad-value", "--max-diff-lines is above the ceiling", "100000 is the most this tool shows", "--max-diff-lines");
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) == 0 {
        e = flag_problem(heap, e, "args.missing-operand", "write takes the PATH to write", "write ... PATH", "PATH");
    } else if cli.operand_count(parsed) > 1 {
        e = flag_problem(heap, e, "args.too-many-operands", "write takes one PATH", "write one file per call", "PATH");
    }

    var o = Outcome { changed: false, created: false, planned: false, bytes: 0, diff_truncated: false };
    var shown_copy = buffer.empty(heap, 64);
    var before = buffer.empty(heap, 1);
    var after = buffer.empty(heap, 1);
    var changes = buffer.empty(heap, 1);
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    if !refused {
        borrow root as &rr in {
            let (target, checked) = path.operand(heap, args, buffer.bytes(rr), cli.operand_index(parsed, 0), e);
            e = checked;
            // The content, from the file or standard input, bounded.
            var content = buffer.empty(heap, 4096);
            var source_ok = true;
            if from_file {
                let (source, checked2) = path.operand(heap, args, buffer.bytes(rr), cli.value_index(parsed, table, "content-file"), e);
                e = checked2;
                borrow source as &sp in {
                    if path.ok(sp) {
                        let (read, errno, too_big) = atomic.read_all(heap, place.open_operand(fs, buffer.bytes(rr), path.shown(sp), path.full(sp)), most, content);
                        content = read;
                        if errno != 0 {
                            e = fail.io_error(heap, e, errno, false, path.shown(sp));
                            source_ok = false;
                        } else if too_big {
                            e = fail.simple(heap, e, "limit.input-too-large", "the content is larger than --max-bytes", "raise --max-bytes, up to 1073741824", "path", path.shown(sp));
                            source_ok = false;
                        }
                    } else {
                        source_ok = false;
                    }
                }
                path.drop(heap, source);
            } else {
                let (read, more) = atomic.read_stdin(heap, io, most, content);
                content = read;
                if more {
                    e = fail.simple(heap, e, "limit.input-too-large", "the content on standard input is larger than --max-bytes", "raise --max-bytes, up to 1073741824", "", "");
                    source_ok = false;
                }
            }
            borrow content as &c in {
                buffer.drop(heap, after);
                after = atomic.sha256_hex(heap, buffer.bytes(c));
                o = Outcome { changed: false, created: false, planned: false, bytes: buffer.size(c), diff_truncated: false };
            }
            if source_ok && cli.has(parsed, table, "content-sha256") {
                borrow after as &a in {
                    if !bytes.equal(buffer.bytes(a), cli.text(args, parsed, table, "content-sha256")) {
                        var w = fail.open(heap, "precondition.content-mismatch", "the new content does not hash to --content-sha256", "the content was not what was meant to be sent; send it again");
                        w = fail.no_repair(heap, w);
                        w = fail.detail_open(heap, w);
                        w = json.put_key(heap, w, "expected_sha256");
                        w = json.put_string(heap, w, cli.text(args, parsed, table, "content-sha256"));
                        w = json.put_key(heap, w, "actual_sha256");
                        w = json.put_string(heap, w, buffer.bytes(a));
                        e = fail.add(heap, e, w);
                        source_ok = false;
                    }
                }
            }
            borrow target as &tp in {
                shown_copy = buffer.append(heap, shown_copy, path.shown(tp));
                if path.ok(tp) && source_ok {
                    borrow content as &c in {
                        borrow after as &a in {
                            let (applied, outcome, had, lines) = beneath(heap, args, parsed, fs, buffer.bytes(rr), path.full(tp), path.shown(tp), buffer.bytes(c), buffer.bytes(a), e);
                            e = applied;
                            o = outcome;
                            buffer.drop(heap, before);
                            before = had;
                            buffer.drop(heap, changes);
                            changes = lines;
                        }
                    }
                }
            }
            path.drop(heap, target);
            buffer.drop(heap, content);
        }
    }
    buffer.drop(heap, root);

    var status = 0;
    borrow e as &er in {
        borrow shown_copy as &sc in {
            borrow before as &b in {
                borrow after as &a in {
                    borrow changes as &ch in {
                        var payload = buffer.empty(heap, 1);
                        var extra = buffer.empty(heap, 1);
                        var line = buffer.empty(heap, 64);
                        if fail.count(er) == 0 {
                            buffer.drop(heap, payload);
                            payload = data(heap, buffer.bytes(sc), o, buffer.bytes(b), buffer.bytes(a), buffer.bytes(ch));
                            if cli.has(parsed, table, "dry-run") {
                                buffer.drop(heap, extra);
                                extra = planned(heap, buffer.bytes(sc), o, buffer.bytes(b), buffer.bytes(a), buffer.bytes(ch));
                                line = buffer.append(heap, line, "dry run: ");
                            }
                            if o.changed {
                                line = buffer.append(heap, line, "wrote ");
                            } else if o.planned {
                                line = buffer.append(heap, line, "would write ");
                            } else {
                                line = buffer.append(heap, line, "unchanged ");
                            }
                            line = buffer.append(heap, line, buffer.bytes(sc));
                            line = buffer.append(heap, line, "\n");
                        }
                        var success = 0;
                        if cli.has(parsed, table, "dry-run") {
                            success = 9;
                        }
                        borrow payload as &d in {
                            borrow extra as &x in {
                                borrow line as &l in {
                                    status = out.respond(heap, io, "write", "write.v1", "0.1.0", buffer.bytes(d), buffer.bytes(x), er, text_mode, buffer.bytes(l), success);
                                }
                            }
                        }
                        buffer.drop(heap, payload);
                        buffer.drop(heap, extra);
                        buffer.drop(heap, line);
                    }
                }
            }
        }
    }
    buffer.drop(heap, shown_copy);
    buffer.drop(heap, before);
    buffer.drop(heap, after);
    buffer.drop(heap, changes);
    fail.drop(heap, e);
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, dir_write, file_write, io_read, io_write, err_write] int {
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
