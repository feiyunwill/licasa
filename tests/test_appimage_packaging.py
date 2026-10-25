#!/usr/bin/env python3
"""Exercise AppImage assembly with fake deployment tools; no real package is built."""

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
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
        for name in ("libQt6WlShellIntegration.so.6", "libQt6WaylandEglClientHwIntegration.so.6",
                     "libSM.so.6", "libICE.so.6", "libuuid.so.1", "libfribidi.so.0"):
            self.write(self.libraries / name, name.encode())
        for name in ("platforms/libqwayland-egl.so", "platforms/libqwayland-generic.so",
                     "wayland-decoration-client/decoration.so",
                     "wayland-graphics-integration-client/libqt-plugin-wayland-egl.so",
                     "wayland-shell-integration/libxdg-shell.so"):
            self.write(self.plugins / name)
        self.payload = self.work / "payload"
        for name in ("usr/bin/licasa", "usr/share/applications/licasa.desktop",
                     "usr/share/icons/hicolor/512x512/apps/licasa.png",
                     "usr/lib/licasa/lib/libQt6Quick.so.6", "usr/lib/licasa/lib/libdav1d.so.7",
                     "usr/lib/libopenjp2.so.7",
                     "usr/lib/licasa/plugins/imageformats/liblicasa_jp2_plugin.so"):
            self.write(self.payload / name)
        self.package = self.work / "fixture.deb"
        self.write(self.package)
        self.output = self.work / "output"
        self.deployment_commands = []
        self.patched_helpers = {}
        self.image_created = False
        self.omit_library = None
        self.omit_dav1d = False
        self.deployed_dav1d = b"distro dav1d selected by dependency scan"

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
            if not self.omit_dav1d:
                self.write(appdir / "usr/lib/libdav1d.so.7", self.deployed_dav1d)
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
            dav1d = appdir / "usr/lib/libdav1d.so.7"
            self.assertTrue(dav1d.is_symlink())
            self.assertEqual(dav1d.readlink(), Path("licasa/lib/libdav1d.so.7"))
            self.assertEqual(dav1d.read_bytes(), (self.payload / "usr/lib/licasa/lib/libdav1d.so.7").read_bytes())
            self.assertEqual(set(self.patched_helpers), {
                "wayland-decoration-client/decoration.so",
                "wayland-graphics-integration-client/libqt-plugin-wayland-egl.so",
                "wayland-shell-integration/libxdg-shell.so",
            })
            self.assertEqual(set(self.patched_helpers.values()), {"$ORIGIN/../../lib:$ORIGIN"})
            self.assertTrue((appdir / "usr/lib/libQt6WaylandEglClientHwIntegration.so.6").is_file())
            self.write(Path(command[-1]), b"synthetic image output")
            self.image_created = True
        elif name == "patchelf":
            self.assertEqual(command[1], "--set-rpath")
            plugin = Path(command[-1])
            self.assertTrue(plugin.is_file())
            self.assertTrue(self.deployment_commands)
            self.assertFalse(self.image_created)
            self.patched_helpers["/".join(plugin.parts[-2:])] = command[2]
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


    def test_missing_wayland_egl_library_stops_before_deployment(self):
        (self.libraries / "libQt6WaylandEglClientHwIntegration.so.6").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing AppImage runtime library: .*WaylandEgl"):
            self.assemble()
        self.assertFalse(self.deployment_commands)
        self.assertFalse(self.image_created)

    def test_missing_deployed_wayland_egl_library_stops_before_image_creation(self):
        self.omit_library = "libQt6WaylandEglClientHwIntegration.so.6"
        with self.assertRaisesRegex(RuntimeError, "AppDir is missing.*WaylandEgl"):
            self.assemble()
        self.assertFalse(self.image_created)

    def test_missing_patchelf_fails_preflight(self):
        with patch.object(sys, "argv", ["package_appimage.py", str(self.package)]), \
                patch.object(packager.shutil, "which", side_effect=lambda name: None if name == "patchelf" else name), \
                patch.object(packager, "fetch_tools") as fetch:
            with self.assertRaises(SystemExit) as failure:
                packager.main()
        self.assertEqual(failure.exception.code, 2)
        fetch.assert_not_called()

    def test_dav1d_always_resolves_to_private_runtime_regardless_of_scan_selection(self):
        for deployed in (b"older distro dav1d", b"private dav1d found first"):
            with self.subTest(deployed=deployed):
                self.image_created = False
                self.deployed_dav1d = deployed
                self.assemble()
                self.assertTrue(self.image_created)

    def test_missing_private_dav1d_stops_before_image_creation(self):
        (self.payload / "usr/lib/licasa/lib/libdav1d.so.7").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing dav1d or its pinned private runtime"):
            self.assemble()
        self.assertFalse(self.image_created)

    def test_missing_deployed_dav1d_stops_before_image_creation(self):
        self.omit_dav1d = True
        with self.assertRaisesRegex(RuntimeError, "missing dav1d or its pinned private runtime"):
            self.assemble()
        self.assertFalse(self.image_created)


@unittest.skipUnless(shutil.which("cc") and shutil.which("patchelf"), "requires a C compiler and patchelf")
class WaylandPluginLoaderTests(unittest.TestCase):
    def test_helper_loads_its_bundled_dependency_without_loader_overrides(self):
        with tempfile.TemporaryDirectory(prefix="licasa-wayland-loader-fixture-") as temporary:
            work = Path(temporary)
            appdir = work / "AppDir with spaces"
            library_dir = appdir / "usr/lib"
            plugin_dir = appdir / "usr/plugins/wayland-graphics-integration-client"
            library_dir.mkdir(parents=True)
            plugin_dir.mkdir(parents=True)
            dependency_name = "liblicasa_wayland_loader_fixture.so.1"
            dependency_source = work / "dependency.c"
            dependency_source.write_text("int licasa_loader_fixture_value(void) { return 42; }\n")
            plugin_source = work / "plugin.c"
            plugin_source.write_text("extern int licasa_loader_fixture_value(void);\n"
                                     "int licasa_loader_fixture_result(void) { return licasa_loader_fixture_value(); }\n")
            subprocess.run(["cc", "-shared", "-fPIC", f"-Wl,-soname,{dependency_name}",
                            str(dependency_source), "-o", str(library_dir / dependency_name)],
                           check=True, capture_output=True)
            plugin = plugin_dir / "libfixture.so"
            subprocess.run(["cc", "-shared", "-fPIC", str(plugin_source), f"-L{library_dir}",
                            f"-l:{dependency_name}", "-o", str(plugin)], check=True, capture_output=True)
            env = {key: value for key, value in os.environ.items()
                   if key not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT")}
            command = [sys.executable, "-c", "import ctypes, sys; "
                       "plugin = ctypes.CDLL(sys.argv[1]); "
                       "assert plugin.licasa_loader_fixture_result() == 42", str(plugin)]
            baseline = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertNotEqual(baseline.returncode, 0)
            self.assertIn(dependency_name, baseline.stderr)
            packager.set_wayland_plugin_runpaths(appdir, ("wayland-graphics-integration-client",))
            fixed = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            runpath = subprocess.check_output(["patchelf", "--print-rpath", str(plugin)], text=True).strip()
            self.assertEqual(runpath, "$ORIGIN/../../lib:$ORIGIN")


if __name__ == "__main__":
    unittest.main()
