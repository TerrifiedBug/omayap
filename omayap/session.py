"""Write a recording session in the layout transcript tooling expects.

The layout is the one yap wrote on macOS, kept byte-compatible so existing
transcripts and any tooling that reads them keep working:

    <recordings_dir>/<yyyy.MM.dd-HHmm>[-N][-<title>]/
      mic.f32  system.f32   <- raw capture, deleted unless keep_audio
      transcript.md         <- existence means the session is ready to read
      transcript.json
      meta.json             <- started/ended are RFC3339 UTC

transcript.md, verbatim shape:

    # 2026.01.02-0930

    engine: parakeet (parakeet-tdt-ctc-110m)

    **[0:02] me:** Hello

Speaker tags are exactly `me` (our microphone) and `them` (everyone else).
Nothing here imports the engine, so the whole contract is testable without a
126 MB model on disk.

A session is a `safeio.Dir`, not a path: the recordings tree is the one place
omayap writes that the user gets to point anywhere, including somewhere shared,
and a two-hour recording is a long time to leave a name resolvable. The tree is
opened and checked once, the session directory is created inside it with
mkdir's own exclusivity as the collision check, and every file after that is
opened relative to that descriptor without following symlinks.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from . import ENGINE, MODEL, safeio

SPEAKERS = ("me", "them")
TRACKS = {"mic": "me", "system": "them"}
FILES = {"mic": "mic.f32", "system": "system.f32"}

_ALNUM = re.compile(r"[^\W_]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
TITLE_MAX = 60

# Enough room for a full day of meetings in one directory before giving up.
MAX_COLLISIONS = 512


def open_root(path) -> safeio.Dir:
    """The recordings tree, created if it is not there yet.

    `private=False`: this one belongs to the user, who may well have it in a
    synced or shared directory. It still has to be theirs and closed to other
    people's writes; what it does not have to be is 0700.
    """
    return safeio.open_dir(path, create=True, private=False)


def stamp(started: datetime) -> str:
    """Session directory name: local-time yyyy.MM.dd-HHmm."""
    return started.astimezone().strftime("%Y.%m.%d-%H%M")


def rfc3339_utc(when: datetime) -> str:
    """2026-01-02T09:30:00Z, the form yap wrote and the tooling parses."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(ms: int) -> str:
    """Segment timestamp: m:ss, or h:mm:ss once past an hour."""
    total = max(0, int(ms)) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def sanitize_title(title) -> str:
    """A window title, made safe to be a directory name.

    Slashes and colons are the two characters a title cannot carry into a
    path; everything else survives, because the title is the only clue about
    which meeting a session was. A title with no letters or digits in it is no
    title at all.
    """
    text = _WHITESPACE.sub(" ", str(title or "").replace("/", "-").replace(":", "-"))
    text = text.strip()[:TITLE_MAX].strip()
    return text if _ALNUM.search(text) else ""


def session_dir(root: safeio.Dir, started: datetime, title=None) -> safeio.Dir:
    """Create and open the session directory, inside the tree's descriptor.

    Two calls in the same minute collide, so the counter goes between the
    stamp and the title, as in `2026.01.02-0930-2-Team Standup`, which keeps
    the vault sorting by time rather than by title. The collision is settled by
    mkdir failing rather than by asking whether the name exists first, which is
    both shorter and the only version without a race in it.
    """
    base = stamp(started)
    clean = sanitize_title(title)
    suffix = f"-{clean}" if clean else ""
    name = base + suffix
    for counter in range(2, MAX_COLLISIONS + 2):
        try:
            root.mkdir(name)
        except FileExistsError:
            name = f"{base}-{counter}{suffix}"
            continue
        return root.child(name)
    raise OSError(f"{root.path} already holds {MAX_COLLISIONS} sessions for {base}")


def open_track(session: safeio.Dir, track: str):
    """The file one capture writes into. Fails if anything is there already."""
    fd = session.open(FILES[track], os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    return os.fdopen(fd, "wb")


def stamp_of(session) -> str:
    """The date and time part of a session directory name, title dropped.

    For logs: `2026.01.02-0930-2-Weekly Sync` identifies a meeting by name, and
    a journal is a worse place for that than the directory itself.
    """
    parts = str(getattr(session, "name", session)).split("-")
    return "-".join(parts[:2])


def write_meta(
    session: safeio.Dir,
    started: datetime,
    ended: datetime,
    offsets: dict,
    app=None,
    title=None,
) -> dict:
    """meta.json — written when the recording stops, before transcription."""
    meta = {
        "duration_seconds": max(0, round((ended - started).total_seconds())),
        "ended": rfc3339_utc(ended),
        "files": dict(FILES),
        "start_offset_ms": {
            track: int(offsets.get(track, 0)) for track in sorted(FILES)
        },
        "started": rfc3339_utc(started),
    }
    if app:
        meta["app"] = str(app)
    clean = sanitize_title(title)
    if clean:
        meta["title"] = clean
    write_json(session, "meta.json", meta)
    return meta


def read_meta(session: safeio.Dir) -> dict:
    return json.loads(session.read_text("meta.json"))


def render_markdown(name: str, segments: list) -> str:
    lines = [f"# {name}", "", f"engine: {ENGINE} ({MODEL})"]
    for seg in segments:
        lines += ["", f"**[{clock(seg['start_ms'])}] {seg['speaker']}:** {seg['text']}"]
    return "\n".join(lines) + "\n"


def write_transcript(session: safeio.Dir, segments: list) -> list:
    """transcript.md first: its existence is what marks a session ready."""
    ordered = sorted(segments, key=lambda seg: (seg["start_ms"], seg["end_ms"]))
    session.write("transcript.md", render_markdown(session.name, ordered))
    write_json(
        session,
        "transcript.json",
        {
            "created_at": rfc3339_utc(datetime.now(timezone.utc)),
            "engine": ENGINE,
            "model": MODEL,
            "segments": ordered,
        },
    )
    return ordered


def pending(root: safeio.Dir) -> list:
    """Names of sessions with meta but no transcript, oldest name first.

    Names rather than descriptors: this runs at startup over a whole tree, and
    holding an open directory for each one only to transcribe them one at a
    time would be a lot of file descriptors for nothing.
    """
    found = []
    for name in root.names():
        if not root.is_dir(name):
            continue
        try:
            with root.child(name) as candidate:
                if candidate.is_file("meta.json") and not candidate.is_file(
                    "transcript.json"
                ):
                    found.append(name)
        except OSError:
            continue
    return sorted(found)


def log(session: safeio.Dir, message: str) -> None:
    """transcribe.log lives beside the audio: one session, one story."""
    line = f"{rfc3339_utc(datetime.now(timezone.utc))} {message}\n"
    try:
        session.append("transcribe.log", line)
    except OSError:
        pass


def write_json(session: safeio.Dir, name: str, data) -> None:
    session.write(name, json.dumps(data, sort_keys=True, indent=2) + "\n")
