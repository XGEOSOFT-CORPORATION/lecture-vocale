"""Voice and language choice, without LibreOffice (the UNO modules are stubbed)."""

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = tempfile.mkdtemp(prefix="lvlo_tests_")


def stub_uno():
    def interface(name):
        return type(name, (), {})

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


def load_extension():
    stub_uno()
    os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = CONFIG_DIR
    os.environ["LECTURE_VOCALE_HEADLESS"] = "1"
    path = ROOT / "extension" / "python" / "lecture_vocale.py"
    spec = importlib.util.spec_from_file_location("lecture_vocale", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lv = load_extension()

WINDOWS_VOICES = [("David", "en-US", "sapi"), ("Zira", "en-US", "sapi"),
                  ("Hortense", "fr-FR", "sapi")]


class PickVoiceTests(unittest.TestCase):
    def test_language_decides(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "fr-FR"), "Hortense")
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "en-GB"), "David")

    def test_never_a_voice_of_another_language(self):
        self.assertIsNone(lv.pick_voice(WINDOWS_VOICES, "de-DE"))

    def test_case_and_separator_do_not_matter(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "FR"), "Hortense")
        self.assertEqual(lv.pick_voice([("A", "fr_CA")], "fr"), "A")

    def test_exact_region_wins(self):
        voices = [("F", "fr-FR"), ("C", "fr-CA")]
        self.assertEqual(lv.pick_voice(voices, "fr-CA"), "C")

    def test_two_codes_for_one_language(self):
        self.assertEqual(lv.pick_voice([("Li", "cmn")], "zh-CN"), "Li")
        self.assertEqual(lv.pick_voice([("Wei", "zh-CN")], "cmn"), "Wei")
        self.assertEqual(lv.pick_voice([("Ola", "no")], "nb-NO"), "Ola")
        self.assertIsNone(lv.pick_voice([("Li", "cmn")], "ja"))


class PiperTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        lv._MODEL_INFO.clear()

    def model(self, name, code, speakers=None):
        path = self.dir / (name + ".onnx")
        path.write_bytes(b"x")
        info = {"language": {"code": code}}
        if speakers:
            info["speaker_id_map"] = speakers
        (self.dir / (name + ".onnx.json")).write_text(json.dumps(info), encoding="utf-8")
        return str(path)

    def engine(self, models, language="fr-FR", **cfg):
        settings = dict(lv.DEFAULTS, **cfg)
        with mock.patch.object(lv, "find_piper", return_value="piper"), \
                mock.patch.object(lv, "find_models", return_value=models), \
                mock.patch.object(lv, "piper_length_flag", return_value=""), \
                mock.patch.object(lv, "_which", return_value=""):
            return lv.PiperEngine(settings, language)

    def test_english_model_is_not_used_for_french(self):
        ryan = self.model("en_US-ryan-medium", "en_US")
        tom = self.model("fr_FR-tom-medium", "fr_FR")
        self.assertEqual(self.engine([ryan, tom]).model, tom)

    def test_chosen_model_reads_everything(self):
        ryan = self.model("en_US-ryan-medium", "en_US")
        tom = self.model("fr_FR-tom-medium", "fr_FR")
        engine = self.engine([ryan, tom], voice=ryan)
        self.assertEqual(engine.model, ryan)
        self.assertEqual(engine._pick("fr-FR"), (ryan, None))
        self.assertTrue(engine.supports("de"))

    def test_reports_a_language_without_model(self):
        ryan = self.model("en_US-ryan-medium", "en_US")
        engine = self.engine([ryan])
        self.assertFalse(engine.supports("fr-FR"))
        self.assertTrue(engine.supports("en-GB"))

    def test_each_speaker_is_a_voice(self):
        upmc = self.model("fr_FR-upmc-medium", "fr_FR", {"jessica": 0, "pierre": 1})
        engine = self.engine([upmc], voice=upmc + "#1")
        self.assertEqual([voice[0] for voice in engine.voices()], [upmc + "#0", upmc + "#1"])
        self.assertEqual(engine.speaker, 1)
        self.assertEqual(engine.voice_text(engine.voices()[1]), "fr_FR-upmc-medium #1")

    def test_language_from_file_name_without_json(self):
        path = self.dir / "de_DE-thorsten-high.onnx"
        path.write_bytes(b"x")
        self.assertEqual(lv.model_language(str(path)), "de")


class EngineTests(unittest.TestCase):
    def setUp(self):
        which = mock.patch.object(lv, "_which", side_effect=lambda *names: names[0])
        which.start()
        self.addCleanup(which.stop)
        lv._ESPEAK_VOICES[:] = [("fr-fr", "fr-fr", "French (France)"), ("en-gb", "en-gb", "English"),
                                ("mb-fr4", "fr", "mb-fr4")]
        self.addCleanup(lv._ESPEAK_VOICES.clear)

    def test_espeak_follows_the_text(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS))
        self.assertEqual(engine.voice("en-GB"), "en")
        self.assertEqual(engine.voice("fr-FR"), "fr")

    def test_espeak_chosen_voice_reads_everything(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS, voice="mb-fr4"))
        self.assertEqual(engine.voice("en-US"), "mb-fr4")

    def test_espeak_voice_list(self):
        output = ("Pty Language       Age/Gender VoiceName          File      Other Languages\n"
                  " 5  af              --/M      Afrikaans          gmw/af\n"
                  " 2  fr-fr           --/M      French_(France)    roa/fr\n"
                  " 5  fr              --/F      mb-fr4             mb/mb-fr4\n")
        self.assertEqual(lv.parse_espeak_voices(output),
                         [("af", "af", "Afrikaans"), ("fr-fr", "fr-fr", "French (France)"),
                          ("mb-fr4", "fr", "mb-fr4")])

    def test_forced_language_beats_the_text(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS, language="de"))
        self.assertEqual(engine.voice("fr-FR"), "de")

    def test_spd_gets_a_normalized_language(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS, language="FR"))
        engine.voices = lambda: []
        command, _ = engine.speak_command("Bonjour")
        self.assertEqual(command[command.index("-l") + 1], "fr")
        self.assertNotIn("-y", command)

    def test_spd_voice_list_skips_variants(self):
        output = ("   NAME   LANGUAGE  VARIANT\n   Mary Ann  en-US  none\n"
                  "   Marie  fr  none\n   Marie+Adam  fr  Adam\n")
        self.assertEqual(lv.parse_spd_voices(output), [("Mary Ann", "en-US"), ("Marie", "fr")])

    def test_spd_keeps_the_region_it_knows(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS))
        engine.voices = lambda: [("a", "pt-BR"), ("b", "fr")]
        self.assertEqual(engine._language_option("pt-BR"), "pt-BR")
        self.assertEqual(engine._language_option("fr-CA"), "fr")

    def test_spd_uses_the_code_its_voices_know(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS))
        engine.voices = lambda: [("Mandarin", "cmn"), ("Norwegian", "nb")]
        self.assertTrue(engine.supports("zh-CN"))
        self.assertEqual(engine._language_option("zh-CN"), "cmn")
        self.assertEqual(engine._language_option("no"), "nb")

    def test_spd_chosen_voice_reads_everything(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS, voice="Mary"))
        engine.voices = lambda: [("Marie", "fr"), ("Mary", "en")]
        command, _ = engine.speak_command("Bonjour", "fr-FR")
        self.assertEqual(command[command.index("-y") + 1], "Mary")
        self.assertEqual(command[command.index("-l") + 1], "en")
        self.assertEqual(engine.take_warning(), "")

    def test_spd_pitch(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS, pitch=-4))
        engine.voices = lambda: []
        command, _ = engine.speak_command("Bonjour", "fr-FR")
        self.assertEqual(command[command.index("-p") + 1], "-40")

    def test_spd_reports_a_module_without_the_language(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS))
        engine.voices = lambda: [("Mary", "en")]
        engine.speak_command("Bonjour", "fr-FR")
        self.assertIn("fr-FR", engine.take_warning())
        engine.speak_command("Bonjour", "fr-FR")
        self.assertEqual(engine.take_warning(), "")

    def test_mac_voice_follows_language_or_choice(self):
        voices = [("Thomas", "fr_FR"), ("Samantha", "en_US")]
        with mock.patch.object(lv, "IS_MAC", True), \
                mock.patch.object(lv, "mac_voices", return_value=voices):
            engine = lv.MacSayEngine(dict(lv.DEFAULTS), "fr-FR")
            self.assertEqual(engine._pick("fr-FR"), "Thomas")
            self.assertEqual(engine._pick("en-US"), "Samantha")
            self.assertIsNone(engine._pick("de-DE"))
            self.assertIn("de-DE", engine.take_warning())
            chosen = lv.MacSayEngine(dict(lv.DEFAULTS, voice="Samantha"), "fr-FR")
            self.assertEqual(chosen._pick("fr-FR"), "Samantha")

    def test_sapi_voice_list_merges_both_apis(self):
        output = ("\ufeffMicrosoft Paul|fr-FR|winrt\r\n"
                  "Microsoft Hortense|fr-FR|winrt\r\n"
                  "Microsoft Hortense Desktop|fr-FR|sapi\r\n"
                  "Vendor Voice|de-DE|sapi\r\nbad line\r\n")
        self.assertEqual(lv.parse_sapi_voices(output),
                         [("Microsoft Paul", "fr-FR", "winrt"),
                          ("Microsoft Hortense", "fr-FR", "winrt"),
                          ("Vendor Voice", "de-DE", "sapi")])

    def sapi(self, voices, language="fr-FR", **cfg):
        patches = (mock.patch.object(lv, "IS_WIN", True),
                   mock.patch.object(lv, "sapi_voices", return_value=voices))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return lv.SapiEngine(dict(lv.DEFAULTS, **cfg), language)

    def test_modern_windows_voice_gets_speed_and_pitch(self):
        engine = self.sapi([("Microsoft Paul", "fr-FR", "winrt")], speed=150, pitch=4)
        script = engine._script("t.txt", None, "fr-FR")
        self.assertIn("-eq 'Microsoft Paul'", script)
        self.assertIn("SpeakingRate = 1.50", script)
        self.assertIn("AudioPitch = 1.20", script)

    def test_windows_scripts_cannot_wait_forever(self):
        engine = self.sapi([("Microsoft Paul", "fr-FR", "winrt")])
        script = engine._script("t.txt", None, "fr-FR")
        self.assertIn("Wait(60000)", script)
        self.assertNotIn("Wait(-1)", script)
        self.assertTrue(script.endswith("[Environment]::Exit(0)"))

    def test_sapi_script_never_hardcodes_the_ssml_language(self):
        engine = self.sapi(WINDOWS_VOICES, "en-US")
        script = engine._script("t.txt", None, "en-US")
        self.assertIn("$s.SelectVoice('David')", script)
        self.assertIn("$s.Voice.Culture.Name", script)
        self.assertNotIn("catch", script)

    def test_windows_chosen_voice_reads_everything(self):
        engine = self.sapi(WINDOWS_VOICES, voice="Zira")
        self.assertEqual(engine._voice("fr-FR"), "Zira")
        self.assertEqual(engine._voice("de-DE"), "Zira")
        self.assertEqual(engine.take_warning(), "")

    def test_unknown_chosen_voice_falls_back_to_automatic(self):
        engine = self.sapi(WINDOWS_VOICES, voice="Removed Voice")
        self.assertEqual(engine._voice("fr-FR"), "Hortense")

    def test_test_button_plays_the_chosen_voice_in_its_language(self):
        self.sapi(WINDOWS_VOICES)
        cfg = dict(lv.DEFAULTS, voice="Zira", engine="sapi")
        with mock.patch.object(lv, "ENGINE_KEYS", ["auto", "sapi"]):
            engine, language, sample = lv.voice_sample(cfg)
        self.assertEqual(language, "en-US")
        self.assertTrue(sample.startswith("Hello"))

    def test_voice_list_is_sorted_and_keeps_automatic_out(self):
        self.sapi(WINDOWS_VOICES)
        cfg = dict(lv.DEFAULTS, engine="sapi", voice="Zira")
        with mock.patch.object(lv, "ENGINE_KEYS", ["auto", "sapi"]):
            engine, voices = lv.voice_choices(cfg)
        self.assertEqual([voice[0] for voice in voices], ["David", "Hortense", "Zira"])


class ResolveTests(unittest.TestCase):
    def fake(self, key, supported):
        class Fake(lv.Engine):
            can_export = True

            def supports(self, language=None):
                return supported

        Fake.key = key
        return Fake

    def resolve(self, engines, keys, language="fr-FR"):
        with mock.patch.dict(lv.ENGINES, engines), \
                mock.patch.object(lv, "ENGINE_KEYS", ["auto"] + keys):
            return lv.resolve_engine(dict(lv.DEFAULTS), language=language)

    def test_auto_skips_an_engine_without_the_language(self):
        engines = {"piper": self.fake("piper", False), "espeak": self.fake("espeak", True)}
        self.assertEqual(self.resolve(engines, ["piper", "espeak"]).key, "espeak")

    def test_auto_needs_every_language_of_the_document(self):
        class FrenchOnly(self.fake("piper", True)):
            def supports(self, language=None):
                return language == "fr-FR"

        engines = {"piper": FrenchOnly, "espeak": self.fake("espeak", True)}
        with mock.patch.dict(lv.ENGINES, engines), \
                mock.patch.object(lv, "ENGINE_KEYS", ["auto", "piper", "espeak"]):
            chosen = lv.resolve_engine(dict(lv.DEFAULTS), languages=["fr-FR", "ar"])
            self.assertEqual(chosen.key, "espeak")
            chosen = lv.resolve_engine(dict(lv.DEFAULTS), languages=["fr-FR"])
            self.assertEqual(chosen.key, "piper")

    def test_auto_keeps_the_first_engine_when_none_has_the_language(self):
        engines = {"piper": self.fake("piper", False), "espeak": self.fake("espeak", False)}
        self.assertEqual(self.resolve(engines, ["piper", "espeak"]).key, "piper")


class EngineOrderTests(unittest.TestCase):
    def test_system_speech_comes_first(self):
        self.assertEqual(lv.ENGINE_KEYS[0], "auto")
        expected = {"win32": "sapi", "darwin": "say"}.get(sys.platform, "spd")
        self.assertEqual(lv.ENGINE_KEYS[1], expected)

    def test_every_engine_has_a_label(self):
        self.assertEqual(len(lv.ENGINE_KEYS), len(lv.ENGINE_LABELS))


class SpdCacheTests(unittest.TestCase):
    def test_voices_are_listed_once_per_module(self):
        lv._SPD_VOICES.clear()
        output = types.SimpleNamespace(stdout=b"NAME LANGUAGE VARIANT\nMarie fr none\n")
        with mock.patch.object(lv.subprocess, "run", return_value=output) as run:
            self.assertEqual(lv.spd_voices("spd-say"), [("Marie", "fr")])
            lv.spd_voices("spd-say")
            self.assertEqual(run.call_count, 1)
            lv.spd_voices("spd-say", "espeak-ng")
            self.assertEqual(run.call_args[0][0], ["spd-say", "-L", "-o", "espeak-ng"])

    def test_a_dead_daemon_means_no_list(self):
        lv._SPD_VOICES.clear()
        with mock.patch.object(lv.subprocess, "run", side_effect=OSError("gone")):
            self.assertEqual(lv.spd_voices("spd-say"), [])


class ConfigTests(unittest.TestCase):
    def write(self, data):
        with open(lv.CONFIG_FILE, "w", encoding="utf-8") as target:
            json.dump(data, target)
        self.addCleanup(os.remove, lv.CONFIG_FILE)

    def test_defaults_saved_by_old_versions_become_auto(self):
        self.write({"language": "fr", "espeak_voice": "fr"})
        cfg = lv.load_config()
        self.assertEqual((cfg["language"], cfg["voice"]), ("auto", ""))

    def test_old_explicit_language_is_kept(self):
        self.write({"language": "en"})
        self.assertEqual(lv.load_config()["language"], "en")

    def test_current_choice_of_french_is_kept(self):
        self.write({"language": "fr", "config_version": lv.CONFIG_VERSION})
        self.assertEqual(lv.load_config()["language"], "fr")

    def test_old_voice_fields_move_to_the_voice_list(self):
        self.write({"sapi_voice": "Microsoft Hortense Desktop", "config_version": 2})
        self.assertEqual(lv.load_config()["voice"], "Microsoft Hortense")

    def test_old_mbrola_and_piper_settings_are_kept(self):
        self.write({"espeak_voice": "mb-fr4", "piper_model_male": "tom.onnx", "config_version": 2})
        cfg = lv.load_config()
        self.assertEqual((cfg["voice"], cfg["piper_model"]), ("mb-fr4", "tom.onnx"))

    def test_gender_is_gone_from_the_settings(self):
        self.write({"gender": "male", "config_version": 2})
        self.assertNotIn("gender", lv.load_config())


class AboutTests(unittest.TestCase):
    def context(self, shell):
        smgr = mock.MagicMock()
        smgr.createInstanceWithContext.return_value = shell
        return types.SimpleNamespace(ServiceManager=smgr)

    def test_links_point_to_the_project(self):
        self.assertEqual(lv.GITHUB_URL,
                         "https://github.com/XGEOSOFT-CORPORATION/lecture-vocale")
        self.assertEqual(lv.ISSUES_URL, lv.GITHUB_URL + "/issues")

    def test_buttons_open_the_right_page(self):
        shell = mock.MagicMock()
        dialog = lv.AboutDialog.__new__(lv.AboutDialog)
        dialog.ctx = self.context(shell)
        with mock.patch.object(lv.uno, "systemPathToFileUrl", create=True,
                               side_effect=lambda path: "file:" + path):
            for command in ("issue", "star", "guide"):
                dialog.on_action(command)
        urls = [call.args[0] for call in shell.execute.call_args_list]
        self.assertEqual(urls[:2], [lv.ISSUES_URL, lv.GITHUB_URL])
        self.assertTrue(urls[2].replace("\\", "/").endswith("/docs/Contributor-Guide.pdf"), urls[2])

    def test_a_missing_browser_shows_the_address(self):
        shell = mock.MagicMock()
        shell.execute.side_effect = RuntimeError("no browser")
        with mock.patch.object(lv, "msgbox") as box:
            lv.open_url(self.context(shell), lv.GITHUB_URL)
        self.assertIn(lv.GITHUB_URL, box.call_args[0][1])

    def test_errors_mention_github_and_other_messages_do_not(self):
        with mock.patch.object(lv, "log") as log:
            lv.msgbox(None, "Panne", "errorbox")
            self.assertIn(lv.ISSUES_URL, log.call_args[0][1])
            lv.msgbox(None, "Terminé")
            self.assertNotIn(lv.ISSUES_URL, log.call_args[0][1])


class InterfaceLanguageTests(unittest.TestCase):
    CODES = [code for code, name in lv.UI_LANGUAGES]

    def tearDown(self):
        lv._UI.update(language="fr", strings={})

    def test_follows_libreoffice_by_default(self):
        self.assertEqual(lv.resolve_ui_language("auto", "de"), "de")
        self.assertEqual(lv.resolve_ui_language("auto", "pt-BR"), "pt")
        self.assertEqual(lv.resolve_ui_language("auto", "fr-CA"), "fr")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(lv.resolve_ui_language("auto", "ja"), "en")
        self.assertEqual(lv.resolve_ui_language("auto", ""), "en")

    def test_the_option_beats_libreoffice(self):
        self.assertEqual(lv.resolve_ui_language("es", "de"), "es")

    def test_every_language_is_complete_and_has_the_same_placeholders(self):
        placeholder = re.compile(r"%[-+ 0]*\d*[sd%]")
        english = lv.load_ui_strings("en")
        self.assertGreater(len(english), 100)
        for code in self.CODES[1:]:
            table = lv.load_ui_strings(code)
            self.assertEqual(set(table), set(english), code)
            for source, text in table.items():
                self.assertTrue(text.strip(), (code, source))
                self.assertEqual(placeholder.findall(source), placeholder.findall(text),
                                 (code, source))

    def test_every_text_of_the_code_is_translated(self):
        tree = ast.parse((ROOT / "extension" / "python" / "lecture_vocale.py")
                         .read_text(encoding="utf-8"))
        used = {node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_"
                and node.args and isinstance(node.args[0], ast.Constant)}
        used |= set(lv.MENU_TITLES.values()) | set(lv._ENGINE_LABELS.values()) - {
            "Speech Dispatcher (spd-say)", "eSpeak NG / MBROLA"}
        self.assertEqual(sorted(used - set(lv.load_ui_strings("en"))), [])

    def test_the_translation_function_is_never_shadowed(self):
        tree = ast.parse((ROOT / "extension" / "python" / "lecture_vocale.py")
                         .read_text(encoding="utf-8"))
        shadows = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Name)
                   and node.id == "_" and isinstance(node.ctx, ast.Store)]
        self.assertEqual(shadows, [])

    def test_menus_are_available_in_every_language(self):
        text = (ROOT / "extension" / "Addons.xcu").read_text(encoding="utf-8")
        total = text.count('xml:lang="fr"')
        self.assertGreater(total, 18)
        for code in ("en-US", "es", "de", "it", "pt"):
            self.assertEqual(text.count('xml:lang="%s"' % code), total, code)

    def test_translation_is_applied(self):
        lv._UI.update(language="de", strings=lv.load_ui_strings("de"))
        self.assertEqual(lv._("Fermer"), "Schließen")
        with mock.patch.object(lv, "log") as log:
            lv.msgbox(None, "x", "errorbox")
        self.assertIn("Wenn das Problem", log.call_args[0][1])


class ConfigConcurrencyTests(unittest.TestCase):
    def test_no_change_is_lost_while_others_read(self):
        self.addCleanup(lambda: os.path.exists(lv.CONFIG_FILE) and os.remove(lv.CONFIG_FILE))
        stop = threading.Event()

        def keep_reading():
            while not stop.is_set():
                lv.load_config()

        readers = [threading.Thread(target=keep_reading) for _ in range(4)]
        for reader in readers:
            reader.start()
        try:
            with mock.patch.object(lv, "log") as log:
                for value in range(-10, 11):
                    self.assertEqual(lv.update_config(pitch=value)["pitch"], value)
            log.assert_not_called()
        finally:
            stop.set()
            for reader in readers:
                reader.join()


class LocaleTests(unittest.TestCase):
    @staticmethod
    def holder(language, country="", variant=""):
        locale = types.SimpleNamespace(Language=language, Country=country, Variant=variant)
        return types.SimpleNamespace(getPropertyValue=lambda name: locale)

    def test_values(self):
        self.assertEqual(lv.locale_language(self.holder("fr", "FR")), "fr-FR")
        self.assertEqual(lv.locale_language(self.holder("fr")), "fr")
        self.assertEqual(lv.locale_language(self.holder("zxx")), "")
        self.assertEqual(lv.locale_language(self.holder("qlt", "", "ca-ES-valencia")),
                         "ca-ES-valencia")
        self.assertEqual(lv.locale_language(None), "")


class ScriptTests(unittest.TestCase):
    @staticmethod
    def holder(**locales):
        def get(name):
            language, _, country = locales[name].partition("-")
            return types.SimpleNamespace(Language=language, Country=country, Variant="")
        return types.SimpleNamespace(getPropertyValue=get)

    def language(self, text, **locales):
        document = self.holder(CharLocale="fr-FR", CharLocaleAsian="zh-CN",
                               CharLocaleComplex="hi-IN")
        return lv.text_language(text, self.holder(**locales) if locales else None, document)

    def test_latin_text_keeps_the_declared_language(self):
        self.assertEqual(self.language("Bonjour à tous"), "fr-FR")
        self.assertEqual(self.language("Hello", CharLocale="en-GB"), "en-GB")

    def test_asian_and_complex_properties_are_read(self):
        self.assertEqual(self.language("你好世界", CharLocaleAsian="zh-TW"), "zh-TW")
        self.assertEqual(self.language("مرحبا بالعالم", CharLocaleComplex="ar-EG"), "ar-EG")

    def test_default_of_another_language_is_corrected_by_the_script(self):
        self.assertEqual(self.language("مرحبا بالعالم"), "ar")
        self.assertEqual(self.language("Привет, мир"), "ru")
        self.assertEqual(self.language("Γειά σου Κόσμε"), "el")
        self.assertEqual(self.language("שלום עולם"), "he")
        self.assertEqual(self.language("สวัสดีชาวโลก"), "th")
        self.assertEqual(self.language("안녕하세요"), "ko")

    def test_japanese_is_not_read_as_chinese(self):
        self.assertEqual(self.language("こんにちは世界"), "ja")

    def test_declared_language_of_the_same_script_is_kept(self):
        self.assertEqual(self.language("Привіт", CharLocale="uk-UA"), "uk-UA")
        self.assertEqual(self.language("नमस्ते", CharLocaleComplex="hi-IN"), "hi-IN")

    def test_a_few_foreign_letters_change_nothing(self):
        self.assertEqual(self.language("La lettre grecque α dans une phrase"), "fr-FR")


@unittest.skipUnless(sys.platform == "win32", "needs Windows")
class SapiAudioTests(unittest.TestCase):
    """Renders real audio: the voice picked must be the voice heard."""

    def render(self, voices, language, voice="", pitch=0):
        with mock.patch.object(lv, "sapi_voices", return_value=voices):
            engine = lv.SapiEngine(dict(lv.DEFAULTS, voice=voice, pitch=pitch), language)
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "out.wav")
                engine.synth("Bonjour tout le monde", path, language)
                return Path(path).read_bytes()

    def test_every_installed_voice_has_its_own_sound(self):
        """Each voice of the list, chosen in turn, must change what is heard."""
        installed = lv.sapi_voices()
        heard = [self.render(installed, "fr-FR", voice[0]) for voice in installed]
        self.assertGreater(len(installed), 0)
        self.assertEqual(len(set(heard)), len(installed))

    def test_voices_seen_only_by_the_older_api_work(self):
        command = ("Add-Type -AssemblyName System.Speech; "
                   "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                   "foreach ($v in $s.GetInstalledVoices()) { "
                   "$v.VoiceInfo.Name + '|' + $v.VoiceInfo.Culture.Name }")
        out = subprocess.run([lv.powershell(), "-NoProfile", "-Command", command],
                             capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        voices = [tuple(line.strip().split("|")) + ("sapi",) for line in out.splitlines() if "|" in line]
        heard = {self.render(voices, voice[1], voice[0]) for voice in voices}
        self.assertEqual(len(heard), len(voices))

    def test_pitch_changes_the_audio_of_every_kind_of_voice(self):
        for source in ("winrt", "sapi"):
            voices = [voice for voice in lv.sapi_voices() if voice[2] == source]
            if voices:
                language = voices[0][1]
                self.assertNotEqual(self.render(voices, language, voices[0][0], 0),
                                    self.render(voices, language, voices[0][0], 6), source)

    def test_audio_can_be_assembled_for_export(self):
        voices = lv.sapi_voices()
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("a.wav", "b.wav"):
                paths.append(os.path.join(directory, name))
                Path(paths[-1]).write_bytes(self.render(voices, voices[0][1]))
            out = os.path.join(directory, "out.wav")
            lv.concat_wavs(paths, out)
            self.assertGreater(lv._wav_seconds(out), 1)

    def test_every_voice_plays_to_the_end(self):
        """Runs the very command used for reading, for every installed voice."""
        for voice in lv.sapi_voices():
            engine = lv.SapiEngine(dict(lv.DEFAULTS, voice=voice[0]), voice[1])
            command, _ = engine.speak_command("Oui", voice[1])
            result = subprocess.run(command, capture_output=True, timeout=60)
            self.assertEqual((voice[0], result.returncode, result.stderr), (voice[0], 0, b""))

    def test_a_failing_script_is_not_silent(self):
        voices = [("No Such Voice", "fr-FR", "sapi")]
        with mock.patch.object(lv, "sapi_voices", return_value=voices):
            engine = lv.SapiEngine(dict(lv.DEFAULTS), "fr-FR")
            command, _ = engine.speak_command("Oui", "fr-FR")
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


class BuildTests(unittest.TestCase):
    def test_declares_libreoffice_24_2(self):
        text = (ROOT / "extension" / "description.xml").read_text(encoding="utf-8")
        self.assertIn('lo:value="24.2"', text)
        self.assertIn('identifier value="com.adanalytics.lecturevocale"', text)

    def test_shortcuts_use_the_real_configuration_name(self):
        text = (ROOT / "extension" / "Accelerators.xcu").read_text(encoding="utf-8")
        self.assertIn('oor:name="Accelerators"', text)

    def test_versions_agree(self):
        text = (ROOT / "extension" / "description.xml").read_text(encoding="utf-8")
        self.assertIn('version value="%s"' % lv.VERSION, text)

    def test_build_runs(self):
        subprocess.run([sys.executable, str(ROOT / "build.py")], check=True,
                       stdout=subprocess.DEVNULL)

    def test_the_guide_is_in_the_extension(self):
        subprocess.run([sys.executable, str(ROOT / "build.py")], check=True,
                       stdout=subprocess.DEVNULL)
        target = ROOT / "dist" / ("LectureVocale-%s.oxt" % lv.VERSION)
        with zipfile.ZipFile(target) as archive:
            guide = archive.read("docs/Contributor-Guide.pdf")
        self.assertTrue(guide.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
