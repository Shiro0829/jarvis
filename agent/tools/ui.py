"""Operate apps through macOS Accessibility: list the controls on screen with numbers, then click or type by number.

Small local models can't reliably click by looking at pixels, but they can pick "[12] button 'Send'" from a list.
Requires Accessibility permission for the terminal app (System Settings > Privacy & Security > Accessibility).
"""

from __future__ import annotations

import re
import time

from AppKit import NSWorkspace
from Foundation import NSDate, NSRunLoop
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXValueGetValue,
    kAXErrorSuccess,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)

_INTERACTIVE = {
    "AXButton": "button", "AXTextField": "text field", "AXTextArea": "text area", "AXSearchField": "search field",
    "AXCheckBox": "checkbox", "AXRadioButton": "option", "AXPopUpButton": "pop-up menu", "AXMenuButton": "menu button",
    "AXComboBox": "combo box", "AXLink": "link", "AXSlider": "slider", "AXRow": "row", "AXTab": "tab",
    "AXDisclosureTriangle": "disclosure", "AXIncrementor": "stepper", "AXSecureTextField": "password field",
}
_TEXT_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField"}
_MAX_ELEMENTS = 150
_MAX_SECONDS = 2.0
_RISKY = re.compile(r"\b(send|delete|remove|erase|trash|buy|purchase|pay|order|checkout|submit|confirm|post|publish|"
                    r"sign out|log out|unsubscribe|transfer|reset|uninstall|format)\b", re.I)

_elements: dict[int, tuple] = {}  # id -> (element, role, label, app name), refreshed by every list call


def _get(el, attr):
    err, value = AXUIElementCopyAttributeValue(el, attr, None)
    return value if err == kAXErrorSuccess else None


def _frame(el):
    pos, size = _get(el, "AXPosition"), _get(el, "AXSize")
    if pos is None or size is None:
        return None
    _, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    _, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    return p.x, p.y, s.width, s.height


def _text_of(el, depth: int = 0) -> str:
    """Label for rows/cells: the first bits of static text inside."""
    if depth > 4:
        return ""
    bits = []
    for c in _get(el, "AXChildren") or []:
        if _get(c, "AXRole") == "AXStaticText":
            v = _get(c, "AXValue")
            if v:
                bits.append(str(v))
        else:
            bits.append(_text_of(c, depth + 1))
        if sum(len(b) for b in bits) > 80:
            break
    return " ".join(b for b in bits if b).strip()


def _label(el, role: str) -> str:
    for attr in ("AXTitle", "AXDescription"):
        v = _get(el, attr)
        if v:
            return str(v)
    if role in ("AXRow", "AXLink", "AXButton", "AXRadioButton", "AXCheckBox"):
        t = _text_of(el)
        if t:
            return t
    for attr in ("AXPlaceholderValue", "AXHelp", "AXIdentifier"):
        v = _get(el, attr)
        if v:
            return str(v)
    return ""


def _refresh() -> None:
    """NSWorkspace only learns about launched/activated apps while a run loop spins; a CLI never spins one,
    so its app list would stay frozen at the first call. Spin briefly to take in the updates."""
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))


def _app(name: str = ""):
    _refresh()
    ws = NSWorkspace.sharedWorkspace()
    if not name:
        return ws.frontmostApplication()
    n = name.lower().removesuffix(".app")
    for _ in range(12):  # an app that was just opened can take a few seconds to show up
        apps = [a for a in ws.runningApplications() if a.activationPolicy() == 0 and a.localizedName()]
        match = next((a for a in apps if a.localizedName().lower() == n), None) or \
            next((a for a in apps if n in a.localizedName().lower()), None)
        if match is not None:
            return match
        time.sleep(0.25)
        _refresh()
    raise RuntimeError(f"{name} isn't running. Open it first with open_app.")


def _check_trusted() -> None:
    if not AXIsProcessTrusted():
        raise RuntimeError("Accessibility permission is missing: allow your terminal app in System Settings > "
                           "Privacy & Security > Accessibility, then restart it.")


def list_ui_elements(app: str = "") -> str:
    _check_trusted()
    target = _app(app)
    name = target.localizedName()
    ax = AXUIElementCreateApplication(target.processIdentifier())
    windows = _get(ax, "AXWindows") or []
    for _ in range(12):  # a just-launched app may not have drawn its window yet
        if windows:
            break
        time.sleep(0.25)
        windows = _get(ax, "AXWindows") or []
    focused = _get(ax, "AXFocusedWindow")
    if focused is not None:
        windows = [focused] + [w for w in windows if w != focused]
    if not windows:
        return f"{name} has no open windows."

    _elements.clear()
    lines: list[str] = []
    texts: list[str] = []
    deadline = time.monotonic() + _MAX_SECONDS

    def walk(el, depth: int) -> None:
        if len(_elements) >= _MAX_ELEMENTS or depth > 40 or time.monotonic() > deadline:
            return
        role = _get(el, "AXRole") or ""
        if role == "AXStaticText":
            v = _get(el, "AXValue")
            if v and len(texts) < 40 and str(v).strip():
                texts.append(str(v).strip()[:120])
            return
        subrole = _get(el, "AXSubrole") or ""
        kind = "search field" if subrole == "AXSearchField" else _INTERACTIVE.get(role)
        if kind and _get(el, "AXEnabled") is not False:
            f = _frame(el)
            if f and f[2] > 1 and f[3] > 1:
                label = _label(el, role)
                if label or role in _TEXT_ROLES:
                    i = len(_elements) + 1
                    _elements[i] = (el, role, label, name)
                    extra = ""
                    if role in _TEXT_ROLES and role != "AXSecureTextField":
                        v = _get(el, "AXValue")
                        extra = f' value="{str(v)[:60]}"' if v else ' (empty)'
                    elif role in ("AXCheckBox", "AXRadioButton"):
                        extra = " (on)" if _get(el, "AXValue") else " (off)"
                    lines.append(f'[{i}] {kind} "{label[:70]}"{extra}')
                    if role == "AXRow":
                        return  # the row's label already covers its contents
        for c in _get(el, "AXChildren") or []:
            walk(c, depth + 1)

    for w in windows[:3]:
        title = _get(w, "AXTitle") or "untitled"
        lines.append(f"-- window: {title}")
        walk(w, 0)
    more = " (list cut short; there are more)" if len(_elements) >= _MAX_ELEMENTS else ""
    shown = "\n".join(f"  {t}" for t in dict.fromkeys(texts))  # de-duplicated, in screen order
    return f"{name} controls{more}:\n" + "\n".join(lines) + (f"\nText on screen:\n{shown}" if shown else "")


def _lookup(element_id, app: str = "") -> tuple:
    try:
        found = _elements[int(element_id)]
    except (KeyError, ValueError, TypeError):
        raise RuntimeError("Unknown element number. Call list_ui_elements again to get fresh numbers.") from None
    if app and app.lower() not in found[3].lower():
        raise RuntimeError(f"Element numbers are from {found[3]}, not {app}. Call list_ui_elements for {app} first.")
    return found


def _click(el) -> None:
    actions = AXUIElementCopyActionNames(el, None)[1] or []
    if "AXPress" in actions and AXUIElementPerformAction(el, "AXPress") == kAXErrorSuccess:
        return
    if _get(el, "AXRole") == "AXRow" and AXUIElementSetAttributeValue(el, "AXSelected", True) == kAXErrorSuccess:
        return
    f = _frame(el)
    if f is None:
        raise RuntimeError("That element is no longer on screen. Call list_ui_elements again.")
    import pyautogui

    pyautogui.click(f[0] + f[2] / 2, f[1] + f[3] / 2)


def _screen_text(el, limit: int = 300) -> str:
    """The static text of the element's window, so the model sees the effect of what it just did."""
    win = _get(el, "AXWindow")
    if win is None:
        return ""
    texts: list[str] = []

    def walk(e, depth: int) -> None:
        if depth > 25 or sum(map(len, texts)) > limit:
            return
        if _get(e, "AXRole") == "AXStaticText":
            v = _get(e, "AXValue")
            if v and str(v).strip():
                texts.append(str(v).strip())
            return
        for c in _get(e, "AXChildren") or []:
            walk(c, depth + 1)

    walk(win, 0)
    return " | ".join(dict.fromkeys(texts))[:limit]


def _by_label(label: str, app: str = "") -> tuple:
    """Find a control from the last list by its label: exact match first, then a unique partial match."""
    want = str(label).strip().lower()
    items = [e for e in _elements.values() if not app or app.lower() in e[3].lower()]
    if not items:
        raise RuntimeError("Call list_ui_elements first.")
    exact = [e for e in items if e[2].strip().lower() == want]
    if exact:
        return exact[0]
    part = [e for e in items if want and want in e[2].lower()]
    if len(part) == 1:
        return part[0]
    if part:
        raise RuntimeError(f'"{label}" matches several controls: {", ".join(repr(e[2]) for e in part[:5])}. Be exact.')
    raise RuntimeError(f'No control labeled "{label}". Call list_ui_elements to see the labels.')


def _targets(inp: dict) -> list:
    """The controls to click, from labels (preferred) or element numbers. Checks all before anything is clicked."""
    app = inp.get("app", "")
    labels = inp.get("labels")
    if isinstance(labels, str):
        labels = [labels]
    if labels:
        return [_by_label(lb, app) for lb in labels]
    ids = inp.get("element_ids")
    if isinstance(ids, str):  # small models sometimes send "5, 8, 6"
        ids = re.findall(r"\d+", ids)
    ids = list(ids or []) or ([inp["element_id"]] if inp.get("element_id") is not None else [])
    if not ids:
        raise RuntimeError("Give labels, or element_id, from list_ui_elements.")
    return [_lookup(i, app) for i in ids]


def click_element(element_id: int | None = None, app: str = "", labels: list | None = None,
                  element_ids: list | None = None) -> str:
    """Click one control, or several in order (one model step instead of one per click: each step costs seconds)."""
    found = _targets({"element_id": element_id, "app": app, "labels": labels, "element_ids": element_ids})
    for n, (el, _, _, _) in enumerate(found):
        if n:
            time.sleep(0.12)
        _click(el)
    time.sleep(0.3)
    seen = _screen_text(found[-1][0])
    what = ", ".join(f'"{f[2]}"' for f in found)
    return (f"Clicked {what} in {found[-1][3]}."
            + (f" Text on screen now: {seen}" if seen else "")
            + " If the window changed, list_ui_elements again before clicking more.")


def click_confirmation(inp: dict) -> str | None:
    try:
        found = _targets(inp)
    except RuntimeError:
        return None
    risky = [f[2] for f in found if _RISKY.search(f[2])]
    if risky:
        return f'I\'m about to click "{risky[0]}" in {found[0][3]}. Should I go ahead?'
    return None


def type_into_element(element_id: int, text: str, submit: bool = False, app: str = "") -> str:
    el, role, label, app = _lookup(element_id, app)
    if role == "AXSecureTextField":
        raise RuntimeError("That's a password field. Ask the user to type it themselves.")
    if role in _TEXT_ROLES and AXUIElementSetAttributeValue(el, "AXFocused", True) != kAXErrorSuccess:
        _click(el)
    elif role not in _TEXT_ROLES:
        _click(el)
    time.sleep(0.15)
    import pyautogui

    from .computer import Computer

    pyautogui.hotkey("command", "a", interval=0.03)  # replace what's there
    Computer._type(None, text)  # handles long / non-ASCII text via the clipboard
    if submit:
        pyautogui.press("enter")
        time.sleep(0.4)
    seen = _screen_text(el)
    return (f'Typed into "{label or role}" in {app}' + (" and pressed Enter." if submit else ".")
            + (f" Text on screen now: {seen}" if seen else ""))


def type_confirmation(inp: dict) -> str | None:
    if inp.get("submit"):
        try:
            _, _, label, app = _lookup(inp.get("element_id"), inp.get("app", ""))
        except RuntimeError:
            return None
        if _RISKY.search(label) or app in ("Messages", "Mail", "Slack", "WhatsApp", "Discord", "Telegram"):
            return f'I\'m about to type "{inp.get("text", "")[:80]}" and press Enter in {app}. Should I?'
    return None
