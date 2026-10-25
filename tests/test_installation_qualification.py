#!/usr/bin/env python3
"""Guard the minimal-runtime qualification boundary before desktop tools are added."""

import ast
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class DependencyRecipeTests(unittest.TestCase):
    def test_library_checks_precede_desktop_tool_installation(self):
        for name in ("verify_deb_container.py", "verify_appimage_container.py",
                     "verify_appimage_fedora.py", "verify_rpm_container.py", "verify_arch_package.py"):
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
                             if line.startswith("RUN ") and "xvfb" in line.lower())
                self.assertLess(check, tools)
                self.assertFalse(any("weston" in line.lower() or "xvfb" in line.lower() or "desktop-file-utils" in line
                                     for line in lines[:check]))
                for line in lines:
                    if line.startswith("RUN "):
                        subprocess.run(["bash", "-n"], input=line[4:], text=True, check=True)

    def test_arch_appimage_dependency_and_lifecycle_gates_precede_display_tools(self):
        recipe = re.sub(r"\\\n\s*", " ", (ROOT / "packaging/verify-appimage-arch.Dockerfile").read_text())
        lines = recipe.splitlines()
        linkage = next(i for i, line in enumerate(lines) if line.startswith("RUN ") and "licasa_check_runtime_dependencies" in line)
        lifecycle = next(i for i, line in enumerate(lines) if line.startswith("RUN ") and "check_appimage_install.sh" in line)
        display = next(i for i, line in enumerate(lines) if line.startswith("RUN ") and "xorg-server-xvfb" in line)
        self.assertLess(linkage, lifecycle)
        self.assertLess(lifecycle, display)
        for line in lines:
            if line.startswith("RUN "):
                subprocess.run(["bash", "-n"], input=line[4:], text=True, check=True)


class LifecycleExecutionGuardTests(unittest.TestCase):
    def test_lifecycle_check_refuses_an_ordinary_home(self):
        with tempfile.TemporaryDirectory(prefix="licasa-lifecycle-guard-") as temporary:
            result = subprocess.run(
                ["bash", str(ROOT / "tools/check_appimage_install.sh"), "unused", "unused"],
                env=dict(os.environ, HOME=temporary), capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(list(Path(temporary).iterdir()))

    def test_session_worker_refuses_a_host_before_touching_home(self):
        with tempfile.TemporaryDirectory(prefix="licasa-session-guard-") as temporary:
            result = subprocess.run(
                ["python3", str(ROOT / "tools/check_linux_session.py"), "--desktop", "gnome",
                 "--appimage", "unused", "status"],
                env=dict(os.environ, HOME=temporary), capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("marked disposable QEMU/KVM guest", result.stderr)
            self.assertFalse(list(Path(temporary).iterdir()))


class DesktopImageAssertionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        cls.addClassCleanup(sys.path.remove, str(ROOT / "tools"))
        import verify_snap_desktop
        cls.pattern_present = staticmethod(verify_snap_desktop.pattern_present)
        cls.colors = verify_snap_desktop.PHOTO_COLORS

    def test_black_frame_and_welcome_screen_cannot_pass_as_a_visible_photo(self):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (1280, 800), "black")
        self.assertFalse(self.pattern_present(image))
        draw = ImageDraw.Draw(image)
        draw.text((300, 300), "Drop an image here", fill="white")
        self.assertFalse(self.pattern_present(image))
        for index, color in enumerate(self.colors):
            x, y = index % 2 * 240 + 100, index // 2 * 180 + 100
            draw.rectangle((x, y, x + 239, y + 179), fill=color)
        self.assertTrue(self.pattern_present(image))

    def test_failed_vm_activation_retains_report_and_marks_failure(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            spec = importlib.util.spec_from_file_location("vm_qualification", ROOT / "tools/verify_linux_vm_desktop.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(ROOT / "tools"))
        with tempfile.TemporaryDirectory(prefix="licasa-failed-session-") as temporary:
            args = ['verify_linux_vm_desktop.py', '--port', '1234', '--identity', temporary + '/key',
                    '--known-hosts', temporary + '/hosts', '--qmp', temporary + '/qmp',
                    '--desktop', 'gnome', '--guest-appimage', '/guest/Licasa.AppImage', '--output', temporary]
            failed = subprocess.CompletedProcess([], 1, '', 'No logged-in compositor')
            with patch('sys.argv', args), patch.object(module.subprocess, 'run', return_value=failed):
                with self.assertRaises(RuntimeError):
                    module.main()
            import json
            report = json.loads((Path(temporary) / 'desktop-report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['routes'], {})
            self.assertIn('No logged-in compositor', (Path(temporary) / 'prepare.stderr').read_text())


if __name__ == "__main__":
    unittest.main()
