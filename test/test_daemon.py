"""Daemon behaviour that only shows up at runtime.

The daemon owns one selectors loop, so anything it leaves registered runs
forever. These tests build a Daemon without touching a model or a real
capture: the loop's callbacks are ordinary methods over a fake pw-record.
"""

from __future__ import annotations

import os
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omayap import MIN_SAMPLES, daemon, safeio  # noqa: E402


class FakeCapture:
    """A pipe with a writable end we close to simulate pw-record exiting."""

    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.write_fd = write_fd
        self.terminated = False
        self.exit_code = None

    def poll(self):
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def wait(self, timeout=None):
        return self.exit_code

    def kill(self) -> None:
        self.exit_code = -9

    def feed(self, payload: bytes) -> None:
        os.write(self.write_fd, payload)

    def die(self) -> None:
        os.close(self.write_fd)
        self.exit_code = 1


class DictationCaptureLoss(unittest.TestCase):
    def setUp(self):
        self.notes: list = []
        self.warnings: list = []
        self.original = (daemon.notify, daemon.warn)
        daemon.notify = lambda *args, **kw: self.notes.append(args)
        daemon.warn = lambda message: self.warnings.append(message)

        self.daemon = object.__new__(daemon.Daemon)
        self.daemon.selector = selectors.DefaultSelector()
        self.daemon.model = "ready"
        self.daemon.dictation = "listening"
        self.daemon.buffer = bytearray()
        self.daemon.levels = None
        self.daemon.level_users = set()
        self.daemon.level_at = 0.0
        self.daemon.engine = None
        self.daemon.session = None
        self.daemon.pending = None
        self.daemon.child = None
        self.daemon.child_dir = None
        # write_state would need a runtime dir; the transitions are what
        # matter here.
        self.daemon.write_state = lambda: None

        self.capture = FakeCapture()
        self.daemon.mic = self.capture
        self.daemon.watch(self.capture, self.daemon.on_dictation_audio)

    def tearDown(self):
        daemon.notify, daemon.warn = self.original
        self.daemon.selector.close()
        try:
            self.capture.stdout.close()
        except OSError:
            pass
        if self.capture.exit_code is None:
            os.close(self.capture.write_fd)

    def registered(self) -> int:
        return len(self.daemon.selector.get_map() or {})

    def test_a_nonblocking_read_with_nothing_ready_keeps_listening(self):
        self.daemon.on_dictation_audio()
        self.assertEqual(self.daemon.dictation, "listening")
        self.assertEqual(self.registered(), 1)
        self.assertIs(self.daemon.mic, self.capture)

    def test_audio_accumulates(self):
        self.capture.feed(b"\x00\x00\x00\x00" * 100)
        self.daemon.on_dictation_audio()
        self.assertEqual(len(self.daemon.buffer), 400)
        self.assertEqual(self.daemon.dictation, "listening")

    def test_a_capture_that_exits_is_cleaned_up_rather_than_spun_on(self):
        # An fd at EOF stays readable forever, so a handler that returns
        # without unregistering pins the loop at 100% CPU until release.
        self.capture.die()
        self.daemon.on_dictation_audio()
        self.assertEqual(self.registered(), 0, "the dead fd must leave the selector")
        self.assertIsNone(self.daemon.mic)
        self.assertEqual(self.daemon.dictation, "idle")
        self.assertTrue(self.notes, "losing the microphone mid-press has to say so")

    def test_speech_captured_before_the_capture_died_is_still_transcribed(self):
        spoken = []
        self.daemon.engine = type("Engine", (), {"text": lambda _self, s: "hello"})()
        self.daemon.deliver = lambda text: spoken.append(text) or "typed"
        self.capture.feed(b"\x00\x00\x00\x00" * (MIN_SAMPLES + 10))
        self.daemon.on_dictation_audio()
        self.capture.die()
        self.daemon.on_dictation_audio()
        self.assertEqual(spoken, ["hello"])
        self.assertEqual(self.daemon.dictation, "idle")
        self.assertEqual(self.registered(), 0)


class ControlPipe(unittest.TestCase):
    """How a key press reaches the daemon: a word on a pipe it owns."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = safeio.open_dir(self.tmp.name)
        self.addCleanup(self.run.close)
        self.warnings: list = []
        self.original = daemon.warn
        daemon.warn = lambda message: self.warnings.append(message)
        self.addCleanup(self.restore)

        self.daemon = object.__new__(daemon.Daemon)
        self.daemon.run = self.run
        self.daemon.selector = selectors.DefaultSelector()
        self.addCleanup(self.daemon.selector.close)
        self.daemon.control_fd = None
        self.daemon.control_buffer = b""
        self.done: list = []
        for name in ("press", "release", "toggle_dictation", "toggle_recording"):
            setattr(self.daemon, name, lambda name=name: self.done.append(name))
        self.daemon.open_control()
        self.addCleanup(os.close, self.daemon.control_fd)

    def restore(self):
        daemon.warn = self.original

    def write(self, text: str) -> None:
        fd = os.open(os.path.join(self.tmp.name, "control"), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)
        self.daemon.on_control()

    def test_the_pipe_is_private_to_this_user(self):
        info = self.run.stat("control")
        self.assertTrue(stat.S_ISFIFO(info.st_mode))
        self.assertEqual(info.st_mode & 0o777, 0o600)

    def test_every_command_reaches_its_handler(self):
        self.write("press\nrelease\ntoggle\nrecord\n")
        self.assertEqual(
            self.done, ["press", "release", "toggle_dictation", "toggle_recording"]
        )

    def test_a_command_split_across_writes_still_arrives_once(self):
        self.write("pre")
        self.assertEqual(self.done, [], "half a word is not a command")
        self.write("ss\n")
        self.assertEqual(self.done, ["press"])

    def test_rubbish_is_logged_and_ignored(self):
        self.write("rm -rf /\n\n   \n")
        self.assertEqual(self.done, [])
        self.assertEqual(len(self.warnings), 1, "one line, one complaint")

    def test_nothing_is_lost_when_the_client_opens_the_pipe_read_write(self):
        # The client opens <> so the open cannot block when the daemon has
        # gone. It never reads, so it cannot eat its own command, and this is
        # the test that says so: two hundred presses, two hundred presses.
        for _ in range(200):
            fd = os.open(
                os.path.join(self.tmp.name, "control"), os.O_RDWR | os.O_NONBLOCK
            )
            try:
                os.write(fd, b"press\n")
            finally:
                os.close(fd)
            self.daemon.on_control()
        self.assertEqual(self.done, ["press"] * 200)

    def test_a_command_left_from_a_dead_daemon_is_not_replayed(self):
        # Nobody is reading, so the write goes nowhere the moment the writer
        # closes: a FIFO is a buffer, not a mailbox.
        self.daemon.selector.unregister(self.daemon.control_fd)
        os.close(self.daemon.control_fd)
        fd = os.open(os.path.join(self.tmp.name, "control"), os.O_RDWR | os.O_NONBLOCK)
        os.write(fd, b"press\n")
        os.close(fd)
        self.daemon.control_fd = os.open(
            os.path.join(self.tmp.name, "control"), os.O_RDWR | os.O_NONBLOCK
        )
        self.daemon.on_control()
        self.assertEqual(self.done, [])

    def test_the_pid_file_is_a_liveness_note_not_a_target(self):
        self.daemon.write_pid()
        pid, token = self.run.read_text("pid").split()
        self.assertEqual(int(pid), os.getpid())
        self.assertEqual(token, daemon.start_token(os.getpid()))
        self.assertIsNone(daemon.start_token(2**30), "no such process, no token")


class TranscriptionDeadline(unittest.TestCase):
    """A decoder that wedges must not hold the queue for the whole session."""

    def setUp(self):
        self.notes: list = []
        self.original = (daemon.notify, daemon.warn)
        daemon.notify = lambda *args, **kw: self.notes.append(args)
        daemon.warn = lambda message: None
        self.addCleanup(self.restore)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = safeio.open_dir(self.tmp.name)
        self.addCleanup(self.dir.close)

        self.daemon = object.__new__(daemon.Daemon)
        self.daemon.queue = deque()
        self.daemon.running = True
        self.daemon.write_state = lambda: None

    def restore(self):
        daemon.notify, daemon.warn = self.original

    def test_a_wedged_transcription_is_killed_and_reported(self):
        child = subprocess.Popen(["/usr/bin/sleep", "30"], start_new_session=True)
        self.addCleanup(child.wait)
        self.daemon.child = child
        self.daemon.child_dir = self.dir
        self.daemon.child_at = time.monotonic() - 1

        self.daemon.poll_child()

        self.assertIsNone(self.daemon.child, "the wedged child is let go of")
        self.assertEqual(child.poll(), -9, "and actually killed")
        self.assertTrue(
            any("failed" in str(note[0]) for note in self.notes),
            "a transcription that had to be killed says so",
        )

    def test_the_deadline_follows_the_length_of_the_recording(self):
        self.dir.write("meta.json", '{"duration_seconds": 3600}')
        self.assertEqual(
            self.daemon.child_deadline(self.dir), 3600 * daemon.TRANSCRIBE_FACTOR
        )

    def test_a_session_with_no_usable_meta_still_gets_a_deadline(self):
        self.assertEqual(self.daemon.child_deadline(self.dir), daemon.TRANSCRIBE_FLOOR)


if __name__ == "__main__":
    unittest.main()
