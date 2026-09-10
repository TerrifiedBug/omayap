"""Parakeet, resident. One model in memory, used by everything.

The whole design rests on this being loaded once and staying loaded: the model
is 126 MB of int8 weights that take half a second to read off disk and 65 ms
to run a 7-second clip through, so the daemon holds one and dictation is a
signal away from a decode. Nothing here starts a thread of its own; four
compute threads inside sherpa is all the parallelism there is.

Audio is f32 16 kHz mono everywhere in omayap, which is exactly what sherpa
wants, so an `array("f")` from `pw-record`'s raw bytes goes in without a copy
or a conversion.
"""

from __future__ import annotations

import array
from pathlib import Path

import sherpa_onnx

from . import MIN_SAMPLES, MODEL, MODELS, RATE

NUM_THREADS = 4
MODEL_DIR = MODELS / MODEL
VAD_MODEL = MODELS / "silero_vad.onnx"

# Silero's frame size. Also the read size when streaming a track through the
# VAD, so a window is one file read and one accept_waveform.
WINDOW = 512

# A 1.0 s gap ends a segment — yap's rule, and the reason transcripts break at
# sentences rather than mid-clause.
SILENCE_S = 1.0
MIN_SPEECH_S = 0.25
MAX_SPEECH_S = 30.0

# The VAD holds unconsumed speech in memory, so this is the cap on how far a
# transcription can fall behind the file it is reading.
VAD_BUFFER_S = 60


class Engine:
    """A loaded recognizer. Construction is the slow part; text() is not."""

    def __init__(self) -> None:
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(MODEL_DIR / "model.int8.onnx"),
            tokens=str(MODEL_DIR / "tokens.txt"),
            num_threads=NUM_THREADS,
            sample_rate=RATE,
            feature_dim=80,
            decoding_method="greedy_search",
        )
        # The first decode allocates the compute buffers, so it costs several
        # times what the second one does. Spend it here, before the user is
        # waiting on it.
        self.text(array.array("f", bytes(4 * (RATE // 2))))

    def text(self, samples) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(RATE, samples)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()


def vad():
    """A fresh detector. One per track: it carries state across accepts."""
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(VAD_MODEL)
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = SILENCE_S
    config.silero_vad.min_speech_duration = MIN_SPEECH_S
    config.silero_vad.max_speech_duration = MAX_SPEECH_S
    config.silero_vad.window_size = WINDOW
    config.sample_rate = RATE
    return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=VAD_BUFFER_S)


def segments(engine: Engine, detector, path: Path, speaker: str, offset_ms: int = 0):
    """Cut one track into speech segments and decode each of them.

    Streamed rather than loaded: a two-hour meeting is 460 MB per track, and
    the VAD only ever holds the last minute of it.

    `offset_ms` is the track's own start relative to the session's, so both
    tracks land on one timeline.
    """
    found: list = []

    def drain() -> None:
        while not detector.empty():
            segment = detector.front
            start = offset_ms + segment.start * 1000 // RATE
            end = offset_ms + (segment.start + len(segment.samples)) * 1000 // RATE
            text = engine.text(segment.samples)
            detector.pop()
            if text:
                found.append(
                    {
                        "start_ms": int(start),
                        "end_ms": int(end),
                        "speaker": speaker,
                        "text": text,
                    }
                )

    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(WINDOW * 4)
            if len(chunk) < WINDOW * 4:
                break
            window = array.array("f")
            window.frombytes(chunk)
            detector.accept_waveform(window)
            drain()

    # Speech still open at the end of the file is a segment too — usually the
    # last thing anyone said.
    detector.flush()
    drain()
    return found
