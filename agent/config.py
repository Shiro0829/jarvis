"""Settings, read from environment variables (or a .env file in the project root)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


# --- Brain -----------------------------------------------------------------
# "ollama" = free local model on this Mac; "claude" = Anthropic API (needs ANTHROPIC_API_KEY).
BRAIN = os.getenv("VA_BRAIN", "ollama").lower()
MODEL = os.getenv("VA_MODEL", "claude-opus-5-5")  # Claude model, used when VA_BRAIN=claude
# low | medium | high | xhigh | max. Lower = faster replies, higher = better at long multi-step tasks.
EFFORT = os.getenv("VA_EFFORT", "medium")
MAX_STEPS = int(os.getenv("VA_MAX_STEPS", "40"))  # max model round-trips per request
ASSISTANT_NAME = os.getenv("VA_NAME", "Jarvis")
HOME_CITY = os.getenv("VA_HOME_CITY", "")  # default city for weather, e.g. "Boston"
UNITS = os.getenv("VA_UNITS", "imperial")  # imperial (°F, mph) | metric (°C, km/h)

# --- Local brain (Ollama) ----------------------------------------------------
OLLAMA_MODEL = os.getenv("VA_OLLAMA_MODEL", "bonsai-2:27b")  # needs "tools" (and "vision" for the screen)
OLLAMA_HOST = os.getenv("VA_OLLAMA_HOST", "http://127.0.0.1:11434")
NUM_CTX = int(os.getenv("VA_NUM_CTX", "16384"))  # context window; bigger = more memory and slower
THINK = os.getenv("VA_THINK", "0").lower() in ("1", "true", "yes")  # reasoning: smarter but much slower
_keep = os.getenv("VA_KEEP_ALIVE", "-1")  # keep the model in memory; -1 = forever, "10m" = 10 minutes
DEBUG = os.getenv("VA_DEBUG", "0").lower() in ("1", "true", "yes")  # print timing for each model step
LOCAL_MAX_STEPS = int(os.getenv("VA_LOCAL_MAX_STEPS", "15"))  # tool rounds per request before giving up
FAST_PATH = os.getenv("VA_FAST_PATH", "1") != "0"  # answer simple commands (open X, volume, weather) instantly
KEEP_ALIVE: int | str = int(_keep) if _keep.lstrip("-").isdigit() else _keep

# --- Ears ------------------------------------------------------------------
# Built-in openWakeWord models: hey_jarvis, alexa, hey_mycroft, hey_rhasspy
WAKE_WORD = os.getenv("VA_WAKE_WORD", "hey_jarvis")
WAKE_THRESHOLD = _float("VA_WAKE_THRESHOLD", 0.5)
# faster-whisper model: tiny.en, base.en, small.en, medium.en, large-v3 ...
WHISPER_MODEL = os.getenv("VA_WHISPER_MODEL", "small.en")
FOLLOWUP_SECONDS = _float("VA_FOLLOWUP_SECONDS", 5)  # listen for a follow-up without the wake word
SILENCE_SECONDS = _float("VA_SILENCE_SECONDS", 1.0)  # pause length that ends your sentence

# --- Mouth -----------------------------------------------------------------
TTS = os.getenv("VA_TTS", "kokoro").lower()  # kokoro (natural, local) | say (macOS built-in)
# Kokoro voices: am_michael, am_adam, am_onyx, bm_george, bm_lewis, af_heart, bf_emma ... (a=US, b=UK)
KOKORO_VOICE = os.getenv("VA_KOKORO_VOICE", "am_michael")
KOKORO_SPEED = _float("VA_KOKORO_SPEED", 1.1)
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
VOICE = os.getenv("VA_VOICE", "")  # `say` only: any voice from `say -v '?'`; empty = system default
SPEECH_RATE = int(os.getenv("VA_SPEECH_RATE", "190"))  # `say` only

# --- Memory ----------------------------------------------------------------
SESSION_IDLE_MINUTES = _float("VA_SESSION_IDLE_MINUTES", 10)  # forget the conversation after this
