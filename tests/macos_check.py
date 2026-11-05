"""Check the real macOS voices, not part of the unit tests.

Run it on a Mac with: python3 tests/macos_check.py
It lists the voices, renders audio with several of them, and compares the results.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_voices import lv  # noqa: E402

TEXT = "Hello, this is a voice test."
failures = []


def check(label, passed, detail=""):
    print("%s %s %s" % ("PASS" if passed else "FAIL", label, detail), flush=True)
    if not passed:
        failures.append(label)


def render(voice):
    engine = lv.MacSayEngine(dict(lv.DEFAULTS, voice=voice[0]), voice[1])
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "out.wav")
        engine.synth(TEXT, path, voice[1])
        return Path(path).read_bytes()


def main():
    check("running on macOS", lv.IS_MAC)
    voices = lv.mac_voices()
    check("voices are listed", len(voices) > 0, "%d voices" % len(voices))
    print([voice[:2] for voice in voices[:12]], flush=True)

    engine = lv.resolve_engine(dict(lv.DEFAULTS), language="en-US")
    check("system voices come first", engine.key == "say", engine.key)
    check("automatic mode finds an English voice", engine._pick("en-US") not in (None, ""),
          engine._pick("en-US"))

    tested = voices[:: max(1, len(voices) // 6)][:6]
    heard = set()
    for voice in tested:
        try:
            audio = render(voice)
        except lv.EngineError as error:
            check("voice renders audio: " + voice[0], False, error)
            continue
        check("voice renders audio: " + voice[0], len(audio) > 1000, "%d bytes" % len(audio))
        heard.add(audio)
    check("voices sound different", len(heard) > 1 or len(tested) < 2,
          "%d / %d" % (len(heard), len(tested)))

    print("FAILED: %s" % failures if failures else "ALL PASSED", flush=True)
    sys.exit(1 if failures else 0)


main()
