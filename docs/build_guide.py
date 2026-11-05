#!/usr/bin/env python3
"""Print docs/contributor-guide.html to docs/Contributor-Guide.pdf.

Usage: python docs/build_guide.py
Needs Microsoft Edge or Google Chrome.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
SOURCE = DOCS / "contributor-guide.html"
TARGET = DOCS / "Contributor-Guide.pdf"
BROWSERS = ("msedge", "google-chrome", "chromium", "chrome",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def find_browser():
    for name in BROWSERS:
        path = shutil.which(name) or (name if Path(name).is_file() else None)
        if path:
            return path
    sys.exit("Microsoft Edge or Google Chrome is required.")


def main():
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([find_browser(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        "--user-data-dir=" + profile, "--print-to-pdf=" + str(TARGET),
                        SOURCE.as_uri()], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("%s (%d KB)" % (TARGET, TARGET.stat().st_size // 1024))


if __name__ == "__main__":
    main()
