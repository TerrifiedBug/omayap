"""The settings, and the three places that have to agree about them.

The daemon's defaults, the template `setup.sh` copies, and the defaults the
panel renders before it has read anything are separate declarations. A key
added to one and forgotten in another is a setting that works until someone
looks at the panel, so it is worth one test.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from omayap import config  # noqa: E402


class TestDeclarationsAgree(unittest.TestCase):
    def test_the_template_has_every_default(self):
        template = json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(template), sorted(config.DEFAULTS))
        for key, value in config.DEFAULTS.items():
            self.assertEqual(template[key], value, key)

    def test_the_panel_defaults_match(self):
        source = (ROOT / "Model.js").read_text(encoding="utf-8")
        block = re.search(r"var DEFAULT_CONFIG = \{(.*?)\}", source, re.S).group(1)
        keys = set(re.findall(r"^\s*([a-z_]+):", block, re.M))
        self.assertEqual(keys, set(config.DEFAULTS))


class TestReadingAndWriting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.original = (config.CONFIG_FILE, config.CONFIG_DIR)
        config.CONFIG_FILE = self.path
        config.CONFIG_DIR = self.path.parent

    def tearDown(self):
        config.CONFIG_FILE, config.CONFIG_DIR = self.original
        self.tmp.cleanup()

    def test_a_missing_file_is_the_defaults(self):
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_unknown_keys_survive_a_write(self):
        self.path.write_text('{"recordings_dir": "/tmp/r", "mystery": 7}', encoding="utf-8")
        self.assertTrue(config.set("keep_audio", False))
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["mystery"], 7)
        self.assertEqual(written["recordings_dir"], "/tmp/r")
        self.assertIs(written["keep_audio"], False)

    def test_a_broken_file_is_never_written_over(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertFalse(config.set("keep_audio", False))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_cli_strings_become_booleans(self):
        self.assertIs(config.coerce("true"), True)
        self.assertIs(config.coerce("false"), False)
        self.assertEqual(config.coerce("~/Recordings"), "~/Recordings")


if __name__ == "__main__":
    unittest.main()
