"""pw-record, three ways: the microphone, the speakers, and stopping.

One helper because the capture paths differ only in their PipeWire properties.
Everything is raw f32 16 kHz mono, which is both what the model wants and what
makes a byte count a sample count: 64000 bytes is one second.

The pipe is unbuffered and non-blocking, so the daemon's one loop can read it
with os.read whenever the selector says there is something there.
"""

from __future__ import annotations

import array
import json
import os
import select
import signal
import subprocess
import time

from . import RATE, proc as runner

# 20 ms, so a level frame on the HUD is current rather than a fifth of a second
# old.
LATENCY = "20ms"

# The microphone. media.role Communication is what marks this a call rather
# than music. node.name is also how detect.py knows not to mistake us for
# another app taking the mic.
MIC = {"node.name": "omayap-mic", "media.role": "Communication"}

# Everything the speakers are playing. stream.capture.sink follows the default
# sink rather than pinning one monitor, so swapping to headphones mid-call
# keeps recording.
SYSTEM = {"node.name": "omayap-system", "stream.capture.sink": True}

BYTES_PER_SAMPLE = 4
CHUNK = 1 << 16

# The last read after a capture is asked to stop. pw-record flushes a fraction
# of a second; a descendant holding the pipe open flushes nothing, forever, so
# the drain runs against a clock and a ceiling instead of against EOF.
DRAIN_S = 2.0
DRAIN_MAX = 16 << 20


def record(props: dict, target: str | None = None) -> subprocess.Popen:
    """Start a capture. Its stdout is a non-blocking pipe of f32 samples.

    pw-record is resolved and checked on every start rather than found on
    PATH, and gets a session of its own so stopping it cannot leave anything
    behind.
    """
    command = [
        "--raw",
        "--format=f32",
        f"--rate={RATE}",
        "--channels=1",
        f"--latency={LATENCY}",
        "-P",
        json.dumps(props),
    ]
    if target:
        command.append(f"--target={target}")
    command.append("-")
    process = runner.spawn("pw-record", command, bufsize=0)
    os.set_blocking(process.stdout.fileno(), False)
    return process


def read(proc: subprocess.Popen) -> bytes | None:
    """Whatever is in the pipe. b"" means the capture ended, None means idle."""
    try:
        return os.read(proc.stdout.fileno(), CHUNK)
    except BlockingIOError:
        return None
    except OSError:
        return b""


def stop(proc: subprocess.Popen) -> bytes:
    """Terminate a capture and return whatever was still in the pipe.

    The pipe stays non-blocking and the drain has a deadline: the last word on
    when this returns belongs to this process, not to whatever is holding the
    write end. A recording that loses its final 20 ms is a recording; a daemon
    blocked on a read is a dictation key that stops working.
    """
    if proc.poll() is None:
        runner.signal_tree(proc, signal.SIGTERM)
    fd = proc.stdout.fileno()
    rest = bytearray()
    deadline = time.monotonic() + DRAIN_S
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    while len(rest) < DRAIN_MAX:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        if not poller.poll(left * 1000):
            break
        try:
            chunk = os.read(fd, CHUNK)
        except BlockingIOError:
            continue
        except OSError:
            break
        if not chunk:
            break
        rest += chunk
    try:
        proc.stdout.close()
    except OSError:
        pass
    runner.stop(proc, grace=5.0)
    return bytes(rest)


def samples(raw: bytes) -> array.array:
    """Whole samples only: a short read can land mid-float."""
    whole = len(raw) - len(raw) % BYTES_PER_SAMPLE
    out = array.array("f")
    out.frombytes(raw[:whole])
    return out


def peak(raw: bytes) -> float:
    return max((abs(value) for value in samples(raw)), default=0.0)
