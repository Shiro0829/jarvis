"""Contacts, iMessage/SMS and Apple Mail."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .mac import osascript

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = datetime(2001, 1, 1)


# --- contacts ----------------------------------------------------------------------

def lookup_contact(name: str) -> str:
    script = '''
on run argv
    set q to item 1 of argv
    set out to ""
    tell application "Contacts"
        set found to (every person whose name contains q)
        set n to 0
        repeat with p in found
            set n to n + 1
            if n > 10 then exit repeat
            set out to out & name of p & ":"
            repeat with ph in phones of p
                set out to out & " phone(" & (label of ph) & ") " & (value of ph) & ";"
            end repeat
            repeat with em in emails of p
                set out to out & " email(" & (label of em) & ") " & (value of em) & ";"
            end repeat
            set out to out & linefeed
        end repeat
    end tell
    return out
end run'''
    return osascript(script, name, timeout=60) or f"No contact matching '{name}'."


# --- iMessage ----------------------------------------------------------------------

def _decode_attributed_body(blob: bytes | None) -> str:
    """Newer macOS stores message text only inside an NSAttributedString archive."""
    if not blob or b"NSString" not in blob:
        return ""
    data = blob.split(b"NSString", 1)[1][5:]
    if not data:
        return ""
    if data[0] == 0x81:
        length, start = int.from_bytes(data[1:3], "little"), 3
    else:
        length, start = data[0], 1
    return data[start:start + length].decode("utf-8", errors="replace")


def _apple_time(value: int | None) -> str:
    if not value:
        return "?"
    seconds = value / 1e9 if value > 1e11 else value  # nanoseconds on modern macOS
    utc = (APPLE_EPOCH + timedelta(seconds=seconds)).replace(tzinfo=timezone.utc)
    local = utc.astimezone()
    return local.strftime("%a %b %d %I:%M %p")


def read_messages(contact: str | None = None, limit: int = 15) -> str:
    try:
        db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        db.execute("SELECT 1 FROM message LIMIT 1")
    except sqlite3.Error as e:
        raise RuntimeError(
            "Can't read Messages history. Give your terminal app Full Disk Access in System Settings > "
            f"Privacy & Security > Full Disk Access, then restart it. ({e})"
        ) from e

    where, params = "", []
    if contact:
        digits = re.sub(r"\D", "", contact)
        needle = f"%{digits[-10:]}%" if len(digits) >= 7 else f"%{contact}%"
        where = "WHERE h.id LIKE ? OR c.display_name LIKE ? OR c.chat_identifier LIKE ?"
        params = [needle, needle, needle]

    rows = db.execute(
        f"""
        SELECT m.date, m.is_from_me, h.id, m.text, m.attributedBody, c.display_name, m.is_read
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        {where}
        ORDER BY m.date DESC LIMIT ?
        """,
        [*params, max(1, min(int(limit), 100))],
    ).fetchall()
    db.close()

    lines = []
    for date, from_me, handle, text, body, group, is_read in reversed(rows):
        text = text or _decode_attributed_body(body) or "[attachment or reaction]"
        who = "Me" if from_me else (handle or "unknown")
        chat = f" (group: {group})" if group else ""
        unread = " [unread]" if not from_me and not is_read else ""
        lines.append(f"{_apple_time(date)} | {who}{chat}{unread}: {text.strip()}")
    return "\n".join(lines) or "No messages found."


def send_imessage(to: str, text: str) -> str:
    script = '''
on run argv
    set targetHandle to item 1 of argv
    set msg to item 2 of argv
    set svcType to item 3 of argv
    tell application "Messages"
        if svcType is "SMS" then
            set svc to 1st account whose service type = SMS
        else
            set svc to 1st account whose service type = iMessage
        end if
        send msg to participant targetHandle of svc
    end tell
end run'''
    try:
        osascript(script, to, text, "iMessage")
        return f"Sent iMessage to {to}."
    except RuntimeError as imessage_error:
        try:
            osascript(script, to, text, "SMS")
            return f"Sent SMS to {to}."
        except RuntimeError:
            raise imessage_error


def imessage_confirmation(inp: dict) -> str:
    return f"Send this message to {inp['to']}: {inp['text']}. Should I send it?"


# --- Mail ---------------------------------------------------------------------------

def read_emails(limit: int = 10, unread_only: bool = False) -> str:
    script = '''
on run argv
    set n to (item 1 of argv) as integer
    set unreadOnly to (item 2 of argv) is "true"
    set out to ""
    tell application "Mail"
        if unreadOnly then
            set msgs to (messages of inbox whose read status is false)
        else
            set msgs to messages of inbox
        end if
        set c to count of msgs
        if c > n then set c to n
        repeat with i from 1 to c
            set m to item i of msgs
            set bodyText to content of m
            if length of bodyText > 500 then set bodyText to text 1 thru 500 of bodyText
            set out to out & "From: " & (sender of m) & linefeed & "Subject: " & (subject of m) & linefeed & ¬
                "Date: " & ((date received of m) as string) & linefeed & bodyText & linefeed & "---" & linefeed
        end repeat
    end tell
    return out
end run'''
    out = osascript(script, str(max(1, min(int(limit), 30))), "true" if unread_only else "false", timeout=120)
    return out or "No emails found."


def send_email(to: str, subject: str, body: str) -> str:
    script = '''
on run argv
    set AppleScript's text item delimiters to ","
    set addrs to text items of (item 1 of argv)
    set AppleScript's text item delimiters to ""
    tell application "Mail"
        set m to make new outgoing message with properties {subject:(item 2 of argv), content:(item 3 of argv), visible:false}
        tell m
            repeat with a in addrs
                make new to recipient at end of to recipients with properties {address:(a as string)}
            end repeat
        end tell
        send m
    end tell
end run'''
    recipients = ",".join(a.strip() for a in to.split(",") if a.strip())
    osascript(script, recipients, subject, body, timeout=60)
    return f"Email sent to {recipients}."


def email_confirmation(inp: dict) -> str:
    return f"Send an email to {inp['to']} with subject {inp['subject']}, saying: {inp['body']}. Should I send it?"
