"""Voice agent for macOS.

    python main.py              # say the wake word ("hey jarvis"), then your request
    python main.py --mode enter # press Enter, then speak (no wake word)
    python main.py --mode text  # type requests instead of speaking (good for testing)
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from agent import config

STOP_PHRASES = re.compile(r"^(never ?mind|cancel|stop|that'?s all|that'?s it|thanks?( you)?|goodbye|bye)\W*$", re.I)


def check_setup() -> None:
    if config.BRAIN not in ("ollama", "claude"):
        sys.exit(f"VA_BRAIN must be 'ollama' or 'claude', not {config.BRAIN!r}.")
    if config.BRAIN == "claude" and not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key "
                 "(https://platform.claude.com/settings/keys).")


def make_agent(toolbox):
    if config.BRAIN == "claude":
        from agent.brain import Agent

        agent = Agent(toolbox)
    else:
        from agent.local_brain import LocalAgent

        print(f"Loading {config.OLLAMA_MODEL}...")
        agent = LocalAgent(toolbox)
    agent.warm_up()
    return agent


def run_text_mode(speak: bool) -> None:
    from agent.audio import Speaker
    from agent.tools import Toolbox

    speaker = Speaker() if speak else None

    def confirm(question: str) -> bool:
        if speaker:
            speaker.wait()
        return input(f"\n{question} [y/N] ").strip().lower() in ("y", "yes")

    agent = make_agent(Toolbox(confirm))
    print(f"{config.ASSISTANT_NAME} ready. Type a request (Ctrl+C to quit, 'reset' to start over).")
    while True:
        try:
            text = input("\nyou> ").strip()
        except EOFError:
            return
        if not text:
            continue
        if text.lower() == "reset":
            agent.reset()
            print("(conversation cleared)")
            continue
        speech = speaker.stream() if speaker else None
        printed = False

        def on_text(delta: str) -> None:
            nonlocal printed
            if not printed:
                print(f"{config.ASSISTANT_NAME.lower()}> ", end="")
                printed = True
            print(delta, end="", flush=True)
            if speech:
                speech.feed(delta)

        try:
            reply = agent.handle(text, on_text=on_text)
        except Exception as e:
            print(f"\nerror: {getattr(e, 'message', e)}")
            continue
        print() if printed else print(f"{config.ASSISTANT_NAME.lower()}> {reply}")
        if speech:
            speech.finish(fallback=reply)


_JUST_WAKE = re.compile(r"^\W*(?:hey|hi|ok|okay|a)?\W*(?:jarvis|service|nervous)?\W*$", re.I)


def run_voice_mode(use_wake_word: bool) -> None:
    from agent.audio import Microphone, Speaker, Transcriber, WakeWordDetector, chime, is_affirmative, record_utterance
    from agent.tools import Toolbox

    print("Loading speech models (the first run downloads them)...")
    transcriber = Transcriber()
    wake = WakeWordDetector() if use_wake_word else None
    speaker = Speaker()
    mic = Microphone(gate=lambda: speaker.busy)  # ignore the mic while Jarvis is talking

    def say(text: str) -> None:
        print(f"{config.ASSISTANT_NAME.lower()}> {text}")
        speaker.wait()
        speaker.say(text)

    def listen(timeout: float, flush: bool = True, quiet: bool = False) -> str:
        speaker.wait()
        if flush:
            mic.flush()
        audio = record_utterance(mic, start_timeout=timeout)
        if audio is None:
            if not quiet:
                print(f"(didn't hear a request: loudest sound {mic.peak:.0f}, speech needs {mic.threshold:.0f}"
                      + ("; your mic may be too quiet or muted" if mic.peak < mic.threshold * 0.5 else "") + ")")
            return ""
        chime("Pop")
        text = transcriber.transcribe(audio)
        if text:
            print(f"you> {text}")
        else:
            print(f"(heard {len(audio) / 16000:.1f}s of sound but couldn't make out any words)")
        return text

    def confirm(question: str) -> bool:
        say(question)
        answer = listen(timeout=7)
        ok = is_affirmative(answer)
        if not ok:
            say("Okay, I won't.")
        return ok

    agent = make_agent(Toolbox(confirm))
    mic.start()
    print(f"Microphone: {mic.device_name()}")
    say(f"{config.ASSISTANT_NAME} is ready.")

    while True:
        if wake:
            print(f"\nListening for '{wake.phrase}'...")
            wake.wait(mic)
        else:
            input("\nPress Enter, then speak...")
            mic.flush()
        chime("Tink")

        timeout = 6.0
        first = True
        while True:  # conversation: keep listening for follow-ups without the wake word
            # Right after the wake word, keep what's already buffered: people say "Hey Jarvis, open Safari" in one go.
            text = listen(timeout, flush=not first, quiet=not first)
            if first and text and _JUST_WAKE.match(text):  # caught only the end of the wake word: wait for the request
                text = listen(timeout, flush=False)
            first = False
            if not text:
                break
            if STOP_PHRASES.match(text):
                chime("Bottle")
                break
            speech = speaker.stream()
            try:
                reply = agent.handle(text, on_text=speech.feed)
                print(f"{config.ASSISTANT_NAME.lower()}> {reply}")
                speech.finish(fallback=reply)
            except Exception as e:
                print(f"error: {e!r}")
                speech.finish()
                say("Sorry, something went wrong on my end.")
            timeout = config.FOLLOWUP_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["wake", "enter", "text"], default="wake")
    parser.add_argument("--speak", action="store_true", help="text mode: also speak replies aloud")
    args = parser.parse_args()
    check_setup()
    try:
        if args.mode == "text":
            run_text_mode(args.speak)
        else:
            run_voice_mode(use_wake_word=args.mode == "wake")
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
