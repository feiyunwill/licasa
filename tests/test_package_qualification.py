#!/usr/bin/env python3
"""Test qualification assertions with synthetic images, exit codes and source recipes."""

import ast
from contextlib import redirect_stderr
import importlib.util
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("snap_desktop", ROOT / "tools/verify_snap_desktop.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
inspection_spec = importlib.util.spec_from_file_location(
    "package_inspection", ROOT / "tools/inspect_package_dependencies.py")
inspection = importlib.util.module_from_spec(inspection_spec)
inspection_spec.loader.exec_module(inspection)


def fixture(colors=desktop.PHOTO_COLORS):
    image = Image.new("RGB", (640, 480), "black")
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(colors):
        x, y = 80 + index % 2 * 240, 60 + index // 2 * 180
        draw.rectangle((x, y, x + 239, y + 179), fill=color)
    return image


class VisiblePhotoTests(unittest.TestCase):
    def test_visible_fixture_is_detected(self):
        self.assertTrue(desktop.pattern_present(fixture()))

    def test_blank_screen_is_not_a_visible_photo(self):
        self.assertFalse(desktop.pattern_present(Image.new("RGB", (640, 480), "black")))

    def test_wrong_quadrant_arrangement_is_rejected(self):
        self.assertFalse(desktop.pattern_present(fixture(tuple(reversed(desktop.PHOTO_COLORS)))))

    def test_one_missing_quadrant_is_rejected(self):
        colors = (*desktop.PHOTO_COLORS[:3], (0, 0, 0))
        self.assertFalse(desktop.pattern_present(fixture(colors)))

    def test_small_color_conversion_differences_are_tolerated(self):
        colors = tuple(tuple(channel + 10 for channel in color) for color in desktop.PHOTO_COLORS)
        self.assertTrue(desktop.pattern_present(fixture(colors)))

    def test_small_desktop_icons_are_not_a_visible_photo(self):
        self.assertFalse(desktop.pattern_present(fixture().resize((64, 48))))


class HostedExecutionGuardTests(unittest.TestCase):
    def test_local_desktop_check_is_rejected_before_starting_processes(self):
        with patch.dict(os.environ, RUNNER_ENVIRONMENT="local", GITHUB_ACTIONS="false"), \
                patch.object(sys, "argv", ["verify_snap_desktop.py", "--desktop", "gnome",
                                           "--version", "0.7.0", "--output", "unused"]), \
                patch.object(desktop.subprocess, "run") as run, \
                patch.object(desktop.subprocess, "Popen") as popen, \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                desktop.main()
            self.assertEqual(raised.exception.code, 2)
            run.assert_not_called()
            popen.assert_not_called()


class StaticPackageInspectionTests(unittest.TestCase):
    def superblock(self):
        data = bytearray(96)
        data[:4] = b"hsqs"
        struct.pack_into("<I", data, 12, 131072)
        struct.pack_into("<HH", data, 28, 4, 0)
        struct.pack_into("<Q", data, 40, 96)
        return bytes(data)

    def test_embedded_filesystem_offset_is_found_without_executing_appimage(self):
        prefix = b"an ELF runtime with incidental hsqs text"
        self.assertEqual(inspection.squashfs_offset(prefix + self.superblock()), len(prefix))

    def test_ambiguous_or_truncated_filesystems_are_rejected(self):
        for data in (b"hsqs", self.superblock() + self.superblock(), b"not an AppImage"):
            with self.subTest(data=data[:16]):
                with self.assertRaises(ValueError):
                    inspection.squashfs_offset(data)

    def test_checksum_mismatch_stops_before_extracting_or_inspecting(self):
        with tempfile.TemporaryDirectory(prefix="licasa-package-hash-fixture-") as temporary:
            package = Path(temporary) / "fixture.deb"
            package.write_bytes(b"package fixture")
            with patch.object(inspection.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                    inspection.inspect(package, "0" * 64, Path(temporary) / "report.json")
                run.assert_not_called()


class SnapShutdownTests(unittest.TestCase):
    def test_failed_or_timed_out_quit_still_cleans_up(self):
        for failure in (SimpleNamespace(returncode=1), subprocess.TimeoutExpired("snap", 15)):
            with self.subTest(failure=failure), \
                    patch.object(desktop.subprocess, "run") as run, \
                    patch.object(desktop, "stop") as stop, \
                    patch.object(desktop, "licasa_processes", return_value=[]):
                if isinstance(failure, Exception):
                    run.side_effect = failure
                else:
                    run.return_value = failure
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                    desktop.close_snap("test process", {})
                stop.assert_called_once_with("test process")

    def test_successful_quit_waits_for_shutdown(self):
        with patch.object(desktop.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                patch.object(desktop, "stop") as stop, \
                patch.object(desktop, "licasa_processes", return_value=[]):
            desktop.close_snap("test process", {})
            stop.assert_called_once_with("test process")


class DependencyRecipeTests(unittest.TestCase):
    def test_library_checks_precede_desktop_tool_installation(self):
        for name in ("verify_deb_container.py", "verify_appimage_container.py"):
            with self.subTest(tool=name):
                tree = ast.parse((ROOT / "tools" / name).read_text())
                names = {"args": SimpleNamespace(ubuntu_version="24.04")}
                for node in tree.body:
                    if (isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == "HOST_RUNTIME_PACKAGES"
                                    for target in node.targets)):
                        names["HOST_RUNTIME_PACKAGES"] = ast.literal_eval(node.value)
                calls = [node for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and node.func.attr == "write_text" and isinstance(node.func.value, ast.BinOp)
                         and isinstance(node.func.value.right, ast.Constant)
                         and node.func.value.right.value == "Dockerfile"]
                self.assertEqual(len(calls), 1)
                recipe = eval(compile(ast.Expression(calls[0].args[0]), name, "eval"),
                              {"__builtins__": {}}, names)
                lines = recipe.splitlines()
                check = next(index for index, line in enumerate(lines)
                             if line.startswith("RUN ") and "licasa_check_runtime_dependencies" in line)
                tools = next(index for index, line in enumerate(lines)
                             if line.startswith("RUN ") and "xvfb" in line)
                self.assertLess(check, tools)
                self.assertFalse(any("weston" in line or "xvfb" in line or "desktop-file-utils" in line
                                     for line in lines[:check]))
                for line in lines:
                    if line.startswith("RUN "):
                        subprocess.run(["bash", "-n"], input=line[4:], text=True, check=True)


class CTestSkipReportingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ctest"), "CTest is needed to check exit-code classification")
    def test_unavailable_is_skipped_and_failures_remain_failures(self):
        with tempfile.TemporaryDirectory(prefix="licasa-ctest-status-fixture-") as temporary:
            work = Path(temporary)
            # CTest only runs these tiny Python fixtures, never application tests or a build.
            (work / "CTestTestfile.cmake").write_text(
                f'add_test(unavailable "{sys.executable}" "-c" "raise SystemExit(77)")\n'
                'set_tests_properties(unavailable PROPERTIES SKIP_RETURN_CODE 77)\n'
                f'add_test(broken "{sys.executable}" "-c" "raise SystemExit(1)")\n'
                'set_tests_properties(broken PROPERTIES SKIP_RETURN_CODE 77)\n'
            )
            result = subprocess.run(
                ["ctest", "--test-dir", str(work), "--output-junit", "results.xml"],
                cwd=work, capture_output=True, text=True, timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            report = ET.parse(work / "results.xml").getroot()
            cases = {case.attrib["name"]: case for case in report.iter("testcase")}
            self.assertIsNotNone(cases["unavailable"].find("skipped"))
            self.assertIsNone(cases["unavailable"].find("failure"))
            self.assertIsNotNone(cases["broken"].find("failure"))
            self.assertIsNone(cases["broken"].find("skipped"))


if __name__ == "__main__":
    unittest.main()
