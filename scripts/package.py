#!/usr/bin/env python3
"""`contract/` as a package: the store a consumer pins (docs/next-tools.md §5).

`cancho vcs publish --std --dir contract` writes one store per module, each
under `.cancho-vcs/<module>`, and the result is committed. A repository that
builds on this contract names the modules it imports:

    [dependencies.cli]
    git = "https://github.com/alpibrusl/cancho-tools"
    rev = "<a full commit hash>"
    path = ".cancho-vcs/toolbox.cli"

The store is generated, not edited, and publishing is deterministic (the same
sources write the same bytes), so the committed store can be checked against
the sources. `toolbox.built` is not here: each tool generates its own.

    python3 scripts/package.py           # regenerate the store
    python3 scripts/package.py --check   # change nothing; exit 1 on drift

The compiler is $CANCHO, or `cancho` on PATH.
"""

import filecmp
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE = ROOT / ".cancho-vcs"


def publish(into):
    out = subprocess.run([os.environ.get("CANCHO", "cancho"), "vcs", "publish", "--std", "--store", str(into),
                          "--dir", str(ROOT / "contract")], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("package: publish refused:\n" + out.stdout + out.stderr)


def same(a, b):
    """Whether two directory trees hold the same files with the same bytes."""
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(same(pathlib.Path(a) / d, pathlib.Path(b) / d) for d in cmp.common_dirs)


def main():
    check = "--check" in sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        fresh = pathlib.Path(tmp) / "store"
        publish(fresh)
        if check:
            if not STORE.is_dir() or not same(fresh, STORE):
                sys.exit("package: .cancho-vcs is not what `vcs publish --dir contract` writes; run scripts/package.py")
            print("package: .cancho-vcs matches contract/")
            return
        if STORE.exists():
            shutil.rmtree(STORE)
        shutil.copytree(fresh, STORE)
        print("package: wrote %s (%d modules)" % (STORE.relative_to(ROOT), len(list(STORE.iterdir()))))


if __name__ == "__main__":
    main()
