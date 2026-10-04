edition 5;

module toolbox.path;

// `toolbox.path` -- D9: paths are checked by the tool before any builtin
// sees one.
//
// The builtins trap on a path they refuse (lex-sys `docs/agent-toolbox.md`
// §2.2, A.4): `..` traps even under the unnarrowed `Fs("")`, and a trap
// carries no tag, so an agent that typed `../src` would get a dead process
// instead of an error it can act on. Here every operand is checked first:
//
// * empty -> `path.empty`; longer than 4096 bytes -> `path.too-long`;
// * a `..` component -> `path.dotdot`, with a repair when the lexical
//   normal form stays inside (the repair is a suggestion the agent can
//   check, not a normalisation the tool performs: with symlinks, `a/l/../b`
//   is not `a/b`, which is why the tool refuses rather than resolves);
// * with `--root`, an absolute path under the root -> `path.absolute`
//   with the relative spelling as the repair, and one that is not under it
//   (a sibling that shares the prefix, `/rootevil` for `/root`, is not)
//   -> `path.outside-root`, exit 4;
// * `.` components, repeated `/` and a trailing `/` are collapsed.
//
// **Symlinks are not handled.** There is no `lstat`, `readlink` or
// no-follow open (L6), and a link inside the root that points outside it is
// followed. `introspect` says `confinement: lexical`, and
// `tests/conformance/test_confinement.py` asserts the escape, so the day a
// no-follow primitive lands the test flips.

import std.buffer;
import std.bytes;
import std.json;
import toolbox.fail;

pub fn longest() -> [] int {
    return 4096;
}

fn slash() -> [] int {
    return '/';
}

// The components of `p`, lexically normalised: `.` and empty components
// dropped, `..` kept (or, when `resolve`, applied to the component before
// it). A leading `/` is kept. Answers the buffer and whether a `..` was
// seen (when `resolve`, whether one climbed above the start).
fn normalise[&h, &p](heap: &!h Heap, p: &p [byte], resolve: bool) -> [heap] (buffer.Buffer, bool) {
    var out = buffer.empty(heap, len(p) + 1);
    var flagged = false;
    let absolute = len(p) > 0 && int_of(p[0]) == slash();
    if absolute {
        out = buffer.push(heap, out, byte_of(slash()));
    }
    let base = len(p) > 0 && absolute;
    var at = 0;
    while at < len(p) {
        var end = at;
        while end < len(p) && int_of(p[end]) != slash() {
            end = end + 1;
        }
        let part = p[at..end];
        if len(part) == 0 || bytes.equal(part, ".") {
            // Nothing.
        } else if bytes.equal(part, "..") && resolve {
            // Drop the last component written, if there is one.
            var count = 0;
            borrow out as &o in {
                count = buffer.size(o);
            }
            var floor = 0;
            if base {
                floor = 1;
            }
            if count <= floor {
                flagged = true;
            } else {
                var cut = count;
                borrow out as &o in {
                    let b = buffer.bytes(o);
                    while cut > floor && int_of(b[cut - 1]) != slash() {
                        cut = cut - 1;
                    }
                }
                // `cut` is just past the separator before the component;
                // drop the separator too unless it is the root's.
                var keep = cut;
                if keep > floor {
                    keep = keep - 1;
                }
                let trimmed = trim_to(heap, out, keep);
                out = trimmed;
            }
        } else {
            if bytes.equal(part, "..") {
                flagged = true;
            }
            var count = 0;
            borrow out as &o in {
                count = buffer.size(o);
            }
            if count > 0 && !(base && count == 1) {
                out = buffer.push(heap, out, byte_of(slash()));
            }
            out = buffer.append(heap, out, part);
        }
        at = end + 1;
    }
    return (out, flagged);
}

// `b` with only its first `n` bytes.
fn trim_to[&h](heap: &!h Heap, b: buffer.Buffer, n: int) -> [heap] buffer.Buffer {
    var out = buffer.empty(heap, n + 1);
    borrow b as &r in {
        out = buffer.append(heap, out, buffer.bytes(r)[0..n]);
    }
    buffer.drop(heap, b);
    return out;
}

// Whether `p` has a `..` component.
pub fn has_dotdot[&p](p: &p [byte]) -> [] bool {
    var at = 0;
    while at < len(p) {
        var end = at;
        while end < len(p) && int_of(p[end]) != slash() {
            end = end + 1;
        }
        if bytes.equal(p[at..end], "..") {
            return true;
        }
        at = end + 1;
    }
    return false;
}

// If `abs` is `root` or under it, the offset where the part below `root`
// starts (past the separator); -1 otherwise. Both are normalised. A
// sibling sharing the byte prefix is not under it.
pub fn under[&r, &a](root: &r [byte], abs: &a [byte]) -> [] int {
    if bytes.equal(root, "/") {
        if len(abs) > 0 && int_of(abs[0]) == slash() {
            return 1;
        }
        return 0 - 1;
    }
    if !bytes.starts_with(abs, root) {
        return 0 - 1;
    }
    if len(abs) == len(root) {
        return len(abs);
    }
    if int_of(abs[len(root)]) == slash() {
        return len(root) + 1;
    }
    return 0 - 1;
}

// A resolved operand: the path to open, and the spelling to show (the
// path relative to the root, or as given and normalised when there is no
// root).
pub res struct Resolved {
    full: buffer.Buffer,
    // `full[shown..]` is what output names it.
    shown: int,
    ok: bool,
}

pub fn full[&r](r: &r Resolved) -> [] &r [byte] {
    return buffer.bytes(r.full);
}

pub fn shown[&r](r: &r Resolved) -> [] &r [byte] {
    let b = buffer.bytes(r.full);
    return b[r.shown..len(b)];
}

pub fn ok[&r](r: &r Resolved) -> [] bool {
    return r.ok;
}

pub fn drop[&h](heap: &!h Heap, r: Resolved) -> [heap] int {
    let Resolved { full, shown, ok } = r;
    return buffer.drop(heap, full);
}

fn refused[&h](heap: &!h Heap) -> [heap] Resolved {
    return Resolved { full: buffer.empty(heap, 1), shown: 0, ok: false };
}

fn path_error[&h, &g, &r](heap: &!h Heap, e: fail.Errors, args: &g Args, rule: &static [byte], message: &static [byte], hint: &static [byte], at: int, repair: &r [byte]) -> [heap, args] fail.Errors {
    var w = fail.open(heap, rule, message, hint);
    if len(repair) > 0 {
        w = fail.retry_replacing(heap, w, args, at, repair);
    } else {
        w = fail.no_repair(heap, w);
    }
    w = fail.detail_open(heap, w);
    w = json.put_key(heap, w, "path");
    w = json.put_string(heap, w, arg(args, at));
    w = json.put_key(heap, w, "position");
    w = json.put_int(heap, w, at);
    return fail.add(heap, e, w);
}

// Check a `--root` value: not empty, no `..`, not too long. Answers the
// normalised root, empty when there was none or it was refused.
pub fn root[&h, &g](heap: &!h Heap, args: &g Args, given: &static [byte], at: int, errs: fail.Errors) -> [heap, args] (buffer.Buffer, fail.Errors) {
    var e = errs;
    if at < 0 {
        return (buffer.empty(heap, 1), e);
    }
    if len(given) == 0 {
        e = path_error(heap, e, args, "path.empty", "--root is empty", "", at, "");
        return (buffer.empty(heap, 1), e);
    }
    if len(given) > longest() {
        e = path_error(heap, e, args, "path.too-long", "--root is longer than 4096 bytes", "", at, "");
        return (buffer.empty(heap, 1), e);
    }
    if has_dotdot(given) {
        e = path_error(heap, e, args, "path.dotdot", "--root has a `..` component", "name the root without `..`", at, "");
        return (buffer.empty(heap, 1), e);
    }
    let (norm, flagged) = normalise(heap, given, false);
    var out = norm;
    var count = 0;
    borrow out as &o in {
        count = buffer.size(o);
    }
    if count == 0 {
        // `--root .` and `--root ./`: the working directory.
        out = buffer.push(heap, out, byte_of('.'));
    }
    return (out, e);
}

// Check operand `at` against `root` (normalised, or empty for none) and
// answer where to open it.
pub fn operand[&h, &g, &r](heap: &!h Heap, args: &g Args, root: &r [byte], at: int, errs: fail.Errors) -> [heap, args] (Resolved, fail.Errors) {
    var e = errs;
    let given = arg(args, at);
    if len(given) == 0 {
        e = path_error(heap, e, args, "path.empty", "the path is empty", "", at, "");
        return (refused(heap), e);
    }
    if len(given) > longest() {
        e = path_error(heap, e, args, "path.too-long", "the path is longer than 4096 bytes", "", at, "");
        return (refused(heap), e);
    }
    let absolute = int_of(given[0]) == slash();
    if has_dotdot(given) {
        // Suggest the lexical normal form, when it climbs nowhere and (with
        // a root, for an absolute path) stays under the root.
        let (resolved, climbed) = normalise(heap, given, true);
        var suggestion = buffer.empty(heap, 1);
        borrow resolved as &r2 in {
            let text = buffer.bytes(r2);
            var good = !climbed && len(text) > 0;
            if good && len(root) > 0 && absolute {
                good = false;
            }
            if good {
                suggestion = buffer.append(heap, suggestion, text);
            }
        }
        buffer.drop(heap, resolved);
        borrow suggestion as &s in {
            e = path_error(heap, e, args, "path.dotdot", "the path has a `..` component", "name the file without `..`; the tool does not resolve it, because a symlink makes `a/link/../b` something other than `a/b`", at, buffer.bytes(s));
        }
        buffer.drop(heap, suggestion);
        return (refused(heap), e);
    }
    let (norm, flagged) = normalise(heap, given, false);
    if len(root) == 0 {
        var count = 0;
        borrow norm as &n in {
            count = buffer.size(n);
        }
        var out = norm;
        if count == 0 {
            out = buffer.push(heap, out, byte_of('.'));
        }
        return (Resolved { full: out, shown: 0, ok: true }, e);
    }
    if absolute {
        var below = 0 - 1;
        var suggestion = buffer.empty(heap, 1);
        borrow norm as &n in {
            let text = buffer.bytes(n);
            below = under(root, text);
            if below >= 0 {
                if below < len(text) {
                    suggestion = buffer.append(heap, suggestion, text[below..len(text)]);
                } else {
                    suggestion = buffer.push(heap, suggestion, byte_of('.'));
                }
            }
        }
        buffer.drop(heap, norm);
        if below >= 0 {
            borrow suggestion as &s in {
                e = path_error(heap, e, args, "path.absolute", "the path is absolute, and paths are relative to --root", "write it relative to --root", at, buffer.bytes(s));
            }
        } else {
            e = path_error(heap, e, args, "path.outside-root", "the path is not under --root", "name a path inside --root", at, "");
        }
        buffer.drop(heap, suggestion);
        return (refused(heap), e);
    }
    var full = buffer.empty(heap, len(root) + len(given) + 2);
    full = buffer.append(heap, full, root);
    if !bytes.equal(root, "/") {
        full = buffer.push(heap, full, byte_of(slash()));
    }
    var shown = 0;
    borrow full as &f in {
        shown = buffer.size(f);
    }
    var count = 0;
    borrow norm as &n in {
        count = buffer.size(n);
        full = buffer.append(heap, full, buffer.bytes(n));
    }
    buffer.drop(heap, norm);
    if count == 0 {
        full = buffer.push(heap, full, byte_of('.'));
    }
    return (Resolved { full: full, shown: shown, ok: true }, e);
}

// The normal form of `p` with `..` applied, for tests.
pub fn lexical[&h, &p](heap: &!h Heap, p: &p [byte]) -> [heap] (buffer.Buffer, bool) {
    return normalise(heap, p, true);
}

// The root of a build-time variant (D14, `scripts/variant.py`): the
// program's `Fs` is narrowed to `baked`, so its authority names the
// directory, and every path must be under it. `--root` may be omitted or
// may name `baked` itself; any other value is refused, because a path
// outside the narrowed prefix would trap rather than answer.
pub fn baked[&h, &g](heap: &!h Heap, args: &g Args, baked: &static [byte], given: &static [byte], at: int, errs: fail.Errors) -> [heap, args] (buffer.Buffer, fail.Errors) {
    var e = errs;
    if at >= 0 {
        let (norm, flagged) = normalise(heap, given, false);
        var same = false;
        borrow norm as &n in {
            same = bytes.equal(buffer.bytes(n), baked) && !has_dotdot(given);
        }
        buffer.drop(heap, norm);
        if !same {
            e = path_error(heap, e, args, "path.outside-root", "this build is confined to one directory, and --root names another", "omit --root: this build's root is fixed (introspect names it)", at, "");
        }
    }
    var out = buffer.empty(heap, len(baked));
    out = buffer.append(heap, out, baked);
    return (out, e);
}
