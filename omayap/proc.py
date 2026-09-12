"""Every external program omayap runs, resolved once and run in a closed world.

The daemon lives as long as the login session does and runs eight programs it
did not write: pw-record, pactl, pw-dump, hyprctl, wtype, wl-copy, busctl and
Omarchy's notification sender. Inheriting PATH for those is how a writable
directory early in someone's PATH turns a dictation key into arbitrary code, so
nothing here consults PATH. A name is resolved against a fixed list of system
directories, the file it lands on has to be a regular executable owned by root
or by us and writable by nobody else, and the check runs on every call rather
than once, because a package upgrade can replace a binary underneath a
long-lived process.

Children get an environment built from scratch rather than a copy of ours, a
session of their own so a deadline can kill a whole process tree instead of a
shell that already forked, and a ceiling on how much they can say. A program
that will not stop talking must not be able to grow this process without bound.
"""

from __future__ import annotations

import os
import selectors
import signal
import stat as stat_module
import subprocess
import sys
import time
from typing import NamedTuple

# Every program omayap runs, by absolute path. Not a search: a name resolved
# against a list of directories is still a name resolved at runtime, and the
# point of this table is that the binaries reviewed at this commit are the
# binaries that run. Two candidates only where Omarchy itself installs to two
# places; the first one that exists and passes the checks wins.
TOOLS = {
    "pw-record": ("/usr/bin/pw-record",),
    "pactl": ("/usr/bin/pactl",),
    "pw-dump": ("/usr/bin/pw-dump",),
    "hyprctl": ("/usr/bin/hyprctl",),
    "wtype": ("/usr/bin/wtype",),
    "wl-copy": ("/usr/bin/wl-copy",),
    "busctl": ("/usr/bin/busctl",),
    "omarchy-notification-send": (
        "/usr/share/omarchy/bin/omarchy-notification-send",
        "/usr/local/share/omarchy/bin/omarchy-notification-send",
    ),
}

# The PATH children get. omarchy-notification-send is a shell script that calls
# omarchy-glyph, omarchy-action and omarchy-exec-argv by name, so its own
# directory has to be here; it is root-owned, like the other two.
SAFE_PATH = "/usr/bin:/bin:/usr/share/omarchy/bin"

# What a child is allowed to inherit. Everything here names the session the
# child has to join: the Wayland socket, the user bus, the PipeWire runtime
# directory, the Hyprland instance. Nothing here can redirect a loader or an
# interpreter, which is the reason the list is a list rather than a copy of the
# environment with a few names removed.
PASSTHROUGH = (
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "PIPEWIRE_REMOTE",
    "PULSE_SERVER",
    "PULSE_RUNTIME_PATH",
)

# Output ceilings. One megabyte is far more than hyprctl or a notification id
# will ever be; pw-dump on a busy graph is the one genuinely large reader, and
# 32 MB is about fifty times the biggest dump seen on this machine.
LIMIT = 1 << 20
DUMP_LIMIT = 32 << 20

# How long a terminated child gets to die before it is killed, and how long the
# kill gets before we give up and let the kernel deal with it.
GRACE = 2.0

# A fire-and-forget child that outlives this is not going to finish.
BACKGROUND_DEADLINE = 20.0
BACKGROUND_MAX = 32


class ToolError(OSError):
    """A program is missing, or is not one we are willing to run."""


class Result(NamedTuple):
    code: int
    out: bytes
    expired: bool


def tool(name: str) -> str:
    """The absolute path of a trusted program, or ToolError.

    Symlinks are followed before the checks, because half of these are links:
    pw-record is pw-cat, python3 is python3.14. What matters is the file that
    ends up being executed, and every directory on the way to it.
    """
    candidates = TOOLS.get(name)
    if not candidates:
        raise ToolError(f"{name} is not one of omayap's programs")
    problems = []
    for candidate in candidates:
        problem = _rejection(candidate, root_only=True)
        if problem is None:
            return candidate
        problems.append(problem)
    raise ToolError("; ".join(problems))


def interpreter() -> str:
    """Our own Python, for the transcription child.

    The one executable that is ours rather than the system's: setup.sh builds
    the venv under ~/.local/share/omayap. Same checks, one owner wider.

    What comes back is the venv's own path, even though what gets checked is
    the system interpreter it links to. Python finds pyvenv.cfg beside the
    binary it was started as, so executing the resolved target instead would
    start a Python with no venv, no sherpa_onnx, and a transcription that
    fails the moment it tries to load the model.
    """
    path = sys.executable or ""
    problem = _rejection(path, root_only=False)
    if problem is not None:
        raise ToolError(problem)
    return path


def _rejection(path: str, *, root_only: bool) -> str | None:
    """Why this file must not be executed, or None if it may be.

    The file and every directory above it, because a program is only as fixed
    as the least fixed directory on the way to it: /usr/bin/wtype cannot be
    swapped by anyone but root, and neither can /usr or /.
    """
    real = os.path.realpath(path)
    owners = (0,) if root_only else (0, os.getuid())
    try:
        info = os.stat(real)
    except OSError as error:
        return f"cannot stat {path}: {error}"
    if not stat_module.S_ISREG(info.st_mode):
        return f"{real} is not a regular file"
    if not info.st_mode & 0o111:
        return f"{real} is not executable"
    if info.st_uid not in owners:
        return f"{real} is owned by uid {info.st_uid}"
    if info.st_mode & 0o022:
        return f"{real} is writable by group or world"
    holder = os.path.dirname(real)
    while True:
        try:
            directory = os.stat(holder)
        except OSError as error:
            return f"cannot stat {holder}: {error}"
        if directory.st_uid not in owners:
            return f"{holder} is owned by uid {directory.st_uid}"
        if directory.st_mode & 0o022:
            return f"{holder} is writable by group or world"
        if holder == "/":
            return None
        holder = os.path.dirname(holder)


def environ(**extra) -> dict:
    """A child's whole environment. Pass None for a name to leave it out."""
    env = {"PATH": SAFE_PATH}
    for name in PASSTHROUGH:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    for name, value in extra.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = str(value)
    return env


def spawn(
    name: str,
    args=(),
    *,
    stdout=subprocess.PIPE,
    stdin=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    env=None,
    bufsize=-1,
    path=None,
    pass_fds=(),
) -> subprocess.Popen:
    """Start a long-lived child: closed environment, session of its own."""
    program = path or tool(name)
    return subprocess.Popen(
        [program, *args],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        env=environ() if env is None else env,
        start_new_session=True,
        close_fds=True,
        bufsize=bufsize,
        pass_fds=pass_fds,
    )


def run(
    name: str,
    args=(),
    *,
    timeout: float,
    limit: int = LIMIT,
    stdin: bytes | None = None,
    env=None,
    capture: bool = True,
) -> Result:
    """Run a child to completion under a deadline and an output ceiling.

    `expired` says the deadline or the ceiling was hit and the process tree was
    killed, which callers treat as a failure with whatever output arrived
    before it. Nothing here ever blocks longer than `timeout`, which is the
    point: this runs on the daemon's only thread, between a key press and the
    words appearing.

    `capture=False` for a program that forks and leaves a child holding the
    pipe. wl-copy is the one: it backgrounds itself to own the clipboard, so
    waiting for end-of-file on its stdout would mean waiting for the user's
    next copy, and then killing the thing holding their transcript.
    """
    proc = spawn(
        name,
        args,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        env=env,
        bufsize=0,
    )
    deadline = time.monotonic() + timeout
    out = bytearray()
    expired = False
    selector = selectors.DefaultSelector()
    try:
        if stdin:
            os.set_blocking(proc.stdin.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE)
        if capture:
            os.set_blocking(proc.stdout.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
        pending = memoryview(stdin) if stdin else memoryview(b"")
        while selector.get_map():
            left = deadline - time.monotonic()
            if left <= 0:
                expired = True
                break
            for key, _ in selector.select(timeout=min(left, 0.25)):
                if key.fileobj is proc.stdout:
                    chunk = _read(proc.stdout)
                    if chunk is None:
                        continue
                    if not chunk:
                        selector.unregister(proc.stdout)
                        proc.stdout.close()
                        continue
                    out += chunk
                    if len(out) > limit:
                        expired = True
                        break
                else:
                    pending = _write(selector, proc.stdin, pending)
            if expired:
                break
    finally:
        selector.close()

    if not expired:
        try:
            code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            expired = True
        else:
            _close(proc)
            return Result(code, bytes(out), False)
    kill_tree(proc)
    _close(proc)
    return Result(-1, bytes(out), True)


def _read(handle) -> bytes | None:
    try:
        return os.read(handle.fileno(), 1 << 16)
    except BlockingIOError:
        return None
    except OSError:
        return b""


def _write(selector, handle, pending: memoryview) -> memoryview:
    """Feed stdin without ever blocking on a child that stopped reading."""
    try:
        written = os.write(handle.fileno(), pending)
    except BlockingIOError:
        return pending
    except OSError:
        written = len(pending)
    pending = pending[written:]
    if not pending:
        selector.unregister(handle)
        try:
            handle.close()
        except OSError:
            pass
    return pending


def _close(proc) -> None:
    for handle in (proc.stdin, proc.stdout):
        if handle is None or handle.closed:
            continue
        try:
            handle.close()
        except OSError:
            pass


def signal_tree(proc, sig) -> bool:
    """Signal a child's whole process group, falling back to the child.

    The group, not the pid: everything here is spawned with a session of its
    own precisely so a wrapper script cannot leave its own children behind.
    The fallback exists for processes we did not spawn that way, which is only
    ever a test double.
    """
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 1:
        try:
            os.killpg(pid, sig)
            return True
        except OSError:
            pass
    try:
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()
    except OSError:
        return False
    return True


def group_alive(proc) -> bool:
    """Is anything still in the child's process group?

    The leader exiting is not the tree ending. A shell that forks and returns
    leaves its children in the same group, and a check on the leader alone
    would call that finished while a microphone is still open.
    """
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 1:
        return proc.poll() is None
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        # EPERM: somebody is in there, it is just not ours to signal.
        return True


def stop(proc, grace: float = GRACE) -> None:
    """Ask a process tree to go, then make it. Reaps the leader either way."""
    signal_tree(proc, signal.SIGTERM)
    if _settled(proc, grace):
        return
    kill_tree(proc, grace=grace)


def kill_tree(proc, grace: float = GRACE) -> None:
    """SIGKILL the whole group, whatever the leader is doing."""
    signal_tree(proc, signal.SIGKILL)
    _settled(proc, grace)


def _settled(proc, grace: float) -> bool:
    """Wait for the group to empty, reaping the leader as soon as it exits.

    The leader is polled first on every pass: an unreaped zombie is still a
    member of its process group, so skipping that would mean waiting out the
    whole grace period for a child that died immediately.
    """
    deadline = time.monotonic() + grace
    while True:
        proc.poll()
        if not group_alive(proc):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


_watched: list = []


def background(name: str, args=(), *, deadline: float = BACKGROUND_DEADLINE, stdin: bytes | None = None):
    """Start something nobody waits for, and hold it to a deadline anyway.

    Two users: notifications, where the round trip to the notification server
    is not worth blocking the loop for, and the clipboard holder, which has to
    outlive this call by design. Both are processes this daemon started, so
    both are tracked and both are killed if they outstay their deadline.
    """
    proc = spawn(
        name,
        args,
        stdout=subprocess.DEVNULL,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        bufsize=0,
    )
    if stdin:
        _feed(proc, stdin)
    if len(_watched) >= BACKGROUND_MAX:
        oldest, _ = _watched.pop(0)
        kill_tree(oldest)
    _watched.append((proc, time.monotonic() + deadline))
    return proc


def _feed(proc, payload: bytes, timeout: float = 2.0) -> None:
    """Hand a child its input without ever blocking on it."""
    handle = proc.stdin
    os.set_blocking(handle.fileno(), False)
    view = memoryview(payload)
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    selector.register(handle, selectors.EVENT_WRITE)
    try:
        while view:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if not selector.select(timeout=min(left, 0.25)):
                continue
            try:
                view = view[os.write(handle.fileno(), view) :]
            except BlockingIOError:
                continue
            except OSError:
                break
    finally:
        selector.close()
        try:
            handle.close()
        except OSError:
            pass


def reap() -> int:
    """Collect finished background children, kill the overdue ones.

    Called once a second from the daemon's tick. Returns how many were killed,
    which is zero every time on a healthy machine.
    """
    killed = 0
    now = time.monotonic()
    for entry in list(_watched):
        proc, due = entry
        if proc.poll() is not None:
            _watched.remove(entry)
            continue
        if now >= due:
            kill_tree(proc)
            _watched.remove(entry)
            killed += 1
    return killed
