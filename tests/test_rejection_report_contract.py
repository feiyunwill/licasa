#!/usr/bin/env python3
"""Unsupported installations require explicit, candidate-bound rejection proof."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from check_package_report import check_report
from qualification_evidence import validate_report


class RejectionReportTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "packaging/support-matrix.json").read_text())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name) / "candidate.deb"
        self.package.write_bytes(b"immutable candidate package")
        self.package_hash = hashlib.sha256(self.package.read_bytes()).hexdigest()

    def target(self, version):
        return next(row for row in self.manifest["targets"]
                    if row["id"] == f"ubuntu{version}-native-rejection")

    def receipt(self, version):
        report = {"ubuntu_version": f"{version}.04",
                  "native_deb": "unsupported_dependency_rejection_confirmed",
                  "package_sha256": self.package_hash}
        if version == "22":
            report.update(appimage="unsupported_glibc_rejection_confirmed", appimage_sha256="a" * 64)
        return report

    def test_both_rejection_targets_use_the_authoritative_dispatch(self):
        for version in ("22", "26"):
            with self.subTest(version=version):
                report, target = self.receipt(version), self.target(version)
                validate_report(report, target, self.manifest)
                check_report(report, target, self.manifest, self.package)

    def test_another_candidate_cannot_supply_rejection_evidence(self):
        report = self.receipt("26")
        report["package_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "different candidate"):
            check_report(report, self.target("26"), self.manifest, self.package)

    def test_native_rejection_marker_is_mandatory(self):
        for value in (None, "passed", "unsupported", "arbitrary dependency error"):
            report = self.receipt("26")
            report["native_deb"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_report(report, self.target("26"), self.manifest, self.package)

    def test_receipt_must_describe_the_requested_userspace(self):
        report = self.receipt("26")
        report["ubuntu_version"] = "22.04"
        with self.assertRaises(ValueError):
            check_report(report, self.target("26"), self.manifest, self.package)

    def test_ubuntu22_requires_explicit_appimage_baseline_rejection(self):
        for field in ("appimage", "appimage_sha256"):
            report = self.receipt("22")
            del report[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_report(report, self.target("22"), self.manifest, self.package)
        report = self.receipt("22")
        report["appimage_sha256"] = "invalid"
        with self.assertRaises(ValueError):
            check_report(report, self.target("22"), self.manifest, self.package)

    def test_nonpassing_report_cannot_override_explicit_receipt_fields(self):
        for status in ("failed", "skipped", "unsupported"):
            report = dict(self.receipt("26"), status=status)
            with self.subTest(status=status), self.assertRaises(ValueError):
                check_report(report, self.target("26"), self.manifest, self.package)

    def test_positive_dialog_report_cannot_substitute_for_rejection(self):
        report = {"status": "passed", "dialog_interactions": [], "package_sha256": self.package_hash}
        with self.assertRaises(ValueError):
            check_report(report, self.target("26"), self.manifest, self.package)

    def test_unknown_unsupported_contract_fails_closed(self):
        target = copy.deepcopy(self.target("26"))
        target["qualifier"][0] = "tools/other_verifier.py"
        with self.assertRaises(ValueError):
            check_report(self.receipt("26"), target, self.manifest, self.package)


if __name__ == "__main__":
    unittest.main()
