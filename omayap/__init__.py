"""omayap: hold-to-talk dictation and a two-track meeting recorder.

This module holds only the things every other module needs and that nothing
needs to import a model or a subprocess for: where files live, and the two
strings that name the engine in a transcript.

Layout, all of it outside the plugin directory so the plugin stays a git
checkout the validator is happy with:

    $XDG_RUNTIME_DIR/omayap/   pid, daemon.lock, state, levels (FIFO)
    ~/.local/share/omayap/     venv/, models/
    ~/.config/omayap/          config.json
"""

from __future__ import annotations

import os
from pathlib import Path

ENGINE = "parakeet"
MODEL = "parakeet-tdt-ctc-110m"
RATE = 16000

# 0.3 s. A shorter press is a mis-hit, not a word: transcribing it would put
# the model's best guess at a click into the user's document. Lives here so the
# daemon can apply it without importing a 126 MB model.
MIN_SAMPLES = 4800

SHARE = Path.home() / ".local" / "share" / "omayap"
MODELS = SHARE / "models"
CONFIG_DIR = Path.home() / ".config" / "omayap"
CONFIG_FILE = CONFIG_DIR / "config.json"


def run_dir() -> Path:
    """The runtime directory, created 0700 on first use."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    path = Path(base) / "omayap"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
