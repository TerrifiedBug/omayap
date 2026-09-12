"""~/.config/omayap/config.json — read on every call, never cached.

There is no watcher and no reload signal: the file is a few hundred bytes and
every read of it is either a hotkey press or a five-second poll, so caching it
would only buy a way for the daemon and the panel to disagree.

A broken file is not an error the user has to fix before dictating: it is
ignored, warned about once, and the defaults stand. A config directory that is
not ours, or a config.json that has become a symlink to something else, is the
same kind of problem and gets the same answer, because a setting is never worth
following a redirect for.

Every read and write goes through a descriptor on the directory, so the file
that gets parsed is the file inside the directory we checked, and the write
lands beside it rather than wherever a name resolves to a second later.
"""

from __future__ import annotations

import json
import os
import sys

from . import CONFIG_DIR, CONFIG_FILE, safeio

NAME = "config.json"

DEFAULTS = {
    "recordings_dir": "~/Recordings",
    "keep_audio": True,
    "newline_after_dictation": False,
    "meeting_detection": False,
    "meeting_auto_record": False,
    "meeting_excluded_apps": [],
}

# The mtime we last complained about, so a broken file is one line in the
# journal rather than one per keypress.
_warned_mtime: float | None = None


def directory(*, create: bool = False) -> safeio.Dir:
    """The config directory as a descriptor. Raises if it is not ours."""
    return safeio.open_dir(CONFIG_DIR, create=create)


def _stamp(handle: safeio.Dir) -> float:
    info = handle.stat(NAME)
    return info.st_mtime if info else 0.0


def _warn_once(message: str, stamp: float) -> None:
    global _warned_mtime
    if _warned_mtime == stamp:
        return
    _warned_mtime = stamp
    print(f"warning: {message}", file=sys.stderr, flush=True)


def load() -> dict:
    """The config, with every default filled in. Never raises."""
    try:
        with directory() as handle:
            raw = handle.read_text(NAME)
            stamp = _stamp(handle)
    except safeio.Unsafe as error:
        _warn_once(f"{error} — ignoring config", 0.0)
        return dict(DEFAULTS)
    except OSError:
        return dict(DEFAULTS)
    data = _parse(raw)
    if data is None:
        _warn_once(f"{CONFIG_FILE} is not valid JSON — ignoring config", stamp)
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def _parse(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def recordings_dir() -> str:
    """Where sessions go, as the user wrote it, with ~ expanded.

    A string rather than an opened directory: nothing exists yet when this is
    called, and the one place that writes there opens it properly.
    """
    value = get("recordings_dir") or DEFAULTS["recordings_dir"]
    return os.path.expanduser(str(value))


def coerce(value: str):
    """CLI strings are the only writer, and only booleans need a type."""
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def set(key: str, value) -> bool:
    """Write one key, preserving every other key the file already has.

    Refuses to write over a file it cannot parse: the user's exclusion list is
    worth more than this one setting.
    """
    try:
        handle = directory(create=True)
    except OSError as error:
        print(f"warning: cannot open {CONFIG_DIR}: {error}", file=sys.stderr, flush=True)
        return False
    with handle:
        try:
            raw = handle.read_text(NAME)
        except OSError:
            raw = ""
        data = _parse(raw) if raw.strip() else {}
        if data is None:
            print(
                f"warning: {CONFIG_FILE} is not valid JSON — not writing",
                file=sys.stderr,
                flush=True,
            )
            return False
        data[key] = value
        try:
            handle.write(NAME, json.dumps(data, indent=2) + "\n")
        except OSError as error:
            print(f"warning: cannot write {CONFIG_FILE}: {error}", file=sys.stderr, flush=True)
            return False
    return True
