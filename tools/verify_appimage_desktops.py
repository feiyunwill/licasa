#!/usr/bin/env python3
"""Smoke the release AppImage on Arch with virtual X11 and Wayland displays."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from display_session_results import parse_display_graphics
from verify_installed_release import SAMPLES
from package_dialog_checks import run_dialog_checks
from package_motion_checks import run_motion_checks


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "licasa-appimage-arch-verify:local"
SAMPLE = "formats/core-matrix/jpeg/micro-1x1.jpg"
DECODE_SAMPLES = {**SAMPLES, "jp2": "formats/additional-probes/jpeg-2000.jp2"}
SETUP = """
mkdir -m 700 /tmp/runtime
export XDG_RUNTIME_DIR=/tmp/runtime
export XDG_CACHE_HOME=/tmp/cache
export XDG_CONFIG_HOME=/tmp/config
export XDG_DATA_HOME=/tmp/home/.local/share
export HOME=/tmp/home
cp "$1" '/tmp/photo with spaces.jpg'
mkdir -p "$HOME" "$XDG_CONFIG_HOME"
/opt/install-licasa-appimage.sh /opt/Licasa.AppImage
desktop-file-validate "$XDG_DATA_HOME/applications/licasa-appimage.desktop"
xdg-mime default licasa-appimage.desktop image/jpeg
test "$(xdg-mime query default image/jpeg)" = licasa-appimage.desktop
cd /tmp
/opt/Licasa.AppImage --appimage-extract >/dev/null
"""


def run_display_check(name: str, script: str, sample: Path,
                      checks: dict[str, object]) -> None:
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--shm-size", "256m",
        "--user", "65534:65534", "--cap-drop", "ALL",
        "-v", f"{ROOT / 'packaging/install-licasa-appimage.sh'}:/opt/install-licasa-appimage.sh:ro",
        "-v", f"{ROOT / 'tools/check_display_session.sh'}:/opt/check_display_session.sh:ro",
        "-v", f"{sample}:/samples/photo.jpg:ro",
        IMAGE, "sh", "-ec", script, "_", "/samples/photo.jpg",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=100)
    if result.returncode:
        raise RuntimeError(f"{name} failed:\n{result.stdout}\n{result.stderr}")
    checks[name] = {"passed": True, **parse_display_graphics(result.stdout, context=name)}
    print(result.stdout.strip(), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "build/qualification/appimage/desktop-smoke.json")
    args = parser.parse_args()
    appimage = args.appimage.resolve(strict=True)
    sample = ROOT / "tests/test-assets" / SAMPLE
    assets = ROOT / "tests/test-assets"
    if any(not (assets / path).is_file() for path in DECODE_SAMPLES.values()):
        parser.error("Repository release samples are missing")

    with tempfile.TemporaryDirectory(prefix="licasa-arch-appimage-") as temporary:
        work = Path(temporary)
        shutil.copy2(ROOT / "packaging/verify-appimage-arch.Dockerfile", work / "Dockerfile")
        shutil.copy2(appimage, work / "Licasa.AppImage")
        shutil.copy2(ROOT / "packaging/install-licasa-appimage.sh", work / "install-licasa-appimage.sh")
        shutil.copy2(ROOT / "tools/check_appimage_install.sh", work / "check_appimage_install.sh")
        subprocess.run(["docker", "build", "--tag", IMAGE, str(work)], check=True)
    checks = {}
    for protocol in ("x11", "wayland"):
        for mode in ("default", "software"):
            name = f"arch_{protocol}_{mode}_frame"
            script = SETUP + (
                "sh /opt/check_display_session.sh "
                '"$HOME/.local/opt/licasa-appimage/AppRun" '
                "/tmp/squashfs-root/usr/bin/licasa_diagnostics "
                f"'/tmp/photo with spaces.jpg' {protocol} {mode}"
            )
            run_display_check(name, script, sample, checks)

    decode_script = (
        "cd /tmp; /opt/Licasa.AppImage --appimage-extract >/dev/null; "
        "appdir=/tmp/squashfs-root; "
        "export LD_LIBRARY_PATH=\"$appdir/usr/lib/licasa/lib:$appdir/usr/lib\"; "
        "export QT_PLUGIN_PATH=\"$appdir/usr/plugins:$appdir/usr/lib/licasa/plugins\"; "
        "export QML2_IMPORT_PATH=\"$appdir/usr/qml\"; "
        "export QML_IMPORT_PATH=\"$QML2_IMPORT_PATH\"; "
        "exec \"$appdir/usr/bin/licasa_diagnostics\" --formats --megapixels 25 \"$@\""
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--user", "65534:65534",
         "--cap-drop", "ALL", "-e", "QT_QPA_PLATFORM=offscreen",
         "-e", "XDG_RUNTIME_DIR=/tmp/runtime", "-e", "XDG_CACHE_HOME=/tmp/cache",
         "-v", f"{assets}:/samples:ro", IMAGE, "sh", "-ec", decode_script, "_",
         *(f"/samples/{path}" for path in DECODE_SAMPLES.values())],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode:
        raise RuntimeError(f"Arch image decoders failed:\n{result.stdout}\n{result.stderr}")
    report = json.loads(result.stdout)
    missing = set(DECODE_SAMPLES) - {name.lower() for name in report["read_formats"]}
    failed = [item["path"] for item in report["samples"] if not item["decoded"]]
    if missing or failed:
        raise RuntimeError(f"Arch missing readers {sorted(missing)} or failed files {failed}")
    for item in report["samples"]:
        for library in item["process"]["mapped_optional_libraries"]:
            if not library.startswith("/tmp/squashfs-root/"):
                raise RuntimeError(f"Arch codec escaped the AppImage bundle: {library}")
    checks["arch_image_decoders"] = len(DECODE_SAMPLES)
    print(f"Arch: decoded {len(DECODE_SAMPLES)} image formats", flush=True)

    dependency_script = (
        "cd /tmp; /opt/Licasa.AppImage --appimage-extract >/dev/null; "
        ". /opt/install-licasa-appimage.sh; licasa_check_runtime_dependencies "
        "/tmp/squashfs-root/usr/lib/licasa/lib:/tmp/squashfs-root/usr/lib "
        "/tmp/squashfs-root/usr"
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--user", "65534:65534",
         "--cap-drop", "ALL",
         IMAGE, "bash", "-ec", dependency_script],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode:
        raise RuntimeError(f"Arch missing bundled dependencies:\n{result.stdout}\n{result.stderr}")
    checks["arch_elf_dependencies"] = True
    print("Arch: all packaged ELF dependencies resolved", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    dialogs = run_dialog_checks(
        IMAGE, output_dir=args.output.parent / "dialog-artifacts", app="/tmp/squashfs-root/AppRun",
        diagnostic="/tmp/squashfs-root/usr/bin/licasa_diagnostics", sample=sample,
        setup="cd /tmp; /opt/Licasa.AppImage --appimage-extract >/dev/null;")
    args.output.write_text(json.dumps({
        "appimage_sha256": hashlib.sha256(appimage.read_bytes()).hexdigest(),
        "test_image": IMAGE,
        "runtime_checked_before_desktop_tools": True,
        "appimage_lifecycle_checked_before_desktop_tools": True,
        "checks": checks,
        "dialog_interactions": dialogs,
        "motion_photo": run_motion_checks(
            IMAGE, diagnostic="/tmp/squashfs-root/usr/bin/licasa_diagnostics",
            runtime_root="/tmp/squashfs-root/usr", output_dir=args.output.parent / "motion-artifacts",
            setup="cd /tmp; /opt/Licasa.AppImage --appimage-extract >/dev/null;"),
    }, indent=2) + "\n")
    print(f"Arch desktop checks passed: {args.output}")


if __name__ == "__main__":
    main()
