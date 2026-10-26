#!/usr/bin/env python3
"""Keep package interactions confined and reject missing or mismatched reports."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("snap_dialog_desktop", ROOT / "tools/verify_snap_desktop.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
import qt_runtime


class SnapDialogQualificationTests(unittest.TestCase):
    def test_activated_desktop_services_receive_the_selected_virtual_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            services = root / "dbus-1/services"
            services.mkdir(parents=True)
            receipt = root / "environment.json"
            service = root / "service.py"
            service.write_text('import json, os\nfrom pathlib import Path\n'
                'from gi.repository import Gio, GLib\n'
                'connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)\n'
                'connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", '
                '"org.freedesktop.DBus", "RequestName", '
                'GLib.Variant("(su)", ("org.example.LicasaDisplayTest", 0)), '
                'None, Gio.DBusCallFlags.NONE, 5000, None)\n'
                f'Path({str(receipt)!r}).write_text(json.dumps(dict(os.environ)))\n'
                'loop = GLib.MainLoop()\nGLib.timeout_add(100, loop.quit)\nloop.run()\n')
            (services / "org.example.LicasaDisplayTest.service").write_text(
                '[D-BUS Service]\nName=org.example.LicasaDisplayTest\n'
                f'Exec={sys.executable} {service}\n')
            probe = root / "probe.py"
            probe.write_text(
                'import os, subprocess, sys\n'
                f'sys.path.insert(0, {str(ROOT / "tools")!r})\n'
                'from verify_snap_desktop import configure_bus_desktop\n'
                'environment = dict(os.environ, DISPLAY=":93", XDG_RUNTIME_DIR="/run/user/1001", '
                'XDG_CURRENT_DESKTOP="KDE", XDG_SESSION_TYPE="wayland")\n'
                'configure_bus_desktop(environment, "wayland-93")\n'
                'subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", '
                '"--object-path", "/org/freedesktop/DBus", "--method", '
                '"org.freedesktop.DBus.StartServiceByName", "org.example.LicasaDisplayTest", "0"], '
                'capture_output=True, timeout=10)\n')
            environment = dict(os.environ, XDG_DATA_DIRS=str(root))
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
                environment.pop(key, None)
            result = subprocess.run(["dbus-run-session", "--", sys.executable, str(probe)],
                                    env=environment, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            received = json.loads(receipt.read_text())
            self.assertEqual(received["DISPLAY"], ":93")
            self.assertEqual(received["WAYLAND_DISPLAY"], "wayland-93")
            self.assertEqual(received["QT_QPA_PLATFORM"], "wayland")
            self.assertEqual(received["XDG_CURRENT_DESKTOP"], "KDE")

    def test_wayland_socket_uses_confined_interface_name_and_rejects_existing_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            name = desktop.wayland_socket(runtime)
            self.assertRegex(name, r"^wayland-[0-9]+$")
            with socket.socket(socket.AF_UNIX) as compositor:
                compositor.bind(str(runtime / name))
                with self.assertRaisesRegex(RuntimeError, "existing Wayland"):
                    desktop.wayland_socket(runtime)
            (runtime / name).unlink()
            (runtime / (name + ".lock")).touch()
            with self.assertRaisesRegex(RuntimeError, "existing Wayland"):
                desktop.wayland_socket(runtime)

    def test_all_desktop_workflows_install_the_actual_plasma_shell(self):
        for name in ("ci.yml", "qualify-candidate.yml", "qualify-packages.yml", "retry-verification.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text()
                kde = workflow.split('kde) sudo apt-get install', 1)[1].split(';;', 1)[0]
                self.assertRegex(kde, r"\bplasma-desktop\b")
                self.assertIn("qtwayland5", kde)

    def test_wrapper_preserves_arguments_and_never_executes_snap_binary_directly(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            app, diagnostic = desktop.confined_dialog_wrappers(output)
            fake_snap = output / "snap"
            arguments = output / "arguments.json"
            fake_snap.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                'open(os.environ["QUALIFICATION_ARGUMENTS"], "w").write(json.dumps(sys.argv[1:]))\n')
            fake_snap.chmod(0o755)
            environment = dict(os.environ, PATH=str(output) + os.pathsep + os.environ["PATH"],
                               QUALIFICATION_ARGUMENTS=str(arguments))
            photo = 'photo with spaces "quotes" and $literal.png'
            subprocess.run([str(app), photo], env=environment, check=True)
            self.assertEqual(json.loads(arguments.read_text()), ["run", "licasa", photo])
            subprocess.run([str(diagnostic), "--dialog-test", photo], env=environment, check=True)
            received = json.loads(arguments.read_text())
            self.assertEqual(received[:4], ["run", "--shell", "licasa", "-c"])
            self.assertIn('"$SNAP/usr/bin/licasa_diagnostics" "$@"', received[4])
            self.assertIn('exec "$SNAP/snap/command-chain/gpu-2404-wrapper" '
                          '"$SNAP/snap/command-chain/desktop-launch"', received[4])
            self.assertEqual(received[5:], ["_", "--dialog-test", photo])

    def test_diagnostic_initializes_desktop_chain_before_first_wayland_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, diagnostic = desktop.confined_dialog_wrappers(root)
            binaries = root / "bin"
            binaries.mkdir()
            snap = root / "payload"
            chain = snap / "snap/command-chain"
            chain.mkdir(parents=True)
            private_runtime = root / "runtime/snap.licasa"
            private_runtime.mkdir(parents=True)
            compositor_socket = root / "runtime/nested-wayland"
            receipt = root / "receipt.json"
            # snap --shell supplies confinement/environment but omits the
            # command-chain; the wrapper must explicitly initialize both parts.
            (binaries / "snap").write_text('#!/bin/sh\nset -eu\n'
                'test "$1 $2 $3" = "run --shell licasa"\nshift 3\nexec /bin/sh "$@"\n')
            (chain / "gpu-2404-wrapper").write_text('#!/bin/sh\nset -eu\n'
                'export TEST_GPU_CHAIN=initialized\nexec "$@"\n')
            (chain / "desktop-launch").write_text('#!/bin/sh\nset -eu\n'
                'test "$TEST_GPU_CHAIN" = initialized\n'
                'test -S "$XDG_RUNTIME_DIR/../$WAYLAND_DISPLAY"\n'
                'ln -s "../$WAYLAND_DISPLAY" "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"\nexec "$@"\n')
            app = snap / "usr/bin/licasa_diagnostics"
            app.parent.mkdir(parents=True)
            app.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                'from pathlib import Path\n'
                'assert (Path(os.environ["XDG_RUNTIME_DIR"]) / os.environ["WAYLAND_DISPLAY"]).is_socket()\n'
                'Path(os.environ["TEST_RECEIPT"]).write_text(json.dumps(sys.argv[1:]))\n')
            for script in (binaries / "snap", chain / "gpu-2404-wrapper", chain / "desktop-launch", app):
                script.chmod(0o755)
            arguments = ["--motion-photo-controls", "fixture with spaces $literal.jpg"]
            with socket.socket(socket.AF_UNIX) as compositor:
                compositor.bind(str(compositor_socket))
                subprocess.run([str(diagnostic), *arguments], check=True,
                    env=dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                             SNAP=str(snap), XDG_RUNTIME_DIR=str(private_runtime),
                             WAYLAND_DISPLAY=compositor_socket.name, TEST_RECEIPT=str(receipt)))
            self.assertEqual(json.loads(receipt.read_text()), arguments)

    def test_nonzero_or_skipped_harness_never_passes_desktop_qualification(self):
        for exit_code, status, protocol in ((139, "passed", "x11"), (0, "skipped", "x11"),
                                            (0, "passed", "wayland")):
            with self.subTest(exit_code=exit_code, status=status), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                def run(command, **kwargs):
                    report = Path(command[command.index("--output") + 1])
                    report.write_text(json.dumps({"status": status, "protocol": protocol,
                                                  "mode": "default", "dialog_backend": "fallback"}))
                    return SimpleNamespace(returncode=exit_code)
                with patch.object(desktop.subprocess, "run", side_effect=run):
                    with self.assertRaises(RuntimeError):
                        desktop.check_dialog_interactions(output, output / "photo.png", {}, protocol="x11", mode="default")

    def test_motion_uses_confined_launcher_in_same_desktop_with_both_rotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            environment = {"DISPLAY": ":93", "WAYLAND_DISPLAY": "nested", "XDG_RUNTIME_DIR": "/run/user/1001"}
            observed = []
            def installed(arguments, environment):
                observed.append((arguments, environment))
                return {"status": "passed", "protocol": arguments.protocol, "mode": arguments.mode,
                        "rotation_degrees": arguments.rotation_degrees, "qt_version": "6.4.2"}
            with patch.object(desktop, "installed_check", side_effect=installed):
                rows = desktop.check_motion_interactions(output, environment, "/snap/licasa/x1/usr",
                    protocol="wayland", mode="software")
            self.assertEqual([row["rotation_degrees"] for row in rows], [0, 90])
            self.assertEqual(len(observed), 2)
            for arguments, passed_environment in observed:
                self.assertIs(passed_environment, environment)
                self.assertIn('exec snap run --shell licasa', arguments.diagnostic.read_text())
                self.assertEqual(arguments.runtime_root, "/snap/licasa/x1/usr")
                self.assertTrue(arguments.fixture.is_file())
            self.assertTrue(all(row["fixture"]["embedded_custom_io"] for row in rows))

    def test_failed_or_mismatched_motion_session_cannot_pass(self):
        for result in ({"status": "skipped"}, {"status": "passed", "protocol": "x11", "mode": "software",
                        "rotation_degrees": 90, "qt_version": "6.4.2"}):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temporary:
                with patch.object(desktop, "installed_check", return_value=result):
                    with self.assertRaises(RuntimeError):
                        desktop.check_motion_interactions(Path(temporary), {}, "/snap/licasa/x1/usr",
                            protocol="x11", mode="software")


class SnapPrimeRuntimeTests(unittest.TestCase):
    def prime(self, root, missing=None):
        staged = root / "staged"
        libraries = staged / "usr/lib/x86_64-linux-gnu"
        qml = libraries / "qt6/qml"
        for module in qt_runtime.requirements()["modules"]:
            directory = qt_runtime.module_directory(qml, module["uri"])
            directory.mkdir(parents=True)
            plugins = module["plugins"]["6.4.2"]
            (directory / "qmldir").write_text(f"module {module['uri']}\n" +
                "".join(f"optional plugin {name}\n" for name in plugins))
            for plugin in plugins:
                (directory / f"lib{plugin}.so").write_bytes(b"staged QML plugin")
            for library in module.get("libraries", []):
                (libraries / library).write_bytes(b"staged companion library")
        if missing:
            shutil.rmtree(qt_runtime.module_directory(qml, missing))
        # craftctl's normal prime operation merges runtime stage-packages into
        # the application payload. Neither source-build nor deb-input installs
        # contains those shared QML files before this operation.
        prime = root / "prime"
        prime.mkdir()
        binaries = root / "bin"
        binaries.mkdir()
        craftctl = binaries / "craftctl"
        craftctl.write_text('#!/bin/sh\nset -eu\ntest "$1" = default\n'
                            'cp -a "$TEST_STAGED/." "$CRAFT_PRIME/"\n')
        craftctl.chmod(0o755)
        recipe = (ROOT / "snap/snapcraft.yaml").read_text()
        hook = re.search(r"(?m)^    override-prime: \|\n((?:^      .*\n|^\n)+)", recipe)
        self.assertIsNotNone(hook, "Snap prime must validate its staged runtime")
        result = subprocess.run(["/bin/sh", "-c", textwrap.dedent(hook[1])],
            env=dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                     CRAFT_PROJECT_DIR=str(ROOT), CRAFT_PRIME=str(prime),
                     CRAFT_ARCH_TRIPLET_BUILD_FOR="x86_64-linux-gnu", TEST_STAGED=str(staged)),
            text=True, capture_output=True)
        return result, prime / "usr/share/licasa/qt-runtime-inventory.json"

    def test_complete_merged_prime_records_hashed_runtime_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, inventory = self.prime(Path(temporary))
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(inventory.read_text())
            self.assertEqual(report["qt_version"], "6.4.2")
            self.assertEqual(len(report["modules"]), len(qt_runtime.requirements()["modules"]))
            self.assertTrue(all(re.fullmatch(r"[a-f0-9]{64}", digest)
                                for module in report["modules"] for digest in module["files"].values()))

    def test_missing_fallback_or_basic_style_blocks_final_snap(self):
        for missing in ("Qt.labs.folderlistmodel", "QtQuick.Controls.Basic", "QtQuick.Controls.Basic.impl"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                result, inventory = self.prime(Path(temporary), missing)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Missing QML runtime file", result.stderr)
                self.assertFalse(inventory.exists())


if __name__ == "__main__":
    unittest.main()
