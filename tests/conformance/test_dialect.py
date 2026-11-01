"""`dialect`'s own gates: what makes a reader of unknown exports a tool and not a guess.

* D1, differential: tables are drawn at random (a delimiter, line ends, decimal marks, missing
  markers, quoted cells with the delimiter, a quote and a comma in them), written out in that
  dialect, and `dialect normalize` must give back the table that was drawn, as Python's `csv`
  reads it; `sniff` must name the delimiter that was used;
* D2, refusals: every way the data can be wrong has its rule, a line number or a column, and
  no cleaned file past it;
* D3, the contract: it writes nothing (no file appears beside the input), the same bytes in give
  the same bytes out, and `sniff` prints a `normalize` line that, run, gives the cleaned file.
"""

import csv
import io
import json
import os
import random
import shlex
import subprocess
import unittest

from harness import Fixture, binary


def dialect(fx, *args):
    p = subprocess.run([binary("dialect"), "--root", str(fx.root), *args], capture_output=True, cwd=fx.dir)
    return p.returncode, p.stdout, p.stderr


def doc(out):
    return json.loads(out)


DELIMS = {",": ",", ";": ";", "\t": "\t", "|": "|"}
MARKERS = ["NA", "N/A", "null", "NaN", "?"]


def draw(rng):
    """A random table: (header, rows of cells as the cleaned file should hold them, the dialect)."""
    delim = rng.choice(list(DELIMS))
    ncols = rng.randint(2, 7)
    kinds = [rng.choice(["int", "float", "floatc", "text", "date"]) for _ in range(ncols)]
    marker = rng.choice(MARKERS)
    header = ["col%d" % i for i in range(ncols)]
    rows, raw = [], []
    for _ in range(rng.randint(5, 60)):
        clean, written = [], []
        for k in kinds:
            if k == "int":
                v = str(rng.randint(-999, 99999))
                clean.append(v)
                written.append(v)
            elif k == "float":
                v = "%d.%d" % (rng.randint(0, 9999), rng.randint(0, 99))
                clean.append(v)
                written.append(v)
            elif k == "floatc":
                a, b = rng.randint(0, 9999), rng.randint(0, 99)
                clean.append("%d.%d" % (a, b))
                written.append("%d,%d" % (a, b))
            elif k == "date":
                v = "2025-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28))
                clean.append(v)
                written.append(v)
            else:
                v = rng.choice(["alpha", "beta gamma", "a,b", 'say "hi"', "x;y", "z|w", "tab\there", "ünï"])
                clean.append(v)
                written.append(v)
        rows.append(clean)
        raw.append(written)
    # missing cells: in numeric columns the marker or an empty cell; both end up empty
    for r in range(len(rows)):
        for c, k in enumerate(kinds):
            if k in ("int", "float", "floatc") and rng.random() < 0.1:
                rows[r][c] = ""
                raw[r][c] = rng.choice(["", marker])
    return header, rows, raw, delim, kinds


def render(header, raw, delim, eol, quote_all):
    """The table in a dialect: a cell is quoted when it must be (it holds the delimiter, a quote, a comma
    that is not a decimal mark) or, with `quote_all`, always."""
    def cell(v):
        must = delim in v or '"' in v or "\n" in v or quote_all
        # a decimal comma in a comma-delimited file has to be quoted
        if delim == "," and "," in v:
            must = True
        return '"%s"' % v.replace('"', '""') if must else v
    lines = [delim.join(cell(h) for h in header)] + [delim.join(cell(v) for v in row) for row in raw]
    return (eol.join(lines) + eol).encode()


def parse_csv(text):
    return list(csv.reader(io.StringIO(text.decode(), newline="")))


class Differential(unittest.TestCase):
    """D1."""

    def test_normalize_gives_back_the_table_that_was_drawn(self):
        rng = random.Random(20251101)
        for case in range(150):
            header, rows, raw, delim, kinds = draw(rng)
            eol = rng.choice(["\n", "\r\n"])
            data = render(header, raw, delim, eol, rng.random() < 0.2)
            if rng.random() < 0.2:
                data = b"\xef\xbb\xbf" + data
            with self.subTest(case=case, delim=repr(delim), kinds=kinds):
                fx = Fixture()
                (fx.root / "in.txt").write_bytes(data)
                rc, out, err = dialect(fx, "--format", "text", "normalize", "in.txt")
                self.assertEqual(rc, 0, err + out)
                got = parse_csv(out)
                self.assertEqual(got[0], header)
                self.assertEqual(got[1:], rows)
                self.assertNotIn(b"\r", out)

    def test_sniff_names_the_delimiter_and_the_columns(self):
        rng = random.Random(7)
        for case in range(100):
            header, rows, raw, delim, kinds = draw(rng)
            data = render(header, raw, delim, rng.choice(["\n", "\r\n"]), False)
            with self.subTest(case=case, delim=repr(delim)):
                fx = Fixture()
                (fx.root / "in.txt").write_bytes(data)
                rc, out, err = dialect(fx, "sniff", "in.txt")
                self.assertEqual(rc, 0, err + out)
                d = doc(out)["data"]
                self.assertEqual(d["delimiter"], delim)
                self.assertEqual(d["fields"], len(header))
                want = {"int": "integer", "float": "number", "floatc": "number", "date": "date", "text": "text"}
                for c, k in enumerate(kinds):
                    col = d["columns"][c]
                    self.assertEqual(col["name"], header[c])
                    if k in ("int", "float", "floatc", "date"):
                        # a column of numbers that is all empty is `empty`; the draw makes that unlikely, not impossible
                        self.assertIn(col["kind"], (want[k], "empty", "integer"))
                    if k == "floatc":
                        self.assertIn(col["decimal"], ("comma", None))


class Confidence(unittest.TestCase):
    """D1b: how sure `sniff` says it is, and that it says so when it is not."""

    def sniff(self, content):
        fx = Fixture()
        (fx.root / "f.txt").write_bytes(content)
        rc, out, err = dialect(fx, "sniff", "f.txt")
        self.assertEqual(rc, 0, err + out)
        return doc(out)["data"]

    def test_a_delimiter_that_splits_too_few_lines_alike_is_not_claimed(self):
        # ten lines, four of them with another count: 60 percent, under the 90 that is asked
        lines = ["a;b;c"] + ["1;2;3"] * 5 + ["1;2"] * 2 + ["1;2;3;4"] * 2
        d = self.sniff(("\n".join(lines) + "\n").encode())
        self.assertEqual(d["confidence"], "none")

    def test_one_that_fits_and_no_other_is_high(self):
        d = self.sniff(b"a;b;c\n1;2;3\n4;5;6\n")
        self.assertEqual((d["delimiter"], d["confidence"], d["also_fits"]), (";", "high", []))

    def test_another_delimiter_with_fewer_fields_is_medium_and_listed(self):
        # the comma of the decimal marks splits every row in two, the header (no comma) in one:
        # it does not fit; a header with a comma makes both fit, with fewer fields for the comma
        d = self.sniff(b"a,x;b;c\n1,5;2;3\n4,5;5;6\n")
        self.assertEqual((d["delimiter"], d["confidence"]), (";", "medium"))
        self.assertEqual(d["also_fits"], ["comma"])

    def test_two_that_fit_alike_are_low(self):
        d = self.sniff(b"a,b;c\n1,2;3\n4,5;6\n")
        self.assertEqual(d["confidence"], "low")
        self.assertEqual(len(d["also_fits"]), 1)

    def test_a_file_of_one_column_is_reported_not_refused(self):
        d = self.sniff(b"a\n1\n2\n")
        self.assertEqual((d["fields"], d["confidence"]), (1, "none"))


class Refusals(unittest.TestCase):
    """D2."""

    def run_on(self, name, content, *args):
        fx = Fixture()
        (fx.root / name).write_bytes(content)
        rc, out, err = dialect(fx, "--format", "json", *args, name)
        return fx, rc, out

    def rule(self, out):
        return doc(out)["error"]["rule"]

    def test_a_ragged_row_names_its_line(self):
        _, rc, out = self.run_on("r.csv", b"a,b\n1,2\n3\n4,5\n", "normalize")
        self.assertNotEqual(rc, 0)
        e = doc(out)["error"]
        self.assertEqual(e["rule"], "dialect.ragged-row")
        self.assertEqual(e["detail"]["line"], 3)

    def test_a_quote_that_does_not_close_names_its_line(self):
        _, rc, out = self.run_on("q.csv", b'x,y\n1,"a\n', "normalize")
        self.assertEqual(self.rule(out), "dialect.bad-quote")
        self.assertEqual(doc(out)["error"]["detail"]["line"], 2)

    def test_mixed_decimal_marks_in_one_column_are_not_guessed(self):
        _, rc, out = self.run_on("m.csv", b"x;y\n1,5;2\n1.5;3\n", "normalize")
        self.assertEqual(self.rule(out), "dialect.mixed-decimal")
        self.assertEqual(doc(out)["error"]["detail"]["name"], "x")

    def test_a_value_the_map_does_not_name_is_refused_not_passed_through(self):
        _, rc, out = self.run_on("m.csv", b"x;y\n1;a\n2;b\n3;c\n", "normalize", "--map", "y=a:1/b:2")
        self.assertEqual(self.rule(out), "dialect.unmapped-value")
        self.assertEqual(doc(out)["error"]["detail"]["value"], "c")

    def test_a_map_column_that_is_not_there(self):
        _, rc, out = self.run_on("m.csv", b"x,y\n1,a\n", "normalize", "--map", "zz=a:1")
        self.assertEqual(self.rule(out), "dialect.map-column-not-found")

    def test_a_malformed_map(self):
        _, rc, out = self.run_on("m.csv", b"x,y\n1,a\n", "normalize", "--map", "nope")
        self.assertEqual(self.rule(out), "args.bad-value")

    def test_an_empty_file(self):
        _, rc, out = self.run_on("e.csv", b"", "sniff")
        self.assertEqual(self.rule(out), "dialect.empty-file")

    def test_the_limits(self):
        _, rc, out = self.run_on("big.csv", b"a,b\n" + b"1,2\n" * 100, "sniff", "--max-bytes", "10")
        self.assertEqual(self.rule(out), "limit.input-too-large")
        _, rc, out = self.run_on("wide.csv", b"a,b,c,d\n1,2,3,4\n", "sniff", "--max-columns", "3")
        self.assertEqual(self.rule(out), "limit.too-many-columns")

    def test_the_arguments(self):
        fx = Fixture()
        (fx.root / "x.csv").write_bytes(b"a,b\n1,2\n")
        for argv, rule in [
            ([], "args.missing-operand"),
            (["frob", "x.csv"], "args.bad-value"),
            (["sniff"], "args.missing-operand"),
            (["sniff", "x.csv", "y.csv"], "args.too-many-operands"),
            (["--delimiter", "ab", "sniff", "x.csv"], "args.bad-value"),
            (["--sample-rows", "0", "sniff", "x.csv"], "args.bad-value"),
            (["sniff", "../x.csv"], "path.dotdot"),
            (["sniff", "nope.csv"], "io.not-found"),
        ]:
            with self.subTest(argv=argv):
                rc, out, err = dialect(fx, *argv)
                self.assertNotEqual(rc, 0)
                self.assertEqual(doc(out)["error"]["rule"], rule)

    def test_a_missing_marker_in_a_text_column_is_kept_unless_named(self):
        fx = Fixture()
        (fx.root / "t.csv").write_bytes(b"c,n\nNA,1\nx,NA\n")
        rc, out, _ = dialect(fx, "--format", "text", "normalize", "t.csv")
        self.assertEqual(out, b"c,n\nNA,1\nx,\n")
        rc, out, _ = dialect(fx, "--format", "text", "normalize", "--na", "NA", "t.csv")
        self.assertEqual(out, b"c,n\n,1\nx,\n")


class Contract(unittest.TestCase):
    """D3."""

    def test_nothing_is_written_beside_the_input(self):
        fx = Fixture()
        (fx.root / "in.txt").write_bytes(b"a;b\r\n1,5;x\r\n")
        before = sorted(os.listdir(fx.root))
        for verb in ("sniff", "normalize"):
            dialect(fx, "--format", "text", verb, "in.txt")
        self.assertEqual(sorted(os.listdir(fx.root)), before)

    def test_the_same_bytes_in_give_the_same_bytes_out(self):
        fx = Fixture()
        (fx.root / "in.txt").write_bytes(b"a;b\r\n1,5;x\r\n2,5;y\r\n")
        a = dialect(fx, "--format", "text", "normalize", "in.txt")
        b = dialect(fx, "--format", "text", "normalize", "in.txt")
        self.assertEqual(a, b)
        self.assertEqual(a[1], b"a,b\n1.5,x\n2.5,y\n")

    def test_the_line_sniff_prints_gives_the_cleaned_file(self):
        fx = Fixture()
        rows = ["id;date;region;amount;outcome\r\n"]
        for i in range(40):
            rows.append("%d;2025-01-%02d;%s;%d,%d;%s\r\n" % (i, i % 28 + 1, "NA" if i % 9 == 0 else "west", i * 7, i % 100, "won" if i % 3 == 0 else "lost"))
        (fx.root / "export.txt").write_bytes("".join(rows).encode())
        rc, out, _ = dialect(fx, "sniff", "export.txt")
        d = doc(out)["data"]
        self.assertEqual(d["delimiter"], ";")
        self.assertEqual(d["label_candidates"][0]["column"], "outcome")
        self.assertEqual([m["column"] for m in d["text_missing_markers"]], ["region"])
        line = d["normalize"]
        self.assertTrue(line.startswith("dialect --format text normalize"))
        self.assertTrue(line.endswith(" > clean.csv"))
        argv = shlex.split(line[: -len(" > clean.csv")])[1:]
        rc, cleaned, err = dialect(fx, *argv)
        self.assertEqual(rc, 0, err)
        got = parse_csv(cleaned)
        self.assertEqual(got[0], ["id", "date", "region", "amount", "outcome"])
        self.assertEqual(got[1][2], "")
        self.assertEqual(got[1][3], "0.0")
        self.assertEqual({r[4] for r in got[1:]}, {"0", "1"})

    def test_introspect_names_the_authority_and_it_is_read_only(self):
        p = subprocess.run([binary("dialect"), "introspect"], capture_output=True)
        d = json.loads(p.stdout)
        labels = {x["name"] for x in d["authority"]["labels"]} if isinstance(d.get("authority"), dict) else set()
        self.assertFalse(labels & {"file_write", "dir_write", "net_out", "net_in", "ffi", "clock"})


if __name__ == "__main__":
    unittest.main()
