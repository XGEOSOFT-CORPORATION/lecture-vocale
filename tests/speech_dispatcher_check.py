"""Check what Speech Dispatcher really outputs, not part of the unit tests.

Needs a running PulseAudio with a sink whose monitor is given as first argument.
Each command built by the extension is run and recorded, then the recordings are
compared: a voice type or pitch option that had no effect fails the check.
"""

import array
import importlib.util
import os
import subprocess
import sys
import time
import types
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MONITOR = sys.argv[1]
failures = []


def check(label, passed, detail=""):
    print("%s %s %s" % ("PASS" if passed else "FAIL", label, detail), flush=True)
    if not passed:
        failures.append(label)


def load_extension():
    interface = lambda name: type(name, (), {})
    sys.modules["uno"] = types.ModuleType("uno")
    helper = types.ModuleType("unohelper")
    helper.Base = interface("Base")
    helper.ImplementationHelper = mock.MagicMock()
    sys.modules["unohelper"] = helper
    for name in ("com", "com.sun", "com.sun.star", "com.sun.star.task", "com.sun.star.awt"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["com.sun.star.task"].XJobExecutor = interface("XJobExecutor")
    for name in ("XActionListener", "XCallback", "XAdjustmentListener", "XItemListener",
                 "XTopWindowListener"):
        setattr(sys.modules["com.sun.star.awt"], name, interface(name))
    os.environ["LECTURE_VOCALE_HEADLESS"] = "1"
    path = ROOT / "extension" / "python" / "lecture_vocale.py"
    spec = importlib.util.spec_from_file_location("lecture_vocale", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lv = load_extension()


def record(command, name):
    """Run a command while recording the sink; return the mono samples."""
    path = "/tmp/%s.wav" % name
    recorder = subprocess.Popen(["parec", "-d", MONITOR, "--file-format=wav", "--channels=1",
                                 "--rate=16000", "--format=s16le", path])
    time.sleep(0.5)
    result = subprocess.run(command, timeout=60)
    time.sleep(0.5)
    recorder.terminate()
    recorder.wait()
    if result.returncode != 0:
        print("command failed:", command, result.returncode, flush=True)
    with wave.open(path) as audio:
        samples = array.array("h", audio.readframes(audio.getnframes()))
    return samples


def voiced(samples):
    """Keep the parts of the recording that are not silence."""
    frame = 320
    kept = array.array("h")
    for start in range(0, len(samples) - frame, frame):
        block = samples[start:start + frame]
        if max(abs(value) for value in block) > 800:
            kept.extend(block)
    return kept


def pitch_hz(samples, rate=16000):
    """Median fundamental frequency, by autocorrelation on 40 ms frames."""
    estimates = []
    frame = 640
    low, high = rate // 400, rate // 70
    for start in range(0, len(samples) - frame - high, frame * 2):
        block = samples[start:start + frame]
        best, best_lag = 0, 0
        for lag in range(low, high):
            score = sum(block[i] * samples[start + i + lag] for i in range(0, frame, 2))
            if score > best:
                best, best_lag = score, lag
        if best_lag:
            estimates.append(rate / best_lag)
    estimates.sort()
    return estimates[len(estimates) // 2] if estimates else 0


def speak(name, text, language, **settings):
    cfg = dict(lv.DEFAULTS, **settings)
    engine = lv.SpdEngine(cfg, language)
    command, _ = engine.speak_command(text, language)
    print(name, " ".join(command), flush=True)
    return voiced(record(command, name))


def main():
    voices = lv.spd_voices(lv._which("spd-say"))
    languages = {lv.primary_language(voice[1]) for voice in voices}
    check("voices are listed", 20 < len(voices) < 1000, "%d voices" % len(voices))
    for language in ("fr", "en-US", "en-GB", "ar", "ru", "zh-CN", "zh-TW", "ja", "ko", "hi",
                     "de", "es", "pt-BR", "it", "nl", "pl", "tr", "vi", "sv", "nb", "he", "th",
                     "id", "sw", "el", "uk"):
        check("language available: " + language,
              lv.SpdEngine(dict(lv.DEFAULTS), language).supports(language))
    print("languages:", len(languages), flush=True)

    french = "Bonjour tout le monde, ceci est un essai de la voix."
    names = {lv.primary_language(voice[1]): voice[0] for voice in voices}
    default = speak("default", french, "fr-FR")
    chosen = speak("chosen", french, "fr-FR", voice=names["de"])
    check("speech is heard", len(default) > 8000 and len(chosen) > 8000,
          "%d %d samples" % (len(default), len(chosen)))
    check("a chosen voice changes the sound", default != chosen,
          "%d / %d samples" % (len(default), len(chosen)))

    low = speak("low", french, "fr-FR", pitch=-8)
    high = speak("high", french, "fr-FR", pitch=8)
    low_hz, high_hz = pitch_hz(low), pitch_hz(high)
    check("pitch setting changes the voice", high_hz > low_hz * 1.15,
          "%.0f Hz / %.0f Hz" % (low_hz, high_hz))

    slow = speak("slow", french, "fr-FR", speed=60)
    fast = speak("fast", french, "fr-FR", speed=180)
    check("speed setting changes the voice", len(slow) > len(fast) * 1.3,
          "%d / %d samples" % (len(slow), len(fast)))

    same_text = "Bonjour, ceci est une phrase de test."
    as_french = speak("as_fr", same_text, "fr-FR")
    as_english = speak("as_en", same_text, "en-US")
    check("language option changes the pronunciation", as_french != as_english,
          "%d / %d samples" % (len(as_french), len(as_english)))

    arabic = speak("arabic", "مرحبا بالعالم، هذا اختبار للصوت", "ar")
    check("non-Latin text is spoken", len(arabic) > 8000, "%d samples" % len(arabic))

    for name, text, language in (("chinese", "你好世界，这是一个声音测试", "zh-CN"),
                                 ("japanese", "こんにちは世界、これはテストです", "ja"),
                                 ("russian", "Привет, мир, это проверка голоса", "ru")):
        spoken = speak(name, text, language)
        check("spoken: " + language, len(spoken) > 8000, "%d samples" % len(spoken))

    print("FAILED: %s" % failures if failures else "ALL PASSED", flush=True)
    sys.exit(1 if failures else 0)


main()
