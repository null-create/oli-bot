"""Unit tests for oli_bot/voice.py.

All external dependencies (pyaudio, webrtcvad, faster_whisper, piper,
simpleaudio) are mocked so the suite runs without any hardware or heavy
ML packages installed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import wave
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav(path: str, n_frames: int = 480) -> None:
    """Write a minimal valid 16-bit mono 16 kHz WAV file at *path*."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16_000)
        wf.writeframes(b"\x00\x01" * n_frames)


def _stub_module(name: str, **attrs) -> types.ModuleType:
    """Return a stub module registered in sys.modules under *name*."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _remove_module(name: str) -> None:
    sys.modules.pop(name, None)


def _write_wav_into_handle(text: str, wf) -> None:
    """Side-effect for mock piper.synthesize: write a minimal valid WAV header.

    Piper's real synthesize() calls wf.setnchannels / setsampwidth /
    setframerate / writeframes on the wave.Wave_write handle it receives.
    Without this, wave.close() raises 'channels not specified'.
    """
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(22_050)
    wf.writeframes(b"\x00\x01" * 100)


# ---------------------------------------------------------------------------
# VoiceEngine.load — happy path
# ---------------------------------------------------------------------------


class TestVoiceEngineLoad:
    def _patch_all_deps(self):
        """Return a context that stubs all three voice deps."""
        mock_whisper_cls = MagicMock(return_value=MagicMock())
        mock_piper_voice = MagicMock()
        mock_piper_voice.load = MagicMock(return_value=MagicMock())
        mock_vad_cls = MagicMock(return_value=MagicMock())

        fw_mod = _stub_module("faster_whisper", WhisperModel=mock_whisper_cls)
        pip_mod = _stub_module("piper", PiperVoice=mock_piper_voice)
        vad_mod = _stub_module("webrtcvad", Vad=mock_vad_cls)

        return (
            fw_mod,
            pip_mod,
            vad_mod,
            mock_whisper_cls,
            mock_piper_voice,
            mock_vad_cls,
        )

    def teardown_method(self):
        for name in ("faster_whisper", "piper", "webrtcvad"):
            _remove_module(name)

    def test_load_sets_loaded_flag(self):
        self._patch_all_deps()
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine(whisper_model_size="tiny", piper_model_path="dummy.onnx")
        engine.load()
        assert engine._loaded is True

    def test_load_is_idempotent(self):
        """Calling load() twice must not re-instantiate models."""
        _, _, _, mock_whisper_cls, mock_piper_voice, _ = self._patch_all_deps()
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine(whisper_model_size="tiny", piper_model_path="dummy.onnx")
        engine.load()
        engine.load()  # second call — should be a no-op

        assert mock_whisper_cls.call_count == 1
        assert mock_piper_voice.load.call_count == 1

    def test_load_stores_model_references(self):
        self._patch_all_deps()
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine(whisper_model_size="tiny", piper_model_path="dummy.onnx")
        engine.load()

        assert engine._whisper is not None
        assert engine._piper is not None
        assert engine._vad is not None


# ---------------------------------------------------------------------------
# VoiceEngine.load — missing dependency errors
# ---------------------------------------------------------------------------


class TestVoiceEngineLoadErrors:
    def teardown_method(self):
        for name in ("faster_whisper", "piper", "webrtcvad"):
            _remove_module(name)

    def test_missing_faster_whisper_raises_runtime_error(self):
        # Ensure the import fails
        sys.modules["faster_whisper"] = None  # type: ignore[assignment]
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = False
        engine.whisper_model_size = "base"
        engine.piper_model_path = "dummy.onnx"
        engine._whisper = engine._piper = engine._vad = None

        with pytest.raises(RuntimeError, match="faster-whisper"):
            engine.load()

    def test_missing_piper_raises_runtime_error(self):
        _stub_module("faster_whisper", WhisperModel=MagicMock(return_value=MagicMock()))
        sys.modules["piper"] = None  # type: ignore[assignment]
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = False
        engine.whisper_model_size = "base"
        engine.piper_model_path = "dummy.onnx"
        engine._whisper = engine._piper = engine._vad = None

        with pytest.raises(RuntimeError, match="piper-tts"):
            engine.load()

    def test_missing_webrtcvad_raises_runtime_error(self):
        _stub_module("faster_whisper", WhisperModel=MagicMock(return_value=MagicMock()))
        mock_pv = MagicMock()
        mock_pv.load = MagicMock(return_value=MagicMock())
        _stub_module("piper", PiperVoice=mock_pv)
        sys.modules["webrtcvad"] = None  # type: ignore[assignment]
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = False
        engine.whisper_model_size = "base"
        engine.piper_model_path = "dummy.onnx"
        engine._whisper = engine._piper = engine._vad = None

        with pytest.raises(RuntimeError, match="webrtcvad"):
            engine.load()

    def test_bad_piper_model_path_raises_runtime_error(self):
        _stub_module("faster_whisper", WhisperModel=MagicMock(return_value=MagicMock()))
        mock_pv = MagicMock()
        mock_pv.load = MagicMock(side_effect=FileNotFoundError("not found"))
        _stub_module("piper", PiperVoice=mock_pv)
        _stub_module("webrtcvad", Vad=MagicMock(return_value=MagicMock()))
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = False
        engine.whisper_model_size = "base"
        engine.piper_model_path = "/nonexistent/model.onnx"
        engine._whisper = engine._piper = engine._vad = None

        with pytest.raises(RuntimeError, match="Piper"):
            engine.load()


# ---------------------------------------------------------------------------
# VoiceEngine.transcribe
# ---------------------------------------------------------------------------


class TestVoiceEngineTranscribe:
    def _make_engine(self) -> "VoiceEngine":
        """Return a VoiceEngine with models pre-stubbed (skipping load())."""
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = True
        engine.whisper_model_size = "base"
        engine.piper_model_path = "dummy.onnx"
        engine._piper = MagicMock()
        engine._vad = MagicMock()
        return engine

    def test_transcribe_joins_segments(self):
        engine = self._make_engine()
        seg1 = MagicMock()
        seg1.text = " Hello "
        seg2 = MagicMock()
        seg2.text = " world"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        engine._whisper = mock_model

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            _make_wav(path)
            result = engine.transcribe(path)
        finally:
            os.unlink(path)

        assert result == "Hello world"

    def test_transcribe_empty_segments_returns_empty_string(self):
        engine = self._make_engine()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], MagicMock())
        engine._whisper = mock_model

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            _make_wav(path)
            result = engine.transcribe(path)
        finally:
            os.unlink(path)

        assert result == ""

    def test_transcribe_strips_whitespace(self):
        engine = self._make_engine()
        seg = MagicMock()
        seg.text = "  spaces  "
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([seg], MagicMock())
        engine._whisper = mock_model

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            _make_wav(path)
            result = engine.transcribe(path)
        finally:
            os.unlink(path)

        assert result == "spaces"


# ---------------------------------------------------------------------------
# VoiceEngine.speak
# ---------------------------------------------------------------------------


class TestVoiceEngineSpeak:
    def _make_engine(self) -> "VoiceEngine":
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = True
        engine.whisper_model_size = "base"
        engine.piper_model_path = "dummy.onnx"
        engine._whisper = MagicMock()
        engine._vad = MagicMock()
        # synthesize must write a valid WAV header so wave.close() doesn't raise
        mock_piper = MagicMock()
        mock_piper.synthesize.side_effect = _write_wav_into_handle
        engine._piper = mock_piper
        return engine

    def teardown_method(self):
        _remove_module("simpleaudio")

    def test_speak_synthesizes_and_plays(self):
        engine = self._make_engine()

        mock_play_obj = MagicMock()
        mock_wave_obj = MagicMock()
        mock_wave_obj.play.return_value = mock_play_obj
        mock_sa = _stub_module("simpleaudio")
        mock_sa.WaveObject = MagicMock()
        mock_sa.WaveObject.from_wave_file = MagicMock(return_value=mock_wave_obj)

        engine.speak("Hello there")

        engine._piper.synthesize.assert_called_once()
        mock_play_obj.wait_done.assert_called_once()

    def test_speak_cleans_up_temp_file_on_success(self):
        engine = self._make_engine()

        created: list[str] = []
        _orig_ntf = tempfile.NamedTemporaryFile

        def tracking_ntf(*args, **kwargs):
            f = _orig_ntf(*args, **kwargs)
            created.append(f.name)
            return f

        mock_play_obj = MagicMock()
        mock_wave_obj = MagicMock()
        mock_wave_obj.play.return_value = mock_play_obj
        mock_sa = _stub_module("simpleaudio")
        mock_sa.WaveObject = MagicMock()
        mock_sa.WaveObject.from_wave_file = MagicMock(return_value=mock_wave_obj)

        with patch("tempfile.NamedTemporaryFile", side_effect=tracking_ntf):
            engine.speak("cleanup test")

        for p in created:
            assert not os.path.exists(p), f"Temp file not removed: {p}"

    def test_speak_cleans_up_temp_file_on_playback_error(self):
        """Temp file must be removed even when simpleaudio raises."""
        engine = self._make_engine()

        created: list[str] = []
        _orig_ntf = tempfile.NamedTemporaryFile

        def tracking_ntf(*args, **kwargs):
            f = _orig_ntf(*args, **kwargs)
            created.append(f.name)
            return f

        mock_sa = _stub_module("simpleaudio")
        mock_sa.WaveObject = MagicMock()
        mock_sa.WaveObject.from_wave_file = MagicMock(side_effect=RuntimeError("boom"))

        with patch("tempfile.NamedTemporaryFile", side_effect=tracking_ntf):
            with pytest.raises(RuntimeError, match="boom"):
                engine.speak("error test")

        for p in created:
            assert not os.path.exists(p), f"Temp file not removed after error: {p}"

    def test_speak_raises_when_simpleaudio_missing(self):
        engine = self._make_engine()
        sys.modules["simpleaudio"] = None  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="simpleaudio"):
            engine.speak("missing dep")


# ---------------------------------------------------------------------------
# VoiceEngine.record
# ---------------------------------------------------------------------------


class TestVoiceEngineRecord:
    def _make_engine(self) -> "VoiceEngine":
        from oli_bot import voice as _v
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine.__new__(VoiceEngine)
        engine._loaded = True
        engine.whisper_model_size = "base"
        engine.piper_model_path = "dummy.onnx"
        engine.sample_rate = _v.SAMPLE_RATE
        engine.frame_duration_ms = _v.FRAME_DURATION_MS
        engine.vad_aggressiveness = _v.VAD_AGGRESSIVENESS
        engine.silence_timeout_ms = _v.SILENCE_TIMEOUT_MS
        engine.max_record_seconds = _v.MAX_RECORD_SECONDS
        engine._whisper = MagicMock()
        engine._piper = MagicMock()
        return engine

    def teardown_method(self):
        _remove_module("pyaudio")

    def _setup_pyaudio_mock(self, is_speech_seq: list[bool]):
        """Return a fully mocked pyaudio module whose VAD sequence follows *is_speech_seq*."""
        CHUNK = 480  # 30 ms @ 16 kHz

        mock_stream = MagicMock()
        mock_stream.read.return_value = b"\x00\x01" * CHUNK

        mock_pa_instance = MagicMock()
        mock_pa_instance.open.return_value = mock_stream
        mock_pa_instance.get_sample_size.return_value = 2

        mock_pa_cls = MagicMock(return_value=mock_pa_instance)

        pa_mod = _stub_module("pyaudio", PyAudio=mock_pa_cls, paInt16=8)

        return pa_mod, mock_pa_instance, mock_stream, mock_pa_cls

    def test_returns_none_when_no_speech_detected(self):
        engine = self._make_engine()
        self._setup_pyaudio_mock([])

        mock_vad = MagicMock()
        mock_vad.is_speech.return_value = False
        engine._vad = mock_vad

        result = engine.record()
        assert result is None

    def test_returns_wav_path_when_speech_detected(self):
        engine = self._make_engine()
        self._setup_pyaudio_mock([])

        # Speech on first frame, then silence until timeout
        from oli_bot import voice as _v

        silence_needed = _v.SILENCE_TIMEOUT_MS // _v.FRAME_DURATION_MS
        speech_values = [True] + [False] * (silence_needed + 2)

        mock_vad = MagicMock()
        mock_vad.is_speech.side_effect = speech_values
        engine._vad = mock_vad

        result = engine.record()
        assert result is not None
        assert result.endswith(".wav")
        # Clean up
        try:
            os.unlink(result)
        except OSError:
            pass

    def test_returned_wav_is_valid(self):
        engine = self._make_engine()
        self._setup_pyaudio_mock([])

        from oli_bot import voice as _v

        silence_needed = _v.SILENCE_TIMEOUT_MS // _v.FRAME_DURATION_MS
        speech_values = [True] + [False] * (silence_needed + 2)

        mock_vad = MagicMock()
        mock_vad.is_speech.side_effect = speech_values
        engine._vad = mock_vad

        path = engine.record()
        assert path is not None
        try:
            with wave.open(path, "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getframerate() == 16_000
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_raises_when_pyaudio_missing(self):
        engine = self._make_engine()
        engine._vad = MagicMock()
        sys.modules["pyaudio"] = None  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="pyaudio"):
            engine.record()

    def test_stream_always_closed_on_exception(self):
        """The mic stream must be stopped/closed even when read() raises."""
        engine = self._make_engine()

        mock_stream = MagicMock()
        mock_stream.read.side_effect = OSError("mic exploded")

        mock_pa_instance = MagicMock()
        mock_pa_instance.open.return_value = mock_stream
        mock_pa_instance.get_sample_size.return_value = 2

        mock_pa_cls = MagicMock(return_value=mock_pa_instance)
        _stub_module("pyaudio", PyAudio=mock_pa_cls, paInt16=8)

        mock_vad = MagicMock()
        mock_vad.is_speech.return_value = True
        engine._vad = mock_vad

        with pytest.raises(OSError, match="mic exploded"):
            engine.record()

        mock_stream.stop_stream.assert_called_once()
        mock_stream.close.assert_called_once()
        mock_pa_instance.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------


class TestVoiceDefaults:
    def test_default_constants_are_sensible(self):
        from oli_bot import voice as _v

        assert _v.SAMPLE_RATE == 16_000
        assert _v.FRAME_DURATION_MS in (10, 20, 30)
        assert 0 <= _v.VAD_AGGRESSIVENESS <= 3
        assert _v.SILENCE_TIMEOUT_MS > 0
        assert _v.MAX_RECORD_SECONDS > 0

    def test_env_override_whisper_model(self, monkeypatch):
        monkeypatch.setenv("OLI_VOICE_WHISPER_MODEL", "large")
        from oli_bot.config import AppConfig
        from oli_bot.voice import VoiceEngine

        cfg = AppConfig()
        assert cfg.voice_whisper_model == "large"
        engine = VoiceEngine(whisper_model_size=cfg.voice_whisper_model)
        assert engine.whisper_model_size == "large"

    def test_env_override_piper_model(self, monkeypatch):
        monkeypatch.setenv("OLI_VOICE_PIPER_MODEL", "/custom/path/model.onnx")
        from oli_bot.config import AppConfig
        from oli_bot.voice import VoiceEngine

        cfg = AppConfig()
        assert cfg.voice_piper_model == "/custom/path/model.onnx"
        engine = VoiceEngine(piper_model_path=cfg.voice_piper_model)
        assert engine.piper_model_path == "/custom/path/model.onnx"

    def test_constants_match_appconfig_defaults(self):
        # Drift guard: voice.py module constants are sourced from AppConfig
        from oli_bot import voice as _v
        from oli_bot.config import AppConfig

        cfg = AppConfig()
        assert _v.WHISPER_MODEL_SIZE == cfg.voice_whisper_model
        assert _v.PIPER_MODEL_PATH == cfg.voice_piper_model
        assert _v.SAMPLE_RATE == cfg.voice_sample_rate
        assert _v.FRAME_DURATION_MS == cfg.voice_frame_duration_ms
        assert _v.VAD_AGGRESSIVENESS == cfg.voice_vad_aggressiveness
        assert _v.SILENCE_TIMEOUT_MS == cfg.voice_silence_timeout_ms
        assert _v.MAX_RECORD_SECONDS == cfg.voice_max_record_seconds

    def test_vad_aggressiveness_validated(self):
        import pytest
        from pydantic import ValidationError

        from oli_bot.config import AppConfig

        with pytest.raises(ValidationError):
            AppConfig(voice_vad_aggressiveness=4)
        with pytest.raises(ValidationError):
            AppConfig(voice_silence_timeout_ms=0)

    def test_engine_stores_vad_tunables(self):
        from oli_bot.voice import VoiceEngine

        engine = VoiceEngine(
            whisper_model_size="tiny",
            piper_model_path="dummy.onnx",
            sample_rate=8000,
            frame_duration_ms=20,
            vad_aggressiveness=3,
            silence_timeout_ms=400,
            max_record_seconds=5,
        )
        assert engine.sample_rate == 8000
        assert engine.frame_duration_ms == 20
        assert engine.vad_aggressiveness == 3
        assert engine.silence_timeout_ms == 400
        assert engine.max_record_seconds == 5
