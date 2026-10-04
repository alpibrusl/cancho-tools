"""M6 -- the authority a tool reports is the compiler's, within its ceiling,
and every label it holds has a row in the lex-os bridge table.

scripts/manifest.py --check does D12's three checks (the committed record
equals a fresh derivation; the binary prints it; it is within tools.toml).
The bridge itself (D13) belongs in lex-os (alpibrusl/lex-os#122); what this
repository can check is that the table the bridge must implement is total
for the labels these tools hold, so no tool depends on a label the bridge
would have to refuse or, worse, map to nothing.
"""

import os
import shutil
import subprocess
import sys
import unittest

from harness import ROOT, TOOLS, introspect

# lex-sys docs/agent-toolbox.md D13: each label and what the bridge does
# with it. `refuse` labels must never appear in a tool.
BRIDGE = {
    "fs_read": "fs_read with the argument as scope",
    "fs_write": "fs_write with the argument as scope",
    "net_out": "net with the host as scope",
    "file_read": "dropped: spent at open; fs_read is in the report",
    "file_write": "dropped: spent at open; fs_write is in the report",
    # lex-sys docs/directory-handles.md: a handle label names no path. A
    # directory is opened under an fs_read scope, and everything a Dir
    # reaches is beneath one, so the scope is the fs_read in the report.
    "dir_read": "dropped: spent at open_dir; fs_read is in the report",
    "dir_write": "fs_write over the scope of the fs_read in the report: a Dir writes only beneath what it opened",
    "conn_read": "dropped",
    "conn_write": "dropped",
    "io_read": "off_lattice, reviewed",
    "io_write": "off_lattice, reviewed",
    "err_write": "off_lattice, reviewed",
    "heap": "off_lattice, reviewed",
    "args": "off_lattice, reviewed",
}
REFUSED = {"ffi", "net_in"}

# What each tool must provably not do: the property the epic sells
# (a read-only tool cannot write).
READ_ONLY = {"seek", "peek", "jsonq", "tally", "hash", "list"}


class Authority(unittest.TestCase):
    def test_manifest_check_passes(self):
        compiler = os.environ.get("LEX_SYS", "lex-sys")
        self.assertTrue(shutil.which(compiler), "M6 needs the compiler: put lex-sys on PATH or set LEX_SYS")
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_bridge_table_is_total_for_every_label_held(self):
        for tool in TOOLS:
            for label in introspect(tool)["authority"]["labels"]:
                self.assertNotIn(label["name"], REFUSED, tool)
                self.assertIn(label["name"], BRIDGE, "%s holds %s, which D13's table does not map" % (tool, label["name"]))

    def test_read_only_tools_hold_no_write(self):
        for tool in READ_ONLY:
            names = {l["name"] for l in introspect(tool)["authority"]["labels"]}
            self.assertFalse(names & {"fs_write", "dir_write", "file_write", "net_out", "ffi"}, (tool, names))
            self.assertTrue(introspect(tool)["authority"]["bounded"])

    def test_no_tool_reads_the_clock_or_the_network(self):
        for tool in TOOLS:
            names = {l["name"] for l in introspect(tool)["authority"]["labels"]}
            self.assertFalse(names & {"clock", "net_out", "net_in", "ffi"}, (tool, names))


if __name__ == "__main__":
    unittest.main()
