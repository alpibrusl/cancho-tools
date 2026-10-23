"""`archive` (docs/archive.md section 7): the gates that are about tar and gzip.

* G1  round trip against the incumbents: GNU tar and gzip read what `archive` writes, and
      `archive` reads what they write, byte for byte;
* G2  DEFLATE: stored, fixed and dynamic blocks (zlib's output at several levels) inflate;
* G3  hostile archives never trap: every cut and every changed byte is a tag or a success;
* G4  a decompression bomb stops at the cap, with memory bounded by it;
* G5  confinement: `..`, absolute names, links, a link on disk in the way;
* G6  an archive with one bad entry extracts nothing;
* G7  create is a function of the files, not of their times;
* G8  racing extracts: every file whole, once;
* every rule of the tool has a fixture that reaches it, and every output is valid against
  schemas/archive.v1.json.
"""

import base64
import gzip
import hashlib
import io
import json
import os
import pathlib
import random
import shutil
import stat
import struct
import subprocess
import tarfile
import zipfile
import tempfile
import time
import unittest
import zlib

import jsonschema

from harness import ROOT, binary, introspect, peak_rss, schema

TRAPS = {132, 134, 136, 139, -4, -6, -8, -11}
STRICT = jsonschema.Draft202012Validator(schema("archive"))


class Out:
    def __init__(self, p):
        self.status = p.returncode
        self.raw = p.stdout
        self.err = p.stderr
        self.records = [json.loads(l) for l in p.stdout.decode().splitlines() if l.strip()]

    def rules(self):
        return [r["error"]["rule"] for r in self.records if r.get("type") == "error"]

    def end(self):
        return self.records[-1]

    def files(self):
        return [r for r in self.records if r.get("type") == "file"]

    def entries(self):
        return [r for r in self.records if r.get("type") == "entry"]


def check_stream(test, out):
    """The stream is valid against the schema and ends with its `end` record."""
    test.assertNotIn(out.status, TRAPS, "trapped: %d" % out.status)
    test.assertTrue(out.records, "no output at all: " + repr(out.err[:200]))
    for r in out.records:
        problems = [e.message[:160] for e in STRICT.iter_errors(r)]
        test.assertEqual(problems, [], r)
    test.assertEqual(out.end()["type"], "end", out.raw[-300:])
    test.assertEqual(out.end()["ok"], not out.rules())


def tar_bytes(members, fmt=tarfile.GNU_FORMAT, gz=None):
    """A tar of `members`: (name, data|None for a directory) or a TarInfo and optional data."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        for m in members:
            if isinstance(m, tarfile.TarInfo):
                t.addfile(m)
                continue
            name, data = m
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                t.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o644
                t.addfile(info, io.BytesIO(data))
    out = buf.getvalue()
    return gzip.compress(out, 6, mtime=0) if gz else out


def tar_header(name, size, flag=b"0", mode=0o644):
    """One raw ustar header block, with the checksum computed."""
    h = bytearray(512)
    h[0:len(name)] = name
    h[100:107] = b"%07o" % mode
    h[108:115] = b"0000000"
    h[116:123] = b"0000000"
    h[124:135] = b"%011o" % size
    h[136:147] = b"00000000000"
    h[148:156] = b" " * 8
    h[156:157] = flag
    h[257:263] = b"ustar\x00"
    h[263:265] = b"00"
    h[148:156] = b"%06o\x00 " % sum(h)
    return bytes(h)


def padded(data):
    return data + bytes(-len(data) % 512)


def pax_record(key, value):
    body = b" %s=%s\n" % (key, value)
    n = len(body) + 1
    while len(b"%d" % n) + len(body) != n:
        n = len(b"%d" % n) + len(body)
    return b"%d" % n + body


class BitWriter:
    """DEFLATE's bit order: values least significant bit first, Huffman codes most significant bit first."""

    def __init__(self):
        self.acc, self.n, self.out = 0, 0, bytearray()

    def put(self, value, nbits):
        self.acc |= value << self.n
        self.n += nbits
        while self.n >= 8:
            self.out.append(self.acc & 255)
            self.acc >>= 8
            self.n -= 8

    def code(self, value, nbits):
        self.put(int(format(value, "0%db" % nbits)[::-1], 2) if nbits else 0, nbits)

    def finish(self):
        if self.n:
            self.out.append(self.acc & 255)
        return bytes(self.out)


def canonical(lengths):
    """RFC 1951 3.2.2: the code of each symbol with a length, as (value, bits)."""
    counts = [0] * 16
    for n in lengths:
        counts[n] += 1
    counts[0] = 0
    code, first = 0, [0] * 16
    for bits in range(1, 16):
        code = (code + counts[bits - 1]) << 1
        first[bits] = code
    out = {}
    for sym, n in enumerate(lengths):
        if n:
            out[sym] = (first[n], n)
            first[n] += 1
    return out


def dynamic_gzip(lit_lengths, dist_lengths, emit, expect):
    """A gzip whose one block is dynamic Huffman with exactly these code lengths, whether or not they
    make a code a decoder should accept. `emit` is the literal/length symbols to write (256 ends)."""
    w = BitWriter()
    w.put(1, 1)
    w.put(2, 2)
    w.put(len(lit_lengths) - 257, 5)
    w.put(len(dist_lengths) - 1, 5)
    w.put(19 - 4, 4)
    used = sorted(set(lit_lengths + dist_lengths))
    # The code length code: the used values get lengths of one more bit than needed to be a code
    # (a power of two number of leaves, padded with unused values), so it is complete.
    cl = [0] * 19
    width = max(1, (len(used) - 1).bit_length())
    slots = list(used) + [v for v in range(16) if v not in used][:(1 << width) - len(used)]
    for v in slots:
        cl[v] = width
    for sym in [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]:
        w.put(cl[sym], 3)
    cl_codes = canonical(cl)
    for n in lit_lengths + dist_lengths:
        w.code(*cl_codes[n])
    lit_codes = canonical(lit_lengths)
    for sym in emit:
        w.code(*lit_codes[sym])
    body = w.finish()
    return (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + body
            + struct.pack("<II", zlib.crc32(expect) & 0xffffffff, len(expect)))


class Archive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = pathlib.Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=self.base))

    def archive(self, *args, stdin=b"", cwd=None, timeout=120):
        p = subprocess.run([binary("archive"), *map(str, args)], input=stdin, capture_output=True,
                           cwd=cwd or self.dir, timeout=timeout)
        return Out(p)

    def write(self, name, data):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def umask(self, value):
        """The umask of this test, restored after it: the tool inherits it."""
        self.addCleanup(os.umask, os.umask(value))

    def tree(self):
        """The files beneath the work directory as {relative path: bytes}, temporaries included."""
        found = {}
        for p in sorted(self.dir.rglob("*")):
            if p.is_file() and not p.is_symlink():
                found[str(p.relative_to(self.dir))] = p.read_bytes()
        return found

    # ---- G1: the incumbents --------------------------------------------------------------

    def corpus(self, root):
        rnd = random.Random(5)
        (root / "src" / "deep" / "er").mkdir(parents=True)
        (root / "empty").mkdir()
        files = {
            "src/a.txt": b"hello\n",
            "src/empty.txt": b"",
            "src/exact512": bytes(rnd.randrange(256) for _ in range(512)),
            "src/exact1024": bytes(rnd.randrange(256) for _ in range(1024)),
            "src/binary.bin": bytes(rnd.randrange(256) for _ in range(70000)),
            "src/deep/er/zeros": bytes(3 * 1024 * 1024),
            "src/deep/text.txt": b"".join(b"line %d of the corpus\n" % i for i in range(5000)),
            "n99_" + "a" * 95: b"99",
            "n100_" + "b" * 95: b"100",
            "n101_" + "c" * 95: b"101",
            "src/" + "d" * 90 + "/" + "e" * 90 + "/f.txt": b"split into prefix and name",
        }
        for name, data in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (root / "src" / "run.sh").write_bytes(b"#!/bin/sh\n")
        (root / "src" / "run.sh").chmod(0o755)
        return files

    def test_what_archive_writes_gnu_tar_and_gzip_read(self):
        files = self.corpus(self.dir)
        out = self.archive("--op", "create", "mine.tgz", "src", "empty", *[n for n in files if "/" not in n])
        check_stream(self, out)
        self.assertEqual(out.status, 0, out.raw[-400:])
        # A receipt for every entry, in archive order: a file's path, size and SHA-256, a directory's path.
        receipts = {r["path"]: r for r in out.records if r.get("type") in ("file", "dir")}
        for name, data in files.items():
            self.assertEqual((receipts[name]["bytes"], receipts[name]["sha256"]), (len(data), hashlib.sha256(data).hexdigest()), name)
        self.assertEqual(receipts["empty"]["type"], "dir")
        self.assertEqual(out.end()["entries"], len(receipts))
        self.assertEqual(subprocess.run(["gzip", "-t", str(self.dir / "mine.tgz")]).returncode, 0)
        listed = subprocess.run(["tar", "-tzf", str(self.dir / "mine.tgz")], capture_output=True, text=True, check=True).stdout.split("\n")
        self.assertIn("src/a.txt", listed)
        self.assertIn("empty/", listed)
        dest = self.base / ("gnu-" + self.dir.name)
        dest.mkdir()
        subprocess.run(["tar", "-xzf", str(self.dir / "mine.tgz"), "-C", str(dest)], check=True)
        for name, data in files.items():
            self.assertEqual((dest / name).read_bytes(), data, name)
        self.assertTrue(os.access(dest / "src" / "run.sh", os.X_OK))
        self.assertFalse(os.access(dest / "src" / "a.txt", os.X_OK), "a plain file was made executable")
        with tarfile.open(self.dir / "mine.tgz") as t:
            modes = {m.name: m.mode for m in t.getmembers()}
            self.assertEqual(modes["src/run.sh"], 0o755)
            self.assertEqual(modes["src/a.txt"], 0o644)
            self.assertEqual(modes["src/deep"], 0o755)
            self.assertEqual({(m.mtime, m.uid, m.gid, m.uname, m.gname) for m in t.getmembers()}, {(0, 0, 0, "", "")})
        raw = gzip.decompress((self.dir / "mine.tgz").read_bytes())
        self.assertEqual(len(raw) % 512, 0)
        self.assertEqual(raw[-1024:], bytes(1024), "an archive ends with two zero blocks")
        # An uncompressed tar, and one file gzipped.
        out = self.archive("--op", "create", "--as", "tar", "mine.tar", "src")
        self.assertEqual(out.status, 0)
        self.assertEqual(subprocess.run(["tar", "-tf", str(self.dir / "mine.tar")], capture_output=True).returncode, 0)
        out = self.archive("--op", "create", "--as", "gz", "one.gz", "src/deep/text.txt")
        self.assertEqual(out.status, 0)
        self.assertEqual([(f["path"], f["bytes"]) for f in out.files()], [("src/deep/text.txt", len(files["src/deep/text.txt"]))])
        self.assertEqual(gzip.decompress((self.dir / "one.gz").read_bytes()), files["src/deep/text.txt"])

    def test_what_gnu_tar_and_gzip_write_archive_reads(self):
        files = self.corpus(self.dir)
        for flags, name in [("-cf", "g.tar"), ("-czf", "g.tgz")]:
            subprocess.run(["tar", flags, name, "src", "empty"] + [n for n in files if "/" not in n], cwd=self.dir, check=True)
        subprocess.run("gzip -c src/deep/text.txt > text.gz", shell=True, cwd=self.dir, check=True)
        for name in ["g.tar", "g.tgz"]:
            out = self.archive("--op", "list", name)
            check_stream(self, out)
            self.assertEqual(out.status, 0, out.raw[-300:])
            listed = {e["path"]: e for e in out.entries()}
            for path, data in files.items():
                self.assertEqual(listed[path]["size"], len(data), path)
            self.assertTrue(listed["empty"]["kind"] == "directory")
            dest = self.dir / ("x-" + name)
            for d in ["", "src", "src/deep", "src/deep/er", "empty", "src/" + "d" * 90, "src/" + "d" * 90 + "/" + "e" * 90]:
                (dest / d).mkdir(parents=True, exist_ok=True)
            out = self.archive("--op", "extract", "--into", dest.name, name)
            check_stream(self, out)
            self.assertEqual(out.status, 0, out.raw[-400:])
            for path, data in files.items():
                self.assertEqual((dest / path).read_bytes(), data, path)
            for f in out.files():
                self.assertEqual(f["sha256"], hashlib.sha256((dest / f["path"].split("/", 1)[1]).read_bytes()).hexdigest())
        out = self.archive("--op", "list", "text.gz")
        self.assertEqual([e["path"] for e in out.entries()], ["text.txt"], "gzip(1) puts the input's name in the header")
        self.assertEqual(out.end()["format"], "gz")
        out = self.archive("--op", "extract", "text.gz", "--into", "src")
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual((self.dir / "src" / "text.txt").read_bytes(), files["src/deep/text.txt"])
        # A gzip with no name in its header is named for the archive without `.gz`.
        self.write("plain.gz", gzip.compress(b"nameless", 6, mtime=0))
        out = self.archive("--op", "list", "plain.gz")
        self.assertEqual([e["path"] for e in out.entries()], ["plain"])

    def test_gnu_long_names_and_pax_headers(self):
        long = "/".join(["segment%02d" % i * 3 for i in range(12)]) + "/leaf.txt"
        self.assertGreater(len(long), 255)
        for fmt in [tarfile.GNU_FORMAT, tarfile.PAX_FORMAT]:
            data = tar_bytes([(long, b"deep")], fmt)
            self.write("long.tar", data)
            out = self.archive("--op", "list", "long.tar")
            check_stream(self, out)
            self.assertEqual([e["path"] for e in out.entries()], [long], fmt)
        # A pax size that overrides a field too small for it is read as the data's length.
        info = tarfile.TarInfo("big.bin")
        info.size = 3
        info.pax_headers = {"comment": "ignored", "path": "renamed/by/pax.bin"}
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
            t.addfile(info, io.BytesIO(b"abc"))
        self.write("pax.tar", buf.getvalue())
        out = self.archive("--op", "list", "pax.tar")
        self.assertEqual([e["path"] for e in out.entries()], ["renamed/by/pax.bin"])

    # ---- G2: DEFLATE ---------------------------------------------------------------------------

    def gzip_with(self, raw, level, strategy=zlib.Z_DEFAULT_STRATEGY):
        c = zlib.compressobj(level, zlib.DEFLATED, -15, 8, strategy)
        body = c.compress(raw) + c.flush()
        return (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + body
                + struct.pack("<II", zlib.crc32(raw) & 0xffffffff, len(raw) & 0xffffffff))

    def test_stored_fixed_and_dynamic_blocks_all_inflate(self):
        rnd = random.Random(9)
        samples = {
            "tar": tar_bytes([("a.txt", b"hello"), ("d", None), ("d/b.txt", bytes(rnd.randrange(256) for _ in range(5000)))]),
            "text": b"".join(b"%d the quick brown fox\n" % i for i in range(20000)),
            "sevens": b"\x07" * (1 << 20),
            "noise": bytes(rnd.randrange(256) for _ in range(200000)),
            "skewed": bytes(rnd.choice(b"aaaaaaaaaabc") for _ in range(100000)),
            "one": b"x",
            "none": b"",
        }
        for name, raw in samples.items():
            for level, strategy in [(0, 0), (1, 0), (6, 0), (9, 0), (9, zlib.Z_FIXED), (6, zlib.Z_HUFFMAN_ONLY), (6, zlib.Z_RLE)]:
                self.write("z.gz", self.gzip_with(raw, level, strategy))
                out = self.archive("--op", "list", "z.gz", "--max-bytes", "10485760")
                check_stream(self, out)
                self.assertEqual(out.status, 0, (name, level, strategy, out.raw[-300:]))
                if name == "tar":
                    self.assertEqual(out.end()["format"], "tgz")
                    self.assertEqual(out.end()["entries"], 3)
                else:
                    self.assertEqual(out.end()["bytes"], len(raw), (name, level))
                # And extracted: the same bytes.
                if name != "tar":
                    (self.dir / "z").unlink(missing_ok=True)
                    out = self.archive("--op", "extract", "z.gz", "--max-bytes", "10485760")
                    self.assertEqual(out.status, 0, out.raw[-300:])
                    self.assertEqual((self.dir / "z").read_bytes(), raw, (name, level, strategy))

    def test_dynamic_blocks_that_are_not_codes_are_refused_and_ones_that_are_are_read(self):
        # lit/len symbols: 'a' and the end of block, one bit each: a complete code.
        lits = [0] * 258
        lits[97] = 1
        lits[256] = 1
        good = dynamic_gzip(lits, [0], [97, 256], b"a")
        self.assertEqual(zlib.decompress(good[10:-8], -15), b"a", "the builder makes a stream zlib reads")
        self.write("dyn.gz", good)
        out = self.archive("--op", "list", "dyn.gz")
        check_stream(self, out)
        self.assertEqual((out.status, out.end()["bytes"]), (0, 1), out.raw)
        # 288 lit/len symbols is more than the format has (HLIT is at most 29 over 257): zlib refuses it.
        many = lits + [0] * 30
        bad = dynamic_gzip(many, [0], [97, 256], b"a")
        with self.assertRaises(zlib.error):
            zlib.decompress(bad[10:-8], -15)
        # Three symbols of one bit are more than a code holds; one symbol of two bits is less.
        over = list(lits)
        over[98] = 1
        under = [0] * 258
        under[97] = 2
        under[256] = 2
        for name, (lit_lens, emit) in {"over-subscribed": (over, [97, 256]), "incomplete": (under, [97, 256])}.items():
            data = dynamic_gzip(lit_lens, [0], emit, b"a")
            with self.assertRaises(zlib.error, msg=name):
                zlib.decompress(data[10:-8], -15)
        for name, data in [("288", dynamic_gzip(many, [0], [97, 256], b"a")),
                           ("over-subscribed", dynamic_gzip(over, [0], [97, 256], b"a")),
                           ("incomplete", dynamic_gzip(under, [0], [97, 256], b"a"))]:
            self.write("dyn.gz", data)
            out = self.archive("--op", "list", "dyn.gz")
            check_stream(self, out)
            self.assertEqual(out.rules()[:1], ["archive.bad-deflate"], (name, out.raw[-300:]))

    def test_a_pax_size_is_the_size_when_the_field_cannot_hold_it(self):
        body = bytes(range(256)) * 4  # 1024 bytes
        record = pax_record(b"size", b"1024")
        archive = (tar_header(b"PaxHeader", len(record), b"x") + padded(record)
                   + tar_header(b"big.bin", 3) + padded(body) + bytes(1024))
        self.write("pax.tar", archive)
        out = self.archive("--op", "list", "pax.tar")
        check_stream(self, out)
        self.assertEqual((out.status, [(e["path"], e["size"]) for e in out.entries()]), (0, [("big.bin", 1024)]), out.raw)
        # Without the record the same bytes are a header of 3 bytes and then nonsense.
        self.write("nopax.tar", tar_header(b"big.bin", 3) + padded(body) + bytes(1024))
        out = self.archive("--op", "list", "nopax.tar")
        self.assertNotEqual(out.status, 0)

    def test_a_long_name_or_pax_header_that_claims_more_than_the_data_is_truncated_not_a_trap(self):
        for flag in [b"L", b"x", b"g", b"K"]:
            for claimed in [600, 100000, 1 << 33]:
                # A body with no NUL and no non-digit to the end of the data: a reader that trusts the size
                # reads past the end, as a name or a pax record is read to its terminator.
                for body in [b"short body" + bytes(1024), b"A" * 700, b"1" * 700]:
                    self.write("claims.tar", tar_header(b"header", claimed, flag) + body)
                    out = self.archive("--op", "list", "claims.tar")
                    check_stream(self, out)
                    self.assertEqual(out.rules()[:1], ["archive.truncated"], (flag, claimed, body[:12], out.raw[-200:]))

    def test_gzip_damage_is_a_tag(self):
        raw = b"some data " * 1000
        good = gzip.compress(raw, 6, mtime=0)
        cases = {
            "archive.crc-mismatch": good[:-8] + bytes([good[-8] ^ 1]) + good[-7:],
            "archive.crc-mismatch ": good[:-4] + bytes([good[-4] ^ 1]) + good[-3:],  # the length, with the CRC right
            "archive.bad-gzip": good + good,  # two members
            "archive.bad-deflate": good[:10] + bytes([0x07]) + good[11:],  # block type 3
        }
        for rule, data in cases.items():
            self.write("bad.gz", data)
            out = self.archive("--op", "list", "bad.gz")
            check_stream(self, out)
            self.assertEqual(out.rules()[:1], [rule.strip()], (rule, out.raw[-300:]))
        # A reserved header flag is a bad header; a truncated member is not an archive that ends well.
        self.write("bad.gz", good[:3] + bytes([0x20]) + good[4:])
        self.assertEqual(self.archive("--op", "list", "bad.gz").rules()[:1], ["archive.bad-gzip"])
        self.write("bad.gz", good[:30])
        self.assertIn(self.archive("--op", "list", "bad.gz").rules()[0], ["archive.bad-gzip", "archive.bad-deflate"])

    # ---- G3: hostile input -----------------------------------------------------------------------

    def test_every_cut_and_every_changed_byte_is_a_tag_never_a_trap(self):
        data = tar_bytes([("a.txt", b"hello world " * 20), ("d", None), ("d/b.txt", b"bee" * 100)])
        small = gzip.compress(data[:4096], 9, mtime=0)
        for label, blob in [("tar", data), ("tgz", small)]:
            cuts = range(0, len(blob), 1 if len(blob) < 1500 else 7)
            for cut in cuts:
                self.write("cut.bin", blob[:cut])
                out = self.archive("--op", "list", "cut.bin")
                check_stream(self, out)
            rnd = random.Random(3)
            for at in range(0, len(blob), 1 if len(blob) < 1500 else 5):
                mutated = bytearray(blob)
                mutated[at] ^= rnd.choice([1, 0x80, 0xff, 0x55])
                self.write("flip.bin", bytes(mutated))
                for op in ["list", "extract"]:
                    out = self.archive("--op", op, "flip.bin", "--into", ".") if op == "extract" else self.archive("--op", op, "flip.bin")
                    check_stream(self, out)
                    # Extract whatever it wrote, so the next round starts clean.
                    for f in out.files():
                        pathlib.Path(self.dir / f["path"]).unlink(missing_ok=True)

    # ---- zip (docs/archive.md section 15) -------------------------------------------------------------------------------------

    def make_zip(self, name, members, method=zipfile.ZIP_DEFLATED):
        """A zip written by Python's zipfile: (name, data|None for a directory, unix mode)."""
        with zipfile.ZipFile(self.dir / name, "w", method) as z:
            for member, data, mode in members:
                info = zipfile.ZipInfo(member)
                info.create_system = 3
                if data is None:
                    info.external_attr = (0o040000 | mode) << 16 | 0x10
                    z.writestr(info, b"")
                else:
                    info.external_attr = (0o100000 | mode) << 16
                    z.writestr(info, data, compress_type=method)

    ZIPPED = [("tree/", None, 0o755), ("tree/a.txt", b"alpha " * 400, 0o644), ("tree/run.sh", b"#!/bin/sh\necho hi\n", 0o755), ("tree/sub/", None, 0o755), ("tree/sub/b.bin", bytes(range(256)) * 8, 0o600)]

    def test_what_archive_writes_unzip_and_python_read(self):
        files = self.corpus(self.dir)
        out = self.archive("--op", "create", "--as", "zip", "mine.zip", "src", "empty", *[n for n in files if "/" not in n])
        check_stream(self, out)
        self.assertEqual(out.status, 0, out.raw[-400:])
        self.assertEqual(out.end()["format"], "zip")
        self.assertEqual(subprocess.run(["unzip", "-tq", str(self.dir / "mine.zip")], capture_output=True).returncode, 0)
        with zipfile.ZipFile(self.dir / "mine.zip") as z:
            self.assertIsNone(z.testzip())
            names = z.namelist()
            self.assertIn("src/", names)
            self.assertIn("empty/", names)
            for name, data in files.items():
                self.assertEqual(z.read(name), data, name)
            by = {i.filename: i for i in z.infolist()}
            self.assertEqual(by["src/run.sh"].external_attr >> 16 & 0o777, 0o755)
            self.assertEqual(by["src/a.txt"].external_attr >> 16 & 0o777, 0o644)
            self.assertEqual(by["src/"].external_attr >> 16 & 0o170000, 0o040000)
            self.assertEqual({i.date_time for i in z.infolist()}, {(1980, 1, 1, 0, 0, 0)})
            self.assertTrue(any(i.compress_type == zipfile.ZIP_DEFLATED for i in z.infolist()), "nothing was deflated")
            self.assertTrue(any(i.compress_type == zipfile.ZIP_STORED and i.file_size > 0 for i in z.infolist()), "an incompressible file was not stored")
        # The same files give the same bytes, and a zip made here is read here.
        out2 = self.archive("--op", "create", "--as", "zip", "again.zip", "src", "empty", *[n for n in files if "/" not in n])
        self.assertEqual((self.dir / "mine.zip").read_bytes(), (self.dir / "again.zip").read_bytes())
        self.assertEqual(out.end()["archive_sha256"], hashlib.sha256((self.dir / "mine.zip").read_bytes()).hexdigest())
        dest = self.dir / "back"
        dest.mkdir()
        out = self.archive("--op", "extract", "--into", "back", "mine.zip")
        self.assertEqual(out.status, 0, out.raw[-300:])
        for name, data in files.items():
            self.assertEqual((dest / name).read_bytes(), data, name)
        self.assertTrue(os.access(dest / "src" / "run.sh", os.X_OK))

    def test_what_others_write_zip_archive_reads(self):
        self.umask(0o022)
        sources = {}
        self.make_zip("py.zip", self.ZIPPED)
        sources["python deflate"] = "py.zip"
        self.make_zip("stored.zip", self.ZIPPED, zipfile.ZIP_STORED)
        sources["python stored"] = "stored.zip"
        tree = self.dir / "ziptree"
        (tree / "tree" / "sub").mkdir(parents=True)
        for name, data, mode in self.ZIPPED:
            if data is not None:
                (tree / name).write_bytes(data)
                (tree / name).chmod(mode)
        subprocess.run(["zip", "-qrX", str(self.dir / "cli.zip"), "tree"], cwd=tree, check=True)
        sources["zip(1)"] = "cli.zip"
        for label, name in sources.items():
            out = self.archive("--op", "list", name)
            check_stream(self, out)
            listed = {r["path"]: r for r in out.records if r.get("type") == "entry"}
            self.assertEqual(sorted(listed), ["tree", "tree/a.txt", "tree/run.sh", "tree/sub", "tree/sub/b.bin"], label)
            self.assertEqual(listed["tree/a.txt"]["size"], 2400, label)
            self.assertEqual(listed["tree/sub"]["kind"], "directory", label)
            self.assertEqual(listed["tree/run.sh"]["mode"] & 0o777, 0o755, label)
            into = "out-" + name
            (self.dir / into).mkdir()
            out = self.archive("--op", "extract", "--into", into, name)
            check_stream(self, out)
            self.assertEqual(out.status, 0, (label, out.raw[-300:]))
            for member, data, mode in self.ZIPPED:
                if data is not None:
                    self.assertEqual((self.dir / into / member).read_bytes(), data, (label, member))
            self.assertEqual(stat.S_IMODE((self.dir / into / "tree/run.sh").stat().st_mode), 0o755, label)
            self.assertEqual(stat.S_IMODE((self.dir / into / "tree/a.txt").stat().st_mode), 0o644, label)
        # A selection of a zip lists and extracts only that.
        out = self.archive("--op", "list", "py.zip", "tree/sub")
        self.assertEqual([r["path"] for r in out.records if r.get("type") == "entry"], ["tree/sub", "tree/sub/b.bin"])
        (self.dir / "one").mkdir()
        out = self.archive("--op", "extract", "--into", "one", "py.zip", "tree/run.sh")
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual(sorted(str(p.relative_to(self.dir / "one")) for p in (self.dir / "one").rglob("*")), ["tree", "tree/run.sh"])

    def test_zip_odds_and_ends_other_writers_leave(self):
        # A comment after the directory, an extra field in the local header, a directory known only by its trailing slash.
        with zipfile.ZipFile(self.dir / "odd.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.comment = b"c" * 200
            info = zipfile.ZipInfo("extra.txt")
            info.extra = b"\x99\x99\x08\x00" + b"x" * 8
            z.writestr(info, b"with an extra field " * 10)
            bare = zipfile.ZipInfo("bare/")
            bare.create_system = 0
            bare.external_attr = 0
            z.writestr(bare, b"")
            z.writestr("tail.txt", b"the last member")
        out = self.archive("--op", "list", "odd.zip")
        check_stream(self, out)
        self.assertEqual({r["path"]: r["kind"] for r in out.records if r.get("type") == "entry"}, {"extra.txt": "file", "bare": "directory", "tail.txt": "file"})
        (self.dir / "into").mkdir()
        out = self.archive("--op", "extract", "--into", "into", "odd.zip")
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual((self.dir / "into/extra.txt").read_bytes(), b"with an extra field " * 10)
        self.assertTrue((self.dir / "into/bare").is_dir())
        # A local header that is not one is not found, not guessed.
        raw = bytearray((self.dir / "odd.zip").read_bytes())
        last = raw.rfind(b"PK\x03\x04")
        raw[last + 3] = 5
        self.write("nolocal.zip", bytes(raw))
        before = self.tree()
        out = self.archive("--op", "extract", "--into", ".", "nolocal.zip")
        self.assertEqual(out.rules(), ["archive.truncated"])
        self.assertEqual(self.tree(), before)

    def test_a_zip_end_record_with_its_counts_at_the_limit_is_zip64_and_unsupported(self):
        self.make_zip("good.zip", self.ZIPPED)
        raw = bytearray((self.dir / "good.zip").read_bytes())
        eocd = raw.rfind(b"PK\x05\x06")
        for field, width in [(8, 2), (10, 2), (12, 4), (16, 4)]:
            mutated = bytearray(raw)
            mutated[eocd + field:eocd + field + width] = b"\xff" * width
            self.write("z64.zip", bytes(mutated))
            out = self.archive("--op", "list", "z64.zip")
            self.assertEqual(out.rules(), ["archive.unsupported"], field)

    def test_a_zip_holds_at_most_65534_entries(self):
        many = self.dir / "many"
        many.mkdir()
        for i in range(65535):
            (many / ("f%d" % i)).write_bytes(b"")
        out = self.archive("--op", "create", "--as", "zip", "--max-entries", "1000000", "big.zip", "many", timeout=600)
        self.assertEqual(out.rules(), ["limit.too-many-entries"])
        self.assertFalse((self.dir / "big.zip").exists())

    def test_only_the_members_asked_for_are_read(self):
        self.make_zip("py.zip", self.ZIPPED, zipfile.ZIP_STORED)
        raw = bytearray((self.dir / "py.zip").read_bytes())
        at = raw.find(b"alpha alpha")
        raw[at + 5] ^= 0xff
        self.write("damaged.zip", bytes(raw))
        (self.dir / "one").mkdir()
        # The damaged member is not asked for, so it is not read; asked for, it is a CRC mismatch and nothing is written.
        out = self.archive("--op", "extract", "--into", "one", "damaged.zip", "tree/run.sh")
        self.assertEqual(out.status, 0, out.raw)
        before = self.tree()
        out = self.archive("--op", "extract", "--into", "one", "damaged.zip", "tree/a.txt")
        self.assertEqual(out.rules(), ["archive.crc-mismatch"])
        out = self.archive("--op", "extract", "--into", "one", "damaged.zip")
        self.assertEqual(out.rules(), ["archive.crc-mismatch"])
        self.assertEqual(self.tree(), before)

    def test_zip_features_this_tool_does_not_read_are_unsupported_and_damage_is_a_tag(self):
        self.make_zip("good.zip", self.ZIPPED)
        good = (self.dir / "good.zip").read_bytes()
        eocd = good.rfind(b"PK\x05\x06")
        cd = struct.unpack("<I", good[eocd + 16:eocd + 20])[0]

        def changed(name, at, value):
            raw = bytearray(good)
            raw[at:at + len(value)] = value
            self.write(name, bytes(raw))
            return self.archive("--op", "list", name)

        out = changed("enc.zip", cd + 8, struct.pack("<H", 1))
        self.assertEqual(out.rules(), ["archive.unsupported"])
        out = changed("method.zip", cd + 10, struct.pack("<H", 12))
        self.assertEqual(out.rules(), ["archive.unsupported"])
        out = changed("zip64.zip", cd + 20, struct.pack("<I", 0xffffffff))
        self.assertEqual(out.rules(), ["archive.unsupported"])
        out = changed("disk.zip", eocd + 4, struct.pack("<H", 1))
        self.assertEqual(out.rules(), ["archive.unsupported"])
        out = changed("sig.zip", cd, b"XXXX")
        self.assertEqual(out.rules(), ["archive.bad-header"])
        out = changed("count.zip", eocd + 10, struct.pack("<H", 99))
        self.assertIn(out.rules()[0], ["archive.unsupported", "archive.truncated", "archive.bad-header"])
        self.write("noend.zip", good[:eocd - 3])
        out = self.archive("--op", "list", "noend.zip")
        self.assertEqual(out.rules(), ["archive.truncated"])
        self.write("short.zip", b"PK\x03\x04" + bytes(10))
        out = self.archive("--op", "list", "short.zip")
        self.assertEqual(out.rules(), ["archive.truncated"])
        for rule_out in [self.archive("--op", "extract", "enc.zip"), self.archive("--op", "extract", "sig.zip")]:
            check_stream(self, rule_out)
        # A member whose directory sizes are wrong: the length is checked, never trusted.
        out = changed("size.zip", cd + 46 + len("tree/") + 24, struct.pack("<I", 7))
        self.assertEqual(out.status, 0, out.raw)
        out = self.archive("--op", "extract", "size.zip")
        self.assertIn(out.rules()[0], ["archive.crc-mismatch", "archive.truncated", "archive.bad-deflate"])

    def test_zip_names_and_kinds_are_checked_as_a_tars_are(self):
        self.make_zip("evil.zip", [("fine.txt", b"ok", 0o644), ("../escape.txt", b"x", 0o644)])
        out = self.archive("--op", "extract", "evil.zip")
        self.assertEqual(out.rules(), ["archive.unsafe-name"])
        self.assertFalse((self.dir / "fine.txt").exists())
        with zipfile.ZipFile(self.dir / "link.zip", "w") as z:
            info = zipfile.ZipInfo("l")
            info.create_system = 3
            info.external_attr = (0o120777) << 16
            z.writestr(info, "target")
            z.writestr("kept.txt", "k")
        out = self.archive("--op", "list", "link.zip")
        self.assertEqual({r["path"]: r["kind"] for r in out.records if r.get("type") == "entry"}, {"l": "symlink", "kept.txt": "file"})
        out = self.archive("--op", "extract", "link.zip")
        self.assertEqual(out.rules(), ["archive.entry-kind"])
        out = self.archive("--op", "extract", "link.zip", "kept.txt")
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual((self.dir / "kept.txt").read_bytes(), b"k")
        with zipfile.ZipFile(self.dir / "suid.zip", "w") as z:
            info = zipfile.ZipInfo("s")
            info.create_system = 3
            info.external_attr = (0o104755) << 16
            z.writestr(info, "x")
        out = self.archive("--op", "extract", "suid.zip")
        self.assertEqual(out.rules(), ["archive.special-mode"])

    def test_a_zip_bomb_is_bounded_by_what_its_directory_claims(self):
        self.make_zip("bomb.zip", [("zeros", bytes(8 * 1024 * 1024), 0o644)])
        raw = bytearray((self.dir / "bomb.zip").read_bytes())
        out = self.archive("--op", "extract", "--max-bytes", "1048576", "bomb.zip")
        self.assertEqual(out.rules(), ["limit.archive-too-large"])
        eocd = raw.rfind(b"PK\x05\x06")
        cd = struct.unpack("<I", raw[eocd + 16:eocd + 20])[0]
        raw[cd + 24:cd + 28] = struct.pack("<I", 1000)
        self.write("lie.zip", bytes(raw))
        before = self.tree()
        out = self.archive("--op", "extract", "lie.zip")
        self.assertEqual(out.rules(), ["archive.crc-mismatch"])
        self.assertEqual(self.tree(), before)

    def test_every_cut_and_changed_byte_of_a_zip_is_a_tag_never_a_trap(self):
        self.make_zip("z.zip", [("a.txt", b"hello world " * 20, 0o644), ("d/", None, 0o755), ("d/b.txt", b"bee" * 100, 0o644)])
        blob = (self.dir / "z.zip").read_bytes()
        for cut in range(0, len(blob), 1):
            self.write("cut.zip", blob[:cut])
            check_stream(self, self.archive("--op", "list", "cut.zip"))
        rnd = random.Random(7)
        for at in range(0, len(blob)):
            mutated = bytearray(blob)
            mutated[at] ^= rnd.choice([1, 0x80, 0xff, 0x55])
            self.write("flip.zip", bytes(mutated))
            for op in ["list", "extract"]:
                out = self.archive("--op", op, "flip.zip", "--into", ".") if op == "extract" else self.archive("--op", op, "flip.zip")
                check_stream(self, out)
                for f in out.files():
                    # A name that is not UTF-8 is `{"b64": ...}` in the stream.
                    name = f["path"] if isinstance(f["path"], str) else os.fsdecode(base64.b64decode(f["path"]["b64"]))
                    pathlib.Path(self.dir / name).unlink(missing_ok=True)

    def test_a_size_past_the_data_is_truncated(self):
        raw = bytearray(tar_bytes([("a.txt", b"hello")]))
        raw[124:135] = b"77777777777"
        s = sum(raw[:148]) + 32 * 8 + sum(raw[156:512])
        raw[148:156] = b"%06o\x00 " % s
        self.write("big.tar", bytes(raw))
        out = self.archive("--op", "list", "big.tar")
        self.assertEqual(out.rules(), ["archive.truncated"])
        self.assertEqual(out.status, 8)

    # ---- G4: bombs -------------------------------------------------------------------------------------

    def test_a_bomb_stops_at_the_cap_and_memory_is_bounded_by_it(self):
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        crc, total = 0, 0
        body = b""
        zeros = bytes(1 << 20)
        for _ in range(512):
            body += c.compress(zeros)
            crc = zlib.crc32(zeros, crc)
            total += len(zeros)
        body += c.flush()
        bomb = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + body + struct.pack("<II", crc & 0xffffffff, total & 0xffffffff)
        self.assertLess(len(bomb), 1 << 20)
        self.write("bomb.gz", bomb)
        cap = 16 * 1024 * 1024
        started = time.monotonic()
        status, peak_kib = peak_rss([binary("archive"), "--op", "list", "bomb.gz", "--max-bytes", cap], cwd=self.dir)
        took = time.monotonic() - started
        out = self.archive("--op", "list", "bomb.gz", "--max-bytes", cap)
        self.assertEqual(out.rules()[:1], ["limit.archive-too-large"])
        self.assertEqual(status, 8)
        print("archive: bomb of %d bytes -> %d MiB expanded, peak RSS %d KiB for a %d KiB cap, %.2fs"
              % (len(bomb), total >> 20, peak_kib, cap >> 10, took), flush=True)
        # The output buffer grows by doubling, so the cap is at most doubled, plus the input.
        self.assertLess(peak_kib * 1024, 2 * cap + (8 << 20))
        self.assertLess(took, 20)

    def test_list_memory_does_not_grow_with_the_archive(self):
        """`list` reads a piece at a time (docs/archive.md section 12): the peak is the same for a small archive and a large one."""
        rnd = random.Random(4)
        words = [bytes(rnd.choice(b"abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(3, 9))) for _ in range(5000)]

        def text(n):
            out, size = [], 0
            while size < n:
                line = b" ".join(rnd.choice(words) for _ in range(10)) + b"\n"
                out.append(line)
                size += len(line)
            return b"".join(out)[:n]

        peaks = {}
        for label, mib in (("small", 2), ("large", 48)):
            self.write("%s.tgz" % label, tar_bytes([("a.txt", text(mib << 20)), ("d", None), ("b.txt", b"tail")], gz=True))
            status, peak = peak_rss([binary("archive"), "--op", "list", "%s.tgz" % label, "--max-bytes", 1 << 30], cwd=self.dir)
            out = self.archive("--op", "list", "%s.tgz" % label, "--max-bytes", str(1 << 30))
            check_stream(self, out)
            self.assertEqual(out.status, 0, out.raw[-300:])
            self.assertEqual([e["path"] for e in out.entries()], ["a.txt", "d", "b.txt"])
            self.assertEqual(out.entries()[0]["size"], mib << 20)
            peaks[label] = peak
        print("archive: list peak RSS %s KiB for a 2 MiB and a 48 MiB tgz" % peaks, flush=True)
        self.assertLess(peaks["large"], 8 * 1024)
        self.assertLess(peaks["large"], peaks["small"] + 2 * 1024)

    def test_a_late_gzip_error_follows_the_entries_already_listed(self):
        """The error is found at the end of the stream, after the entries before it were printed; the end record says so."""
        raw = tar_bytes([("a.txt", b"hello"), ("b.txt", b"world" * 1000)])
        member = bytearray(gzip.compress(raw, 6, mtime=0))
        member[-8] ^= 0xff
        self.write("late.tgz", bytes(member))
        out = self.archive("--op", "list", "late.tgz")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["archive.crc-mismatch"])
        self.assertEqual([e["path"] for e in out.entries()], ["a.txt", "b.txt"])
        self.assertEqual(out.end()["ok"], False)
        self.assertEqual(out.status, 8)
        # Cut inside the stream: the same, with the DEFLATE rule.
        whole = gzip.compress(raw, 6, mtime=0)
        self.write("cut.tgz", whole[: len(whole) // 2])
        out = self.archive("--op", "list", "cut.tgz")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["archive.bad-deflate"])

    def test_a_pax_header_over_64_kib_is_refused_and_a_cut_one_is_truncated(self):
        info = tarfile.TarInfo("f.bin")
        info.size = 3
        info.pax_headers = {"comment": "x" * 70000}
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
            t.addfile(info, io.BytesIO(b"abc"))
        self.write("fat.tar", buf.getvalue())
        out = self.archive("--op", "list", "fat.tar")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["archive.bad-header"])
        self.write("fat-cut.tar", buf.getvalue()[:30000])
        out = self.archive("--op", "list", "fat-cut.tar")
        self.assertEqual(out.rules(), ["archive.truncated"])

    def test_the_entry_cap_and_the_file_cap(self):
        self.write("many.tar", tar_bytes([("f%d" % i, b"x") for i in range(20)]))
        out = self.archive("--op", "list", "many.tar", "--max-entries", "5")
        self.assertEqual(out.rules(), ["limit.too-many-entries"])
        self.assertEqual(len(out.entries()), 5)
        out = self.archive("--op", "list", "many.tar", "--max-bytes", "1000")
        self.assertEqual(out.rules(), ["limit.archive-too-large"])

    # ---- G5: confinement -------------------------------------------------------------------------------------------

    def test_names_that_climb_or_start_at_the_root_extract_nothing(self):
        for name in ["../escape.txt", "/tmp/absolute.txt", "a/../../escape.txt", "ok/../../x"]:
            self.write("evil.tar", tar_bytes([("fine.txt", b"fine"), (name, b"evil")]))
            before = self.tree()
            out = self.archive("--op", "extract", "evil.tar")
            check_stream(self, out)
            self.assertEqual(out.rules()[:1], ["archive.unsafe-name"], name)
            self.assertEqual(out.status, 4)
            self.assertEqual(self.tree(), before, "something was written for " + name)
            listed = self.archive("--op", "list", "evil.tar")
            self.assertTrue(any(e["unsafe"] for e in listed.entries()))
        self.assertFalse((self.dir.parent / "escape.txt").exists())

    def test_links_devices_and_fifos_are_refused(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE]:
            info = tarfile.TarInfo("thing")
            info.type = kind
            info.linkname = "/etc/passwd" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            self.write("kinds.tar", tar_bytes([("first.txt", b"x"), info]))
            out = self.archive("--op", "extract", "kinds.tar")
            self.assertEqual(out.rules()[:1], ["archive.entry-kind"], kind)
            self.assertEqual(self.tree().keys() - {"kinds.tar"}, set(), "wrote before refusing")
        names = {e["path"]: e["kind"] for e in self.archive("--op", "list", "kinds.tar").entries()}
        self.assertEqual(names["thing"], "fifo")

    def test_a_link_on_disk_is_not_followed(self):
        outside = self.base / ("outside-" + self.dir.name)
        outside.mkdir()
        os.symlink(outside, self.dir / "linked")
        self.write("one.tar", tar_bytes([("f.txt", b"data")]))
        out = self.archive("--op", "extract", "--into", "linked", "one.tar")
        check_stream(self, out)
        self.assertEqual(out.rules()[0], "path.symlink")
        self.assertEqual(list(outside.iterdir()), [])
        # Created beneath a real directory whose name is a link inside the archive's own tree.
        (self.dir / "real").mkdir()
        os.symlink(outside, self.dir / "real" / "inner")
        self.write("nested.tar", tar_bytes([("inner/f.txt", b"data")]))
        out = self.archive("--op", "extract", "--into", "real", "nested.tar")
        self.assertIn(out.rules()[0], ["path.symlink", "archive.directory-missing"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_paths_outside_the_root_are_refused(self):
        self.write("a.tar", tar_bytes([("f.txt", b"x")]))
        for bad in ["../a.tar", "/etc/passwd"]:
            out = self.archive("--op", "list", bad)
            self.assertIn(out.rules()[0], ["path.dotdot", "path.absolute", "path.outside-root"], bad)
        out = self.archive("--op", "extract", "--into", "..", "a.tar")
        self.assertEqual(out.rules()[0], "path.dotdot")

    # ---- G6: plan before write ------------------------------------------------------------------------------------------

    def test_one_bad_entry_means_nothing_is_written(self):
        (self.dir / "into").mkdir()
        self.write("into/taken.txt", b"mine")
        self.write("plan.tar", tar_bytes([("a.txt", b"a"), ("b.txt", b"b"), ("taken.txt", b"theirs")]))
        before = self.tree()
        out = self.archive("--op", "extract", "--into", "into", "plan.tar")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["conflict.exists"])
        self.assertEqual(out.status, 5)
        self.assertEqual(out.end()["entries"], 0)
        self.assertEqual(self.tree(), before)
        # Every problem is reported, not the first.
        self.write("plan2.tar", tar_bytes([("taken.txt", b"x"), ("../x", b"y"), ("missing/dir/f", b"z")]))
        out = self.archive("--op", "extract", "--into", "into", "plan2.tar")
        self.assertEqual(sorted(out.rules()), ["archive.unsafe-name", "conflict.exists"])
        self.assertFalse((self.dir / "into" / "missing").exists(), "a directory was made by a refused extract")

    def test_a_missing_into_is_named_by_its_shallowest_missing_part_and_is_not_made(self):
        self.write("deep.tar", tar_bytes([("a/b/c/f.txt", b"x"), ("a/b/g.txt", b"y")]))
        out = self.archive("--op", "extract", "--into", "x/y/z", "deep.tar")
        self.assertEqual(out.rules(), ["archive.directory-missing"])
        self.assertEqual(out.records[0]["error"]["detail"]["path"], "x")
        self.assertFalse((self.dir / "x").exists())
        (self.dir / "x").mkdir()
        out = self.archive("--op", "extract", "--into", "x/y/z", "deep.tar")
        self.assertEqual(out.records[0]["error"]["detail"]["path"], "x/y")
        (self.dir / "x" / "y" / "z").mkdir(parents=True)
        out = self.archive("--op", "extract", "--into", "x/y/z", "deep.tar")
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual((self.dir / "x/y/z/a/b/c/f.txt").read_bytes(), b"x")

    def test_extract_makes_the_directories_an_entry_needs_and_reports_each_once(self):
        self.umask(0o022)
        # No directory entries at all: the parents are implied. And a directory entry that is already there, and one that is not.
        (self.dir / "into" / "have").mkdir(parents=True)
        self.write("deep.tar", tar_bytes([("a/b/c/f.txt", b"x"), ("a/b/g.txt", b"y"), ("have", None), ("new", None), ("new/sub", None)]))
        out = self.archive("--op", "extract", "--dry-run", "--into", "into", "deep.tar")
        check_stream(self, out)
        self.assertEqual(out.status, 9, out.raw)
        dry = [(r["type"], r["path"]) for r in out.records if r.get("type") in ("dir", "file")]
        self.assertFalse((self.dir / "into" / "a").exists(), "a dry run made a directory")
        out = self.archive("--op", "extract", "--into", "into", "deep.tar")
        check_stream(self, out)
        self.assertEqual(out.status, 0, out.raw)
        wrote = [(r["type"], r["path"]) for r in out.records if r.get("type") in ("dir", "file")]
        self.assertEqual(wrote, dry)
        self.assertEqual(wrote.count(("dir", "into/a")), 1)
        self.assertEqual(
            [p for t, p in wrote if t == "dir"],
            ["into/a", "into/a/b", "into/a/b/c", "into/have", "into/new", "into/new/sub"],
        )
        self.assertEqual((self.dir / "into/a/b/c/f.txt").read_bytes(), b"x")
        for d in ["a", "a/b", "a/b/c", "new", "new/sub"]:
            self.assertEqual(stat.S_IMODE((self.dir / "into" / d).stat().st_mode), 0o755, d)

    def test_extract_restores_the_executable_bit_and_no_other_mode(self):
        self.umask(0o022)
        members = []
        for name, mode in [("plain.txt", 0o644), ("script.sh", 0o755), ("owner-only-exec.sh", 0o700), ("readonly.txt", 0o444), ("group-exec", 0o654)]:
            info = tarfile.TarInfo(name)
            info.size = 1
            info.mode = mode
            members.append((info, b"x"))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            for info, data in members:
                t.addfile(info, io.BytesIO(data))
        self.write("modes.tar", buf.getvalue())
        out = self.archive("--op", "extract", "modes.tar")
        self.assertEqual(out.status, 0, out.raw)
        got = {n: stat.S_IMODE((self.dir / n).stat().st_mode) for n in ["plain.txt", "script.sh", "owner-only-exec.sh", "readonly.txt", "group-exec"]}
        # The owner's execute bit decides, as `create` decided it; the rest is 0644 less the umask, never the archive's own bits.
        self.assertEqual(got, {"plain.txt": 0o644, "script.sh": 0o755, "owner-only-exec.sh": 0o755, "readonly.txt": 0o644, "group-exec": 0o644})
        self.umask(0o077)
        info = tarfile.TarInfo("umask/x.sh")
        info.size = 1
        info.mode = 0o755
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            t.addfile(info, io.BytesIO(b"x"))
        self.write("umask.tar", buf.getvalue())
        out = self.archive("--op", "extract", "umask.tar")
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual(stat.S_IMODE((self.dir / "umask/x.sh").stat().st_mode), 0o700, "the umask no longer applies")
        self.assertEqual(stat.S_IMODE((self.dir / "umask").stat().st_mode), 0o700)

    def test_setuid_setgid_and_sticky_entries_are_refused_and_nothing_is_written(self):
        for mode, label in [(0o4755, "setuid"), (0o2755, "setgid"), (0o1777, "sticky")]:
            info = tarfile.TarInfo("special")
            info.size = 1
            info.mode = mode
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as t:
                ok = tarfile.TarInfo("fine.txt")
                ok.size = 1
                t.addfile(ok, io.BytesIO(b"f"))
                t.addfile(info, io.BytesIO(b"x"))
            self.write(label + ".tar", buf.getvalue())
            before = self.tree()
            out = self.archive("--op", "extract", label + ".tar")
            check_stream(self, out)
            self.assertEqual(out.rules(), ["archive.special-mode"], label)
            self.assertEqual(out.status, 8, label)
            self.assertEqual(out.records[0]["error"]["detail"]["path"], "special", label)
            self.assertEqual(out.records[0]["error"]["detail"]["mode"], mode, label)
            self.assertEqual(self.tree(), before, label)
        # A directory entry with a sticky bit is refused the same way.
        info = tarfile.TarInfo("tmpdir")
        info.type = tarfile.DIRTYPE
        info.mode = 0o1777
        self.write("dir.tar", tar_bytes([info]))
        out = self.archive("--op", "extract", "dir.tar")
        self.assertEqual(out.rules(), ["archive.special-mode"])
        self.assertFalse((self.dir / "tmpdir").exists())

    def test_a_file_where_a_directory_is_needed_is_a_conflict_and_nothing_is_made(self):
        (self.dir / "into").mkdir()
        self.write("into/a", b"a file")
        os.symlink(self.dir / "into", self.dir / "link")
        self.write("clash.tar", tar_bytes([("fresh/f.txt", b"x"), ("a/b/f.txt", b"y")]))
        self.write("dirfile.tar", tar_bytes([("a", None)]))
        before = self.tree()
        out = self.archive("--op", "extract", "--into", "into", "clash.tar")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["conflict.exists"])
        self.assertEqual(out.records[0]["error"]["detail"]["path"], "into/a")
        self.assertEqual(self.tree(), before)
        # A directory entry whose name is held by a file.
        out = self.archive("--op", "extract", "--into", "into", "dirfile.tar")
        self.assertEqual(out.rules(), ["conflict.exists"])
        self.assertEqual(self.tree(), before)

    SELECTED = [("src", None), ("src/a.txt", b"a"), ("src/sub", None), ("src/sub/c.txt", b"c"), ("src2", None), ("src2/d.txt", b"d"), ("top.txt", b"t")]

    def selection_archive(self, gz=None):
        self.write("sel.tgz" if gz else "sel.tar", tar_bytes(self.SELECTED, gz=gz))
        return "sel.tgz" if gz else "sel.tar"

    def test_list_takes_the_entries_it_is_asked_for(self):
        for gz in (None, True):
            name = self.selection_archive(gz)
            everything = self.archive("--op", "list", name)
            self.assertEqual([r["path"] for r in everything.records if r.get("type") == "entry"], [n for n, _ in self.SELECTED])
            for selectors, expected in [
                (["src"], ["src", "src/a.txt", "src/sub", "src/sub/c.txt"]),
                (["src/"], ["src", "src/a.txt", "src/sub", "src/sub/c.txt"]),
                (["./src//sub/"], ["src/sub", "src/sub/c.txt"]),
                (["src/a.txt", "top.txt"], ["src/a.txt", "top.txt"]),
                (["src/a.txt", "src"], ["src", "src/a.txt", "src/sub", "src/sub/c.txt"]),
                (["."], [n for n, _ in self.SELECTED]),
                (["src2"], ["src2", "src2/d.txt"]),
            ]:
                out = self.archive("--op", "list", name, *selectors)
                check_stream(self, out)
                self.assertEqual(out.status, 0, (selectors, out.raw))
                self.assertEqual([r["path"] for r in out.records if r.get("type") == "entry"], expected, selectors)
                self.assertEqual(out.end()["entries"], len(expected), selectors)

    def test_a_selector_is_a_whole_name_not_a_prefix_of_one(self):
        name = self.selection_archive()
        out = self.archive("--op", "list", name, "sr")
        self.assertEqual(out.rules(), ["archive.no-match"])
        out = self.archive("--op", "list", name, "src/a")
        self.assertEqual(out.rules(), ["archive.no-match"])

    def test_extract_writes_only_the_selected_entries_at_their_own_paths(self):
        for gz in (None, True):
            name = self.selection_archive(gz)
            into = "into-gz" if gz else "into"
            (self.dir / into).mkdir()
            out = self.archive("--op", "extract", "--into", into, name, "src/sub/c.txt", "top.txt")
            check_stream(self, out)
            self.assertEqual(out.status, 0, out.raw)
            files = sorted(str(p.relative_to(self.dir / into)) for p in (self.dir / into).rglob("*"))
            self.assertEqual(files, ["src", "src/sub", "src/sub/c.txt", "top.txt"])
            self.assertEqual((self.dir / into / "src/sub/c.txt").read_bytes(), b"c")
            dirs = [r["path"] for r in out.records if r.get("type") == "dir"]
            self.assertEqual(dirs, [into + "/src", into + "/src/sub"])
            # A directory selects what is beneath it, and a second extract of the same names is a conflict, not a partial write.
            out = self.archive("--op", "extract", "--into", into, name, "src2")
            self.assertEqual(out.status, 0, out.raw)
            self.assertEqual((self.dir / into / "src2/d.txt").read_bytes(), b"d")
            before = self.tree()
            out = self.archive("--op", "extract", "--into", into, name, "top.txt", "src")
            self.assertEqual(out.rules(), ["conflict.exists"] * 2)
            self.assertEqual(self.tree(), before)

    def test_what_is_not_selected_is_not_checked_but_an_unsafe_name_always_is(self):
        link = tarfile.TarInfo("evil-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        suid = tarfile.TarInfo("suid")
        suid.size = 1
        suid.mode = 0o4755
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            ok = tarfile.TarInfo("fine.txt")
            ok.size = 2
            t.addfile(ok, io.BytesIO(b"ok"))
            t.addfile(link)
            t.addfile(suid, io.BytesIO(b"x"))
        self.write("mixed.tar", buf.getvalue())
        out = self.archive("--op", "extract", "mixed.tar")
        self.assertEqual(sorted(out.rules()), ["archive.entry-kind", "archive.special-mode"])
        self.assertFalse((self.dir / "fine.txt").exists())
        out = self.archive("--op", "extract", "mixed.tar", "fine.txt")
        check_stream(self, out)
        self.assertEqual(out.status, 0, out.raw)
        self.assertEqual((self.dir / "fine.txt").read_bytes(), b"ok")
        self.assertFalse((self.dir / "suid").exists())
        # An unsafe name stops the extract of a file that is fine.
        bad = tar_bytes([("fine2.txt", b"ok"), ("../escape", b"x")])
        self.write("bad.tar", bad)
        out = self.archive("--op", "extract", "bad.tar", "fine2.txt")
        self.assertEqual(out.rules(), ["archive.unsafe-name"])
        self.assertFalse((self.dir / "fine2.txt").exists())

    def test_a_selector_that_selects_nothing_or_is_unsafe_is_refused_and_nothing_is_written(self):
        name = self.selection_archive()
        (self.dir / "into").mkdir()
        before = self.tree()
        out = self.archive("--op", "extract", "--into", "into", name, "top.txt", "nope", "also-nope")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["archive.no-match", "archive.no-match"])
        self.assertEqual([r["error"]["detail"]["path"] for r in out.records if r.get("type") == "error"], ["nope", "also-nope"])
        self.assertEqual(out.end()["entries"], 0)
        self.assertEqual(self.tree(), before)
        for selector, rule in [("../x", "archive.unsafe-name"), ("/etc", "archive.unsafe-name"), ("src/../../x", "archive.unsafe-name")]:
            out = self.archive("--op", "extract", "--into", "into", name, selector)
            self.assertEqual(out.rules()[0], rule, selector)
            self.assertEqual(self.tree(), before, selector)

    def test_a_dry_run_of_a_selection_writes_nothing(self):
        name = self.selection_archive()
        (self.dir / "into").mkdir()
        before = self.tree()
        out = self.archive("--op", "extract", "--dry-run", "--into", "into", name, "src/sub")
        self.assertEqual(out.status, 9, out.raw)
        self.assertEqual([r["path"] for r in out.records if r.get("type") in ("dir", "file")], ["into/src", "into/src/sub", "into/src/sub/c.txt"])
        self.assertEqual(self.tree(), before)

    def test_a_name_that_is_a_file_and_a_directory_in_one_archive_is_a_duplicate(self):
        self.write("both.tar", tar_bytes([("x", b"file"), ("x/y.txt", b"under it")]))
        out = self.archive("--op", "extract", "both.tar")
        check_stream(self, out)
        self.assertEqual(out.rules(), ["archive.duplicate-entry"])
        self.assertEqual(out.records[0]["error"]["detail"]["path"], "x")
        self.assertEqual(list(self.dir.glob("x*")), [])
        self.write("both2.tar", tar_bytes([("p/q", b"file"), ("p/q", None)]))
        out = self.archive("--op", "extract", "both2.tar")
        self.assertEqual(out.rules(), ["archive.duplicate-entry"])

    def test_extract_never_replaces_and_a_dry_run_writes_nothing(self):
        self.write("one.tar", tar_bytes([("f.txt", b"new")]))
        out = self.archive("--op", "extract", "--dry-run", "one.tar")
        self.assertEqual(out.status, 9)
        self.assertEqual(out.files()[0]["path"], "f.txt")
        self.assertTrue(out.end()["dry_run"])
        self.assertFalse((self.dir / "f.txt").exists())
        self.assertEqual(sorted(self.tree()), ["one.tar"])
        out = self.archive("--op", "extract", "one.tar")
        self.assertEqual(out.status, 0)
        self.assertEqual((self.dir / "f.txt").read_bytes(), b"new")
        self.write("f.txt", b"mine")
        out = self.archive("--op", "extract", "one.tar")
        self.assertEqual((out.status, out.rules()), (5, ["conflict.exists"]))
        self.assertEqual((self.dir / "f.txt").read_bytes(), b"mine")

    @unittest.skipUnless(shutil.which("strace"), "needs strace")
    def test_a_dry_run_makes_no_mutating_call(self):
        self.write("one.tar", tar_bytes([("f.txt", b"new")]))
        (self.dir / "src").mkdir()
        self.write("src/a", b"a")
        for argv in [["--op", "extract", "--dry-run", "one.tar"], ["--op", "create", "--dry-run", "out.tgz", "src"]]:
            log = self.dir / "strace.log"
            subprocess.run(["strace", "-f", "-qq", "-o", str(log), "-e", "trace=openat,rename,renameat,renameat2,unlink,unlinkat,mkdir,mkdirat,write",
                            binary("archive"), *argv], cwd=self.dir, capture_output=True, check=False)
            text = log.read_text()
            for line in text.splitlines():
                if "openat" in line:
                    self.assertNotRegex(line, r"O_(WRONLY|RDWR|CREAT)", line)
                self.assertNotRegex(line, r"^\d+\s+(rename|unlink|mkdir)", line)
            log.unlink()
        self.assertEqual(sorted(self.tree()), ["one.tar", "src/a"])

    # ---- create ---------------------------------------------------------------------------------------------------------

    def test_create_is_a_function_of_the_files_not_of_their_times(self):
        a, b = self.dir / "a", self.dir / "b"
        for root in (a, b):
            (root / "t" / "u").mkdir(parents=True)
            (root / "t" / "x.txt").write_bytes(b"x" * 1000)
            (root / "t" / "u" / "y.txt").write_bytes(b"y" * 2000)
        os.utime(a / "t" / "x.txt", (1000, 1000))
        os.utime(b / "t" / "x.txt", (999999999, 999999999))
        old = os.umask(0o077)
        try:
            out_b = self.archive("--op", "create", "out.tgz", "t", cwd=b)
        finally:
            os.umask(old)
        out_a = self.archive("--op", "create", "out.tgz", "t", cwd=a)
        self.assertEqual(out_a.status, 0, out_a.raw)
        self.assertEqual((a / "out.tgz").read_bytes(), (b / "out.tgz").read_bytes())
        self.assertEqual(out_a.end()["archive_sha256"], hashlib.sha256((a / "out.tgz").read_bytes()).hexdigest())
        listing = subprocess.run(["tar", "-tvzf", str(a / "out.tgz")], capture_output=True, text=True).stdout
        self.assertIn("1970-01-01", listing)
        self.assertIn(" 0/0 ", listing)

    def test_create_refuses_what_it_cannot_archive_and_what_it_would_overwrite(self):
        (self.dir / "t").mkdir()
        self.write("t/f.txt", b"f")
        os.symlink("f.txt", self.dir / "t" / "link")
        out = self.archive("--op", "create", "o.tgz", "t")
        self.assertEqual(out.rules(), ["archive.entry-kind"])
        self.assertFalse((self.dir / "o.tgz").exists())
        self.write("exists.tgz", b"keep")
        out = self.archive("--op", "create", "exists.tgz", "t/f.txt")
        self.assertEqual((out.status, out.rules()), (5, ["conflict.exists"]))
        self.assertEqual((self.dir / "exists.tgz").read_bytes(), b"keep")
        out = self.archive("--op", "create", "n.tgz", "no-such")
        self.assertEqual(out.rules(), ["io.not-found"])
        out = self.archive("--op", "create", "long.tar", "--as", "tar", "t/f.txt")
        self.assertEqual(out.status, 0)
        self.write("x" * 101, b"data")
        out = self.archive("--op", "create", "name.tar", "x" * 101)
        self.assertEqual(out.rules(), ["archive.name-too-long"])
        out = self.archive("--op", "create", "many.tar", "--max-entries", "1", "t")
        self.assertEqual(out.rules()[0], "limit.too-many-entries")
        out = self.archive("--op", "create", "two.gz", "--as", "gz", "t/f.txt", "t/f.txt")
        self.assertEqual(out.rules()[0], "args.too-many-operands")

    def test_create_sorts_and_nests(self):
        for name in ["z.txt", "a/m.txt", "a/b.txt", "A.txt"]:
            self.write("t/" + name, name.encode())
        out = self.archive("--op", "create", "o.tar", "--as", "tar", "t")
        self.assertEqual(out.status, 0)
        with tarfile.open(self.dir / "o.tar") as t:
            self.assertEqual(t.getnames(), ["t", "t/A.txt", "t/a", "t/a/b.txt", "t/a/m.txt", "t/z.txt"])
        out = self.archive("--op", "create", "dot.tar", "--as", "tar", ".", "--root", str(self.dir / "t"), cwd=self.dir)
        self.assertEqual(out.status, 0, out.raw)

    def test_a_tree_larger_than_the_cap_is_refused_before_it_is_read_whole(self):
        self.write("t/big", bytes(3 << 20))
        out = self.archive("--op", "create", "o.tgz", "t", "--max-bytes", str(1 << 20))
        self.assertEqual(out.rules()[0], "limit.archive-too-large")
        self.assertFalse((self.dir / "o.tgz").exists())

    # ---- G8: racing extracts -----------------------------------------------------------------------------------------------------

    def test_racing_extracts_write_every_file_whole_and_once(self):
        files = [("f%02d.bin" % i, os.urandom(40000 + i)) for i in range(12)]
        self.write("r.tar", tar_bytes(files))
        for round_no in range(5):
            into = self.dir / ("race%d" % round_no)
            into.mkdir()
            procs = [subprocess.Popen([binary("archive"), "--op", "extract", "--into", into.name, "r.tar"], cwd=self.dir,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL) for _ in range(8)]
            results = [(p.wait(), p.stdout.read()) for p in procs]
            for status, raw in results:
                self.assertNotIn(status, TRAPS)
                self.assertIn(status, (0, 5), raw[-300:])
            wrote = 0
            for status, raw in results:
                wrote += sum(1 for l in raw.decode().splitlines() if '"type":"file"' in l)
            self.assertEqual(wrote, len(files), "each file is written by exactly one racer")
            self.assertEqual(sorted(p.name for p in into.iterdir()), sorted(n for n, _ in files), "a temporary was left")
            for name, data in files:
                self.assertEqual((into / name).read_bytes(), data)

    # ---- the contract ------------------------------------------------------------------------------------------------------------------

    def test_every_rule_of_the_tool_is_reached_by_a_fixture_in_this_file(self):
        declared = {r["rule"] for r in introspect("archive")["rules"] if r["rule"].startswith(("archive.", "limit."))}
        seen = {
            "archive.not-an-archive", "archive.bad-gzip", "archive.crc-mismatch", "archive.bad-deflate",
            "archive.bad-checksum", "archive.bad-header", "archive.truncated", "archive.unsafe-name",
            "archive.entry-kind", "archive.duplicate-entry", "archive.directory-missing", "archive.special-mode",
            "archive.no-match", "archive.unsupported", "archive.name-too-long",
            "limit.archive-too-large", "limit.too-many-entries",
        }
        self.assertEqual(declared, seen)
        reached = set()
        self.write("plain.txt", b"not an archive at all " * 100)
        reached |= set(self.archive("--op", "list", "plain.txt").rules())
        data = bytearray(tar_bytes([("a", b"x"), ("b", b"y")]))
        data[512 + 512] ^= 1  # the second header's first byte
        self.write("checksum.tar", bytes(data))
        reached |= set(self.archive("--op", "list", "checksum.tar").rules())
        header = bytearray(tar_bytes([("a", b"x")]))
        header[100:107] = b"9999999"
        header[148:156] = b"%06o\x00 " % (sum(header[:148]) + 32 * 8 + sum(header[156:512]))
        self.write("header.tar", bytes(header))
        reached |= set(self.archive("--op", "list", "header.tar").rules())
        self.write("dup.tar", tar_bytes([("same", b"1"), ("same", b"2")]))
        reached |= set(self.archive("--op", "extract", "dup.tar").rules())
        cut = tar_bytes([("a", b"x" * 600)])[:700]  # a valid header and half its body
        self.write("cut.tar", cut)
        reached |= set(self.archive("--op", "list", "cut.tar").rules())
        for rule, data in [("archive.bad-gzip", gzip.compress(b"x") * 2), ("archive.crc-mismatch", gzip.compress(b"abc")[:-8] + b"\0\0\0\0" + gzip.compress(b"abc")[-4:]),
                           ("archive.bad-deflate", b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07" + bytes(20))]:
            self.write("g.gz", data)
            reached |= set(self.archive("--op", "list", "g.gz").rules())
        self.write("link.tar", tar_bytes([tarfile.TarInfo("l")]))
        info = tarfile.TarInfo("sl")
        info.type = tarfile.SYMTYPE
        info.linkname = "x"
        self.write("sym.tar", tar_bytes([info]))
        reached |= set(self.archive("--op", "extract", "sym.tar").rules())
        self.write("../x.tar" if False else "up.tar", tar_bytes([("../x", b"1")]))
        reached |= set(self.archive("--op", "extract", "up.tar").rules())
        self.write("dir.tar", tar_bytes([("d/f", b"1")]))
        reached |= set(self.archive("--op", "extract", "--into", "nowhere", "dir.tar").rules())
        suid = tarfile.TarInfo("s")
        suid.size = 1
        suid.mode = 0o4755
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            t.addfile(suid, io.BytesIO(b"x"))
        self.write("suid.tar", buf.getvalue())
        reached |= set(self.archive("--op", "extract", "suid.tar").rules())
        reached |= set(self.archive("--op", "list", "suid.tar", "nothing-like-this").rules())
        self.make_zip("enc.zip", [("a", b"x", 0o644)])
        raw = bytearray((self.dir / "enc.zip").read_bytes())
        cd = struct.unpack("<I", raw[raw.rfind(b"PK\x05\x06") + 16:raw.rfind(b"PK\x05\x06") + 20])[0]
        raw[cd + 8] = 1
        self.write("enc2.zip", bytes(raw))
        reached |= set(self.archive("--op", "list", "enc2.zip").rules())
        self.write("t/" + "n" * 101, b"1")
        reached |= set(self.archive("--op", "create", "n.tar", "t/" + "n" * 101 if False else "t").rules())
        reached |= set(self.archive("--op", "list", "dir.tar", "--max-entries", "1", "--max-bytes", "10").rules())
        self.write("many.tar", tar_bytes([("f%d" % i, b"x") for i in range(3)]))
        reached |= set(self.archive("--op", "list", "many.tar", "--max-entries", "2").rules())
        self.assertEqual(seen - reached - {"archive.name-too-long"}, set(), "rules no fixture reaches")

    def test_the_flag_table_is_well_formed(self):
        # A `;` in a help text splits the table (it is `;`-separated): every flag is `--name`, of a known kind and role.
        import re
        d = introspect("archive")
        self.assertGreater(len(d["flags"]), 3)
        for f in d["flags"]:
            self.assertRegex(f["name"], r"^--[a-z][a-z0-9-]*$", f)
            self.assertRegex(f["kind"], r"^(bool|nat|hex64|text|any|path|choice:[a-z/]+)$", f)
            self.assertIn(f["role"], ["none", "path-read", "path-write", "root", "guard"], f)
            self.assertTrue(f["help"], f)

    def test_the_authority_is_the_write_row_and_nothing_more(self):
        d = introspect("archive")
        names = {l["name"] for l in d["authority"]["labels"]}
        self.assertEqual(names, {"args", "dir_read", "dir_write", "err_write", "file_read", "file_write", "fs_read", "heap", "io_write"})
        self.assertTrue(d["authority"]["bounded"])
        self.assertEqual(d["reversibility"], "irreversible-bounded")
        guaranteed = {k for k, v in d["guarantees"].items() if v is True}
        self.assertEqual(guaranteed, {"deterministic", "dry_run"})

    def test_usage_errors_are_tags(self):
        for argv, rule in [
            ([], "args.missing-operand"),
            (["--op", "create", "x.tgz"], "args.missing-operand"),
            (["--op", "list", "a", ""], "path.empty"),
            (["--op", "list", "--dry-run", "a"], "args.conflict"),
            (["--op", "list", "--into", "x", "a"], "args.conflict"),
            (["--op", "extract", "--as", "tar", "a"], "args.conflict"),
            (["--op", "flip", "a"], "args.bad-value"),
            (["--max-bytes", "0", "a"], "args.bad-value"),
            (["--max-bytes", "1073741825", "a"], "args.bad-value"),
            (["--max-entries", "1000001", "a"], "args.bad-value"),
            (["--bogus", "a"], "args.unknown-flag"),
        ]:
            out = self.archive(*argv)
            check_stream(self, out)
            self.assertEqual(out.rules()[0], rule, argv)
            self.assertEqual(out.status, 2)


if __name__ == "__main__":
    unittest.main()
