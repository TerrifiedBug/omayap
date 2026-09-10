"""Daemon behaviour that only shows up at runtime.

The daemon owns one selectors loop, so anything it leaves registered runs
forever. These tests build a Daemon without touching a model or a real
capture: the loop's callbacks are ordinary methods over a fake pw-record.
"""

from __future__ import annotations

import os
import selectors
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omayap import MIN_SAMPLES, daemon  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
