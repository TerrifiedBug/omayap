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
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import ENGINE, MODEL

SPEAKERS = ("me", "them")
TRACKS = {"mic": "me", "system": "them"}
FILES = {"mic": "mic.f32", "system": "system.f32"}

_ALNUM = re.compile(r"[^\W_]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
TITLE_MAX = 60


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


def session_dir(root: Path, started: datetime, title=None) -> Path:
    """Create and return the session directory.

    Two calls in the same minute collide, so the counter goes between the
    stamp and the title, as in `2026.01.02-0930-2-Team Standup`, which keeps
    the vault sorting by time rather than by title.
    """
    base = stamp(started)
    clean = sanitize_title(title)
    suffix = f"-{clean}" if clean else ""
    name = base + suffix
    counter = 2
    while (root / name).exists():
        name = f"{base}-{counter}{suffix}"
        counter += 1
    path = root / name
    path.mkdir(parents=True)
    return path


def stamp_of(session: Path) -> str:
    """The date and time part of a session directory name, title dropped.

    For logs: `2026.01.02-0930-2-Weekly Sync` identifies a meeting by name, and
    a journal is a worse place for that than the directory itself.
    """
    parts = str(getattr(session, "name", session)).split("-")
    return "-".join(parts[:2]) if len(parts) > 2 else "-".join(parts[:2])


def write_meta(
    session: Path,
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
    write_json(session / "meta.json", meta)
    return meta


def read_meta(session: Path) -> dict:
    return json.loads((session / "meta.json").read_text(encoding="utf-8"))


def render_markdown(name: str, segments: list) -> str:
    lines = [f"# {name}", "", f"engine: {ENGINE} ({MODEL})"]
    for seg in segments:
        lines += ["", f"**[{clock(seg['start_ms'])}] {seg['speaker']}:** {seg['text']}"]
    return "\n".join(lines) + "\n"


def write_transcript(session: Path, segments: list) -> list:
    """transcript.md first: its existence is what marks a session ready."""
    ordered = sorted(segments, key=lambda seg: (seg["start_ms"], seg["end_ms"]))
    write_text(session / "transcript.md", render_markdown(session.name, ordered))
    write_json(
        session / "transcript.json",
        {
            "created_at": rfc3339_utc(datetime.now(timezone.utc)),
            "engine": ENGINE,
            "model": MODEL,
            "segments": ordered,
        },
    )
    return ordered


def pending(root: Path) -> list:
    """Sessions with audio and meta but no transcript, oldest name first."""
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and (path / "meta.json").is_file()
        and not (path / "transcript.json").is_file()
    )


def log(session: Path, message: str) -> None:
    """transcribe.log lives beside the audio: one session, one story."""
    line = f"{rfc3339_utc(datetime.now(timezone.utc))} {message}\n"
    try:
        with open(session / "transcribe.log", "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, sort_keys=True, indent=2) + "\n")
