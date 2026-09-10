"""Who else is using the microphone, and what window it belongs to.

Pure functions over `pw-dump` and `hyprctl clients -j` output, so the whole
meeting-detection rule set is unit-testable and the daemon only has to run the
two commands.

PipeWire splits the information across two objects: the *node* is the stream
and carries `media.class` and `node.name`, and the *client* that owns it
carries `application.process.binary` and the pid. pw-record's node says
nothing about which program it is, so the join is not optional.
"""

from __future__ import annotations

import re

# The shell itself watches levels for the bar; it is not a meeting.
IGNORED = {"quickshell"}

CAPTURE_CLASS = "Stream/Input/Audio"
NODE_TYPE = "PipeWire:Interface:Node"
CLIENT_TYPE = "PipeWire:Interface:Client"

TITLE_SEPARATORS = (" - ", " | ", " — ")
PPID_HOPS = 8


# Streams name themselves after their direction: Chromium's capture stream
# calls itself "Google Chrome input", which reads badly in "X is in a call".
STREAM_SUFFIXES = (" input", " output", " capture", " recording")


def pretty_app(name: str) -> str:
    """The app's name without the stream direction glued on the end."""
    text = str(name or "").strip()
    lowered = text.lower()
    for suffix in STREAM_SUFFIXES:
        if lowered.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)].strip()
    return text


def _props(obj) -> dict:
    return ((obj or {}).get("info") or {}).get("props") or {}


def clients(dump) -> dict:
    """Client id -> its props, so a node can be traced back to a process."""
    found = {}
    for obj in dump or []:
        if obj.get("type") == CLIENT_TYPE:
            found[obj.get("id")] = _props(obj)
    return found


def capturing(dump, excluded=()) -> list:
    """Every program recording the microphone right now.

    Returns `{"pid", "app", "binary"}` per program. `app` is what the stream
    calls itself, which is the name the user recognises and the name that goes
    in meta.json; `binary` is the process, which is what matches a window
    class. They differ more often than not — `pw-record` is a symlink to
    `pw-cat`, and Chromium's streams say "Chromium" while its binary is
    `chromium` — so both are kept and the exclusion list matches either.

    Excludes monitors (a stream reading what the speakers play is not a call),
    our own captures, and the shell.
    """
    skip = {str(name).lower() for name in IGNORED | set(excluded or ())}
    owners = clients(dump)
    found: list = []
    seen = set()
    for obj in dump or []:
        if obj.get("type") not in (NODE_TYPE, None):
            continue
        props = _props(obj)
        if props.get("media.class") != CAPTURE_CLASS:
            continue
        if ((obj.get("info") or {}).get("state")) != "running":
            continue
        if str(props.get("stream.monitor", "")).lower() == "true":
            continue
        node_name = str(props.get("node.name", ""))
        if node_name.startswith("omayap"):
            continue
        owner = owners.get(props.get("client.id"), {})
        name = props.get("application.name") or owner.get("application.name")
        binary = props.get("application.process.binary") or owner.get(
            "application.process.binary"
        )
        app = pretty_app(name) or binary or node_name
        if not app:
            continue
        labels = {str(label).lower() for label in (name, binary, node_name) if label}
        if labels & skip:
            continue
        pid = props.get("application.process.id") or owner.get("application.process.id")
        key = pid if pid else f"name:{app}"
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "pid": int(pid) if pid else None,
                "app": str(app),
                "binary": str(binary or app),
            }
        )
    return found


def read_ppid(pid) -> int | None:
    try:
        with open(f"/proc/{int(pid)}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def pid_chain(pid, ppid=read_ppid) -> list:
    """The pid and its ancestors, closest first.

    Chromium and Electron capture audio from a child process, so the window
    belongs to a grandparent rather than to the pid PipeWire reports.
    """
    chain = []
    current = pid
    for _ in range(PPID_HOPS):
        if not current or current in chain or current <= 1:
            break
        chain.append(int(current))
        current = ppid(current)
    return chain


# Five characters of a shared prefix, because `chromium` and `chrome` are the
# same brand and neither contains the other. Only ever applied to program
# identifiers, never to a window title: a class and a binary are chosen by the
# same developer, and the false-positive cost here is one wrong title rather
# than a truncated one.
BRAND_PREFIX = 5


def related(window_class, binary) -> bool:
    """Could this window belong to that program?

    `google-chrome` and `chrome` by containment, `chromium` and `chrome` by
    prefix, `com.mitchellh.ghostty` and `pw-cat` not at all.
    """
    left = re.sub(r"[^a-z0-9]", "", str(window_class or "").lower())
    right = re.sub(r"[^a-z0-9]", "", str(binary or "").lower())
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    return (
        len(left) >= BRAND_PREFIX
        and len(right) >= BRAND_PREFIX
        and left[:BRAND_PREFIX] == right[:BRAND_PREFIX]
    )


def window_title(pid, windows, binary=None, chain=None):
    """The title of the window the capture belongs to, cleaned up.

    Three passes, in order of how much they can be trusted:

    1. The capturing pid's own window. Cannot be wrong.
    2. An ancestor's window, but only one that plausibly belongs to the same
       program. Chromium captures from a child process, so ancestry is
       necessary; without the class check it also means `pw-record` inherits
       the title of the terminal it was typed into, which is how a meeting
       ended up named after a text editor.
    3. Any window of the same program, most recently focused first, because a
       browser's audio child sometimes has no window in its ancestry at all.
    """
    owned = chain if chain is not None else pid_chain(pid)
    ranked = sorted(
        (w for w in (windows or []) if isinstance(w, dict)),
        key=lambda w: w.get("focusHistoryID", 1 << 30),
    )
    by_pid = {}
    for window in ranked:
        by_pid.setdefault(window.get("pid"), window)

    own = by_pid.get(owned[0]) if owned else None
    if own:
        return clean_title(own.get("title"), binary)

    for candidate in owned[1:]:
        window = by_pid.get(candidate)
        if window and related(window.get("class"), binary):
            return clean_title(window.get("title"), binary)

    for window in ranked:
        if related(window.get("class"), binary):
            return clean_title(window.get("title"), binary)
    return None


# What browsers and meeting apps hang off the end of a title. Stripped by
# name because the binary is only ever one of them: a Google Meet call in
# Chromium is titled "Standup - Google Meet - Google Chrome", and the meeting
# is called Standup.
TITLE_TAILS = (
    "google chrome", "chromium", "brave", "firefox", "mozilla firefox",
    "microsoft edge", "google meet", "microsoft teams", "teams", "zoom",
    "zoom workplace", "slack", "discord", "webex", "jitsi meet", "whereby",
)

# Chrome and Firefox prefix a title with the unread count.
UNREAD = re.compile(r"^\(\d+\)\s*")

# Two brands can stack: "… - Google Meet - Google Chrome".
MAX_TAILS = 3


def clean_title(title, binary):
    """Drop the app's own name, and the service's, off the end of a title.

    "Quarterly Review | Microsoft Teams" is a meeting called Quarterly Review.
    The app is recorded separately, so keeping it would put it in the directory
    name twice, and a title that is nothing but brands is no title at all.
    """
    text = UNREAD.sub("", str(title or "").strip())
    for _ in range(MAX_TAILS):
        shorter = strip_tail(text, binary)
        if shorter == text:
            break
        text = shorter
    return text or None


def strip_tail(text: str, binary) -> str:
    needle = str(binary or "").lower()
    for separator in TITLE_SEPARATORS:
        cut = text.rfind(separator)
        if cut <= 0:
            continue
        tail = text[cut + len(separator) :].strip().lower()
        if (needle and needle in tail) or tail in TITLE_TAILS:
            return text[:cut].strip()
    return text
