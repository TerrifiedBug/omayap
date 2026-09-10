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
import subprocess

from . import RATE

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


def record(props: dict, target: str | None = None) -> subprocess.Popen:
    """Start a capture. Its stdout is a non-blocking pipe of f32 samples."""
    command = [
        "pw-record",
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
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
    )
    os.set_blocking(proc.stdout.fileno(), False)
    return proc


def read(proc: subprocess.Popen) -> bytes | None:
    """Whatever is in the pipe. b"" means the capture ended, None means idle."""
    try:
        return os.read(proc.stdout.fileno(), CHUNK)
    except BlockingIOError:
        return None
    except OSError:
        return b""


def stop(proc: subprocess.Popen) -> bytes:
    """Terminate a capture and return whatever was still in the pipe."""
    if proc.poll() is None:
        proc.terminate()
    fd = proc.stdout.fileno()
    rest = bytearray()
    try:
        os.set_blocking(fd, True)
        while True:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            rest += chunk
    except OSError:
        pass
    try:
        proc.stdout.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    return bytes(rest)


def samples(raw: bytes) -> array.array:
    """Whole samples only: a short read can land mid-float."""
    whole = len(raw) - len(raw) % BYTES_PER_SAMPLE
    out = array.array("f")
    out.frombytes(raw[:whole])
    return out


def peak(raw: bytes) -> float:
    return max((abs(value) for value in samples(raw)), default=0.0)
