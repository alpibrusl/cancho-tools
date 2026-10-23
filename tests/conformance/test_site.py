"""The project site (scripts/site.py): the front page is generated from the built tools, and what it shows is theirs.

Checks that nothing in the page is a placeholder, that every local link and asset exists, that the session the page
replays is the one the tools print (the stale write is refused with the rule, exit 5, and nothing is lost), and that the
page claims no more than the tools' own authority data shows.
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

from harness import BIN, ROOT

SITE_PY = ROOT / "scripts" / "site.py"


class Site(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = pathlib.Path(cls.tmp.name) / "site"
        r = subprocess.run([sys.executable, str(SITE_PY), "--build", str(BIN), "--out", str(cls.out)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.index = (cls.out / "index.html").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_nothing_is_left_to_fill_in(self):
        self.assertNotIn("{{", self.index)
        self.assertNotIn("}}", re.sub(r"<script>window\.CFLOW = .*?;</script>", "", self.index, flags=re.S))

    def test_every_local_link_and_asset_exists(self):
        for name in ("index.html", "docs.html"):
            page = (self.out / name).read_text()
            for target in re.findall(r'(?:href|src)="([^"#]+)(?:#[^"]*)?"', page):
                if re.match(r"^[a-z]+:", target) or target.startswith("//"):
                    continue
                self.assertTrue((self.out / target).exists(), f"{name} links {target}, which the site does not have")

    def test_the_replayed_session_is_what_the_tools_print(self):
        m = re.search(r"window\.CFLOW = (.*?);</script>", self.index, flags=re.S)
        flow = json.loads(m.group(1).replace("<\\/", "</"))
        plain, guarded = flow["plain"]["items"], flow["guarded"]["items"]
        # Plain writes lose a line and say so; guarded writes refuse the stale one with the rule, and keep both.
        self.assertEqual(plain[-1]["s"]["verdict"][1], "bad")
        self.assertEqual(guarded[-1]["s"]["verdict"][1], "ok")
        text = "\n".join(it["l"][1] for it in guarded)
        self.assertIn("precondition.hash-mismatch", text)
        self.assertIn("# exit status 5", text)
        self.assertIn("- milk (agent A)", [x[0] for x in guarded[-1]["s"]["file"]])
        self.assertIn("- bread (agent B)", [x[0] for x in guarded[-1]["s"]["file"]])
        for it in plain + guarded:
            self.assertIn(it["l"][0], "dgbaw")

    def test_the_page_claims_what_the_authority_data_shows(self):
        for tool in ("seek", "write", "run"):
            d = json.loads(subprocess.run([str(BIN / tool), "introspect"], capture_output=True, text=True, check=True).stdout)
            names = {x["name"] for x in d["authority"]["labels"]}
            self.assertEqual(any(n.startswith(("net", "tcp", "udp", "sock")) for n in names), False)
        # Only run starts programs or reads the clock, as the page says.
        for tool in ("seek", "list", "peek", "jsonq", "tally", "hash", "write", "replace", "move", "archive"):
            d = json.loads(subprocess.run([str(BIN / tool), "introspect"], capture_output=True, text=True, check=True).stdout)
            names = {x["name"] for x in d["authority"]["labels"]}
            self.assertFalse(names & {"exec", "clock"}, tool)
        self.assertIn("None of them can reach the network", self.index)

    def test_every_tool_has_a_page_and_a_card(self):
        for tool in ("seek", "list", "peek", "jsonq", "tally", "hash", "write", "replace", "move", "archive", "run"):
            self.assertTrue((self.out / f"{tool}.html").exists(), tool)
            self.assertIn(f'href="{tool}.html"', self.index, tool)

    def test_the_text_of_the_front_page_is_short(self):
        # The point of the page: a person scans it. Strip tags, scripts and styles, and count what is left.
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", self.index, flags=re.S)
        words = len(re.sub(r"<[^>]+>", " ", body).split())
        self.assertLess(words, 1500, f"the front page has {words} words")


if __name__ == "__main__":
    unittest.main()
