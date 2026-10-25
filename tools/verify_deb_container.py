#!/usr/bin/env python3
"""Install a .deb with apt in clean Ubuntu 24.04 and smoke its installed readers."""

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
SAMPLES = (
    "formats/core-matrix/jpeg/micro-1x1.jpg",
    "formats/core-matrix/png/micro-1x1.png",
    "formats/core-matrix/tiff/micro-1x1.tiff",
    "formats/core-matrix/webp/micro-1x1.webp",
    "formats/core-matrix/avif/micro-1x1.avif",
    "modern/jxl/full-hd.jxl",
    "modern/heif/with-alpha-512x512.heic",
    "formats/additional-probes/jpeg-2000.jp2",
)
FORMATS = ("jpeg", "png", "tiff", "webp", "avif", "jxl", "heif", "jp2")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/qualification/deb/install-smoke.json")
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    assets = ROOT / "tests/test-assets"
    for sample in SAMPLES:
        if not (assets / sample).is_file():
            parser.error(f"Missing repository sample: {sample}")
    with tempfile.TemporaryDirectory(prefix="licasa-deb-verify-") as temporary:
        work = Path(temporary)
        (work / "licasa.deb").write_bytes(package.read_bytes())
        shutil.copy2(ROOT / "packaging/install-licasa-appimage.sh", work / "installer.sh")
        (work / "Dockerfile").write_text(
            "FROM ubuntu:24.04\n"
            "COPY licasa.deb /tmp/licasa.deb\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "/tmp/licasa.deb "
            "&& dpkg-query -W -f='${Status}\\n' licasa "
            "| grep -Fx 'install ok installed' && rm -rf /var/lib/apt/lists/*\n"
            "COPY installer.sh /opt/licasa-installer.sh\n"
            # No desktop harness or undeclared runtime packages may help this check.
            "RUN bash -ec '. /opt/licasa-installer.sh; "
            "licasa_check_runtime_dependencies /usr/lib/licasa/lib "
            "/usr/bin/licasa /usr/bin/licasa_diagnostics /usr/lib/licasa "
            "/usr/lib/x86_64-linux-gnu/qt6/plugins "
            "/usr/lib/x86_64-linux-gnu/qt6/qml'\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "xvfb x11-utils weston libgl1-mesa-dri desktop-file-utils xdg-utils "
            "&& rm -rf /var/lib/apt/lists/*\n"
        )
        tag = "licasa-deb-verify:local"
        subprocess.run(["docker", "build", "--tag", tag, str(work)], check=True)
        expected_icon_hash = hashlib.sha256((ROOT / "assets/licasa.png").read_bytes()).hexdigest()
        icon_result = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", tag, "sh", "-ec",
             "grep -Fx 'Icon=licasa' /usr/share/applications/licasa.desktop >/dev/null; "
             "sha256sum /usr/share/pixmaps/licasa.png "
             "/usr/share/icons/hicolor/512x512/apps/licasa.png"],
            capture_output=True, text=True, check=True,
        )
        installed_icon_hashes = [line.split()[0] for line in icon_result.stdout.splitlines()]
        if installed_icon_hashes != [expected_icon_hash, expected_icon_hash]:
            raise RuntimeError("Installed launcher icon does not match assets/licasa.png")
        result = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "--tmpfs", "/tmp:rw,nosuid,nodev", "--user", "65534:65534",
             "-e", "QT_QPA_PLATFORM=offscreen", "-e", "QT_QUICK_BACKEND=software",
             "-e", "XDG_RUNTIME_DIR=/tmp/runtime", "-e", "XDG_CACHE_HOME=/tmp/cache",
             "-v", f"{assets}:/samples:ro", tag,
             "/usr/bin/licasa_diagnostics", "--formats", "--megapixels", "25",
             *(f"/samples/{sample}" for sample in SAMPLES)],
            capture_output=True, text=True, check=True,
        )
        report = json.loads(result.stdout)
        actual = {name.lower() for name in report["read_formats"]}
        missing = [name for name in FORMATS if name not in actual]
        failed = [sample["path"] for sample in report["samples"] if not sample["decoded"]]
        if missing or failed:
            raise RuntimeError(f"Missing reader formats: {missing}; failed installed decodes: {failed}")
        viewer = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "--tmpfs", "/tmp:rw,nosuid,nodev", "--user", "65534:65534",
             "-e", "QT_QPA_PLATFORM=offscreen", "-e", "QT_QUICK_BACKEND=software",
             "-e", "XDG_RUNTIME_DIR=relative", "-e", "XDG_CACHE_HOME=/tmp/cache",
             "-e", "XDG_STATE_HOME=/tmp/state",
             "-e", "XDG_CONFIG_HOME=/tmp/config", "-v", f"{assets}:/samples:ro", tag,
             "sh", "-ec", "status=0; timeout 12 /usr/bin/licasa \"$1\" "
             ">/tmp/viewer.log 2>&1 || status=$?; "
             "if [ \"$status\" -ne 124 ]; then cat /tmp/viewer.log; exit 1; fi; "
             "grep -Fq 'Single-instance IPC disabled' /tmp/state/licasa/errors.log",
             "_", f"/samples/{SAMPLES[0]}"],
            capture_output=True, text=True, timeout=35,
        )
        if viewer.returncode:
            raise RuntimeError(f"Installed viewer did not stay open:\n{viewer.stdout}\n{viewer.stderr}")
        displays = {}
        for protocol in ("x11", "wayland"):
            for mode in ("default", "software"):
                name = f"{protocol}_{mode}"
                display = subprocess.run(
                    ["docker", "run", "--rm", "--network", "none", "--read-only",
                     "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--shm-size", "256m",
                     "--user", "65534:65534", "--cap-drop", "ALL",
                     "-e", "HOME=/tmp/home", "-e", "XDG_CONFIG_HOME=/tmp/home/.config",
                     "-e", "XDG_RUNTIME_DIR=/tmp/runtime",
                     "-v", f"{ROOT / 'tools/check_display_session.sh'}:/opt/check_display_session.sh:ro",
                     "-v", f"{assets / SAMPLES[0]}:/samples/photo.jpg:ro",
                     tag, "sh", "/opt/check_display_session.sh", "/usr/bin/licasa",
                     "/usr/bin/licasa_diagnostics", "/samples/photo.jpg", protocol, mode],
                    capture_output=True, text=True, timeout=100,
                )
                if display.returncode:
                    raise RuntimeError(f"Ubuntu .deb {name} failed:\n"
                                       f"{display.stdout}\n{display.stderr}")
                displays[name] = parse_display_graphics(display.stdout, context=f"Ubuntu .deb {name}")
        report["display_sessions"] = displays
        report["runtime_dependencies_checked_before_desktop_tools"] = True
        report["package_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Clean Ubuntu apt install, viewer launch, and {len(SAMPLES)} decodes passed: {args.output}")


if __name__ == "__main__":
    main()
