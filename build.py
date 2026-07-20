"""
Build script for RAMDisplay.

Packages the application into a single standalone .exe using PyInstaller.
Usage:
    python build.py          # normal build, no version bump
    python build.py --bump   # bump patch version, then build
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import shutil

VERSION_FILE = "ramdisplay.py"
APP_NAME = "RAMDisplay"


def _read_version() -> str:
    with open(VERSION_FILE, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r'^VERSION\s*=\s*"(.+?)"', content, re.MULTILINE)
    if not m:
        raise RuntimeError("VERSION constant not found in ramdisplay.py")
    return m.group(1)


def _write_version(new_version: str) -> None:
    with open(VERSION_FILE, encoding="utf-8") as f:
        content = f.read()
    content = re.sub(
        r'^VERSION\s*=\s*".+?"',
        f'VERSION = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Version -> {new_version}")


def bump_version() -> str:
    """Bump the patch version (x.y.z -> x.y.(z+1)) and return new version."""
    current = _read_version()
    parts = [int(x) for x in current.split(".")]
    parts[-1] += 1
    new_version = ".".join(str(p) for p in parts)
    _write_version(new_version)
    return new_version


def build() -> None:
    """Run PyInstaller to create the standalone executable."""
    version = _read_version()
    print(f"Building {APP_NAME} v{version} ...")

    # Ensure PyInstaller is installed
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Clean previous build artifacts
    for path in ("build", "dist", f"{APP_NAME}.spec"):
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

    # Run PyInstaller
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--noconsole",
            "--name",
            APP_NAME,
            "--distpath",
            "dist",
            VERSION_FILE,
        ]
    )

    exe_path = os.path.join("dist", f"{APP_NAME}.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"  OK: {exe_path} ({size_mb:.1f} MB)")
    else:
        print(f"  ERROR: {exe_path} not found!")
        sys.exit(1)


def main() -> None:
    bump = "--bump" in sys.argv
    if bump:
        sys.argv.remove("--bump")
        new_ver = bump_version()
        print(f"Version bumped to {new_ver}")
    build()


if __name__ == "__main__":
    main()
