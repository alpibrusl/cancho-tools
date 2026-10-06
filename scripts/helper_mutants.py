#!/usr/bin/env python3
"""Mutation check of the contract's helpers (`fail.choose_*`, `fail.detail_*`, `toolbox.sort`): each
mutant is `contract/fail.ls` or `contract/sort.ls` with one deliberate defect. It is killed when
`tests/sort_test.ls` (lex-sys test) or `tests/conformance/test_extension.py` (a program built with the
contract, tests/extension/ext.ls) fails, or when the contract no longer builds. The file is restored
after every mutant, whatever happens.

    python3 scripts/helper_mutants.py [name-substring ...]

Run where the pinned compiler is (`lex-sys` on PATH, or LEX_SYS). Exit status 1 if one survives.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPILER = os.environ.get("LEX_SYS", "lex-sys")

# (file, name, the text replaced, its replacement[, which occurrence]): without the last, `old` occurs exactly once in its file.
MUTANTS = [
    # fail.choose_*
    ("fail", "a choose repair says retry", 'o = json.put_string(heap, o, "choose");', 'o = json.put_string(heap, o, "retry");'),
    ("fail", "a choose repair's list is not called options", 'o = json.put_key(heap, o, "options");', 'o = json.put_key(heap, o, "choices");'),
    ("fail", "an option has no argv key", 'o = json.put_key(heap, o, "argv");\n    return json.begin_array(heap, o);\n}\n\n// One argument', 'o = json.put_key(heap, o, "args");\n    return json.begin_array(heap, o);\n}\n\n// One argument'),
    ("fail", "an option is not closed", "// `]}` -- the option is complete.\npub fn choose_option_close[&h](heap: &!h Heap, w: json.Writer) -> [heap] json.Writer {\n    let a = json.end_array(heap, w);\n    return json.end_object(heap, a);", "// `]}` -- the option is complete.\npub fn choose_option_close[&h](heap: &!h Heap, w: json.Writer) -> [heap] json.Writer {\n    return json.end_array(heap, w);"),
    ("fail", "a choose argument is written as a key", "return json.put_string(heap, w, argument);", "return json.put_key(heap, w, argument);"),
    ("fail", "an option drops the replaced argument", "        if i == at {\n            o = choose_arg(heap, o, replacement);\n        } else {\n            o = choose_arg(heap, o, arg(args, i));\n        }\n        i = i + 1;\n    }\n    return choose_option_close", "        if i == at {\n            o = choose_arg(heap, o, arg(args, i));\n        } else {\n            o = choose_arg(heap, o, arg(args, i));\n        }\n        i = i + 1;\n    }\n    return choose_option_close"),
    ("fail", "an option skips its last argument", "    while i < arg_count(args) {\n        if i == at {\n            o = choose_arg(", "    while i + 1 < arg_count(args) {\n        if i == at {\n            o = choose_arg("),
    # fail.detail_*
    ("fail", "detail_str writes the key twice", "pub fn detail_str[&h, &k, &v](heap: &!h Heap, w: json.Writer, key: &k [byte], value: &v [byte]) -> [heap] json.Writer {\n    let o = json.put_key(heap, w, key);\n    return json.put_string(heap, o, value);", "pub fn detail_str[&h, &k, &v](heap: &!h Heap, w: json.Writer, key: &k [byte], value: &v [byte]) -> [heap] json.Writer {\n    let o = json.put_key(heap, w, key);\n    return json.put_string(heap, o, key);"),
    ("fail", "detail_int writes a string", "    let o = json.put_key(heap, w, key);\n    return json.put_int(heap, o, value);", "    let o = json.put_key(heap, w, key);\n    return json.put_int(heap, o, 0 - value);"),
    ("fail", "detail_bool writes the opposite", "return json.put_bool(heap, o, value);", "return json.put_bool(heap, o, !value);"),
    ("fail", "detail_text writes bytes as a plain string", "    let o = json.put_key(heap, w, key);\n    return text.put(heap, o, value);", "    let o = json.put_key(heap, w, key);\n    return json.put_string(heap, o, value);"),
    # sort: `by` and `by_map` are two copies of one loop, so each defect is made in each copy (occurrence 0 is `by`, 1 is `by_map`).
    ("sort", "the sort is not stable", "!before(table, order[j], order[i])", "before(table, order[i], order[j])"),
    ("sort", "the map sort is not stable", "!before(m, order[j], order[i])", "before(m, order[i], order[j])"),
    ("sort", "the sort puts the right run first on a tie", "!before(table, order[j], order[i])", "before(table, order[i], order[j]) && false"),
    ("sort", "the map sort puts the right run first on a tie", "!before(m, order[j], order[i])", "before(m, order[i], order[j]) && false"),
    ("sort", "the last run is not merged", "        while lo < total {\n", "        while lo + width < total {\n", 0),
    ("sort", "the map sort's last run is not merged", "        while lo < total {\n", "        while lo + width < total {\n", 1),
    ("sort", "the merge width does not grow", "        width = width * 2;", "        width = width + 1;", 0),
    ("sort", "the map sort's merge width does not grow", "        width = width * 2;", "        width = width + 1;", 1),
    ("sort", "the result is not copied back", "            order[c] = spare[c];", "            order[c] = order[c];", 0),
    ("sort", "the map sort's result is not copied back", "            order[c] = spare[c];", "            order[c] = order[c];", 1),
    ("sort", "n is not clamped to order", "    if total > len(order) {\n        total = len(order);\n    }\n", "", 0),
    ("sort", "the map sort's n is not clamped to order", "    if total > len(order) {\n        total = len(order);\n    }\n", "", 1),
    ("sort", "n is not clamped to spare", "    if total > len(spare) {\n        total = len(spare);\n    }\n", "", 0),
    ("sort", "the map sort's n is not clamped to spare", "    if total > len(spare) {\n        total = len(spare);\n    }\n", "", 1),
    ("sort", "the run's end is not clamped", "            if hi > total {\n                hi = total;\n            }", "", 0),
    ("sort", "the map sort's run end is not clamped", "            if hi > total {\n                hi = total;\n            }", "", 1),
    ("sort", "descending sorts ascending", "        return by(keys, order, spare, n, key_after);", "        return by(keys, order, spare, n, key_before);"),
    ("sort", "ascending sorts descending", "    return by(keys, order, spare, n, key_before);", "    return by(keys, order, spare, n, key_after);"),
    ("sort", "an index past the keys is read", "    if i < 0 || i >= len(keys) {\n        return 0;\n    }\n", ""),
    ("sort", "a negative index is read", "    if i < 0 || i >= len(keys) {", "    if i >= len(keys) {"),
    ("sort", "identity is shifted", "        order[i] = i;", "        order[i] = i + 1;"),
    ("sort", "identity writes past n", "    while i < n && i < len(order) {", "    while i <= n && i < len(order) {"),
]

PATHS = {"fail": ROOT / "contract" / "fail.ls", "sort": ROOT / "contract" / "sort.ls"}
originals = {k: v.read_text() for k, v in PATHS.items()}


def restore(*_):
    for k, v in PATHS.items():
        v.write_text(originals[k])


def build_and_test():
    sources = sorted(str(p) for p in (ROOT / "contract").glob("*.ls"))
    u = subprocess.run([COMPILER, "test", *sources, str(ROOT / "tests" / "sort_test.ls"), "--std"], cwd=ROOT, capture_output=True, text=True, timeout=900)
    if u.returncode:
        return "killed", "sort_test: " + (u.stdout + u.stderr).strip().splitlines()[-1][:100]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_extension"], cwd=ROOT / "tests" / "conformance", capture_output=True, text=True, timeout=900)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main():
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    wanted = sys.argv[1:]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[1] for w in wanted)]
    verdict, _ = build_and_test()
    print("unmutated:", "pass" if verdict == "SURVIVED" else verdict, flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for which, name, old, new, *nth in chosen:
        text = originals[which]
        if text.count(old) != (2 if nth else 1):
            print("!! %s: the site occurs %d times" % (name, text.count(old)))
            return 1
        at = text.index(old)
        if nth and nth[0] == 1:
            at = text.index(old, at + 1)
        try:
            PATHS[which].write_text(text[:at] + new + text[at + len(old):])
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-9s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict != "killed":
            survivors.append(name)
    print("%d mutants, %d survived" % (len(chosen), len(survivors)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
