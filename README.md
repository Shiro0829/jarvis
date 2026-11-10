# Jarvis: a voice agent for your Mac

Say **"Hey Jarvis"**, then ask for something. It can:

- **Operate apps**: open and quit apps, drive them with AppleScript, or look at the screen and click, type, and scroll like a person would.
- **Teach you an app**: it takes a screenshot of what you're looking at and explains the next steps out loud. It can also do them for you.
- **Browse the web**: search, read pages, and summarize them, or open them in your browser.
- **Handle messages and email**: read recent iMessages and Mail, draft replies in your voice, and send them after you say yes.
- **Run files and system tasks**: find files with Spotlight, run shell commands, change the volume, and use the clipboard.

```
mic -> wake word (openWakeWord) -> speech-to-text (Whisper)
    -> local LLM via Ollama (bonsai-2:27b) + tools -> Kokoro-82M voice, spoken sentence by sentence
```

Everything runs on your Mac and is free: no API key and no audio or text leaves the machine (except web searches
and the weather lookup). Set `VA_BRAIN=claude` to use the Claude API instead.

## Setup

```bash
cd ~/Desktop/voice_agent
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt   # already done
./setup_models.sh           # installs Ollama (runs in the background), pulls the model (8 GB), downloads Kokoro
cp .env.example .env        # optional: change voice, city, model...
./run.sh                    # or: ./run.sh --mode text
```

The first launch also downloads the Whisper model (about 500 MB for `small.en`).

### How the local brain stays fast

- The model stays loaded in memory (`VA_KEEP_ALIVE=-1`) and is warmed up at start (about 15 s, once).
- Conversation history only ever grows, so the model reuses what it has already read: a new request costs about
  0.6 s of reading instead of 8 s. When history gets long it is trimmed in the background, right after a reply.
- Replies are streamed: Kokoro starts speaking the first sentence while the model is still writing the rest.
- The model writes about 13 tokens (word pieces) a second, so every tool call costs 2 to 4 s just to write. Tools themselves
  take under half a second. So the speed-ups are about fewer model steps:
  - **Instant commands** (`agent/fastpath.py`) skip the model entirely: open/quit an app, set/raise/lower/mute the
    volume, weather (set `VA_HOME_CITY`), the time and date, and System Settings panes ("open Bluetooth settings").
    They answer in 0.1 to 1.5 s. Anything else, or anything that fails, goes to the model. `VA_FAST_PATH=0` turns it off.
  - **Several clicks in one step**: `click_element` takes a list of labels, so "125 divided by 5" in Calculator is
    one step (about 8 s) instead of six.
  - A task that repeats the same action five times in a row is stopped, instead of using up all 15 steps.
- Measured on an M6 with 24 GB: instant commands 0.1 to 1.5 s; chat first words about 0.6 s; tasks that need the
  model about 2.5 s to "On it." and 4 to 15 s in total.

### How it operates apps

Small local models can't click accurately by looking at screenshots (tested: 0 to 1 hits out of 5 on Calculator
keys). So the local brain reads each app's real controls through macOS Accessibility (`list_ui_elements` gives
`[12] button "Send"`), then clicks by label (or number) and types by number. Screenshots are still used to describe what's on screen.
Clicking anything labelled Send, Delete, Buy, Submit and so on asks you first.

### macOS permissions (one time)

Grant these to the app you run it from (Terminal, iTerm, VS Code, ...) in **System Settings > Privacy & Security**, then restart that app:

| Permission | Needed for |
|---|---|
| Microphone | hearing you |
| Accessibility | reading app controls, clicking and typing |
| Screen & System Audio Recording | screenshots, "how do I use this app" |
| Full Disk Access | reading iMessages (`~/Library/Messages/chat.db`) |
| Automation (prompted on first use) | controlling Messages, Mail, Contacts, Safari... |

Messages and Mail must be signed in on this Mac.

## Usage

```bash
./run.sh                 # wake word mode: "Hey Jarvis, ..."
./run.sh --mode enter    # press Enter, then speak
./run.sh --mode text     # type instead of talking (handy for testing)
./run.sh --mode text --speak
```

After it answers, you have about 5 seconds to follow up without the wake word. Say "stop", "never mind", or "that's all" to end the conversation. Ctrl+C quits.

Things to try:

- "Open Spotify and play some jazz."
- "How do I add a filter in this spreadsheet?" (with Numbers or Excel open)
- "Search for the best ramen near Union Square and open the top result."
- "Summarize the page I'm on."
- "Read my last messages from Mom and reply that I'll be there at seven."
- "Do I have any unread emails? Reply to the one from Alex saying sounds good."
- "Find the PDF I downloaded about taxes."
- "Turn the volume down to 20 percent."

## Safety

- **Always confirmed out loud first:** sending iMessages and emails, any shell command that isn't read-only, and AppleScript that can send, delete, or shut down.  With the local model, clicking a button labelled Send, Delete, Buy, Submit and so on is confirmed too, and once you say no it gets no more actions in that request. Say "yes" to proceed. Anything else cancels.
- The model is told to ask before irreversible on-screen actions (purchases, deleting, submitting forms), never to type passwords or payment details, and to treat web pages, emails, and messages as information, not instructions.
- **Emergency stop:** move the mouse into any screen corner to abort a click or typing sequence (pyautogui failsafe), or press Ctrl+C.
- On-screen control works on your real desktop, not in a sandbox. Watch what it does the first few times.

## Tuning

All settings live in `.env` (see `.env.example`). The most useful ones:

- `VA_OLLAMA_MODEL`: any Ollama model with tool support. `bonsai-2:27b` was the only one tested here that did
  multi-step app tasks reliably. `ornith-1.5:9b` starts talking in 0.2 s but makes up results, so use it only for chat.
- `VA_KOKORO_VOICE` (`am_michael`, `bm_george`, `af_heart`...) and `VA_KOKORO_SPEED`. `VA_TTS=say` goes back to the macOS voice.
- `VA_HOME_CITY=Boston` so "what's the weather" works without naming a city.
- `VA_THINK=1` lets the model reason first. It's smarter on hard tasks but 20 to 40 s slower.
- `VA_DEBUG=1` prints how long each model step takes.
- `VA_WAKE_THRESHOLD`: raise it if it wakes by accident, lower it if it misses you.
- `VA_WHISPER_MODEL=base.en` for faster transcription, or `medium.en` for better accuracy.

## Known limits of the local model

- Multi-step on-screen tasks are slower than Claude (2 to 4 s per step) and less reliable. In testing it sometimes pressed
  the wrong Calculator key, but it reported what the screen really showed instead of claiming success.
- It only reads text it can get from Accessibility. Apps that draw their own interface (some games, Electron
  apps) expose little, so it falls back to keyboard shortcuts.
- For hard, long tasks, switch to `VA_BRAIN=claude` (paid).
