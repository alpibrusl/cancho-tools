edition 5;

// `hash` -- SHA-256 or SHA-512 of files, as NDJSON, any size.
//
//     hash [--root DIR] [--algo sha256|sha512] [--verify HEX] [--format ndjson|text] PATH...
//
// lex-sys `docs/agent-toolbox.md` D15 row 7: built as a by-product, and
// not worth it alone -- `sha256sum` is faster and already right. Its worth
// is that `write`'s precondition needs the same function, so an agent can
// get the hash `write --if-sha256` wants from a tool whose row has no
// write in it. The hashing streams through `toolbox.sha` (the in-package
// port of L4), so a file past `std.crypto`'s 65,527-byte ceiling is hashed
// rather than trapping.
//
// `--verify HEX` takes one PATH and fails with `precondition.hash-failed`
// (exit 8) when the digest differs.

import std.buffer;
import std.bytes;
import std.json;
import toolbox.built;
import toolbox.cli;
import toolbox.describe;
import toolbox.fail;
import toolbox.out;
import toolbox.path;
import toolbox.place;
import toolbox.sha;
import toolbox.text;

fn flag_table() -> [] &static [byte] {
    return "root||path|root||resolve every PATH relative to this directory and refuse paths outside it;algo|a|choice:sha256/sha512|none|sha256|the digest;verify||text|none||the expected digest in lowercase hex, takes one PATH, and a difference is precondition.hash-failed;format||choice:ndjson/text|none|ndjson|ndjson for a program, text (sha256sum's layout) for a person";
}

fn tool() -> [] describe.Tool {
    return describe.Tool { name: "hash", version: "0.1.0", summary: "SHA-256 or SHA-512 of files of any size, as NDJSON records, with --verify; the hash write --if-sha256 wants, from a tool that cannot write.", usage: "hash [--root DIR] [--algo sha256|sha512] [--verify HEX] [--format ndjson|text] PATH...", output: "stream", schema: "hash.v1", flags: flag_table(), operands: "PATH...|path-read|the files to hash, in order", rules: "args.unknown-flag;args.missing-value;args.bad-value;args.duplicate-flag;args.missing-operand;args.too-many-operands;path.empty;path.dotdot;path.absolute;path.outside-root;path.too-long;path.symlink;io.not-found;io.not-a-directory;io.is-a-directory;io.permission-denied;io.read-failed;precondition.hash-failed", limits: "", reversibility: "reversible-cheap", stdin: "no" };
}

fn built() -> [] describe.Built {
    return describe.Built { authority: built.authority(), schema: built.schema(), compiler: built.compiler() };
}

fn flag_problem[&h](heap: &!h Heap, e: fail.Errors, rule: &static [byte], message: &static [byte], hint: &static [byte], flags: &static [byte]) -> [heap] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    w = fail.no_repair(heap, w);
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "flags");
    w = json.put_string(heap, w, flags);
    return fail.add(heap, e, w);
}

// Hash an open file: the hex digest (empty on a failed read), the errno,
// and the size.
fn digest_of[&h, &f](heap: &!h Heap, file: &!f File, wide: bool) -> [heap, file_read] (buffer.Buffer, int, int) {
    var s = sha.sha256(heap);
    if wide {
        sha.discard(heap, s);
        s = sha.sha512(heap);
    }
    var chunk = buffer.empty(heap, 65536);
    var errno = 0;
    var size = 0;
    var going = true;
    while going {
        var got = 0;
        borrow mut chunk as &!c in {
            buffer.clear(c);
            match file_read(file, buffer.room(c)) {
                Read::Got(n) => {
                    buffer.filled(c, n);
                    got = n;
                }
                Read::End => {
                    going = false;
                }
                Read::Failed(reason) => {
                    going = false;
                    errno = reason;
                    if errno == 0 {
                        errno = 5;
                    }
                }
            }
        }
        if got > 0 {
            borrow chunk as &c in {
                s = sha.update(s, buffer.bytes(c));
            }
            size = size + got;
        }
    }
    buffer.drop(heap, chunk);
    var hex = buffer.empty(heap, 128);
    region a {
        let digest = alloc_slice[a](64, byte_of(0));
        let n = sha.finish(heap, s, digest);
        if errno == 0 {
            let text = alloc_slice[a](128, byte_of(0));
            sha.hex_into(digest, n, text);
            hex = buffer.append(heap, hex, text[0..2 * n]);
        }
    }
    return (hex, errno, size);
}

struct Tally {
    files: int,
    broken: bool,
    emitted: int,
}

fn sync[&h, &i, &e](heap: &!h Heap, io: &!i Io, errs: &e fail.Errors, t: Tally, text_mode: bool) -> [heap, io_write] Tally {
    var broken = t.broken;
    if !text_mode && !broken {
        if !out.error_records(heap, io, errs, t.emitted) {
            broken = true;
        }
    }
    return Tally { files: t.files, broken: broken, emitted: fail.count(errs) };
}

fn one[&h, &g, &p, &f, &i, &r](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, root: &r [byte], at: int, errs: fail.Errors, tally: Tally) -> [heap, args, fs_read(""), dir_read, file_read, io_write] (fail.Errors, Tally) {
    let table = flag_table();
    let wide = bytes.equal(cli.text(args, parsed, table, "algo"), "sha512");
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    var t = tally;
    let (resolved, checked) = path.operand(heap, args, root, at, errs);
    var e = checked;
    borrow resolved as &rp in {
        if path.ok(rp) {
            match place.open_operand(fs, root, path.shown(rp), path.full(rp)) {
                Opened::Failed(reason) => {
                    e = fail.io_error(heap, e, reason, false, path.shown(rp));
                }
                Opened::Ok(opened) => {
                    var file = opened;
                    var hex = buffer.empty(heap, 1);
                    var errno = 0;
                    var size = 0;
                    borrow mut file as &!handle in {
                        buffer.drop(heap, hex);
                        let (h2, e2, s2) = digest_of(heap, handle, wide);
                        hex = h2;
                        errno = e2;
                        size = s2;
                    }
                    file_close(file);
                    if errno != 0 {
                        e = fail.io_error(heap, e, errno, false, path.shown(rp));
                    } else {
                        t = Tally { files: t.files + 1, broken: t.broken, emitted: t.emitted };
                        var wrote = true;
                        borrow hex as &x in {
                            if text_mode {
                                var b = buffer.empty(heap, 160);
                                b = buffer.append(heap, b, buffer.bytes(x));
                                b = buffer.append(heap, b, "  ");
                                b = buffer.append(heap, b, path.shown(rp));
                                wrote = out.buffer_line(heap, io, b);
                            } else {
                                var w = json.writer(heap, 256);
                                w = json.begin_object(heap, w);
                                w = json.put_key(heap, w, "type");
                                w = json.put_string(heap, w, "hash");
                                w = json.put_key(heap, w, "path");
                                w = text.put(heap, w, path.shown(rp));
                                w = json.put_key(heap, w, "algo");
                                w = json.put_string(heap, w, cli.text(args, parsed, table, "algo"));
                                w = json.put_key(heap, w, "hex");
                                w = json.put_string(heap, w, buffer.bytes(x));
                                w = json.put_key(heap, w, "bytes");
                                w = json.put_int(heap, w, size);
                                wrote = out.close_line(heap, io, w);
                            }
                            if cli.has(parsed, table, "verify") && !bytes.equal(buffer.bytes(x), cli.text(args, parsed, table, "verify")) {
                                var w = fail.open(heap, "precondition.hash-failed", "the file's digest is not the one --verify named", "");
                                w = fail.no_repair(heap, w);
                                w = fail.detail_open(heap, w);
                                w = json.put_key(heap, w, "path");
                                w = text.put(heap, w, path.shown(rp));
                                w = json.put_key(heap, w, "expected");
                                w = json.put_string(heap, w, cli.text(args, parsed, table, "verify"));
                                w = json.put_key(heap, w, "actual");
                                w = json.put_string(heap, w, buffer.bytes(x));
                                e = fail.add(heap, e, w);
                            }
                        }
                        if !wrote {
                            t = Tally { files: t.files, broken: true, emitted: t.emitted };
                        }
                    }
                    buffer.drop(heap, hex);
                }
            }
        }
    }
    path.drop(heap, resolved);
    borrow e as &er in {
        t = sync(heap, io, er, t, text_mode);
    }
    return (e, t);
}

fn body[&h, &g, &p, &f, &i](heap: &!h Heap, args: &g Args, parsed: &p cli.Parsed, fs: &f Fs(""), io: &!i Io, errs: fail.Errors) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
    let table = flag_table();
    var e = errs;
    let text_mode = bytes.equal(cli.text(args, parsed, table, "format"), "text");
    var t = Tally { files: 0, broken: false, emitted: 0 };
    let wide = bytes.equal(cli.text(args, parsed, table, "algo"), "sha512");
    if cli.has(parsed, table, "verify") {
        var digits = 64;
        if wide {
            digits = 128;
        }
        if !sha.is_hex(cli.text(args, parsed, table, "verify"), digits) {
            e = flag_problem(heap, e, "args.bad-value", "--verify wants the digest in lowercase hex: 64 digits for sha256, 128 for sha512", "", "--verify");
        }
        if cli.operand_count(parsed) > 1 {
            e = flag_problem(heap, e, "args.too-many-operands", "--verify checks one PATH", "verify one file per call", "PATH");
        }
    }
    let (root, after_root) = path.root(heap, args, cli.text(args, parsed, table, "root"), cli.value_index(parsed, table, "root"), e);
    e = after_root;
    if cli.operand_count(parsed) == 0 {
        e = flag_problem(heap, e, "args.missing-operand", "hash takes at least one PATH", "hash PATH...", "PATH");
    }
    borrow e as &er in {
        t = sync(heap, io, er, t, text_mode);
    }
    var refused = false;
    borrow e as &er in {
        refused = fail.count(er) > 0;
    }
    var k = 0;
    while !refused && !t.broken && k < cli.operand_count(parsed) {
        borrow root as &rr in {
            let (e2, t2) = one(heap, args, parsed, fs, io, buffer.bytes(rr), cli.operand_index(parsed, k), e, t);
            e = e2;
            t = t2;
        }
        k = k + 1;
    }
    buffer.drop(heap, root);
    var status = 0;
    var broken = t.broken;
    borrow e as &er in {
        status = fail.exit_code(er);
        if text_mode {
            out.say_errors(io, "hash", er);
        } else if !broken {
            var w = out.end_open(heap, "hash", "hash.v1", fail.count(er) == 0, fail.count(er) == 0);
            w = json.put_key(heap, w, "files");
            w = json.put_int(heap, w, t.files);
            w = json.put_key(heap, w, "errors");
            w = json.put_int(heap, w, fail.count(er));
            if !out.close_line(heap, io, w) {
                broken = true;
            }
        }
    }
    fail.drop(heap, e);
    if !broken && !out.flushed(io) {
        broken = true;
    }
    if broken {
        out.write_failed(io, "hash");
        return 1;
    }
    return status;
}

fn run[&h, &g, &f, &i](heap: &!h Heap, args: &g Args, fs: &f Fs(""), io: &!i Io) -> [heap, args, fs_read(""), dir_read, file_read, io_write, err_write] int {
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
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    // No foreign code, no network, no clock.
    release(ffi);
    release(net);
    release(clock);
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
