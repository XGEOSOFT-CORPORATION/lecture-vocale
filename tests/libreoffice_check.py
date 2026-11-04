"""End-to-end check inside a real LibreOffice, not part of the unit tests.

Run it with LibreOffice's own Python, which provides the uno module, after
starting soffice with:

    --headless --norestore --accept=socket,host=localhost,port=2002;urp;

and the environment variables LECTURE_VOCALE_HEADLESS=1 and
LECTURE_VOCALE_EXPORT_PATH=<file.wav>. The first argument is the extension's
log file.
"""

import os
import sys
import time
import wave

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.lang import Locale

LOG = sys.argv[1]
WAV = os.environ["LECTURE_VOCALE_EXPORT_PATH"]
SERVICE = "service:com.adanalytics.LectureVocale?"
FRENCH = Locale("fr", "FR", "")
ENGLISH = Locale("en", "US", "")
failures = []


def check(label, passed, detail=""):
    print("%s %s %s" % ("PASS" if passed else "FAIL", label, detail), flush=True)
    if not passed:
        failures.append(label)


def connect():
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(60):
        try:
            return resolver.resolve(
                "uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext")
        except Exception:
            time.sleep(1)
    raise SystemExit("cannot connect to LibreOffice")


def dispatch(ctx, frame, command):
    helper = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.DispatchHelper", ctx)
    helper.executeDispatch(frame, SERVICE + command, "", 0, ())


def log_text():
    if not os.path.exists(LOG):
        return ""
    with open(LOG, encoding="utf-8") as source:
        return source.read()


def wait_for_log(text, offset, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if text in log_text()[offset:]:
            return True
        time.sleep(0.2)
    return False


def read_and_stop(ctx, doc, command="readall"):
    """Run a reading command; return what the log says about that reading."""
    offset = len(log_text())
    dispatch(ctx, doc.getCurrentController().getFrame(), command)
    started = wait_for_log("langues:", offset)
    time.sleep(1)
    dispatch(ctx, doc.getCurrentController().getFrame(), "stop")
    wait_for_log("lecture terminée", offset)
    return started, log_text()[offset:]


def open_document(desktop, kind):
    hidden = PropertyValue()
    hidden.Name, hidden.Value = "Hidden", False
    return desktop.loadComponentFromURL("private:factory/" + kind, "_blank", 0, (hidden,))


def writer(ctx, desktop):
    doc = open_document(desktop, "swriter")
    text = doc.getText()
    cursor = text.createTextCursor()
    cursor.setPropertyValue("CharLocale", FRENCH)
    text.insertString(cursor, "Bonjour tout le monde. Ceci est une phrase en français.", False)
    text.insertControlCharacter(cursor, 0, False)
    cursor.setPropertyValue("CharLocale", ENGLISH)
    text.insertString(cursor, "This sentence is written in English.", False)

    started, log = read_and_stop(ctx, doc)
    check("Writer: reading starts", started, log[-300:] if not started else "")
    check("Writer: 3 sentences", "segments=3" in log)
    expected = os.environ.get("LV_EXPECT_ENGINE")
    if expected:
        check("Writer: engine is " + expected, "moteur=%s " % expected in log, log[:200])
    check("Writer: both languages detected", "en-US" in log and "fr-FR" in log,
          [line for line in log.splitlines() if "langues:" in line])

    offset = len(log_text())
    dispatch(ctx, doc.getCurrentController().getFrame(), "export")
    check("Writer: audio export", wait_for_log("export ok", offset, 60), log_text()[offset:][-300:])
    with wave.open(WAV) as audio:
        seconds = audio.getnframes() / float(audio.getframerate())
    check("Writer: exported audio has a realistic length", 2 < seconds < 30, "%.1f s" % seconds)

    frame = doc.getCurrentController().getFrame()
    dispatch(ctx, frame, "selftest")
    check("Settings dialog builds", "selftest dialogue ok" in log_text())
    doc.close(True)


def scripts(ctx, desktop):
    """Non-Latin text left in the document's default language."""
    doc = open_document(desktop, "swriter")
    text = doc.getText()
    cursor = text.createTextCursor()
    cursor.setPropertyValue("CharLocale", FRENCH)
    for sentence in ("Привет, мир.", "مرحبا بالعالم.", "こんにちは世界。", "안녕하세요."):
        text.insertString(cursor, sentence, False)
        text.insertControlCharacter(cursor, 0, False)
    started, log = read_and_stop(ctx, doc)
    check("Scripts: reading starts", started, log[-300:] if not started else "")
    line = next((item for item in log.splitlines() if "langues:" in item), "")
    check("Scripts: languages found", all("'%s'" % code in line
                                          for code in ("ru", "ar", "ja", "ko")), line)
    doc.close(True)


def calc(ctx, desktop):
    doc = open_document(desktop, "scalc")
    sheet = doc.getSheets().getByIndex(0)
    sheet.getCellByPosition(0, 0).setString("Nom")
    sheet.getCellByPosition(1, 0).setString("Ville")
    sheet.getCellByPosition(0, 1).setString("Alice")
    started, log = read_and_stop(ctx, doc)
    check("Calc: reading starts", started, log[-300:] if not started else "")
    check("Calc: 2 rows", "segments=2" in log)
    doc.close(True)


def impress(ctx, desktop):
    doc = open_document(desktop, "simpress")
    page = doc.getDrawPages().getByIndex(0)
    shape = doc.createInstance("com.sun.star.drawing.TextShape")
    page.add(shape)
    shape.setString("Une diapositive. Avec deux phrases.")
    started, log = read_and_stop(ctx, doc)
    check("Impress: reading starts", started, log[-300:] if not started else "")
    check("Impress: 2 sentences", "segments=2" in log)
    doc.close(True)


def configuration(ctx):
    provider = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.configuration.ConfigurationProvider", ctx)
    node = PropertyValue()
    node.Name, node.Value = "nodepath", "/org.openoffice.Office.Accelerators/PrimaryKeys/Global"
    keys = provider.createInstanceWithArguments(
        "com.sun.star.configuration.ConfigurationAccess", (node,))
    check("Shortcuts registered", keys.hasByName("L_SHIFT_MOD1_MOD2"))
    node.Value = "/org.openoffice.Office.Addons/AddonUI/OfficeMenuBar"
    menus = provider.createInstanceWithArguments(
        "com.sun.star.configuration.ConfigurationAccess", (node,))
    check("Menu registered", any("lecturevocale" in name for name in menus.getElementNames()),
          menus.getElementNames())


def main():
    ctx = connect()
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    print("python", sys.version.split()[0], flush=True)
    configuration(ctx)
    for scenario in (writer, scripts, calc, impress):
        try:
            scenario(ctx, desktop)
        except Exception as error:
            check(scenario.__name__, False, repr(error))
    try:
        desktop.terminate()
    except Exception:
        pass
    print("FAILED: %s" % failures if failures else "ALL PASSED", flush=True)
    sys.exit(1 if failures else 0)


main()
