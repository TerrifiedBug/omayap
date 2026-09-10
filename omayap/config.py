"""~/.config/omayap/config.json — read on every call, never cached.

There is no watcher and no reload signal: the file is a few hundred bytes and
every read of it is either a hotkey press or a five-second poll, so caching it
would only buy a way for the daemon and the panel to disagree.

A broken file is not an error the user has to fix before dictating: it is
ignored, warned about once, and the defaults stand.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import CONFIG_DIR, CONFIG_FILE

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


def _mtime() -> float:
    try:
        return CONFIG_FILE.stat().st_mtime
    except OSError:
        return 0.0


def _warn_once(message: str) -> None:
    global _warned_mtime
    stamp = _mtime()
    if _warned_mtime == stamp:
        return
    _warned_mtime = stamp
    print(f"warning: {message}", file=sys.stderr, flush=True)


def load() -> dict:
    """The config, with every default filled in. Never raises."""
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
    except OSError:
        return dict(DEFAULTS)
    data = _parse(raw)
    if data is None:
        _warn_once(f"{CONFIG_FILE} is not valid JSON — ignoring config")
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


def recordings_dir() -> Path:
    value = get("recordings_dir") or DEFAULTS["recordings_dir"]
    return Path(str(value)).expanduser()


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
    raw = ""
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
    except OSError:
        pass
    data = _parse(raw) if raw.strip() else {}
    if data is None:
        print(
            f"warning: {CONFIG_FILE} is not valid JSON — not writing",
            file=sys.stderr,
            flush=True,
        )
        return False
    data[key] = value
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
    return True
