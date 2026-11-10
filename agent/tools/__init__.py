"""Tool registry: schemas Claude sees, the Python that runs them, and which ones need your OK first."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Callable

from .. import web
from . import comms, mac, ui
from .computer import TOOLSET_DEFINITION, TOOLSET_NAME, Computer

ConfirmFn = Callable[[str], bool]


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list[str]
    run: Callable[..., str]
    confirm: Callable[[dict], str | None] | None = None  # returns a question to ask, or None

    def local_definition(self) -> dict:
        """Ollama / OpenAI-style function schema."""
        d = self.definition()
        return {"type": "function",
                "function": {"name": d["name"], "description": d["description"], "parameters": d["input_schema"]}}

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": self.required,
                "additionalProperties": False,
            },
        }


S = {"type": "string"}

CUSTOM_TOOLS = [
    Tool("open_app", "Open (or bring to front) a Mac application by name, e.g. 'Safari', 'Spotify', 'Notes'.",
         {"name": S}, ["name"], mac.open_app),
    Tool("quit_app", "Quit a running Mac application by name.", {"name": S}, ["name"], mac.quit_app),
    Tool("list_running_apps", "List the visible applications that are currently running.", {}, [],
         mac.list_running_apps),
    Tool("get_frontmost_window",
         "Get the frontmost app, its window title, and the current URL if it is a browser. Use this to learn what "
         "the user is looking at (e.g. 'summarize this page', 'how do I use this').", {}, [],
         mac.get_frontmost_window),
    Tool("open_url", "Open a URL (or a search like https://www.google.com/search?q=...) in the default browser.",
         {"url": S}, ["url"], mac.open_url),
    Tool("run_applescript",
         "Run AppleScript to control scriptable apps (Music, Finder, Safari, Notes, Reminders, Calendar, System "
         "Events, ...). Returns the script's result. Prefer this over GUI clicking when an app is scriptable.",
         {"script": S}, ["script"], mac.run_applescript, mac.applescript_confirmation),
    Tool("run_shell",
         "Run a zsh command in the user's home directory and return its output. Read-only commands run immediately; "
         "anything else is confirmed with the user automatically.",
         {"command": S, "timeout": {"type": "integer", "description": "seconds, default 60"}}, ["command"],
         mac.run_shell, mac.shell_confirmation),
    Tool("find_files", "Find files with Spotlight. name_only=true matches file names; false searches contents too.",
         {"query": S, "folder": {"type": "string", "description": "optional folder, e.g. ~/Documents"},
          "name_only": {"type": "boolean"}, "limit": {"type": "integer"}}, ["query"], mac.find_files),
    Tool("get_clipboard", "Read the text currently on the clipboard.", {}, [], mac.get_clipboard),
    Tool("set_clipboard", "Put text on the clipboard.", {"text": S}, ["text"], mac.set_clipboard),
    Tool("set_volume", "Set output volume (0-100) and/or mute.",
         {"level": {"type": "integer"}, "muted": {"type": "boolean"}}, [], mac.set_volume),
    Tool("lookup_contact", "Search the Contacts app by name; returns phone numbers and emails.",
         {"name": S}, ["name"], comms.lookup_contact),
    Tool("read_messages",
         "Read recent iMessage/SMS messages, oldest first. Optionally filter by contact phone number, email, or "
         "group chat name (look names up with lookup_contact first).",
         {"contact": S, "limit": {"type": "integer", "description": "default 15"}}, [], comms.read_messages),
    Tool("send_imessage",
         "Send an iMessage (falls back to SMS) to a phone number or email. The user is asked to confirm automatically.",
         {"to": S, "text": S}, ["to", "text"], comms.send_imessage, comms.imessage_confirmation),
    Tool("read_emails", "Read the most recent emails in the Apple Mail inbox.",
         {"limit": {"type": "integer", "description": "default 10"}, "unread_only": {"type": "boolean"}}, [],
         comms.read_emails),
    Tool("send_email",
         "Send an email via Apple Mail. 'to' may be comma-separated. The user is asked to confirm automatically.",
         {"to": S, "subject": S, "body": S}, ["to", "subject", "body"], comms.send_email, comms.email_confirmation),
]

# Tools only the local brain gets. Claude runs web search/fetch on Anthropic's servers and clicks by vision;
# small local models get free web tools and click through Accessibility labels instead.
LOCAL_TOOLS = [
    Tool("list_ui_elements",
         "List the clickable controls and text fields of an app's windows, numbered, with their labels. Use this "
         "before clicking or typing in an app. Omit app for the frontmost app.",
         {"app": {"type": "string", "description": "app name, optional"}}, [], ui.list_ui_elements),
    Tool("click_element",
         "Click controls listed by list_ui_elements. Best: pass their exact labels in order, e.g. "
         "labels=[\"1\", \"2\", \"5\", \"Divide\", \"5\", \"Equals\"] or labels=[\"Displays\"]. "
         "Or pass element_id, the number in [brackets]. Returns the text on screen after.",
         {"labels": {"type": "array", "items": {"type": "string"}, "description": "exact labels, clicked in order"},
          "element_id": {"type": "integer"},
          "app": {"type": "string", "description": "app the controls are in"}},
         [], ui.click_element, ui.click_confirmation),
    Tool("type_into_element",
         "Replace the text of a field (number from list_ui_elements) with new text. submit=true presses Enter after.",
         {"element_id": {"type": "integer"}, "text": S, "submit": {"type": "boolean"},
          "app": {"type": "string", "description": "app the number came from"}}, ["element_id", "text"],
         ui.type_into_element, ui.type_confirmation),
    Tool("web_search", "Search the web (DuckDuckGo). Returns titles, links, and snippets.",
         {"query": S, "max_results": {"type": "integer", "description": "default 5"}}, ["query"], web.web_search),
    Tool("web_fetch", "Fetch a web page and return its text. Use after web_search to read a result.",
         {"url": S}, ["url"], web.web_fetch),
    Tool("get_weather", "Current weather and forecast for a city. Use this for any weather question.",
         {"location": {"type": "string", "description": "city name; omit for the user's home city"},
          "days": {"type": "integer", "description": "forecast days, default 3"}}, [], web.get_weather),
]

# No x,y clicking for the local brain: small models can't aim (tested: 0-1 of 5 hits on Calculator keys),
# so clicks go through click_element, and this tool is for looking, keys, typing and scrolling.
LOCAL_COMPUTER_ACTIONS = ["screenshot", "key", "type", "scroll", "wait"]
LOCAL_COMPUTER_TOOL = {
    "type": "function",
    "function": {
        "name": "computer",
        "description": (
            "'screenshot' shows you the screen (use it to describe or read what's visible). 'key' presses a key or "
            "combo like 'super+n' or 'enter' (super = Command, alt = Option). 'type' types text where the cursor is. "
            "'scroll' scrolls the frontmost window. To click anything, use list_ui_elements and click_element."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": LOCAL_COMPUTER_ACTIONS},
                "text": {"type": "string", "description": "text to type, or key combo for 'key'"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "description": "scroll clicks, default 3"},
                "seconds": {"type": "number", "description": "for 'wait'"},
            },
            "required": ["action"],
        },
    },
}

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
]

DECLINED = ("DECLINED BY USER: the action was NOT done. Do not retry it or try another way. "
            "Tell the user briefly that you didn't do it.")
SKIPPED = "Not executed: an earlier computer action in this turn failed."


def _shrink(b64: str, long_edge: int = 1024) -> str:
    """Smaller screenshots for local models: image tokens are the slowest part of a step."""
    import base64
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    if max(img.size) <= long_edge:
        return b64
    img.thumbnail((long_edge, long_edge), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _coerce(args: dict, tool: Tool) -> dict:
    """Small models sometimes send "5" for 5, "true" for True, or extra keys. Clean that up."""
    out = {}
    for k, v in args.items():
        spec = tool.properties.get(k)
        if spec is None or v is None:
            continue
        t = spec.get("type")
        try:
            if t == "integer" and not isinstance(v, int):
                v = int(float(v))
            elif t == "boolean" and not isinstance(v, bool):
                v = str(v).strip().lower() in ("true", "1", "yes")
            elif t == "string" and not isinstance(v, str):
                v = str(v)
        except (TypeError, ValueError):
            pass
        out[k] = v
    return out


class Toolbox:
    def __init__(self, confirm: ConfirmFn, log: Callable[[str], None] = print) -> None:
        self.confirm = confirm
        self.log = log
        self.declined = False  # set when the user says no; the local brain then stops using tools this turn
        self.computer = Computer()
        self._tools = {t.name: t for t in CUSTOM_TOOLS + LOCAL_TOOLS}
        self.definitions = [t.definition() for t in CUSTOM_TOOLS] + SERVER_TOOLS + [TOOLSET_DEFINITION]
        self.local_definitions = [t.local_definition() for t in CUSTOM_TOOLS + LOCAL_TOOLS] + [LOCAL_COMPUTER_TOOL]

    # -- local (Ollama) brain -------------------------------------------------
    def run_local(self, name: str, args: dict | None) -> tuple[str, list[str], bool]:
        """Run one tool call from the local model. Returns (text, base64 images, is_error)."""
        args = dict(args or {})
        if name == "computer":
            try:
                action, inp = self._local_computer_input(args)
            except (KeyError, TypeError, ValueError) as e:
                return f"Error: bad computer arguments ({e}).", [], True
            content, is_error = self._run_one(action, inp, TOOLSET_NAME)
        else:
            tool = self._tools.get(name)
            if tool is not None:
                args = _coerce(args, tool)
            content, is_error = self._run_one(name, args, None)
        if isinstance(content, str):
            return content, [], is_error
        images = [_shrink(c["source"]["data"]) for c in content if c.get("type") == "image"]
        text = " ".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
        return text or ("Screenshot attached." if images else "OK"), images, is_error

    def _local_computer_input(self, a: dict) -> tuple[str, dict]:
        action = str(a["action"])
        if action not in LOCAL_COMPUTER_ACTIONS:
            raise ValueError(f"unknown action {action!r}; to click, use list_ui_elements and click_element")
        inp: dict = {}
        if action in ("type", "key"):
            inp["text"] = str(a["text"])
        if action == "scroll":
            inp["scroll_direction"] = a.get("direction", "down")
            inp["scroll_amount"] = int(a.get("amount", 3))
        if action == "wait":
            inp["duration"] = float(a.get("seconds", 1))
        return action, inp

    def run_all(self, blocks) -> list[dict]:
        """Run every tool_use block in order and return one tool_result per block."""
        results, computer_failed = [], False
        for block in blocks:
            toolset = getattr(block, "toolset_name", None)
            result: dict = {"type": "tool_result", "tool_use_id": block.id}
            if toolset == TOOLSET_NAME:
                result["toolset_name"] = TOOLSET_NAME
                if computer_failed:
                    results.append({**result, "is_error": True, "content": SKIPPED})
                    continue
            content, is_error = self._run_one(block.name, dict(block.input or {}), toolset)
            if is_error and toolset == TOOLSET_NAME:
                computer_failed = True
            result["content"] = content if isinstance(content, list) else [{"type": "text", "text": content}]
            if is_error:
                result["is_error"] = True
            results.append(result)
        return results

    def _run_one(self, name: str, inp: dict, toolset: str | None) -> tuple[list[dict] | str, bool]:
        shown = {k: (v if len(str(v)) < 120 else str(v)[:117] + "...") for k, v in inp.items()}
        self.log(f"  -> {name} {shown if shown else ''}")
        try:
            if toolset == TOOLSET_NAME:
                return self.computer.run(name, inp), False
            tool = self._tools.get(name)
            if tool is None:
                return f"Unknown tool: {name}", True
            question = tool.confirm(inp) if tool.confirm else None
            if question and not self.confirm(question):
                self.log("     (declined)")
                self.declined = True
                return DECLINED, False
            return tool.run(**inp), False
        except Exception as e:  # report failures to Claude instead of crashing
            self.log(f"     error: {e}")
            if not isinstance(e, (RuntimeError, ValueError)):
                traceback.print_exc()
            return f"Error: {e}", True
