"""
Voice I/O engine for the /voice command.

Wraps faster-whisper (STT), piper-tts (TTS), pyaudio (mic capture),
webrtcvad (voice-activity detection) and simpleaudio (playback).

All heavy operations are designed to run in a background thread via
``asyncio.to_thread()`` so they never block Textual's event loop.

Configuration
-------------
All values resolve through ``AppConfig`` (oli_bot/config.py) with the usual
precedence: settings.json > ``OLI_*`` environment variables > defaults.

OLI_VOICE_WHISPER_MODEL        Whisper model size: tiny | base (default) |
                               small | medium | large
OLI_VOICE_PIPER_MODEL          Path to the local Piper ONNX model file.
                               Default: en_US-lessac-medium.onnx
OLI_VOICE_SAMPLE_RATE          Mic sample rate (16000; required by WebRTC VAD)
OLI_VOICE_FRAME_DURATION_MS    VAD frame size (10 | 20 | 30; default 30)
OLI_VOICE_VAD_AGGRESSIVENESS   0-3; higher = more aggressive noise rejection
OLI_VOICE_SILENCE_TIMEOUT_MS   Stop after this much silence (default 800)
OLI_VOICE_MAX_RECORD_SECONDS   Hard cap on recording length (default 15)

Download a Piper model before first use:
  wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
  wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
  export OLI_VOICE_PIPER_MODEL=/path/to/en_US-lessac-medium.onnx
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import wave
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults sourced from AppConfig — overridable via OLI_VOICE_* env vars,
# .env, or settings.json (see docs/CONFIGURE.md).  Callers that hold their own
# AppConfig (e.g. the TUI) should pass explicit values to VoiceEngine() since
# the module-level `configs` singleton is an import-time snapshot.
# ---------------------------------------------------------------------------
from .config import configs as _configs

WHISPER_MODEL_SIZE   = _configs.voice_whisper_model
PIPER_MODEL_PATH     = _configs.voice_piper_model
SAMPLE_RATE          = _configs.voice_sample_rate
FRAME_DURATION_MS    = _configs.voice_frame_duration_ms
VAD_AGGRESSIVENESS   = _configs.voice_vad_aggressiveness
SILENCE_TIMEOUT_MS   = _configs.voice_silence_timeout_ms
MAX_RECORD_SECONDS   = _configs.voice_max_record_seconds


class VoiceEngine:
    """Lazy-loading, stateful voice I/O engine.

    Instantiate once per session.  Models are loaded on the first call to
    :meth:`load` (or automatically by the first voice-mode activation) so
    that startup latency stays near zero.

    All public methods that perform I/O are **blocking** — wrap them in
    ``asyncio.to_thread(engine.method, ...)`` inside async callers.
    """

    def __init__(
        self,
        whisper_model_size: str = WHISPER_MODEL_SIZE,
        piper_model_path: str = PIPER_MODEL_PATH,
        sample_rate: int = SAMPLE_RATE,
        frame_duration_ms: int = FRAME_DURATION_MS,
        vad_aggressiveness: int = VAD_AGGRESSIVENESS,
        silence_timeout_ms: int = SILENCE_TIMEOUT_MS,
        max_record_seconds: int = MAX_RECORD_SECONDS,
    ) -> None:
        self.whisper_model_size  = whisper_model_size
        self.piper_model_path    = piper_model_path
        self.sample_rate         = sample_rate
        self.frame_duration_ms   = frame_duration_ms
        self.vad_aggressiveness  = vad_aggressiveness
        self.silence_timeout_ms  = silence_timeout_ms
        self.max_record_seconds  = max_record_seconds

        self._whisper = None   # faster_whisper.WhisperModel  (set by load())
        self._piper   = None   # piper.PiperVoice              (set by load())
        self._vad     = None   # webrtcvad.Vad                 (set by load())
        self._loaded  = False

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load Whisper, Piper, and WebRTC VAD models.

        Idempotent — safe to call multiple times; only runs once.
        BLOCKING — call via ``asyncio.to_thread(engine.load)``.
        """
        if self._loaded:
            return

        logger.info(
            "VoiceEngine: loading models (whisper=%s, piper=%s)",
            self.whisper_model_size,
            self.piper_model_path,
        )

        # ---- faster-whisper ------------------------------------------------
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]

            self._whisper = WhisperModel(
                self.whisper_model_size, device="cpu", compute_type="int8"
            )
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is required for voice mode. "
                "Install it with:  pip install 'oli-bot[voice]'"
            ) from exc

        # ---- piper-tts -----------------------------------------------------
        try:
            from piper import PiperVoice  # type: ignore[import]

            self._piper = PiperVoice.load(self.piper_model_path)
        except ImportError as exc:
            raise RuntimeError(
                "piper-tts is required for voice mode. "
                "Install it with:  pip install 'oli-bot[voice]'"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load Piper TTS model '{self.piper_model_path}'. "
                "Download the model from "
                "https://huggingface.co/rhasspy/piper-voices and set "
                "OLI_VOICE_PIPER_MODEL to the .onnx file path."
            ) from exc

        # ---- webrtcvad -----------------------------------------------------
        try:
            import webrtcvad  # type: ignore[import]

            self._vad = webrtcvad.Vad(self.vad_aggressiveness)
        except ImportError as exc:
            raise RuntimeError(
                "webrtcvad is required for voice mode. "
                "Install it with:  pip install 'oli-bot[voice]'"
            ) from exc

        self._loaded = True
        logger.info("VoiceEngine: all models loaded.")

    # ------------------------------------------------------------------
    # Audio capture
    # ------------------------------------------------------------------

    def record(self, stop_event: Optional[threading.Event] = None) -> Optional[str]:
        """Capture microphone audio until silence or the maximum duration.

        Uses WebRTC VAD to automatically stop when the user stops speaking.

        ``stop_event``, if given, is polled every frame so a caller can abort
        the recording early (e.g. voice mode was toggled off mid-listen).

        Returns a path to a temporary WAV file containing the captured audio,
        or ``None`` if no speech was detected.  The caller is responsible for
        deleting the file after use.

        BLOCKING — call via ``asyncio.to_thread(engine.record)``.
        """
        try:
            import pyaudio  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "pyaudio is required for voice mode. "
                "Install it with:  pip install 'oli-bot[voice]'"
            ) from exc

        chunk    = int(self.sample_rate * self.frame_duration_ms / 1000)  # frames per chunk
        channels = 1
        fmt      = pyaudio.paInt16

        mic    = pyaudio.PyAudio()
        stream = mic.open(
            format=fmt,
            channels=channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=chunk,
        )

        max_frames            = int(self.max_record_seconds * 1000 / self.frame_duration_ms)
        silence_frames_needed = self.silence_timeout_ms // self.frame_duration_ms

        triggered     = False   # True once we've heard the first speech frame
        silence_count = 0
        frames: list[bytes] = []

        try:
            for _ in range(max_frames):
                if stop_event is not None and stop_event.is_set():
                    return None
                frame     = stream.read(chunk, exception_on_overflow=False)
                is_speech = self._vad.is_speech(frame, self.sample_rate)

                if not triggered:
                    if is_speech:
                        triggered = True
                        frames.append(frame)
                else:
                    frames.append(frame)
                    if is_speech:
                        silence_count = 0
                    else:
                        silence_count += 1
                        if silence_count >= silence_frames_needed:
                            break
        finally:
            stream.stop_stream()
            stream.close()
            mic.terminate()

        if not frames:
            logger.debug("VoiceEngine.record: no speech detected.")
            return None

        # Write captured frames to a temp WAV file
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(mic.get_sample_size(fmt))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b"".join(frames))

        logger.debug("VoiceEngine.record: saved %d frames to %s.", len(frames), tmp.name)
        return tmp.name

    # ------------------------------------------------------------------
    # Speech-to-text
    # ------------------------------------------------------------------

    def transcribe(self, audio_path: str) -> str:
        """Transcribe a WAV file to text using faster-whisper.

        Returns the transcribed string (may be empty if whisper found nothing).

        BLOCKING — call via ``asyncio.to_thread(engine.transcribe, path)``.
        """
        segments, _info = self._whisper.transcribe(audio_path, beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.debug("VoiceEngine.transcribe: %r", text)
        return text

    # ------------------------------------------------------------------
    # Text-to-speech + playback
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        """Synthesize ``text`` to a WAV file via Piper and play it back.

        The temporary WAV file is always deleted after playback (or on error).

        BLOCKING — call via ``asyncio.to_thread(engine.speak, text)``.
        """
        try:
            import simpleaudio as sa  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "simpleaudio is required for voice mode. "
                "Install it with:  pip install 'oli-bot[voice]'"
            ) from exc

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()  # close so wave.open can reopen it on Windows

        try:
            with wave.open(tmp_path, "wb") as wf:
                self._piper.synthesize(text, wf)

            wave_obj = sa.WaveObject.from_wave_file(tmp_path)
            play_obj = wave_obj.play()
            play_obj.wait_done()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
