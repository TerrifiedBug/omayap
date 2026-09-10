"""Transcribe one finished session, in a process of its own.

Its own process because a two-hour meeting is a minute of solid decoding, and
the daemon must stay a signal away from dictating throughout. The cost is a
second model load, which is half a second and 126 MB that go away again when
the child exits.

Failure leaves the session pending: meta.json without transcript.json is what
the daemon looks for at startup, so a crashed or killed transcription is
retried rather than lost.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import config
from .session import FILES, TRACKS, log, read_meta, write_transcript


def main(argv) -> int:
    if not argv:
        print("usage: omayap transcribe <session-dir>", file=sys.stderr)
        return 2
    session = Path(argv[0]).expanduser()
    try:
        meta = read_meta(session)
    except (OSError, ValueError) as error:
        print(f"omayap: cannot read {session}/meta.json: {error}", file=sys.stderr)
        return 1

    from .engine import Engine, segments, vad

    engine = Engine()
    offsets = meta.get("start_offset_ms") or {}
    files = meta.get("files") or FILES

    collected: list = []
    attempted = 0
    failed = 0
    for track, speaker in sorted(TRACKS.items()):
        path = session / files.get(track, FILES[track])
        if not path.is_file() or path.stat().st_size == 0:
            log(session, f"skipping missing track {path.name}")
            continue
        attempted += 1
        log(session, f"transcribing {path.name} (parakeet)")
        try:
            collected += segments(
                engine, vad(), path, speaker, int(offsets.get(track, 0))
            )
        except Exception as error:  # one bad track must not lose the other
            failed += 1
            log(session, f"track {track} failed: {error}")

    if attempted and failed == attempted:
        log(session, "transcription failed: every track failed")
        return 1

    ordered = write_transcript(session, collected)
    log(session, f"done — {len(ordered)} segments")

    if not config.get("keep_audio"):
        for track in FILES:
            path = session / files.get(track, FILES[track])
            path.unlink(missing_ok=True)
        log(session, "audio removed (keep_audio off)")
    return 0
