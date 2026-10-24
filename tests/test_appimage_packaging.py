#!/usr/bin/env python3
"""Exercise AppImage assembly with fake deployment tools; no real package is built."""

import hashlib
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("appimage_packager", ROOT / "tools/package_appimage.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class AppImagePackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="licasa-appimage-assembly-fixture-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.libraries = self.work / "qt/lib"
        self.plugins = self.work / "qt/plugins"
        for name in ("libQt6WlShellIntegration.so.6", "libSM.so.6", "libICE.so.6", "libuuid.so.1", "libfribidi.so.0"):
            self.write(self.libraries / name, name.encode())
        for name in ("platforms/libqwayland-egl.so", "platforms/libqwayland-generic.so",
                     "wayland-decoration-client/decoration.so",
                     "wayland-graphics-integration-client/libqt-plugin-wayland-egl.so",
                     "wayland-shell-integration/libxdg-shell.so"):
            self.write(self.plugins / name)
        self.payload = self.work / "payload"
        for name in ("usr/bin/licasa", "usr/share/applications/licasa.desktop",
                     "usr/share/icons/hicolor/512x512/apps/licasa.png",
                     "usr/lib/licasa/lib/libQt6Quick.so.6", "usr/lib/libopenjp2.so.7",
                     "usr/lib/licasa/plugins/imageformats/liblicasa_jp2_plugin.so"):
            self.write(self.payload / name)
        self.package = self.work / "fixture.deb"
        self.write(self.package)
        self.output = self.work / "output"
        self.deployment_commands = []
        self.image_created = False
        self.omit_library = None

    def write(self, path, content=b"synthetic fixture"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def metadata(self, command, **kwargs):
        if command[0] == "dpkg-deb":
            return {"Package": "licasa", "Architecture": "amd64", "Version": "0.7.1-1"}[command[-1]]
        return str({"QT_INSTALL_PLUGINS": self.plugins, "QT_INSTALL_LIBS": self.libraries}[command[-1]])

    def run_tool(self, command, **kwargs):
        name = Path(command[0]).name
        if name == "dpkg-deb":
            shutil.copytree(self.payload, Path(command[-1]), dirs_exist_ok=True)
        elif name == "linuxdeploy-x86_64.AppImage":
            self.deployment_commands.append(command)
            appdir = Path(command[command.index("--appdir") + 1])
            for plugin in ("libqxcb.so", "libqoffscreen.so", "libqwayland-egl.so", "libqwayland-generic.so"):
                self.write(appdir / "usr/plugins/platforms" / plugin)
            self.write(appdir / "usr/lib/libQt6Quick.so.6")
            # Model the failure: excluded libraries are copied only when explicitly requested.
            for index, argument in enumerate(command):
                if argument == "--library":
                    source = Path(command[index + 1])
                    if source.name != self.omit_library:
                        shutil.copy2(source, appdir / "usr/lib" / source.name)
        elif name == "appimagetool-x86_64.AppImage":
            appdir = Path(command[-2])
            for library in ("libSM.so.6", "libICE.so.6", "libuuid.so.1", "libfribidi.so.0"):
                self.assertEqual((appdir / "usr/lib" / library).read_bytes(), library.encode())
            quick = appdir / "usr/lib/libQt6Quick.so.6"
            self.assertTrue(quick.is_symlink())
            self.assertEqual(quick.readlink(), Path("licasa/lib/libQt6Quick.so.6"))
            self.write(Path(command[-1]), b"synthetic image output")
            self.image_created = True
        else:
            self.fail(f"Unexpected tool request: {command}")

    def assemble(self):
        with patch.object(sys, "argv", ["package_appimage.py", str(self.package),
                                        "--output-dir", str(self.output)]), \
                patch.object(packager.shutil, "which", return_value="fixture-tool"), \
                patch.object(packager.subprocess, "check_output", side_effect=self.metadata), \
                patch.object(packager, "fetch_tools"), \
                patch.object(packager, "run", side_effect=self.run_tool):
            packager.main()

    def test_image_contains_excluded_runtime_libraries_and_private_qt_quick(self):
        self.assemble()
        self.assertTrue(self.image_created)
        image = self.output / "Licasa-0.7.1-x86_64.AppImage"
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        self.assertEqual(image.with_name(image.name + ".sha256").read_text(), f"{digest}  {image.name}\n")

    def test_missing_system_library_stops_before_deployment(self):
        (self.libraries / "libfribidi.so.0").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing AppImage runtime library: .*libfribidi"):
            self.assemble()
        self.assertFalse(self.deployment_commands)
        self.assertFalse(self.image_created)

    def test_incomplete_deployment_stops_before_image_creation(self):
        self.omit_library = "libICE.so.6"
        with self.assertRaisesRegex(RuntimeError, "AppDir is missing.*libICE.so.6"):
            self.assemble()
        self.assertFalse(self.image_created)


if __name__ == "__main__":
    unittest.main()
