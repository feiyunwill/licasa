#!/usr/bin/env python3
"""A surviving process or a host-assisted import cannot pass recovery acceptance."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_dialog_recovery import MISSING_MODULE, RECOVERY_ROOT, validate_recovery
from verify_dialog_interactions import RECOVERY_SCENARIOS
from verify_dialog_interactions import application_executable


class RecoveryEvidenceTests(unittest.TestCase):
    def test_real_report_producer_distinguishes_native_launcher_from_relocated_payload(self):
        with tempfile.TemporaryDirectory(prefix="licasa-report-identity-") as temporary:
            root = Path(temporary)
            app = root / "licasa"
            payload = root / "licasa.bin"
            app.write_text('#!/bin/sh\nexec "$(dirname "$0")/licasa.bin" "$@"\n')
            shutil.copy2("/bin/true", payload)
            diagnostic, sample = root / "diagnostic", root / "photo"
            diagnostic.write_bytes(b"diagnostic identity fixture")
            sample.write_bytes(b"sample identity fixture")
            for wrapped in (True, False):
                with self.subTest(wrapped=wrapped):
                    if not wrapped:
                        shutil.copy2(payload, app)
                        payload.unlink()
                    output = root / "report.json"
                    # Unsupported native driving returns after recording real
                    # input identities, before any display/process setup.
                    result = subprocess.run([sys.executable, str(ROOT / "tools/verify_dialog_interactions.py"),
                        "--app", str(app), "--diagnostic", str(diagnostic), "--sample", str(sample),
                        "--output", str(output), "--dialog-backend", "native"],
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    report = json.loads(output.read_text())
                    isolated = root / "relocated-licasa"
                    shutil.copy2(application_executable(app), isolated)
                    self.assertEqual(report["app_sha256"], hashlib.sha256(isolated.read_bytes()).hexdigest())
                    self.assertEqual(report["app_launcher_sha256"], hashlib.sha256(app.read_bytes()).hexdigest())
                    self.assertEqual(report["app_executable_path"], str(application_executable(app)))
                    self.assertEqual(report["app_sha256"] == report["app_launcher_sha256"], not wrapped)

    def fixture(self, native=False):
        roots = (["/usr/lib/x86_64-linux-gnu", "/usr/lib/licasa/lib"] if native
                 else [RECOVERY_ROOT + "/usr/lib"])
        return {"status": "passed", "protocol": "x11", "mode": "software", "dialog_backend": "fallback",
                "missing_module": MISSING_MODULE, "assembly_rejected": True,
                "assembly_error": "Missing QML runtime file: " + RECOVERY_ROOT + "/usr/qml/Qt/labs/folderlistmodel/qmldir",
                "isolated_qml_root": RECOVERY_ROOT + "/usr/qml", "qt_library_roots": roots,
                "app_sha256": "a" * 64, "original_app_sha256": "a" * 64,
                "diagnostic_sha256": "b" * 64, "original_diagnostic_sha256": "b" * 64,
                "scenarios": [{"name": name, "status": "passed"}
                              for name in sorted(RECOVERY_SCENARIOS | {"application_launch"})],
                "runtime": {"qt_version": "6.8.2" if native else "6.4.2",
                            "qml_import_paths": [RECOVERY_ROOT + "/usr/bin", "qrc:/qt/qml",
                                                 RECOVERY_ROOT + "/usr/qml"],
                            "process": {"mapped_qt_libraries": [roots[0] + f"/libQt6{name}.so.6"
                                                                for name in ("Core", "Qml", "Quick")]}}}

    def test_native_and_portable_recovery_require_real_state_and_isolated_modules(self):
        for native in (False, True):
            report = self.fixture(native)
            validate_recovery(report, "6.8.2" if native else "6.4.2")

    def test_missing_skipped_duplicate_or_wrong_binary_evidence_cannot_pass(self):
        for mutate in (
            lambda row: row.update(status="skipped"),
            lambda row: row.update(assembly_rejected=False),
            lambda row: row.update(assembly_error="unrelated build error"),
            lambda row: row.update(missing_module="QtQuick"),
            lambda row: row.update(app_sha256="c" * 64),
            lambda row: row.update(app_sha256="", original_app_sha256=""),
            lambda row: row["scenarios"].pop(),
            lambda row: row["scenarios"][0].update(status="failed"),
            lambda row: row["scenarios"].append(copy.deepcopy(row["scenarios"][0])),
            lambda row: row["runtime"].update(qt_version="6.8.2"),
        ):
            report = self.fixture()
            mutate(report)
            with self.subTest(report=report), self.assertRaises(RuntimeError):
                validate_recovery(report, "6.4.2")

    def test_system_or_original_bundle_modules_cannot_mask_the_missing_module(self):
        for path in ("/usr/lib/x86_64-linux-gnu/qt6/qml", "/tmp/squashfs-root/usr/qml",
                     "/opt/licasa/usr/qml", RECOVERY_ROOT + "/../borrowed-qml"):
            report = self.fixture()
            report["runtime"]["qml_import_paths"].append(path)
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                validate_recovery(report, "6.4.2")

    def test_runtime_origin_policy_cannot_be_weakened_by_the_report(self):
        report = self.fixture()
        report["qt_library_roots"] = ["/"]
        with self.assertRaises(RuntimeError):
            validate_recovery(report, "6.4.2")


if __name__ == "__main__":
    unittest.main()
