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

from harness import ADDED, EVERY, ROOT, TOOLS, introspect

# cancho docs/agent-toolbox.md D13: each label and what the bridge does
# with it. `refuse` labels must never appear in a tool.
BRIDGE = {
    "fs_read": "fs_read with the argument as scope",
    "fs_write": "fs_write with the argument as scope",
    "net_out": "net with the host as scope",
    "file_read": "dropped: spent at open; fs_read is in the report",
    "file_write": "dropped: spent at open; fs_write is in the report",
    # cancho docs/directory-handles.md: a handle label names no path. A
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
    # `run` alone (docs/next-tools.md section 3). D13's table does not yet map these; the rows are this
    # repository's proposal for the bridge: a program the directory names, the wait on it, and its pipes.
    "exec": "exec with the programs directory as scope (proposed: a spawn grant for that directory)",
    "poll": "off_lattice, reviewed: the wait on the child's pipes and exit",
    "pipe_read": "dropped: the child's channel, spent at spawn",
    "pipe_write": "dropped: the child's channel, spent at spawn",
    "child_signal": "off_lattice, reviewed: the kill at the deadline, of the child this tool started",
    "clock": "off_lattice, reviewed: run's deadline, the one recorded exception (tools.toml)",
}
REFUSED = {"ffi", "net_in"}

# What each tool must provably not do: the property the epic sells
# (a read-only tool cannot write).
READ_ONLY = {"seek", "peek", "jsonq", "tally", "hash", "list"}


class Authority(unittest.TestCase):
    def test_manifest_check_passes(self):
        compiler = os.environ.get("CANCHO", "cancho")
        self.assertTrue(shutil.which(compiler), "M6 needs the compiler: put cancho on PATH or set CANCHO")
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_bridge_table_is_total_for_every_label_held(self):
        for tool in EVERY:
            for label in introspect(tool)["authority"]["labels"]:
                self.assertNotIn(label["name"], REFUSED, tool)
                self.assertIn(label["name"], BRIDGE, "%s holds %s, which D13's table does not map" % (tool, label["name"]))

    def test_read_only_tools_hold_no_write(self):
        for tool in READ_ONLY:
            names = {l["name"] for l in introspect(tool)["authority"]["labels"]}
            self.assertFalse(names & {"fs_write", "dir_write", "file_write", "net_out", "ffi"}, (tool, names))
            self.assertTrue(introspect(tool)["authority"]["bounded"])

    def test_no_tool_reads_the_clock_or_the_network(self):
        for tool in TOOLS + ["archive"]:
            names = {l["name"] for l in introspect(tool)["authority"]["labels"]}
            self.assertFalse(names & {"clock", "net_out", "net_in", "ffi"}, (tool, names))

    def test_run_alone_starts_programs_and_holds_a_clock_and_nothing_reaches_the_network(self):
        for tool in EVERY:
            names = {l["name"] for l in introspect(tool)["authority"]["labels"]}
            self.assertFalse(names & {"net_out", "net_in", "ffi"}, (tool, names))
            if tool == "run":
                self.assertTrue(names >= {"exec", "clock", "poll"}, names)
            else:
                self.assertFalse(names & {"exec", "exec_under", "clock", "poll", "child_signal", "pipe_read", "pipe_write"}, (tool, names))

    def test_the_tools_added_since_hold_what_their_design_says(self):
        archive = {l["name"] for l in introspect("archive")["authority"]["labels"]}
        write = {l["name"] for l in introspect("write")["authority"]["labels"]}
        # docs/archive.md section 6: `write`'s row, less the standard input it does not read.
        self.assertEqual(archive, write - {"io_read"})
        self.assertEqual(sorted(ADDED), ["archive", "run"])


if __name__ == "__main__":
    unittest.main()
