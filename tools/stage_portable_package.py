#!/usr/bin/env python3
"""Stage the qualified AppImage as a FUSE-free system package payload."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    appimage = args.appimage.resolve(strict=True)
    destination = args.destination.resolve()
    if not appimage.name.startswith("Licasa-") or not appimage.name.endswith("-x86_64.AppImage"):
        parser.error("Expected a versioned x86_64 Licasa AppImage")
    if destination.exists() and any(destination.iterdir()):
        parser.error("Destination must be empty")

    with tempfile.TemporaryDirectory(prefix="licasa-portable-") as temporary:
        work = Path(temporary)
        runnable = work / appimage.name
        shutil.copy2(appimage, runnable)
        runnable.chmod(0o755)
        subprocess.run([str(runnable), "--appimage-extract"], cwd=work, check=True,
                       stdout=subprocess.DEVNULL)
        extracted = work / "squashfs-root"
        desktop = extracted / "usr/share/applications/licasa.desktop"
        icon = extracted / "usr/share/icons/hicolor/512x512/apps/licasa.png"
        for required in (extracted / "AppRun", extracted / "usr/bin/licasa", desktop, icon):
            if not required.is_file():
                raise RuntimeError(f"AppImage is missing {required.relative_to(extracted)}")
        shutil.copytree(extracted, destination / "opt/licasa", symlinks=True)
        binary = destination / "usr/bin/licasa"
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nexec /opt/licasa/AppRun "$@"\n')
        binary.chmod(0o755)
        system_desktop = destination / "usr/share/applications/licasa.desktop"
        system_desktop.parent.mkdir(parents=True)
        shutil.copy2(desktop, system_desktop)
        system_icon = destination / "usr/share/icons/hicolor/512x512/apps/licasa.png"
        system_icon.parent.mkdir(parents=True)
        shutil.copy2(icon, system_icon)
        license_file = extracted / "usr/share/licenses/licasa/LICENSE"
        if not license_file.is_file():
            raise RuntimeError("AppImage is missing the first-party license")
        system_license = destination / "usr/share/licenses/licasa-appimage/LICENSE"
        system_license.parent.mkdir(parents=True)
        shutil.copy2(license_file, system_license)


if __name__ == "__main__":
    main()
