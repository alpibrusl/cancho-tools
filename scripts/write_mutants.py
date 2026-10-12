#!/usr/bin/env python3
"""Mutation check of `write --create`'s no-replace install (`atomic.create`, `dir_rename_new`): each mutant is
`contract/atomic.cho`, `contract/rules.cho` or `tools/write/write.cho` with one deliberate defect, rebuilt, and
run against `test_write_create`, `test_rules` and `test_mutation` (the writers' atomic/dry-run/temporary
gates). A mutant is killed when a test fails. Every file is restored after every mutant, whatever happens.

    python3 scripts/write_mutants.py [name-substring ...]

Run where the pinned compiler is (`cancho` on PATH, or CANCHO), on Linux with strace: the replacing rename,
the unmapped EEXIST and the unmapped errnos are killed by tests that delay the rename or inject the kernel's
answer under strace. The unmapped 45 (macOS's ENOTSUP) is killed on Linux by the injected EL2NSYNC, which is
45 there. Exit status 1 if one survives.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPILER = os.environ.get("CANCHO", "cancho")
FILES = {"atomic": ROOT / "contract" / "atomic.cho", "rules": ROOT / "contract" / "rules.cho", "write": ROOT / "tools" / "write" / "write.cho"}

# (file, name, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    ("atomic", "create installs with the replacing rename (flag)", "    return install(dir, name, temp, data, true);", "    return install(dir, name, temp, data, false);"),
    ("atomic", "the fresh install's rename is dir_rename", "        match dir_rename_new(dir, temp, name) {", "        match dir_rename(dir, temp, name) {"),
    ("atomic", "replace installs with the no-replace rename", "    return install(dir, name, temp, data, false);\n}\n\n// The same for a name that must not exist", "    return install(dir, name, temp, data, true);\n}\n\n// The same for a name that must not exist"),
    ("atomic", "the temporary is left after a refused rename", "    if renamed != 0 {\n        dir_remove(dir, temp);\n        return (renamed, 4);", "    if renamed != 0 {\n        return (renamed, 4);"),
    ("atomic", "a failed rename answers success", "    if renamed != 0 {\n        dir_remove(dir, temp);\n        return (renamed, 4);", "    if renamed != 0 {\n        dir_remove(dir, temp);\n        return (0, 0);"),
    ("atomic", "errno 95 is not 'unsupported'", "    return errno == 95 || errno == 45;", "    return errno == 99 || errno == 45;"),
    ("atomic", "errno 45 is not 'unsupported'", "    return errno == 95 || errno == 45;", "    return errno == 95 || errno == 99;"),
    ("write", "--create does not use the no-replace install", "            if creating {\n                let (errno2, step2) = atomic.create(", "            if false && creating {\n                let (errno2, step2) = atomic.create("),
    ("write", "the kernel's EEXIST is not the conflict", "            } else if creating && step == 4 && failed == 17 {", "            } else if creating && step == 4 && failed == 99 {"),
    ("write", "a creator's identical content is not recognised as applied", "                    same = there_errno == 0 && bytes.equal(buffer.bytes(tb), after);", "                    same = false;"),
    ("write", "any content a creator made is taken for the applied one", "                    same = there_errno == 0 && bytes.equal(buffer.bytes(tb), after);", "                    same = true;"),
    ("write", "an unsupported filesystem is not mapped", "            } else if creating && step == 4 && atomic.rename_unsupported(failed) {", "            } else if creating && step == 4 && false {"),
    ("write", "the unsupported refusal drops the errno", "                w = json.put_key(heap, w, \"errno\");\n                w = json.put_int(heap, w, failed);\n                e = fail.add(heap, e, w);\n            } else {", "                e = fail.add(heap, e, w);\n            } else {"),
    ("rules", "the unsupported refusal has the wrong exit code", "io.rename-unsupported|8|never", "io.rename-unsupported|1|never"),
]

originals = {k: p.read_text() for k, p in FILES.items()}


def restore(*_):
    for k, p in FILES.items():
        p.write_text(originals[k])


def build_and_test():
    b = subprocess.run([COMPILER, "build"], cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "does not build", b.stderr.strip()[:140]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_write_create", "test_rules", "test_mutation", "test_move"],
                       cwd=ROOT / "tests" / "conformance", capture_output=True, text=True, timeout=1800)
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
    for key, name, old, new in chosen:
        if originals[key].count(old) != 1:
            print("!! %s: the site occurs %d times" % (name, originals[key].count(old)))
            return 1
        try:
            FILES[key].write_text(originals[key].replace(old, new, 1))
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-9s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict != "killed":
            survivors.append(name)
    subprocess.run([COMPILER, "build"], cwd=ROOT, capture_output=True)  # leave build/ as the sources are
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
