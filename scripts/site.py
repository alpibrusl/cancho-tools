#!/usr/bin/env python3
"""Build the GitHub Pages site from the tools themselves.

Every tool page is generated from the tool's own `introspect` output, the
same tables that drive its parser (D11), so the site cannot drift from the
binaries the build produced. The front page is README.md. Nothing here is
hand-maintained except the stylesheet.

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
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ["seek", "write", "replace", "peek", "jsonq", "tally", "hash", "list"]
REPO = "https://github.com/alpibrusl/lexsys-tools"

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
header.site a.home { font-weight: 700; color: var(--fg); text-decoration: none; }
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


# --- Pages ---------------------------------------------------------------------

def page(title, body, current=None, description=""):
    marked = ' aria-current="page"'
    nav = "".join(f'<a href="{t}.html"{marked if t == current else ""}>{t}</a>' for t in TOOLS)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<style>{CSS}</style>
</head>
<body>
<header class="site"><a class="home" href="index.html">lexsys-tools</a><nav>{nav}</nav></header>
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
                 ("confinement", f"{code(d['confinement'])}: {html.escape(d['confinement_note'])}"),
                 ("reads the environment", "no" if not d["reads_environment"] else "yes"),
                 ("reads the clock", "no" if not d["reads_clock"] else "yes"),
                 ("compiler", f'<a href="https://github.com/alpibrusl/lex-sys/commit/{d["compiler"]}">{d["compiler"][:12]}</a>'),
                 ("for an agent", f'<a href="{name}.SKILL.md">SKILL.md</a> (<code>{name} skill</code>)')]:
        b.append(f"<dt>{k}</dt><dd>{v}</dd>")
    b.append("</dl>")

    b.append("<h2>Authority</h2>")
    b.append("<p>Derived by <code>lex-sys authority</code> from the source, embedded in the binary, "
             "and checked against <code>tools.toml</code>'s ceiling in CI. Every label is bounded.</p>")
    b.append('<ul class="chips">' + "".join(f"<li>{html.escape(label(x))}</li>" for x in d["authority"]["labels"]) + "</ul>")
    b.append("<p>What the row cannot say:</p><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in d["not_narrowable"]) + "</ul>")

    b.append("<h2>Operands</h2>")
    b.append(table(["Operand", "Role", "Meaning"],
                   [[code(o["name"]), code(o["role"]), html.escape(o["help"])] for o in d["operands"]]))
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
    return page(f"{name} — lexsys-tools", "\n".join(b), current=name, description=d["summary"])


def index_page(readme, tools):
    cards = "".join(
        f'<div class="card"><h3><a href="{d["tool"]}.html">{d["tool"]}</a></h3><p>{html.escape(d["summary"])}</p></div>'
        for d in tools
    )
    body = markdown(readme)
    # The README's own tool table stays; the cards come first, as the way in.
    first_h2 = body.find("<h2")
    body = body[:first_h2] + f'<h2 id="tools-at-a-glance">The tools at a glance</h2><div class="cards">{cards}</div>' + body[first_h2:]
    return page("lexsys-tools", body, description="An agent toolbox in lex-sys: seven unix-like tools with a JSON contract.")


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
            sys.exit(f"site: {exe} is missing; run `lex-sys build` first")
        d = json.loads(subprocess.run([str(exe), "introspect"], check=True, capture_output=True).stdout)
        tools.append(d)
        (out / f"{name}.html").write_text(tool_page(d))
        (out / f"{name}.SKILL.md").write_bytes(subprocess.run([str(exe), "skill"], check=True, capture_output=True).stdout)
        for schema, body in d["schemas"].items():
            (out / "schemas" / f"{schema}.json").write_text(json.dumps(body, indent=2) + "\n")
    (out / "index.html").write_text(index_page((ROOT / "README.md").read_text(), tools))
    (out / ".nojekyll").write_text("")
    print(f"site: {len(tools)} tools -> {out}")


if __name__ == "__main__":
    main()
