#!/usr/bin/env python3
"""Install the RPM on clean Fedora and launch a photo as an unprivileged user."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from display_session_results import parse_display_graphics


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "formats/core-matrix/jpeg/micro-1x1.jpg"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rpm", type=Path)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "build/qualification/rpm/install-smoke.json")
    args = parser.parse_args()
    package = args.rpm.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="licasa-rpm-verify-") as temporary:
        work = Path(temporary)
        shutil.copy2(package, work / "licasa.rpm")
        shutil.copy2(ROOT / "packaging/install-licasa-appimage.sh", work / "check-runtime.sh")
        (work / "Dockerfile").write_text(
            "FROM fedora:44\n"
            "COPY licasa.rpm /tmp/licasa.rpm\n"
            "COPY check-runtime.sh /opt/check-runtime.sh\n"
            "RUN dnf install -y --setopt=install_weak_deps=False "
            "/tmp/licasa.rpm && dnf clean all\n"
            "RUN bash -ec '. /opt/check-runtime.sh; licasa_check_runtime_dependencies "
            "/opt/licasa/usr/lib/licasa/lib:/opt/licasa/usr/lib /opt/licasa/usr'\n"
            "RUN dnf reinstall -y --setopt=install_weak_deps=False /tmp/licasa.rpm "
            "&& dnf remove -y --noautoremove licasa-appimage "
            "&& test ! -e /opt/licasa && test ! -e /usr/bin/licasa "
            "&& test ! -e /usr/share/applications/licasa.desktop "
            "&& test ! -e /usr/share/icons/hicolor/512x512/apps/licasa.png "
            "&& dnf install -y --setopt=install_weak_deps=False /tmp/licasa.rpm\n"
            "RUN dnf install -y --setopt=install_weak_deps=False desktop-file-utils xdg-utils "
            "xorg-x11-server-Xvfb xwininfo weston mesa-dri-drivers "
            "&& dnf clean all\n"
            "USER 65534:65534\n"
        )
        tag = "licasa-rpm-verify:fedora44"
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
                raise RuntimeError(f"Fedora RPM {name} failed:\n"
                                   f"{result.stdout}\n{result.stderr}")
            displays[name] = parse_display_graphics(result.stdout, context=f"Fedora RPM {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "fedora_version": 44,
        "rpm_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "clean_install": True,
        "runtime_checked_before_desktop_tools": True,
        "package_reinstall_and_removal_checked": True,
        "desktop_valid": True,
        "jpeg_default_selected": True,
        "viewer_launched": True,
        "display_sessions": displays,
    }, indent=2) + "\n")
    print(f"Fedora RPM installation passed: {args.output}")


if __name__ == "__main__":
    main()
