"""Executes Claude's computer-use toolset actions (screenshot, click, type, ...) on the main display."""

from __future__ import annotations

import base64
import contextlib
import io
import math
import os
import subprocess
import tempfile
import time

import pyautogui
from PIL import Image

pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort

TOOLSET_NAME = "computer"
TOOLSET_DEFINITION = {"type": "computer_toolset_20260801"}

# Keep screenshots modest: sharper than needed costs tokens and latency.
_MAX_LONG_EDGE = 1568
_MAX_PIXELS = 1_150_000
_SCROLL_MULTIPLIER = 3

_KEY_ALIASES = {
    "return": "enter", "kp_enter": "enter", "escape": "esc", "back_space": "backspace",
    "page_up": "pageup", "prior": "pageup", "page_down": "pagedown", "next": "pagedown",
    "control": "ctrl", "alt": "option", "alt_l": "option", "alt_r": "option",
    "super": "command", "super_l": "command", "super_r": "command", "cmd": "command",
    "meta": "command", "win": "command", "shift_l": "shift", "shift_r": "shift",
    "control_l": "ctrl", "control_r": "ctrl", "caps_lock": "capslock",
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'", "grave": "`",
    "bracketleft": "[", "bracketright": "]", "space": "space",
}


def _norm_key(key: str) -> str:
    k = key.strip()
    k = _KEY_ALIASES.get(k.lower(), k if len(k) == 1 else k.lower())
    if len(k) == 1:
        k = k.lower()
    if k not in pyautogui.KEYBOARD_KEYS:
        raise ValueError(f"Unknown key: {key!r}")
    return k


def _split_combo(combo: str) -> list[str]:
    combo = combo.strip()
    if combo == "+":
        return ["+"]
    if combo.endswith("++"):
        return [_norm_key(k) for k in combo[:-2].split("+")] + ["+"]
    return [_norm_key(k) for k in combo.split("+") if k]


def _image_block(img: Image.Image) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _fit(img: Image.Image) -> Image.Image:
    w, h = img.size
    s = min(1.0, _MAX_LONG_EDGE / max(w, h), math.sqrt(_MAX_PIXELS / (w * h)))
    return img if s >= 1.0 else img.resize((round(w * s), round(h * s)), Image.LANCZOS)


class Computer:
    def __init__(self) -> None:
        self.width, self.height = pyautogui.size()  # main display, in points
        self.scale = min(
            1.0, _MAX_LONG_EDGE / max(self.width, self.height), math.sqrt(_MAX_PIXELS / (self.width * self.height))
        )
        self.shot_size = (round(self.width * self.scale), round(self.height * self.scale))

    # -- helpers ---------------------------------------------------------------
    def _capture(self) -> Image.Image:
        """Full-resolution capture of the main display (Retina pixels)."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            r = subprocess.run(["screencapture", "-x", "-m", "-C", "-t", "png", path],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise RuntimeError(
                    "Screenshot failed - give your terminal app Screen Recording permission in System Settings > "
                    f"Privacy & Security > Screen & System Audio Recording, then restart it. ({r.stderr.strip()})"
                )
            img = Image.open(path)
            img.load()
            return img.convert("RGB")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)

    def _to_screen(self, coord) -> tuple[float, float]:
        x, y = coord
        sx = min(max(x / self.scale, 0), self.width - 1)
        sy = min(max(y / self.scale, 0), self.height - 1)
        return sx, sy

    @contextlib.contextmanager
    def _modifiers(self, text: str | None):
        keys = _split_combo(text) if text else []
        for k in keys:
            pyautogui.keyDown(k)
        try:
            yield
        finally:
            for k in reversed(keys):
                pyautogui.keyUp(k)

    def _type(self, text: str) -> None:
        if text.isascii() and len(text) <= 200:
            pyautogui.write(text, interval=0.01)
            return
        # Long or non-ASCII text: paste it, then restore the clipboard.
        old = subprocess.run(["pbpaste"], capture_output=True).stdout
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        pyautogui.hotkey("command", "v", interval=0.05)
        time.sleep(0.3)
        subprocess.run(["pbcopy"], input=old, check=False)

    # -- actions ---------------------------------------------------------------
    def screenshot(self) -> list[dict]:
        time.sleep(0.4)  # let the UI settle after the previous action
        return [_image_block(self._capture().resize(self.shot_size, Image.LANCZOS))]

    def run(self, name: str, inp: dict) -> list[dict] | str:
        if name == "screenshot":
            return self.screenshot()

        if name == "zoom":
            x0, y0, x1, y1 = inp["region"]
            full = self._capture()
            f = full.width / self.shot_size[0]
            crop = full.crop((round(x0 * f), round(y0 * f), round(x1 * f), round(y1 * f)))
            return [_image_block(_fit(crop))]

        clicks = {"left_click": ("left", 1), "right_click": ("right", 1), "middle_click": ("middle", 1),
                  "double_click": ("left", 2), "triple_click": ("left", 3)}
        if name in clicks:
            button, n = clicks[name]
            with self._modifiers(inp.get("text")):
                if inp.get("coordinate"):
                    x, y = self._to_screen(inp["coordinate"])
                    pyautogui.click(x, y, clicks=n, interval=0.08, button=button)
                else:
                    pyautogui.click(clicks=n, interval=0.08, button=button)
            return "OK"

        if name == "left_click_drag":
            sx, sy = self._to_screen(inp["start_coordinate"])
            ex, ey = self._to_screen(inp["coordinate"])
            with self._modifiers(inp.get("text")):
                pyautogui.moveTo(sx, sy)
                pyautogui.dragTo(ex, ey, duration=0.5, button="left")
            return "OK"

        if name == "mouse_move":
            pyautogui.moveTo(*self._to_screen(inp["coordinate"]), duration=0.1)
            return "OK"

        if name == "left_mouse_down":
            pyautogui.mouseDown()
            return "OK"

        if name == "left_mouse_up":
            pyautogui.mouseUp()
            return "OK"

        if name == "cursor_position":
            x, y = pyautogui.position()
            return f"X={round(x * self.scale)},Y={round(y * self.scale)}"

        if name == "scroll":
            if inp.get("coordinate"):
                pyautogui.moveTo(*self._to_screen(inp["coordinate"]))
            amount = int(inp.get("scroll_amount", 3)) * _SCROLL_MULTIPLIER
            direction = inp["scroll_direction"]
            with self._modifiers(inp.get("text")):
                if direction in ("up", "down"):
                    pyautogui.scroll(amount if direction == "up" else -amount)
                else:
                    pyautogui.hscroll(amount if direction == "right" else -amount)
            return "OK"

        if name == "type":
            self._type(inp["text"])
            return "OK"

        if name == "key":
            keys = _split_combo(inp["text"])
            for _ in range(max(1, min(int(inp.get("repeat", 1)), 100))):
                pyautogui.hotkey(*keys, interval=0.03)
            return "OK"

        if name == "hold_key":
            with self._modifiers(inp["text"]):
                time.sleep(min(float(inp["duration"]), 30))
            return "OK"

        if name == "wait":
            time.sleep(min(float(inp.get("duration", 1)), 30))
            return "OK"

        raise ValueError(f"Unsupported computer action: {name}")
