#!/usr/bin/env python3
"""Build a tool with its root baked in (cancho docs/agent-toolbox.md D14).

    python3 scripts/variant.py --tool seek --root /srv/work [--out DIR]

`narrow` takes a literal and nothing is generic over an `Fs` prefix (L5), so
the type-level proof of a tool's extent is a source substitution: every
`Fs("")`, `fs_read("")` and `fs_write("")` becomes the baked directory, main
narrows its `Fs` to it, and --root is fixed to it (path.baked). The variant's
manifest is derived like any other (D12): its authority reads
fs_read("/srv/work"), and that is what its `introspect` prints.

What it does not change: the variant is built from the same sources, so its
behaviour is the tool's -- including `toolbox.place`, which opens every path
beneath the root following no link. A writer's row names the directory once,
as fs_read, and writes by handle (dir_write), so no fs_write appears.

The design's recommendation is followed: the transform is built and used
in the tests (tests/conformance/test_variant.py); no variant is shipped.
"""

import argparse
import json
import pathlib
import posixpath
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import manifest  # noqa: E402

ROOT = manifest.ROOT


def transform(text, root, is_main):
    out = text.replace('Fs("")', 'Fs("%s")' % root)
    out = out.replace('fs_read("")', 'fs_read("%s")' % root).replace('fs_write("")', 'fs_write("%s")' % root)
    if is_main:
        out = re.sub(r'path\.root\(heap, args, (cli\.text\(args, parsed, table, "root"\)), (cli\.value_index\(parsed, table, "root"\)), e\)',
                     r'path.baked(heap, args, "%s", \1, \2, e)' % root, out)
        if "path.baked(" not in out:
            sys.exit("variant: the tool does not resolve --root through path.root; nothing to bake")
        # main: narrow the capability once, and lend the narrowed one.
        main_at = out.index("fn main(world: World)")
        head, body = out[:main_at], out[main_at:]
        body = body.replace("    var status = 0;\n", '    let confined = narrow(fs, "%s");\n    var status = 0;\n' % root, 1)
        body = body.replace("            borrow fs as &f in {", "            borrow confined as &f in {", 1)
        body = body.replace("    release(fs);", "    release(confined);", 1)
        if body.count("confined") != 3:
            sys.exit("variant: main does not have the shape the transform expects")
        out = head + body
    return out


def build(tool, root, out_dir):
    if not root.startswith("/") or posixpath.normpath(root) != root or '"' in root or "\\" in root or root == "/":
        sys.exit("variant: --root must be an absolute, normalised directory other than /")
    proj = manifest.project()
    entry = next(b for b in proj["bin"] if b["name"] == tool)
    src = pathlib.Path(out_dir) / "src"
    src.mkdir(parents=True, exist_ok=True)
    files = []
    for f in manifest.sources(entry):
        p = pathlib.Path(f)
        is_main = p.parent.name == tool and p.parent.parent.name == "tools"
        target = src / ("%s__%s" % (p.parent.name, p.name))
        target.write_text(transform(p.read_text(), root, is_main))
        files.append(str(target))
    # The variant's own manifest, derived and embedded, with the fixed point
    # checked, exactly as for the shipped tools.
    first = manifest.derive(files)
    schema = json.loads((ROOT / "schemas" / ("%s.v1.json" % tool)).read_text())
    built = next(f for f in files if f.endswith("__built.cho"))
    pathlib.Path(built).write_text(manifest.generated(first, schema, proj["package"]["cancho"]))
    if manifest.derive(files) != first:
        sys.exit("variant: no fixed point")
    binary = pathlib.Path(out_dir) / tool
    subprocess.run([manifest.compiler(), "build", *files, "--std", "-o", str(binary)], check=True, capture_output=True)
    return binary, first


def main():
    p = argparse.ArgumentParser(description="build a tool with its root baked in (D14)")
    p.add_argument("--tool", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--out")
    args = p.parse_args()
    out = args.out or tempfile.mkdtemp(prefix="variant-")
    binary, authority = build(args.tool, args.root, out)
    print(json.dumps({"binary": str(binary), "authority": authority["labels"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
