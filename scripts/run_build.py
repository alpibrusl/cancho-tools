#!/usr/bin/env python3
"""Build `run` for one deployment: the programs directory baked in.

    python3 scripts/run_build.py --programs /opt/agent/programs --out build/run-agent

`run` starts only programs found in one directory, and that directory is the
allow-list. `narrow` takes a literal (cancho docs/processes.md section 7.1), so
the directory is in the source: this script replaces it everywhere it appears
(`programs()`, `main`'s `narrow`, and every `Exec(...)` and `exec(...)` in the
signatures) in a scratch copy and builds that, the way scripts/variant.py bakes
a root (D14) and scripts/mcp.py bakes the server's directory. The variant's
authority is derived like any other: its row says `exec("<dir>")`, which is what
a supervisor reads before granting it.

The committed source says DEFAULT, which is what `cancho build` builds and what
the manifest records; tools.toml's `[run]` ceiling names it.
"""

import argparse
import pathlib
import posixpath
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import manifest  # noqa: E402

ROOT = manifest.ROOT
DEFAULT = "/opt/cancho-tools/programs"


def valid(directory):
    return (directory.startswith("/") and posixpath.normpath(directory) == directory
            and directory != "/" and '"' not in directory and "\\" not in directory
            and "\n" not in directory)


def baked(text, directory):
    if DEFAULT not in text:
        sys.exit("run_build: the source does not name %s; nothing to bake" % DEFAULT)
    return text.replace(DEFAULT, directory)


def build(programs, out, build_dir=None):
    """Build `run` into `out`; the compiler is $CANCHO or `cancho`."""
    if not valid(programs):
        sys.exit("run_build: --programs must be an absolute, normalised directory other than /")
    proj = manifest.project()
    entry = next(b for b in proj["bin"] if b["name"] == "run")
    with tempfile.TemporaryDirectory() as scratch:
        src = pathlib.Path(scratch)
        files = []
        for f in manifest.sources(entry):
            p = pathlib.Path(f)
            target = src / ("%s__%s" % (p.parent.name, p.name))
            target.write_text(baked(p.read_text(), programs) if p.name == "run.cho" else p.read_text())
            files.append(str(target))
        # The variant's authority is its own (its `exec("<dir>")` names the baked
        # directory), so the embedded manifest is derived again, as manifest.py does.
        import json
        schema = json.loads((ROOT / "schemas" / "run.v1.json").read_text())
        built = next(f for f in files if f.endswith("built.cho"))
        authority = manifest.derive(files)
        pathlib.Path(built).write_text(manifest.generated(authority, schema, proj["package"]["cancho"]))
        if manifest.derive(files) != authority:
            sys.exit("run_build: no fixed point -- embedding the manifest changed the authority")
        r = subprocess.run([manifest.compiler(), "build", *files, "--std", "-o", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("run_build: the compiler refused:\n" + r.stdout + r.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--programs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.programs, a.out)
    print("built %s (programs: %s)" % (a.out, a.programs))


if __name__ == "__main__":
    main()
