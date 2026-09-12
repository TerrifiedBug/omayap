"""The rules every child process is held to.

Each of these is a way the daemon could hang or leak: a program that never
stops talking, one that never exits, one that exits and leaves its children
behind. They are the things a review asked for, so they are the things worth a
test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omayap import proc  # noqa: E402

# Ordinary coreutils, borrowed for the duration of a test. proc only runs
# programs that are in its table, which is the point of the table.
EXTRAS = {
    "sleep": ("/usr/bin/sleep",),
    "cat": ("/usr/bin/cat",),
    "yes": ("/usr/bin/yes",),
    "bash": ("/usr/bin/bash",),
}


def group_gone(pid: int) -> bool:
    for _ in range(100):
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


class ToolTable(unittest.TestCase):
    def test_only_the_listed_programs_can_be_run(self):
        with self.assertRaises(proc.ToolError):
            proc.tool("rm")
        with self.assertRaises(proc.ToolError):
            proc.tool("/usr/bin/wtype")

    def test_a_listed_program_resolves_to_its_absolute_path(self):
        if not os.path.exists("/usr/bin/pactl"):
            self.skipTest("pactl is not installed")
        self.assertEqual(proc.tool("pactl"), "/usr/bin/pactl")

    def test_a_program_we_could_rewrite_ourselves_is_refused(self):
        mine = Path(self.enterContext(_tempdir())) / "pw-dump"
        mine.write_text("#!/bin/sh\n", encoding="utf-8")
        mine.chmod(0o755)
        with _tools({"pw-dump": (str(mine),)}):
            with self.assertRaises(proc.ToolError) as caught:
                proc.tool("pw-dump")
        self.assertIn("owned by uid", str(caught.exception))

    def test_the_interpreter_is_the_path_it_was_started_as(self):
        # Returning the resolved target is what made every transcription fail
        # in silence: a venv's bin/python links to the system interpreter, and
        # Python looks for pyvenv.cfg beside the binary it was started as, so
        # the link's target is a Python with none of the venv's packages.
        link = Path(self.enterContext(_tempdir())) / "python"
        link.symlink_to(sys.executable)
        original = proc.sys.executable
        proc.sys.executable = str(link)
        self.addCleanup(setattr, proc.sys, "executable", original)
        self.assertEqual(proc.interpreter(), str(link))


class ClosedEnvironment(unittest.TestCase):
    def test_nothing_is_inherited_that_was_not_asked_for(self):
        os.environ["OMAYAP_TEST_LEAK"] = "1"
        self.addCleanup(os.environ.pop, "OMAYAP_TEST_LEAK", None)
        env = proc.environ()
        self.assertNotIn("OMAYAP_TEST_LEAK", env)
        self.assertEqual(env["PATH"], proc.SAFE_PATH)

    def test_a_name_can_be_added_or_dropped(self):
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = "abc"
        self.addCleanup(os.environ.pop, "HYPRLAND_INSTANCE_SIGNATURE", None)
        self.assertEqual(proc.environ()["HYPRLAND_INSTANCE_SIGNATURE"], "abc")
        self.assertNotIn(
            "HYPRLAND_INSTANCE_SIGNATURE",
            proc.environ(HYPRLAND_INSTANCE_SIGNATURE=None),
        )
        self.assertEqual(proc.environ(PYTHONPATH="/x")["PYTHONPATH"], "/x")


class Deadlines(unittest.TestCase):
    def setUp(self):
        self.tools = _tools(EXTRAS)
        self.tools.__enter__()
        self.addCleanup(self.tools.__exit__, None, None, None)

    def test_a_child_that_will_not_exit_is_killed_with_its_group(self):
        started = time.monotonic()
        done = proc.run("sleep", ["30"], timeout=0.3)
        self.assertTrue(done.expired)
        self.assertLess(time.monotonic() - started, 5.0)

    def test_output_past_the_ceiling_ends_the_child(self):
        done = proc.run("yes", ["chatter"], timeout=5.0, limit=4096)
        self.assertTrue(done.expired, "an endless talker has to be cut off")
        self.assertLessEqual(len(done.out), 4096 + (1 << 16))

    def test_input_reaches_the_child_and_output_comes_back(self):
        done = proc.run("cat", timeout=5.0, stdin=b"dictated words")
        self.assertFalse(done.expired)
        self.assertEqual(done.code, 0)
        self.assertEqual(done.out, b"dictated words")

    def test_stopping_takes_the_children_of_a_child_with_it(self):
        # The leader exits immediately and leaves a sleep behind in its group,
        # which is exactly how a wrapper script loses a microphone.
        child = proc.spawn("bash", ["-c", "sleep 30 & exit 0"])
        self.addCleanup(child.stdout.close)
        time.sleep(0.2)
        proc.stop(child, grace=0.5)
        self.assertTrue(group_gone(child.pid), "the whole group has to go")

    def test_a_background_child_is_killed_once_it_is_overdue(self):
        child = proc.background("sleep", ["30"], deadline=0.05)
        time.sleep(0.1)
        self.assertEqual(proc.reap(), 1)
        self.assertTrue(group_gone(child.pid))
        self.assertEqual(proc.reap(), 0, "nothing is left to collect")

    def test_a_background_child_that_finishes_is_collected_not_killed(self):
        child = proc.background("sleep", ["0.05"], deadline=30.0)
        time.sleep(0.2)
        self.assertEqual(proc.reap(), 0)
        self.assertEqual(child.poll(), 0)


class _tools:
    """Lend proc's table a few extra programs for one test."""

    def __init__(self, extra: dict) -> None:
        self.extra = extra
        self.original = None

    def __enter__(self):
        self.original = dict(proc.TOOLS)
        proc.TOOLS.update(self.extra)
        return proc.TOOLS

    def __exit__(self, *_):
        proc.TOOLS.clear()
        proc.TOOLS.update(self.original)


def _tempdir():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
