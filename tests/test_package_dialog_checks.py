#!/usr/bin/env python3
"""Exercise final-package session orchestration and durable failure evidence."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import package_dialog_checks as checks
from verify_dialog_interactions import REQUIRED_SCENARIOS


class PackageDialogChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="licasa dialog contract ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sample = self.root / "photo with spaces.jpg"
        self.sample.write_bytes(b"fixture; never decoded by unit test")
        self.output = self.root / "evidence"
        self.commands = []
        self.cleanups = []
        self.timeout = False
        self.mutate = lambda report: None
        self.exit_code = 0
        self.retain_primary = True
        self.mutate_retained = lambda report: None

    def simulate_container(self, command, **kwargs):
        if command[:3] == ["docker", "rm", "-f"]:
            self.cleanups.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        self.commands.append(command)
        if self.timeout:
            raise subprocess.TimeoutExpired(command, 150)
        mounts = [command[index + 1] for index, part in enumerate(command) if part == "-v"]
        artifact_path = next(value for value in mounts if value.endswith(":/reports:rw"))
        artifacts = Path(artifact_path.removesuffix(":/reports:rw"))
        # Session identity is passed as individually quoted shell argv.
        script = command[-1]
        protocol = "wayland" if "--protocol wayland" in script else "x11"
        mode = "software" if "--mode software" in script else "default"
        report = {"status": "passed", "protocol": protocol, "mode": mode,
                  "dialog_backend": "fallback", "runtime_log": "/reports/runtime.log",
                  "saved_image": {"path": "/reports/saved.png"},
                  "runtime": {"qt_version": "6.4.2", "process": {"mapped_qt_libraries": [
                      f"/opt/licasa/usr/lib/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")] }},
                  "scenarios": [{"name": name, "status": "passed"}
                                for name in sorted(REQUIRED_SCENARIOS | {"application_launch"})]}
        self.mutate(report)
        (artifacts / "runtime.log").write_text("retained Qt import log")
        (artifacts / "harness.log").write_text("retained bounded session log")
        (artifacts / "saved.png").write_bytes(b"retained encoded output fixture")
        (artifacts / "module-inventory.json").write_text('{"status":"passed"}')
        if self.retain_primary:
            retained = copy.deepcopy(report)
            self.mutate_retained(retained)
            (artifacts / "report.json").write_text(json.dumps(retained))
        return SimpleNamespace(returncode=self.exit_code, stdout=json.dumps(report), stderr="failure log" if self.exit_code else "")

    def run_checks(self):
        with patch.object(checks.subprocess, "run", side_effect=self.simulate_container), \
                patch.object(checks, "run_dialog_recovery", return_value={"status": "passed"}):
            return checks.run_dialog_checks("candidate-image:local", app="/opt/licasa/AppRun",
                diagnostic="/opt/licasa/usr/bin/licasa_diagnostics", sample=self.sample,
                qml_root="/opt/licasa/usr/qml", library_root="/opt/licasa/usr/lib",
                output_dir=self.output)

    def test_four_sessions_preserve_required_evidence_and_isolation(self):
        reports = self.run_checks()
        self.assertEqual({(row["protocol"], row["mode"]) for row in reports},
                         {("x11", "default"), ("x11", "software"), ("wayland", "default"), ("wayland", "software")})
        self.assertEqual(len(reports), 4)
        self.assertEqual(reports[1]["missing_module_recovery"], {"status": "passed"})
        self.assertEqual(len(self.cleanups), 4)
        for command, cleanup in zip(self.commands, self.cleanups, strict=True):
            self.assertEqual(cleanup[-1], command[command.index("--name") + 1])
        self.assertEqual(len({row["artifacts_directory"] for row in reports}), 4)
        for report, command in zip(reports, self.commands, strict=True):
            self.assertTrue(Path(report["runtime_log"]).is_file())
            self.assertTrue(Path(report["saved_image"]["path"]).is_file())
            self.assertIn("none", command[command.index("--network") + 1])
            self.assertIn("--read-only", command)
            self.assertEqual(command[command.index("--user") + 1], "65534:65534")
            self.assertIn("/reports/module-inventory.json", command[-1])
            self.assertIn("/qualification/packaging/qt-runtime-modules.json:ro", " ".join(command))

    def test_failure_keeps_logs_after_temporary_container_is_gone(self):
        self.exit_code = 139
        with self.assertRaisesRegex(RuntimeError, "Package dialogs failed"):
            self.run_checks()
        retained = list(self.output.glob("*/harness.log"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_text(), "retained bounded session log")

    def test_timeout_removes_the_exact_named_container_and_keeps_artifact_directory(self):
        self.timeout = True
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_checks()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(len(self.cleanups), 1)
        name = self.commands[0][self.commands[0].index("--name") + 1]
        self.assertTrue(name.startswith("licasa-dialog-"))
        self.assertEqual(self.cleanups[0], ["docker", "rm", "-f", name])
        self.assertEqual(len(list(self.output.glob("*/container-cleanup.log"))), 1)

    def test_incomplete_or_mismatched_session_cannot_pass(self):
        for mutation in (
            lambda report: report.update(status="skipped"),
            lambda report: report.update(protocol="x11" if report["protocol"] == "wayland" else "wayland"),
            lambda report: report.update(mode="software" if report["mode"] == "default" else "default"),
            lambda report: report.update(dialog_backend="native"),
            lambda report: report.update(scenarios=[]),
            lambda report: report["scenarios"][0].update(status="failed"),
            lambda report: report["scenarios"].pop(),
            lambda report: report["scenarios"].append(copy.deepcopy(report["scenarios"][0])),
            lambda report: report["runtime"].update(qt_version="6.8.2"),
            lambda report: report["runtime"]["process"].update(mapped_qt_libraries=[]),
            lambda report: report["runtime"]["process"]["mapped_qt_libraries"].append("/usr/lib/libQt6Gui.so.6"),
        ):
            with self.subTest(mutation=mutation):
                self.mutate = mutation
                with self.assertRaises(RuntimeError):
                    self.run_checks()

    def test_stdout_without_retained_primary_evidence_cannot_pass(self):
        self.retain_primary = False
        with self.assertRaises(RuntimeError):
            self.run_checks()

    def test_retained_report_cannot_differ_from_stdout_claim(self):
        self.mutate_retained = lambda report: report.update(status="failed")
        with self.assertRaises(RuntimeError):
            self.run_checks()


if __name__ == "__main__":
    unittest.main()
