# Lecture vocale – Read Aloud for LibreOffice

[English](#english) · [Français](#français)

Offline text-to-speech extension for LibreOffice 24.2 and all later versions, on Windows, Linux
and macOS.
Written in Python, MIT licensed.

Extension de synthèse vocale hors ligne pour LibreOffice 24.2 et toutes les versions suivantes,
sous Windows, Linux et macOS. Écrite en Python, sous licence MIT.

---

## English

### What it does

Lecture vocale reads your Writer, Calc, Impress and Draw documents aloud. Everything runs on your
computer: no account, no internet connection, and your text never leaves your machine.

- **Read** the selection, or from the cursor when nothing is selected, or the whole document.
- **Every language your system has a voice for**, including Arabic, Chinese, Hindi, Japanese,
  Korean and Russian.
- **The voice follows the language of the text**, sentence by sentence. A French paragraph in an
  English document gets a French voice.
- **Female or male voice**, using the gender declared by the system for each voice.
- **Pause and resume** at the same sentence, and jump to the next or previous sentence.
- **Highlighting** of the sentence being read.
- **Reading panel** to change speed, pitch and female/male voice while reading.
- **Audio export** to WAV, and to MP3 or OGG when ffmpeg is installed.
- **Reading mode per application**: sentence by sentence in Writer, row by row in Calc, shape by
  shape in Impress and Draw.

The extension's interface is in French.

### Requirements

- LibreOffice **24.2 or later**, with Python scripting support.
- The speech service of your system:

| System | Speech service | Voices used |
|---|---|---|
| Windows 10/11 | Windows speech | All installed voices, including those added in Windows Settings |
| macOS | `say` | All voices installed in macOS |
| Linux | Speech Dispatcher | All voices of the Speech Dispatcher module in use |

In automatic mode the extension uses this service first. It moves on to
[Piper](https://github.com/rhasspy/piper), then eSpeak NG, only if the service lacks a voice for one
of the document's languages. You can also choose an engine in the settings: Piper offers very
natural neural voices.

Optional: **ffmpeg**, for MP3/OGG export and for pitch control with Piper.

On Debian and Ubuntu, install Python scripting support and Speech Dispatcher:

```bash
sudo apt install libreoffice-script-provider-python speech-dispatcher speech-dispatcher-espeak-ng
```

LibreOffice installed as a Flatpak or Snap runs in a sandbox and may not see the speech engines
installed on your system. A native package is the safest choice.

### Installation

1. Download `LectureVocale-1.3.0.oxt`.
2. In LibreOffice, open **Tools ▸ Extensions…** (or press Ctrl+Alt+E), then **Add…**, and pick the file.
3. Restart LibreOffice. A **Lecture vocale** menu and toolbar appear.

Version 1.3.0 replaces 1.2 directly, and your settings are kept.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+Alt+Shift+L | Read / pause |
| Ctrl+Alt+Shift+K | Stop |
| Ctrl+Alt+Shift+P | Reading panel |
| Ctrl+Alt+Shift+N | Next sentence |
| Ctrl+Alt+Shift+B | Previous sentence |
| Ctrl+Alt+Shift+Page Up / Page Down | Faster / slower |

On macOS, use Cmd+Option+Shift instead of Ctrl+Alt+Shift.

### Voices and languages

The language comes from LibreOffice: the one set on the text (**Tools ▸ Language**), otherwise the
document's default language. If the writing system contradicts it, the writing system wins: Russian
text left marked as French gets a Russian voice. In **Lecture vocale ▸ Settings**, the **Langue**
field is `auto` by default. Enter a code such as `fr` or `en` to force one language for everything.

The extension never uses a voice from another language when one is available for the text. If no
installed voice speaks that language, it tells you once, then reads with the default voice.

If the voice sounds wrong, check that the text really has the right language in LibreOffice, then
install a voice:

- **Windows**: Settings ▸ Time & language ▸ Speech ▸ Add voices.
- **macOS**: System Settings ▸ Accessibility ▸ Spoken Content ▸ System voice ▸ Manage voices.
- **Linux**: install a Speech Dispatcher module, for example `speech-dispatcher-espeak-ng`, which
  covers about a hundred languages. Run `spd-say -L` to list the available voices.
- **Piper**: download a voice (the `.onnx` file and its `.onnx.json`) from
  [piper-voices](https://huggingface.co/rhasspy/piper-voices), for example `fr_FR-siwis-medium`
  (female) or `fr_FR-tom-medium` (male), and select it in the settings.

The settings list the voices installed on your computer. Leave the field empty to let the language
and the gender decide. A voice you pick is used only for its own language and gender.

A female or male voice can only be used if the system has one for that language. The extension tells
you when it has to use the other gender. With Speech Dispatcher, the gender depends on the module:
`speech-dispatcher-espeak-ng` offers both.

Pitch control depends on the engine:

| Engine | Pitch control |
|---|---|
| Windows voices | Yes |
| Speech Dispatcher | Yes, if the module supports it (`speech-dispatcher-espeak-ng` does) |
| eSpeak NG | Yes |
| Piper | Yes, with ffmpeg |
| macOS voices | Only on voices that support it, mainly older ones |

Audio export uses one voice for the whole file, chosen from the language of the first sentence.

### Building from source

Only Python 3.8 or later is needed:

```bash
python build.py
```

The extension is written to `dist/LectureVocale-<version>.oxt`. The script stops on malformed XML,
on Python syntax that LibreOffice 24.2's Python 3.8 cannot read, and on files missing from the
manifest.

Run the tests (the audio test runs on Windows only):

```bash
python -m unittest discover -s tests
```

### Project layout

| Path | Content |
|---|---|
| `extension/python/lecture_vocale.py` | All the extension's code |
| `extension/Addons.xcu`, `Accelerators.xcu` | Menu, toolbar and shortcuts |
| `extension/description.xml` | Identifier, version, minimum LibreOffice version |
| `build.py` | Builds the `.oxt` |
| `tests/` | Automated tests, which run without LibreOffice |

### Troubleshooting

Settings and the log file are in `%APPDATA%\lecture-vocale-libreoffice` on Windows, and in
`~/.config/lecture-vocale-libreoffice` on Linux and macOS. **Lecture vocale ▸ About** shows the
engines detected on your computer.

### Changelog

**1.3.0**

- Fixed: text could be read by a voice of the wrong language, usually English.
- Fixed: the female/male choice was ignored by Windows voices, and Piper could use a voice of the
  wrong gender.
- Windows: all installed voices are now available, not only the older ones, with real pitch control.
- macOS: the gender of each voice now comes from the system, for all languages.
- Linux: Speech Dispatcher is now the default engine, and regional variants such as pt-BR are used.
- All languages: Asian and complex scripts are now recognized, and a Latin-only language setting no
  longer mislabels Russian, Arabic or Japanese text.
- The language now comes from the text, sentence by sentence, instead of a fixed setting.
- In automatic mode, the system's speech service comes first, and an engine is skipped if it has no
  voice for one of the document's languages. **If you use Piper**, select it in the settings.
- The settings list the installed voices.
- A message now tells you when no voice exists for a language or gender.
- Piper: multi-speaker voices are supported, and more voices are recognized as female or male.
- LibreOffice 24.2 is now the minimum version.

### License

MIT. See [LICENSE](LICENSE).

---

## Français

### Ce que fait l'extension

Lecture vocale lit à voix haute vos documents Writer, Calc, Impress et Draw. Tout se passe sur votre
ordinateur : pas de compte, pas de connexion Internet, et votre texte ne quitte jamais votre machine.

- **Lecture** de la sélection, ou depuis le curseur si rien n'est sélectionné, ou du document entier.
- **Toutes les langues pour lesquelles votre système a une voix**, y compris l'arabe, le chinois, le
  coréen, l'hindi, le japonais et le russe.
- **La voix suit la langue du texte**, phrase par phrase. Un paragraphe en français dans un document
  anglais est lu par une voix française.
- **Voix féminine ou masculine**, d'après le genre déclaré par le système pour chaque voix.
- **Pause et reprise** à la même phrase, et passage à la phrase suivante ou précédente.
- **Surlignage** de la phrase en cours de lecture.
- **Panneau de lecture** pour régler le débit, la tonalité et la voix femme/homme pendant la lecture.
- **Export audio** en WAV, et en MP3 ou OGG si ffmpeg est installé.
- **Mode de lecture adapté** : phrase par phrase dans Writer, ligne par ligne dans Calc, forme par
  forme dans Impress et Draw.

L'interface de l'extension est en français.

### Prérequis

- LibreOffice **24.2 ou plus récent**, avec la prise en charge des scripts Python.
- Le service vocal de votre système :

| Système | Service vocal | Voix utilisées |
|---|---|---|
| Windows 10/11 | Synthèse vocale de Windows | Toutes les voix installées, y compris celles ajoutées dans les Paramètres de Windows |
| macOS | `say` | Toutes les voix installées dans macOS |
| Linux | Speech Dispatcher | Toutes les voix du module Speech Dispatcher utilisé |

En mode automatique, l'extension utilise d'abord ce service. Elle ne passe à
[Piper](https://github.com/rhasspy/piper), puis à eSpeak NG, que si le service n'a pas de voix pour
l'une des langues du document. Vous pouvez aussi choisir un moteur dans les paramètres : Piper propose
des voix neuronales très naturelles.

Facultatif : **ffmpeg**, pour l'export MP3/OGG et le réglage de la tonalité avec Piper.

Sous Debian et Ubuntu, installez les scripts Python et Speech Dispatcher :

```bash
sudo apt install libreoffice-script-provider-python speech-dispatcher speech-dispatcher-espeak-ng
```

Un LibreOffice installé en Flatpak ou en Snap tourne dans un bac à sable et peut ne pas voir les
moteurs installés sur le système. Un paquet natif est le choix le plus sûr.

### Installation

1. Téléchargez `LectureVocale-1.3.0.oxt`.
2. Dans LibreOffice, ouvrez **Outils ▸ Extensions…** (ou Ctrl+Alt+E), puis **Ajouter…**, et choisissez
   le fichier.
3. Redémarrez LibreOffice. Un menu et une barre d'outils **Lecture vocale** apparaissent.

La version 1.3.0 remplace directement la 1.2, et vos réglages sont conservés.

### Raccourcis clavier

| Raccourci | Action |
|---|---|
| Ctrl+Alt+Maj+L | Lire / mettre en pause |
| Ctrl+Alt+Maj+K | Arrêter |
| Ctrl+Alt+Maj+P | Panneau de lecture |
| Ctrl+Alt+Maj+N | Phrase suivante |
| Ctrl+Alt+Maj+B | Phrase précédente |
| Ctrl+Alt+Maj+Page↑ / Page↓ | Plus vite / moins vite |

Sous macOS, utilisez Cmd+Option+Maj à la place de Ctrl+Alt+Maj.

### Voix et langues

La langue vient de LibreOffice : celle définie sur le texte (**Outils ▸ Langue**), sinon la langue par
défaut du document. Si l'écriture la contredit, c'est l'écriture qui l'emporte : un texte russe resté
marqué en français est lu par une voix russe. Dans **Lecture vocale ▸ Paramètres**, le champ **Langue**
vaut `auto` par défaut. Indiquez un code comme `fr` ou `en` pour imposer une seule langue partout.

L'extension n'utilise jamais une voix d'une autre langue quand une voix existe pour le texte. Si aucune
voix installée ne parle cette langue, elle vous prévient une fois, puis lit avec la voix par défaut.

Si la voix semble incorrecte, vérifiez que le texte a bien la bonne langue dans LibreOffice, puis
installez une voix :

- **Windows** : Paramètres ▸ Heure et langue ▸ Voix ▸ Ajouter des voix.
- **macOS** : Réglages Système ▸ Accessibilité ▸ Contenu énoncé ▸ Voix du système ▸ Gérer les voix.
- **Linux** : installez un module de Speech Dispatcher, par exemple `speech-dispatcher-espeak-ng`, qui
  couvre une centaine de langues. La commande `spd-say -L` liste les voix disponibles.
- **Piper** : téléchargez une voix (le fichier `.onnx` et son `.onnx.json`) sur
  [piper-voices](https://huggingface.co/rhasspy/piper-voices), par exemple `fr_FR-siwis-medium`
  (femme) ou `fr_FR-tom-medium` (homme), et choisissez-la dans les paramètres.

Les paramètres listent les voix installées sur votre ordinateur. Laissez le champ vide pour que la
langue et le genre décident. Une voix choisie n'est utilisée que pour sa langue et son genre.

Une voix féminine ou masculine n'est utilisable que si le système en a une pour cette langue.
L'extension vous prévient quand elle doit utiliser l'autre genre. Avec Speech Dispatcher, le genre
dépend du module : `speech-dispatcher-espeak-ng` propose les deux.

Le réglage de la tonalité dépend du moteur :

| Moteur | Réglage de la tonalité |
|---|---|
| Voix Windows | Oui |
| Speech Dispatcher | Oui, si le module le permet (c'est le cas de `speech-dispatcher-espeak-ng`) |
| eSpeak NG | Oui |
| Piper | Oui, avec ffmpeg |
| Voix macOS | Seulement sur les voix qui le permettent, surtout les plus anciennes |

L'export audio utilise une seule voix pour tout le fichier, choisie d'après la langue de la première
phrase.

### Construire depuis les sources

Seul Python 3.8 ou plus récent est nécessaire :

```bash
python build.py
```

L'extension est écrite dans `dist/LectureVocale-<version>.oxt`. Le script s'arrête sur un XML mal
formé, sur une syntaxe que le Python 3.8 de LibreOffice 24.2 ne sait pas lire, et sur un fichier
absent du manifeste.

Lancer les tests (le test audio ne tourne que sous Windows) :

```bash
python -m unittest discover -s tests
```

### Organisation du projet

| Chemin | Contenu |
|---|---|
| `extension/python/lecture_vocale.py` | Tout le code de l'extension |
| `extension/Addons.xcu`, `Accelerators.xcu` | Menu, barre d'outils et raccourcis |
| `extension/description.xml` | Identifiant, version, version minimale de LibreOffice |
| `build.py` | Construit le `.oxt` |
| `tests/` | Tests automatiques, qui tournent sans LibreOffice |

### Dépannage

Les réglages et le journal se trouvent dans `%APPDATA%\lecture-vocale-libreoffice` sous Windows, et
dans `~/.config/lecture-vocale-libreoffice` sous Linux et macOS. **Lecture vocale ▸ À propos** affiche
les moteurs détectés sur votre ordinateur.

### Historique des versions

**1.3.0**

- Corrigé : le texte pouvait être lu par une voix de la mauvaise langue, le plus souvent l'anglais.
- Corrigé : le choix femme/homme était ignoré par les voix Windows, et Piper pouvait utiliser une
  voix du mauvais genre.
- Windows : toutes les voix installées sont disponibles, pas seulement les plus anciennes, avec un
  vrai réglage de la tonalité.
- macOS : le genre de chaque voix vient désormais du système, pour toutes les langues.
- Linux : Speech Dispatcher devient le moteur par défaut, et les variantes régionales comme pt-BR
  sont utilisées.
- Toutes les langues : les écritures asiatiques et complexes sont reconnues, et un texte russe, arabe
  ou japonais n'est plus confondu avec la langue du document.
- La langue vient désormais du texte, phrase par phrase, et non plus d'un réglage fixe.
- En mode automatique, le service vocal du système passe en premier, et un moteur est ignoré s'il n'a
  pas de voix pour l'une des langues du document. **Si vous utilisez Piper**, choisissez-le dans les
  paramètres.
- Les paramètres listent les voix installées.
- Un message prévient quand aucune voix n'existe pour une langue ou un genre.
- Piper : prise en charge des voix à plusieurs locuteurs, et davantage de voix reconnues comme
  féminines ou masculines.
- LibreOffice 24.2 est désormais la version minimale.

### Licence

MIT. Voir [LICENSE](LICENSE).
