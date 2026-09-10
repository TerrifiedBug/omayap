"""The on-disk contract: directory names, meta.json, and transcript.md.

The fixtures are synthetic. What they pin is the shape yap wrote on macOS,
which existing transcripts and any tooling reading them still expect, so a
drift here is a bug in omayap rather than an improvement.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omayap import session  # noqa: E402

# A wholly invented session in the exact shape the contract fixes: made-up
# date, made-up clock, made-up words. Nothing here is copied from a recording.
GOLDEN_NAME = "2026.01.02-0930"
GOLDEN_SEGMENTS = [
    {"start_ms": 2000, "end_ms": 3500, "speaker": "me", "text": "Hello"},
    {
        "start_ms": 7000,
        "end_ms": 9500,
        "speaker": "me",
        "text": "Can you hear me now?",
    },
    {"start_ms": 10000, "end_ms": 11500, "speaker": "me", "text": "Sharing my screen"},
    {"start_ms": 27000, "end_ms": 28000, "speaker": "them", "text": "Got it."},
]
GOLDEN_MARKDOWN = """# 2026.01.02-0930

engine: parakeet (parakeet-tdt-ctc-110m)

**[0:02] me:** Hello

**[0:07] me:** Can you hear me now?

**[0:10] me:** Sharing my screen

**[0:27] them:** Got it.
"""


class TestClock(unittest.TestCase):
    def test_minutes_and_seconds(self):
        self.assertEqual(session.clock(0), "0:00")
        self.assertEqual(session.clock(2500), "0:02")
        self.assertEqual(session.clock(59_999), "0:59")
        self.assertEqual(session.clock(60_000), "1:00")

    def test_hours_appear_only_past_an_hour(self):
        self.assertEqual(session.clock(3_599_000), "59:59")
        self.assertEqual(session.clock(3_600_000), "1:00:00")
        self.assertEqual(session.clock(3_661_000), "1:01:01")


class TestSanitizeTitle(unittest.TestCase):
    def test_path_hostile_characters_become_dashes(self):
        self.assertEqual(
            session.sanitize_title("Join meeting | Design: Review"),
            "Join meeting | Design- Review",
        )
        self.assertEqual(session.sanitize_title("Q3/Q4 planning"), "Q3-Q4 planning")

    def test_whitespace_collapses(self):
        self.assertEqual(session.sanitize_title("  Team   Standup \n"), "Team Standup")

    def test_a_title_with_no_words_is_no_title(self):
        for empty in ("", None, "   ", "- : /", "***"):
            self.assertEqual(session.sanitize_title(empty), "")

    def test_long_titles_are_cut_without_a_trailing_space(self):
        long = "Weekly " * 20
        clean = session.sanitize_title(long)
        self.assertLessEqual(len(clean), session.TITLE_MAX)
        self.assertEqual(clean, clean.strip())


class TestStampOf(unittest.TestCase):
    """Log lines identify a session without naming the meeting."""

    def test_the_title_and_counter_are_dropped(self):
        self.assertEqual(session.stamp_of(Path("/r/2026.01.02-0930-2-Weekly Sync")), "2026.01.02-0930")
        self.assertEqual(session.stamp_of(Path("/r/2026.01.02-0930-Weekly Sync")), "2026.01.02-0930")

    def test_a_bare_session_is_unchanged(self):
        self.assertEqual(session.stamp_of(Path("/r/2026.01.02-0930")), "2026.01.02-0930")

    def test_no_title_survives_whatever_the_name(self):
        for name in ("2026.01.02-0930-2-A Very Private Call", "2026.01.02-0930-x-y-z"):
            self.assertNotIn("Private", session.stamp_of(Path("/r") / name))
            self.assertEqual(session.stamp_of(Path("/r") / name).count("-"), 1)


class TestSessionDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Naive, so it is local time: the stamp is local by contract.
        self.started = datetime(2026, 1, 2, 9, 30).astimezone()

    def tearDown(self):
        self.tmp.cleanup()

    def test_bare_and_titled_names(self):
        self.assertEqual(session.session_dir(self.root, self.started).name, "2026.01.02-0930")
        titled = session.session_dir(self.root, self.started, "Weekly Sync")
        self.assertEqual(titled.name, "2026.01.02-0930-Weekly Sync")

    def test_the_counter_goes_before_the_title(self):
        first = session.session_dir(self.root, self.started, "Weekly Sync")
        second = session.session_dir(self.root, self.started, "Weekly Sync")
        self.assertEqual(first.name, "2026.01.02-0930-Weekly Sync")
        self.assertEqual(second.name, "2026.01.02-0930-2-Weekly Sync")

    def test_untitled_collisions_still_get_a_counter(self):
        session.session_dir(self.root, self.started)
        self.assertEqual(
            session.session_dir(self.root, self.started).name, "2026.01.02-0930-2"
        )


class TestWrittenFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.started = datetime(2026, 1, 2, 9, 30, 0, tzinfo=timezone.utc)
        self.ended = datetime(2026, 1, 2, 9, 30, 28, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_matches_the_vault(self):
        self.assertEqual(
            session.render_markdown(GOLDEN_NAME, GOLDEN_SEGMENTS), GOLDEN_MARKDOWN
        )

    def test_meta_keys_and_order(self):
        session.write_meta(
            self.dir, self.started, self.ended, {"mic": 1500, "system": 0}
        )
        raw = (self.dir / "meta.json").read_text(encoding="utf-8")
        meta = json.loads(raw)
        self.assertEqual(
            list(meta),
            ["duration_seconds", "ended", "files", "start_offset_ms", "started"],
        )
        self.assertEqual(meta["duration_seconds"], 28)
        self.assertEqual(meta["started"], "2026-01-02T09:30:00Z")
        self.assertEqual(meta["ended"], "2026-01-02T09:30:28Z")
        self.assertEqual(meta["files"], {"mic": "mic.f32", "system": "system.f32"})
        self.assertEqual(meta["start_offset_ms"], {"mic": 1500, "system": 0})
        self.assertTrue(raw.endswith("\n"))

    def test_app_and_title_appear_only_when_there_is_one(self):
        meta = session.write_meta(
            self.dir,
            self.started,
            self.ended,
            {},
            app="teams",
            title="Weekly Sync",
        )
        self.assertEqual(meta["app"], "teams")
        self.assertEqual(meta["title"], "Weekly Sync")
        self.assertEqual(
            list(json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))),
            [
                "app",
                "duration_seconds",
                "ended",
                "files",
                "start_offset_ms",
                "started",
                "title",
            ],
        )

    def test_transcript_is_sorted_and_named_after_its_directory(self):
        target = self.dir / GOLDEN_NAME
        target.mkdir()
        shuffled = [GOLDEN_SEGMENTS[3], GOLDEN_SEGMENTS[0], GOLDEN_SEGMENTS[2], GOLDEN_SEGMENTS[1]]
        session.write_transcript(target, shuffled)
        self.assertEqual(
            (target / "transcript.md").read_text(encoding="utf-8"), GOLDEN_MARKDOWN
        )
        data = json.loads((target / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["created_at", "engine", "model", "segments"])
        self.assertEqual(data["engine"], "parakeet")
        self.assertEqual(data["model"], "parakeet-tdt-ctc-110m")
        self.assertEqual([seg["start_ms"] for seg in data["segments"]], [2000, 7000, 10000, 27000])

    def test_pending_is_meta_without_a_transcript(self):
        done = self.dir / "2026.01.02-0900"
        waiting = self.dir / "2026.01.02-1000"
        for path in (done, waiting):
            path.mkdir()
            (path / "meta.json").write_text("{}", encoding="utf-8")
        (done / "transcript.json").write_text("{}", encoding="utf-8")
        (self.dir / "not-a-session").mkdir()
        self.assertEqual(session.pending(self.dir), [waiting])


if __name__ == "__main__":
    unittest.main()
