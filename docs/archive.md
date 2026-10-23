# `archive`: tar and gzip, bounded, and what a network tier would take

> **Status, 2026-10-19: built.** The design was written first (cancho `CONTRIBUTING.md`, "How a slice is done") and
> the code followed; §11 says what the build corrected and what it measured, and the sections above it were
> corrected in place where the build found them wrong. §1 to §8 are `archive`; §9 is the answer to "should `curl` and
> `ssh` be another project?". A claim below is either **measured** (and says where), **read** (and names the file),
> or marked **open**.

## 0. What is being decided

`archive` is the tenth tool. It reads, lists, extracts and writes `tar` archives, optionally gzip-compressed, and
gzips or gunzips a single file. It is admitted under D1 as amended (cancho `docs/agent-toolbox.md`, "Amended: twelve"):

| D1 condition | here |
|---|---|
| 1. a stated gain over the incumbent | §1 |
| 2. a versioned schema and a rule catalogue with a fixture per tag | `schemas/archive.v1.json`; §5 |
| 3. an authority row the compiler derives, with a person-written ceiling | §6; `tools.toml` `[archive]` |
| 4. every error path is a tag; no input reaches a trap | §5, §7 (a fuzz gate over hostile archives) |

## 1. Why, and the honest state of the evidence

`tar -x` is the textbook example of a tool whose behaviour is a matter of flags, versions and warnings on a stderr that
an agent often cannot see: which names it strips or refuses, whether it replaces what it finds, what it does with a
link or a device, and `gzip -d` of a small file can fill a disk. None of that is wrong for a person at a terminal who
reads the warning. The gain `archive` claims is the toolbox's gain, not speed (it is slower than GNU tar and gzip;
§11 reports by how much and gates only against pathologies):

* **Refusal by rule, not by warning.** An entry whose name is absolute, contains `..`, or reaches outside `--into` is
  `archive.unsafe-name`; an entry that is a link, device or FIFO is `archive.entry-kind`; an existing file is
  `conflict.exists`. Nothing is created on the way to discovering it (§4.3: the archive is fully validated before the
  first file is written).
* **Bounded by flags that are reported.** `--max-bytes` (total expanded size, default 64 MiB, ceiling 1 GiB) and
  `--max-entries` (default 10,000) stop a decompression bomb with `limit.archive-too-large`, whose detail names the cap;
  a gzip stream that expands past the cap is stopped at the cap, not after (measured, §11).
* **Deterministic create.** The same files give the same bytes: sorted entries, mtime 0, owner 0:0, mode 0644 or 0755,
  gzip header with mtime 0. Two trees of equal content and different times and umasks give equal bytes (measured, G7),
  which `tar | gzip` does not: it records times and owners, and gzip a name and a time.
* **Records with a schema, a cursor and an `end` record**, as every tool here.

**What is not established.** The repository's bar is two real askers (`CONTRIBUTING.md`, cancho `docs/standard-library.md`).
The evidence for `archive` is: the maintainer asked for it (2026-10-19); and `tar`/`gzip`/`gunzip` do not appear in the
list of commands the benchmark's `bash` arm ran that the tools do not cover (`docs/next-tools.md` §1; I read that list, not the
benchmark's logs), which is no evidence either way, because that task set was written around the first tools. **Open:** a second asker. The tool is proposed on the
maintainer's request, not on a count; if the count is wanted first, this document is the design and nothing needs to
be un-merged.

## 2. What the platform decides

All **read** from the cancho checkout this was written against (`7e4088b`, 2026-10-19) unless it says measured.

* **There is no inflate or deflate in `std`.** `std/` has `crypto.cho`, `bytes.cho`, `buffer.cho` and no
  `zlib`/`flate`/`gzip`; at the compiler this repository is pinned to (`62c2be5`) it has no `crc.cho` either (the
  newer compiler of 2026-10-19 does). DEFLATE (RFC 1951), gzip (RFC 1952) and CRC-32 are written in this package
  (`tools/archive/flate.cho`) and move into `std` when a second program wants them, which is cancho `AGENTS.md` §7's rule.
* **There was no `mkdir` (now there is, §13).** *As first written:* `crates/cancho-ir/src/builtin.rs` names these filesystem builtins: `fs_read`, `fs_write`,
  `fs_rename`, `fs_remove`, `open_dir`, `dir_enter`, `dir_open_read`, `dir_open_new`, `dir_open_append`, `dir_rename`,
  `dir_rename_new`, `dir_remove`, `dir_sync`, `dir_list`, `dir_next`, `dir_stat`, `dir_mode`, `dir_own_mode`. None
  creates a directory. **Consequence for `extract`:** it writes regular files, and every directory an entry needs
  must already exist beneath `--into` (§4.3, rule `archive.directory-missing`, which lists the missing directories so
  an agent can create them with whatever it has). This is the largest limit of the tool and it is the language's, not a
  design choice; the fix is a `dir_mkdir` builtin in cancho (§10, question 2), not a workaround here.
* **A directory is listed and stat'ed by handle** (`dir_list`, `dir_next`, `dir_stat` answering kind, size and mtime,
  never following a link; `docs/directory-listing.md`), so `create` can walk a tree beneath `--root` the way `list`
  does, and a link is seen as a link.
* **A write beneath a directory is atomic and never overwrites on request**: `toolbox.atomic` (`contract/atomic.cho`),
  `dir_open_new` (`O_EXCL`) and `dir_rename_new` (`renameat2(RENAME_NOREPLACE)`), the machinery `write --create` and
  `move` use (cancho-tools#32, #34). `archive` reuses `toolbox.atomic`'s reading, hashing and temporary names and has its own
  placing step, because `atomic.create` cannot be used for an extract (§6, §11).
* **Memory is the archive, except for `list`** (§12). A streaming inflater needs a resumable state machine, which a language
  with no closures and linear resources makes expensive, and the tar reader needs random access to validate before it
  writes (§4.3). So `extract` and `create` hold the expanded archive in memory, up to `--max-bytes`, and the tool as a whole
  **does not claim `bounded_memory`** in the sense `introspect` defines it ("peak memory does not grow with the input's
  size"): their memory is a small multiple of `--max-bytes`, stated by `introspect` as a limit (`limits`), and
  `test_archive.py` checks the *bound* on a decompression bomb (§11), not the flatness. `list` is the exception since
  §12: it reads a piece at a time and its peak is flat (2 MiB for a 2 MiB archive and for a 48 MiB one). This is weaker
  than the other tools and the design says so rather than rounding up.

### 2.1 The checksum

gzip's trailer is CRC-32 (IEEE 802.3, reflected polynomial `0xEDB88320`) and the length mod 2^32. It is a 256-entry
table and eight lines in `flate.cho`, held by the published check value (`"123456789"` is `0xCBF43926`, in
`tests/flate_test.cho`) and, end to end, by `gzip -t` and Python's `zlib` agreeing with it on every archive of
`test_archive.py`.

## 3. The command

```
archive --op list    [--root DIR] [--max-bytes N] [--max-entries N] ARCHIVE
archive --op extract [--root DIR] [--into DIR] [--dry-run] [--max-bytes N] [--max-entries N] ARCHIVE
archive --op create  [--root DIR] [--as tar|tgz|gz] [--dry-run] [--max-bytes N] [--max-entries N] ARCHIVE PATH...
```

* `--op` is `list` by default: the read-only mode is the one an agent gets when it forgets.
* **`--root` defaults to the working directory, and `archive` is always confined to it**: unlike the read-only tools, which
  without `--root` open any path they are given, every path here (the archive, `--into`, the PATHs, and each name an
  archive holds) is relative and beneath a directory, following no link (`toolbox.place`). An absolute path outside it is
  `path.outside-root`, one inside it `path.absolute`.
* `ARCHIVE` is a path beneath `--root` (the same `path.*` rules as every tool). On `list` and `extract` the format
  is **detected from content**, never from the name: gzip magic `1f 8b`, else a tar header with a valid checksum
  (`archive.not-an-archive` otherwise). A `.tar.gz` that is really a plain tar still lists.
* `--into DIR` (extract) is a directory beneath `--root`, default `.` (the root itself).
* `--as` (create): `tar`, `tgz` (a gzip-compressed tar) or `gz` (one file, compressed, no tar). Default `tgz`. It is a
  flag, not a guess from the name, so the output is a function of the arguments.
* `create` takes files and directories beneath `--root`. A directory contributes its own entry and everything beneath
  it, sorted bytewise, links **refused** (`archive.entry-kind`, never followed, never archived), depth at most 64 (deeper is `limit.too-many-entries` with `max_depth` in its detail).
  `ARCHIVE` is never overwritten (`conflict.exists`); to replace one, move the old one first.
* `--dry-run` (create, extract) does the whole validation and reports what would be written, with exit 9 and no
  mutating system call (as `write`, M7, under `strace`).
* `introspect` and `skill` as every tool.

## 4. Semantics

### 4.1 What is a tar here

POSIX ustar (`ustar\0` + `00`) and the old V7 header, plus the two extensions that real archives use for long names:
GNU `L` (long name) records and pax `x` extended headers with the keys `path` and `size` (other keys, and `g` and `K`
records, are skipped). Entry types: regular file (`0`, `\0`), directory (`5`); a hard link (`1`), symbolic link (`2`), character
or block device (`3`, `4`), FIFO (`6`), and anything else are **listed** with their kind and **refused on extract**
(`archive.entry-kind`). Sparse files (GNU `S`) and multi-volume are `archive.entry-kind`. The checksum of every header
is checked (`archive.bad-checksum`); the end of an archive is the first all-zero block (a writer emits two, and `archive` writes two),
and an archive that stops before one is `archive.truncated`, not an end of input.

### 4.2 What is a gzip here

RFC 1952, one member (a second member is `archive.bad-gzip`, deliberately: the common cause is a concatenation an
agent did not intend), the header flags FEXTRA/FNAME/FCOMMENT/FHCRC skipped correctly on read, the CRC-32 and ISIZE
checked (`archive.crc-mismatch`; FHCRC is skipped, not verified). On write: no name, mtime 0, XFL 0, OS 255 (unknown), so the header carries no fact of
the machine. All three block types of RFC 1951 are read; the writer emits one block in the **fixed** Huffman code with an LZ77 pass
of a 32 KiB window and a hash chain of at most 16 candidates (no dynamic code, no lazy matching: simpler, and the reason
its output is larger than gzip's, §11).

### 4.3 Extract is a plan, then a write

1. Read the whole archive (inflating if gzip) up to `--max-bytes`, else `limit.archive-too-large`.
2. Walk every header. Collect every error; for each entry compute its destination beneath `--into` by the `path.*`
   rules (`archive.unsafe-name` for a name that is absolute, has an empty or `..` component, has a NUL, or is over
   4,096 bytes; two *files* whose names normalise equal are `archive.duplicate-entry`; a directory may repeat, as tars do).
3. Check the plan against the filesystem (as amended by §13): `--into` and everything above it exist (else
   `archive.directory-missing`, naming the shallowest missing one; extract never makes its destination root), a name an
   entry needs as a directory that holds a file or a link is `conflict.exists`, no file destination exists (else
   `conflict.exists`, one error per path, so the agent sees them all), a name that is a file in one entry and a directory
   (or the parent of an entry) in another is `archive.duplicate-entry`, and an entry whose mode has a setuid, setgid or
   sticky bit is `archive.special-mode`.
4. **Only if the plan is clean** write the files, each atomically (a synced temporary renamed with the kernel refusing a taken name, §6), in archive order, and
   report each. A failure in the middle (a full disk) leaves the files written so far, each whole, and the `end` record
   says `complete:false` and how many were written: the same honesty as `write`, with no pretence of an all-or-nothing
   extract (a rename of a directory would give it, and `mkdir` is the missing builtin of §2).
5. **Owner and mtime are not restored; the permission bits are (§13).** *As first written:* a file got what `dir_open_new`
   creates (measured: 0644 under a umask of 022), so the execute bit of an entry was lost on extract; there was no `chmod`,
   `chown` or `utimens` in the language, and a restored mtime would make extract depend on the archive in a way the
   deterministic-output gate would have to carve out. `create` reads the execute bit (`dir_mode`) and writes 0755 for such
   a file and for every directory, 0644 otherwise; `extract` now applies the same two modes.

Race: between step 3 and step 4 another process can create a destination. `dir_rename_new` makes that a
`conflict.exists` for that one file and never a lost file (the same measured property as `write --create`, cancho-tools#34).

### 4.4 Output

A stream, as every tool's (cancho `docs/agent-toolbox.md` D2): no `end` record means the stream was cut.

* `list`: `{"type":"entry","path","kind","size","mode"?,"unsafe"}` per entry (`kind` is `file`, `directory`, `hardlink`, `symlink`,
  `device`, `fifo`, `sparse` or `other`; `unsafe` is true for a name `extract` would refuse; `mode` is absent for a single gzipped file).
* `extract` and `create`: `{"type":"file","path","bytes","sha256"}` per file written (or, with `--dry-run`, that would be) and
  `{"type":"dir","path"}` per directory (extract: one that exists; create: one added). The hash is the one `write --if-sha256`
  and `move --remove --if-sha256` want, so an extract is undone by naming what it reports.
* then every error as `{"type":"error","error":{…}}`, then `{"type":"end","ok","complete","op","format","entries","bytes","errors","dry_run"}`, and for a
  successful `create` also `"archive_sha256"`. Exit status: the first error's, else 0, else 9 for a completed dry run.

## 5. Rules

The tool's own tags are in its `extra_rules` (`toolbox.rules`, "A tool's own rules, beside the shared catalogue"), so the
shared catalogue and its 39 fixtures are untouched; `test_archive.py` holds a fixture for each of these:

| tag | exit | meaning |
|---|---|---|
| `archive.not-an-archive` | 8 | neither gzip magic nor a tar header with a valid checksum |
| `archive.bad-gzip` | 8 | gzip header invalid, a reserved flag, a second member, or bytes after the member |
| `archive.crc-mismatch` | 8 | the gzip trailer's CRC-32 or length is not the data's |
| `archive.bad-deflate` | 8 | the DEFLATE stream is invalid (bad block, distance too far, bad code lengths) |
| `archive.bad-checksum` | 8 | a tar header's checksum is wrong (after the first header, which is `not-an-archive`) |
| `archive.bad-header` | 8 | a tar header's size or mode is not octal |
| `archive.truncated` | 8 | the archive ends inside a header, a body, or before the end marker |
| `archive.unsafe-name` | 4 | an entry name is absolute, has `..` or a NUL (4, as `path.outside-root`: a confinement refusal) |
| `archive.entry-kind` | 8 | an entry is a link, device, FIFO, sparse or unknown type |
| `archive.duplicate-entry` | 8 | two entries name the same file |
| `archive.directory-missing` | 8 | `--into`, or a directory above it, does not exist (§13: extract makes the directories an entry needs beneath `--into`, never `--into`); the detail names the shallowest missing one |
| `archive.no-match` | 8 | (list, extract) a selected path matches no entry of the archive (§14); an extract writes nothing |
| `archive.bad-bzip2` | 8 | a bzip2 stream that is not valid, or bytes after it (§16) |
| `archive.bad-xz` | 8 | an xz stream that is not valid, or bytes after it (§17) |
| `archive.special-mode` | 8 | (extract) an entry's mode has a setuid, setgid or sticky bit (§13); nothing was extracted |
| `archive.name-too-long` | 8 | (create) a name does not fit a ustar header |
| `limit.archive-too-large` | 8 | expanded size would pass `--max-bytes` (or the file grew while it was read) |
| `limit.too-many-entries` | 8 | more than `--max-entries` entries, or a tree deeper than 64 |

and the shared `args.*`, `path.*`, `io.*`, `conflict.exists`, `conflict.locked`, `io.rename-unsupported`. A rule
carries a `repair` only when one is mechanical; none of these is, so every `archive.*` repair is `null` and the hint
says what to do. (The raising of a limit to the size seen, as `limit.line-too-long` does, is the one candidate; it is
not built: `--max-bytes` is a memory ceiling the caller should choose, not a number to copy from an error.)

## 6. Authority and guarantees

* **Row:** `write`'s without `io_read`: `args`, `dir_read`, `dir_write`, `err_write`, `file_read`, `file_write`,
  `fs_read("")`, `heap`, `io_write`. No `exec`, `net`, `ffi`, `clock`. `tools.toml` `[archive]` is this and
  `manifest.py --check` holds it both ways; `test_authority.py` holds it equal to `write`'s less `io_read`.
* **Guarantees claimed:** `deterministic` (create writes the same bytes for the same files, and every list is the same)
  and `dry_run` (`--dry-run` makes no mutating call, under `strace`). **Not claimed:** `bounded_memory` (§2), `idempotent`
  (a second extract is `conflict.exists`, which is the point), `requires_precondition`. `atomic` is true of each
  file (a temporary renamed with the kernel refusing a taken name) but the guarantee is defined as the shared M7 gate
  and that gate is not wired for this tool, so it is not claimed rather than claimed on a different evidence.
* **Reversibility:** `irreversible-bounded`, `write`'s class: it adds files, never changes or removes one, and an
  extract is undone by `move --remove` on each reported path (every `file` record carries the hash `move --remove` needs).
* **Temporaries:** `.<name>.<hash>.lexsys-tmp` beside the target, as `write`'s. Unlike `write`, `archive` takes no lock
  sidecar (an extract of ten thousand files would leave ten thousand), and it does not remove a temporary it finds: that
  may be another extract's, so it is `conflict.locked`, and a stale one after a crash is a person's to clear.

## 7. Gates

Stated before the numbers existed, and now the tests that hold them (`tests/conformance/test_archive.py`, 25 tests;
the unit tests are `tests/flate_test.cho` and `tests/tar_test.cho`; `scripts/archive_mutants.py` is G10):

| | gate | where |
|---|---|---|
| G1 | **Round trip against the incumbents.** A corpus (sources, binary, empty files, lengths of 512 and 1024, names of 99/100/101 bytes, a name that must be split into a prefix, a 3 MiB file of zeros, an execute bit): `archive create` is read by GNU `tar -tzf`/`-xzf` and `gzip -t`; GNU `tar -c`/`-cz` and `gzip -c` output is listed and extracted by `archive` to the same bytes; GNU long names and pax headers are read. | `test_what_archive_writes_...`, `test_what_gnu_tar_...`, `test_gnu_long_names_...` |
| G2 | **DEFLATE.** zlib's output at levels 0, 1, 6, 9, `Z_FIXED`, `Z_HUFFMAN_ONLY`, `Z_RLE` of seven kinds of input (a tar, text, a constant, noise, skewed, one byte, none) lists and extracts to the original; damage to a gzip is the right tag. | `test_stored_fixed_and_dynamic_...`, `test_gzip_damage_...` |
| G3 | **Hostile archives never trap.** Every cut of a tar and a gzip, every changed byte (a stride of 5 above 1.5 KB), listed and extracted: always a valid stream with an `end` record, never a signal. | `test_every_cut_and_every_changed_byte_...` |
| G4 | **Bombs stop at the cap.** A gzip of 521,844 bytes that expands to 512 MiB, `--max-bytes 16MiB`: `limit.archive-too-large`, exit 8. Memory and time are in §11. | `test_a_bomb_stops_...` |
| G5 | **Confinement.** `..`, absolute, a NUL, links, devices, FIFOs, a link on disk in the way (`path.symlink`), paths outside `--root`. Nothing outside is ever written (checked by listing the outside directory). | `test_names_that_climb_...`, `test_links_...`, `test_a_link_on_disk_...`, `test_paths_outside_...` |
| G6 | **Plan before write.** An archive with a bad last entry, a name taken, a missing directory or a duplicate leaves the tree unchanged, and every problem is reported, not the first. | `test_one_bad_entry_...`, `test_a_missing_directory_...` |
| G7 | **Create is deterministic.** Two trees of equal content and different mtimes and umasks give byte-identical archives; owner 0:0 and 1970-01-01 in `tar -tv`. | `test_create_is_a_function_...` |
| G8 | **Race.** Eight extracts of one archive at once, five rounds: every file is written by exactly one, whole, and no temporary is left. | `test_racing_extracts_...` |
| G9 | **Reported, not gated.** Ratio and time against `gzip` and `tar` on 64 MiB (§11). | §11 |
| G10 | **Mutants.** `scripts/archive_mutants.py` breaks the checks above one at a time; each must turn a test red. | §11 |

Also: a dry run makes no mutating call (`strace`); every flag error is a tag with exit 2; every `archive.*` and
`limit.*` rule of the tool is reached by a fixture; the authority row is `write`'s less `io_read`.

## 8. Non-goals

`zip`, `xz`, `bzip2`, `zstd`; multi-member gzip and concatenated gzip; random access into a `.tar.gz`; archiving links
or devices; restoring owner, mtime or special modes; extracting into a `--into` that does not exist (§13);
streaming from standard input (a path only; `--stdin` can be added if an asker appears); encryption.

## 9. `curl` and `ssh`: another project, and would cancho help?

**Short answer: yes, another project; and cancho gives one real advantage that no other language gives these tools, plus
three costs that are specific to it.** The question is not whether cancho *can* open a socket (it can: `Net`,
`net_out("host:port")`, `docs/net.md`) but whether the property that defines this repository survives it.

**Why not in this repository.** `scripts/manifest.py` has `FORBIDDEN = {"ffi", "net_out", "net_in", "clock"}` and
`tools.toml` says "No tool may hold ffi, net_out, net_in or clock". The README sells "no network" as a checked
property of nine binaries. A tenth that holds `net_out` changes what the sentence "none of these tools can reach the
network" means for the whole set, in every deployment. A separate repository (the way `cancho-table` is outside the
count, `docs/next-tools.md` §5) keeps that sentence true here and lets the network tier have its own ceiling file,
its own conformance (SSRF, redirects, credentials), and its own review cadence. Recommendation: `cancho-net` (a name,
not a decision), reusing this package's `contract/` through the published package (`scripts/package.py`).

**What cancho gives that C does not (read, not measured).**

* **A host allow-list in the authority row.** `net_out("api.example.com:443")` is a label the compiler derives; the
  grant a deployment must give is that row, and `bounded: true` holds only if no `ffi` is reached
  (`docs/net.md`, `docs/foreign-authority.md`). `curl` has the process's whole network authority and the OS firewall is
  the only bound. For an agent runtime, a binary that *cannot* reach any other host, shown by `cancho authority`, is the
  same kind of fact as "`seek` cannot write". **Not yet literally true, and it matters:** the bound is checked at run time against the destination
  (`docs/net.md` §4, and its "Correction (#77)": the literal is a bound, not a destination). Measured against cancho
  (2026-10-20): the bound splits at its last `:`; the **host is a prefix match** (a bound of `127.0.0.1:443` admits
  `127.0.0.10`, and `ns1.example.com` admits `ns1.example.com.attacker.net`); the **port is exact** (a `:44` bound traps on
  443 and a `:443` bound traps on 4430; an earlier version of this paragraph guessed the opposite and was wrong); a bound
  with no `:` checks no port. One `Net` still carries one bound for dialing and for listening. cancho's `docs/net-bounds.md`
  has the fix as a design: its steps 1 (an unnarrowed `Net("")` is no longer reported `bounded: true`) and 2 (several
  bounds from one `narrow`, and a `narrow` that can no longer swap one port for another) are built; step 3 (exact hosts,
  an edition) is not.
  A `fetch` whose row is to be trusted as an allow-list needs that design built first.
* **TLS without `Ffi`.** The reason D15 row 9 (`fetch`) said "the dominant case defeats the premise" was that HTTPS
  needed `Ffi` and made the report `bounded: false`. cancho now has `packages/tls` (TLS 1.3 client and server, no C
  library, `docs/tls-pure.md`, `docs/tls-server.md`), `packages/x509`, `packages/http-client`, and the primitives
  (`std.x25519`, `std.ed25519`, `std.chacha20`, `std.gcm`, `std.hkdf`, `std.ecdsa`, `std.rsa`). A `fetch` with a bounded row
  is therefore possible for HTTPS, which it was not when D15 was written. **Caveat that matters:** the TLS stack's own
  status line is "not independently reviewed (#209)". An agent tool that fetches secrets over a protocol implementation
  nobody outside the project has reviewed is a risk to name, not to hide.
* **Refusals as data.** The same envelope: `net.redirect-limit`, `net.host-not-allowed`, `net.body-too-large` with
  a `repair`, rather than curl's exit 22/35/60 and a sentence on stderr.

**Costs specific to cancho.**

1. **`narrow` takes a literal.** The host list is baked at build, like `run`'s programs directory. There is no `Dir` for
   `Net` (cancho `docs/processes.md` §4.14 built one for `Fs` and `Exec`, and left "a bound that travels with the value"
   undone). So each deployment builds `fetch` with its own hosts. For a security tool that is arguably a feature; for
   ergonomics it is a build per allow-list. A runtime-chosen host can only be had by holding `net_out("")`, which is the
   unbounded row.
2. **`curl` is not a small tool.** The 20% of curl that an agent needs is `GET`/`POST` of bytes with headers, a size cap, a
   redirect cap and a timeout; the other 80% (proxies, cookies, auth schemes, FTP, HTTP/2, certificate pinning flags)
   is a bottomless pit of POSIX-fidelity (`docs/flags.md`). A `fetch` that refuses everything else with a tag and a
   pointer to `curl` is the same stance `jsonq` takes towards `jq`. And `clock` is needed (a deadline), so it needs
   the same exception `run` has.
3. **`ssh` is a different project, not a tool.** An SSH client is a transport (RFC 4253), key exchange
   (`curve25519-sha256` is available from `std.x25519`), host-key verification and a known-hosts policy, user-auth
   (`ssh-ed25519` is available), channels and a PTY. The primitives are there; nothing above them is. Its
   dangerous parts are not cryptographic but policy: whose keys, which commands, what a session is allowed to do after
   it starts, and `ssh host cmd` is `run` on another machine, which is `irreversible-consequential` over a
   boundary the authority row cannot show. I would not build it before the question "what is the agent allowed to do on
   the other side" has an answer that a reviewer can read in a row, and a `ssh` tool whose answer is "anything" is a
   backdoor with a nicer envelope.

**Order if it is taken up.** (0) cancho's exact host matching and the report fix of `docs/net-bounds.md`, which are cancho's to build; (1) `fetch`, GET/HEAD/POST of bytes, plain HTTP to a baked host first (the row D15 already
describes, `net_out("h:80")`, `bounded: true`), HTTPS second behind the #209 caveat; (2) an HTTP-only `post` if an asker
shows; (3) `ssh` only as a design document in that repository until (1) has shipped and the policy question is answered.
Each is its own D1 admission. None is in this repository.

## 10. Open questions for the maintainer

1. **A second asker for `archive`** (§1)? Proposed on request, not on a count.
2. **A `dir_mkdir` builtin in cancho.** Without it `extract` cannot create directories (§2). The cost is a builtin on
   both Rust backends and the compiler in cancho (`CONTRIBUTING.md`, "The compiler in cancho"), edition-gated like
   `dir_rename_new` (cancho#351). **Done: `dir_mkdir` (cancho#595) and `dir_open_new_mode` (cancho#596) are built, and §13 uses them.** Worth a cancho issue now; `archive` needs no change when it lands except dropping
   `archive.directory-missing` for a creation, which is a minor version.
3. **Memory vs. streaming** (§2). Memory bounded by `--max-bytes` (default 64 MiB) is simple; a streaming inflater is a
   larger piece of work that would restore `bounded_memory`. **Done for `list` (§12); `extract` and `create` are not.**
4. **Create from stdin / extract from stdin.** Not offered; the MCP route has no pipe for binary data (a `stdin` string
   is text). Accept?
5. **Ceiling.** Twelve, with `run` and `archive` making eleven (cancho `agent-toolbox.md` D1, amended).

## 11. What the build corrected, and what it measured

Every item is something the design above said, or would have said, that the build found false or incomplete; the section
it affected was corrected in place.

* **The first real gzip trapped the reader.** The unit tests wrote their own archives, and the writer only emits the
  fixed Huffman code, so no test had a *dynamic* block until `tar czf` made one. The table of code lengths was 320 long
  and a dynamic header needs up to 19 + 286 + 30 = 335. Now 400, and `test_archive.py` (G2) feeds zlib's dynamic,
  fixed, stored, Huffman-only and RLE output so the next such mistake is red. Lesson kept in the design: a decoder is
  not tested by its own encoder.
* **The design reused `toolbox.atomic.create` for extract; it could not.** That function removes a temporary that already
  exists (it assumes the caller holds `write`'s lock sidecar), and two extracts of the same archive name the same
  temporary, so the second would delete the first's file mid-write. `write`'s sidecar would leave one lock file per
  extracted file. `archive` has its own `place_file`: exclusive create, `fsync`, `dir_rename_new`, and a temporary that is
  already there is `conflict.locked`. G8 (eight racing extracts, five rounds) holds it, and two mutants (a replacing
  rename; a stale temporary taken for one's own) turn it red.
* **Extract cannot restore the execute bit** (§4.3, §2): `dir_open_new` takes no mode and there is no `chmod`. Measured:
  a 0755 file in a GNU tar extracts as 0644 under a umask of 022. `create` does record it.
* **An archive of nothing but zeros is an empty tar**, by the tar format's own end marker, so a gzip of a megabyte of
  `0x00` lists as a `tgz` of no entries, not as a `gz` of one file. A gzip of a megabyte of `0x07` is the file. The
  test that first used zeros was wrong about this, not the tool.
* **`gzip -c FILE` stores `FILE`'s name** in the header, so `archive` lists it under that name; a gzip with no name is
  named for the archive without `.gz` (§3).
* **The rule table changed:** `archive.bad-header` was added; `archive.directory-missing` is exit 8, not 5 (5 is a
  conflict); `archive.unsafe-name` is exit 4, the confinement class. A tar smaller than 1,024 bytes was first
  `not-an-archive` even with a valid first header; a valid first header is now enough (512 bytes), so a cut tar is
  `archive.truncated`.
* **The missing-directory error names the shallowest missing directory** (`a`, not `a/b/c/f.txt`'s parent), because
  that is the one to make first.
* **G4 was stated as "peak RSS under cap + 8 MiB".** Measured: a gzip of 521,844 bytes that expands to 512 MiB, with
  `--max-bytes 16MiB`, is refused in 0.05 to 0.16 s with a peak RSS of **18,608 KiB, 1.14 times the cap** (the output
  buffer grows by doubling, and stops at the cap). The test bound is `2 * cap + 8 MiB`.
* **The first memory numbers were Python's.** A tool started from a Python test and measured with `os.wait4` reports at
  least the test runner's own peak (Linux keeps the larger of the old and new address space's mark across `exec`), and
  `maxrss.c` exists for that reason (its header says so; `test_memory.py`'s first version made the same mistake). The
  bomb first read 27,400 KiB; with `maxrss.c` it is 18,608. `harness.peak_rss` is the one way to measure now.
**Measured, not gated** (G9; Linux x86-64, a shared machine, one run, 64 MiB of random lowercase words, GNU tar 1.35 and
gzip 1.12 as found):

| | `archive` | incumbent |
|---|---|---|
| compress (create a `tgz` of one 64 MiB file) | 2.74 s | `gzip -c`: 1.91 s |
| compressed size | 32,468,365 bytes | `gzip -c`: 25,088,851 bytes (`archive` is 29 % larger: fixed Huffman, no lazy matching) |
| inflate (`--op list` of that `tgz`, CRC checked) | 1.24 s | `gzip -dc`: 0.54 s |

and on a 400 KB tree (100 KB of noise, 300 KB of repeated text) `archive create` makes 107,867 bytes where `tar czf`
makes 101,291. A million zero bytes compress to about 6.3 KB where a dynamic code gets under 1 KB (`tests/flate_test.cho`
holds the bound).

**Speed, measured again and improved** (`scripts/bench_archive.py`; Linux x86-64, 4 cores, a shared machine, medians of 3; one 64 MiB file of random
lowercase words from a 20,000-word vocabulary, which gzip compresses to 39.6 MB; GNU tar 1.35, gzip 1.12; wall seconds). The
first column is `main` before this change, built the same way on the same machine:

| job | `archive` before | `archive` now | incumbent |
|---|---|---|---|
| `--op create --as gz` | 4.69 s | **3.14 s** | `gzip -c`: 2.94 s |
| `--op create --as tgz` | 4.71 s | **3.43 s** | `tar czf`: 2.98 s |
| `--op list` of a tgz (inflate, CRC, tar walk) | 2.18 s | **1.16 s** | `tar tzf`: 0.86 s |
| peak resident memory, create / list | 176 MiB / 171 MiB | the same | gzip 21 MiB |
| size of the gz / tgz written | 55,898,202 / 55,898,261 B | **the same bytes** | gzip: 39,596,303 B (**41 % smaller**) |

The compressed bytes did not change (a test over eight inputs, from empty and one byte to random, runs of zeros, long
distances and repeats, compares the old and new writers byte for byte, and `gzip -dc` and `tar xzf` read them), so
nothing that compares an archive to an earlier one is affected. What `callgrind` showed, on a 4 MiB input, and what was done:

* **Inflate: 100 instructions a symbol in a function that only returned the next one.** The decoder looped over a code's bits,
  passing and returning the four-field `Bits` struct for each, and wrote one byte at a time through `buffer.push`. Now a
  10-bit lookup table (8 for distances) finds a symbol in one read, the bit buffer lives in four locals inside the loop,
  and the output is written straight into the buffer's allocation (grown by `reserve` when it is nearly full). A code longer
  than the table, and the last bytes of the input, take the old careful path, which is where truncation is detected.
  Inflate of a tgz: 2.18 s to 1.16 s.
* **Create hashed the archive twice.** `put_new` computed the SHA-256 of what it was about to write (it names the
  temporary file) after `create` had computed it for the answer: 174,755 block compressions for a 4 MiB file is the file
  (65,536) plus the archive twice (2 x 54,608). SHA-256 was 44 % of the instructions of `create`. It is hashed once now,
  and `extract` is unchanged (it still hashes each file once). The SHA-256 itself is already tuned (about 81 instructions
  a byte) and was not touched.
* **The writer reversed bit patterns in a loop for every symbol, searched for the length and distance code linearly, and
  pushed output a byte at a time.** Now the fixed code, the length codes and the distance codes are read from tables built
  once, a match's whole code (length, extra bits, distance, extra bits) is written as one value, and the buffer is reserved
  for the worst case (9 bits a byte) up front. The match search skips a candidate that differs at the byte just past the best
  length so far, which cannot win, and stops when the best is the longest possible; it makes the same choices as before.

**Round two: dynamic Huffman blocks and lazy matching** (same machine, same input; the compressed bytes now differ from
every earlier version, and `gzip -dc` and `tar xzf` read them):

| job | `archive` round 1 | `archive` round 2 | incumbent |
|---|---|---|---|
| `--op create --as gz` | 3.14 s | 4.58 s | `gzip -c`: 2.95 s |
| `--op create --as tgz` | 3.43 s | 4.53 s | `tar czf`: 3.01 s |
| `--op list` of a tgz | 1.16 s | 1.31 s | `tar tzf`: 0.82 s |
| size of the gz written | 55,898,202 B | **39,568,288 B** | gzip: 39,596,303 B |

The output is 29 % smaller than round 1 and within 0.1 % of gzip on this input (within 0.4 % of zlib level 6 on a fuzz of
157 inputs of every kind: text, runs, noise, long distances, empty). Each block of up to 32,768 tokens is written as
whichever of stored, fixed or dynamic costs the fewest bits, so noise no longer grows. Creating costs 46 % more time than
round 1 (lazy matching looks at two positions for each match); list uses the same inflater and the spread of its runs (1.05 to 1.67 s) covers the difference. The cost is the price of the size: gzip is 1.55 times
faster at the same size, and the remaining gap is the match search (chain depth 16, no wide compare in the language).

**What is still slower, and why it is not fixed here:** `create` is 55 % slower than `gzip -c`, and `list` is 60 % slower
than `tar tzf`; the remaining cost is the byte loops in the match search, the CRC and the match copy, which the language
has no wide operation for.

**Mutants (G10)**, `scripts/archive_mutants.py`: **29 of 29 killed** on the committed code. The first run killed 20: nine survived, and each
survivor was a test that could not have failed (the execute bit, the gzip length, a pax size, the end marker, the header
time, a tar size past the data, a code that is not a code, a stored block past the cap, the bound on the header counts), so a
test was written for each and the runner now also runs the cancho unit tests. One survivor was equivalent at the command line
(a stored block cannot out-grow the file that holds it) and is killed by a unit test that passes the limit directly. A
mutant that looked like it survived once was a stale binary left by the previous mutant; the runner now rebuilds the real one at the end.

**Not done, and said so:** a streaming inflater (§2); `mkdir` (cancho's); any `zip`, `xz`,
`bzip2`, `zstd`; restoring modes and times; a second asker (§1).

## 12. Reading a piece at a time

Status: built for `--op list`; `extract` and `create` still hold the archive (§12.5).

`list` used to read the file whole, inflate it whole and parse the tar whole: 171,444 KiB of peak memory for the 64 MiB
benchmark archive (§11), and 18,608 KiB to refuse a 512 MiB decompression bomb. It no longer does either.

### 12.1 The pieces

* **`archive.stream`** (`tools/archive/stream.cho`) inflates one gzip member through a 64 KiB input buffer and a 128 KiB output
  window, driven by a loop in the caller: `step` answers *output* (a piece of at most 32 KiB), *input* (read the file into
  `room`, tell it how much with `supplied`; 0 is the end of the file), *done*, or an error that is one of
  `archive.flate.gunzip`'s statuses. There is no coroutine and no closure: the inflater is re-entered **at a symbol
  boundary**. `codes` asks for input before it starts a symbol it cannot be sure to finish (48 bits are enough for the longest
  literal/length code, its extra bits, a distance code and its extra bits), a block header waits until 512 bytes are held (a
  dynamic header is under 300), and what has to be kept between two calls is the bit buffer, the phase and the tables.
  The window keeps the last 32 KiB as history and slides when 64 KiB have been handed out. The CRC-32 and the length are
  accumulated on the pieces and checked against the trailer; the trailer is not judged until it is known whether bytes
  follow it, so the same damage has the same status as in `gunzip`.
* **`tar.scan_feed`** reads the tar a piece at a time: a 512-byte header buffer, the body of one pax or long-name header,
  and the name of one entry. It makes the entries `tar.parse` makes, with the same names, kinds, sizes, flags and failures in
  the same order. An entry is made only when its body (to the block) has been seen, as `parse` makes it only when the body is
  in the data, so a cut archive lists the same entries and fails at the same offset.
* **`list_file`** in `archive.cho` ties them: the first two bytes say gzip or not, the first 512 (or 1,024, for a zero first
  block) bytes of content say tar or one file, and each piece goes to the scanner and each entry it completes is printed.

### 12.2 What is the same, and how that was shown

* **Output.** `list` of twenty cases (GNU tar and gzip of a tree with a link, a single `.gz`, an empty `.gz`, a damaged `.tgz`,
  a cut `.tgz` and `.tar`, a text file, an empty file, a missing file, a directory, a path outside the root, no operand, an
  extra operand, `--max-entries` and `--max-bytes` on each) is byte for byte what the previous build printed, but for the
  differences in §12.3 (two of the twenty showed them, and found one slip of mine: the path in a tar error's detail). All 29 conformance tests of the earlier build pass unchanged.
* **The inflater.** `tests/stream_test.cho`: streaming equals `gunzip` for inputs from empty to 400 KB (noise, a short
  alphabet and word text, so stored, fixed and dynamic blocks), at eleven chunk sizes from 1 to 65,536 bytes; and
  for damage (every byte of the first 200 and last 20 changed, a cut every 13 bytes, a limit, bytes after the member) it
  gives the same status as `gunzip`. The test found three bugs in the first version, each a real one: giving back the bytes
  the bit buffer took ahead after the input had moved (a trap, only at small chunk sizes), an empty file reported as
  truncated rather than as not gzip, and a trailer cut short reported as the wrong status.
* **The scanner.** `tests/tar_test.cho`: `scan_feed` against `parse` on six archives (the writer's, a GNU long name, a pax
  path and size, global and link headers, odd and unsafe names, block-sized bodies) at eleven chunk sizes from 1 byte to 88,573, with `--max-entries`
  of 1, 2 and 1,000, with a cut every 41 bytes, and with one byte changed every 7 bytes (outside bytes 400 to 500 of a block,
  which no field uses): the same entries (a fingerprint of
  name, kind, size, mode and flag), the same count, the same failure at the same offset. Five deliberate breaks of the
  scanner (the pax size ignored, a name kept for the next entry, the entry count off by one, the cut offset wrong, a held
  entry never made) each turn it red.

### 12.3 What is different

* **Entries come before a late error.** The whole archive was validated before anything was printed; a damaged gzip printed no
  entries and one error. Now the entries before the damage are printed, and the error follows them: `end` says `ok: false`
  and `complete: false` as before. A caller that acts on an entry line before it has read the `end` record is acting on an
  archive that may turn out to be damaged; that was always the contract of a `stream` output and is now visible.
  When the gzip is damaged the gzip error is the only error (the tar's own failure at that point is not reported), as it was.
* **A pax or long-name header body over 64 KiB** is `archive.bad-header` (it was read whatever its size). If it is claimed but
  the data ends first, the archive is `archive.truncated`, as before.
* **A plain tar larger than `--max-bytes`** lists the entries up to the limit and then reports `limit.archive-too-large`
  (it printed none).
* **`--max-bytes` no longer bounds the size of the compressed file**, only what it expands to (a compressed file cannot
  expand to less than its own length, so a gzip larger than the limit still exceeds it).

### 12.4 What it measured

Linux x86-64, 4 cores, a shared machine; the 64 MiB word text of §11, `tar czf`, 39.6 MB.

| `list` | before | now | `tar tzf` |
|---|---|---|---|
| peak resident memory | 171,444 KiB | **2,068 KiB** | |
| wall time | 1.1 to 1.6 s | **0.80 to 0.83 s** | 0.82 s |
| the 512 MiB bomb, `--max-bytes 16 MiB` | 18,608 KiB | **2,068 KiB** | |
| a 2 MiB tgz / a 48 MiB tgz | | 2,132 / 2,068 KiB | |

The time falls, I think, because nothing is allocated, zeroed or copied at the size of the archive (the earlier build grew a
64 MiB buffer by doubling and walked it a second time for the tar); I did not profile the earlier build to say how much of its
time that was. The inflater itself is not faster: the `codes` loop is the earlier one, with a check for input at the top of each
symbol.

### 12.5 What is not done

`extract` still reads, inflates and checks the whole archive before it writes anything (§4.3), and `create` still builds the whole
tar and the whole gzip in memory. Streaming `extract` is two passes over the file (the first checks every name and every
destination, as now; the second inflates again and writes each file as it goes), and streaming `create` is a deflate that
writes its output as it goes into a temporary file that is renamed at the end. Both are possible on these pieces, and
neither is built: the tool as a whole still does not claim `bounded_memory`.

## 13. Extract makes directories and restores modes

> **Status: built on the compiler of cancho#602 (`dir_mkdir`, cancho#595; `dir_open_new_mode`, cancho#596).** This section
> amends §2, §4.3 and §5; those sections say where they were first written otherwise.

`extract` was limited by the language, not by choice (§2): it could not make a directory, so an archive of a tree could only
be unpacked into a tree that already existed, and it dropped the execute bit. Both are now possible.

* **Directories.** A directory an entry needs beneath `--into` and the filesystem lacks is made as the entry is written
  (`mkdir -p`: each missing component, shallowest first, mode `0755` less the umask), once, and reported as
  `{"type":"dir","path"}` like a directory entry. An entry that names a directory that exists is reported as before and
  nothing is made. `--dry-run` reports the same records and makes nothing.
* **`--into` is not made.** The destination root is the caller's choice, so a missing `--into` (or a missing directory above
  it) stays `archive.directory-missing`, and its detail now says that. Everything beneath it is the archive's.
* **A file where a directory is needed** is `conflict.exists`, found in the plan, so nothing is written. Within the archive, a
  name that is a file in one entry and a directory (or the parent of an entry) in another is `archive.duplicate-entry`.
* **Modes.** A file is made with `0755` when its entry's mode has the owner's execute bit and `0644` otherwise, which is what
  `create` writes; the mode is given at creation (`dir_open_new_mode`), so the temporary file never exists with other bits,
  and the umask applies as always. Owner and mtime are still not restored.
* **Special bits are refused, not stripped.** An entry whose mode has a setuid, setgid or sticky bit (`0o7000`) is
  `archive.special-mode`, found in the plan, so nothing is written: dropping the bit silently would extract something other
  than the archive says, and restoring it would be the one thing an unpacker must never do on its own.
* **A race** between the plan and the write (another process made a directory in the meantime) is fine when what exists is a
  directory, as `mkdir -p` treats it, and `conflict.exists` when it is not.
* **Version.** `0.1.0` to `0.2.0`: `archive.directory-missing` no longer fires for what extract can make, and a new rule exists.

## 14. Selecting entries

> **Status: built (0.3.0).** `list` and `extract` take the entries they are asked for after the ARCHIVE:
> `archive --op list|extract ARCHIVE [PATH...]`. (`create` always took PATHs, which name files on disk; here they name entries.)

* **A selector is an entry name** as `list` shows it, normalised as entry names are (`./a//b/` is `a/b`). It selects the entry of that name
  and, when that is a directory, everything beneath it (`src` selects `src`, `src/a.txt` and `src/sub/c.txt`, not `src2`). `.` selects every
  entry. With no selector, every entry is selected, as before. Several selectors select the union.
* **`list`** prints only the selected entries, and `end.entries` counts those. It still reads the whole archive (a gzip cannot be entered in
  the middle), in the same bounded memory as §12.
* **`extract`** writes only the selected entries, each beneath `--into` at its own path (`src/a.txt` becomes `into/src/a.txt`; the directories
  above it are made and reported as §13 says, even though no entry named them). The plan checks only the selected entries: a name clash, a
  link or a setuid file elsewhere in the archive does not stop the extract of a file that is fine.
* **What selecting does not excuse.** An entry whose name is unsafe (absolute, `..`, NUL) is `archive.unsafe-name` whether or not it was
  selected: an archive that holds one is not one to extract from quietly. A selector that is itself unsafe is `archive.unsafe-name`; an
  empty one is `path.empty`.
* **A selector that selects nothing** is `archive.no-match` (exit 8), one error for each, so a typo cannot succeed. An `extract` that has one
  writes nothing.
* **Version.** 0.2.0 to 0.3.0: an additive change to the operands; `archive.no-match` is a new rule.

## 15. Zip

> **Status: built (0.4.0).** `archive` reads and writes zip files, beside tar and gzip. `--op list|extract` recognise a zip by its
> first bytes (`PK\3\4`, or the end record of an empty one); `--op create --as zip` writes one. Everything in §13 and §14 (directories,
> modes, selection) applies to a zip as it does to a tar.

* **What is read.** The central directory at the end of the file, which is what every zip reader goes by (the local headers only
  tell where a member's data starts). Members stored (method 0) or deflated (method 8). The Unix mode in the high half of the
  attributes when the zip was made on Unix (host 3), else 0644 for a file and 0755 for a directory; a directory is a name ending in
  `/` or one marked as one; a symbolic link, device or other type is listed as such and refused on extract (`archive.entry-kind`).
  Names are normalised and flagged as a tar's are (`archive.unsafe-name`), and a backslash is an ordinary byte, not a separator.
* **What is not read, by rule.** Encrypted members, zip64 (a count, size or offset at its limit, or a zip64 locator), a zip on several
  disks, and every method but stored and deflate (bzip2, LZMA, zstd and the rest inside a zip) are `archive.unsupported` (exit 8),
  never guessed at.
* **Nothing is trusted.** Every offset and length is checked against the file. A member is inflated into at most the size the
  directory claims and its length and CRC-32 must be exactly what the directory says (`archive.crc-mismatch`; a stream that runs past the
  claim is the same rule), so a lying directory cannot make an expansion bomb of a small file. An unreadable directory entry is
  `archive.bad-header`, a file that ends where the directory says there is more is `archive.truncated`.
* **Only what is asked for is read.** `extract` inflates the selected members and no others, so one file out of a large zip costs that file;
  a damaged member that was not asked for is not noticed, and `list` reads the directory and no member at all. Memory is the zip file (to `--max-bytes`) plus the selected members' content, not the streaming of
  §12: a zip's directory is at its end, and `list` of a zip reads the file whole.
* **What is written.** `create --as zip` writes the zip of the same files a tar of them would be: names in the walk's sorted order,
  a directory as `name/`, the mode in the Unix attributes (0755 for an executable file and every directory, 0644 otherwise, as in a tar),
  a fixed time (1980-01-01 00:00) so the same files give the same bytes, UTF-8 names, no extra fields and no data descriptors. A member is
  deflated when that is smaller and stored when it is not. The walk, the limits and the receipts are the tar's: the zip is made from the tar
  the walk produced, which costs memory (both are held) and keeps one source of truth. More than 65,534 entries is
  `limit.too-many-entries`, since there is no zip64.
* **Checked against** Python's `zipfile`, Info-ZIP's `zip` and `unzip` in both directions (`test_archive.py`), every cut and every changed
  byte of a small zip (no trap, a tag each), and mutants of the reader and the writer.
* **Version.** 0.3.0 to 0.4.0: `--as zip`, the `zip` format, and a new rule, `archive.unsupported`.

## 16. bzip2

> **Status: built (0.5.0), reading only.** `list` and `extract` read `.tar.bz2` (format `tbz`) and a single `.bz2` file (format `bz2`), recognised by
> `BZh` and a level `1`..`9` at the start. Everything in §13 and §14 applies. `create` does not write bzip2 (§16.3).

### 16.1 What is read

One bzip2 stream: blocks of up to 100,000 times the level bytes, each with its Huffman tables (2 to 6, switched every 50 symbols by a
move-to-front coded selector), the zero-run coding of the move-to-front indexes, the inverse Burrows-Wheeler transform, bzip2's last run-length
step (four equal bytes and a count), and the block's CRC-32; then the end marker and the combined CRC-32. The decompressed content is a tar
(listed and extracted as one) or, when it is not, one file named as the archive is without `.bz2`, as a `.gz` is (§4.1).

### 16.2 What is refused, and bounds

* **Bytes after the stream, and a second stream** are `archive.bad-bzip2`, as they are for gzip (a concatenation is not one archive).
* **A randomised block** (a form no bzip2 since 0.9.5 writes) is `archive.unsupported`.
* **A CRC-32 that is not the data's**, block or combined, is `archive.crc-mismatch`; a stream that stops early is `archive.truncated`; anything
  else that is not a bzip2 stream (a count out of range, a code that is not a prefix code, an index past the block, a position past the
  block) is `archive.bad-bzip2`.
* **The expansion is bounded** by `--max-bytes` as everything is (`limit.archive-too-large`), checked as the bytes are produced, so a 96 MiB
  run of one byte compressed to a few hundred bytes (measured: the test) is refused at the cap with the memory of the tables and one block
  (about 5 MiB for level 9), not of the expansion.
* **Memory** is the compressed file, the expanded content (to `--max-bytes`) and the decoder's block (4 bytes per byte of the block size). `list` of a
  bzip2 reads and expands the whole file, as a zip's reads the whole file (§15): there is no streaming of a bzip2 here (§12 is gzip's).

### 16.3 What is not done

Writing bzip2: the encoder's work is the Burrows-Wheeler sort, a large piece with nothing to check it against but the decoders, and an agent
that wants a `.tar.bz2` has the other formats here. Concatenated streams, and the randomised form, are refused above. Both are listed so that
the next reader knows they are decisions and not omissions.

### 16.4 Measured

32 MiB of text-like data at level 9 expands in 2.5 s here (Python's C `bz2` module: 1.7 s on the same machine), and the output is byte for byte the
original. Checked against Python's `bz2` and the command-line `bzip2` at levels 1 and 9 on text, random bytes, long runs, two symbols, one byte, a
byte repeated 300,000 times, an empty stream and `tar -cjf` trees; every cut and every changed byte of a small stream gives a tag and never a trap
(`test_archive.py`); 15 mutants of the decoder (`scripts/archive_mutants.py`).

## 17. xz

> **Status: built (0.6.0), reading only.** `list` and `extract` read `.tar.xz` (format `txz`) and a single `.xz` file (format `xz`), recognised by the
> six-byte magic `FD 37 7A 58 5A 00`. Everything in §13 and §14 applies. `create` does not write xz (§17.3).

### 17.1 What is read

One xz stream: the 12-byte header (the check type, a CRC-32), blocks (a header with its filters and a CRC-32, LZMA2 data, padding to four bytes, and
the check), the index of the blocks (their sizes, padding, a CRC-32), and the 12-byte footer. **Several blocks** are read (`xz -T` and `--block-size`
write them). The filter is **LZMA2**, which is what `xz` writes without options: every chunk kind (stored with or without a dictionary reset,
and coded with any of the four resets), every `lc`/`lp`/`pb` with `lc + lp <= 4`, any dictionary size. The checks are **none, CRC-32, CRC-64 and
SHA-256**, each verified against the block's content. The decompressed content is a tar or one file named as the archive without `.xz` (§4.1).

### 17.2 What is refused, and bounds

* **A filter in front of LZMA2** (the BCJ filters for x86, ARM and the rest; delta), and **a check type this tool does not know**, are
  `archive.unsupported`: reading the stream without undoing the filter would give wrong bytes with a valid CRC.
* **Bytes after the stream, and a second stream,** are `archive.bad-xz`, as for gzip and bzip2.
* **A CRC-32 or check that is not the data's** (the stream header, a block header, a block's check, the index, the footer) is
  `archive.crc-mismatch`; a stream that stops early is `archive.truncated`; anything else that is not an xz stream (an index that
  does not say what the blocks were, a size in a block header that is not the block's, a match that reaches before the start of the
  dictionary or past its size or past its chunk, a chunk whose range coder does not end exactly where its size says) is `archive.bad-xz`.
* **The expansion is bounded** by `--max-bytes`, checked before each chunk is decoded, so a 96 MiB run of one byte compressed to a few KiB
  (measured: the test) is refused at the cap in the memory of the cap, not of the expansion. The content of a block is its own dictionary and is
  copied once into the output, so peak memory is the output and one block's content.
* **`list` of an xz reads and expands the whole file**, as bzip2's does (§16.2); there is no streaming of xz here.

### 17.3 What is not done

Writing xz: an LZMA2 encoder is a match finder and a range coder, a large piece to be checked against nothing but the decoders; the formats here that
can be written are tar, tgz, gz and zip. The BCJ and delta filters, and several streams, are refused above and listed so the next reader knows they are
decisions and not omissions.

### 17.4 Measured

32 MiB of text-like data at `xz -6` expands in 1.9 s here (Python's C `lzma` module: 0.65 s) and is byte for byte the original. Checked against Python's
`lzma` (all four checks, presets 0, 6, 9) and `xz(1)` (several `lc`/`lp`/`pb`, `-T2` with a block size, a 64 MiB dictionary, SHA-256, empty input) and
`tar -cJf` trees; every cut and every changed byte of a small stream gives a tag and never a trap; 17 mutants of the reader (`scripts/archive_mutants.py`).
**Found by testing:** the range coder owes one normalisation after a chunk's last symbol (a chunk whose final range was small ended one byte "early"); it
showed with `--block-size=100000` and not with the default block size.
