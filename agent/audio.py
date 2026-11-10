"""Microphone capture, wake word, speech-to-text (local Whisper) and text-to-speech (Kokoro, or macOS `say`)."""

from __future__ import annotations

import collections
import contextlib
import queue
import re
import subprocess
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

from . import config

SAMPLE_RATE = 16000
CHUNK = 1280  # 80 ms at 16 kHz - the frame size openWakeWord expects
CHUNK_SECONDS = CHUNK / SAMPLE_RATE


def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))


class Microphone:
    """Continuously captures 16 kHz mono int16 audio into a queue."""

    def __init__(self, gate: Callable[[], bool] | None = None) -> None:
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._muted = False
        self._gate = gate or (lambda: False)  # returns True while audio should be ignored (we're talking)
        self._levels: collections.deque[float] = collections.deque(maxlen=150)
        self._last_audio = time.monotonic()
        self.peak = self.threshold = 0.0  # loudest level / speech threshold of the last recording attempt
        try:
            self._stream = self._open()
        except sd.PortAudioError as e:
            raise SystemExit(
                f"No usable microphone ({e}). Connect one, pick it in System Settings > Sound > Input, and allow "
                "your terminal app under Privacy & Security > Microphone. Or run with --mode text."
            ) from e

    def _open(self) -> sd.InputStream:
        return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK,
                              callback=self._callback)

    def _callback(self, indata, frames, time_info, status) -> None:
        self._last_audio = time.monotonic()  # the stream is alive, even if we drop this chunk
        if not self._muted and not self._gate():
            self._q.put(indata[:, 0].copy())

    def start(self) -> None:
        self._stream.start()
        self._last_audio = time.monotonic()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()

    def _restart(self) -> None:
        """Reopen the mic on the current default input. PortAudio keeps the device list it saw at startup, so
        after a switch (AirPods connecting, or changing between music and headset mode) it must be reinitialized."""
        print("(microphone stopped sending audio; reconnecting...)")
        with contextlib.suppress(Exception):
            self._stream.abort()
            self._stream.close()
        for attempt in range(10):
            try:
                sd._terminate()
                sd._initialize()
                self._stream = self._open()
                self.start()
                return
            except sd.PortAudioError as e:
                print(f"(microphone not ready: {e}; retrying)")
                time.sleep(1 + attempt)
        raise RuntimeError("The microphone stopped working. Check System Settings > Sound > Input.")

    def read(self, timeout: float | None = None) -> np.ndarray:
        """Next chunk of audio. Raises queue.Empty after `timeout`. Reconnects the mic if it goes silent."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            wait = 0.5 if deadline is None else max(0.0, min(0.5, deadline - time.monotonic()))
            try:
                return self._q.get(timeout=wait)
            except queue.Empty:
                if time.monotonic() - self._last_audio > 2.0:  # callbacks stopped: the stream is dead
                    self._restart()
                if deadline is not None and time.monotonic() >= deadline:
                    raise

    @staticmethod
    def device_name() -> str:
        with contextlib.suppress(Exception):
            return str(sd.query_devices(kind="input")["name"])
        return "unknown"

    def flush(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    @contextlib.contextmanager
    def muted(self):
        """Ignore the mic (e.g. while the assistant is speaking) and drop anything buffered."""
        self._muted = True
        try:
            yield
        finally:
            self.flush()
            self._muted = False

    def observe(self, chunk: np.ndarray) -> None:
        self._levels.append(_rms(chunk))

    @property
    def noise_floor(self) -> float:
        if not self._levels:
            return 150.0
        return float(np.percentile(self._levels, 20))


class WakeWordDetector:
    def __init__(self, name: str = config.WAKE_WORD, threshold: float = config.WAKE_THRESHOLD) -> None:
        import openwakeword.utils
        from openwakeword.model import Model

        try:
            self._model = Model(wakeword_models=[name], inference_framework="onnx")
        except ValueError:
            openwakeword.utils.download_models([name])
            self._model = Model(wakeword_models=[name], inference_framework="onnx")
        self.threshold = threshold
        self.phrase = name.replace("_", " ")

    def wait(self, mic: Microphone) -> None:
        """Block until the wake word is heard."""
        mic.flush()
        self._model.reset()
        while True:
            chunk = mic.read()
            mic.observe(chunk)
            scores = self._model.predict(chunk)
            if max(scores.values()) >= self.threshold:
                self._model.reset()
                return


def record_utterance(
    mic: Microphone,
    start_timeout: float,
    silence_seconds: float = config.SILENCE_SECONDS,
    max_seconds: float = 30.0,
) -> np.ndarray | None:
    """Record one spoken request. Returns None if nobody starts talking within `start_timeout`."""
    threshold = max(mic.noise_floor * 3.0, 300.0)
    mic.peak = 0.0
    preroll: collections.deque[np.ndarray] = collections.deque(maxlen=6)
    frames: list[np.ndarray] = []
    waited = loud = quiet = 0.0
    started = False

    while True:
        try:
            chunk = mic.read(timeout=1.0)
        except queue.Empty:
            if not started:
                waited += 1.0
                if waited >= start_timeout:
                    return None
            elif frames:  # mid-sentence and the audio stopped: use what we have
                return np.concatenate(frames)
            continue
        level = _rms(chunk)
        if not started:
            mic.peak = max(mic.peak, level)
            mic.threshold = threshold
            mic.observe(chunk)
            preroll.append(chunk)
            loud = loud + 1 if level > threshold else 0
            if loud >= 2:  # 160 ms of speech
                started = True
                frames.extend(preroll)
                continue
            waited += CHUNK_SECONDS
            if waited >= start_timeout:
                return None
        else:
            frames.append(chunk)
            quiet = quiet + CHUNK_SECONDS if level < threshold * 0.7 else 0.0
            if quiet >= silence_seconds or len(frames) * CHUNK_SECONDS >= max_seconds:
                return np.concatenate(frames)


# Whisper likes to "hear" these in near-silence.
_HALLUCINATIONS = {"", "you", "thank you", "thank you.", "thanks for watching!", "bye.", ".", "okay."}


class Transcriber:
    def __init__(self, model_name: str = config.WHISPER_MODEL) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")
        self._language = "en" if model_name.endswith(".en") else None

    def transcribe(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            samples, language=self._language, beam_size=1, vad_filter=True, condition_on_previous_text=False
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return "" if text.lower() in _HALLUCINATIONS else text


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)  # code blocks
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+")


def _afplay(samples: np.ndarray, rate: int) -> None:
    """Play through macOS `afplay`, which always uses the current output device and converts formats itself."""
    import tempfile
    import wave

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        subprocess.run(["afplay", f.name], check=False)


class _Say:
    """macOS built-in voice."""

    def synth(self, text: str):
        return text

    def play(self, text: str) -> None:
        cmd = ["say", "-r", str(config.SPEECH_RATE)]
        if config.VOICE:
            cmd += ["-v", config.VOICE]
        subprocess.run(cmd + ["--", text], check=False)


class _Kokoro:
    """Kokoro-82M via ONNX: natural voice, runs locally."""

    def __init__(self) -> None:
        from kokoro_onnx import Kokoro

        model, voices = config.MODELS_DIR / "kokoro-v1.0.onnx", config.MODELS_DIR / "voices-v1.0.bin"
        if not (model.exists() and voices.exists()):
            raise SystemExit(f"Kokoro model files are missing from {config.MODELS_DIR}. Run ./setup_models.sh")
        self._k = Kokoro(str(model), str(voices))
        self._voice = config.KOKORO_VOICE
        self._lang = "en-gb" if self._voice.startswith("b") else "en-us"
        self._afplay = False
        self.synth("Ready.")  # warm up the ONNX session so the first real sentence is quick

    def synth(self, text: str):
        samples, rate = self._k.create(text, voice=self._voice, speed=config.KOKORO_SPEED, lang=self._lang)
        return samples, rate

    def play(self, audio) -> None:
        samples, rate = audio
        if not self._afplay:
            try:
                sd.play(samples, rate)
                sd.wait()
                return
            except sd.PortAudioError as e:
                # Happens with Bluetooth headsets (e.g. AirPods switching into headset mode when the mic opens).
                print(f"(audio output changed ({e}); switching to afplay)")
                self._afplay = True
        _afplay(samples, rate)


class Speaker:
    """Speaks text as it arrives. One thread synthesizes the next sentence while another plays the current one."""

    ECHO_TAIL = 0.3  # keep the mic closed this long after speaking, so we don't hear ourselves

    def __init__(self) -> None:
        self._engine = _Kokoro() if config.TTS == "kokoro" else _Say()
        self._text_q: queue.Queue[str] = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self._pending = 0
        self._cond = threading.Condition()
        self._last_end = 0.0
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    # -- public ----------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._pending > 0 or time.monotonic() - self._last_end < self.ECHO_TAIL

    def say(self, text: str) -> None:
        """Speak and wait until finished."""
        self.enqueue(text)
        self.wait()

    def enqueue(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        with self._cond:
            self._pending += 1
        self._text_q.put(text)

    def wait(self) -> None:
        with self._cond:
            self._cond.wait_for(lambda: self._pending == 0)

    def stream(self) -> "SpeechStream":
        return SpeechStream(self)

    # -- workers ---------------------------------------------------------------
    def _done_one(self) -> None:
        with self._cond:
            self._pending -= 1
            self._last_end = time.monotonic()
            self._cond.notify_all()

    def _synth_loop(self) -> None:
        while True:
            text = self._text_q.get()
            try:
                self._audio_q.put(self._engine.synth(text))
            except Exception as e:
                print(f"(speech error: {e})")
                self._done_one()

    def _play_loop(self) -> None:
        while True:
            audio = self._audio_q.get()
            try:
                self._engine.play(audio)
            except Exception as e:
                print(f"(playback error: {e})")
            finally:
                self._done_one()


class SpeechStream:
    """Collects streamed model text and hands each finished sentence to the speaker right away."""

    def __init__(self, speaker: Speaker) -> None:
        self._speaker = speaker
        self._buf = ""
        self._fed = False

    def feed(self, delta: str) -> None:
        self._fed = True
        self._buf += delta
        while True:
            m = _SENTENCE_END.search(self._buf)
            if m is None:
                break
            sentence, self._buf = self._buf[: m.end()], self._buf[m.end():]
            self._speaker.enqueue(sentence)
        if len(self._buf) > 220 and ", " in self._buf[60:]:  # a long run-on: break at a comma
            cut = self._buf.rindex(", ") + 1
            self._speaker.enqueue(self._buf[:cut])
            self._buf = self._buf[cut:]

    def finish(self, fallback: str = "") -> None:
        """Speak what's left (or `fallback` if nothing was streamed) and wait until done."""
        if self._buf.strip():
            self._speaker.enqueue(self._buf)
        elif not self._fed and fallback:
            self._speaker.enqueue(fallback)
        self._buf = ""
        self._speaker.wait()


def chime(name: str) -> None:
    """Play a short system sound without blocking (Tink, Pop, Glass, Basso...)."""
    with contextlib.suppress(OSError):
        subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|go ahead|do it|send it|please do|affirmative)\b")
_NO = re.compile(r"\b(no|nope|don't|do not|cancel|stop|wait|never mind)\b")


def is_affirmative(text: str) -> bool:
    t = text.lower()
    return bool(_YES.search(t)) and not _NO.search(t)
