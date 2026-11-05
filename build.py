#!/usr/bin/env python3
"""Build dist/LectureVocale-<version>.oxt from the extension/ folder.

Usage: python build.py
Needs nothing but Python 3.8 or later.
"""

import ast
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "extension"
DIST = ROOT / "dist"
GUIDE = ROOT / "docs" / "Contributor-Guide.pdf"
DESCRIPTION_NS = "{http://openoffice.org/extensions/description/2006}"
MANIFEST_NS = "{http://openoffice.org/2001/manifest}"


def extension_files():
    return sorted(path for path in SOURCE.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts)


def read_version():
    root = ElementTree.parse(SOURCE / "description.xml").getroot()
    return root.find(DESCRIPTION_NS + "version").get("value")


def check_sources(files):
    """Stop the build on anything that would only fail inside LibreOffice."""
    for path in files:
        if path.suffix in (".xml", ".xcu"):
            ElementTree.parse(path)
        elif path.suffix == ".py":
            # LibreOffice 24.2 embeds Python 3.8.
            ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 8))
    if not GUIDE.is_file():
        sys.exit("Missing guide, run: python docs/build_guide.py")
    manifest = ElementTree.parse(SOURCE / "META-INF" / "manifest.xml").getroot()
    for entry in manifest:
        listed = entry.get(MANIFEST_NS + "full-path")
        if not (SOURCE / listed).is_file():
            sys.exit("manifest.xml lists a missing file: " + listed)


def build():
    files = extension_files()
    check_sources(files)
    DIST.mkdir(exist_ok=True)
    target = DIST / ("LectureVocale-%s.oxt" % read_version())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(SOURCE).as_posix())
        archive.write(ROOT / "LICENSE", "LICENSE")
        archive.write(GUIDE, "docs/" + GUIDE.name)
    print("%s (%d files, %d KB)" % (target, len(files) + 2, target.stat().st_size // 1024))
    return target


if __name__ == "__main__":
    build()
