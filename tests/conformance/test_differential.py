"""M5 -- agreement with the incumbents on the semantic subset they share.

Each comparison reduces both outputs to a datum and compares that, over
seeded random cases plus the hand-written edge corpus (CRLF, no trailing
newline, NUL, long lines, invalid UTF-8, an empty file):

    seek   vs  LC_ALL=C grep -F -n -b -a [-i]
    peek   vs  sed -n 'A,Bp', and total_lines vs wc -l
    jsonq  vs  jq -c           (no duplicate keys: jq keeps the last, jsonq the first)
    hash   vs  sha256sum / sha512sum, across every padding boundary and past 64 KiB
    write  vs  cp              (the end state)
    tally  vs  LC_ALL=C sort | uniq -c | sort -k1,1nr -k2

DIFF_N cases per comparison (default 60), DIFF_SEED (default 7).
"""

import base64
import json
import os
import shutil
import subprocess
import unittest

from harness import Fixture, binary, rng

ENV = dict(os.environ, LC_ALL="C")
ALPHABET = [b"a", b"b", b"g", b"am", b"ma", b" ", b"\r", b"\x00", b"\xff", b"\xc3\xa9", b"\t", b"A", b"M", b"G"]


def text_of(value):
    if isinstance(value, dict):
        return base64.b64decode(value["b64"])
    return value.encode("utf-8")


def random_text(r, lines):
    out = []
    for _ in range(lines):
        out.append(b"".join(r.choice(ALPHABET) for _ in range(r.randint(0, 12))))
    data = b"\n".join(out)
    if r.random() < 0.7:
        data += b"\n"
    return data


def edge_files():
    return {
        "crlf": b"one\r\ntwo am\r\nthree\r\n",
        "no-newline": b"first\nlast am",
        "nul": b"he\x00am\nnext\x00\n",
        "latin1": b"caf\xe9 am\n\xff\xfe\n",
        "empty": b"",
        "long": b"short\n" + b"g" * 300000 + b" am\nend\n",
        "blank-lines": b"\n\n\nam\n\n",
    }


def cases(r, n):
    for name, data in edge_files().items():
        yield name, data
    for i in range(n):
        yield "random-%d" % i, random_text(r, r.randint(0, 40))


class Differential(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.n = int(os.environ.get("DIFF_N", "60"))
        self.seed = int(os.environ.get("DIFF_SEED", "7"))

    def tearDown(self):
        self.fx.cleanup()

    def put(self, name, data):
        path = self.fx.dir / name
        path.write_bytes(data)
        return path

    def test_seek_agrees_with_grep(self):
        r = rng(self.seed)
        for name, data in cases(r, self.n):
            path = self.put("f", data)
            for pattern in [b"am", b"g", b"\xff", b"", b"\xc3\xa9", r.choice(ALPHABET) + r.choice(ALPHABET)]:
                for fold in (False, True):
                    # An argument cannot hold a NUL, and grep treats an empty
                    # pattern as matching everything, which seek also does but
                    # which says nothing interesting twice.
                    if pattern == b"" or b"\x00" in pattern:
                        continue
                    flags = ["-i"] if fold else []
                    g = subprocess.run(["grep", "-F", "-n", "-b", "-a"] + flags + ["--", pattern, str(path)], capture_output=True, env=ENV)
                    want = []
                    rows = g.stdout.split(b"\n")
                    if rows[-1] == b"":
                        rows = rows[:-1]
                    for line in rows:
                        n, offset, text = line.split(b":", 2)
                        want.append((int(n), int(offset), text))
                    s = subprocess.run([binary("seek")] + (["--ascii-case-insensitive"] if fold else []) + ["--", pattern, str(path)], capture_output=True)
                    got = [(m["line"], m["offset"], text_of(m["text"])) for m in map(json.loads, s.stdout.splitlines()) if m["type"] == "match"]
                    self.assertEqual(got, want, (name, pattern, fold))

    def test_peek_agrees_with_sed_and_wc(self):
        r = rng(self.seed + 1)
        for name, data in cases(r, self.n):
            path = self.put("f", data)
            total = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
            for a, b in [(1, 1), (1, 5), (2, 3), (r.randint(1, 30), r.randint(30, 60))]:
                sed = subprocess.run(["sed", "-n", "%d,%dp" % (a, b), str(path)], capture_output=True, env=ENV).stdout
                want = sed.split(b"\n")
                if want and want[-1] == b"":
                    want = want[:-1]
                p = subprocess.run([binary("peek"), "--lines", "%d:%d" % (a, b), "--max-bytes", "16777216", "--count-lines", str(path)], capture_output=True)
                d = json.loads(p.stdout)["data"]
                self.assertEqual([text_of(l["text"]) for l in d["lines"]], want, (name, a, b))
                self.assertEqual(d["total_lines"], total, name)
                self.assertEqual(d["size"], len(data))
            wc = int(subprocess.run(["wc", "-l"], input=data, capture_output=True).stdout)
            self.assertEqual(total - (1 if data and not data.endswith(b"\n") else 0), wc)

    def random_json(self, r, depth=0):
        k = r.random()
        if depth > 3 or k < 0.3:
            return r.choice([None, True, False, r.randint(-10**6, 10**6), r.choice([0.5, -2.25, 1e10, 3.0]),
                             r.choice(["", "x", "é", "a/b", "t~0", "q\"uote", "line\nbreak", "ÿ中"])])
        if k < 0.65:
            return [self.random_json(r, depth + 1) for _ in range(r.randint(0, 4))]
        keys = ["a", "b", "c~d", "e/f", "", "é", "0", "10"]
        r.shuffle(keys)
        return {key: self.random_json(r, depth + 1) for key in keys[:r.randint(0, 5)]}

    def pointers(self, value, prefix=""):
        yield prefix
        if isinstance(value, dict):
            for key, v in value.items():
                yield from self.pointers(v, prefix + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                yield from self.pointers(v, prefix + "/%d" % i)

    def test_jsonq_agrees_with_jq(self):
        r = rng(self.seed + 2)
        for i in range(self.n):
            doc = self.random_json(r)
            text = json.dumps(doc, ensure_ascii=r.random() < 0.5, indent=r.choice([None, 2]))
            path = self.put("d.json", text.encode())
            for pointer in list(self.pointers(doc))[:12]:
                q = subprocess.run([binary("jsonq"), "-p", pointer, str(path)], capture_output=True)
                got = json.loads(q.stdout)
                self.assertTrue(got["ok"], (text, pointer, q.stdout))
                path_expr = json.dumps([p for p in pointer_parts(pointer, doc)])
                jq = subprocess.run(["jq", "-c", "getpath(%s)" % path_expr, str(path)], capture_output=True)
                self.assertEqual(got["data"]["value"], json.loads(jq.stdout), (text, pointer))
                if isinstance(json.loads(jq.stdout), dict):
                    k = subprocess.run([binary("jsonq"), "--keys", "-p", pointer, str(path)], capture_output=True)
                    jk = subprocess.run(["jq", "-c", "getpath(%s) | keys_unsorted" % path_expr, str(path)], capture_output=True)
                    self.assertEqual(json.loads(k.stdout)["data"]["keys"], json.loads(jk.stdout))

    def test_hash_agrees_with_sha256sum_and_sha512sum(self):
        r = rng(self.seed + 3)
        sizes = [0, 1, 55, 56, 63, 64, 65, 111, 112, 119, 120, 127, 128, 129, 65527, 65528, 65536, 1 << 20 | 1]
        sizes += [r.randint(0, 300000) for _ in range(10)]
        for size in sizes:
            path = self.put("h.bin", bytes(r.getrandbits(8) for _ in range(min(size, 4096))) * (size // 4096 + 1))
            with open(path, "r+b") as f:
                f.truncate(size)
            for algo, gnu in (("sha256", "sha256sum"), ("sha512", "sha512sum")):
                ours = json.loads(subprocess.run([binary("hash"), "--algo", algo, str(path)], capture_output=True).stdout.splitlines()[0])
                theirs = subprocess.run([gnu, str(path)], capture_output=True).stdout.split()[0].decode()
                self.assertEqual(ours["hex"], theirs, (size, algo))
                self.assertEqual(ours["bytes"], size)

    def test_write_ends_where_cp_ends(self):
        r = rng(self.seed + 4)
        for name, data in cases(r, self.n // 2):
            src = self.put("src", data)
            dst_cp = self.fx.dir / "dst-cp"
            dst_ours = self.fx.dir / "dst-ours"
            for p in (dst_cp, dst_ours):
                if p.exists():
                    p.unlink()
            shutil.copyfile(src, dst_cp)
            w = subprocess.run([binary("write"), "--create", "--content-file", str(src), str(dst_ours)], capture_output=True)
            self.assertEqual(w.returncode, 0, w.stdout)
            self.assertEqual(dst_ours.read_bytes(), dst_cp.read_bytes(), name)
            via_stdin = self.fx.dir / "dst-stdin"
            if via_stdin.exists():
                via_stdin.unlink()
            w = subprocess.run([binary("write"), "--create", "--stdin", str(via_stdin)], input=data, capture_output=True)
            self.assertEqual(via_stdin.read_bytes(), data, name)

    def test_list_agrees_with_find(self):
        """`list --long` over a random tree equals `find -printf` at every
        depth: the same paths, kinds, sizes and whole-second times. `list`'s
        order is each directory sorted bytewise, depth first, which is checked
        directly; `find`'s order is the kernel's, so it is compared as a set."""
        r = rng(7)
        root = self.fx.dir / "tree"
        root.mkdir()
        dirs = [root]
        for i in range(400):
            parent = r.choice(dirs)
            name = r.choice(["a", "b", "B", "-x", "x y", "z.txt", "0", "\u00e9t\u00e9", "dir"]) + str(i % 37)
            path = parent / name
            if path.exists():
                continue
            pick = r.random()
            if pick < 0.25 and len(path.relative_to(root).parts) < 5:
                path.mkdir()
                dirs.append(path)
            elif pick < 0.3:
                os.symlink(r.choice(["nowhere", "..", "a1"]), path)
            else:
                path.write_bytes(os.urandom(r.randrange(0, 3000)))
        kinds = {"f": "file", "d": "directory", "l": "link"}
        for depth in (1, 2, 3, 64):
            out = subprocess.run([binary("list"), "--root", str(root), "--depth", str(depth), "--long", "--max-entries", "1000000"],
                                 capture_output=True)
            self.assertEqual(out.returncode, 0, out.stdout[-300:])
            records = [json.loads(l) for l in out.stdout.splitlines()]
            entries = [x for x in records if x["type"] == "entry"]
            ours = sorted((e["path"], e["kind"], e["size"], e["mtime"]) for e in entries)
            found = subprocess.run(["find", str(root), "-mindepth", "1", "-maxdepth", str(depth), "-printf", "%P\t%y\t%s\t%T@\n"],
                                   capture_output=True, text=True).stdout
            theirs = sorted((p, kinds.get(y, "other"), int(size), int(float(t)))
                            for p, y, size, t in (line.split("\t") for line in found.splitlines()))
            self.assertEqual(ours, theirs, "--depth %d" % depth)
            # The order: within each directory, names ascend bytewise.
            seen = {}
            for e in entries:
                parent, _, name = e["path"].rpartition("/")
                last = seen.get(parent)
                if last is not None:
                    self.assertLess(last.encode(), name.encode(), e["path"])
                seen[parent] = name

    def test_tally_agrees_with_sort_uniq(self):
        r = rng(self.seed + 5)
        keys = [b"a", b"b", b"ab", b"B", b"\xff", b"\xc3\xa9", b"a b", b"zz", b"a\x01"]
        for i in range(self.n):
            data = b"".join(r.choice(keys) + b"\n" for _ in range(r.randint(1, 80)))
            path = self.put("t.txt", data)
            pipe = subprocess.run("sort | uniq -c | sort -k1,1nr -k2", shell=True, input=data, capture_output=True, env=ENV).stdout
            want = []
            for line in pipe.splitlines():
                count, key = line.lstrip().split(b" ", 1)
                want.append((key, int(count)))
            t = json.loads(subprocess.run([binary("tally"), "--top", "1000", str(path)], capture_output=True).stdout)["data"]
            got = [(text_of(e["key"]), e["count"]) for e in t["top"]]
            self.assertEqual(got, want, data)
            self.assertEqual(t["total"], data.count(b"\n"))


def pointer_parts(pointer, doc):
    """The jq path for an RFC 6901 pointer: keys as strings, indices as numbers."""
    parts = []
    node = doc
    for raw in pointer.split("/")[1:] if pointer else []:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            parts.append(int(key))
            node = node[int(key)]
        else:
            parts.append(key)
            node = node[key]
    return parts


if __name__ == "__main__":
    unittest.main()
