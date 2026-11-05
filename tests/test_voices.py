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

FRENCH_ONLY_MAN = [("Paul", "fr-FR", "male"), ("Zira", "en-US", "female")]
WINDOWS_VOICES = [("David", "en-US", "male", "sapi"), ("Zira", "en-US", "female", "sapi"),
                  ("Hortense", "fr-FR", "female", "sapi")]


class PickVoiceTests(unittest.TestCase):
    def test_language_comes_first(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "fr-FR", "male"), ("Hortense", False))

    def test_gender_inside_the_language(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "en-GB", "male"), ("David", True))
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "en-GB", "female"), ("Zira", True))

    def test_never_a_voice_of_another_language(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "de-DE", "female"), (None, False))

    def test_case_and_separator_do_not_matter(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "FR", "female")[0], "Hortense")
        self.assertEqual(lv.pick_voice([("A", "fr_CA", None)], "fr", "male"), ("A", True))

    def test_two_codes_for_one_language(self):
        self.assertEqual(lv.pick_voice([("Li", "cmn", "female")], "zh-CN", "female")[0], "Li")
        self.assertEqual(lv.pick_voice([("Wei", "zh-CN", "male")], "cmn", "male")[0], "Wei")
        self.assertEqual(lv.pick_voice([("Ola", "no", None)], "nb-NO", "male")[0], "Ola")
        self.assertIsNone(lv.pick_voice([("Li", "cmn", "female")], "ja", "female")[0])

    def test_exact_region_wins_at_equal_gender(self):
        voices = [("F", "fr-FR", "female"), ("C", "fr-CA", "female")]
        self.assertEqual(lv.pick_voice(voices, "fr-CA", "female")[0], "C")

    def test_preferred_voice_needs_language_and_gender(self):
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "en-US", "female", "Zira")[0], "Zira")
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "en-US", "male", "Zira")[0], "David")
        self.assertEqual(lv.pick_voice(WINDOWS_VOICES, "fr-FR", "female", "Zira")[0], "Hortense")


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
        engine = self.engine([ryan, tom], gender="male")
        self.assertEqual(engine.model, tom)

    def test_female_voice_of_the_language_is_chosen(self):
        siwis = self.model("fr_FR-siwis-medium", "fr_FR")
        tom = self.model("fr_FR-tom-medium", "fr_FR")
        amy = self.model("en_US-amy-medium", "en_US")
        engine = self.engine([tom, amy, siwis], gender="female")
        self.assertEqual(engine.model, siwis)
        self.assertFalse(engine.gender_fallback)

    def test_gender_fallback_stays_in_the_language(self):
        tom = self.model("fr_FR-tom-medium", "fr_FR")
        amy = self.model("en_US-amy-medium", "en_US")
        engine = self.engine([amy, tom], gender="female")
        self.assertEqual(engine.model, tom)
        self.assertTrue(engine.gender_fallback)

    def test_configured_model_of_another_language_is_ignored(self):
        amy = self.model("en_US-amy-medium", "en_US")
        siwis = self.model("fr_FR-siwis-medium", "fr_FR")
        engine = self.engine([amy, siwis], gender="female", piper_model_female=amy)
        self.assertEqual(engine.model, siwis)
        self.assertEqual(engine._pick("en-US")[0], amy)

    def test_reports_a_language_without_model(self):
        ryan = self.model("en_US-ryan-medium", "en_US")
        engine = self.engine([ryan])
        self.assertFalse(engine.supports("fr-FR"))
        self.assertTrue(engine.supports("en-GB"))

    def test_speaker_of_a_multi_speaker_model(self):
        upmc = self.model("fr_FR-upmc-medium", "fr_FR", {"jessica": 0, "pierre": 1})
        self.assertEqual(self.engine([upmc], gender="male").speaker, 1)
        self.assertEqual(self.engine([upmc], gender="female").speaker, 0)

    def test_language_from_file_name_without_json(self):
        path = self.dir / "de_DE-thorsten-high.onnx"
        path.write_bytes(b"x")
        self.assertEqual(lv.model_language(str(path)), "de")


class EngineTests(unittest.TestCase):
    def setUp(self):
        which = mock.patch.object(lv, "_which", side_effect=lambda *names: names[0])
        which.start()
        self.addCleanup(which.stop)

    def test_espeak_follows_the_text(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS))
        self.assertEqual(engine.voice("en-GB"), "en+f3")
        self.assertEqual(engine.voice("fr-FR"), "fr+f3")

    def test_espeak_gender(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS, gender="male"))
        self.assertEqual(engine.voice("fr"), "fr+m3")

    def test_espeak_configured_voice_only_for_its_language(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS, espeak_voice="mb-fr1"))
        self.assertEqual(engine.voice("fr-FR"), "mb-fr4")
        self.assertEqual(engine.voice("en-US"), "en+f3")

    def test_forced_language_beats_the_text(self):
        engine = lv.EspeakEngine(dict(lv.DEFAULTS, language="de"))
        self.assertEqual(engine.voice("fr-FR"), "de+f3")

    def test_spd_gets_a_normalized_language(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS, language="FR"))
        engine.voices = lambda: []
        command, _ = engine.speak_command("Bonjour")
        self.assertEqual(command[command.index("-l") + 1], "fr")
        self.assertIn("female1", command)

    def test_spd_voice_list(self):
        output = "   NAME   LANGUAGE  VARIANT\n   Mary Ann  en-US  none\n   Marie  fr  none\n"
        self.assertEqual(lv.parse_spd_voices(output),
                         [("Mary Ann", "en-US", None), ("Marie", "fr", None)])

    def test_spd_keeps_the_region_it_knows(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS))
        engine.voices = lambda: [("a", "pt-BR", None), ("b", "fr", None)]
        self.assertEqual(engine._language_option("pt-BR"), "pt-BR")
        self.assertEqual(engine._language_option("fr-CA"), "fr")

    def test_spd_uses_the_code_its_voices_know(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS))
        engine.voices = lambda: [("Mandarin", "cmn", None), ("Norwegian", "nb", None)]
        self.assertTrue(engine.supports("zh-CN"))
        self.assertEqual(engine._language_option("zh-CN"), "cmn")
        self.assertEqual(engine._language_option("no"), "nb")

    def test_spd_voice_only_for_its_language(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS, spd_voice="Marie"))
        engine.voices = lambda: [("Marie", "fr", None), ("Mary", "en", None)]
        french, _ = engine.speak_command("Bonjour", "fr-FR")
        self.assertIn("-y", french)
        english, _ = engine.speak_command("Hello", "en-US")
        self.assertNotIn("-y", english)
        self.assertIn("female1", english)

    def test_spd_pitch(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS, pitch=-4))
        engine.voices = lambda: []
        command, _ = engine.speak_command("Bonjour", "fr-FR")
        self.assertEqual(command[command.index("-p") + 1], "-40")

    def test_spd_reports_a_module_without_the_language(self):
        engine = lv.SpdEngine(dict(lv.DEFAULTS))
        engine.voices = lambda: [("Mary", "en", None)]
        engine.speak_command("Bonjour", "fr-FR")
        self.assertIn("fr-FR", engine.take_warning())
        engine.speak_command("Bonjour", "fr-FR")
        self.assertEqual(engine.take_warning(), "")

    def test_mac_voice_follows_language_and_gender(self):
        voices = [("Thomas", "fr_FR", "male"), ("Amélie", "fr_CA", "female"),
                  ("Samantha", "en_US", "female")]
        with mock.patch.object(lv, "IS_MAC", True), \
                mock.patch.object(lv, "mac_voices", return_value=voices):
            engine = lv.MacSayEngine(dict(lv.DEFAULTS), "fr-FR")
        self.assertEqual(engine._pick("fr-FR"), "Amélie")
        self.assertEqual(engine._pick("en-US"), "Samantha")
        self.assertIsNone(engine._pick("de-DE"))
        self.assertIn("de-DE", engine.take_warning())

    def test_mac_genders_come_from_the_system(self):
        output = "Kyoko|VoiceGenderFemale\nOtoya|VoiceGenderMale\nZarvox|VoiceGenderNeuter\n"
        self.assertEqual(lv.parse_mac_genders(output),
                         {"Kyoko": "female", "Otoya": "male", "Zarvox": None})

    def test_sapi_voice_list_merges_both_apis(self):
        output = ("\ufeffMicrosoft Paul|fr-FR|Male|winrt\r\n"
                  "Microsoft Hortense|fr-FR|Female|winrt\r\n"
                  "Microsoft Hortense Desktop|fr-FR|Female|sapi\r\n"
                  "Vendor Voice|de-DE|Neutral|sapi\r\nbad line\r\n")
        self.assertEqual(lv.parse_sapi_voices(output),
                         [("Microsoft Paul", "fr-FR", "male", "winrt"),
                          ("Microsoft Hortense", "fr-FR", "female", "winrt"),
                          ("Vendor Voice", "de-DE", None, "sapi")])

    def test_modern_windows_voice_gets_speed_and_pitch(self):
        voices = [("Microsoft Paul", "fr-FR", "male", "winrt")]
        with mock.patch.object(lv, "IS_WIN", True), \
                mock.patch.object(lv, "sapi_voices", return_value=voices):
            engine = lv.SapiEngine(dict(lv.DEFAULTS, gender="male", speed=150, pitch=4), "fr-FR")
            script = engine._script("t.txt", None, "fr-FR")
        self.assertIn("-eq 'Microsoft Paul'", script)
        self.assertIn("SpeakingRate = 1.50", script)
        self.assertIn("AudioPitch = 1.20", script)

    def test_name_saved_by_version_1_2_still_works(self):
        voices = [("Microsoft Julie", "fr-FR", "female", "winrt"),
                  ("Microsoft Hortense", "fr-FR", "female", "winrt")]
        with mock.patch.object(lv, "IS_WIN", True), \
                mock.patch.object(lv, "sapi_voices", return_value=voices):
            engine = lv.SapiEngine(
                dict(lv.DEFAULTS, sapi_voice="Microsoft Hortense Desktop"), "fr-FR")
            self.assertEqual(engine._voice("fr-FR"), "Microsoft Hortense")

    def test_sapi_script_never_hardcodes_the_ssml_language(self):
        with mock.patch.object(lv, "IS_WIN", True), \
                mock.patch.object(lv, "sapi_voices", return_value=WINDOWS_VOICES):
            engine = lv.SapiEngine(dict(lv.DEFAULTS, gender="male"), "en-US")
            script = engine._script("t.txt", None, "en-US")
        self.assertIn("$s.SelectVoice('David')", script)
        self.assertIn("$s.Voice.Culture.Name", script)
        self.assertNotIn("catch", script)


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
            self.assertEqual(lv.spd_voices("spd-say"), [("Marie", "fr", None)])
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
        self.write({"language": "fr", "espeak_voice": "fr", "gender": "male"})
        cfg = lv.load_config()
        self.assertEqual((cfg["language"], cfg["espeak_voice"], cfg["gender"]),
                         ("auto", "", "male"))

    def test_old_explicit_choice_is_kept(self):
        self.write({"language": "en", "espeak_voice": "mb-fr4"})
        cfg = lv.load_config()
        self.assertEqual((cfg["language"], cfg["espeak_voice"]), ("en", "mb-fr4"))

    def test_current_choice_of_french_is_kept(self):
        self.write({"language": "fr", "config_version": lv.CONFIG_VERSION})
        self.assertEqual(lv.load_config()["language"], "fr")


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

    def test_gender_warning_is_a_whole_sentence(self):
        lv._UI.update(language="en", strings=lv.load_ui_strings("en"))
        with mock.patch.object(lv, "_which", side_effect=lambda *names: names[0]):
            engine = lv.EspeakEngine(dict(lv.DEFAULTS, gender="male"))
        engine.note_missing_gender("fr")
        self.assertEqual(engine.take_warning(),
                         'eSpeak NG has no male voice for the language "fr": '
                         "a female voice is used.")


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

    def render(self, voices, language, gender, pitch=0):
        with mock.patch.object(lv, "sapi_voices", return_value=voices):
            engine = lv.SapiEngine(dict(lv.DEFAULTS, gender=gender, pitch=pitch), language)
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "out.wav")
                engine.synth("Bonjour tout le monde", path, language)
                return Path(path).read_bytes()

    def test_selected_voice_is_heard(self):
        installed = lv.sapi_voices()
        english = [voice for voice in installed if voice[1] == "en-US"]
        french = [voice for voice in installed if voice[1] == "fr-FR"]
        if not english or not french:
            self.skipTest("needs a French and an English voice")
        self.assertNotEqual(self.render(installed, "en-US", "male"),
                            self.render(installed, "fr-FR", "male"))
        men = [voice for voice in english if voice[2] == "male"]
        women = [voice for voice in english if voice[2] == "female"]
        if men and women:
            self.assertNotEqual(self.render(installed, "en-US", "male"),
                                self.render(installed, "en-US", "female"))


    def test_french_man_and_woman_are_different_voices(self):
        installed = lv.sapi_voices()
        french = {voice[2] for voice in installed if voice[1] == "fr-FR"}
        if not {"female", "male"} <= french:
            self.skipTest("needs a French man and a French woman")
        self.assertNotEqual(self.render(installed, "fr-FR", "male"),
                            self.render(installed, "fr-FR", "female"))

    def test_pitch_changes_the_audio_of_every_kind_of_voice(self):
        for source in ("winrt", "sapi"):
            voices = [voice for voice in lv.sapi_voices() if voice[3] == source]
            if voices:
                language, gender = voices[0][1], voices[0][2]
                self.assertNotEqual(self.render(voices, language, gender, 0),
                                    self.render(voices, language, gender, 6), source)

    def test_audio_can_be_assembled_for_export(self):
        voices = lv.sapi_voices()
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("a.wav", "b.wav"):
                paths.append(os.path.join(directory, name))
                Path(paths[-1]).write_bytes(self.render(voices, voices[0][1], voices[0][2]))
            out = os.path.join(directory, "out.wav")
            lv.concat_wavs(paths, out)
            self.assertGreater(lv._wav_seconds(out), 1)


    def test_playback_scripts_run_to_the_end(self):
        """Runs the very command used for reading, for each kind of voice."""
        for source in ("winrt", "sapi"):
            voices = [voice for voice in lv.sapi_voices() if voice[3] == source]
            if not voices:
                continue
            with mock.patch.object(lv, "sapi_voices", return_value=voices[:1]):
                engine = lv.SapiEngine(dict(lv.DEFAULTS, gender=voices[0][2] or "female"),
                                       voices[0][1])
                command, _ = engine.speak_command("Oui", voices[0][1])
            result = subprocess.run(command, capture_output=True, timeout=60)
            self.assertEqual((source, result.returncode, result.stderr), (source, 0, b""))

    def test_a_failing_script_is_not_silent(self):
        voices = [("No Such Voice", "fr-FR", "female", "sapi")]
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
