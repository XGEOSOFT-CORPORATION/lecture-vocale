# -*- coding: utf-8 -*-
"""Lecture vocale pour LibreOffice (Writer, Calc, Impress, Draw).

Moteurs : Piper (voix neuronales hors ligne), Speech Dispatcher (spd-say),
eSpeak NG / MBROLA, Windows SAPI, voix macOS. Lecture phrase par phrase avec surlignage,
pause/reprise, phrase précédente/suivante, réglage en direct du débit, de la
tonalité et de la voix (femme/homme), export audio (WAV, MP3, OGG).

Auteur : ADAnalytics — licence MIT.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
import itertools

import uno
import unohelper
from com.sun.star.task import XJobExecutor
from com.sun.star.awt import (XActionListener, XCallback, XAdjustmentListener,
                              XItemListener, XTopWindowListener)

IMPL_NAME = "com.adanalytics.LectureVocale"
SERVICE_NAMES = ("com.adanalytics.LectureVocale",)
VERSION = "1.3.0"
TITLE = "Lecture vocale"

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WIN else 0
HOME = os.path.expanduser("~")
HEADLESS = bool(os.environ.get("LECTURE_VOCALE_HEADLESS"))

SAMPLE_TEXT = ("Bonjour ! Ceci est un test de la lecture vocale dans LibreOffice. "
               "Le débit, la tonalité et la voix se règlent pendant la lecture.")
SAMPLE_TEXT_EN = ("Hello! This is a test of Read Aloud in LibreOffice. "
                  "Speed, pitch and voice can be changed while reading.")

_ENGINE_LABELS = {"auto": "Automatique (voix du système d'abord)",
                  "piper": "Piper — voix neuronale hors ligne",
                  "spd": "Speech Dispatcher (spd-say)",
                  "sapi": "Voix Windows (toutes les voix installées)",
                  "say": "Voix macOS (toutes les voix installées)",
                  "espeak": "eSpeak NG / MBROLA"}
# L'ordre compte : le mode automatique essaie d'abord le service vocal du système.
ENGINE_KEYS = list(("auto", "sapi", "piper", "espeak") if IS_WIN else
                   ("auto", "say", "piper", "espeak") if IS_MAC else
                   ("auto", "spd", "piper", "espeak"))
ENGINE_LABELS = [_ENGINE_LABELS[key] for key in ENGINE_KEYS]

SPEED_MIN, SPEED_MAX, SPEED_STEP = 50, 250, 10
PITCH_MIN, PITCH_MAX = -10, 10
_COUNTER = itertools.count()
CONFIG_VERSION = 2


# --------------------------------------------------------------------------
# Configuration et journal
# --------------------------------------------------------------------------

def config_dir():
    if IS_WIN:
        base = os.environ.get("APPDATA") or HOME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
    path = os.path.join(base, "lecture-vocale-libreoffice")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = tempfile.gettempdir()
    return path


CONFIG_FILE = os.path.join(config_dir(), "config.json")
LOG_FILE = os.path.join(config_dir(), "lecture-vocale.log")

DEFAULTS = {
    "engine": "auto",
    "piper_bin": "",
    "piper_model": "",          # voix choisie dans la v1.0 (reste utilisée en secours)
    "piper_model_female": "",
    "piper_model_male": "",
    "speed": 100,               # % : 100 = normal
    "pitch": 0,                 # -10 (grave) … +10 (aigu)
    "gender": "female",         # female | male
    "language": "auto",         # auto = langue du texte, sinon code forcé (fr, en…)
    "spd_module": "",
    "spd_voice": "",
    "espeak_voice": "",
    "sapi_voice": "",
    "mac_voice": "",
    "highlight": True,
    "pause_ms": 200,
    "config_version": CONFIG_VERSION,
}

_CFG_LOCK = threading.Lock()


def log(*parts):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") +
                     " ".join(str(p) for p in parts) + "\n")
    except Exception:
        pass


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except Exception:
        return default


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        if data.get("config_version", 1) < 2:
            # Jusqu'à la 1.2, « fr » était une valeur par défaut enregistrée, pas un choix.
            if str(cfg["language"]).lower() == "fr":
                cfg["language"] = "auto"
            if str(cfg["espeak_voice"]).lower() == "fr":
                cfg["espeak_voice"] = ""
            cfg["config_version"] = CONFIG_VERSION
    except Exception:
        pass
    cfg["speed"] = _clamp(cfg["speed"], 30, 300, 100)
    cfg["pitch"] = _clamp(cfg["pitch"], PITCH_MIN, PITCH_MAX, 0)
    if cfg["gender"] not in ("female", "male"):
        cfg["gender"] = "female"
    return cfg


def save_config(cfg):
    with _CFG_LOCK:
        try:
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_FILE)
        except Exception as exc:
            log("save_config:", exc)


def update_config(**changes):
    cfg = load_config()
    cfg.update(changes)
    save_config(cfg)
    return load_config()


# --------------------------------------------------------------------------
# Détection des outils
# --------------------------------------------------------------------------

def _extra_bin_dirs():
    """Dossiers absents du PATH quand LibreOffice est lancé depuis le Finder, le menu
    Démarrer ou un Flatpak (Homebrew, MacPorts, Program Files…)."""
    dirs = [os.path.join(HOME, ".local", "bin"), os.path.join(HOME, "bin")]
    if IS_WIN:
        for root in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            if root:
                dirs += [os.path.join(root, "eSpeak NG"), os.path.join(root, "eSpeak", "command_line"),
                         os.path.join(root, "ffmpeg", "bin"), os.path.join(root, "piper")]
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        dirs.append(os.path.join(sysroot, "System32", "WindowsPowerShell", "v1.0"))
    else:
        dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin", "/bin",
                 "/run/host/usr/bin", "/snap/bin"]
    return dirs


def _which(*names):
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    extra = os.pathsep.join(d for d in _extra_bin_dirs() if os.path.isdir(d))
    for name in names:
        path = shutil.which(name, path=extra) if extra else None
        if path:
            return path
    return ""


def primary_language(tag):
    """« fr-FR », « fr_CA » ou « FR » → « fr »."""
    return re.split(r"[-_]", (tag or "").strip().lower())[0]


def pick_voice(voices, language, gender, preferred=None):
    """Choisit une voix parmi des triplets (voix, locale, genre ou None).

    La langue est impérative : aucune voix d'une autre langue n'est renvoyée.
    Viennent ensuite la voix préférée si elle a le bon genre, le genre demandé,
    puis la région exacte (fr-CA avant fr-FR). Renvoie (voix, genre respecté) ;
    voix vaut None si personne ne parle cette langue.
    """
    wanted = primary_language(language)
    exact = (language or "").replace("_", "-").lower()
    same = [v for v in voices if primary_language(v[1]) == wanted]
    same.sort(key=lambda v: v[1].replace("_", "-").lower() != exact)
    for voice in same:
        if voice[0] == preferred and voice[2] in (gender, None):
            return voice[0], True
    for wanted_gender in (gender, None):
        for voice in same:
            if voice[2] == wanted_gender:
                return voice[0], True
    return (same[0][0], False) if same else (None, False)


# Écritures reconnues : plages Unicode, propriété LibreOffice qui porte la langue de ce
# type de texte, langues qui l'utilisent ; la première sert quand le document n'en dit rien.
_SCRIPTS = (
    ("hangul", ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)), "CharLocaleAsian", ("ko",)),
    ("kana", ((0x3040, 0x30FF),), "CharLocaleAsian", ("ja",)),
    ("han", ((0x3400, 0x9FFF), (0xF900, 0xFAFF)), "CharLocaleAsian",
     ("zh", "ja", "ko", "yue", "lzh")),
    ("hebrew", ((0x0590, 0x05FF),), "CharLocaleComplex", ("he", "yi")),
    ("arabic", ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)), "CharLocaleComplex",
     ("ar", "fa", "ur", "ps", "ku", "sd", "ug")),
    ("devanagari", ((0x0900, 0x097F),), "CharLocaleComplex", ("hi", "mr", "ne", "sa", "kok")),
    ("bengali", ((0x0980, 0x09FF),), "CharLocaleComplex", ("bn", "as")),
    ("gurmukhi", ((0x0A00, 0x0A7F),), "CharLocaleComplex", ("pa",)),
    ("gujarati", ((0x0A80, 0x0AFF),), "CharLocaleComplex", ("gu",)),
    ("tamil", ((0x0B80, 0x0BFF),), "CharLocaleComplex", ("ta",)),
    ("telugu", ((0x0C00, 0x0C7F),), "CharLocaleComplex", ("te",)),
    ("kannada", ((0x0C80, 0x0CFF),), "CharLocaleComplex", ("kn",)),
    ("malayalam", ((0x0D00, 0x0D7F),), "CharLocaleComplex", ("ml",)),
    ("thai", ((0x0E00, 0x0E7F),), "CharLocaleComplex", ("th",)),
    ("lao", ((0x0E80, 0x0EFF),), "CharLocaleComplex", ("lo",)),
    ("myanmar", ((0x1000, 0x109F),), "CharLocaleComplex", ("my",)),
    ("khmer", ((0x1780, 0x17FF),), "CharLocaleComplex", ("km",)),
    ("greek", ((0x0370, 0x03FF), (0x1F00, 0x1FFF)), "CharLocale", ("el", "grc")),
    ("cyrillic", ((0x0400, 0x052F),), "CharLocale",
     ("ru", "uk", "be", "bg", "sr", "mk", "kk", "ky", "mn", "tg", "uz", "tt", "ba")),
    ("armenian", ((0x0530, 0x058F),), "CharLocale", ("hy",)),
    ("georgian", ((0x10A0, 0x10FF),), "CharLocale", ("ka",)),
)


def text_script(text):
    """Écriture dominante du texte (entrée de _SCRIPTS), None pour l'alphabet latin."""
    counts = {}
    latin = 0
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        for script in _SCRIPTS:
            if any(low <= code <= high for low, high in script[1]):
                counts[script[0]] = counts.get(script[0], 0) + 1
                break
        else:
            latin += 1
    if not counts:
        return None
    # Le japonais mêle kana et idéogrammes : un seul kana suffit à le reconnaître.
    if counts.get("kana") and counts.get("han"):
        counts["kana"] += counts.pop("han")
    best = max(counts, key=counts.get)
    if counts[best] <= latin:
        return None
    return next(script for script in _SCRIPTS if script[0] == best)


def install_hint(apt, brew, win):
    """Conseil d'installation adapté au système."""
    if IS_WIN:
        return win
    if IS_MAC:
        return "brew install " + brew
    return "sudo apt install " + apt


def powershell():
    return _which("powershell", "pwsh") or "powershell"


def _spd_conf_text():
    chunks = []
    for folder in (os.path.join(HOME, ".config", "speech-dispatcher", "modules"),
                   os.path.join(HOME, ".config", "speech-dispatcher"),
                   "/etc/speech-dispatcher/modules"):
        try:
            for name in os.listdir(folder):
                if name.endswith(".conf"):
                    with open(os.path.join(folder, name), "r",
                              encoding="utf-8", errors="replace") as fh:
                        chunks.append(fh.read())
        except OSError:
            pass
    return "\n".join(chunks)


def find_piper(cfg=None):
    if cfg and cfg.get("piper_bin") and os.path.isfile(cfg["piper_bin"]):
        return cfg["piper_bin"]
    exe = "piper.exe" if IS_WIN else "piper"
    found = _which(exe, "piper-tts")
    if found:
        return found
    candidates = [os.path.join(HOME, ".local", "bin", exe),
                  os.path.join(HOME, "piper", exe),
                  os.path.join(HOME, "piper", "piper", exe),
                  os.path.join(HOME, ".local", "share", "piper", exe),
                  os.path.join(HOME, ".local", "share", "piper", "piper", exe),
                  os.path.join(HOME, ".local", "opt", "piper", exe),
                  "/opt/piper/" + exe, "/opt/piper/piper/" + exe,
                  "/usr/local/share/piper/" + exe, "/usr/share/piper/" + exe]
    if IS_WIN:
        for root in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), HOME):
            if root:
                candidates += [os.path.join(root, "piper", exe),
                               os.path.join(root, "piper", "piper", exe)]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    for match in re.findall(r"""(/[^\s"'|;]*?/piper)(?=[\s"'])""", _spd_conf_text()):
        if os.path.isfile(match) and os.access(match, os.X_OK):
            return match
    return ""


def _model_dirs():
    dirs = [os.path.join(HOME, ".local", "share", "piper"),
            os.path.join(HOME, ".local", "share", "piper-voices"),
            os.path.join(HOME, ".local", "share", "piper-tts"),
            os.path.join(HOME, ".local", "share", "speech-dispatcher"),
            os.path.join(HOME, ".config", "piper"),
            os.path.join(HOME, "piper"),
            os.path.join(HOME, "piper-voices"),
            os.path.join(HOME, "voices"),
            "/usr/share/piper-voices", "/usr/local/share/piper-voices",
            "/usr/share/piper", "/usr/local/share/piper", "/opt/piper",
            "/usr/share/speech-dispatcher"]
    if IS_WIN:
        for root in (os.environ.get("LOCALAPPDATA", ""), HOME):
            if root:
                dirs.append(os.path.join(root, "piper"))
    return dirs


def _scan_models():
    found = []

    def add(path):
        path = os.path.abspath(os.path.expanduser(path))
        if path not in found and os.path.isfile(path) and os.path.isfile(path + ".json"):
            found.append(path)

    for match in re.findall(r"""([^\s"'=]+\.onnx)(?=[\s"']|$)""", _spd_conf_text(), re.M):
        add(match)
    shallow = [os.path.join(HOME, d) for d in ("Téléchargements", "Downloads", "Documents")]
    for base, max_depth in [(d, 4) for d in _model_dirs()] + [(d, 1) for d in shallow]:
        if not os.path.isdir(base):
            continue
        base_depth = base.rstrip(os.sep).count(os.sep)
        visited = 0
        for root, subdirs, files in os.walk(base):
            visited += 1
            if visited > 2000:
                break
            if root.count(os.sep) - base_depth >= max_depth:
                subdirs[:] = []
            for name in files:
                if name.endswith(".onnx"):
                    add(os.path.join(root, name))
    found.sort(key=lambda p: (0 if os.path.basename(p).lower().startswith("fr") else 1,
                              os.path.basename(p).lower()))
    return found


_MODELS = {"t": 0.0, "list": []}


def find_models(cfg=None, refresh=False):
    """Voix Piper (.onnx + .onnx.json) trouvées ; résultat mis en cache 2 minutes."""
    if refresh or time.time() - _MODELS["t"] > 120:
        _MODELS["list"] = _scan_models()
        _MODELS["t"] = time.time()
    models = list(_MODELS["list"])
    if cfg:
        for key in ("piper_model_male", "piper_model_female", "piper_model"):
            p = cfg.get(key) or ""
            if p and os.path.isfile(p) and p not in models:
                models.insert(0, p)
    return models


_FEMALE_TOKENS = {"siwis", "jessica", "female", "femme", "woman", "women", "girl", "fille",
                  "amy", "kathleen", "kristin", "lessac", "ljspeech", "jenny", "alba", "cori",
                  "kerstin", "eva", "ramona", "paola"}
_MALE_TOKENS = {"tom", "gilles", "pierre", "male", "homme", "man", "men", "bernard", "boy",
                "ryan", "alan", "joe", "john", "danny", "kusal", "norman", "thorsten",
                "karlsson", "pavoque", "davefx", "carlfm", "riccardo", "faber", "edresson"}


def guess_gender(path):
    tokens = set(re.split(r"[^a-z0-9]+", os.path.basename(path or "").lower()))
    if tokens & _FEMALE_TOKENS:
        return "female"
    if tokens & _MALE_TOKENS:
        return "male"
    return None


_MODEL_INFO = {}


def model_info(path):
    """Contenu du fichier .onnx.json d'une voix Piper (vide s'il est illisible)."""
    if path not in _MODEL_INFO:
        try:
            with open(path + ".json", "r", encoding="utf-8") as fh:
                _MODEL_INFO[path] = json.load(fh)
        except Exception as exc:
            log("voix piper:", path, exc)
            _MODEL_INFO[path] = {}
    return _MODEL_INFO[path]


def model_language(path):
    info = model_info(path)
    language = info.get("language")
    code = language.get("code") if isinstance(language, dict) else ""
    espeak = info.get("espeak")
    code = code or (espeak.get("voice") if isinstance(espeak, dict) else "")
    if code:
        return code
    match = re.match(r"([a-z]{2,3})[_-]", os.path.basename(path).lower())
    return match.group(1) if match else ""


def model_voices(path, cfg):
    """Triplets pour pick_voice : une voix par locuteur identifié, sinon le modèle.

    Une voix est un couple (modèle, numéro de locuteur ou None).
    """
    language = model_language(path)
    speakers = model_info(path).get("speaker_id_map")
    named = []
    if isinstance(speakers, dict):
        named = [((path, number), language, guess_gender(name))
                 for name, number in speakers.items() if guess_gender(name)]
    if named:
        return named
    gender = guess_gender(path)
    for assigned in ("female", "male"):
        if path == cfg.get("piper_model_" + assigned):
            gender = assigned
    return [((path, None), language, gender)]


_PIPER_HELP = {}


def piper_length_flag(piper):
    if piper not in _PIPER_HELP:
        txt = ""
        try:
            out = subprocess.run([piper, "--help"], capture_output=True, timeout=30,
                                 creationflags=NO_WINDOW)
            txt = (out.stdout + out.stderr).decode("utf-8", "replace")
        except Exception as exc:
            log("piper --help:", exc)
        _PIPER_HELP[piper] = txt
    txt = _PIPER_HELP[piper]
    if "--length_scale" in txt:
        return "--length_scale"
    if "--length-scale" in txt:
        return "--length-scale"
    return ""


def find_player():
    if IS_WIN:
        return [powershell()]
    players = (("afplay", []),) if IS_MAC else ()
    players += (("pw-play", []), ("paplay", []), ("aplay", ["-q"]),
                ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]))
    for name, args in players:
        path = _which(name)
        if path:
            return [path] + args
    return None


def play_command(wav_path):
    player = find_player()
    if player is None:
        raise EngineError("Aucun lecteur audio trouvé (pw-play, paplay, aplay ou ffplay).\n"
                          "Installez par exemple : sudo apt install pulseaudio-utils")
    if IS_WIN:
        safe = wav_path.replace("'", "''")
        return player + ["-NoProfile", "-NonInteractive", "-Command",
                "(New-Object Media.SoundPlayer '%s').PlaySync()" % safe]
    return player + [wav_path]


def detection_summary(cfg):
    def mark(ok):
        return "✓" if ok else "✗"
    piper = find_piper(cfg)
    models = find_models(cfg) if piper else []
    player = find_player()
    parts = ["Piper " + mark(piper and models) + (" (%d voix)" % len(models) if piper else "")]
    if not IS_WIN and not IS_MAC:
        parts.append("spd-say " + mark(_which("spd-say")))
    parts.append("eSpeak " + mark(_which("espeak-ng", "espeak")))
    if IS_WIN:
        parts.append("voix Windows ✓ (%d)" % len(sapi_voices()))
    if IS_MAC:
        parts.append("voix macOS %s (%d)" % (mark(_which("say")), len(mac_voices())))
    parts.append("lecteur : " + (os.path.basename(player[0]) if player else "aucun"))
    parts.append("ffmpeg " + mark(_which("ffmpeg")))
    return "   ".join(parts)


# --------------------------------------------------------------------------
# Moteurs de synthèse
# --------------------------------------------------------------------------

class EngineError(Exception):
    pass


class Engine(object):
    key = ""
    label = ""
    file_based = False
    can_export = False

    def __init__(self, cfg, language=None):
        self.language = language
        self.reported = set()
        self.warning = ""
        self.update(cfg)

    def language_for(self, language=None):
        """Langue forcée dans les paramètres, sinon celle du texte."""
        forced = str(self.cfg.get("language") or "auto").strip()
        if forced.lower() != "auto":
            return forced
        return language or self.language or "fr"

    def supports(self, language=None):
        """Ce moteur a-t-il une voix pour cette langue ?"""
        return True

    def _warn_once(self, key, message):
        if key not in self.reported:
            self.reported.add(key)
            self.warning = message

    def note_missing_language(self, language):
        self._warn_once(("language", primary_language(language)),
                        "%s n'a pas de voix pour la langue « %s » : la voix par défaut "
                        "lit le texte avec son propre accent.\nInstallez une voix pour "
                        "cette langue, puis relancez LibreOffice." % (self.label, language))

    def note_missing_gender(self, language):
        self._warn_once(("gender", primary_language(language), self.gender),
                        "%s n'a pas de voix %s pour la langue « %s » : une voix de "
                        "l'autre genre est utilisée."
                        % (self.label, "d'homme" if self.gender == "male" else "de femme",
                           language))

    def take_warning(self):
        warning, self.warning = self.warning, ""
        return warning

    def update(self, cfg):
        """Relit les réglages modifiables pendant la lecture."""
        self.cfg = cfg
        self.speed = _clamp(cfg.get("speed", 100), 30, 300, 100)
        self.pitch = _clamp(cfg.get("pitch", 0), PITCH_MIN, PITCH_MAX, 0)
        self.gender = cfg.get("gender", "female")

    def signature(self):
        return (self.speed, self.pitch, self.gender)

    def describe(self):
        return self.label

    def speak_command(self, text, language=None):
        raise NotImplementedError

    def synth(self, text, wav_path, language=None):
        raise EngineError("Ce moteur ne sait pas écrire de fichier audio.")

    def stop_all(self):
        pass


class PiperEngine(Engine):
    key, label = "piper", "Piper"
    file_based = True
    can_export = True

    def __init__(self, cfg, language=None):
        self.bin = find_piper(cfg)
        if not self.bin:
            raise EngineError("Exécutable Piper introuvable. Indiquez son chemin dans "
                              "Lecture vocale ▸ Paramètres.")
        self.models = find_models(cfg)
        self.ffmpeg = _which("ffmpeg")
        Engine.__init__(self, cfg, language)
        if not self.model:
            raise EngineError("Aucune voix Piper (.onnx + .onnx.json) trouvée. "
                              "Indiquez le fichier de voix dans les paramètres.")
        self.flag = piper_length_flag(self.bin)

    def update(self, cfg):
        Engine.update(self, cfg)
        self.model, self.speaker, self.gender_fallback = self._pick(self.language_for())

    def _voices(self):
        cfg = self.cfg
        pool = [cfg.get("piper_model_" + self.gender), cfg.get("piper_model"),
                cfg.get("piper_model_female"), cfg.get("piper_model_male")] + self.models
        seen = []
        for path in pool:
            if path and path not in seen and os.path.isfile(path):
                seen.append(path)
        return [voice for path in seen for voice in model_voices(path, cfg)]

    def supports(self, language=None):
        wanted = primary_language(self.language_for(language))
        return any(primary_language(voice[1]) == wanted for voice in self._voices())

    def _pick(self, language):
        """Renvoie (modèle, locuteur, genre non respecté)."""
        voices = self._voices()
        preferred = (self.cfg.get("piper_model_" + self.gender) or "", None)
        voice, gender_ok = pick_voice(voices, language, self.gender, preferred)
        if voice is None:
            voice = voices[0][0] if voices else ("", None)
        return voice[0], voice[1], not gender_ok

    def signature(self):
        return Engine.signature(self) + (self.model,)

    def describe(self):
        name = os.path.splitext(os.path.basename(self.model))[0]
        txt = "Piper · voix : " + name
        if self.gender_fallback:
            txt += ("\n⚠ Pas de voix Piper « %s » : installez par ex. %s"
                    % ("homme" if self.gender == "male" else "femme",
                       "fr_FR-tom-medium" if self.gender == "male" else "fr_FR-siwis-medium"))
        elif self.pitch and not self.ffmpeg:
            txt += "\n⚠ Tonalité Piper : installez ffmpeg"
        return txt

    def synth(self, text, wav_path, language=None):
        language = self.language_for(language)
        model, speaker, gender_fallback = self._pick(language)
        if not self.supports(language):
            self.note_missing_language(language)
        elif gender_fallback:
            self.note_missing_gender(language)
        raw = wav_path + ".piper.wav" if (self.pitch and self.ffmpeg) else wav_path
        cmd = [self.bin, "-m", model, "-f", raw]
        if speaker is not None:
            cmd += ["--speaker", str(speaker)]
        if self.flag and self.speed != 100:
            cmd += [self.flag, "%.3f" % (100.0 / self.speed)]
        res = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True,
                             timeout=600, cwd=os.path.dirname(self.bin) or None,
                             creationflags=NO_WINDOW)
        if not (os.path.isfile(raw) and os.path.getsize(raw) > 44):
            err = res.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise EngineError("Piper n'a pas produit d'audio (code %s).\n%s"
                              % (res.returncode, "\n".join(err)))
        if raw != wav_path:
            self._shift_pitch(raw, wav_path)

    def _shift_pitch(self, src, dst):
        """Décale la tonalité (±6 demi-tons max) sans changer la durée."""
        try:
            with wave.open(src, "rb") as w:
                rate = w.getframerate()
            factor = 2 ** (self.pitch * 0.6 / 12.0)
            af = "asetrate=%d,aresample=%d,atempo=%.5f" % (int(rate * factor), rate, 1.0 / factor)
            subprocess.run([self.ffmpeg, "-y", "-loglevel", "error", "-i", src, "-af", af, dst],
                           capture_output=True, timeout=120, creationflags=NO_WINDOW)
        except Exception as exc:
            log("pitch:", exc)
        if not (os.path.isfile(dst) and os.path.getsize(dst) > 44):
            shutil.copyfile(src, dst)
        try:
            os.remove(src)
        except OSError:
            pass


class SpdEngine(Engine):
    key, label = "spd", "Speech Dispatcher"

    def __init__(self, cfg, language=None):
        self.bin = _which("spd-say")
        if not self.bin:
            raise EngineError("spd-say introuvable : sudo apt install speech-dispatcher")
        Engine.__init__(self, cfg, language)

    def voices(self):
        """Voix du module (nom, locale, None) ; vide si le module ne dit rien."""
        return spd_voices(self.bin, self.cfg.get("spd_module"))

    def supports(self, language=None):
        voices = self.voices()
        return not voices or pick_voice(voices, self.language_for(language),
                                        self.gender)[0] is not None

    def _language_option(self, language):
        """La région si le module la connaît (pt-BR, en-GB), sinon la langue seule."""
        exact = language.replace("_", "-").lower()
        for voice in self.voices():
            if voice[1].replace("_", "-").lower() == exact:
                return voice[1]
        return primary_language(language)

    def describe(self):
        mod = self.cfg.get("spd_module") or "module par défaut"
        voice = self.cfg.get("spd_voice") or ("voix femme" if self.gender == "female" else "voix homme")
        return "Speech Dispatcher · %s · %s" % (mod, voice)

    def speak_command(self, text, language=None):
        language = self.language_for(language)
        if not self.supports(language):
            self.note_missing_language(language)
        cmd = [self.bin, "-w", "-r", str(max(-100, min(100, self.speed - 100))),
               "-p", str(self.pitch * 10), "-l", self._language_option(language)]
        if self.cfg.get("spd_module"):
            cmd += ["-o", self.cfg["spd_module"]]
        voice = self.cfg.get("spd_voice")
        names = [v[0] for v in self.voices()
                 if primary_language(v[1]) == primary_language(language)]
        if voice and (voice in names or not self.voices()):
            cmd += ["-y", voice]
        else:
            cmd += ["-t", "female1" if self.gender == "female" else "male1"]
        if text.startswith("-"):
            text = " " + text
        return cmd + [text], None

    def stop_all(self):
        try:
            subprocess.run([self.bin, "-C"], timeout=5, capture_output=True)
        except Exception:
            pass


def parse_spd_voices(output):
    """Voix listées par « spd-say -L » (colonnes NOM LANGUE VARIANTE)."""
    voices = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] != "NAME":
            voices.append((" ".join(fields[:-2]), fields[-2], None))
    return voices


_SPD_VOICES = {}


def spd_voices(program, module=""):
    """Voix d'un module de Speech Dispatcher ; résultat gardé 2 minutes."""
    module = module or ""
    cached = _SPD_VOICES.get(module)
    if cached is not None and time.time() - cached[0] < 120:
        return cached[1]
    cmd = [program, "-L"]
    if module:
        cmd += ["-o", module]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=5).stdout
        voices = parse_spd_voices(out.decode("utf-8", "replace"))
    except Exception as exc:
        log("spd-say -L:", exc)
        voices = []
    _SPD_VOICES[module] = (time.time(), voices)
    return voices


_MBROLA_FEMALE = {"mb-fr2", "mb-fr4"}
_MBROLA_MALE = {"mb-fr1", "mb-fr3", "mb-fr5", "mb-fr6", "mb-fr7"}


class EspeakEngine(Engine):
    key, label = "espeak", "eSpeak NG"
    can_export = True

    def __init__(self, cfg, language=None):
        self.bin = _which("espeak-ng", "espeak")
        if not self.bin:
            raise EngineError("eSpeak NG introuvable : " + install_hint(
                "espeak-ng", "espeak-ng", "installez eSpeak NG (github.com/espeak-ng/espeak-ng/releases)"))
        Engine.__init__(self, cfg, language)

    def voice(self, language=None):
        wanted = primary_language(self.language_for(language))
        base = (self.cfg.get("espeak_voice") or "").strip()
        low = base.lower()
        spoken = low[3:].rstrip("0123456789") if low.startswith("mb-") else low.split("+")[0]
        if primary_language(spoken) != wanted:
            base = low = wanted     # la voix réglée ne parle pas la langue du texte
        if low.startswith("mb-"):
            if self.gender == "female" and low in _MBROLA_MALE:
                return "mb-fr4"
            if self.gender == "male" and low in _MBROLA_FEMALE:
                return "mb-fr1"
            return base
        return base.split("+")[0] + ("+f3" if self.gender == "female" else "+m3")

    def describe(self):
        return "eSpeak NG · voix : " + self.voice()

    def _base(self, language=None):
        wpm = int(175 * self.speed / 100.0)
        pitch = max(0, min(99, 50 + self.pitch * 4))
        return [self.bin, "-v", self.voice(language), "-s", str(wpm), "-p", str(pitch)]

    def speak_command(self, text, language=None):
        return self._base(language) + ["--stdin"], text.encode("utf-8")

    def synth(self, text, wav_path, language=None):
        subprocess.run(self._base(language) + ["-w", wav_path, "--stdin"],
                       input=text.encode("utf-8"),
                       capture_output=True, timeout=600, creationflags=NO_WINDOW)
        if not os.path.isfile(wav_path):
            raise EngineError("eSpeak n'a pas produit d'audio.")


_SAPI_VOICES = []


_WINRT_SYNTH = "[Windows.Media.SpeechSynthesis.SpeechSynthesizer]"
_WINRT_LOAD = ("Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
               "$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer,"
               "Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]; "
               "$null = [Windows.Storage.Streams.DataReader,"
               "Windows.Storage.Streams,ContentType=WindowsRuntime]")


def parse_sapi_voices(output):
    """Lignes nom|locale|genre|source ; une voix présente deux fois garde sa première source.

    « Microsoft Paul » (API moderne) et « Microsoft Paul Desktop » (SAPI) sont la même voix.
    """
    voices = []
    seen = set()
    for line in output.lstrip("\ufeff").splitlines():
        fields = line.strip().split("|")
        if len(fields) != 4:
            continue
        name, culture, gender, source = fields
        same_voice = re.sub(r" Desktop$", "", name)
        if same_voice not in seen:
            seen.add(same_voice)
            voices.append((name, culture,
                           gender.lower() if gender in ("Female", "Male") else None, source))
    return voices


def sapi_voices():
    """Toutes les voix Windows : (nom, locale, genre, source), lues une seule fois.

    System.Speech ne voit qu'une partie des voix : celles ajoutées depuis les Paramètres
    de Windows ne sont visibles que par l'API moderne, utilisée en priorité.
    """
    if not _SAPI_VOICES:
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  "try { " + _WINRT_LOAD + "; foreach ($v in " + _WINRT_SYNTH + "::AllVoices) { "
                  "$v.DisplayName + '|' + $v.Language + '|' + $v.Gender + '|winrt' } } catch {}; "
                  "Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "foreach ($v in $s.GetInstalledVoices()) { if ($v.Enabled) { $i = $v.VoiceInfo; "
                  "$i.Name + '|' + $i.Culture.Name + '|' + $i.Gender + '|sapi' } }")
        try:
            out = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, timeout=30, creationflags=NO_WINDOW).stdout
            _SAPI_VOICES.extend(parse_sapi_voices(out.decode("utf-8", "replace")))
        except Exception as exc:
            log("voix SAPI:", exc)
    return _SAPI_VOICES


class SapiEngine(Engine):
    key, label = "sapi", "Windows SAPI"
    can_export = True

    def __init__(self, cfg, language=None):
        if not IS_WIN:
            raise EngineError("Windows SAPI n'existe que sous Windows.")
        Engine.__init__(self, cfg, language)

    def supports(self, language=None):
        voices = sapi_voices()
        return not voices or pick_voice(voices, self.language_for(language),
                                        self.gender)[0] is not None

    def _voice(self, language):
        """Nom de la voix pour cette langue ; None laisse la voix par défaut du système."""
        voices = sapi_voices()
        preferred = self.cfg.get("sapi_voice") or ""
        if preferred not in [voice[0] for voice in voices]:
            preferred = re.sub(r" Desktop$", "", preferred)     # nom enregistré par la 1.2
        name, gender_ok = pick_voice(voices, language, self.gender, preferred or None)
        if name is None:
            self.note_missing_language(language)
        elif not gender_ok:
            self.note_missing_gender(language)
        return name

    def describe(self):
        return "Windows SAPI · " + (self._voice(self.language_for()) or "voix par défaut")

    def _winrt_script(self, voice, text_file, wav_path):
        """Synthèse par l'API moderne : la voix, le débit et la tonalité sont tous respectés."""
        await_op = ("function Await($op, $type) { "
                    "$task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); "
                    "$null = $task.Wait(-1); $task.Result }")
        as_task = ("$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | "
                   "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and "
                   "$_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]")
        text = text_file.replace("'", "''")
        lines = ["$ErrorActionPreference = 'Stop'", _WINRT_LOAD, as_task, await_op,
                 "$t = [IO.File]::ReadAllText('%s', [Text.Encoding]::UTF8)" % text,
                 "Remove-Item -LiteralPath '%s' -ErrorAction SilentlyContinue" % text,
                 "$s = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer",
                 "foreach ($v in %s::AllVoices) { if ($v.DisplayName -eq '%s') { $s.Voice = $v } }"
                 % (_WINRT_SYNTH, voice.replace("'", "''")),
                 "$s.Options.SpeakingRate = %.2f" % (self.speed / 100.0),
                 "$s.Options.AudioPitch = %.2f" % (1 + self.pitch * 0.05),
                 "$st = Await ($s.SynthesizeTextToStreamAsync($t)) "
                 "([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])",
                 "$size = [uint32]$st.Size",
                 "$r = New-Object Windows.Storage.Streams.DataReader($st.GetInputStreamAt(0))",
                 "$null = Await ($r.LoadAsync($size)) ([uint32])",
                 "$b = New-Object byte[] $size",
                 "$r.ReadBytes($b)"]
        if wav_path:
            lines.append("[IO.File]::WriteAllBytes('%s', $b)" % wav_path.replace("'", "''"))
        else:
            lines.append("(New-Object Media.SoundPlayer (New-Object IO.MemoryStream(,$b)))"
                         ".PlaySync()")
        return "; ".join(lines)

    def _script(self, text_file, wav_path=None, language=None):
        voice = self._voice(self.language_for(language))
        if (voice, "winrt") in [(v[0], v[3]) for v in sapi_voices()]:
            return self._winrt_script(voice, text_file, wav_path)
        rate = max(-10, min(10, int(round((self.speed - 100) / 10.0))))
        lines = ["$ErrorActionPreference = 'Stop'",
                 "Add-Type -AssemblyName System.Speech",
                 "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
                 "$s.Rate = %d" % rate]
        if voice:
            lines.append("$s.SelectVoice('%s')" % voice.replace("'", "''"))
        if wav_path:
            lines.append("$s.SetOutputToWaveFile('%s')" % wav_path.replace("'", "''"))
        lines.append("$t = [Security.SecurityElement]::Escape([IO.File]::ReadAllText('%s', "
                     "[Text.Encoding]::UTF8))" % text_file.replace("'", "''"))
        lines.append("Remove-Item -LiteralPath '%s' -ErrorAction SilentlyContinue"
                     % text_file.replace("'", "''"))
        # xml:lang doit être celui de la voix choisie : SAPI remplace sinon cette voix
        # par la première voix de la langue indiquée, quel que soit son genre.
        lines.append("$x = '<speak version=\"1.0\" xmlns=\"http://www.w3.org/2001/10/synthesis\" "
                     "xml:lang=\"' + $s.Voice.Culture.Name + '\"><prosody pitch=\"%+d%%\">' + $t + "
                     "'</prosody></speak>'" % (self.pitch * 5))
        lines.append("$s.SpeakSsml($x)")
        lines.append("$s.Dispose()")
        return "; ".join(lines)

    def _text_file(self, text):
        fd, path = tempfile.mkstemp(prefix="lvlo_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def speak_command(self, text, language=None):
        return ([powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", self._script(self._text_file(text), None, language)], None)

    def synth(self, text, wav_path, language=None):
        subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                        "Bypass", "-Command",
                        self._script(self._text_file(text), wav_path, language)],
                       capture_output=True, timeout=600, creationflags=NO_WINDOW)
        if not os.path.isfile(wav_path):
            raise EngineError("SAPI n'a pas produit d'audio.")


_MAC_FEMALE = {"amélie", "amelie", "audrey", "aurélie", "aurelie", "marie", "virginie",
               "samantha", "karen", "moira", "tessa", "anna", "alice", "monica", "paulina"}
_MAC_MALE = {"thomas", "jacques", "nicolas", "daniel", "alex", "fred", "tom", "aaron",
             "arthur", "reed", "rocko", "eddy", "grandpa", "jorge", "luca", "markus"}


def mac_gender(name):
    base = name.split(" (")[0].lower()
    return "female" if base in _MAC_FEMALE else "male" if base in _MAC_MALE else None


_MAC_ATTRIBUTES_SCRIPT = """ObjC.import('AppKit');
var voices = $.NSSpeechSynthesizer.availableVoices, out = [];
for (var i = 0; i < voices.count; i++) {
  var a = $.NSSpeechSynthesizer.attributesForVoice(voices.objectAtIndex(i));
  out.push(ObjC.unwrap(a.objectForKey('VoiceName')) + '|'
           + ObjC.unwrap(a.objectForKey('VoiceGender')));
}
out.join('\\n')"""


def parse_mac_genders(output):
    """Lignes nom|VoiceGenderFemale : dictionnaire nom → female, male ou None."""
    genders = {}
    for line in output.splitlines():
        name, _, gender = line.strip().rpartition("|")
        genders[name] = {"VoiceGenderFemale": "female", "VoiceGenderMale": "male"}.get(gender)
    return genders


def mac_system_genders():
    """Genre déclaré par macOS pour chaque voix, quelle que soit sa langue."""
    osascript = _which("osascript")
    if not osascript:
        return {}
    try:
        out = subprocess.run([osascript, "-l", "JavaScript", "-e", _MAC_ATTRIBUTES_SCRIPT],
                             capture_output=True, timeout=15).stdout
        return parse_mac_genders(out.decode("utf-8", "replace"))
    except Exception as exc:
        log("genres macOS:", exc)
        return {}


_MAC_VOICES = []


def mac_voices():
    """Voix installées sous macOS : liste de (nom, locale, genre), lue une seule fois."""
    if not _MAC_VOICES:
        _MAC_VOICES.extend(_read_mac_voices())
    return _MAC_VOICES


def _read_mac_voices():
    say = _which("say")
    if not say:
        return []
    genders = mac_system_genders()
    try:
        out = subprocess.run([say, "-v", "?"], capture_output=True, timeout=15).stdout
    except Exception as exc:
        log("say -v ?:", exc)
        return []
    voices = []
    for line in out.decode("utf-8", "replace").splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9]+)\s+#", line)
        if m:
            name = m.group(1).strip()
            voices.append((name, m.group(2), genders.get(name) or mac_gender(name)))
    return voices


class MacSayEngine(Engine):
    key, label = "say", "macOS"
    can_export = True

    def __init__(self, cfg, language=None):
        if not IS_MAC:
            raise EngineError("La synthèse « say » n'existe que sous macOS.")
        self.bin = _which("say")
        if not self.bin:
            raise EngineError("Commande « say » introuvable.")
        self.voices = mac_voices()
        Engine.__init__(self, cfg, language)

    def update(self, cfg):
        Engine.update(self, cfg)
        self.voice = self._pick(self.language_for()) or ""

    def supports(self, language=None):
        return not self.voices or pick_voice(self.voices, self.language_for(language),
                                             self.gender)[0] is not None

    def _pick(self, language):
        explicit = (self.cfg.get("mac_voice") or "").strip()
        if not self.voices:
            return explicit
        name, gender_ok = pick_voice(self.voices, language, self.gender, explicit or None)
        if name is None:
            self.note_missing_language(language)
        elif not gender_ok:
            self.note_missing_gender(language)
        return name

    def signature(self):
        return Engine.signature(self) + (self.voice,)

    def describe(self):
        return "macOS · voix : " + (self.voice or "voix par défaut du système")

    def _base(self, language=None):
        cmd = [self.bin, "-r", str(int(180 * self.speed / 100.0))]
        voice = self._pick(self.language_for(language))
        if voice:
            cmd += ["-v", voice]
        return cmd

    def _text(self, text):
        # [[pbas]] : hauteur de base (ignorée sans erreur par les voix qui ne la gèrent pas)
        return ("[[pbas %+d]] " % (self.pitch * 2) if self.pitch else "") + text

    def speak_command(self, text, language=None):
        return self._base(language) + ["-f", "-"], self._text(text).encode("utf-8")

    def synth(self, text, wav_path, language=None):
        subprocess.run(self._base(language) + ["-o", wav_path, "--file-format=WAVE",
                                       "--data-format=LEI16@22050", "-f", "-"],
                       input=self._text(text).encode("utf-8"), capture_output=True, timeout=600)
        if not (os.path.isfile(wav_path) and os.path.getsize(wav_path) > 44):
            raise EngineError("« say » n'a pas produit d'audio.")


ENGINES = {"piper": PiperEngine, "spd": SpdEngine, "espeak": EspeakEngine, "sapi": SapiEngine,
           "say": MacSayEngine}


def resolve_engine(cfg, need_export=False, language=None, languages=None):
    """En mode automatique, le premier moteur qui a une voix pour toutes les langues du
    texte ; à défaut, le premier moteur disponible."""
    choice = cfg.get("engine") or "auto"
    if choice not in ENGINE_KEYS:       # réglage venu d'un autre système
        choice = "auto"
    if choice != "auto":
        engine = ENGINES[choice](cfg, language)
        if need_export and not engine.can_export:
            raise EngineError("%s ne peut pas enregistrer de fichier audio.\n"
                              "Choisissez Piper (recommandé) ou eSpeak NG dans les paramètres."
                              % engine.label)
        return engine
    order = [k for k in ENGINE_KEYS if k != "auto"]
    errors = []
    fallback = None
    for key in order:
        if need_export and not ENGINES[key].can_export:
            continue
        try:
            engine = ENGINES[key](cfg, language)
        except EngineError as exc:
            errors.append("• " + str(exc))
            continue
        if all(engine.supports(wanted) for wanted in languages or [language]):
            return engine
        log("moteur sans voix pour la langue:", key, language)
        fallback = fallback or engine
    if fallback is not None:
        return fallback
    raise EngineError("Aucun moteur de synthèse vocale utilisable.\n\n" + "\n".join(errors))


# --------------------------------------------------------------------------
# Outils UNO
# --------------------------------------------------------------------------

class _Callback(unohelper.Base, XCallback):
    def __init__(self, fn):
        self.fn = fn

    def notify(self, data):
        try:
            self.fn()
        except Exception:
            log("callback:", traceback.format_exc())


def run_in_main(ctx, fn):
    """Exécute fn dans le fil principal de LibreOffice (interface graphique)."""
    try:
        cb = ctx.ServiceManager.createInstanceWithContext("com.sun.star.awt.AsyncCallback", ctx)
        cb.addCallback(_Callback(fn), None)
    except Exception:
        fn()


def desktop(ctx):
    return ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)


def msgbox(ctx, message, kind="infobox", title=TITLE):
    log("msgbox[%s]:" % kind, message.replace("\n", " | "))
    if HEADLESS:
        return
    try:
        tk = ctx.ServiceManager.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
        parent = None
        try:
            parent = desktop(ctx).getCurrentFrame().getContainerWindow()
        except Exception:
            pass
        box = tk.createMessageBox(parent, uno.Enum("com.sun.star.awt.MessageBoxType", kind.upper()),
                                  1, title, message)
        box.execute()
        box.dispose()
    except Exception:
        log("msgbox:", traceback.format_exc())


def flash_status(ctx, text, secs=2.5):
    """Message bref dans la barre d'état (retour visuel des raccourcis)."""
    log("statut:", text)
    try:
        ind = desktop(ctx).getCurrentFrame().createStatusIndicator()
        ind.start("Lecture vocale — " + text, 0)
        threading.Timer(secs, lambda: run_in_main(ctx, ind.end)).start()
    except Exception:
        pass


def file_dialog(ctx, save=False, filters=(), default_name="", title=""):
    fp = ctx.ServiceManager.createInstanceWithContext("com.sun.star.ui.dialogs.FilePicker", ctx)
    td = "com.sun.star.ui.dialogs.TemplateDescription."
    mode = uno.getConstantByName(td + ("FILESAVE_AUTOEXTENSION" if save else "FILEOPEN_SIMPLE"))
    fp.initialize((mode,))
    if title:
        fp.setTitle(title)
    for label, pattern in filters:
        fp.appendFilter(label, pattern)
    if filters:
        fp.setCurrentFilter(filters[0][0])
    if default_name:
        fp.setDefaultName(default_name)
    if fp.execute() != 1:
        return None, None
    files = fp.getSelectedFiles() if hasattr(fp, "getSelectedFiles") else fp.getFiles()
    if not files:
        return None, None
    return uno.fileUrlToSystemPath(files[0]), fp.getCurrentFilter()


class StatusBar(object):
    def __init__(self, ctx, ctrl, text, total):
        self.ctx = ctx
        self.ind = None
        try:
            self.ind = ctrl.getFrame().createStatusIndicator()
            self.ind.start(text, max(1, total))
        except Exception:
            self.ind = None

    def value(self, v, text=None):
        if self.ind is None:
            return
        ind = self.ind

        def upd():
            if text:
                ind.setText(text)
            ind.setValue(v)
        run_in_main(self.ctx, upd)

    def end(self):
        if self.ind is not None:
            run_in_main(self.ctx, self.ind.end)


# --------------------------------------------------------------------------
# Extraction du texte à lire (segments = phrases)
# --------------------------------------------------------------------------

_SPACES = re.compile(r"[ \t  ]+")
_INVISIBLE = re.compile(r"[­​‌‍﻿\x00-\x08\x0b\x0c\x0e-\x1f]")
_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+(?=\S)")


def clean(text):
    text = _INVISIBLE.sub("", text.replace("\r", "\n").replace("\t", " "))
    text = text.replace("\n", " ")
    text = _SPACES.sub(" ", text).strip()
    return text if re.search(r"\w", text) else ""


def split_sentences(text):
    out = []
    for para in re.split(r"[\r\n]+", text):
        for sent in _SENTENCE_END.split(para):
            sent = clean(sent)
            if sent:
                out.append(sent)
    return out


def _writer_sentences(text, start, end, limit=20000):
    out = []
    cur = text.createTextCursorByRange(start)
    for _ in range(limit):
        sent = text.createTextCursorByRange(cur)
        sent.collapseToStart()
        try:
            sent.gotoEndOfSentence(True)
        except Exception:
            sent.gotoEndOfParagraph(True)
        try:
            if text.compareRegionEnds(sent, end) < 0:
                sent.gotoRange(end, True)
        except Exception:
            pass
        s = clean(sent.getString())
        if s:
            out.append((s, sent))
        prev = text.createTextCursorByRange(cur)
        moved = cur.gotoNextSentence(False)
        if moved:
            try:
                if text.compareRegionStarts(prev, cur) != 1:
                    moved = cur.gotoNextParagraph(False)
            except Exception:
                pass
        if not moved:
            break
        try:
            if text.compareRegionStarts(cur, end) <= 0:
                break
        except Exception:
            break
    return out


def _writer_segments(doc, ctrl, mode):
    segs = []
    if mode != "all":
        sel = ctrl.getSelection()
        try:
            for i in range(sel.getCount()):
                rng = sel.getByIndex(i)
                if rng.getString().strip():
                    segs += _writer_sentences(rng.getText(), rng.getStart(), rng.getEnd())
        except Exception:
            segs = []
        if segs:
            return segs
    if mode == "cursor":
        vc = ctrl.getViewCursor()
        try:
            text = vc.getText()
            segs = _writer_sentences(text, vc.getStart(), text.getEnd())
        except Exception:
            segs = []
        if segs:
            return segs
    text = doc.getText()
    return _writer_sentences(text, text.getStart(), text.getEnd())


def _is_single_cell(rng):
    a = rng.getRangeAddress()
    return a.StartColumn == a.EndColumn and a.StartRow == a.EndRow


def _calc_segments(doc, ctrl, mode):
    sel = doc.getCurrentSelection()
    ranges = []
    if mode != "all" and sel is not None:
        if sel.supportsService("com.sun.star.sheet.SheetCellRanges"):
            ranges = [sel.getByIndex(i) for i in range(sel.getCount())]
        elif sel.supportsService("com.sun.star.sheet.SheetCellRange"):
            ranges = [sel]
    if not ranges or (len(ranges) == 1 and _is_single_cell(ranges[0])):
        first_row = ranges[0].getRangeAddress().StartRow if (ranges and mode == "cursor") else 0
        sheet = ctrl.getActiveSheet()
        cur = sheet.createCursor()
        cur.gotoEndOfUsedArea(False)
        addr = cur.getRangeAddress()
        first_row = min(first_row, addr.EndRow)
        ranges = [sheet.getCellRangeByPosition(0, first_row, addr.EndColumn, addr.EndRow)]
    segs = []
    budget = 20000
    for rng in ranges:
        addr = rng.getRangeAddress()
        cols = addr.EndColumn - addr.StartColumn + 1
        rows = addr.EndRow - addr.StartRow + 1
        for r in range(rows):
            vals = []
            for c in range(cols):
                budget -= 1
                if budget < 0:
                    return segs
                v = clean(rng.getCellByPosition(c, r).getString())
                if v:
                    vals.append(v)
            if vals:
                segs.append((" ; ".join(vals) + ".", rng.getCellRangeByPosition(0, r, cols - 1, r)))
    return segs


def _draw_segments(doc, ctrl, mode):
    shapes = []
    if mode != "all":
        try:
            sel = ctrl.getSelection()
            if sel is not None and hasattr(sel, "getCount"):
                shapes = [sel.getByIndex(i) for i in range(sel.getCount())]
        except Exception:
            shapes = []
    if not shapes:
        page = ctrl.getCurrentPage()
        shapes = [page.getByIndex(i) for i in range(page.getCount())]
    segs = []
    for shape in shapes:
        try:
            txt = shape.getString()
        except Exception:
            continue
        for s in split_sentences(txt):
            segs.append((s, shape))
    return segs


def locale_language(obj, prop="CharLocale"):
    """Langue d'un curseur, d'une plage, d'une forme ou d'un document ; vide si inconnue."""
    try:
        locale = obj.getPropertyValue(prop)
        if locale.Language == "qlt":        # étiquette BCP 47 sans code ISO
            return locale.Variant
        if locale.Language in ("", "zxx", "und"):
            return ""
        return "%s-%s" % (locale.Language, locale.Country) if locale.Country else locale.Language
    except Exception:
        return ""


def segment_language(segment):
    return segment[2] if len(segment) > 2 else None


def collect_segments(doc, ctrl, mode="cursor"):
    """Segments (texte, cible à surligner, langue) ; voir _raw_segments pour mode."""
    return [(text, target, text_language(text, target, doc))
            for text, target in _raw_segments(doc, ctrl, mode)]


def text_language(text, target, doc):
    """Langue d'un texte : celle déclarée dans LibreOffice, si l'écriture du texte la permet.

    LibreOffice range la langue dans trois propriétés selon l'écriture. Un texte russe
    laissé en langue « français », ou un texte arabe dans un document dont la langue
    complexe par défaut est l'hindi, doit quand même être lu par une voix qui sait le lire.
    """
    script = text_script(text)
    prop = script[2] if script else "CharLocale"
    declared = locale_language(target, prop) or locale_language(doc, prop)
    if script and primary_language(declared) not in script[3]:
        return script[3][0]
    return declared


def _raw_segments(doc, ctrl, mode):
    """mode : 'cursor' (sélection sinon depuis le curseur), 'all', 'export' (sélection sinon tout)."""
    if doc.supportsService("com.sun.star.text.TextDocument"):
        return _writer_segments(doc, ctrl, mode)
    if doc.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
        return _calc_segments(doc, ctrl, mode)
    if (doc.supportsService("com.sun.star.presentation.PresentationDocument")
            or doc.supportsService("com.sun.star.drawing.DrawingDocument")):
        return _draw_segments(doc, ctrl, mode)
    raise EngineError("Ce type de document n'est pas pris en charge.")


# --------------------------------------------------------------------------
# Lecture en arrière-plan
# --------------------------------------------------------------------------

DONE, STOPPED, RESTART = "done", "stopped", "restart"


class Reader(threading.Thread):
    def __init__(self, ctx, engine, segments, start=0, ctrl=None, highlight=True):
        threading.Thread.__init__(self, name="LectureVocale")
        self.daemon = True
        self.ctx = ctx
        self.engine = engine
        self.segments = segments
        self.index = start
        self.ctrl = ctrl
        self.highlight = highlight and ctrl is not None
        self.stop_evt = threading.Event()
        self.restart_evt = threading.Event()
        self.proc = None
        self.lock = threading.Lock()
        self.finished_ok = False

    # -- contrôle depuis l'extérieur ------------------------------------
    def _kill_current(self):
        with self.lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def stop(self):
        self.stop_evt.set()
        self._kill_current()
        self.engine.stop_all()

    def request_restart(self):
        """Nouveaux réglages : la phrase en cours est relue avec eux."""
        self.restart_evt.set()
        self._kill_current()

    def jump(self, delta):
        with self.lock:
            self.index = max(0, min(len(self.segments) - 1, self.index + delta))
        self.request_restart()

    @property
    def stopped(self):
        return self.stop_evt.is_set()

    # -- utilitaires ------------------------------------------------------
    def _run(self, cmd, stdin_bytes=None):
        with self.lock:
            if self.stopped:
                return STOPPED
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_bytes else subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=NO_WINDOW)
            proc = self.proc
        if stdin_bytes:
            try:
                proc.stdin.write(stdin_bytes)
                proc.stdin.close()
            except Exception:
                pass
        status = DONE
        while proc.poll() is None:
            if self.stop_evt.wait(0.04):
                status = STOPPED
            elif self.restart_evt.is_set():
                status = RESTART
            else:
                continue
            try:
                proc.kill()
            except Exception:
                pass
            if status == RESTART:
                self.engine.stop_all()
            break
        proc.wait()
        with self.lock:
            self.proc = None
        if self.stopped:
            return STOPPED
        if self.restart_evt.is_set():
            return RESTART
        if proc.returncode not in (0, None):
            raise EngineError("La commande « %s » a échoué (code %s)."
                              % (os.path.basename(cmd[0]), proc.returncode))
        return status

    def _advance(self, i):
        with self.lock:
            if self.index == i:
                self.index = i + 1

    def _refresh(self):
        self.restart_evt.clear()
        self.engine.update(load_config())

    def _report(self):
        message = self.engine.take_warning()
        if message:
            run_in_main(self.ctx, lambda: msgbox(self.ctx, message, "warningbox"))

    def _select(self, i):
        if not self.highlight:
            return
        target = self.segments[i][1]
        if target is None:
            return
        ctrl = self.ctrl

        def do():
            if not self.stopped:
                ctrl.select(target)
        run_in_main(self.ctx, do)

    # -- boucle principale ------------------------------------------------
    def run(self):
        log("lecture: moteur=%s segments=%d départ=%d"
            % (self.engine.key, len(self.segments), self.index))
        log("langues:", sorted({str(segment_language(s)) for s in self.segments}),
            "·", self.engine.describe().replace("\n", " "))
        try:
            if self.engine.file_based:
                self._run_files()
            else:
                self._run_direct()
            self.finished_ok = not self.stopped
        except Exception as exc:
            log("lecture:", traceback.format_exc())
            if not self.stopped:
                message = str(exc) or exc.__class__.__name__
                run_in_main(self.ctx, lambda: msgbox(self.ctx, message, "errorbox"))
        finally:
            _on_reader_end(self)

    def _run_direct(self):
        n = len(self.segments)
        while not self.stopped:
            if self.restart_evt.is_set():
                self._refresh()
            i = self.index
            if i >= n:
                break
            self._select(i)
            segment = self.segments[i]
            cmd, data = self.engine.speak_command(segment[0], segment_language(segment))
            self._report()
            status = self._run(cmd, data)
            if status == DONE:
                self._advance(i)
            elif status == STOPPED:
                break

    def _run_files(self):
        n = len(self.segments)
        tmp = tempfile.mkdtemp(prefix="lvlo_")
        jobs = {}

        def job(i):
            """Synthèse (éventuellement anticipée) de la phrase i avec les réglages actuels."""
            sig = self.engine.signature()
            current = jobs.get(i)
            if current is not None and current[2] == sig:
                return current
            res = {}
            path = os.path.join(tmp, "%06d_%d.wav" % (i, next(_COUNTER)))
            text = self.segments[i][0]
            language = segment_language(self.segments[i])
            engine = self.engine

            def work():
                try:
                    engine.synth(text, path, language)
                    res["path"] = path
                except Exception as exc:
                    res["err"] = exc
            t = threading.Thread(target=work, daemon=True)
            t.start()
            jobs[i] = (t, res, sig)
            return jobs[i]

        try:
            while not self.stopped:
                if self.restart_evt.is_set():
                    self._refresh()
                i = self.index
                if i >= n:
                    break
                t, res, _ = job(i)
                while t.is_alive() and not self.stop_evt.wait(0.03) \
                        and not self.restart_evt.is_set():
                    pass
                if self.stopped:
                    break
                if self.restart_evt.is_set() or t.is_alive():
                    continue
                if "err" in res:
                    raise res["err"]
                self._report()
                if i + 1 < n:
                    job(i + 1)
                self._select(i)
                status = self._run(play_command(res["path"]))
                if status == DONE:
                    jobs.pop(i, None)
                    try:
                        os.remove(res["path"])
                    except OSError:
                        pass
                    self._advance(i)
                elif status == STOPPED:
                    break
        finally:
            def cleanup():
                for t, _, _ in list(jobs.values()):
                    t.join(60)
                shutil.rmtree(tmp, ignore_errors=True)
            threading.Thread(target=cleanup, daemon=True).start()


class _State(object):
    reader = None
    paused = None     # dict : segments, index, doc, ctrl


STATE = _State()
STATE_LOCK = threading.RLock()


def _on_reader_end(reader):
    with STATE_LOCK:
        if STATE.reader is reader:
            STATE.reader = None
        if not reader.stopped and STATE.paused and \
                STATE.paused.get("segments") is reader.segments:
            STATE.paused = None
    log("lecture terminée: index=%d/%d ok=%s" % (reader.index, len(reader.segments),
                                               reader.finished_ok))


def current_reader():
    with STATE_LOCK:
        return STATE.reader


def stop_reading(clear_pause=True):
    with STATE_LOCK:
        reader = STATE.reader
        STATE.reader = None
        if clear_pause:
            STATE.paused = None
    if reader is not None:
        reader.stop()
        reader.join(3)
    return reader


def start_reading(ctx, doc, ctrl, segments, start=0, cfg=None):
    cfg = cfg or load_config()
    languages = sorted({segment_language(segment) or "" for segment in segments})
    engine = resolve_engine(cfg, language=segment_language(segments[start]),
                            languages=languages)
    stop_reading()
    reader = Reader(ctx, engine, segments, start, ctrl, bool(cfg.get("highlight", True)))
    with STATE_LOCK:
        STATE.reader = reader
        STATE.paused = {"segments": segments, "index": start, "doc": doc, "ctrl": ctrl}
    reader.start()
    return reader


def pause_reading():
    reader = current_reader()
    if reader is None:
        return False
    stop_reading(clear_pause=False)
    with STATE_LOCK:
        STATE.paused = {"segments": reader.segments, "index": reader.index,
                        "doc": STATE.paused["doc"] if STATE.paused else None,
                        "ctrl": reader.ctrl}
    log("pause à l'index", reader.index)
    return True


def jump_reading(ctx, delta):
    reader = current_reader()
    if reader is not None:
        reader.jump(delta)
        return True
    with STATE_LOCK:
        paused = STATE.paused
        if not paused:
            return False
        paused["index"] = max(0, min(len(paused["segments"]) - 1, paused["index"] + delta))
        target = paused["segments"][paused["index"]][1]
        ctrl = paused.get("ctrl")
    if target is not None and ctrl is not None:
        try:
            ctrl.select(target)
        except Exception:
            pass
    return True


def reading_status():
    """(état, index, total) avec état ∈ {'lecture', 'pause', 'prêt'}."""
    with STATE_LOCK:
        reader, paused = STATE.reader, STATE.paused
    if reader is not None and reader.is_alive():
        return "lecture", reader.index, len(reader.segments)
    if paused and paused["index"] < len(paused["segments"]):
        return "pause", paused["index"], len(paused["segments"])
    return "prêt", 0, 0


# --------------------------------------------------------------------------
# Réglages rapides (pendant la lecture)
# --------------------------------------------------------------------------

def apply_live(ctx, announce=True, **changes):
    cfg = update_config(**changes)
    reader = current_reader()
    if reader is not None:
        reader.request_restart()
    if ReadingPanel.instance is not None:
        panel = ReadingPanel.instance
        run_in_main(ctx, panel.sync)
    if announce:
        flash_status(ctx, "débit %d %% · tonalité %+d · voix %s"
                     % (cfg["speed"], cfg["pitch"], "femme" if cfg["gender"] == "female" else "homme"))
    return cfg


def quick_setting(ctx, command):
    cfg = load_config()
    if command == "faster":
        return apply_live(ctx, speed=min(SPEED_MAX, cfg["speed"] + SPEED_STEP))
    if command == "slower":
        return apply_live(ctx, speed=max(SPEED_MIN, cfg["speed"] - SPEED_STEP))
    if command == "pitchup":
        return apply_live(ctx, pitch=min(PITCH_MAX, cfg["pitch"] + 1))
    if command == "pitchdown":
        return apply_live(ctx, pitch=max(PITCH_MIN, cfg["pitch"] - 1))
    if command in ("female", "male"):
        return apply_live(ctx, gender=command)
    if command == "resetvoice":
        return apply_live(ctx, speed=100, pitch=0)
    return None


# --------------------------------------------------------------------------
# Export audio
# --------------------------------------------------------------------------

def concat_wavs(paths, out_path, pause_ms=200):
    params = None
    with wave.open(out_path, "wb") as out:
        for k, path in enumerate(paths):
            with wave.open(path, "rb") as w:
                p = w.getparams()
                if params is None:
                    params = p
                    out.setnchannels(p.nchannels)
                    out.setsampwidth(p.sampwidth)
                    out.setframerate(p.framerate)
                elif (p.nchannels, p.sampwidth, p.framerate) != \
                        (params.nchannels, params.sampwidth, params.framerate):
                    raise EngineError("Formats audio incompatibles entre les phrases.")
                if k and pause_ms:
                    n = int(params.framerate * pause_ms / 1000.0)
                    out.writeframes(b"\x00" * n * params.sampwidth * params.nchannels)
                out.writeframes(w.readframes(w.getnframes()))


def _wav_seconds(path):
    try:
        with wave.open(path, "rb") as w:
            return int(w.getnframes() / float(w.getframerate()))
    except Exception:
        return 0


def export_audio(ctx, doc, ctrl):
    cfg = load_config()
    segments = collect_segments(doc, ctrl, "export")
    if not segments:
        msgbox(ctx, "Rien à enregistrer : le document (ou la sélection) est vide.", "warningbox")
        return
    # Une seule langue, donc une seule voix : les WAV à assembler doivent avoir le même format.
    language = segment_language(segments[0])
    engine = resolve_engine(cfg, need_export=True, language=language)
    filters = [("Audio WAV (*.wav)", "*.wav")]
    ffmpeg = _which("ffmpeg")
    if ffmpeg:
        filters += [("Audio MP3 (*.mp3)", "*.mp3"), ("Audio OGG Vorbis (*.ogg)", "*.ogg")]
    title = "lecture"
    try:
        title = os.path.splitext(doc.getTitle())[0] or title
    except Exception:
        pass
    out_path = os.environ.get("LECTURE_VOCALE_EXPORT_PATH") if HEADLESS else None
    chosen = filters[0][0]
    if not out_path:
        out_path, chosen = file_dialog(ctx, True, filters, title + ".wav",
                                       "Enregistrer la lecture audio")
        if not out_path:
            return
    ext = os.path.splitext(out_path)[1].lower()
    if ext not in (".wav", ".mp3", ".ogg"):
        ext = {"*.mp3": ".mp3", "*.ogg": ".ogg"}.get(dict(filters).get(chosen or "", "*.wav"), ".wav")
        out_path += ext
    if ext != ".wav" and not ffmpeg:
        raise EngineError("ffmpeg est nécessaire pour le MP3/OGG : " + install_hint(
            "ffmpeg", "ffmpeg", "winget install ffmpeg"))

    status = StatusBar(ctx, ctrl, "Lecture vocale : synthèse audio…", len(segments))

    def work():
        tmp = tempfile.mkdtemp(prefix="lvlo_exp_")
        try:
            paths = []
            for i, segment in enumerate(segments):
                p = os.path.join(tmp, "%06d.wav" % i)
                engine.synth(segment[0], p, language)
                paths.append(p)
                status.value(i + 1, "Lecture vocale : phrase %d / %d" % (i + 1, len(segments)))
            wav_path = out_path if ext == ".wav" else os.path.join(tmp, "complet.wav")
            concat_wavs(paths, wav_path, int(cfg.get("pause_ms", 200)))
            if ext != ".wav":
                codec = ["-codec:a", "libmp3lame", "-q:a", "3"] if ext == ".mp3" \
                    else ["-codec:a", "libvorbis", "-q:a", "5"]
                res = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", wav_path]
                                     + codec + [out_path], capture_output=True,
                                     creationflags=NO_WINDOW)
                if res.returncode != 0:
                    raise EngineError("Conversion ffmpeg échouée :\n"
                                      + res.stderr.decode("utf-8", "replace")[-400:])
            secs = _wav_seconds(wav_path)
            log("export ok:", out_path, "%ds" % secs)
            msg = ("Fichier audio enregistré (%d phrases, %d min %02d s) :\n%s"
                   % (len(segments), secs // 60, secs % 60, out_path))
            warning = engine.take_warning()
            if warning:
                msg += "\n\n" + warning
            run_in_main(ctx, lambda: msgbox(ctx, msg))
        except Exception as exc:
            log("export:", traceback.format_exc())
            message = "Échec de l'export audio :\n" + str(exc)
            run_in_main(ctx, lambda: msgbox(ctx, message, "errorbox"))
        finally:
            status.end()
            shutil.rmtree(tmp, ignore_errors=True)

    t = threading.Thread(target=work, name="LectureVocaleExport", daemon=True)
    t.start()
    return t


# --------------------------------------------------------------------------
# Écouteurs UNO génériques
# --------------------------------------------------------------------------

class _ActionListener(unohelper.Base, XActionListener):
    def __init__(self, handler):
        self.handler = handler

    def actionPerformed(self, event):
        try:
            self.handler(event.ActionCommand)
        except Exception:
            log("action:", traceback.format_exc())

    def disposing(self, event):
        pass


class _AdjustListener(unohelper.Base, XAdjustmentListener):
    def __init__(self, handler):
        self.handler = handler

    def adjustmentValueChanged(self, event):
        try:
            self.handler(event.Source.getModel().Name, event.Value)
        except Exception:
            log("adjust:", traceback.format_exc())

    def disposing(self, event):
        pass


class _ItemListener(unohelper.Base, XItemListener):
    def __init__(self, handler):
        self.handler = handler

    def itemStateChanged(self, event):
        try:
            self.handler(event.Source.getModel().Name)
        except Exception:
            log("item:", traceback.format_exc())

    def disposing(self, event):
        pass


class _CloseListener(unohelper.Base, XTopWindowListener):
    def __init__(self, handler):
        self.handler = handler

    def windowClosing(self, event):
        self.handler()

    def windowOpened(self, e): pass
    def windowClosed(self, e): pass
    def windowMinimized(self, e): pass
    def windowNormalized(self, e): pass
    def windowActivated(self, e): pass
    def windowDeactivated(self, e): pass
    def disposing(self, e): pass


class _DialogBase(object):
    """Petite aide pour construire des boîtes de dialogue UNO en code."""

    def _init_model(self, ctx, title, w, h):
        self.ctx = ctx
        self.model = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.UnoControlDialogModel", ctx)
        self.model.Title = title
        self.model.Width, self.model.Height = w, h
        self._tab = 0

    def _add(self, kind, name, x, y, w, h, **props):
        m = self.model.createInstance("com.sun.star.awt.UnoControl%sModel" % kind)
        m.Name = name
        m.PositionX, m.PositionY, m.Width, m.Height = x, y, w, h
        self._tab += 1
        try:
            m.TabIndex = self._tab
        except Exception:
            pass
        for k, v in props.items():
            setattr(m, k, v)
        self.model.insertByName(name, m)
        return m

    def _create(self, parent_peer=None):
        smgr = self.ctx.ServiceManager
        self.dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", self.ctx)
        self.dialog.setModel(self.model)
        tk = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)
        self.dialog.createPeer(tk, parent_peer)

    def ctl(self, name):
        return self.dialog.getControl(name)

    def _buttons(self, listener, names):
        for name in names:
            c = self.ctl(name)
            c.setActionCommand(name)
            c.addActionListener(listener)


# --------------------------------------------------------------------------
# Panneau de lecture flottant (réglages en direct)
# --------------------------------------------------------------------------

class ReadingPanel(_DialogBase):
    instance = None
    W, H = 206, 150

    def __init__(self, ctx):
        self._init_model(ctx, "Lecture vocale", self.W, self.H)
        self.syncing = False
        self.pending = {}
        self.timer = None
        self.alive = True
        self._build()
        parent = None
        try:
            win = desktop(ctx).getCurrentFrame().getContainerWindow()
            if win is not None and win.isVisible():     # fenêtre de document visible
                parent = win
        except Exception:
            parent = None
        self._create(parent)
        self.actions = _ActionListener(self.on_action)
        self._buttons(self.actions, ["prev", "toggle", "next", "stop", "rSpeed", "rPitch",
                                     "settings", "close"])
        self.adjust = _AdjustListener(self.on_adjust)
        self.ctl("speed").addAdjustmentListener(self.adjust)
        self.ctl("pitch").addAdjustmentListener(self.adjust)
        self.items = _ItemListener(self.on_item)
        self.ctl("female").addItemListener(self.items)
        self.ctl("male").addItemListener(self.items)
        self.closer = _CloseListener(self.close)
        try:
            self.dialog.addTopWindowListener(self.closer)
        except Exception:
            pass
        self.sync()
        self._place(parent)
        self._poll()

    def _build(self):
        x0 = 6
        y = 6
        self._add("Button", "prev", x0, y, 30, 16, Label="◀◀", HelpText="Phrase précédente")
        self._add("Button", "toggle", x0 + 33, y, 70, 16, Label="▶  Lire")
        self._add("Button", "next", x0 + 106, y, 30, 16, Label="▶▶", HelpText="Phrase suivante")
        self._add("Button", "stop", x0 + 139, y, 55, 16, Label="■  Arrêter")
        y += 21
        self._add("FixedText", "status", x0, y, self.W - 12, 10, Label="Prêt", TextColor=0x505050)
        y += 15
        for key, label, lo, hi, step in (("speed", "Débit", SPEED_MIN, SPEED_MAX, SPEED_STEP),
                                         ("pitch", "Tonalité", PITCH_MIN, PITCH_MAX, 1)):
            self._add("FixedText", "l" + key, x0, y + 1, 36, 10, Label=label)
            self._add("ScrollBar", key, x0 + 38, y, 110, 11, Orientation=0,
                      ScrollValueMin=lo, ScrollValueMax=hi, LineIncrement=step,
                      BlockIncrement=step * 2, VisibleSize=0)
            self._add("FixedText", "v" + key, x0 + 152, y + 1, 26, 10, Align=2)
            self._add("Button", "r" + key.capitalize(), x0 + 181, y - 1, 13, 13, Label="↺",
                      HelpText="Valeur normale")
            y += 17
        self._add("FixedText", "lvoice", x0, y + 1, 36, 10, Label="Voix")
        self._add("RadioButton", "female", x0 + 38, y, 50, 11, Label="Femme")
        self._add("RadioButton", "male", x0 + 92, y, 50, 11, Label="Homme")
        y += 16
        self._add("FixedText", "info", x0, y, self.W - 12, 22, MultiLine=True, TextColor=0x707070)
        by = self.H - 20
        self._add("Button", "settings", x0, by, 100, 14, Label="Paramètres avancés…")
        self._add("Button", "close", self.W - 56, by, 50, 14, Label="Fermer")

    def _place(self, parent):
        """Place le panneau en haut à droite de la fenêtre du document."""
        try:
            p = parent.getPosSize()
            me = self.dialog.getPosSize()
            self.dialog.setPosSize(p.X + p.Width - me.Width - 40, p.Y + 140, 0, 0, 3)
        except Exception:
            pass

    # -- affichage --------------------------------------------------------
    def sync(self):
        """Recopie la configuration actuelle dans les contrôles."""
        if not self.alive:
            return
        cfg = load_config()
        self.syncing = True
        try:
            self.model.getByName("speed").ScrollValue = cfg["speed"]
            self.model.getByName("pitch").ScrollValue = cfg["pitch"]
            self._labels(cfg["speed"], cfg["pitch"])
            self.ctl("female").setState(cfg["gender"] == "female")
            self.ctl("male").setState(cfg["gender"] == "male")
            self._info(cfg)
        finally:
            self.syncing = False

    def _labels(self, speed, pitch):
        self.model.getByName("vspeed").Label = "%d %%" % speed
        self.model.getByName("vpitch").Label = "%+d" % pitch if pitch else "0"

    def _info(self, cfg=None):
        try:
            engine = resolve_engine(cfg or load_config())
            text = "Moteur : " + engine.describe()
        except EngineError as exc:
            text = "⚠ " + str(exc).splitlines()[0]
        self.model.getByName("info").Label = text

    def _poll(self):
        """Met à jour l'état (lecture/pause, numéro de phrase) deux fois par seconde."""
        if not self.alive:
            return

        def refresh():
            if not self.alive:
                return
            state, i, n = reading_status()
            if state == "lecture":
                status, button = "Lecture : phrase %d / %d" % (i + 1, n), "‖  Pause"
            elif state == "pause":
                status, button = "En pause : phrase %d / %d" % (i + 1, n), "▶  Reprendre"
            else:
                status, button = "Prêt — lit la sélection ou depuis le curseur", "▶  Lire"
            m = self.model
            if m.getByName("status").Label != status:
                m.getByName("status").Label = status
            if m.getByName("toggle").Label != button:
                m.getByName("toggle").Label = button

        run_in_main(self.ctx, refresh)
        t = threading.Timer(0.5, self._poll)
        t.daemon = True
        t.start()

    # -- événements -------------------------------------------------------
    def on_action(self, cmd):
        if cmd in ("toggle", "stop"):
            run_command(self.ctx, cmd)
        elif cmd == "prev":
            jump_reading(self.ctx, -1)
        elif cmd == "next":
            jump_reading(self.ctx, +1)
        elif cmd == "rSpeed":
            apply_live(self.ctx, announce=False, speed=100)
        elif cmd == "rPitch":
            apply_live(self.ctx, announce=False, pitch=0)
        elif cmd == "settings":
            if SettingsDialog(self.ctx).execute():
                apply_live(self.ctx, announce=False)
        elif cmd == "close":
            self.close()

    def on_adjust(self, name, value):
        if self.syncing:
            return
        speed = value if name == "speed" else self.model.getByName("speed").ScrollValue
        pitch = value if name == "pitch" else self.model.getByName("pitch").ScrollValue
        if name == "speed":
            speed = int(round(speed / float(SPEED_STEP))) * SPEED_STEP
        self._labels(speed, pitch)
        self.pending[name] = speed if name == "speed" else pitch
        if self.timer is not None:
            self.timer.cancel()
        self.timer = threading.Timer(0.35, self._apply_pending)   # attend la fin du glissement
        self.timer.daemon = True
        self.timer.start()

    def _apply_pending(self):
        changes, self.pending = self.pending, {}
        if changes:
            apply_live(self.ctx, announce=False, **changes)

    def on_item(self, name):
        if self.syncing:
            return
        if self.ctl(name).getState():
            apply_live(self.ctx, announce=False, gender=name)

    def close(self):
        if not self.alive:
            return
        self.alive = False
        ReadingPanel.instance = None
        try:
            self.dialog.setVisible(False)
            self.dialog.dispose()
        except Exception:
            pass

    @classmethod
    def show(cls, ctx):
        panel = cls.instance
        if panel is not None:
            try:
                panel.dialog.setVisible(True)
                panel.sync()
                return panel
            except Exception:
                cls.instance = None
        panel = cls(ctx)
        cls.instance = panel
        panel.dialog.setVisible(True)
        return panel


# --------------------------------------------------------------------------
# Boîte de dialogue des paramètres
# --------------------------------------------------------------------------

class SettingsDialog(_DialogBase):
    W, H = 300, 278

    def __init__(self, ctx):
        self._init_model(ctx, "Lecture vocale — paramètres", self.W, self.H)
        self.cfg = load_config()
        self._build()
        self._create()
        self._fill()
        self.listener = _ActionListener(self.on_action)
        self._buttons(self.listener, ["btnPiper", "btnModelF", "btnModelM", "btnTest", "btnRefresh"])

    def _build(self):
        L, F = 8, 96
        W = self.W - F - 8 - 22
        y = 8
        self._add("FixedText", "lEngine", L, y + 2, 86, 10, Label="Moteur de synthèse")
        self._add("ListBox", "engine", F, y, W + 22, 13, Dropdown=True,
                  StringItemList=tuple(ENGINE_LABELS))
        y += 22
        self._add("FixedLine", "fl1", L, y, self.W - 16, 8, Label="Piper (voix neuronales)")
        y += 12
        self._add("FixedText", "lPiper", L, y + 2, 86, 10, Label="Exécutable piper")
        self._add("Edit", "piper_bin", F, y, W, 13)
        self._add("Button", "btnPiper", F + W + 4, y, 18, 13, Label="…")
        y += 17
        self._add("FixedText", "lModelF", L, y + 2, 86, 10, Label="Voix femme (.onnx)")
        self._add("ComboBox", "piper_model_female", F, y, W, 13, Dropdown=True)
        self._add("Button", "btnModelF", F + W + 4, y, 18, 13, Label="…")
        y += 17
        self._add("FixedText", "lModelM", L, y + 2, 86, 10, Label="Voix homme (.onnx)")
        self._add("ComboBox", "piper_model_male", F, y, W, 13, Dropdown=True)
        self._add("Button", "btnModelM", F + W + 4, y, 18, 13, Label="…")
        y += 21
        self._add("FixedLine", "fl2", L, y, self.W - 16, 8, Label="Réglages par défaut")
        y += 12
        self._add("FixedText", "lSpeed", L, y + 2, 86, 10, Label="Débit (%)")
        self._add("NumericField", "speed", F, y, 40, 13, ValueMin=float(SPEED_MIN),
                  ValueMax=float(SPEED_MAX), ValueStep=float(SPEED_STEP), DecimalAccuracy=0,
                  Spin=True, StrictFormat=True)
        self._add("FixedText", "lPitch", F + 50, y + 2, 38, 10, Label="Tonalité")
        self._add("NumericField", "pitch", F + 90, y, 36, 13, ValueMin=float(PITCH_MIN),
                  ValueMax=float(PITCH_MAX), ValueStep=1.0, DecimalAccuracy=0, Spin=True,
                  StrictFormat=True)
        y += 17
        self._add("FixedText", "lGender", L, y + 2, 86, 10, Label="Voix")
        self._add("ListBox", "gender", F, y, 50, 13, Dropdown=True, StringItemList=("Femme", "Homme"))
        self._add("FixedText", "lLang", F + 56, y + 2, 32, 10, Label="Langue")
        self._add("Edit", "language", F + 90, y, 36, 13,
                  HelpText="auto = langue du texte ; ou un code pour la forcer : fr, en, de…")
        y += 17
        self._add("CheckBox", "highlight", F, y, W + 22, 12,
                  Label="Surligner la phrase en cours de lecture")
        y += 18
        self._add("FixedLine", "fl3", L, y, self.W - 16, 8, Label="Autres moteurs (facultatif)")
        y += 12
        self._add("FixedText", "lSpdMod", L, y + 2, 86, 10, Label="Module spd-say")
        self._add("Edit", "spd_module", F, y, 70, 13,
                  HelpText="vide = module par défaut ; ex. espeak-ng-mbrola-generic, piper")
        self._add("FixedText", "lSpdVoice", F + 76, y + 2, 24, 10, Label="Voix")
        self._add("ComboBox", "spd_voice", F + 100, y, W - 78, 13, Dropdown=True,
                  HelpText="vide = voix choisie selon la langue et le genre")
        y += 17
        self._add("FixedText", "lEspeak", L, y + 2, 86, 10,
                  Label="Voix eSpeak" + (" / SAPI" if IS_WIN else " / macOS" if IS_MAC else ""))
        self._add("Edit", "espeak_voice", F, y, 70, 13, HelpText="vide = langue du texte ; ex. fr, mb-fr4 (MBROLA)")
        if IS_WIN:
            self._add("ComboBox", "sapi_voice", F + 76, y, W - 54, 13, Dropdown=True,
                      HelpText="vide = voix choisie selon la langue et le genre")
        if IS_MAC:
            self._add("ComboBox", "mac_voice", F + 76, y, W - 54, 13, Dropdown=True,
                      HelpText="vide = voix choisie selon la langue et le genre")
        y += 19
        self._add("FixedText", "detect", L, y, self.W - 16, 20, MultiLine=True, TextColor=0x606060)
        by = self.H - 20
        self._add("Button", "btnRefresh", L, by, 54, 14, Label="Redétecter")
        self._add("Button", "btnTest", L + 58, by, 54, 14, Label="▶ Tester")
        self._add("Button", "ok", self.W - 116, by, 52, 14, Label="OK", PushButtonType=1,
                  DefaultButton=True)
        self._add("Button", "cancel", self.W - 60, by, 52, 14, Label="Annuler", PushButtonType=2)

    def _fill(self):
        c = self.cfg
        key = c.get("engine", "auto")
        self.ctl("engine").selectItemPos(ENGINE_KEYS.index(key) if key in ENGINE_KEYS else 0, True)
        self.ctl("piper_bin").setText(c.get("piper_bin") or find_piper(c))
        self._fill_models()
        self.ctl("speed").setValue(float(c.get("speed", 100)))
        self.ctl("pitch").setValue(float(c.get("pitch", 0)))
        self.ctl("gender").selectItemPos(1 if c.get("gender") == "male" else 0, True)
        self.ctl("language").setText(c.get("language", "auto"))
        self.ctl("highlight").setState(1 if c.get("highlight", True) else 0)
        self.ctl("spd_module").setText(c.get("spd_module", ""))
        spd = _which("spd-say") if not IS_WIN and not IS_MAC else ""
        self._fill_voices("spd_voice", spd_voices(spd, c.get("spd_module")) if spd else [])
        self.ctl("spd_voice").setText(c.get("spd_voice", ""))
        self.ctl("espeak_voice").setText(c.get("espeak_voice", ""))
        if IS_WIN:
            self._fill_voices("sapi_voice", sapi_voices())
            self.ctl("sapi_voice").setText(c.get("sapi_voice", ""))
        if IS_MAC:
            self._fill_voices("mac_voice", mac_voices())
            self.ctl("mac_voice").setText(c.get("mac_voice", ""))
        self._detect()

    def _fill_voices(self, control, voices):
        if voices:
            self.ctl(control).addItems(tuple(voice[0] for voice in voices), 0)

    def _fill_models(self, refresh=False):
        models = find_models(self.cfg, refresh)
        for g in ("female", "male"):
            combo = self.ctl("piper_model_" + g)
            combo.removeItems(0, combo.getItemCount())
            if models:
                combo.addItems(tuple(models), 0)
            current = self.cfg.get("piper_model_" + g) or ""
            if not current:
                guesses = [m for m in models if guess_gender(m) == g]
                if guesses:
                    current = guesses[0]
                elif g == "female" and self.cfg.get("piper_model") \
                        and guess_gender(self.cfg["piper_model"]) != "male":
                    current = self.cfg["piper_model"]
            combo.setText(current)

    def _detect(self):
        self.model.getByName("detect").Label = "Détecté : " + detection_summary(self.read())

    def read(self):
        c = dict(self.cfg)
        pos = self.ctl("engine").getSelectedItemPos()
        c["engine"] = ENGINE_KEYS[pos] if 0 <= pos < len(ENGINE_KEYS) else "auto"
        c["piper_bin"] = self.ctl("piper_bin").getText().strip()
        c["piper_model_female"] = self.ctl("piper_model_female").getText().strip()
        c["piper_model_male"] = self.ctl("piper_model_male").getText().strip()
        c["speed"] = _clamp(self.ctl("speed").getValue(), SPEED_MIN, SPEED_MAX, 100)
        c["pitch"] = _clamp(self.ctl("pitch").getValue(), PITCH_MIN, PITCH_MAX, 0)
        c["gender"] = "male" if self.ctl("gender").getSelectedItemPos() == 1 else "female"
        c["language"] = self.ctl("language").getText().strip() or "auto"
        c["highlight"] = self.ctl("highlight").getState() == 1
        c["spd_module"] = self.ctl("spd_module").getText().strip()
        c["spd_voice"] = self.ctl("spd_voice").getText().strip()
        c["espeak_voice"] = self.ctl("espeak_voice").getText().strip()
        if IS_WIN:
            c["sapi_voice"] = self.ctl("sapi_voice").getText().strip()
        if IS_MAC:
            c["mac_voice"] = self.ctl("mac_voice").getText().strip()
        return c

    def on_action(self, cmd):
        if cmd == "btnPiper":
            path, _ = file_dialog(self.ctx, title="Choisir l'exécutable piper")
            if path:
                self.ctl("piper_bin").setText(path)
                self._detect()
        elif cmd in ("btnModelF", "btnModelM"):
            path, _ = file_dialog(self.ctx, filters=[("Voix Piper (*.onnx)", "*.onnx")],
                                  title="Choisir une voix Piper")
            if path:
                if not os.path.isfile(path + ".json"):
                    msgbox(self.ctx, "Le fichier %s.json doit se trouver à côté de la voix."
                           % os.path.basename(path), "warningbox")
                self.ctl("piper_model_" + ("female" if cmd == "btnModelF" else "male")).setText(path)
                self._detect()
        elif cmd == "btnRefresh":
            self.cfg = self.read()
            self._fill_models(refresh=True)
            self._detect()
        elif cmd == "btnTest":
            cfg = self.read()
            language = "fr" if cfg["language"].lower() == "auto" else cfg["language"]
            sample = SAMPLE_TEXT_EN if primary_language(language) == "en" else SAMPLE_TEXT
            try:
                engine = resolve_engine(cfg, language=language)
            except EngineError as exc:
                msgbox(self.ctx, str(exc), "errorbox")
                return
            stop_reading()
            reader = Reader(self.ctx, engine, [(sample, None, language)], 0, None, False)
            with STATE_LOCK:
                STATE.reader = reader
            reader.start()

    def execute(self):
        ok = self.dialog.execute() == 1
        cfg = self.read()
        self.dialog.dispose()
        if ok:
            save_config(cfg)
            log("paramètres enregistrés:", json.dumps(cfg, ensure_ascii=False))
        return ok


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------

QUICK = ("faster", "slower", "pitchup", "pitchdown", "female", "male", "resetvoice")


def run_command(ctx, command):
    try:
        log("commande:", command)
        if command == "stop":
            stop_reading()
            return
        if command in QUICK:
            quick_setting(ctx, command)
            return
        if command in ("next", "prev"):
            jump_reading(ctx, 1 if command == "next" else -1)
            return
        if command == "panel":
            ReadingPanel.show(ctx)
            return
        if command == "settings":
            if SettingsDialog(ctx).execute():
                apply_live(ctx, announce=False)
            return
        if command == "selftest":
            dlg = SettingsDialog(ctx)
            log("selftest dialogue ok:", json.dumps(dlg.read(), ensure_ascii=False))
            dlg.dialog.dispose()
            return
        if command == "about":
            msgbox(ctx, "Lecture vocale %s — ADAnalytics\n\nMoteurs : Piper, Speech Dispatcher, "
                        "eSpeak NG / MBROLA, Windows SAPI, macOS.\n\nDétecté : %s\n\n"
                        "Paramètres : %s\nJournal : %s"
                   % (VERSION, detection_summary(load_config()), CONFIG_FILE, LOG_FILE))
            return

        doc = desktop(ctx).getCurrentComponent()
        if doc is None or not hasattr(doc, "getCurrentController"):
            msgbox(ctx, "Ouvrez d'abord un document.", "warningbox")
            return
        ctrl = doc.getCurrentController()

        if command == "export":
            export_audio(ctx, doc, ctrl)
            return

        if command == "toggle":
            if pause_reading():
                return
            with STATE_LOCK:
                paused = STATE.paused
            if paused and paused.get("doc") == doc and paused["index"] < len(paused["segments"]):
                start_reading(ctx, doc, ctrl, paused["segments"], paused["index"])
                return
            command = "read"

        if command in ("read", "readall"):
            segments = collect_segments(doc, ctrl, "all" if command == "readall" else "cursor")
            if not segments:
                msgbox(ctx, "Rien à lire : le document (ou la sélection) est vide.", "warningbox")
                return
            start_reading(ctx, doc, ctrl, segments, 0)
    except EngineError as exc:
        msgbox(ctx, str(exc), "errorbox")
    except Exception:
        log("commande:", traceback.format_exc())
        msgbox(ctx, "Erreur inattendue. Détails dans le journal :\n" + LOG_FILE, "errorbox")


class LectureVocale(unohelper.Base, XJobExecutor):
    def __init__(self, ctx):
        self.ctx = ctx

    def trigger(self, command):
        run_command(self.ctx, command)


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(LectureVocale, IMPL_NAME, SERVICE_NAMES)
