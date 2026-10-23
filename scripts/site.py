#!/usr/bin/env python3
"""Build the GitHub Pages site from the tools themselves.

Every tool page is generated from the tool's own `introspect` output, the
same tables that drive its parser (D11), so the site cannot drift from the
binaries the build produced. The front page (docs/landing/) is a designed page
whose demo, matrix and numbers come from the built tools, which it runs in a
temporary directory; README.md is rendered as docs.html. Hand-written: the
stylesheets and the landing page's template.

    python3 scripts/site.py                  # build/ -> site/
    python3 scripts/site.py --build build --out site

Needs only the standard library. The Markdown converter handles the subset
README.md uses (headings, paragraphs, lists, tables, fenced code, inline code,
bold, italics, links) and nothing else; anything it does not know is escaped
and shown as text, never dropped.
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ["seek", "write", "replace", "peek", "jsonq", "tally", "hash", "list", "move", "archive", "run"]
REPO = "https://github.com/alpibrusl/cancho-tools"

CSS = """
:root {
  --bg: #fbfaf8; --fg: #1d1d1b; --muted: #5d5b56; --rule: #e3e0da;
  --code-bg: #f1eee8; --accent: #3a5a8c; --accent-soft: #e6ecf5;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --fg: #e8e6e1; --muted: #a29f97; --rule: #33322f;
    --code-bg: #22211f; --accent: #8fb0e0; --accent-soft: #1f2a3a;
  }
}
:root[data-theme="dark"] {
  --bg: #161615; --fg: #e8e6e1; --muted: #a29f97; --rule: #33322f;
  --code-bg: #22211f; --accent: #8fb0e0; --accent-soft: #1f2a3a;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
}
header.site {
  border-bottom: 1px solid var(--rule); padding: 12px 16px;
  display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: baseline;
}
header.site a.home { font-weight: 700; color: var(--fg); text-decoration: none; display: inline-flex; align-items: center; gap: 8px; }
header.site a.home img { border-radius: 6px; }
header.site nav { display: flex; flex-wrap: wrap; gap: 4px 12px; }
header.site nav a { color: var(--muted); text-decoration: none; font-family: ui-monospace, monospace; font-size: 14px; }
header.site nav a:hover, header.site nav a[aria-current] { color: var(--accent); }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
h1, h2, h3 { line-height: 1.25; }
h1 { font-size: 28px; margin-top: 0; }
h2 { font-size: 21px; margin-top: 40px; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }
h3 { font-size: 17px; margin-top: 28px; }
a { color: var(--accent); }
code, pre { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 14px; }
code { background: var(--code-bg); padding: 1px 4px; border-radius: 3px; overflow-wrap: anywhere; }
h1 code { font-size: inherit; background: none; padding: 0; }
pre { background: var(--code-bg); padding: 12px; overflow-x: auto; border-radius: 4px; }
pre code { background: none; padding: 0; }
.table-wrap { overflow-x: auto; margin: 16px 0; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
th, td { border-bottom: 1px solid var(--rule); padding: 6px 10px; text-align: left; vertical-align: top; }
th { font-weight: 600; white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.summary { font-size: 18px; color: var(--muted); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 16px; padding: 0; list-style: none; }
.chips li { background: var(--accent-soft); color: var(--fg); padding: 2px 8px; border-radius: 999px;
            font-family: ui-monospace, monospace; font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin: 16px 0; }
.card { border: 1px solid var(--rule); border-radius: 6px; padding: 12px 14px; }
.card h3 { margin: 0 0 4px; font-family: ui-monospace, monospace; }
.card h3 a { text-decoration: none; }
.card p { margin: 0; color: var(--muted); font-size: 15px; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; }
footer { color: var(--muted); font-size: 14px; border-top: 1px solid var(--rule); padding: 16px; text-align: center; }
"""


# --- Markdown, the subset README.md uses -------------------------------------


def how_many(operand):
    """`introspect`'s operand counts, as a person reads them."""
    lo, hi = operand["min"], operand["max"]
    if hi is None:
        return "%d or more" % lo
    if lo == hi:
        return str(lo)
    return "%d to %d" % (lo, hi)

def inline(text):
    """Escape, then mark up links (whose text may hold code), code spans, bold and italics."""
    parts = re.split(r"(\[[^\]]+\]\([^)\s]+\))", text)
    if len(parts) > 1:
        out = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                m = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)", part)
                out.append(f'<a href="{link(m.group(2))}">{inline(m.group(1))}</a>')
            else:
                out.append(inline(part))
        return "".join(out)
    out = []
    # Code spans first, so nothing inside them is touched.
    for i, part in enumerate(re.split(r"(`[^`]+`)", text)):
        if i % 2 == 1:
            out.append("<code>" + html.escape(part[1:-1]) + "</code>")
            continue
        s = html.escape(part, quote=False)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def link(target):
    """A README link resolves on GitHub; on the site it must point there too."""
    if re.match(r"^[a-z]+:", target) or target.startswith("#"):
        return html.escape(target)
    return html.escape(f"{REPO}/blob/main/{target}")


def split_row(line):
    # A `|` inside a code span is not a column boundary (`sort \\| uniq`).
    cells, cell, in_code, i = [], "", False, 0
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            cell += "|"
            i += 2
            continue
        if c == "`":
            in_code = not in_code
        if c == "|" and not in_code:
            cells.append(cell.strip())
            cell = ""
        else:
            cell += c
        i += 1
    cells.append(cell.strip())
    return cells


def markdown(text):
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        # A one-line HTML comment (a generated section's marker) shows nothing.
        if line.startswith("<!--") and line.rstrip().endswith("-->"):
            i += 1
            continue
        # The logo line (raw HTML) shows nothing here: the header carries the mark.
        if line.startswith('<p align="center"><img'):
            i += 1
            continue
        # A badge line (an image link) shows nothing: the converter has no images.
        if line.startswith("[![") and line.rstrip().endswith(")"):
            i += 1
            continue
        if line.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,6}) (.*)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            out.append(f'<h{level} id="{slug}">{inline(title)}</h{level}>')
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            head = split_row(line)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append(split_row(lines[j]))
                j += 1
            t = ['<div class="table-wrap"><table><thead><tr>']
            t += [f"<th>{inline(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            i = j
            continue
        if re.match(r"^[*-] ", line):
            items, j = [], i
            while j < len(lines) and (re.match(r"^[*-] ", lines[j]) or (lines[j].startswith("  ") and lines[j].strip())):
                if re.match(r"^[*-] ", lines[j]):
                    items.append(lines[j][2:].strip())
                else:
                    items[-1] += " " + lines[j].strip()
                j += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            i = j
            continue
        if re.match(r"^\d+\. ", line):
            items, j = [], i
            while j < len(lines) and (re.match(r"^\d+\. ", lines[j]) or (lines[j].startswith("   ") and lines[j].strip())):
                if re.match(r"^\d+\. ", lines[j]):
                    items.append(re.sub(r"^\d+\. ", "", lines[j]).strip())
                else:
                    items[-1] += " " + lines[j].strip()
                j += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            i = j
            continue
        if not line.strip():
            i += 1
            continue
        para, j = [], i
        while j < len(lines) and lines[j].strip() and not re.match(r"^(#|```|\||[*-] |\d+\. )", lines[j]):
            para.append(lines[j].strip())
            j += 1
        out.append("<p>" + inline(" ".join(para)) + "</p>")
        i = j
    return "\n".join(out)


# --- The landing page -----------------------------------------------------------

# What each tool replaces and how it is put to a reader: the one hand-kept table of the front page.
BLURBS = {
    "seek": ("grep -F", "Find literal text in files. A stream of matches, with a cursor when it is cut short."),
    "list": ("ls, find, tree", "The entries beneath a directory, sorted and depth-bounded. Links are never followed."),
    "peek": ("sed -n, head, tail", "A range of lines or bytes, with numbers and a cursor to carry on from."),
    "jsonq": ("jq", "One value from one strict JSON document, by RFC 6901 pointer."),
    "tally": ("sort | uniq -c", "Distinct lines or fields, counted and ranked."),
    "hash": ("sha256sum", "SHA-256 or SHA-512 of files of any size, and --verify."),
    "write": ("cp, >, tee", "Replace a file atomically, only if it holds what you say it does."),
    "replace": ("sed -i", "Replace exact text an expected number of times, atomically."),
    "move": ("mv", "Rename a file or directory in place, never over an existing name."),
    "archive": ("tar, gzip, zip", "List, extract and create tar, zip, gzip and zstd; read bzip2 and xz. Checked whole before a file is written."),
    "run": ("exec, timeout", "Opt-in. Start one program from a fixed directory with exact arguments, a fixed environment, a deadline and an output cap."),
}

# README's "How fast" rows: (what, cancho tool, [(incumbent, seconds)], cancho seconds, strings that must still be in README).
SPEED = [
    ("seek", 0.49, [("grep", 0.51), ("rg", 0.39)], ["**0.49 s**", "0.51 s", "0.39 s"]),
    ("peek", 0.08, [("sed+wc", 0.13)], ["**0.08 s**", "0.13 s"]),
    ("tally", 0.16, [("cut|sort|uniq", 0.68)], ["**0.16 s**", "0.68 s"]),
    ("jsonq", 0.14, [("jq", 0.73)], ["**0.14 s**", "0.73 s"]),
    ("list", 0.144, [("find", 0.146)], ["**0.144 s**", "0.146 s"]),
    ("hash", 0.52, [("sha256sum", 0.17)], ["0.52 s", "0.17 s"]),
]

PROMPT = r"""Try cancho-tools on one small task. Work in a new empty directory, never use sudo, and report honestly: do not retry a failing command in a loop and do not hide a failure.

1. Check that git, cargo (Rust) and clang exist. If one is missing, stop and tell me which; do not install system packages yourself.
2. Get the sources and build, as the Quick start of the README says:
   git clone https://github.com/alpibrusl/cancho
   git clone https://github.com/alpibrusl/cancho-tools && cd cancho-tools
   REV=$(sed -n 's/^cancho *= *"\([0-9a-f]*\)".*/\1/p' cancho.toml)
   (cd ../cancho && git fetch -q origin && git checkout "$REV" && cargo build --release -p cancho)
   export PATH=$PWD/../cancho/target/release:$PWD/build:$PATH
   cancho build
3. Ask a tool what it is: `seek introspect` (JSON: flags, rules, exit codes, authority).
4. Reproduce the race on a scratch file, with the tools only:
   printf 'shopping list\n' > notes.txt
   hash --root . notes.txt                          # note the hex: H1
   printf 'shopping list\n- milk\n'  | write --root . --if-sha256 H1 --stdin notes.txt   # expect changed:true
   printf 'shopping list\n- bread\n' | write --root . --if-sha256 H1 --stdin notes.txt   # expect exit 5, rule precondition.hash-mismatch
   Read actual_sha256 from the refusal's detail, re-apply both lines and retry with that hash.
5. Tell me: the exit status and the `rule` of the refusal in step 4, the final contents of notes.txt, and anything that failed, with the exact error.
Docs: README.md, docs/reference.md, docs/agent-first.md."""


def short(h):
    return h[:12] + "\u2026"


def sh(cmd, cwd, stdin=None):
    r = subprocess.run(cmd, shell=True, cwd=cwd, input=stdin, capture_output=True, text=True)
    return r.stdout, r.returncode


def real_flow(build):
    """Run the two-agents-one-file race with the built tools in a temporary directory; answer what the page replays."""
    b = str(build.resolve())
    start = "shopping list\n"
    a_text, b_text = start + "- milk (agent A)\n", start + "- bread (agent B)\n"
    lost = lambda t: [(x, "") for x in t.rstrip("\n").split("\n")]
    ev = lambda t, k="": (t, k)

    def state(file, a, b2, verdict):
        return {"file": file, "a": a, "b": b2, "verdict": verdict}

    first = state([("shopping list", "")], [ev("read notes.txt")], [ev("read notes.txt")], ["Both agents have read the file.", ""])
    # Plain writes.
    with tempfile.TemporaryDirectory() as d:
        Path(d, "notes.txt").write_text(start)
        sh("printf 'shopping list\\n- milk (agent A)\\n' > notes.txt", d)
        sh("printf 'shopping list\\n- bread (agent B)\\n' > notes.txt", d)
        final, _ = sh("cat notes.txt", d)
    assert final == b_text, final
    plain = [
        {"l": ["d", "$ printf 'shopping list\\n- milk (agent A)\\n'  > notes.txt   # agent A"],
         "s": state([("shopping list", ""), ("- milk (agent A)", "a")], [ev("read notes.txt"), ev("write: ok", "ok")], [ev("read notes.txt")], ["Agent A wrote its line.", ""])},
        {"l": ["d", "$ printf 'shopping list\\n- bread (agent B)\\n' > notes.txt   # agent B"],
         "s": state([("shopping list", ""), ("- milk (agent A)", "gone"), ("- bread (agent B)", "b")], [ev("read notes.txt"), ev("write: ok", "ok")], [ev("read notes.txt"), ev("write: ok", "ok")],
                    ["Agent A's line is gone, and nobody was told.", "bad"])},
        {"l": ["d", "$ cat notes.txt"]},
    ] + [{"l": ["w", x]} for x in final.rstrip("\n").split("\n")]
    plain[-1]["s"] = plain[1]["s"]  # the cat shows what the last write left

    # Guarded writes.
    with tempfile.TemporaryDirectory() as d:
        Path(d, "notes.txt").write_text(start)
        out, _ = sh(f"{b}/hash --root . notes.txt", d)
        h = json.loads(out.splitlines()[0])
        h1 = h["hex"]
        wa, rc_a = sh(f"{b}/write --root . --if-sha256 {h1} --stdin notes.txt", d, stdin=a_text)
        ja = json.loads(wa)["data"]
        wb, rc_b = sh(f"{b}/write --root . --if-sha256 {h1} --stdin notes.txt", d, stdin=b_text)
        jb = json.loads(wb)
        err = jb["error"]
        h2 = err["detail"]["actual_sha256"]
        merged = a_text + "- bread (agent B)\n"
        wc, rc_c = sh(f"{b}/write --root . --if-sha256 {h2} --stdin notes.txt", d, stdin=merged)
        jc = json.loads(wc)["data"]
        final2, _ = sh("cat notes.txt", d)
    assert (rc_a, rc_b, rc_c) == (0, 5, 0), (rc_a, rc_b, rc_c)
    assert err["rule"] == "precondition.hash-mismatch" and ja["after_sha256"] == h2 and final2 == merged
    compact = lambda o: json.dumps(o, separators=(",", ":"), ensure_ascii=False)
    pipe = "$ \u2026 | write --root . --if-sha256 %s --stdin notes.txt   # %s"
    both = state([("shopping list", ""), ("- milk (agent A)", "a"), ("- bread (agent B)", "b")], [ev("read, hash H1"), ev("write if H1: ok", "ok")],
                 [ev("read, hash H1"), ev("write if H1: refused", "bad"), ev("re-read, hash H2"), ev("write if H2: ok", "ok")], ["Both lines kept. B was told why, and what changed.", "ok"])
    guarded = [
        {"l": ["d", "$ hash --root . notes.txt                 # A and B both read it: H1 is its hash"],
         "s": state([("shopping list", "")], [ev("read notes.txt, hash H1")], [ev("read notes.txt, hash H1")], ["Both agents hold the hash of what they read.", ""])},
        {"l": ["a", compact({"type": "hash", "path": h["path"], "algo": h["algo"], "hex": short(h1), "bytes": h["bytes"]})]},
        {"l": ["d", pipe % ("H1", "agent A adds its line")]},
        {"l": ["a", compact({"path": ja["path"], "changed": ja["changed"], "bytes": ja["bytes"], "after_sha256": short(ja["after_sha256"])})],
         "s": state([("shopping list", ""), ("- milk (agent A)", "a")], [ev("read, hash H1"), ev("write if H1: ok", "ok")], [ev("read, hash H1")], ["Agent A's write went through.", ""])},
        {"l": ["d", pipe % ("H1", "agent B, stale")]},
        {"l": ["b", compact({"rule": err["rule"], "hint": err["hint"], "repair": err["repair"]["kind"],
                             "detail": {"expected_sha256": short(err["detail"]["expected_sha256"]), "actual_sha256": short(h2), "size": err["detail"]["size"]}})]},
        {"l": ["d", "# exit status 5"],
         "s": state([("shopping list", ""), ("- milk (agent A)", "a")], [ev("read, hash H1"), ev("write if H1: ok", "ok")],
                    [ev("read, hash H1"), ev("write if H1: refused", "bad"), ev("the refusal says what changed: H2")], ["Agent B was refused, and nothing was lost.", "ok"])},
        {"l": ["d", pipe % ("H2", "agent B re-reads (H2 came with the refusal), re-applies, retries")]},
        {"l": ["a", compact({"path": jc["path"], "changed": jc["changed"], "bytes": jc["bytes"], "after_sha256": short(jc["after_sha256"])})],
         "s": both},
        {"l": ["d", "$ cat notes.txt"]},
    ] + [{"l": ["w", x]} for x in final2.rstrip("\n").split("\n")]
    guarded[-1]["s"] = both

    refusal = json.dumps({"ok": False, "command": "write", "error": {
        "code": err["code"], "rule": err["rule"], "hint": err["hint"], "repair": err["repair"],
        "detail": {"path": err["detail"]["path"], "expected_sha256": short(err["detail"]["expected_sha256"]),
                   "actual_sha256": short(h2), "size": err["detail"]["size"]}}}, indent=2, ensure_ascii=False)
    return {"plain": {"start": first, "items": plain}, "guarded": {"start": first, "items": guarded}}, refusal


def authority_matrix(tools):
    cols = [("reads files", {"file_read", "dir_read"}), ("reads stdin", {"io_read"}), ("changes files", {"file_write", "dir_write"}),
            ("starts programs", {"exec"}), ("reads the clock", {"clock"}), ("uses the network", None)]
    net = lambda n: re.match(r"^(net|tcp|udp|sock|conn|listen|dns|http)", n) is not None
    rows = []
    for d in tools:
        names = {x["name"] for x in d["authority"]["labels"]}
        cells = []
        for title, effs in cols:
            on = any(net(n) for n in names) if effs is None else bool(names & effs)
            cells.append(f'<td><span class="dot {"x" if effs is None and on else "on" if on else ""}" title="{html.escape(title)}: {"yes" if on else "no"}"></span><span class="sr" hidden>{"yes" if on else "no"}</span></td>')
        kind = d["reversibility"].split("-")[1]
        rows.append(f'<tr{" class=opt" if d["tool"] == "run" else ""}><td><a href="{d["tool"]}.html">{d["tool"]}</a></td>' + "".join(cells) +
                    f'<td><span class="rev {kind}">{html.escape(d["reversibility"])}</span></td></tr>')
    head = "".join(f"<th>{t}</th>" for t, _ in cols)
    n_net = sum(1 for d in tools if any(net(x["name"]) for x in d["authority"]["labels"]))
    note = ("Dots are what the compiler found each program may do; a row is read from the binary's own <code>introspect</code>. "
            + ("None of them can reach the network. " if n_net == 0 else f"{n_net} of them can reach the network. ")
            + "<code>run</code> is opt-in and the only one that starts programs or reads the clock. Not shown: how much of each it may touch (every label is bounded; see a tool's page).")
    return f'<table><thead><tr><th>tool</th>{head}<th>if it goes wrong</th></tr></thead><tbody>{"".join(rows)}</tbody></table>', note, n_net


def speed_bars(readme):
    for _, _, _, needles in SPEED:
        for needle in needles:
            if needle not in readme:
                sys.exit(f"site: README.md no longer says {needle!r}; update SPEED in scripts/site.py with its 'How fast' table")
    out = ["<h3>Time to answer, 64 MiB (shorter is faster)</h3>"]
    for tool, mine, others, _ in SPEED:
        top = max([mine] + [v for _, v in others])
        rows = [(tool, mine, "")] + [(n, v, " inc") for n, v in others]
        bars = "".join(f'<div class="bar1{c}" style="--w:{v / top * 100:.1f}%"><span>{n} {v:g} s</span></div>' for n, v, c in rows)
        out.append(f'<div class="brow"><span>{tool}</span><div class="tr">{bars}</div></div>')
    return "".join(out)


SHOWN = ["seek", "list", "peek", "jsonq", "tally", "hash", "write", "replace", "move", "archive", "run"]  # reads first, then what changes things


def landing_page(tools, readme, build):
    tools = sorted(tools, key=lambda d: SHOWN.index(d["tool"]))
    tpl = (ROOT / "docs" / "landing" / "landing.html").read_text()
    flow, refusal = real_flow(build)
    matrix, matrix_note, n_net = authority_matrix(tools)
    kind = {"cheap": ("read-only", "r"), "bounded": ("changes files", "w"), "consequential": ("runs programs", "x")}
    cards = []
    for d in tools:
        name = d["tool"]
        vs, line = BLURBS[name]
        label, cls = kind[d["reversibility"].split("-")[1]]
        extra = '<span class="tag">opt-in</span>' if name == "run" else ""
        cards.append(f'<a class="tool{" opt" if name == "run" else ""}" href="{name}.html"><b>{name}</b><span class="vs">instead of {html.escape(vs)}</span>'
                     f'<p>{html.escape(line)}</p><span class="tags"><span class="tag {cls}">{label}</span>{extra}<span class="tag">{len(d["rules"])} rules</span></span></a>')
    rules = sum(len(d["rules"]) for d in tools)
    schemas = len({s for d in tools for s in d["schemas"]})
    n = len(tools)
    stats = "".join(f'<div class="num"><b>{v}</b><span>{html.escape(t)}</span></div>' for v, t in [
        (n, f"tools: {n - 1} by default, and run, which is opt-in"),
        (rules, "named refusals, each with an exit status and a repair kind"),
        (n_net, "tools that can reach the network, by the authority the compiler derived"),
        (schemas, "published JSON schemas the outputs are checked against"),
    ])
    pills = "".join(f"<span>{html.escape(x)}</span>" for x in [f"{n} tools", "JSON out", "refusals with a rule", "no network", "EUPL-1.2"] if x != "no network" or n_net == 0)
    body = (tpl.replace("{{PILLS}}", pills).replace("{{N_TOOLS}}", str(n)).replace("{{TOOLS}}", "".join(cards))
            .replace("{{MATRIX}}", matrix).replace("{{MATRIX_NOTE}}", matrix_note).replace("{{STATS}}", stats)
            .replace("{{BARS}}", speed_bars(readme)).replace("{{REFUSAL}}", html.escape(refusal))
            .replace("{{PROMPT}}", html.escape(PROMPT)).replace("{{FLOW}}", json.dumps(flow).replace("</", "<\\/")))
    return body


# --- Pages ---------------------------------------------------------------------

def page(title, body, current=None, description=""):
    marked = ' aria-current="page"'
    nav = "".join(f'<a href="{t}.html"{marked if t == current else ""}>{t}</a>' for t in TOOLS) + f'<a href="docs.html"{marked if current == "docs" else ""}>docs</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<meta property="og:image" content="https://alpibrusl.github.io/cancho-tools/assets/cancho-tools-logo-256.png">
<link rel="icon" type="image/png" href="assets/cancho-tools-icon-64.png">
<style>{CSS}</style>
</head>
<body>
<header class="site"><a class="home" href="index.html"><img src="assets/cancho-tools-icon-64.png" alt="" width="28" height="28">cancho-tools</a><nav>{nav}</nav></header>
<main>
{body}
</main>
<footer>Generated by <code>scripts/site.py</code> from the tools' own <code>introspect</code> output and
<a href="{REPO}/blob/main/README.md">README.md</a>. <a href="{REPO}">Source</a>.</footer>
</body>
</html>
"""


def table(head, rows, numeric=()):
    t = ['<div class="table-wrap"><table><thead><tr>']
    t += [f'<th{" class=num" if i in numeric else ""}>{h}</th>' for i, h in enumerate(head)]
    t.append("</tr></thead><tbody>")
    for r in rows:
        t.append("<tr>" + "".join(f'<td{" class=num" if i in numeric else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>")
    t.append("</tbody></table></div>")
    return "".join(t)


def code(s):
    return "<code>" + html.escape(str(s)) + "</code>"


def label(entry):
    name = entry["name"]
    return f'{name}("{entry["argument"]}")' if entry.get("argument") is not None else name


def tool_page(d):
    name = d["tool"]
    b = [f"<h1><code>{html.escape(name)}</code> {html.escape(d['version'])}</h1>",
         f'<p class="summary">{html.escape(d["summary"])}</p>',
         f"<pre><code>{html.escape(d['usage'])}</code></pre>"]
    b.append('<dl class="facts">')
    for k, v in [("output", "NDJSON stream ending in an <code>end</code> record" if d["output"] == "stream" else "one JSON document"),
                 ("schema", ", ".join(f'<a href="schemas/{s}.json">{s}</a>' for s in d["schemas"])),
                 ("reversibility", code(d["reversibility"])),
                 ("guarantees", ", ".join(code(k) for k, v in d["guarantees"].items() if v is True) or "none"),
                 ("concurrency", html.escape(d["guarantees"]["concurrency"])),
                 ("confinement", f"{code(d['confinement'])}: {html.escape(d['confinement_note'])}"),
                 ("reads the environment", "no" if not d["reads_environment"] else "yes"),
                 ("reads the clock", "no" if not d["reads_clock"] else "yes"),
                 ("compiler", f'<a href="https://github.com/alpibrusl/cancho/commit/{d["compiler"]}">{d["compiler"][:12]}</a>'),
                 ("for an agent", f'<a href="{name}.SKILL.md">SKILL.md</a> (<code>{name} skill</code>)')]:
        b.append(f"<dt>{k}</dt><dd>{v}</dd>")
    b.append("</dl>")

    b.append("<h2>Authority</h2>")
    b.append("<p>Derived by <code>cancho authority</code> from the source, embedded in the binary, "
             "and checked against <code>tools.toml</code>'s ceiling in CI. Every label is bounded.</p>")
    b.append('<ul class="chips">' + "".join(f"<li>{html.escape(label(x))}</li>" for x in d["authority"]["labels"]) + "</ul>")
    b.append("<p>What the row cannot say:</p><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in d["not_narrowable"]) + "</ul>")

    b.append("<h2>Operands</h2>")
    b.append(table(["Operand", "Role", "How many", "Meaning"],
                   [[code(o["name"]), code(o["role"]), html.escape(how_many(o)), html.escape(o["help"])] for o in d["operands"]]))
    b.append("<h2>Flags</h2>")
    b.append(table(["Flag", "Kind", "Role", "Default", "Meaning"],
                   [[code(f["name"]) + (" " + code(f["short"]) if f["short"] else ""), code(f["kind"]), code(f["role"]),
                     code(f["default"]) if f["default"] is not None else "", html.escape(f["help"])] for f in d["flags"]]))
    if d["limits"]:
        b.append("<h2>Limits</h2>")
        b.append(table(["Limit", "Default", "Ceiling"],
                       [[code(x["name"]), f'{x["default"]:,}', f'{x["ceiling"]:,}'] for x in d["limits"]], numeric=(1, 2)))
    b.append("<h2>Exit codes</h2>")
    b.append(table(["Code", "Name", "Meaning"],
                   [[str(x["code"]), code(x["name"]), html.escape(x["meaning"])] for x in d["exit_codes"]], numeric=(0,)))
    b.append(f"<h2>Rules ({len(d['rules'])})</h2>")
    b.append("<p>Match on <code>rule</code>, not on the message. A <code>retry</code> repair is an argv "
             "that can be run as it stands, and never widens what the tool may touch.</p>")
    b.append(table(["Rule", "Exit", "Repair", "When"],
                   [[code(r["rule"]), str(r["exit"]), html.escape(r["repairable"]), html.escape(r["summary"])] for r in d["rules"]],
                   numeric=(1,)))
    ev = d["evidence"]
    b.append("<h2>Evidence</h2>")
    b.append("<p>" + html.escape(ev["offline_note"]) + ":</p>")
    b.append('<ul class="chips">' + "".join(f"<li>{html.escape(x)}</li>" for x in ev["offline"]) + "</ul>")
    b.append("<p><strong>Agent in the loop:</strong> " + html.escape(ev["agent_in_the_loop"]) + ".</p>")
    return page(f"{name} — cancho-tools", "\n".join(b), current=name, description=d["summary"])


def index_page(readme, tools):
    cards = "".join(
        f'<div class="card"><h3><a href="{d["tool"]}.html">{d["tool"]}</a></h3><p>{html.escape(d["summary"])}</p></div>'
        for d in tools
    )
    body = markdown(readme)
    # The README's own tool table stays; the cards come first, as the way in.
    first_h2 = body.find("<h2")
    body = body[:first_h2] + f'<h2 id="tools-at-a-glance">The tools at a glance</h2><div class="cards">{cards}</div>' + body[first_h2:]
    return page("cancho-tools docs", body, current="docs", description="The cancho-tools README: the tools, the contract, how to build and use them.")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--build", default=str(ROOT / "build"))
    p.add_argument("--out", default=str(ROOT / "site"))
    a = p.parse_args()
    build, out = Path(a.build), Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "schemas").mkdir(parents=True)
    tools = []
    for name in TOOLS:
        exe = build / name
        if not exe.exists():
            sys.exit(f"site: {exe} is missing; run `cancho build` first")
        d = json.loads(subprocess.run([str(exe), "introspect"], check=True, capture_output=True).stdout)
        tools.append(d)
        (out / f"{name}.html").write_text(tool_page(d))
        (out / f"{name}.SKILL.md").write_bytes(subprocess.run([str(exe), "skill"], check=True, capture_output=True).stdout)
        for schema, body in d["schemas"].items():
            (out / "schemas" / f"{schema}.json").write_text(json.dumps(body, indent=2) + "\n")
    (out / "assets").mkdir()
    for f in sorted((ROOT / "docs" / "assets").iterdir()):
        shutil.copy(f, out / "assets" / f.name)
    for f in ("landing.css", "landing.js"):
        shutil.copy(ROOT / "docs" / "landing" / f, out / "assets" / f)
    readme = (ROOT / "README.md").read_text()
    (out / "docs.html").write_text(index_page(readme, tools))
    (out / "index.html").write_text(landing_page(tools, readme, build))
    (out / ".nojekyll").write_text("")
    print(f"site: {len(tools)} tools -> {out}")


if __name__ == "__main__":
    main()
