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
- **A drop-down list of all available voices**, in the reading panel and the settings.
- **Pause and resume** at the same sentence, and jump to the next or previous sentence.
- **Highlighting** of the sentence being read.
- **Reading panel** to change speed, pitch and voice while reading.
- **Audio export** to WAV, and to MP3 or OGG when ffmpeg is installed.
- **Reading mode per application**: sentence by sentence in Writer, row by row in Calc, shape by
  shape in Impress and Draw.

- **Interface in six languages**: English, French, German, Italian, Portuguese and Spanish. Menus,
  buttons and messages follow the language of LibreOffice, and you can choose another one.

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
| Ctrl+Alt+Shift+S | Stop |
| Ctrl+Alt+Shift+O | Reading panel |
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
  or `fr_FR-tom-medium`, and select it in the settings.

The **Voice** list, in the reading panel and the settings, shows the voices available on your computer:

- **Automatic**, the default: the voice follows the language of the text, sentence by sentence.
- **A voice from the list**: that voice reads everything, even text in another language. It will then
  read with its own accent.

The **Test** button in the settings plays the chosen voice in its own language. After installing a new
voice, click **Detect again** in the settings.

With Speech Dispatcher, the list shows the main voice of each language of the module in use.

Pitch control depends on the engine:

| Engine | Pitch control |
|---|---|
| Windows voices | Yes |
| Speech Dispatcher | Yes, if the module supports it (`speech-dispatcher-espeak-ng` does) |
| eSpeak NG | Yes |
| Piper | Yes, with ffmpeg |
| macOS voices | Only on voices that support it, mainly older ones |

Audio export uses one voice for the whole file, chosen from the language of the first sentence.

### Interface language

By default, the menus, buttons and messages use the language of LibreOffice. If that language is not
translated, they appear in English.

To choose another language, open **Lecture vocale ▸ Advanced Settings** and change **Interface
language**. Windows and messages change at once; the menus change the next time LibreOffice starts.

To add a language, copy `extension/python/i18n/en.json`, translate its values, then add the language
to `UI_LANGUAGES` in `lecture_vocale.py` and to the titles in `Addons.xcu`.

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
| `extension/python/i18n/` | Interface translations |
| `extension/Addons.xcu`, `Accelerators.xcu` | Menu, toolbar and shortcuts |
| `extension/description.xml` | Identifier, version, minimum LibreOffice version |
| `build.py` | Builds the `.oxt` |
| `tests/` | Automated tests and checks in a real LibreOffice |
| `docs/` | Contributor Guide (PDF) and its source |

### Troubleshooting

Settings and the log file are in `%APPDATA%\lecture-vocale-libreoffice` on Windows, and in
`~/.config/lecture-vocale-libreoffice` on Linux and macOS. **Lecture vocale ▸ Settings** shows the
engines detected on your computer.

### Help and support

Found a bug, or a voice that does not work?
[Open an issue on GitHub](https://github.com/XGEOSOFT-CORPORATION/lecture-vocale/issues).

Does the extension help you? Encourage XGEOSOFT CORPORATION with a star on
[GitHub](https://github.com/XGEOSOFT-CORPORATION/lecture-vocale).

The **About** window has a button for each. Open it from the **Lecture vocale** menu, the reading
panel or the settings.

Want to go further? The [Contributor Guide](docs/Contributor-Guide.pdf), in English and French, explains
how to support the project, how it is organised, and how to contribute. It is also included in the
extension, and opens from the **About** window.

### Changelog

**1.3.0**

- New **About** window, with links to report a problem and to encourage the project on GitHub.
- New Contributor Guide in English and French, included in the extension.
- The interface is now available in six languages, follows LibreOffice by default, and has an option
  to choose another language.
- The toolbar now has its real name, instead of "Add-on 2", and is docked at the top.
- Fixed: text could be read by a voice of the wrong language, usually English.
- Fixed: the chosen voice could be ignored, on Windows in particular.
- Fixed: keyboard shortcuts did not work at all. Stop is now Ctrl+Alt+Shift+S and the reading
  panel Ctrl+Alt+Shift+O, because K and P are taken in Impress and Draw.
- Fixed: "Open a document first" could appear while a document was open.
- Fixed: after stopping, the sentence stayed selected, so the next reading or audio export covered
  only that sentence, and typing could replace it.
- Fixed: settings changed quickly during reading could be lost on Windows.
- Windows: all installed voices are now available, not only the older ones, with real pitch control.
- Linux: Speech Dispatcher is now the default engine, and regional variants such as pt-BR are used.
- All languages: Asian and complex scripts are now recognized, and a Latin-only language setting no
  longer mislabels Russian, Arabic or Japanese text.
- The language now comes from the text, sentence by sentence, instead of a fixed setting.
- In automatic mode, the system's speech service comes first, and an engine is skipped if it has no
  voice for one of the document's languages. **If you use Piper**, select it in the settings.
- The settings list the installed voices.
- A message now tells you when no voice exists for a language.
- Piper: each speaker of a multi-speaker voice appears in the voice list.
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
- **Une liste déroulante de toutes les voix disponibles**, dans le panneau de lecture et les paramètres.
- **Pause et reprise** à la même phrase, et passage à la phrase suivante ou précédente.
- **Surlignage** de la phrase en cours de lecture.
- **Panneau de lecture** pour régler le débit, la tonalité et la voix pendant la lecture.
- **Export audio** en WAV, et en MP3 ou OGG si ffmpeg est installé.
- **Mode de lecture adapté** : phrase par phrase dans Writer, ligne par ligne dans Calc, forme par
  forme dans Impress et Draw.

- **Interface en six langues** : allemand, anglais, espagnol, français, italien et portugais. Menus,
  boutons et messages suivent la langue de LibreOffice, et vous pouvez en choisir une autre.

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
| Ctrl+Alt+Maj+S | Arrêter |
| Ctrl+Alt+Maj+O | Panneau de lecture |
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
  ou `fr_FR-tom-medium`, et choisissez-la dans les paramètres.

La liste **Voix**, dans le panneau de lecture et les paramètres, affiche les voix disponibles sur votre
ordinateur :

- **Automatique**, par défaut : la voix suit la langue du texte, phrase par phrase.
- **Une voix de la liste** : cette voix lit tout, même un texte dans une autre langue. Elle le lira alors
  avec son propre accent.

Le bouton **Tester** des paramètres fait entendre la voix choisie dans sa propre langue. Après avoir
installé une voix, cliquez sur **Redétecter** dans les paramètres.

Avec Speech Dispatcher, la liste affiche la voix principale de chaque langue du module utilisé.

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

### Langue de l'interface

Par défaut, les menus, les boutons et les messages utilisent la langue de LibreOffice. Si cette langue
n'est pas traduite, ils s'affichent en anglais.

Pour choisir une autre langue, ouvrez **Lecture vocale ▸ Paramètres avancés** et changez **Langue de
l'interface**. Les fenêtres et les messages changent aussitôt ; les menus changent au prochain
démarrage de LibreOffice.

Pour ajouter une langue, copiez `extension/python/i18n/en.json`, traduisez ses valeurs, puis ajoutez la
langue à `UI_LANGUAGES` dans `lecture_vocale.py` et aux titres de `Addons.xcu`.

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
| `extension/python/i18n/` | Traductions de l'interface |
| `extension/Addons.xcu`, `Accelerators.xcu` | Menu, barre d'outils et raccourcis |
| `extension/description.xml` | Identifiant, version, version minimale de LibreOffice |
| `build.py` | Construit le `.oxt` |
| `tests/` | Tests automatiques et vérifications dans un vrai LibreOffice |
| `docs/` | Guide du contributeur (PDF) et sa source |

### Dépannage

Les réglages et le journal se trouvent dans `%APPDATA%\lecture-vocale-libreoffice` sous Windows, et
dans `~/.config/lecture-vocale-libreoffice` sous Linux et macOS. **Lecture vocale ▸ Paramètres**
affiche les moteurs détectés sur votre ordinateur.

### Aide et soutien

Une erreur, une voix qui ne fonctionne pas ?
[Ouvrez un ticket sur GitHub](https://github.com/XGEOSOFT-CORPORATION/lecture-vocale/issues).

L'extension vous rend service ? Encouragez XGEOSOFT CORPORATION avec une étoile sur
[GitHub](https://github.com/XGEOSOFT-CORPORATION/lecture-vocale).

La fenêtre **À propos** propose un bouton pour chacun. Elle s'ouvre depuis le menu **Lecture vocale**,
le panneau de lecture ou les paramètres.

Vous voulez aller plus loin ? Le [guide du contributeur](docs/Contributor-Guide.pdf), en anglais et en
français, explique comment soutenir le projet, comment il est organisé et comment y contribuer. Il est
aussi inclus dans l'extension et s'ouvre depuis la fenêtre **À propos**.

### Historique des versions

**1.3.0**

- Nouvelle fenêtre **À propos**, avec des liens pour signaler un problème et encourager le projet sur
  GitHub.
- Nouveau guide du contributeur en anglais et en français, inclus dans l'extension.
- L'interface existe désormais en six langues, suit LibreOffice par défaut, et une option permet d'en
  choisir une autre.
- La barre d'outils porte son vrai nom, au lieu de « Add-on 2 », et s'ancre en haut.
- Corrigé : le texte pouvait être lu par une voix de la mauvaise langue, le plus souvent l'anglais.
- Corrigé : la voix choisie pouvait être ignorée, en particulier sous Windows.
- Corrigé : les raccourcis clavier ne fonctionnaient pas du tout. Arrêter devient Ctrl+Alt+Maj+S et
  le panneau Ctrl+Alt+Maj+O, car K et P sont déjà pris dans Impress et Draw.
- Corrigé : « Ouvrez d'abord un document » pouvait apparaître alors qu'un document était ouvert.
- Corrigé : après un arrêt, la phrase restait sélectionnée ; la lecture ou l'export suivant ne portait
  alors que sur elle, et une frappe pouvait la remplacer.
- Corrigé : des réglages changés rapidement pendant la lecture pouvaient se perdre sous Windows.
- Windows : toutes les voix installées sont disponibles, pas seulement les plus anciennes, avec un
  vrai réglage de la tonalité.
- Linux : Speech Dispatcher devient le moteur par défaut, et les variantes régionales comme pt-BR
  sont utilisées.
- Toutes les langues : les écritures asiatiques et complexes sont reconnues, et un texte russe, arabe
  ou japonais n'est plus confondu avec la langue du document.
- La langue vient désormais du texte, phrase par phrase, et non plus d'un réglage fixe.
- En mode automatique, le service vocal du système passe en premier, et un moteur est ignoré s'il n'a
  pas de voix pour l'une des langues du document. **Si vous utilisez Piper**, choisissez-le dans les
  paramètres.
- Les paramètres listent les voix installées.
- Un message prévient quand aucune voix n'existe pour une langue.
- Piper : chaque locuteur d'une voix à plusieurs locuteurs apparaît dans la liste des voix.
- LibreOffice 24.2 est désormais la version minimale.

### Licence

MIT. Voir [LICENSE](LICENSE).
