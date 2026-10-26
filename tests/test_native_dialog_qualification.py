#!/usr/bin/env python3
"""A loaded GTK library or surviving Qt picker cannot masquerade as native GTK."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_native_dialog_container import build_recipe
import verify_native_dialog_container as container
from verify_native_dialog_driver import gtk_picker_identity, REQUIRED_SCENARIOS
from qualification_evidence import validate_native_dialogs


class NativeGtkQualificationTests(unittest.TestCase):
    def setUp(self):
        self.theme = "/usr/lib/x86_64-linux-gnu/qt6/plugins/platformthemes/libqgtk3.so"
        self.gtk = "/usr/lib/x86_64-linux-gnu/libgtk-3.so.0.2406.32"
        self.identity = {"toolkit": "gtk", "role": "file chooser", "process_id": 42,
                         "expected_pid": 42, "mapped": [self.theme, self.gtk]}
        self.target = {"id": "debian13-native-gtk", "artifact": "debian-deb", "profile": "debian-6.8.2",
                       "contract": "native-dialogs"}
        self.manifest = {"profiles": {"debian-6.8.2": {"qt": "6.8.2"}}}
        confirmation = gtk_picker_identity(**self.identity)
        self.report = {"kind": "native-dialogs", "target_id": self.target["id"], "status": "passed",
                       "desktop": "MATE", "protocol": "x11", "dialog_backend": "native-gtk",
                       "runtime_dependencies_checked_before_desktop_tools": True, "qt_version": "6.8.2",
                       "runtime": {"qt_version": "6.8.2", "process": {"mapped_qt_libraries": [
                           f"/usr/lib/x86_64-linux-gnu/libQt6{name}.so.6.8.2" for name in ("Core", "Qml", "Quick")]}},
                       "scenarios": [{"name": name, "status": "passed"} for name in sorted(REQUIRED_SCENARIOS)],
                       "native_confirmations": [copy.deepcopy(confirmation) for _ in range(5)]}

    def test_accepts_same_process_gtk_chooser_and_complete_native_journeys(self):
        gtk_picker_identity(**self.identity)
        validate_native_dialogs(self.report, self.target, self.manifest)

    def test_toolkit_process_role_and_both_native_libraries_are_required(self):
        for mutate in (
            lambda values: values.update(toolkit="Qt"),
            lambda values: values.update(process_id=99),
            lambda values: values.update(role="push button"),
            lambda values: values.update(mapped=[self.gtk]),
            lambda values: values.update(mapped=[self.theme]),
        ):
            changed = copy.deepcopy(self.identity)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(RuntimeError, "not proven"):
                gtk_picker_identity(**changed)

    def test_direct_gate_rejects_fallback_partial_skipped_and_foreign_runtime(self):
        for mutate in (
            lambda report: report.update(status="unsupported"),
            lambda report: report.update(dialog_backend="fallback"),
            lambda report: report.update(runtime_dependencies_checked_before_desktop_tools=False),
            lambda report: report["scenarios"].pop(),
            lambda report: report["scenarios"][0].update(status="skipped"),
            lambda report: report["scenarios"].append(copy.deepcopy(report["scenarios"][0])),
            lambda report: report["native_confirmations"].pop(),
            lambda report: report["native_confirmations"][0].update(toolkit="Qt"),
            lambda report: report["native_confirmations"][0].update(process_id=99),
            lambda report: report["native_confirmations"][0].update(platform_theme_plugin="/foreign/libqgtk3.so"),
            lambda report: report["runtime"].update(qt_version="6.4.2"),
            lambda report: report["runtime"]["process"].update(mapped_qt_libraries=[]),
        ):
            changed = copy.deepcopy(self.report)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_native_dialogs(changed, self.target, self.manifest)

    def test_desktop_and_gtk_packages_cannot_mask_pristine_install_dependencies(self):
        recipe = build_recipe("debian-13")
        self.assertLess(recipe.index("/tmp/candidate.deb &&"), recipe.index("licasa_check_runtime_dependencies"))
        self.assertLess(recipe.index("licasa_check_runtime_dependencies"), recipe.index("qt6-gtk-platformtheme"))
        self.assertLess(recipe.index("licasa_check_runtime_dependencies"), recipe.index("mate-session-manager"))
        self.assertIn("X-Licasa-Target", recipe)

    def test_cached_image_cannot_qualify_a_rebuilt_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "candidate.deb"
            package.write_bytes(b"rebuilt candidate")
            output = root / "result.json"
            arguments = ["qualify", str(package), "--output", str(output), "--skip-build"]
            labels = json.dumps({"licasa.native.package.sha256": "a" * 64, "licasa.native.target": "debian-13"})
            with patch.object(sys, "argv", arguments), \
                    patch.object(container.subprocess, "check_output", side_effect=["sha256:image\n", labels]), \
                    patch.object(container.subprocess, "run") as run:
                self.assertEqual(container.main(), 1)
            run.assert_not_called()
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertIn("different candidate", report["error"])

    def test_timed_out_session_removes_only_its_named_container_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "candidate.deb"
            package.write_bytes(b"candidate")
            output = root / "result.json"
            arguments = ["qualify", str(package), "--output", str(output), "--skip-build"]
            labels = json.dumps({"licasa.native.package.sha256": container.digest(package),
                                 "licasa.native.target": "debian-13"})
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if command[:2] == ["docker", "run"]:
                    raise subprocess.TimeoutExpired(command, 210)
                return SimpleNamespace(stdout="cleaned\n", stderr="", returncode=0)
            with patch.object(sys, "argv", arguments), \
                    patch.object(container.subprocess, "check_output", side_effect=["sha256:image\n", labels]), \
                    patch.object(container.subprocess, "run", side_effect=run):
                self.assertEqual(container.main(), 1)
            name = calls[0][calls[0].index("--name") + 1]
            self.assertTrue(name.startswith("licasa-native-gtk-"))
            self.assertEqual(calls[1], ["docker", "rm", "-f", name])
            self.assertEqual(json.loads(output.read_text())["status"], "failed")
            self.assertEqual((root / "result-native-gtk/container-cleanup.log").read_text(), "cleaned\n")


if __name__ == "__main__":
    unittest.main()
