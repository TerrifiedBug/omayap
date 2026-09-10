"""`python -m omayap <subcommand>` — everything that is not a signal.

The bash client handles press, release and the two toggles by signalling the
daemon directly, so nothing on the hot path ever reaches this file. What is
left is the daemon itself, one-shot transcription, writing a config key, and a
benchmark for the one number worth knowing.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import wave
from pathlib import Path

from . import RATE, config


def bench(path: Path, runs: int = 5) -> int:
    """Decode one WAV repeatedly and report milliseconds and real-time factor."""
    from .engine import Engine

    with wave.open(str(path)) as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != RATE:
            print(
                f"omayap: bench needs mono {RATE} Hz audio, got "
                f"{handle.getnchannels()}ch {handle.getframerate()}Hz",
                file=sys.stderr,
            )
            return 2
        frames = handle.getnframes()
        raw = handle.readframes(frames)
        width = handle.getsampwidth()

    if width != 2:
        print(f"omayap: bench needs 16-bit audio, got {width * 8}-bit", file=sys.stderr)
        return 2

    import array

    pcm = array.array("h")
    pcm.frombytes(raw)
    samples = array.array("f", (value / 32768.0 for value in pcm))
    seconds = frames / RATE

    engine = Engine()
    engine.text(samples)  # warm the buffers this size of clip needs

    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        text = engine.text(samples)
        timings.append((time.perf_counter() - start) * 1000)

    median = statistics.median(timings)
    print(f"audio {path.name} ({seconds:.1f}s)")
    print(
        f"min {min(timings):.0f}ms  p50 {median:.0f}ms  max {max(timings):.0f}ms  "
        f"x{seconds * 1000 / median:.0f} realtime"
    )
    print(f"text {text}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omayap", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="run the dictation and recording daemon")

    transcribe_parser = sub.add_parser("transcribe", help="transcribe one session dir")
    transcribe_parser.add_argument("dir")

    config_parser = sub.add_parser("config", help="set one config key")
    config_parser.add_argument("key")
    config_parser.add_argument("value")

    bench_parser = sub.add_parser("bench", help="time the model on a 16 kHz mono WAV")
    bench_parser.add_argument("wav")

    args = parser.parse_args(argv)

    if args.command == "daemon":
        from . import daemon

        return daemon.main()
    if args.command == "transcribe":
        from . import transcribe

        return transcribe.main([args.dir])
    if args.command == "config":
        if args.key not in config.DEFAULTS:
            print(
                f"omayap: unknown setting {args.key!r} "
                f"(known: {', '.join(sorted(config.DEFAULTS))})",
                file=sys.stderr,
            )
            return 2
        return 0 if config.set(args.key, config.coerce(args.value)) else 1
    return bench(Path(args.wav).expanduser())


if __name__ == "__main__":
    sys.exit(main())
