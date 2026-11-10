"""Mac apps, browser, shell, files, clipboard, volume."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

MAX_OUTPUT = 8000


def osascript(script: str, *args: str, timeout: int = 60) -> str:
    """Run AppleScript. Arguments are passed to `on run argv` (never spliced into the script)."""
    r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "AppleScript failed")
    return r.stdout.strip()


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n... [truncated {len(text) - MAX_OUTPUT} chars]"


# --- apps & browser ------------------------------------------------------------

def open_app(name: str) -> str:
    r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"Could not open {name}")
    return f"Opened {name}."


def quit_app(name: str) -> str:
    osascript('on run argv\n tell application (item 1 of argv) to quit\nend run', name)
    return f"Quit {name}."


def list_running_apps() -> str:
    return osascript('tell application "System Events" to get name of every application process whose background only is false')


def get_frontmost_window() -> str:
    script = '''
tell application "System Events"
    set p to first application process whose frontmost is true
    set appName to name of p
    set winTitle to ""
    try
        set winTitle to name of front window of p
    end try
end tell
return appName & linefeed & winTitle'''
    app, _, title = osascript(script).partition("\n")
    out = f"App: {app}\nWindow: {title or '(none)'}"
    url_scripts = {
        "Safari": 'tell application "Safari" to return URL of front document',
        "Google Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
        "Arc": 'tell application "Arc" to return URL of active tab of front window',
        "Microsoft Edge": 'tell application "Microsoft Edge" to return URL of active tab of front window',
        "Brave Browser": 'tell application "Brave Browser" to return URL of active tab of front window',
    }
    if app in url_scripts:
        try:
            out += f"\nURL: {osascript(url_scripts[app], timeout=10)}"
        except Exception:
            pass
    return out


def open_url(url: str) -> str:
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        url = "https://" + url
    subprocess.run(["open", url], check=True)
    return f"Opened {url}."


def run_applescript(script: str) -> str:
    return _truncate(osascript(script, timeout=120)) or "(no output)"


_RISKY_APPLESCRIPT = re.compile(
    r"\b(send|delete|do shell script|empty( the)? trash|shut ?down|restart|log ?out|sleep|"
    r"move .* to (the )?trash|keystroke return|key code 36|make new outgoing message|remove|erase)\b",
    re.I,
)


def applescript_confirmation(inp: dict) -> str | None:
    if _RISKY_APPLESCRIPT.search(inp["script"]):
        return "I'm about to run an AppleScript that can send, delete, or change things on your Mac. Should I go ahead?"
    return None


# --- shell -----------------------------------------------------------------------

_SAFE_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "grep", "find", "mdfind", "date", "cal", "whoami", "uname",
    "sw_vers", "df", "du", "ps", "uptime", "which", "echo", "file", "stat", "sort", "uniq",
    "system_profiler", "ifconfig", "defaults", "mdls", "printenv", "diskutil",
}
# Words that make an otherwise read-only command able to change things.
_SAFE_SUBCOMMAND_BLOCKLIST = {
    "defaults": {"write", "delete", "import", "rename"},
    "diskutil": {"erase", "erasedisk", "erasevolume", "partitiondisk", "unmount", "unmountdisk", "eject",
                 "mount", "rename", "repairvolume", "apfs"},
    "sort": {"-o", "--output"},
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"},
}


def _is_read_only(command: str) -> bool:
    if re.search(r"[;&`>]|\$\(|\|\|", command):
        return False
    for segment in command.split("|"):
        try:
            words = shlex.split(segment)
        except ValueError:
            return False
        if not words or words[0] not in _SAFE_COMMANDS:
            return False
        blocked = _SAFE_SUBCOMMAND_BLOCKLIST.get(words[0], set())
        if any(w.lower() in blocked for w in words[1:]):
            return False
    return True


def shell_confirmation(inp: dict) -> str | None:
    if _is_read_only(inp["command"]):
        return None
    return f"I want to run this command: {inp['command']}. Should I run it?"


def run_shell(command: str, timeout: int = 60) -> str:
    r = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=min(int(timeout), 300), cwd=Path.home()
    )
    out = (r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr.strip() else "")).strip()
    return _truncate(f"exit code {r.returncode}\n{out}")


# --- files & clipboard & volume ---------------------------------------------------

def find_files(query: str, folder: str | None = None, name_only: bool = True, limit: int = 25) -> str:
    cmd = ["mdfind"]
    if folder:
        cmd += ["-onlyin", os.path.expanduser(folder)]
    cmd += ["-name", query] if name_only else [query]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    lines = [ln for ln in r.stdout.splitlines() if ln][: int(limit)]
    return "\n".join(lines) or "No files found."


def get_clipboard() -> str:
    return _truncate(subprocess.run(["pbpaste"], capture_output=True, text=True).stdout) or "(clipboard is empty)"


def set_clipboard(text: str) -> str:
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    return "Copied to clipboard."


def set_volume(level: int | None = None, muted: bool | None = None) -> str:
    if level is not None:
        osascript(f"set volume output volume {max(0, min(int(level), 100))}")
    if muted is not None:
        osascript(f"set volume output muted {'true' if muted else 'false'}")
    return osascript("output volume of (get volume settings) as string") + "% volume"
