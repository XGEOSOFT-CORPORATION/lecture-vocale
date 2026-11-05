"""End-to-end check inside a real LibreOffice, not part of the unit tests.

Run it with LibreOffice's own Python, which provides the uno module, after
starting soffice with:

    --norestore --accept=socket,host=localhost,port=2002;urp;

and the environment variables LECTURE_VOCALE_HEADLESS=1 and
LECTURE_VOCALE_EXPORT_PATH=<file.wav>. The first argument is the extension's
log file.
"""

import json
import os
import re
import sys
import threading
import time
import wave

import uno
import unohelper
from com.sun.star.awt import XCallback
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
    for _ in range(180):
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


class MainThreadCall(unohelper.Base, XCallback):
    def __init__(self, function):
        self.function = function
        self.finished = threading.Event()

    def notify(self, data):
        self.function()
        self.finished.set()


def dispatch_like_a_menu(ctx, frame, command):
    """Run a command on LibreOffice's main thread, where menu clicks land."""
    call = MainThreadCall(lambda: dispatch(ctx, frame, command))
    ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.AsyncCallback", ctx).addCallback(call, None)
    return call.finished.wait(30)


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
    check("Writer: export covers the whole document after a stop",
          re.search(r"\(3 \w+, ", log_text()[offset:]) is not None, log_text()[offset:][-200:])
    with wave.open(WAV) as audio:
        seconds = audio.getnframes() / float(audio.getframerate())
    check("Writer: exported audio has a realistic length", 2 < seconds < 30, "%.1f s" % seconds)

    frame = doc.getCurrentController().getFrame()
    check("Settings dialog returns", dispatch_like_a_menu(ctx, frame, "selftest"))
    check("Settings dialog builds", "selftest dialogue ok" in log_text())
    doc.close(True)


def french_document(desktop):
    doc = open_document(desktop, "swriter")
    text = doc.getText()
    cursor = text.createTextCursor()
    cursor.setPropertyValue("CharLocale", FRENCH)
    text.insertString(cursor, "Un. Deux. Trois.", False)
    return doc


def controls(ctx, desktop):
    """The whole reading flow, on a real document."""
    doc = french_document(desktop)
    frame = doc.getCurrentController().getFrame()

    offset = len(log_text())
    dispatch(ctx, frame, "readall")
    check("Flow: reads to the end", wait_for_log("index=3/3 ok=True", offset, 60),
          log_text()[offset:][-300:])
    time.sleep(0.5)
    selected = doc.getCurrentController().getViewCursor().getString()
    check("Flow: nothing stays selected at the end", selected == "", repr(selected))

    offset = len(log_text())
    dispatch(ctx, frame, "readall")
    wait_for_log("langues:", offset)
    dispatch(ctx, frame, "toggle")
    check("Flow: pause", wait_for_log("pause à l'index", offset, 10))
    time.sleep(0.5)
    dispatch(ctx, frame, "toggle")
    check("Flow: resume reads to the end", wait_for_log("ok=True", offset, 60),
          log_text()[offset:][-300:])

    offset = len(log_text())
    dispatch(ctx, frame, "readall")
    wait_for_log("langues:", offset)
    time.sleep(0.3)
    dispatch(ctx, frame, "next")
    dispatch(ctx, frame, "prev")
    dispatch(ctx, frame, "faster")
    dispatch(ctx, frame, "slower")
    dispatch(ctx, frame, "pitchup")
    dispatch(ctx, frame, "pitchdown")
    check("Flow: live commands keep reading", wait_for_log("ok=True", offset, 60),
          log_text()[offset:][-300:])
    check("Flow: every live setting is saved", "save_config" not in log_text()[offset:],
          log_text()[offset:][-300:])
    with open(os.path.join(os.path.dirname(LOG), "config.json"), encoding="utf-8") as source:
        saved = json.load(source)
    check("Flow: settings end where they started", (saved["speed"], saved["pitch"]) == (100, 0),
          (saved["speed"], saved["pitch"]))

    offset = len(log_text())
    for command in ("panel", "about"):
        check("Flow: %s returns" % command, dispatch_like_a_menu(ctx, frame, command))
    check("Flow: about window builds", "à propos prêt" in log_text()[offset:])
    time.sleep(1)
    offset = len(log_text())
    dispatch(ctx, frame, "readall")
    check("Flow: reading with the panel open", wait_for_log("ok=True", offset, 60),
          log_text()[offset:][-300:])
    check("Flow: no internal error", "Traceback" not in log_text(),
          log_text()[-600:] if "Traceback" in log_text() else "")
    doc.close(True)


def live_pitch(ctx, desktop):
    """Changing the pitch rereads the current sentence: no sentence may be skipped."""
    doc = open_document(desktop, "swriter")
    text = doc.getText()
    cursor = text.createTextCursor()
    cursor.setPropertyValue("CharLocale", FRENCH)
    sentence = "Cette phrase est assez longue pour durer plusieurs secondes quand elle est lue. "
    text.insertString(cursor, sentence * 40, False)
    frame = doc.getCurrentController().getFrame()
    offset = len(log_text())
    dispatch(ctx, frame, "readall")
    wait_for_log("langues:", offset)
    time.sleep(1.5)
    for command in ("pitchup", "pitchup", "pitchdown", "pitchdown"):
        dispatch(ctx, frame, command)
        time.sleep(0.7)
    time.sleep(2)
    dispatch(ctx, frame, "stop")
    wait_for_log("lecture terminée", offset)
    match = re.search(r"lecture terminée: index=(\d+)/40", log_text()[offset:])
    check("Pitch change skips no sentence", match is not None and int(match.group(1)) <= 2,
          match.group(0) if match else log_text()[offset:][-200:])
    doc.close(True)


def every_voice(ctx, desktop):
    """Each voice of the list reads a real document, to the end, and gets exported."""
    offset = len(log_text())
    doc = open_document(desktop, "swriter")
    frame = doc.getCurrentController().getFrame()
    check("Voice list and panel work", dispatch_like_a_menu(ctx, frame, "selftest"))
    match = re.search(r"voix disponibles: (\[.*\])", log_text()[offset:])
    voices = json.loads(match.group(1)) if match else []
    check("Voices are listed", len(voices) > 0, len(voices))
    panel = re.search(r"voix du panneau: (.*)", log_text()[offset:])
    check("Panel list saves the voice", panel is not None and panel.group(1).strip() == voices[0][0],
          panel.group(1) if panel else "")
    doc.close(True)

    every = int(os.environ.get("LV_VOICE_STEP", "1"))
    config = os.path.join(os.path.dirname(LOG), "config.json")
    with open(config, encoding="utf-8") as source:
        saved = json.load(source)
    heard = set()
    tested = voices[::every]
    for name, language in tested:
        with open(config, "w", encoding="utf-8") as target:
            json.dump(dict(saved, voice=name), target)
        doc = french_document(desktop)
        frame = doc.getCurrentController().getFrame()
        offset = len(log_text())
        dispatch(ctx, frame, "readall")
        finished = wait_for_log("index=3/3 ok=True", offset, 40)
        line = next((item for item in log_text()[offset:].splitlines() if "langues:" in item), "")
        # A Piper voice is listed by its path, and logged by its file name.
        shown = os.path.splitext(os.path.basename(name))[0]
        check("Voice reads a document: " + name, finished and shown in line, line[-90:])
        check("Voice shows no error: " + name, "errorbox" not in log_text()[offset:])
        if os.environ.get("LV_EXPECT_ENGINE") == "sapi":
            offset = len(log_text())
            dispatch(ctx, frame, "export")
            check("Voice exports audio: " + name, wait_for_log("export ok", offset, 40))
            with open(WAV, "rb") as source:
                heard.add(source.read())
        doc.close(True)
    if os.environ.get("LV_EXPECT_ENGINE") == "sapi":
        check("Every voice has its own sound", len(heard) == len(tested),
              "%d / %d" % (len(heard), len(tested)))
    with open(config, "w", encoding="utf-8") as target:
        json.dump(saved, target)


def close_while_reading(ctx, desktop):
    """Closing a document during reading, with the panel open, must leave LibreOffice alive."""
    for attempt in range(3):
        doc = french_document(desktop)
        frame = doc.getCurrentController().getFrame()
        dispatch_like_a_menu(ctx, frame, "panel")
        offset = len(log_text())
        dispatch(ctx, frame, "readall")
        wait_for_log("langues:", offset)
        time.sleep(0.4)
        doc.close(True)
        time.sleep(2)
    doc = french_document(desktop)
    started, log = read_and_stop(ctx, doc)
    check("Closing a document during reading: LibreOffice keeps working", started, log[-300:])
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


def interface(ctx, desktop):
    """The option changes the language of messages and of the menus."""
    config = os.path.join(os.path.dirname(LOG), "config.json")
    with open(config, encoding="utf-8") as source:
        saved = json.load(source)
    doc = open_document(desktop, "swriter")
    frame = doc.getCurrentController().getFrame()
    provider = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.configuration.ConfigurationProvider", ctx)
    node = PropertyValue()
    node.Name = "nodepath"
    node.Value = ("/org.openoffice.Office.Addons/AddonUI/OfficeMenuBar/"
                  "com.adanalytics.lecturevocale.menu")
    expected = (("en", "Nothing to read", "Read ~Aloud"), ("es", "Nada que leer", "Lectura en ~voz alta"),
                ("de", "Nichts zu lesen", "~Vorlesen"), ("it", "Niente da leggere", "Lettura ~vocale"),
                ("pt", "Nada para ler", "Leitura em ~voz alta"), ("fr", "Rien à lire", "Lecture ~vocale"))
    for code, message, title in expected:
        with open(config, "w", encoding="utf-8") as target:
            json.dump(dict(saved, ui_language=code), target)
        offset = len(log_text())
        dispatch(ctx, frame, "readall")
        check("Interface %s: message" % code, wait_for_log(message, offset, 10),
              log_text()[offset:][-200:])
        dispatch(ctx, frame, "menulanguage")
        menu = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (node,))
        check("Interface %s: menu" % code, menu.getByName("Title") == title,
              menu.getByName("Title"))
    with open(config, "w", encoding="utf-8") as target:
        json.dump(dict(saved, ui_language="auto"), target)
    automatic = os.environ.get("LV_EXPECT_AUTO_MESSAGE")
    if automatic:
        offset = len(log_text())
        dispatch(ctx, frame, "readall")
        check("Interface follows LibreOffice by default", wait_for_log(automatic, offset, 10),
              log_text()[offset:][-200:])
    offset = len(log_text())
    dispatch_like_a_menu(ctx, frame, "selftest")
    check("Settings dialog builds with the new option", "selftest dialogue ok" in log_text()[offset:])
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
    shortcuts = [key + "_SHIFT_MOD1_MOD2"
                 for key in ("L", "S", "O", "N", "B", "PAGEUP", "PAGEDOWN")]
    for shortcut in shortcuts:
        check("Shortcut registered: " + shortcut, keys.hasByName(shortcut))
    # A module's own shortcut hides a global one.
    for module in ("com.sun.star.text.TextDocument", "com.sun.star.sheet.SpreadsheetDocument",
                   "com.sun.star.presentation.PresentationDocument",
                   "com.sun.star.drawing.DrawingDocument"):
        node.Value = "/org.openoffice.Office.Accelerators/PrimaryKeys/Modules/" + module
        taken = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (node,))
        conflicts = [shortcut for shortcut in shortcuts if taken.hasByName(shortcut)]
        check("No shortcut conflict in " + module.split(".")[-1], not conflicts, conflicts)
    node.Value = "/org.openoffice.Office.Addons/AddonUI/OfficeMenuBar"
    menus = provider.createInstanceWithArguments(
        "com.sun.star.configuration.ConfigurationAccess", (node,))
    check("Menu registered", any("lecturevocale" in name for name in menus.getElementNames()),
          menus.getElementNames())
    toolbar = "private:resource/toolbar/addon_com.adanalytics.lecturevocale.toolbar"
    for application in ("Writer", "Calc", "Impress", "Draw"):
        node.Value = "/org.openoffice.Office.UI.%sWindowState/UIElements/States" % application
        states = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (node,))
        name = states.getByName(toolbar).UIName if states.hasByName(toolbar) else ""
        check("Toolbar named in " + application, name == "Lecture vocale", name)


def give_up():
    print("FAIL watchdog: LibreOffice stopped responding", flush=True)
    print("log end:", log_text()[-400:], flush=True)
    os._exit(2)


def guide(ctx):
    """The PDF must be where the About window looks for it, and open."""
    packages = ctx.getValueByName("/singletons/com.sun.star.deployment.PackageInformationProvider")
    location = packages.getPackageLocation("com.adanalytics.lecturevocale")
    url = location + "/docs/Contributor-Guide.pdf"
    path = uno.fileUrlToSystemPath(url)
    check("Guide installed with the extension",
          os.path.isfile(path) and os.path.getsize(path) > 10000, path)
    if os.environ.get("LV_OPEN_GUIDE"):
        shell = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.system.SystemShellExecute", ctx)
        shell.execute(url, "", 0)
        check("Guide opens", True)


def main():
    watchdog = threading.Timer(600, give_up)
    watchdog.daemon = True
    watchdog.start()
    ctx = connect()
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    print("python", sys.version.split()[0], flush=True)
    configuration(ctx)
    guide(ctx)
    for scenario in (writer, controls, live_pitch, every_voice, close_while_reading, scripts,
                     interface, calc, impress):
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
