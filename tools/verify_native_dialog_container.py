#!/usr/bin/env python3
"""Qualify a native package with its distro's GTK picker after pristine linkage checks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "debian-13": ("debian:trixie-slim", "6.8.2", "debian13-native-gtk"),
}
DESKTOP_PACKAGES = (
    "qt6-gtk-platformtheme", "python3", "python3-pyatspi", "python3-pil", "at-spi2-core", "dbus-x11",
    "mate-session-manager", "marco", "mate-settings-daemon", "mate-panel", "caja",
    "xvfb", "xauth", "xdotool", "x11-utils", "imagemagick", "fonts-dejavu-core", "libgl1-mesa-dri",
)


def build_recipe(target: str) -> str:
    base, _, _ = TARGETS[target]
    return (
        f"FROM {base}\nCOPY candidate.deb /tmp/candidate.deb\n"
        f"RUN test \"$(dpkg-deb --field /tmp/candidate.deb X-Licasa-Target)\" = {target} "
        # The disposable build root lacks SYS_PTRACE; its APT worker must stay
        # inspectable without weakening native production process validation.
        "&& apt-get -o APT::Sandbox::User=root update "
        "&& apt-get -o APT::Sandbox::User=root install -y --no-install-recommends /tmp/candidate.deb "
        "&& rm -rf /var/lib/apt/lists/*\n"
        "COPY installer.sh /opt/runtime-check.sh\n"
        # GTK/MATE may not mask missing declared package dependencies.
        "RUN bash -ec '. /opt/runtime-check.sh; licasa_check_runtime_dependencies "
        "/usr/lib/licasa/lib /usr/bin/licasa /usr/bin/licasa.bin /usr/bin/licasa_diagnostics /usr/lib/licasa "
        "/usr/lib/x86_64-linux-gnu/qt6/plugins /usr/lib/x86_64-linux-gnu/qt6/qml'\n"
        "RUN apt-get -o APT::Sandbox::User=root update && "
        "apt-get -o APT::Sandbox::User=root install -y --no-install-recommends "
        + " ".join(DESKTOP_PACKAGES) + " && rm -rf /var/lib/apt/lists/*\n"
    )


SESSION = r'''
set -eu
mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME"
chmod 700 "$HOME" "$XDG_RUNTIME_DIR"
python3 - <<'PY'
from PIL import Image, ImageDraw
image = Image.new('RGB', (480, 360))
draw = ImageDraw.Draw(image)
for index, color in enumerate(((238,32,32),(32,220,32),(32,64,238),(238,210,32))):
    x, y = index % 2 * 240, index // 2 * 180
    draw.rectangle((x, y, x+239, y+179), fill=color)
image.save('/tmp/native-photo.png')
PY
Xvfb :93 -ac -screen 0 1360x900x24 -nolisten tcp >/reports/xvfb.log 2>&1 &
xvfb=$!
mate=
cleanup() {
    [ -z "$mate" ] || kill "$mate" 2>/dev/null || true
    kill "$xvfb" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM
attempt=0
while ! xdotool getdisplaygeometry >/dev/null 2>&1 && [ "$attempt" -lt 50 ]; do
    sleep 0.1
    attempt=$((attempt+1))
done
xdotool getdisplaygeometry >/dev/null
dpkg-query -W > /reports/installed-packages.txt
/usr/bin/licasa_diagnostics --formats /tmp/native-photo.png >/reports/runtime-formats.json 2>/reports/runtime-formats.log
gsettings set org.mate.interface accessibility true
mate-session --failsafe --disable-acceleration-check >/reports/mate-session.log 2>&1 &
mate=$!
attempt=0
while ! pgrep -x marco >/dev/null && [ "$attempt" -lt 80 ]; do
    sleep 0.1
    attempt=$((attempt+1))
done
pgrep -x mate-session >/dev/null
pgrep -x marco >/dev/null
python3 /qualification/tools/verify_native_dialog_driver.py --app /usr/bin/licasa \
    --sample /tmp/native-photo.png --destination /reports/native-saved.png --output /reports/native.json
'''


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--target", choices=tuple(TARGETS), default="debian-13")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    output = args.output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = output.parent / f"{output.stem}-native-gtk"
    if raw.exists():
        parser.error("Use a fresh native GTK evidence directory")
    raw.mkdir()
    raw.chmod(0o777)
    _, qt_version, target_id = TARGETS[args.target]
    tag = f"licasa-native-gtk-{args.target}:qualification"
    report = {"schema_version": 1, "kind": "native-dialogs", "target_id": target_id, "status": "failed",
              "artifact_sha256": digest(package), "target": args.target, "desktop": "MATE", "protocol": "x11",
              "dialog_backend": "native-gtk", "scenarios": [], "native_confirmations": [],
              "runtime_dependencies_checked_before_desktop_tools": True,
              "coverage_limits": ["Native GTK selection is exercised in an actual MATE session on a virtual X11 display.",
                                  "Other native dialog providers, Wayland portals, physical graphics, and spoken screen-reader order require their own acceptance."]}
    try:
        recipe = build_recipe(args.target)
        (raw / "Dockerfile").write_text(recipe)
        if not args.skip_build:
            with tempfile.TemporaryDirectory(prefix="licasa-native-gtk-") as temporary:
                context = Path(temporary)
                shutil.copy2(package, context / "candidate.deb")
                if digest(context / "candidate.deb") != report["artifact_sha256"]:
                    raise RuntimeError("Candidate package changed while preparing native GTK qualification")
                shutil.copy2(ROOT / "packaging/install-licasa-appimage.sh", context / "installer.sh")
                (context / "Dockerfile").write_text(recipe)
                with (raw / "image-build.log").open("w") as log:
                    result = subprocess.run(["docker", "build", "--label", f"licasa.native.package.sha256={report['artifact_sha256']}",
                                             "--label", f"licasa.native.target={args.target}", "-t", tag, str(context)],
                                            stdout=log, stderr=subprocess.STDOUT, text=True, timeout=900)
                if result.returncode:
                    raise RuntimeError("Native GTK qualification image failed its pristine install or desktop setup")
        identity = subprocess.check_output(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], text=True, timeout=15)
        report["image_id"] = identity.strip()
        labels = json.loads(subprocess.check_output(
            ["docker", "image", "inspect", tag, "--format", "{{json .Config.Labels}}"], text=True, timeout=15)) or {}
        if labels.get("licasa.native.package.sha256") != report["artifact_sha256"] or labels.get("licasa.native.target") != args.target:
            raise RuntimeError("The cached native GTK image contains a different candidate; rebuild it")
        container_name = "licasa-native-gtk-" + uuid.uuid4().hex[:16]
        command = ["docker", "run", "--name", container_name, "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--shm-size", "256m", "--pids-limit", "256",
                   "--user", "65534:65534", "-e", "HOME=/tmp/home", "-e", "XDG_RUNTIME_DIR=/tmp/runtime",
                   "-e", "XDG_CONFIG_HOME=/tmp/config", "-e", "XDG_STATE_HOME=/tmp/state", "-e", "XDG_CACHE_HOME=/tmp/cache",
                   "-e", "DISPLAY=:93", "-e", "XDG_CURRENT_DESKTOP=MATE", "-e", "DESKTOP_SESSION=mate",
                   "-e", "QT_QPA_PLATFORM=xcb", "-e", "QT_QPA_PLATFORMTHEME=gtk3", "-e", "QT_DEBUG_PLUGINS=1",
                   "-e", "QT_QUICK_BACKEND=software", "-e", "LICASA_VIRTUAL_DESKTOP=1", "-e", "LC_ALL=C.UTF-8",
                   "-v", f"{ROOT / 'tools'}:/qualification/tools:ro", "-v", f"{raw}:/reports:rw",
                   tag, "sh", "-ec", 'mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"; exec dbus-run-session -- sh -ec "$1"', "_", SESSION]
        (raw / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        try:
            with (raw / "container.log").open("w") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=210)
        finally:
            # Terminating the client on timeout does not remove a running container.
            try:
                cleanup = subprocess.run(["docker", "rm", "-f", container_name], text=True,
                                         capture_output=True, timeout=20)
                (raw / "container-cleanup.log").write_text(cleanup.stdout + cleanup.stderr)
            except (OSError, subprocess.SubprocessError) as error:
                (raw / "container-cleanup.log").write_text(str(error) + "\n")
        report["exit_code"] = result.returncode
        if (raw / "runtime-formats.json").is_file():
            formats = json.loads((raw / "runtime-formats.json").read_text())
            report["qt_version"] = formats.get("qt_version")
            report["runtime"] = {"qt_version": formats.get("qt_version"), "process": formats.get("before_decode", {})}
        if not (raw / "native.json").is_file():
            raise RuntimeError("Native GTK driver did not retain its report")
        native = json.loads((raw / "native.json").read_text())
        report["native_report"] = native
        if "runtime" in report:
            report["runtime"]["process"] = native.get("process", {})
        report["scenarios"] = native.get("scenarios", [])
        report["native_confirmations"] = native.get("native_confirmations", [])
        if result.returncode or native.get("status") != "passed" or report.get("qt_version") != qt_version:
            raise RuntimeError(native.get("error", "Native GTK qualification did not pass its exact Qt profile"))
        from qualification_evidence import validate_native_dialogs
        from check_support_manifest import load_manifest
        manifest = load_manifest()
        target = next(row for row in manifest["targets"] if row["id"] == target_id)
        report["status"] = "passed"
        validate_native_dialogs(report, target, manifest)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
        return 1
    finally:
        report["evidence_directory"] = str(raw)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Native GTK qualification: {report['status']}; evidence {output}")


if __name__ == "__main__":
    raise SystemExit(main())
