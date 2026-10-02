"""
Voice pipeline: audio frames -> gate -> transcription -> mode routing.

Excerpt from the voice client (`aihylu.py`), trimmed for publication.

One consumer thread drains a queue fed by the hotkey handler, so the recording
path never blocks on model inference. Transcription is local (faster-whisper,
int8 on CPU) — audio does not leave the machine; only the resulting text does,
and only for the modes that need a model at all.
"""

import os
import tempfile
import time

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16_000
RMS_THRESHOLD = 0.004   # below this the clip is room tone, not speech
MIN_SECONDS = 0.5       # below this the user tapped the chord by accident


def pipeline_worker(queue, cfg, ui, route):
    """Consume recordings forever. `route(mode, text, screenshot)` does the work."""
    from faster_whisper import WhisperModel

    model = WhisperModel(cfg["whisper_model"], device="cpu", compute_type="int8")
    ui("status", "ready")

    while True:
        item = queue.get()
        if item is None:
            break

        frames, mode, screenshot = item["frames"], item["mode"], item["screenshot"]

        try:
            # Screen modes capture the screenshot on a parallel thread the moment
            # recording starts — otherwise the shot would show the UI we just
            # opened, not what the user was looking at. It lands in ~0.3 s while
            # recording runs for seconds, so this wait is almost always a no-op.
            if mode in ("screen_qa", "agent") and screenshot is None:
                screenshot = _await_screenshot(timeout_s=3.0)

            # ── signal gate ───────────────────────────────────────────────────
            audio = np.concatenate(frames, axis=0).flatten().astype("float32")
            duration = len(audio) / SAMPLE_RATE
            rms = float(np.sqrt(np.mean(audio ** 2)))

            if duration < MIN_SECONDS:
                ui("error", "Recording too short")
                continue
            if rms < RMS_THRESHOLD:
                ui("error", f"Too quiet (rms={rms:.4f})")
                continue

            # ── transcription ─────────────────────────────────────────────────
            ui("status", "transcribing")
            text = (
                _native_stt(audio)            # quick mode: OS recogniser, no LLM
                if mode == "quick"
                else _whisper(model, audio)   # everything else: local Whisper
            )
            if not text:
                ui("error", "Nothing recognised")
                continue

            ui("transcribed", {"text": text, "mode": mode})

            # ── routing ───────────────────────────────────────────────────────
            # quick/dictate never reach a model; structure/screen_qa/agent do,
            # each with its own model tier. Cheap work stays cheap.
            route(mode, text, screenshot)

        except Exception as e:
            ui("error", str(e))
        finally:
            ui("status", "ready")


def _whisper(model, audio):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    sf.write(wav_path, audio, SAMPLE_RATE)
    try:
        segments, _ = model.transcribe(
            wav_path,
            beam_size=1,
            # Priming the decoder with domain words cuts misrecognition of
            # product and tool names that Whisper has never seen.
            initial_prompt="Voice assistant command.",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return " ".join(s.text for s in segments).strip()
    finally:
        os.unlink(wav_path)


def _await_screenshot(timeout_s):
    """Placeholder for the shared slot written by the capture thread."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        shot = current_screenshot()
        if shot is not None:
            return shot
        time.sleep(0.1)
    return None


def current_screenshot():  # pragma: no cover - wired to the capture thread
    return None


def _native_stt(audio):  # pragma: no cover - macOS SFSpeechRecognizer wrapper
    """Apple's on-device recogniser: no Whisper load, no network, lowest latency."""
    raise NotImplementedError
