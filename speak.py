"""Save a spoken copy of text and play it on Windows."""

import argparse
import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

import edge_tts

from ilkelma.engine.language import detect
from ilkelma.languages import packs


OUT = Path(__file__).resolve().parent / "out"
VOICES = {code: pack["voices"] for code, pack in packs().items()}


async def speak(text, voices, play, output=None):
    OUT.mkdir(exist_ok=True)
    for voice in voices:
        path = output or OUT / f"{voice}-{uuid4().hex[:8]}.mp3"
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(text, voice, connect_timeout=8, receive_timeout=12).save(path),
                timeout=20,
            )
        except Exception as error:
            path.unlink(missing_ok=True)
            print(f"{voice}: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        print(f"Saved {path}")
        if play:
            try:
                os.startfile(path)
            except OSError as error:
                print(f"Could not open the audio player: {error}", file=sys.stderr)
                return 1
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(description="Read text aloud using online neural voices.")
    parser.add_argument("text")
    parser.add_argument("--lang", choices=VOICES)
    parser.add_argument("--voice", choices=[voice for group in VOICES.values() for voice in group])
    parser.add_argument("--save-only", action="store_true")
    parser.add_argument("--output", type=Path, help="Save to this MP3 file.")
    args = parser.parse_args()
    if not args.text.strip():
        parser.error("Enter some text to read.")
    voices = (args.voice,) if args.voice else VOICES[args.lang or detect(args.text)]
    return asyncio.run(speak(args.text, voices, not args.save_only, args.output))


if __name__ == "__main__":
    sys.exit(main())
