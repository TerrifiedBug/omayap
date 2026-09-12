"""What a path is allowed to be before omayap writes through it.

Every case here is a redirect: a symlink where a directory should be, a
symlink where a file should be, a directory somebody else can write in. The
answer is always the same, which is why it is worth pinning: refuse, or write
inside the descriptor we already checked.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omayap import safeio  # noqa: E402


class Walking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_symlinked_directory_is_refused(self):
        (self.root / "real").mkdir()
        (self.root / "link").symlink_to(self.root / "real")
        with self.assertRaises(safeio.Unsafe):
            safeio.open_dir(self.root / "link")

    def test_a_symlink_anywhere_above_is_refused(self):
        (self.root / "real").mkdir()
        (self.root / "real" / "inside").mkdir()
        (self.root / "link").symlink_to(self.root / "real")
        with self.assertRaises(safeio.Unsafe):
            safeio.open_dir(self.root / "link" / "inside")

    def test_a_directory_others_can_write_in_is_refused(self):
        wide = self.root / "wide"
        wide.mkdir(mode=0o777)
        os.chmod(wide, 0o777)
        with self.assertRaises(safeio.Unsafe):
            safeio.open_dir(wide)

    def test_our_own_directory_is_tightened_rather_than_refused(self):
        loose = self.root / "loose"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        with safeio.open_dir(loose):
            pass
        self.assertEqual(loose.stat().st_mode & 0o777, 0o700)

    def test_a_missing_directory_is_created_private(self):
        with safeio.open_dir(self.root / "new" / "deeper", create=True) as handle:
            self.assertTrue(handle.is_dir.__self__ is handle)
        self.assertEqual((self.root / "new").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "new" / "deeper").stat().st_mode & 0o777, 0o700)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = safeio.open_dir(self.root)
        self.addCleanup(self.dir.close)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_write_replaces_a_symlink_instead_of_following_it(self):
        target = self.root / "elsewhere"
        target.write_text("untouched", encoding="utf-8")
        (self.root / "state").symlink_to(target)
        self.dir.write("state", "ours")
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")
        self.assertFalse((self.root / "state").is_symlink())
        self.assertEqual((self.root / "state").read_text(encoding="utf-8"), "ours")

    def test_reading_through_a_symlink_fails(self):
        (self.root / "real").write_text("secret", encoding="utf-8")
        (self.root / "config.json").symlink_to(self.root / "real")
        with self.assertRaises(OSError):
            self.dir.read_text("config.json")

    def test_written_files_are_private(self):
        self.dir.write("meta.json", "{}")
        self.assertEqual((self.root / "meta.json").stat().st_mode & 0o777, 0o600)

    def test_rewrite_keeps_the_inode_a_watcher_is_holding(self):
        self.dir.rewrite("state", "first\n")
        before = (self.root / "state").stat().st_ino
        self.dir.rewrite("state", "second\n")
        after = (self.root / "state").stat()
        self.assertEqual(before, after.st_ino, "the QML side watches this inode")
        self.assertEqual((self.root / "state").read_text(encoding="utf-8"), "second\n")

    def test_a_failed_write_leaves_no_temporary_behind(self):
        with self.assertRaises(TypeError):
            self.dir.write("meta.json", 17)
        self.assertEqual(sorted(self.dir.names()), [])

    def test_names_have_to_be_names(self):
        for bad in ("../escape", "sub/file", "", "."):
            with self.assertRaises(ValueError):
                self.dir.open(bad, os.O_RDONLY)


class Adopting(unittest.TestCase):
    def test_a_descriptor_that_is_not_a_directory_is_refused(self):
        handle = tempfile.NamedTemporaryFile()
        self.addCleanup(handle.close)
        with self.assertRaises(safeio.Unsafe):
            safeio.adopt(handle.fileno(), handle.name)

    def test_an_inherited_directory_is_used_as_it_is(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fd = os.open(tmp.name, os.O_RDONLY | os.O_DIRECTORY)
        with safeio.adopt(fd, tmp.name) as handle:
            handle.write("meta.json", "{}")
        self.assertEqual(
            (Path(tmp.name) / "meta.json").read_text(encoding="utf-8"), "{}"
        )


if __name__ == "__main__":
    unittest.main()
