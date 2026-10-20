#!/usr/bin/env python3
"""Install the Arch package on a clean image and launch a photo."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "formats/core-matrix/jpeg/micro-1x1.jpg"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "build/qualification/arch/install-smoke.json")
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="licasa-arch-verify-") as temporary:
        work = Path(temporary)
        shutil.copy2(package, work / "licasa.pkg.tar.zst")
        (work / "Dockerfile").write_text(
            "FROM archlinux:latest\n"
            "COPY licasa.pkg.tar.zst /tmp/licasa.pkg.tar.zst\n"
            "RUN pacman -Syu --noconfirm --needed desktop-file-utils xdg-utils "
            "xorg-server-xvfb xorg-xwininfo weston mesa "
            "&& pacman -U --noconfirm /tmp/licasa.pkg.tar.zst\n"
            "USER 65534:65534\n"
        )
        tag = "licasa-arch-package-verify:local"
        subprocess.run(["docker", "build", "--tag", tag, str(work)], check=True)
    setup = (
        "mkdir -p \"$HOME\" \"$XDG_CONFIG_HOME\"; "
        "desktop-file-validate /usr/share/applications/licasa.desktop; "
        "xdg-mime default licasa.desktop image/jpeg; "
        "test \"$(xdg-mime query default image/jpeg)\" = licasa.desktop; "
    )
    displays = {}
    for protocol in ("x11", "wayland"):
        for mode in ("default", "software"):
            name = f"{protocol}_{mode}"
            script = setup + (
                "sh /opt/check_display_session.sh /usr/bin/licasa "
                "/opt/licasa/usr/bin/licasa_diagnostics /samples/photo.jpg "
                f"{protocol} {mode}"
            )
            result = subprocess.run(
                ["docker", "run", "--rm", "--network", "none", "--read-only",
                 "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--shm-size", "256m",
                 "--user", "65534:65534", "--cap-drop", "ALL",
                 "-e", "HOME=/tmp/home", "-e", "XDG_CONFIG_HOME=/tmp/home/.config",
                 "-e", "XDG_DATA_HOME=/tmp/home/.local/share",
                 "-e", "XDG_RUNTIME_DIR=/tmp/runtime",
                 "-v", f"{ROOT / 'tools/check_display_session.sh'}:/opt/check_display_session.sh:ro",
                 "-v", f"{ROOT / 'tests/test-assets' / SAMPLE}:/samples/photo.jpg:ro",
                 tag, "sh", "-ec", script], capture_output=True, text=True, timeout=100)
            if result.returncode:
                raise RuntimeError(f"Arch package {name} failed:\n"
                                   f"{result.stdout}\n{result.stderr}")
            graphics = re.search(r"graphicsApi=([A-Za-z0-9]+)", result.stdout)
            fallback = re.search(r"diagnostic_fallback=(true|false); app_retry=(true|false)",
                                 result.stdout)
            if not graphics or not fallback:
                raise RuntimeError(f"Arch package {name} has incomplete results:\n"
                                   f"{result.stdout}")
            displays[name] = {"graphics_api": graphics.group(1),
                              "diagnostic_fallback": fallback.group(1) == "true",
                              "app_retry": fallback.group(2) == "true"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "distribution": "archlinux",
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "clean_install": True,
        "desktop_valid": True,
        "jpeg_default_selected": True,
        "viewer_launched": True,
        "display_sessions": displays,
    }, indent=2) + "\n")
    print(f"Arch package installation passed: {args.output}")


if __name__ == "__main__":
    main()
