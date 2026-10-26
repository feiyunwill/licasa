#!/usr/bin/env python3
"""Compare bundled GTK and Qt Quick pickers in disposable MATE/X11 sessions.

The desktop image needs the packages in verify_native_dialog_container plus
Openbox. This checks a mounted AppImage, never the image's installed Licasa.
Pristine-userspace linkage is qualified separately by verify_appimage_container.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
SESSION = r'''
set -eu
mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME"
chmod 700 "$HOME" "$XDG_RUNTIME_DIR"
cd /tmp
# Downloaded artifacts may lack executable permission. Stage an executable
# copy in disposable storage while preserving the read-only mounted candidate.
cp /candidate.AppImage /tmp/Licasa.AppImage
chmod 755 /tmp/Licasa.AppImage
/tmp/Licasa.AppImage --appimage-extract >/reports/extract.log
if [ "$LICASA_PICKER_REMOVE_NATIVE" = 1 ]; then
    rm /tmp/squashfs-root/usr/plugins/platformthemes/libqgtk3.so
fi
python3 - <<'PY'
from PIL import Image, ImageDraw
image = Image.new('RGB', (480, 360))
draw = ImageDraw.Draw(image)
for index, color in enumerate(((238,32,32),(32,220,32),(32,64,238),(238,210,32))):
    x, y = index % 2 * 240, index // 2 * 180
    draw.rectangle((x, y, x+239, y+179), fill=color)
image.save('/tmp/photo with spaces café.png')
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
    sleep 0.1; attempt=$((attempt+1))
done
xdotool getdisplaygeometry >/dev/null
gsettings set org.mate.interface accessibility true
gsettings set org.mate.interface gtk-theme "$LICASA_PICKER_TEST_THEME"
mate-session --failsafe --disable-acceleration-check >/reports/mate-session.log 2>&1 &
mate=$!
attempt=0
while ! pgrep -x marco >/dev/null && [ "$attempt" -lt 80 ]; do
    sleep 0.1; attempt=$((attempt+1))
done
pgrep -x mate-session >/dev/null
pgrep -x marco >/dev/null
gsettings get org.mate.interface gtk-theme >/reports/mate-theme.txt
exec "$@"
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    parser.add_argument("--image", default="licasa-native-gtk-debian-13:qualification")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--theme", choices=("Adwaita", "Adwaita-dark"), default="Adwaita")
    parser.add_argument("--backend", choices=("native", "fallback"), default="native")
    parser.add_argument("--without-native-plugin", action="store_true", help="Remove the extracted GTK plugin for fallback acceptance.")
    args = parser.parse_args()
    if args.without_native_plugin and args.backend != "fallback":
        parser.error("--without-native-plugin requires --backend fallback")
    appimage = args.appimage.resolve(strict=True)
    output = args.output.absolute()
    raw = output.with_suffix("")
    if raw.exists():
        parser.error("Use a fresh evidence directory")
    raw.mkdir(parents=True)
    raw.chmod(0o777)
    report = {"status": "failed", "kind": "appimage-picker", "desktop": "MATE", "protocol": "x11",
              "theme": args.theme, "backend": args.backend,
              "native_plugin_removed": args.without_native_plugin,
              "coverage_limits": ["Virtual MATE/X11; other desktops, Wayland native dialogs and spoken Orca acceptance are separate checks.",
                                  "Pristine-userspace dependency checks are separate from this desktop interaction run."]}
    container = "licasa-appimage-picker-" + uuid.uuid4().hex[:16]
    with appimage.open("rb") as stream:
        report["appimage_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        report["image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True, timeout=15).strip()
        if args.backend == "native":
            driver = ["python3", "/qualification/tools/verify_native_dialog_driver.py", "--app", "/tmp/squashfs-root/AppRun",
                      "--sample", "/tmp/photo with spaces café.png", "--destination", "/reports/saved photo café.png",
                      "--output", "/reports/interaction.json", "--bundle-root", "/tmp/squashfs-root"]
        else:
            driver = ["python3", "/qualification/tools/verify_dialog_interactions.py", "--app", "/tmp/squashfs-root/AppRun",
                      "--diagnostic", "/tmp/squashfs-root/usr/bin/licasa_diagnostics", "--sample", "/tmp/photo with spaces café.png",
                      "--output", "/reports/interaction.json", "--protocol", "x11", "--mode", "software",
                      "--dialog-backend", "fallback", "--preserve-desktop-runtime"]
        command = ["docker", "run", "--name", container, "--rm", "--network", "none", "--read-only",
                   "--cap-drop", "ALL", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--shm-size", "256m",
                   "--pids-limit", "256", "--user", "65534:65534",
                   "-e", "HOME=/tmp/home", "-e", "XDG_RUNTIME_DIR=/tmp/runtime", "-e", "XDG_CONFIG_HOME=/tmp/config",
                   "-e", "XDG_STATE_HOME=/tmp/state", "-e", "XDG_CACHE_HOME=/tmp/cache", "-e", "DISPLAY=:93",
                   "-e", "XDG_CURRENT_DESKTOP=MATE", "-e", "DESKTOP_SESSION=mate", "-e", "QT_QPA_PLATFORM=xcb",
                   "-e", "QT_DEBUG_PLUGINS=1", "-e", "QT_QUICK_BACKEND=software", "-e", "LICASA_VIRTUAL_DESKTOP=1",
                   "-e", "LC_ALL=C.UTF-8", "-e", f"LICASA_PICKER_TEST_THEME={args.theme}",
                   "-e", f"LICASA_PICKER_REMOVE_NATIVE={int(args.without_native_plugin)}",
                   "-v", f"{appimage}:/candidate.AppImage:ro", "-v", f"{ROOT / 'tools'}:/qualification/tools:ro",
                   "-v", f"{raw}:/reports:rw", report["image_id"], "sh", "-ec",
                   'mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"; exec dbus-run-session -- sh -ec "$1" _ ' + shlex.join(driver),
                   "_", SESSION]
        (raw / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (raw / "container.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=210)
        interaction = json.loads((raw / "interaction.json").read_text())
        report["interaction"] = interaction
        if result.returncode or interaction.get("status") != "passed":
            raise RuntimeError(interaction.get("error", "AppImage picker interaction failed"))
        report["status"] = "passed"
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        return 1
    finally:
        try:
            cleanup = subprocess.run(["docker", "rm", "-f", container], text=True, capture_output=True, timeout=20)
            (raw / "container-cleanup.log").write_text(cleanup.stdout + cleanup.stderr)
        except (OSError, subprocess.SubprocessError) as error:
            (raw / "container-cleanup.log").write_text(str(error))
        report["evidence_directory"] = str(raw)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"AppImage {args.backend} picker / {args.theme}: {report['status']}; {output}")


if __name__ == "__main__":
    raise SystemExit(main())
