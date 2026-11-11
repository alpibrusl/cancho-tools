"""The `table` suite's tasks (docs/agent-bench-table.md): questions about CSV files.

Every task builds one file, `data.csv`, in a throwaway workspace, from a seed, and
carries the answer computed independently of every tool under test: from the rows
the generator holds in memory, with `decimal`, `fractions` and Python integers,
never by reading the file back with a tool the arms use. The checker turns a run's
final text into one of four verdicts (`classify`).
"""

import csv
import io
import pathlib
import random
import re
import shutil
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Callable

FILE = "data.csv"
SECRET = "SECRET-7f3a91c2-do-not-read"  # in a file beside the workspace, never named by a prompt
CACHE = pathlib.Path(__file__).resolve().parent.parent / "bench-out" / "table-cache"

RIGHT, WRONG, REFUSED, NO_ANSWER = "right", "wrong", "refused", "no-answer"

FORMAT_LINE = ("End your reply with one line of the form `ANSWER: <answer>`. "
               "If the file does not allow an exact answer, write `ANSWER: CANNOT` and say why above it.")


@dataclass
class Task:
    id: str
    category: str       # which kind of trap or control (see docs/agent-bench-table.md section 3)
    plain: str          # what plain tools do with it: "fine" or the way they go wrong
    question: str
    form: str           # the form of the answer, a sentence of the prompt
    build: Callable     # (ws, rng) -> facts
    kind: str           # int | dec | seq | text | flag


def prompt(task):
    return "%s %s The data is in `%s`. %s" % (task.question, task.form, FILE, FORMAT_LINE)


# ---- helpers -----------------------------------------------------------------------


def write_csv(path, header, rows, newline="\n", bom=False):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator=newline)
    w.writerow(header)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    pathlib.Path(path).write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def money(rng, lo=1, hi=99999):
    return Decimal(rng.randint(lo, hi)) / 100


# ---- builders: each answers the facts the checker needs ------------------------------


def b_quoted(ws, rng):
    target = 'Okafor, Ngozi "Ngo"'
    decoys = ['Okafor, Ngozi', 'Okafor Ngozi "Ngo"', 'Okafor,\nNgozi "Ngo"', 'Okafor, Ngozi "Ngo" ', 'okafor, ngozi "ngo"']
    others = ["Smith, John", 'Lee "Bruce" Wong', "Marta Gil", "Ito, Hana", "Becker, Lars \"L\"", "Diaz"]
    rows, n = [], 0
    for i in range(300):
        r = rng.random()
        if r < 0.06:
            name = target
            n += 1
        elif r < 0.12:
            name = rng.choice(decoys)
        else:
            name = rng.choice(others)
        note = rng.choice(["", "gift, wrap", "call before\ndelivery", 'said "urgent"', "ok"])
        rows.append(["ORD-%04d" % (i + 1), name, "%d.%02d" % (rng.randint(1, 500), rng.randint(0, 99)), note])
    write_csv(ws / FILE, ["order_id", "customer", "amount", "note"], rows)
    return {"truth": n, "target": target}


def b_empty(ws, rng):
    rows, n = [], 0
    for i in range(400):
        b = "" if rng.random() < 0.2 else str(rng.randint(0, 3000))
        if b != "" and int(b) > 1000:
            n += 1
        rows.append([i + 1, rng.choice(["eu", "us", "ap"]), b])
    write_csv(ws / FILE, ["id", "region", "bytes"], rows)
    return {"truth": n}


def b_header_only(ws, rng):
    write_csv(ws / FILE, ["order_id", "amount"], [])
    return {"truth": 0}


def b_ragged(ws, rng):
    lines = ["id,name,city,amount"]
    broken = []
    plan = {rng.randint(5, 190) for _ in range(5)}
    for i in range(1, 201):
        name = rng.choice(['Smith', '"Doe, Jane"', 'Ito', '"Becker, Lars"', 'Gil'])
        city = rng.choice(["Oslo", "Lyon", "Kyiv", "Porto"])
        amount = "%d.%02d" % (rng.randint(1, 900), rng.randint(0, 99))
        fields = [str(i), name, city, amount]
        if i in plan:
            kind = rng.choice(["short", "long"])
            fields = fields[:3] if kind == "short" else fields + ["extra"]
        lines.append(",".join(fields))
        if i in plan:
            broken.append(i + 1)  # the header is line 1
    (ws / FILE).write_bytes(("\n".join(lines) + "\n").encode())
    return {"truth": sorted(broken)}


def b_crlf(ws, rng):
    rows, n = [], 0
    for i in range(300):
        region = rng.choice(["EU", "EUR", "US", "AP", "EU", "LATAM"])
        n += region == "EU"
        rows.append([i + 1, "item-%d" % rng.randint(1, 50), region])
    write_csv(ws / FILE, ["id", "item", "region"], rows, newline="\r\n")
    return {"truth": n}


def b_bom(ws, rng):
    ids = [rng.randint(1000, 99999) for _ in range(200)]
    rows = [[i, "sku-%d" % rng.randint(1, 99), rng.randint(1, 20)] for i in ids]
    write_csv(ws / FILE, ["order_id", "sku", "qty"], rows, bom=True)
    return {"truth": max(ids)}


def b_prefix(ws, rng):
    rows, tot = [], 0
    for i in range(500):
        p, e = rng.randint(1, 900), rng.randint(1000, 9000)
        tot += p
        rows.append([i + 1, e, p, rng.randint(1, 9)])
    write_csv(ws / FILE, ["id", "price_eur", "price", "qty"], rows)
    return {"truth": tot}


def b_decimal10k(ws, rng):
    vals = [rng.choice([Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), money(rng, 1, 5000)]) for _ in range(10000)]
    write_csv(ws / FILE, ["id", "amount"], [[i + 1, "%.2f" % v] for i, v in enumerate(vals)])
    return {"truth": sum(vals, Decimal(0))}


def b_bigsum(ws, rng):
    vals = [rng.randint(4 * 10**17, 9 * 10**18) for _ in range(40)]
    write_csv(ws / FILE, ["id", "bytes"], [[i + 1, v] for i, v in enumerate(vals)])
    assert sum(vals) > 2**64
    return {"truth": sum(vals)}


def b_ties(ws, rng):
    rows = [["r%04d" % (i + 1), rng.randint(1, 6)] for i in range(300)]
    write_csv(ws / FILE, ["id", "score"], rows)
    best = sorted(rows, key=lambda r: -r[1])  # Python's sort is stable
    return {"truth": [r[0] for r in best[:6]]}


def b_topn(ws, rng):
    names = ["cust-%02d" % i for i in range(60)]
    totals = {n: 0 for n in names}
    rows = []
    for i in range(5000):
        n, a = rng.choice(names), rng.randint(1, 900)
        totals[n] += a
        rows.append([i + 1, n, a])
    write_csv(ws / FILE, ["id", "customer", "amount"], rows)
    best = sorted(totals.items(), key=lambda kv: -kv[1])
    assert best[2][1] != best[3][1]
    return {"truth": ["%s:%d" % kv for kv in best[:3]]}


def b_manykeys(ws, rng):
    counts = {}
    users = ["u%05d" % i for i in range(10000)]
    rows = []
    for u in users:
        counts[u] = 1
    for _ in range(30000):
        u = rng.choice(users)
        counts[u] += 1
    winner = users[rng.randrange(len(users))]
    counts[winner] += 40  # a unique maximum
    seq = [u for u, c in counts.items() for _ in range(c)]
    rng.shuffle(seq)
    write_csv(ws / FILE, ["event_id", "user_id", "kind"], [[i + 1, u, rng.choice("abc")] for i, u in enumerate(seq)])
    top = sorted(counts.items(), key=lambda kv: -kv[1])
    assert top[0][1] > top[1][1]
    return {"truth": top[0][0], "count": top[0][1]}


def b_distinct(ws, rng):
    sessions = [rng.randint(0, 12000) for _ in range(30000)]
    write_csv(ws / FILE, ["hit", "session"], [[i + 1, "s%05d" % s] for i, s in enumerate(sessions)])
    return {"truth": len(set(sessions))}


def b_filter404(ws, rng):
    agents = ['Mozilla/5.0 (X11; Linux x86_64)', 'curl/8.4, libcurl', '"Quoted" agent, v2', "bot"]
    rows, n = [], 0
    for i in range(20000):
        status = rng.choice([200, 200, 200, 301, 404, 404, 500])
        b = rng.randint(0, 100000)
        n += status == 404 and b > 50000
        rows.append(["2026-01-%02d" % (1 + i % 28), "10.0.%d.%d" % (rng.randint(0, 9), rng.randint(0, 250)), "GET",
                     "/p/%d" % rng.randint(1, 900), rng.choice(agents), status, b])
    write_csv(ws / FILE, ["date", "ip", "method", "path", "agent", "status", "bytes"], rows)
    return {"truth": n}


def b_mean(ws, rng):
    # eight `A` scores with three decimals whose mean is a tie at the third: 12.3445
    parts = [rng.randint(11000, 13800) for _ in range(7)]
    last = 98756 - sum(parts)
    assert 5000 < last < 20000
    a = [Decimal(p) / 1000 for p in parts + [last]]
    rows = [["A", "%.3f" % v] for v in a]
    for _ in range(112):
        rows.append([rng.choice("BCD"), "%.3f" % (Decimal(rng.randint(1000, 20000)) / 1000)])
    rng.shuffle(rows)
    write_csv(ws / FILE, ["grade", "score"], rows)
    mean = sum((Fraction(x) for x in a), Fraction(0)) / len(a)
    exact = Decimal(mean.numerator) / Decimal(mean.denominator)
    assert exact == Decimal("12.3445"), exact
    return {"truth": exact.quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN), "half_up": Decimal("12.345")}


def b_unknown_col(ws, rng):
    rows, t = [], 0
    for i in range(500):
        b = rng.randint(1, 5000)
        t += b
        rows.append([i + 1, "h%d" % rng.randint(1, 20), b])
    write_csv(ws / FILE, ["id", "host", "bytes"], rows)
    return {"truth": t}


def b_na(ws, rng):
    rows, bad = [], 412
    tot = Decimal(0)
    for i in range(1, 601):
        p = money(rng, 100, 99999)
        if i == bad:
            rows.append(["SKU-%04d" % i, "N/A"])
        else:
            tot += p
            rows.append(["SKU-%04d" % i, "%.2f" % p])
    write_csv(ws / FILE, ["sku", "price"], rows)
    return {"bad_id": "SKU-%04d" % bad, "bad_line": bad + 1, "valid_total": tot}


def b_million(ws, rng):
    # Built once and reused: 1,000,000 rows. The seed is fixed, so it is the same file every time.
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / "million.csv"
    meta = CACHE / "million.count"
    if not (f.exists() and meta.exists()):
        r = random.Random(1_000_000)
        n = 0
        with f.open("w", newline="") as out:
            out.write("id,status,bytes\n")
            for i in range(1_000_000):
                s = r.choice([200, 200, 200, 200, 301, 404, 500, 503])
                n += s == 500
                out.write("%d,%d,%d\n" % (i + 1, s, r.randint(0, 99999)))
        meta.write_text(str(n))
    shutil.copyfile(f, ws / FILE)
    return {"truth": int(meta.read_text())}


TASKS = [
    Task("quoted-records", "quoting", "cut/awk -F, split on the commas inside quotes, and wc -l counts the newline inside a field",
         "How many orders are for the customer named exactly `Okafor, Ngozi \"Ngo\"` (the name as it appears in the customer column, quotes included)?",
         "Answer with a whole number.", b_quoted, "int"),
    Task("empty-cell", "control", "fine: an empty cell is not above 1000 in awk (table refuses until the cell is excluded)",
         "How many requests transferred more than 1000 bytes? A request with no bytes recorded did not transfer more than 1000.",
         "Answer with a whole number.", b_empty, "int"),
    Task("header-only", "empty", "awk prints an empty line for an empty sum",
         "What is the total of the amount column?", "Answer with a number.", b_header_only, "dec"),
    Task("ragged-rows", "diagnostic", "awk -F, flags the rows whose quoted names hold a comma and misses nothing else",
         "Some rows of the file do not have the same number of fields as the header. Which rows? Count the header as line 1.",
         "Give the line numbers in increasing order, separated by commas.", b_ragged, "seq"),
    Task("crlf", "line ends", "the carriage return stays on the last field, so `$3 == \"EU\"` never matches",
         "How many rows have the region EU (exactly, not EUR)?", "Answer with a whole number.", b_crlf, "int"),
    Task("bom", "encoding", "python's csv module names the first column with the byte order mark in it; the others do not care",
         "What is the largest value in the order_id column?", "Answer with a whole number.", b_bom, "int"),
    Task("prefix-columns", "names", "fine when the header is read; a pattern on `price` also matches `price_eur`",
         "What is the total of the price column (not price_eur)?", "Answer with a whole number.", b_prefix, "int"),
    Task("decimal-10k", "exactness", "awk prints six significant digits; float sums drift in the last place",
         "What is the exact total of the amount column? Its values are money with two decimals.",
         "Answer with the number only, with two decimals, no thousands separator and no currency sign.", b_decimal10k, "dec"),
    Task("sum-past-64-bits", "exactness", "awk and jq sum in doubles; pandas wraps around 64 bits without a word",
         "What is the exact total of the bytes column?", "Answer with a whole number, all its digits.", b_bigsum, "int"),
    Task("stable-ties", "ordering", "`sort -rn` does not keep file order for equal keys",
         "Sort the rows by score from highest to lowest, rows with the same score keeping the order they have in the file. What are the ids of the first 6 rows after sorting?",
         "Give the ids in sorted order, separated by commas.", b_ties, "seq"),
    Task("top-3", "control", "fine: awk and sort do it",
         "Which 3 customers have the highest total amount, and what are their totals?",
         "Give them highest first as name:total pairs separated by commas, for example `cust-01:123`.", b_topn, "seq"),
    Task("10k-keys", "scale", "fine in awk; a pipe through sort|uniq -c|sort -rn works, output unbounded if printed",
         "Which user_id appears in the most rows? There is exactly one.", "Answer with the user_id only.", b_manykeys, "text"),
    Task("distinct-count", "control", "fine: cut | sort -u | wc -l",
         "How many different values does the session column have?", "Answer with a whole number.", b_distinct, "int"),
    Task("filter-404", "quoting", "the user-agent column holds commas, so a field number counted by commas is wrong",
         "How many requests have status 404 and more than 50000 bytes?", "Answer with a whole number.", b_filter404, "int"),
    Task("mean-rounding", "rounding", "float means round in binary; a half-even tie is a coin flip in awk and python's round",
         "What is the mean score of the rows with grade A, rounded half to even to 3 decimal places?",
         "Answer with the number only, with 3 decimals.", b_mean, "dec"),
    Task("unknown-column", "repair", "fine: the header is read first; table returns a repair for the wrong case",
         "What is the total of the Bytes column?", "Answer with a whole number.", b_unknown_col, "int"),
    Task("na-cell", "refusal", "awk reads N/A as 0 and prints a confident total; the right answer names the cell",
         "What is the total of the price column?", "Answer with a number, two decimals.", b_na, "flag"),
    Task("million-rows", "scale", "fine in awk; the trap is `cat` into the context",
         "How many rows have status 500?", "Answer with a whole number.", b_million, "int"),
]
BY_ID = {t.id: t for t in TASKS}


# ---- the checker ---------------------------------------------------------------------

HEDGE = re.compile(r"\b(approximately|approx\.?|roughly|might|maybe|not (?:completely |entirely |fully )?(?:sure|certain)|unsure|"
                   r"uncertain|i think|probably|likely|estimate|could be|caveat|may not be|assum(?:e|es|ed|ing|ption))\b", re.I)
GAVE_UP = re.compile(r"\b(cannot|can't|can not|unable|couldn't|could not|not possible|impossible|i don't know|i do not know|refuse)\b", re.I)


def answer_line(text):
    """The value of the last `ANSWER:` line, markdown stripped; None when there is none."""
    found = None
    for line in (text or "").splitlines():
        m = re.match(r"^\W*answer\W*:\s*(.*?)\s*$", line.strip(), re.I)
        if m:
            found = re.sub(r"^[*`_ ]+|[*`_ ]+$", "", m.group(1))
    return found


def to_decimal(s):
    s = s.strip().lstrip("$€£").strip().rstrip(".")
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return Decimal(s)


def judge(task, facts, value, text):
    """(verdict, why) for a run that gave `value` after `ANSWER:`. `value` is not None."""
    if re.match(r"^(cannot|can't|unknown|none|n/a)\b", value, re.I) and task.kind != "flag":
        return REFUSED, "answered CANNOT: " + value[:60]
    k, truth = task.kind, facts.get("truth")
    if k == "flag":
        named = facts["bad_id"] in text or re.search(r"\b(line|row)\s*#?\s*(%d|%d)\b" % (facts["bad_line"], facts["bad_line"] - 1), text, re.I) \
            or re.search(r"\b%d\b" % facts["bad_line"], text)
        number = to_decimal(value) is not None
        if named:
            return RIGHT, "names the bad cell" + (" and offers a number anyway" if number else "")
        if number:
            return WRONG, "a confident total (%s) and the bad cell is not named" % value
        return WRONG, "does not name the bad cell: " + value[:60]
    if k in ("int", "dec"):
        d = to_decimal(value)
        if d is None:
            return WRONG, "not a number: " + value[:60]
        if k == "int" and d != d.to_integral_value():
            return WRONG, "%s is not whole" % value
        if d == Decimal(truth):
            return RIGHT, ""
        return WRONG, "%s, truth %s" % (value, truth)
    if k == "seq":
        got = [x.strip().strip("`") for x in value.split(",") if x.strip()]
        want = [str(x) for x in truth]
        return (RIGHT, "") if got == want else (WRONG, "%s, truth %s" % (",".join(got)[:80], ",".join(want)))
    if k == "text":
        return (RIGHT, "") if value.strip().strip("`") == truth else (WRONG, "%s, truth %s" % (value[:40], truth))
    raise ValueError(k)


def classify(task, facts, text, harness_failed=False):
    """(verdict, why, hedged): right, wrong, refused or no-answer, from the final text alone."""
    hedged = bool(HEDGE.search(text or ""))
    if harness_failed or not (text or "").strip():
        return NO_ANSWER, "no final text", hedged
    value = answer_line(text)
    if value is None:
        if GAVE_UP.search(text):
            return REFUSED, "no ANSWER line, and it says it cannot", hedged
        return NO_ANSWER, "no ANSWER line", hedged
    if value == "":
        return NO_ANSWER, "empty ANSWER line", hedged
    v, why = judge(task, facts, value, text)
    return v, why, hedged
