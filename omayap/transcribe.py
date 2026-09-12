"""Transcribe one finished session, in a process of its own.

Its own process because a two-hour meeting is a minute of solid decoding, and
the daemon must stay a signal away from dictating throughout. The cost is a
second model load, which is half a second and 126 MB that go away again when
the child exits.

The session arrives as a path and is opened the same way the daemon opens it:
one verified walk to a descriptor, then every track, log line and transcript
relative to that. The child re-checks rather than inheriting a descriptor,
because it is also what runs when somebody types `omayap transcribe <dir>` by
hand.

Failure leaves the session pending: meta.json without transcript.json is what
the daemon looks for at startup, so a crashed or killed transcription is
retried rather than lost.
"""

from __future__ import annotations

import os
import sys

from . import config, safeio
from .session import FILES, TRACKS, log, open_root, read_meta, write_transcript


def main(argv) -> int:
    argv = list(argv)
    inherited = None
    if argv and argv[0] == "--fd":
        if len(argv) < 2:
            print("usage: omayap transcribe [--fd N] <session-dir>", file=sys.stderr)
            return 2
        try:
            inherited = int(argv[1])
        except ValueError:
            print(f"omayap: {argv[1]!r} is not a file descriptor", file=sys.stderr)
            return 2
        argv = argv[2:]
    if not argv:
        print("usage: omayap transcribe [--fd N] <session-dir>", file=sys.stderr)
        return 2
    target = os.path.expanduser(argv[0])

    if inherited is not None:
        # The daemon already opened and checked this directory and handed the
        # descriptor over, so there is no name to resolve and nothing to race.
        holder = safeio.adopt(inherited, target)
    else:
        try:
            holder = open_root(target)
        except OSError as error:
            print(f"omayap: cannot open {target}: {error}", file=sys.stderr)
            return 1

    with holder as session:
        try:
            meta = read_meta(session)
        except (OSError, ValueError) as error:
            print(
                f"omayap: cannot read {session.path}/meta.json: {error}",
                file=sys.stderr,
            )
            return 1
        return _run(session, meta)


def _run(session: safeio.Dir, meta: dict) -> int:
    from .engine import Engine, segments, vad

    engine = Engine()
    offsets = meta.get("start_offset_ms") or {}
    files = meta.get("files") or FILES

    collected: list = []
    attempted = 0
    failed = 0
    for track, speaker in sorted(TRACKS.items()):
        name = files.get(track, FILES[track])
        if not session.is_file(name) or session.size(name) == 0:
            log(session, f"skipping missing track {name}")
            continue
        attempted += 1
        log(session, f"transcribing {name} (parakeet)")
        try:
            with _open(session, name) as handle:
                collected += segments(
                    engine, vad(), handle, speaker, int(offsets.get(track, 0))
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
            session.unlink(files.get(track, FILES[track]))
        log(session, "audio removed (keep_audio off)")
    return 0


def _open(session: safeio.Dir, name: str):
    return os.fdopen(session.open(name, os.O_RDONLY), "rb")
