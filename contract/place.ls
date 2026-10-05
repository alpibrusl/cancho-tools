edition 6;

module toolbox.place;

// `toolbox.place` -- D9's second half: a path under `--root` is opened
// beneath the root, one component at a time, following no link.
//
// `toolbox.path` checks an operand lexically and answers the spelling to
// open; it cannot see a link, and a link inside the root that points
// outside it used to be followed. Here the root is opened once with
// `open_dir` -- the trust anchor: links in the root's own spelling are
// followed, because the caller named it -- and every component below it
// with `dir_enter` or `dir_open_read`, which pass `O_NOFOLLOW` and refuse
// `.`, `..` and an empty name themselves (lex-sys
// `docs/directory-handles.md`). A link anywhere below the root is refused:
// `ELOOP` from the open, which `fail.io_rule` names `path.symlink`.
//
// The walk is `std.dirs.open_file`'s with one addition. Linux answers
// `ENOTDIR`, not `ELOOP`, when a link to a directory is entered with
// `O_DIRECTORY | O_NOFOLLOW`, and that is also what a component that is a
// plain file answers. When `dir_enter` says `ENOTDIR`, the component is
// opened once more as a file, with `O_NOFOLLOW`: a link answers `ELOOP`,
// and the walk answers that, so a linked directory is `path.symlink` on
// every kernel rather than `io.not-a-directory` on one.
//
// Without `--root` there is nothing to be beneath. A reader opens the path
// as given, links and all (`confinement: none`). A writer works beneath the
// directory that holds the file whichever way it was named (`parent`), so
// its own last component -- the file it replaces, its temporary and its
// lock -- is never a link it follows.

import std.bytes;

// `ELOOP`: 40 on Linux, 62 on macOS.
pub fn is_loop(errno: int) -> [] bool {
    return errno == 40 || errno == 62;
}

fn enotdir() -> [] int {
    return 20;
}

// The length of an opened file, a directory refused first.
//
// `file_size` is `lseek(SEEK_END)`, and what that answers on a directory
// is the filesystem's: ext4 and APFS answer a number, so the directory used
// to be found at the first read as `EISDIR`, but tmpfs
// (`dcache_dir_lseek`) takes `SEEK_SET` and `SEEK_CUR` only and answers
// `EINVAL`, which `fail.io_rule` names `io.read-failed`. A one-byte `pread`
// at 0 asks the handle itself: a directory answers `EISDIR` on every
// filesystem, before any seek, and a file is left as it was (`pread` moves
// no cursor). The lex-sys pinned in `lex-sys.toml` has no `fstat` on a
// `File` -- `dir_stat` needs the `Dir` and a name, and does not follow the
// link a reader without `--root` follows -- so the read is the probe.
pub fn file_length[&f](file: &!f File) -> [file_read] Done {
    var errno = 0;
    region a {
        let probe = alloc_slice[a](1, byte_of(0));
        match file_pread(file, 0, probe) {
            Read::Got(unused) => {
            }
            Read::End => {
            }
            Read::Failed(reason) => {
                errno = reason;
                if errno == 0 {
                    errno = 5;
                }
            }
        }
    }
    if errno != 0 {
        return Done::Failed(errno);
    }
    return file_size(file);
}

// The last component of a normalised path: what follows its last `/`.
pub fn leaf[&p](p: &p [byte]) -> [] &p [byte] {
    var cut = len(p);
    while cut > 0 && int_of(p[cut - 1]) != '/' {
        cut = cut - 1;
    }
    return p[cut..len(p)];
}

// The directory part of a normalised path, `.` when it has none and `/`
// for a file at the top.
pub fn parent_of[&p](p: &p [byte]) -> [] &p [byte] {
    var cut = len(p);
    while cut > 0 && int_of(p[cut - 1]) != '/' {
        cut = cut - 1;
    }
    if cut == 0 {
        return ".";
    }
    if cut == 1 {
        return p[0..1];
    }
    return p[0..cut - 1];
}

// Whether a leaf names no file a writer could replace: the root itself
// (`.`) or the top of the tree (an empty leaf, from `/`).
pub fn is_directory_name[&p](name: &p [byte]) -> [] bool {
    return len(name) == 0 || bytes.equal(name, ".");
}

// One step into the directory `name`, telling a link from a file when the
// kernel answers `ENOTDIR` for both (see the header).
fn step[&d, &n](dir: &d Dir, name: &n [byte]) -> [dir_read] DirOpened {
    match dir_enter(dir, name) {
        DirOpened::Ok(sub) => {
            return DirOpened::Ok(sub);
        }
        DirOpened::Failed(reason) => {
            if reason != enotdir() {
                return DirOpened::Failed(reason);
            }
            match dir_open_read(dir, name) {
                Opened::Ok(file) => {
                    file_close(file);
                    return DirOpened::Failed(reason);
                }
                Opened::Failed(other) => {
                    if is_loop(other) {
                        return DirOpened::Failed(other);
                    }
                    return DirOpened::Failed(reason);
                }
            }
        }
    }
}

// Open the file `rel` beneath `dir`.
fn open_file[&d, &p](dir: &d Dir, rel: &p [byte]) -> [dir_read] Opened {
    let k = index_of_byte(rel, byte_of('/'));
    if k < 0 {
        return dir_open_read(dir, rel);
    }
    match step(dir, rel[0..k]) {
        DirOpened::Ok(sub) => {
            var held = sub;
            var out = Opened::Failed(5);
            borrow held as &h in {
                match out {
                    Opened::Ok(unused) => {
                        file_close(unused);
                    }
                    Opened::Failed(unused) => {
                    }
                }
                out = open_file(h, rel[k + 1..len(rel)]);
            }
            dir_close(held);
            return out;
        }
        DirOpened::Failed(reason) => {
            return Opened::Failed(reason);
        }
    }
}

// Enter the directory `rel` beneath `dir`.
fn enter[&d, &p](dir: &d Dir, rel: &p [byte]) -> [dir_read] DirOpened {
    let k = index_of_byte(rel, byte_of('/'));
    if k < 0 {
        return step(dir, rel);
    }
    match step(dir, rel[0..k]) {
        DirOpened::Ok(sub) => {
            var held = sub;
            var out = DirOpened::Failed(5);
            borrow held as &h in {
                match out {
                    DirOpened::Ok(unused) => {
                        dir_close(unused);
                    }
                    DirOpened::Failed(unused) => {
                    }
                }
                out = enter(h, rel[k + 1..len(rel)]);
            }
            dir_close(held);
            return out;
        }
        DirOpened::Failed(reason) => {
            return DirOpened::Failed(reason);
        }
    }
}

// Open an operand for reading. With a root (`root` non-empty), `rel` is
// the operand relative to it, as `path.shown` spells it, and is opened
// beneath it; `.` is the root itself, opened by its own name. Without
// one, `full` is opened as given.
pub fn open_operand[&f, &r, &p, &q](fs: &f Fs(""), root: &r [byte], rel: &p [byte], full: &q [byte]) -> [fs_read(""), dir_read] Opened {
    if len(root) == 0 || bytes.equal(rel, ".") {
        return open_read(fs, full);
    }
    match open_dir(fs, root) {
        DirOpened::Ok(opened) => {
            var dir = opened;
            var out = Opened::Failed(5);
            borrow dir as &d in {
                match out {
                    Opened::Ok(unused) => {
                        file_close(unused);
                    }
                    Opened::Failed(unused) => {
                    }
                }
                out = open_file(d, rel);
            }
            dir_close(dir);
            return out;
        }
        DirOpened::Failed(reason) => {
            return Opened::Failed(reason);
        }
    }
}

// Open the directory that holds an operand, for a writer: beneath the root
// with one, by name without. The writer then names the file by `leaf`.
pub fn parent[&f, &r, &p, &q](fs: &f Fs(""), root: &r [byte], rel: &p [byte], full: &q [byte]) -> [fs_read(""), dir_read] DirOpened {
    if len(root) == 0 {
        return open_dir(fs, parent_of(full));
    }
    match open_dir(fs, root) {
        DirOpened::Ok(opened) => {
            let k = index_of_byte(rel, byte_of('/'));
            if k < 0 {
                return DirOpened::Ok(opened);
            }
            var dir = opened;
            var out = DirOpened::Failed(5);
            borrow dir as &d in {
                match out {
                    DirOpened::Ok(unused) => {
                        dir_close(unused);
                    }
                    DirOpened::Failed(unused) => {
                    }
                }
                out = enter(d, parent_of(rel));
            }
            dir_close(dir);
            return out;
        }
        DirOpened::Failed(reason) => {
            return DirOpened::Failed(reason);
        }
    }
}

// Open an operand as a directory, for a lister. With a root, `rel` is
// entered beneath it following no link, and `.` is the root itself; without
// one, `full` is opened by name, links and all, as a reader's file is.
pub fn open_directory[&f, &r, &p, &q](fs: &f Fs(""), root: &r [byte], rel: &p [byte], full: &q [byte]) -> [fs_read(""), dir_read] DirOpened {
    if len(root) == 0 {
        return open_dir(fs, full);
    }
    if bytes.equal(rel, ".") {
        return open_dir(fs, root);
    }
    match open_dir(fs, root) {
        DirOpened::Ok(opened) => {
            var dir = opened;
            var out = DirOpened::Failed(5);
            borrow dir as &d in {
                match out {
                    DirOpened::Ok(unused) => {
                        dir_close(unused);
                    }
                    DirOpened::Failed(unused) => {
                    }
                }
                out = enter(d, rel);
            }
            dir_close(dir);
            return out;
        }
        DirOpened::Failed(reason) => {
            return DirOpened::Failed(reason);
        }
    }
}
