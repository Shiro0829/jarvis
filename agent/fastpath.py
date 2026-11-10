"""Instant answers for common one-step commands, without waiting on the language model.

On this Mac the local model writes about 13 tokens a second, so even "open Safari" spends ~3 s just composing the
tool call and the confirmation. These patterns cover the everyday one-liners and answer in well under a second.
Anything that doesn't match exactly (or fails) goes to the model as usual.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import config, web
from .tools import mac

_FILLER = re.compile(rf"^(?:(?:hey|ok|okay)\s+)?(?:{re.escape(config.ASSISTANT_NAME.lower())}\b[,.]?\s*)?"
                     r"(?:(?:please|can you|could you|would you)\s+)?", re.I)
_TAIL = re.compile(r"\s*(?:,?\s*please|\s+for me|\s+now)?\s*$", re.I)
_NAME = r"([a-z0-9][a-z0-9 .'&+-]{0,30}?)"
_OPEN = re.compile(rf"^(?:open|launch|start|bring up|switch to|go to)\s+(?:the\s+)?(?:app\s+)?{_NAME}(?:\s+app)?$", re.I)
_QUIT = re.compile(rf"^(?:quit|close|exit|kill)\s+(?:the\s+)?(?:app\s+)?{_NAME}(?:\s+app)?$", re.I)
_VOL_SET = re.compile(r"^(?:set|turn|change|put)?\s*(?:the\s+)?(?:volume|sound)\s+(?:to|at|up to|down to)?\s*(\d{1,3})"
                      r"\s*(?:%|percent)?$", re.I)
_VOL_STEP = re.compile(r"^(?:turn\s+(?:the\s+)?(?:volume|sound|it)\s+(up|down)|(?:volume|sound)\s+(up|down)|"
                       r"(louder|quieter|softer))$", re.I)
_MUTE = re.compile(r"^(mute|unmute)(?:\s+(?:the\s+)?(?:sound|volume|audio|mac|computer))?$", re.I)
_WEATHER = re.compile(r"^(?:what's|what is|how's|how is|tell me)?\s*(?:the\s+)?weather(?:\s+like)?"
                      r"(?:\s+(?:in|for|at)\s+([a-z .'-]{2,40}?))?(?:\s+(?:today|right now|now|outside))?$", re.I)
_TIME = re.compile(r"^(?:what(?:'s| is) the time|what time is it)(?: now| right now)?$", re.I)
_DATE = re.compile(r"^(?:what(?:'s| is) (?:the date|today's date)|what day is (?:it|today))(?: today)?$", re.I)
# System Settings panes that open directly (checked on this Mac; Trackpad/Mouse/General don't deep-link).
_PANES = {
    "displays": ("Displays", "com.apple.Displays-Settings.extension"),
    "display": ("Displays", "com.apple.Displays-Settings.extension"),
    "wi-fi": ("Wi-Fi", "com.apple.wifi-settings-extension"), "wifi": ("Wi-Fi", "com.apple.wifi-settings-extension"),
    "bluetooth": ("Bluetooth", "com.apple.BluetoothSettings"),
    "sound": ("Sound", "com.apple.Sound-Settings.extension"),
    "notifications": ("Notifications", "com.apple.Notifications-Settings.extension"),
    "appearance": ("Appearance", "com.apple.Appearance-Settings.extension"),
    "network": ("Network", "com.apple.Network-Settings.extension"),
    "privacy": ("Privacy & Security", "com.apple.settings.PrivacySecurity.extension"),
    "privacy and security": ("Privacy & Security", "com.apple.settings.PrivacySecurity.extension"),
    "privacy & security": ("Privacy & Security", "com.apple.settings.PrivacySecurity.extension"),
    "security": ("Privacy & Security", "com.apple.settings.PrivacySecurity.extension"),
    "keyboard": ("Keyboard", "com.apple.Keyboard-Settings.extension"),
    "accessibility": ("Accessibility", "com.apple.Accessibility-Settings.extension"),
    "wallpaper": ("Wallpaper", "com.apple.Wallpaper-Settings.extension"),
    "focus": ("Focus", "com.apple.Focus-Settings.extension"),
    "software update": ("Software Update", "com.apple.Software-Update-Settings.extension"),
    "energy": ("Energy", "com.apple.Battery-Settings.extension"),
    "battery": ("Energy", "com.apple.Battery-Settings.extension"),
}
_PANE = re.compile(r"^(?:open|show|go to|take me to|bring up)\s+(?:me\s+)?(?:(?:the\s+)?system settings\s+(?:and\s+)?(?:go\s+)?"
                   r"(?:to\s+)?)?(?:the\s+)?(?:my\s+)?(?:settings\s+for\s+)?([a-z& -]{3,25}?)"
                   r"(?:\s+(?:settings|preferences|section|pane|page))?(?:\s+in\s+system\s+settings)?$", re.I)
# Words that mean the request is more than "open <app>": leave those to the model.
_NOT_AN_APP = re.compile(r"\b(and|then|to|in|on|with|my|a|an|new|tab|window|file|folder|page|website|site|"
                         r"settings for|it|this|that|everything|all)\b", re.I)


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip().rstrip(".!?").strip()
    t = _FILLER.sub("", t)
    return _TAIL.sub("", t).strip()


def _running(name: str) -> str | None:
    apps = [a.strip() for a in mac.list_running_apps().split(",")]
    return next((a for a in apps if a.lower() == name.lower()), None)


def handle(text: str) -> tuple[str, str, dict, str] | None:
    """Return (reply, tool name, tool args, tool result) if this is a command we can do instantly, else None."""
    t = _clean(text)
    if not t or len(t) > 60:
        return None
    try:
        m = _PANE.match(t)
        if m and m.group(1).strip().lower() in _PANES:
            title, pane = _PANES[m.group(1).strip().lower()]
            result = mac.open_url(f"x-apple.systempreferences:{pane}")
            return f"Here are your {title} settings.", "open_url", {"url": f"x-apple.systempreferences:{pane}"}, result
        if m := _OPEN.match(t):
            name = m.group(1).strip()
            if _NOT_AN_APP.search(name):
                return None
            result = mac.open_app(name)  # raises if there's no such app -> the model takes over
            return f"{name.title() if name.islower() else name} is open.", "open_app", {"name": name}, result
        if m := _QUIT.match(t):
            name = _running(m.group(1).strip())  # only quit apps that are running (else AppleScript asks "where is it?")
            if not name:
                return None
            return f"Closed {name}.", "quit_app", {"name": name}, mac.quit_app(name)
        if m := _VOL_SET.match(t):
            level = max(0, min(int(m.group(1)), 100))
            return f"Volume is at {level} percent.", "set_volume", {"level": level}, mac.set_volume(level=level)
        if m := _VOL_STEP.match(t):
            up = (m.group(1) or m.group(2) or m.group(3)).lower() in ("up", "louder")
            now = int(float(mac.osascript("output volume of (get volume settings)")))
            level = max(0, min(now + (15 if up else -15), 100))
            return f"Volume is at {level} percent.", "set_volume", {"level": level}, mac.set_volume(level=level)
        if m := _MUTE.match(t):
            muted = m.group(1).lower() == "mute"
            return ("Muted." if muted else "Sound is back on."), "set_volume", {"muted": muted}, \
                mac.set_volume(muted=muted)
        if m := _WEATHER.match(t):
            place = (m.group(1) or "").strip()
            sentence = web.weather_sentence(place)
            return sentence, "get_weather", {"location": place} if place else {}, sentence
        if _TIME.match(t):
            now = datetime.now().strftime("%-I:%M %p")
            return f"It's {now}.", "", {}, ""
        if _DATE.match(t):
            return f"It's {datetime.now().strftime('%A, %B %-d')}.", "", {}, ""
    except Exception:  # anything odd: let the model handle it properly
        return None
    return None
