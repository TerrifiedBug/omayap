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


def window_title(pid, windows, binary=None, chain=None):
    """The title of the window the capture belongs to, cleaned up.

    By pid first, because that is the only answer that cannot be wrong; by
    class name second, because a browser's audio child sometimes has no window
    anywhere in its ancestry.
    """
    owned = chain if chain is not None else pid_chain(pid)
    by_pid = {}
    for window in windows or []:
        by_pid.setdefault(window.get("pid"), window)
    for candidate in owned:
        window = by_pid.get(candidate)
        if window:
            return clean_title(window.get("title"), binary)
    if binary:
        needle = str(binary).lower()
        for window in windows or []:
            if needle in str(window.get("class", "")).lower():
                return clean_title(window.get("title"), binary)
    return None


def clean_title(title, binary):
    """Drop the application's own name off the end of its window title.

    "Quarterly Review | Microsoft Teams" is a meeting called Quarterly Review.
    The app name is already recorded separately, so keeping it would put it in
    the directory name twice.
    """
    text = str(title or "").strip()
    needle = str(binary or "").lower()
    if needle:
        for separator in TITLE_SEPARATORS:
            cut = text.rfind(separator)
            if cut > 0 and needle in text[cut + len(separator) :].lower():
                text = text[:cut].strip()
                break
    return text or None
