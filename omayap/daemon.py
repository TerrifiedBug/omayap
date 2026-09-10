"""The daemon: one loop, one model, no threads, no sockets.

Everything the user does arrives as a signal, because the hot path is a key
being held down and the cheapest possible thing between the key and the
recording is `kill -USR1`. There is no server socket to connect to, no JSON to
parse and no Python to start: `bin/omayap press` is a bash builtin away from
this process already having the model in memory.

The loop is `selectors` over four kinds of fd — the signal wakeup pipe, a
dictation capture, a meeting session's two captures, and `pactl subscribe` —
plus a one-second tick that drives every timer. Single-threaded on purpose: a
decode blocks the loop for a few hundred milliseconds, and the alternative is
locks around the model for no benefit anyone can see.

State lands in $XDG_RUNTIME_DIR/omayap/state as one line of JSON, rewritten in
place rather than replaced, so an inotify watch on it survives.
"""

from __future__ import annotations

import ctypes
import fcntl
import functools
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import MIN_SAMPLES, capture, config, detect, run_dir
from . import session as vault

# The client protocol, in full.
PRESS = signal.SIGUSR1
RELEASE = signal.SIGUSR2
TOGGLE_DICTATION = signal.SIGRTMIN
TOGGLE_RECORDING = signal.SIGRTMIN + 1

GLYPH = "󰍬"  # nf-md-microphone

# Timers, all in seconds.
TICK = 1.0
LEVEL_INTERVAL = 0.05
CONFIG_POLL = 5.0
STALL_POLL = 15.0
STALL_AFTER = 45.0
# Two polls before believing a mic was taken, which is what yap settled on:
# clients open and close streams while they negotiate a call.
DETECT_DEBOUNCE = 2.0
# A call is not over because a client dropped its stream for a moment.
DETECT_END_GRACE = 16.0
# Missed-event safety: pactl does not always say when a stream goes idle.
DETECT_POLL = 30.0

CLI = Path(__file__).resolve().parent.parent / "bin" / "omayap"


def trim_heap() -> None:
    """Hand the decoder's freed pages back to the kernel.

    ONNX Runtime's arena reaches a steady state a few decodes in and glibc
    keeps every page it ever asked for, so the daemon sits ~50 MB above what
    it is using. malloc_trim costs microseconds, the pages do not come back on
    the next decode, and this process is resident for the whole session.
    """
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def warn(message: str) -> None:
    """The journal is the log.

    It never carries content: not the transcribed words, not a window title,
    and not a session path, because a path ends in the meeting's name. What it
    carries is timing, counts, app names and errors, which is what a bug report
    needs and all it needs.
    """
    print(message, file=sys.stderr, flush=True)


def notify(headline: str, body: str = "", urgency: str = "low", timeout=None, click=None, want_id: bool = False):
    """Fire a toast. With `want_id`, wait for its id so it can be withdrawn.

    Waiting costs a D-Bus round trip, so only the notifications that can go
    stale ask for it: an offer to record a call is a lie the moment the call
    ends, and leaving it up for its full two minutes is worse than never
    having shown it.
    """
    options = ["-u", urgency, "-g", GLYPH]
    if timeout:
        options += ["-t", str(int(timeout))]
    if want_id:
        options.append("-p")
    command = ["omarchy-notification-send", *options, headline, body]
    if click:
        # --exec is last and takes the argv as separate words, by contract.
        command += ["--exec", *click]
    try:
        if not want_id:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        done = subprocess.run(command, capture_output=True, text=True, timeout=5)
        return int(done.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        warn(f"notification failed: {error}")
        return None


def withdraw(notification_id) -> None:
    """Take a toast off the screen. omarchy-notification-send only sends."""
    if not notification_id:
        return
    try:
        subprocess.Popen(
            [
                "busctl", "--user", "call",
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "CloseNotification", "u", str(notification_id),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        warn(f"cannot withdraw notification: {error}")


class Session:
    """One meeting recording: two captures writing two files."""

    def __init__(self, directory: Path, started: datetime, title, app, auto: bool):
        self.dir = directory
        self.started = started
        self.title = title
        self.app = app
        self.auto = auto
        self.procs: dict = {}
        self.files: dict = {}
        # Monotonic time of each track's first sample. The difference between
        # them is the only honest way to line the two files up: pw-record does
        # not start both streams on the same millisecond.
        self.first: dict = {}
        self.size: dict = {}
        self.grew: dict = {}
        self.stalled: set = set()


class Daemon:
    def __init__(self) -> None:
        self.run = run_dir()
        self.selector = selectors.DefaultSelector()
        self.running = True
        self.lock_fd = None
        self.signal_fd = None

        self.engine = None
        self.model = "loading"

        self.dictation = "idle"
        self.mic = None
        self.buffer = bytearray()
        self.levels = None
        self.level_users: set = set()
        self.level_at = 0.0

        self.session = None
        self.stall_at = 0.0

        self.queue: deque = deque()
        self.child = None
        self.child_dir = None

        self.subscriber = None
        self.subscriber_buffer = b""
        self.in_meeting = False
        self.pending = None
        self.pending_toast = None
        self.evaluate_at = None
        self.end_at = None
        self.repoll_at = None
        self.config_at = 0.0

    # ---- lifecycle ------------------------------------------------------

    def main(self) -> int:
        if not self.lock():
            return 1
        (self.run / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        # Handlers first: a signal that arrives while the model is loading has
        # to be a "still loading" toast, not a default-action kill.
        self.install_signals()
        self.write_state()

        from .engine import Engine

        started = time.monotonic()
        try:
            self.engine = Engine()
        except Exception as error:
            self.model = "failed"
            self.write_state()
            warn(f"model failed to load: {error}")
            notify("omayap", "speech model failed to load — run setup.sh", "critical")
            return 1
        self.model = "ready"
        self.write_state()
        warn(f"model ready in {int((time.monotonic() - started) * 1000)}ms")

        self.resume_pending()
        self.apply_config()

        while self.running:
            for key, _ in self.selector.select(timeout=TICK):
                key.data()
            self.tick()
        return 0

    def lock(self) -> bool:
        """One daemon owns the hotkey. A stale one is replaced, not tolerated."""
        fd = os.open(str(self.run / "daemon.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        if self.take(fd):
            return True
        other = self.other_pid()
        warn(f"another omayap daemon has the hotkey (pid {other}) — replacing it")
        if not other:
            return False
        for sig, tries in ((signal.SIGTERM, 100), (signal.SIGKILL, 20)):
            try:
                os.kill(other, sig)
            except OSError:
                pass
            for _ in range(tries):
                time.sleep(0.05)
                if self.take(fd):
                    return True
        warn(f"pid {other} will not release the lock — giving up")
        return False

    def take(self, fd) -> bool:
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        # Never closed: closing any fd on the file drops the lock.
        self.lock_fd = fd
        return True

    def other_pid(self):
        try:
            return int((self.run / "pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def install_signals(self) -> None:
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        # The C-level handler writes the signal number to the pipe, so the
        # Python handler has nothing to do and the loop stays the only place
        # anything happens.
        signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
        for sig in (
            PRESS,
            RELEASE,
            TOGGLE_DICTATION,
            TOGGLE_RECORDING,
            signal.SIGTERM,
            signal.SIGINT,
        ):
            signal.signal(sig, lambda *_: None)
        self.signal_fd = read_fd
        self.selector.register(read_fd, selectors.EVENT_READ, self.on_signal)

    def on_signal(self) -> None:
        try:
            raw = os.read(self.signal_fd, 64)
        except (BlockingIOError, OSError):
            return
        for number in raw:
            if number == PRESS:
                self.press()
            elif number == RELEASE:
                self.release()
            elif number == TOGGLE_DICTATION:
                self.toggle_dictation()
            elif number == TOGGLE_RECORDING:
                self.toggle_recording()
            elif number in (signal.SIGTERM, signal.SIGINT):
                self.shutdown()

    def shutdown(self) -> None:
        self.running = False
        if self.mic:
            self.unwatch(self.mic)
            capture.stop(self.mic)
            self.mic = None
        self.close_levels("dictation")
        self.close_levels("session")
        self.dictation = "idle"
        # meta.json now means the next start picks the session up rather than
        # finding audio with nothing to say what it is.
        if self.session:
            self.stop_session()
        self.clear_pending()
        if self.child:
            self.child.terminate()
        (self.run / "pid").unlink(missing_ok=True)
        self.write_state()
        warn("stopped")

    # ---- state ----------------------------------------------------------

    def snapshot(self) -> dict:
        session = self.session
        return {
            "dictation": self.dictation,
            "model": self.model,
            "recording": None
            if not session
            else {
                "dir": str(session.dir),
                "started": vault.rfc3339_utc(session.started),
                "title": session.title,
                "app": session.app,
                "auto": session.auto,
            },
            "pending": self.pending,
            "transcribing": str(self.child_dir) if self.child_dir else None,
        }

    def write_state(self) -> None:
        # Truncated and rewritten, never replaced: the QML side watches this
        # exact inode and a rename would leave it watching a deleted file.
        try:
            with open(self.run / "state", "w", encoding="utf-8") as handle:
                handle.write(json.dumps(self.snapshot()) + "\n")
        except OSError as error:
            warn(f"cannot write state: {error}")

    # ---- dictation ------------------------------------------------------

    def press(self) -> None:
        if self.model != "ready":
            notify("omayap", "model still loading — try again")
            return
        if self.dictation == "listening":
            # A second press is a release: the alternative is a recording the
            # user cannot stop because the release was swallowed.
            self.release()
            return
        if self.dictation == "transcribing":
            return
        self.buffer = bytearray()
        self.dictation = "listening"
        # Feedback before the capture: the HUD has to appear on key-down, and
        # spawning pw-record takes long enough to notice.
        self.write_state()
        try:
            self.mic = capture.record(capture.MIC)
        except OSError as error:
            self.dictation = "idle"
            self.write_state()
            notify("omayap — dictation failed", str(error), "critical")
            return
        self.watch(self.mic, self.on_dictation_audio)
        self.open_levels("dictation")

    def on_dictation_audio(self) -> None:
        if not self.mic:
            return
        chunk = capture.read(self.mic)
        if chunk is None:
            return
        if chunk == b"":
            # pw-record exited on its own: the device went away, or it never
            # started. The fd stays readable forever at EOF, so leaving it
            # registered would spin the loop at 100% CPU until release.
            warn("microphone capture ended on its own")
            short = len(self.buffer) < MIN_SAMPLES * capture.BYTES_PER_SAMPLE
            self.release()
            if short:
                notify("omayap — dictation failed", "microphone is not available", "critical")
            return
        self.buffer += chunk
        self.emit_level(chunk)

    def release(self) -> None:
        if self.dictation != "listening" or not self.mic:
            return
        self.unwatch(self.mic)
        self.buffer += capture.stop(self.mic)
        self.mic = None
        self.close_levels("dictation")
        self.dictation = "transcribing"
        self.write_state()

        samples = capture.samples(bytes(self.buffer))
        self.buffer = bytearray()
        if len(samples) < MIN_SAMPLES:
            self.dictation = "idle"
            self.write_state()
            return

        start = time.monotonic()
        try:
            text = self.engine.text(samples)
        except Exception as error:
            self.dictation = "idle"
            self.write_state()
            warn(f"decode failed: {error}")
            notify("omayap — dictation failed", str(error), "critical")
            return
        decoded = time.monotonic()
        if text:
            route = self.deliver(text)
            done = time.monotonic()
            warn(
                f"→ {int((done - start) * 1000)}ms "
                f"({int((decoded - start) * 1000)}ms model + "
                f"{int((done - decoded) * 1000)}ms {route}) · {len(text)} chars"
            )
        self.dictation = "idle"
        self.write_state()
        trim_heap()

    def deliver(self, text: str) -> str:
        """Type it where the cursor is, or fall back to the clipboard.

        With `newline_after_dictation` the text is followed by Return, which
        turns a dictated line into a sent message or a run command. Only after
        a successful type: a transcript on the clipboard has nothing to submit.
        """
        try:
            typed = subprocess.run(
                ["wtype", "-"], input=text.encode(), stderr=subprocess.DEVNULL
            )
        except OSError as error:
            warn(f"wtype missing: {error}")
            typed = None
        if typed is not None and typed.returncode == 0:
            if config.get("newline_after_dictation"):
                self.submit()
            return "typed"
        try:
            subprocess.run(
                ["wl-copy"], input=text.encode(), stderr=subprocess.DEVNULL, check=False
            )
        except OSError as error:
            warn(f"wl-copy failed: {error}")
            return "lost"
        notify("omayap", "No text field focused — transcript copied to clipboard")
        return "clipboard"

    def submit(self) -> None:
        """Return, on its own keystroke.

        Separate from the text because wtype reads the words from stdin, and
        because a failure to press Enter must not lose what was already typed.
        """
        try:
            subprocess.run(["wtype", "-k", "Return"], stderr=subprocess.DEVNULL)
        except OSError as error:
            warn(f"newline failed: {error}")

    def toggle_dictation(self) -> None:
        if self.dictation == "listening":
            self.release()
        else:
            self.press()

    # ---- the level stream ----------------------------------------------

    def open_levels(self, user: str) -> None:
        """A FIFO, so nothing is written unless the HUD is reading it.

        Both dictation and a meeting recording feed the meter, and a press
        during a recording must not close the pipe out from under the session,
        so the writers are counted.
        """
        self.level_users.add(user)
        if self.levels is not None:
            return
        path = self.run / "levels"
        try:
            if not path.is_fifo():
                path.unlink(missing_ok=True)
                os.mkfifo(path, 0o600)
            self.levels = os.open(str(path), os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            # ENXIO: nobody is listening. That is the normal case when the HUD
            # is disabled, and it costs nothing.
            self.levels = None
        self.level_at = 0.0

    def emit_level(self, chunk: bytes) -> None:
        now = time.monotonic()
        if self.levels is None or now - self.level_at < LEVEL_INTERVAL:
            return
        self.level_at = now
        line = json.dumps({"peak": round(capture.peak(chunk), 4)}) + "\n"
        try:
            os.write(self.levels, line.encode())
        except OSError:
            # The reader went away. Drop the pipe; the next press or recording
            # opens it again if something is listening by then.
            self.close_levels(force=True)

    def close_levels(self, user: str | None = None, force: bool = False) -> None:
        if user:
            self.level_users.discard(user)
        if force:
            self.level_users.clear()
        if self.levels is None or self.level_users:
            return
        try:
            os.close(self.levels)
        except OSError:
            pass
        self.levels = None

    # ---- meeting sessions ----------------------------------------------

    def start_session(self, title=None, app=None, auto: bool = False) -> None:
        if self.session:
            return
        root = config.recordings_dir()
        started = datetime.now(timezone.utc)
        try:
            root.mkdir(parents=True, exist_ok=True)
            directory = vault.session_dir(root, started, title)
        except OSError as error:
            notify("omayap — recording failed", str(error), "critical")
            return

        session = Session(
            directory, started, vault.sanitize_title(title) or None, app, auto
        )
        try:
            # System first: it is the track that never fails, so if the mic is
            # going to fail it does so with something already running to stop.
            for track, props in (("system", capture.SYSTEM), ("mic", capture.MIC)):
                session.procs[track] = capture.record(props)
                session.files[track] = open(directory / vault.FILES[track], "wb")
        except OSError as error:
            self.abandon(session, str(error))
            return
        # pw-record fails by exiting, not by refusing to start.
        time.sleep(0.08)
        for track, proc in session.procs.items():
            if proc.poll() is None:
                continue
            if track == "mic":
                self.abandon(session, "microphone is not available")
                return
            warn(f"{track} track failed to start — recording microphone only")
            notify("omayap", "system audio is not available — recording mic only")

        now = time.monotonic()
        for track, proc in session.procs.items():
            session.grew[track] = now
            session.size[track] = 0
            if proc.poll() is None:
                self.watch(proc, functools.partial(self.on_session_audio, track))
        self.session = session
        self.stall_at = now
        # The HUD shows the mic while a meeting records, so it needs the levels.
        self.open_levels("session")
        self.write_state()
        warn(f"● recording {vault.stamp(started)}")

    def abandon(self, session: Session, reason: str) -> None:
        for proc in session.procs.values():
            capture.stop(proc)
        for handle in session.files.values():
            handle.close()
        for track in vault.FILES.values():
            (session.dir / track).unlink(missing_ok=True)
        try:
            session.dir.rmdir()
        except OSError:
            pass
        warn(f"recording failed: {reason}")
        notify("omayap — recording failed", reason, "critical")

    def on_session_audio(self, track: str) -> None:
        session = self.session
        if not session or track not in session.procs:
            return
        proc = session.procs[track]
        chunk = capture.read(proc)
        if chunk is None:
            return
        if chunk == b"":
            self.unwatch(proc)
            return
        if track not in session.first:
            session.first[track] = time.monotonic()
        session.files[track].write(chunk)
        if track == "mic":
            self.emit_level(chunk)

    def stop_session(self) -> None:
        session = self.session
        if not session:
            return
        self.session = None
        self.close_levels("session")
        ended = datetime.now(timezone.utc)
        for track, proc in session.procs.items():
            self.unwatch(proc)
            rest = capture.stop(proc)
            if rest:
                session.first.setdefault(track, time.monotonic())
                session.files[track].write(rest)
            session.files[track].close()

        earliest = min(session.first.values()) if session.first else 0.0
        offsets = {
            track: int(round((at - earliest) * 1000))
            for track, at in session.first.items()
        }
        vault.write_meta(
            session.dir, session.started, ended, offsets, session.app, session.title
        )
        elapsed = int((ended - session.started).total_seconds() * 1000)
        warn(f"○ stopped · {vault.clock(elapsed)} · {vault.stamp(session.started)}")
        vault.log(session.dir, f"recorded {vault.clock(elapsed)}")
        self.queue.append(session.dir)
        self.write_state()
        self.pump()
        trim_heap()

    def watch_stalls(self) -> None:
        session = self.session
        if not session:
            return
        now = time.monotonic()
        for track, handle in session.files.items():
            size = handle.tell()
            if size != session.size.get(track):
                session.size[track] = size
                session.grew[track] = now
                if track in session.stalled:
                    session.stalled.discard(track)
                    notify("omayap", f"{track} track is recording again")
                continue
            if now - session.grew.get(track, now) < STALL_AFTER:
                continue
            if track in session.stalled:
                continue
            session.stalled.add(track)
            warn(f"{track} track stalled")
            notify("omayap", f"{track} track stalled — no audio for {int(STALL_AFTER)}s")

    def toggle_recording(self) -> None:
        if self.session:
            self.stop_session()
            return
        if self.pending:
            offer = self.pending
            self.clear_pending()
            self.start_session(offer.get("title"), offer.get("app"), auto=True)
        else:
            self.start_session(None, None, auto=False)
        # No toast: the HUD card carries the clock and the stop button, and a
        # notification would only push the user's own notifications down.

    # ---- transcription queue -------------------------------------------

    def resume_pending(self) -> None:
        found = vault.pending(config.recordings_dir())
        if not found:
            return
        warn(f"resuming {len(found)} untranscribed session(s)")
        self.queue.extend(found)
        self.pump()

    def pump(self) -> None:
        """At most one transcription at a time; the daemon's model stays hot."""
        if self.child or not self.queue or not self.running:
            return
        directory = self.queue.popleft()
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = (
            root
            if not env.get("PYTHONPATH")
            else root + os.pathsep + env["PYTHONPATH"]
        )
        try:
            self.child = subprocess.Popen(
                [sys.executable, "-m", "omayap", "transcribe", str(directory)], env=env
            )
        except OSError as error:
            warn(f"cannot start transcription: {error}")
            return
        self.child_dir = directory
        self.write_state()

    def poll_child(self) -> None:
        if not self.child:
            return
        code = self.child.poll()
        if code is None:
            return
        directory = self.child_dir
        self.child = None
        self.child_dir = None
        if code == 0:
            notify("omayap — transcript ready", directory.name)  # on screen only
        else:
            warn(f"transcription failed for {vault.stamp_of(directory)}")
            notify(
                "omayap — transcription failed",
                f"{directory.name} — see transcribe.log",
                "normal",
            )
        self.write_state()
        self.pump()

    # ---- meeting detection ---------------------------------------------

    def apply_config(self) -> None:
        wanted = bool(config.get("meeting_detection"))
        if wanted and not self.subscriber:
            self.detector_start()
        elif not wanted and self.subscriber:
            self.detector_stop()

    def detector_start(self) -> None:
        try:
            self.subscriber = subprocess.Popen(
                ["pactl", "subscribe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError as error:
            warn(f"meeting detection unavailable: {error}")
            return
        os.set_blocking(self.subscriber.stdout.fileno(), False)
        self.subscriber_buffer = b""
        self.selector.register(
            self.subscriber.stdout.fileno(), selectors.EVENT_READ, self.on_subscribe
        )
        warn("meeting detection on")
        self.evaluate_at = time.monotonic() + 0.5

    def detector_stop(self) -> None:
        proc = self.subscriber
        self.subscriber = None
        self.evaluate_at = None
        self.end_at = None
        self.repoll_at = None
        self.in_meeting = False
        self.clear_pending()
        if not proc:
            return
        try:
            self.selector.unregister(proc.stdout.fileno())
        except (KeyError, ValueError, OSError):
            pass
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        warn("meeting detection off")

    def on_subscribe(self) -> None:
        proc = self.subscriber
        if not proc:
            return
        try:
            chunk = os.read(proc.stdout.fileno(), 4096)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if chunk == b"":
            # pactl died; the 5 s config poll restarts it.
            self.detector_stop_partial()
            return
        self.subscriber_buffer += chunk
        lines = self.subscriber_buffer.split(b"\n")
        self.subscriber_buffer = lines.pop()
        for line in lines:
            if b"source-output" in line:
                # Coalesced: a client opening a stream produces a burst.
                self.evaluate_at = time.monotonic() + DETECT_DEBOUNCE
                return

    def detector_stop_partial(self) -> None:
        proc = self.subscriber
        self.subscriber = None
        if not proc:
            return
        try:
            self.selector.unregister(proc.stdout.fileno())
        except (KeyError, ValueError, OSError):
            pass
        proc.wait(timeout=2)
        warn("pactl subscribe exited — will restart")

    def clear_pending(self) -> None:
        """Drop the offer and take its toast down with it.

        The offer is only true while the other app still holds the microphone:
        it ends when the call does, when the user accepts it, when detection is
        turned off, and when the daemon stops.
        """
        withdraw(self.pending_toast)
        self.pending_toast = None
        if self.pending is None:
            return
        self.pending = None
        self.write_state()

    def evaluate(self) -> None:
        self.evaluate_at = None
        settings = config.load()
        if not settings.get("meeting_detection"):
            return
        live = detect.capturing(
            self.pipewire_dump(), settings.get("meeting_excluded_apps") or []
        )
        now = time.monotonic()
        if not live:
            self.repoll_at = None
            self.clear_pending()
            if self.in_meeting and self.end_at is None:
                self.end_at = now + DETECT_END_GRACE
            return

        self.end_at = None
        self.repoll_at = now + DETECT_POLL
        if self.in_meeting:
            return
        self.in_meeting = True
        stream = live[0]
        app = stream["app"]
        # The window is found by the process, not by the app's own name.
        title = detect.window_title(
            stream["pid"], self.hyprland_windows(), stream["binary"]
        )
        # The title goes on screen and into meta.json, never into the journal.
        warn(f"◆ {app} is in use" + (" · window title found" if title else ""))
        if self.session:
            # A recording started by hand is never replaced by a detected one.
            return
        if settings.get("meeting_auto_record"):
            self.start_session(title, app, auto=True)
            if self.session:
                notify(
                    f"Recording {app} call",
                    title or "Click to stop",
                    click=[str(CLI), "record"],
                )
            return
        self.pending = {"app": app, "title": title}
        self.write_state()
        self.pending_toast = notify(
            f"{app} is in a call",
            f"{title or 'Record this meeting?'} · click to record",
            timeout=120000,
            click=[str(CLI), "record"],
            want_id=True,
        )

    def end_meeting(self) -> None:
        self.end_at = None
        self.in_meeting = False
        self.clear_pending()
        warn("◇ call ended")
        # A hand-started recording outlives the call it happened to overlap.
        if self.session and self.session.auto:
            self.stop_session()
        else:
            self.write_state()

    def pipewire_dump(self):
        try:
            done = subprocess.run(
                ["pw-dump"], capture_output=True, text=True, timeout=5
            )
            return json.loads(done.stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            warn(f"pw-dump failed: {error}")
            return []

    def hyprland_windows(self):
        """Every open window, or an empty list.

        Retried without HYPRLAND_INSTANCE_SIGNATURE because the daemon
        outlives the compositor: if Hyprland restarts, the signature this
        process inherited names a socket that is gone, and hyprctl finds the
        live instance on its own when the variable is absent.
        """
        last = None
        for env in (None, self.hyprland_fallback_env()):
            if env is None and "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ:
                continue
            try:
                done = subprocess.run(
                    ["hyprctl", "clients", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=env,
                )
                return json.loads(done.stdout)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                last = error
        warn(f"hyprctl failed: {last}")
        return []

    def hyprland_fallback_env(self) -> dict:
        env = dict(os.environ)
        env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        return env

    # ---- the tick -------------------------------------------------------

    def tick(self) -> None:
        now = time.monotonic()
        if self.evaluate_at and now >= self.evaluate_at:
            self.evaluate()
        if self.end_at and now >= self.end_at:
            self.end_meeting()
        if self.repoll_at and now >= self.repoll_at:
            self.evaluate()
        if self.session and now - self.stall_at >= STALL_POLL:
            self.stall_at = now
            self.watch_stalls()
        if now - self.config_at >= CONFIG_POLL:
            self.config_at = now
            self.apply_config()
        self.poll_child()

    # ---- selector plumbing ---------------------------------------------

    def watch(self, proc, callback) -> None:
        self.selector.register(proc.stdout.fileno(), selectors.EVENT_READ, callback)

    def unwatch(self, proc) -> None:
        try:
            self.selector.unregister(proc.stdout.fileno())
        except (KeyError, ValueError, OSError):
            pass


def main(argv=()) -> int:
    return Daemon().main()
