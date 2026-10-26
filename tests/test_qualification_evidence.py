#!/usr/bin/env python3
"""Prove stable promotion rejects stale, skipped, missing, and mismatched evidence."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import qualification_evidence as evidence
from package_dialog_recovery import MISSING_MODULE, RECOVERY_ROOT
from package_motion_checks import FFMPEG
from package_reader_checks import READER_SAMPLES, validate_readers
from verify_dialog_interactions import RECOVERY_SCENARIOS


class QualificationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packages = self.root / "packages"
        self.reports = self.root / "reports"
        self.manifest_path = self.root / "support.json"
        self.manifest = {"profiles": {"ubuntu-6.4.2": {"qt": "6.4.2"}}, "artifacts": {
            "ubuntu-deb": "deb/licasa_{version}.deb", "appimage": "appimage/Licasa_{version}.AppImage"}, "targets": [
            {"id": "supported", "artifact": "ubuntu-deb", "profile": "ubuntu-6.4.2", "status": "supported"},
            {"id": "unsupported", "artifact": "ubuntu-deb", "profile": "ubuntu-6.4.2", "status": "unsupported",
             "qualifier": ["tools/verify_unsupported_ubuntu.py", "{artifact}", "--ubuntu-version", "22.04", "--appimage", "{appimage}"]},
            {"id": "development", "artifact": "ubuntu-deb", "profile": "ubuntu-6.4.2", "status": "in-development"},
        ]}
        self.manifest_path.write_text(json.dumps(self.manifest))
        (self.packages / "deb").mkdir(parents=True)
        (self.packages / "deb/licasa_1.2.3.deb").write_bytes(b"exact candidate package")
        (self.packages / "appimage").mkdir()
        (self.packages / "appimage/Licasa_1.2.3.AppImage").write_bytes(b"exact candidate portable package")
        self.identity = {"source_sha": "a" * 40, "build_run_id": "123", "version": "1.2.3",
                         "manifest_sha256": evidence.digest(self.manifest_path)}
        self.attestation = {"schema_version": 1, **self.identity,
                            "packages": evidence.package_hashes(self.manifest, self.packages, "1.2.3"),
                            "outcomes": []}
        for target in self.manifest["targets"]:
            directory = self.reports / target["id"] / "raw"
            directory.mkdir(parents=True)
            interactions = [{"status": "passed", "protocol": protocol, "mode": mode,
                             "app_sha256": "d" * 64, "diagnostic_sha256": "e" * 64,
                             "dialog_backend": "fallback", "runtime": {"qt_version": "6.4.2", "process": {"mapped_qt_libraries": [f"/usr/lib/x86_64-linux-gnu/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")]}}, "scenarios": [
                                 {"name": name, "status": "passed"}
                                 for name in sorted(evidence.REQUIRED_DIALOG_SCENARIOS)]}
                            for protocol in ("x11", "wayland") for mode in ("default", "software")]
            negative = next(case for case in interactions if case["protocol"] == "x11" and case["mode"] == "software")
            negative["missing_module_recovery"] = self.recovery_report()
            report = {"status": "passed", "dialog_interactions": interactions, "motion_photo": self.motion_report()}
            if target["status"] == "unsupported":
                report = {"ubuntu_version": "22.04", "native_deb": "unsupported_dependency_rejection_confirmed",
                    "appimage": "unsupported_glibc_rejection_confirmed",
                    "package_sha256": self.attestation["packages"]["ubuntu-deb"]["sha256"],
                    "appimage_sha256": self.attestation["packages"]["appimage"]["sha256"]}
            (directory / "report.json").write_text(json.dumps(report))
            (directory / "qualifier.log").write_text("meaningful assertions passed\n")
            files = [{"path": str(path.relative_to(self.reports)), "sha256": evidence.digest(path)}
                     for path in directory.iterdir()]
            self.attestation["outcomes"].append({**self.identity, "target": target["id"],
                "artifact": "ubuntu-deb", "profile": "ubuntu-6.4.2", "package_sha256": self.attestation["packages"]["ubuntu-deb"]["sha256"],
                "status": "unsupported" if target["status"] == "unsupported" else "pass",
                "exit_code": 0, "evidence": files})

    def recovery_report(self):
        return {"status": "passed", "protocol": "x11", "mode": "software", "dialog_backend": "fallback",
                "missing_module": MISSING_MODULE, "isolated_qml_root": RECOVERY_ROOT + "/usr/qml",
                "assembly_rejected": True, "assembly_error": "Missing QML runtime file: Qt/labs/folderlistmodel/qmldir",
                "app_sha256": "d" * 64, "original_app_sha256": "d" * 64,
                "diagnostic_sha256": "e" * 64, "original_diagnostic_sha256": "e" * 64,
                "qt_library_roots": ["/usr/lib/x86_64-linux-gnu", "/usr/lib/licasa/lib"],
                "runtime": {"qt_version": "6.4.2", "qml_import_paths": [RECOVERY_ROOT + "/usr/qml", "qrc:/qt/qml"],
                    "process": {"mapped_qt_libraries": [f"/usr/lib/x86_64-linux-gnu/libQt6{name}.so.6.4.2"
                                                        for name in ("Core", "Qml", "Quick")]}},
                "scenarios": [{"name": name, "status": "passed"}
                              for name in sorted(RECOVERY_SCENARIOS | {"application_launch"})]}

    def motion_report(self):
        cases = []
        fixtures = {rotation: evidence.make_fixture(self.root / f"motion-{rotation}.jpg", rotation) for rotation in (0, 90)}
        for protocol in ("x11", "wayland"):
            for mode in ("default", "software"):
                for rotation in (0, 90):
                    width, height = (240, 320) if rotation else (320, 240)
                    controls = {name: True for name in evidence.REQUIRED_CONTROLS}
                    controls.update({name: False for name in ("final_session_available", "final_session_active",
                                                              "final_session_playing", "final_control_present")})
                    controls.update(saved_frame_width=width, saved_frame_height=height,
                        process_after_clear={
                            "mapped_qt_libraries": [f"/usr/lib/x86_64-linux-gnu/libQt6{name}.so.6.4.2"
                                                    for name in ("Core", "Qml", "Quick")],
                            "mapped_optional_libraries": ["/usr/lib/licasa/plugins/multimedia/libffmpegmediaplugin.so",
                                "/usr/lib/x86_64-linux-gnu/libQt6Multimedia.so.6.4.2",
                                *["/usr/lib/x86_64-linux-gnu/" + name for name in FFMPEG["6.4.2"]]]})
                    runtime = evidence.validate_controls(controls, "/usr", "6.4.2", rotation)
                    cases.append({"status": "passed", "protocol": protocol, "mode": mode,
                        "rotation_degrees": rotation, "qt_version": "6.4.2", "fixture": fixtures[rotation],
                        "diagnostic_sha256": "e" * 64,
                        "runtime_probe": {"qt_version": "6.4.2", "report_sha256": "b" * 64,
                            "mapped_qt_libraries": controls["process_after_clear"]["mapped_qt_libraries"]},
                        "controls": controls, "runtime": dict(runtime, private_ffmpeg_sha256="f" * 64),
                        "scenarios": [{"name": name, "status": "passed"} for name in sorted(evidence.REQUIRED_CONTROLS)],
                        "saved_frame": {"width": width, "height": height, "sha256": "a" * 64}})
        return {"status": "passed", "qt_version": "6.4.2", "runtime_root": "/usr", "cases": cases}

    def test_missing_module_recovery_is_required_and_bound_to_same_installed_executables(self):
        complete = json.loads((self.reports / "supported/raw/report.json").read_text())
        target = self.manifest["targets"][0]
        for mutate in (
            lambda recovery: recovery.clear(),
            lambda recovery: recovery.update(status="skipped"),
            lambda recovery: recovery.update(missing_module="QtQuick.Dialogs"),
            lambda recovery: recovery.update(assembly_rejected=False),
            lambda recovery: recovery["scenarios"].pop(),
            lambda recovery: recovery["scenarios"][0].update(status="failed"),
            lambda recovery: recovery["runtime"]["qml_import_paths"].append("/usr/lib/x86_64-linux-gnu/qt6/qml"),
            lambda recovery: recovery["runtime"].update(qt_version="6.8.2"),
            lambda recovery: recovery.update(app_sha256="c" * 64, original_app_sha256="c" * 64),
            lambda recovery: recovery.update(diagnostic_sha256="c" * 64, original_diagnostic_sha256="c" * 64),
            lambda recovery: recovery.update(qt_library_roots=[RECOVERY_ROOT + "/usr/lib"]),
        ):
            changed = copy.deepcopy(complete)
            recovery = next(row for row in changed["dialog_interactions"]
                            if row["protocol"] == "x11" and row["mode"] == "software")["missing_module_recovery"]
            mutate(recovery)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                evidence.validate_report(changed, target, self.manifest)

    def test_motion_requires_all_rotated_display_cases_and_actual_production_controls(self):
        target = self.manifest["targets"][0]
        complete = {"motion_photo": self.motion_report()}
        evidence.validate_motion_photo(complete, target, self.manifest)
        for mutate in (
            lambda motion: motion.clear(),
            lambda motion: motion.update(status="skipped"),
            lambda motion: motion.update(qt_version="6.8.2"),
            lambda motion: motion.update(runtime_root="/opt/foreign/usr"),
            lambda motion: motion["cases"].pop(),
            lambda motion: motion["cases"].append(copy.deepcopy(motion["cases"][0])),
            lambda motion: motion["cases"][0].update(status="failed"),
            lambda motion: motion["cases"][0]["scenarios"].pop(),
            lambda motion: motion["cases"][0]["controls"].update(scrub_presentation_frame_visible=False),
            lambda motion: motion["cases"][0]["controls"].update(final_session_available=True),
            lambda motion: motion["cases"][0]["fixture"].update(embedded_custom_io=False),
            lambda motion: motion["cases"][0]["fixture"].update(video_offset=0),
            lambda motion: motion["cases"][0]["fixture"].update(video_sha256="a" * 64),
            lambda motion: motion["cases"][1]["saved_frame"].update(width=320, height=240),
            lambda motion: motion["cases"][0]["runtime"].update(private_ffmpeg_sha256=""),
            lambda motion: motion["cases"][0]["runtime"].update(mapped_qt_libraries=[]),
            lambda motion: motion["cases"][0].update(runtime_probe={}),
            lambda motion: motion["cases"][0]["runtime_probe"].update(qt_version="6.8.2"),
            lambda motion: motion["cases"][0]["runtime_probe"].update(mapped_qt_libraries=[]),
            lambda motion: motion["cases"][0]["runtime_probe"].update(report_sha256=""),
            lambda motion: motion["cases"][0]["controls"]["process_after_clear"]["mapped_optional_libraries"].append("/usr/local/lib/libavcodec.so.60"),
        ):
            changed = copy.deepcopy(complete)
            mutate(changed["motion_photo"])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                evidence.validate_motion_photo(changed, target, self.manifest)

    def test_motion_evidence_cannot_borrow_a_different_diagnostic(self):
        complete = json.loads((self.reports / "supported/raw/report.json").read_text())
        complete["motion_photo"]["cases"][0]["diagnostic_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "same installed production diagnostic"):
            evidence.validate_report(complete, self.manifest["targets"][0], self.manifest)

    def test_portable_motion_must_load_the_qualified_private_backend_bytes(self):
        target = dict(self.manifest["targets"][0], artifact="appimage")
        motion = self.motion_report()
        motion["runtime_root"] = "/tmp/squashfs-root/usr"
        prefix = motion["runtime_root"]
        reference = prefix + "/lib/licasa/plugins/multimedia/libffmpegmediaplugin.so"
        for case in motion["cases"]:
            process = case["controls"]["process_after_clear"]
            for key in ("mapped_qt_libraries", "mapped_optional_libraries"):
                process[key] = [path.replace("/usr/lib/x86_64-linux-gnu/", prefix + "/lib/")
                    .replace("/usr/lib/licasa/", prefix + "/lib/licasa/") for path in process[key]]
            case["runtime_probe"]["mapped_qt_libraries"] = process["mapped_qt_libraries"]
            probe = {"qt_version": "6.4.2", "before_decode": {"mapped_qt_libraries": process["mapped_qt_libraries"]}}
            case["runtime"] = dict(evidence.validate_controls(case["controls"], prefix, "6.4.2", case["rotation_degrees"], probe),
                private_ffmpeg_sha256="f" * 64, private_ffmpeg_reference=reference,
                private_ffmpeg_reference_sha256="f" * 64)
        evidence.validate_motion_photo({"motion_photo": motion}, target, self.manifest)
        for mutation in (
            lambda runtime: runtime.update(private_ffmpeg_reference_sha256=""),
            lambda runtime: runtime.update(private_ffmpeg_reference_sha256="a" * 64),
            lambda runtime: runtime.update(private_ffmpeg_sha256="a" * 64),
            lambda runtime: runtime.update(private_ffmpeg_reference="/usr/lib/stock/libffmpegmediaplugin.so"),
        ):
            changed = copy.deepcopy(motion)
            mutation(changed["cases"][0]["runtime"])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "different FFmpeg backend"):
                evidence.validate_motion_photo({"motion_photo": changed}, target, self.manifest)

    def test_nonpassing_primary_report_cannot_hide_behind_passing_nested_checks(self):
        complete = json.loads((self.reports / "supported/raw/report.json").read_text())
        for status in ("failed", "skipped", "unsupported"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "Primary"):
                evidence.validate_report(dict(complete, status=status), self.manifest["targets"][0], self.manifest)

    def validate(self, attestation=None):
        return evidence.validate(attestation or self.attestation, self.manifest,
            source_sha=self.identity["source_sha"], build_run_id="123", version="1.2.3",
            packages=self.packages, evidence=self.reports, manifest_path=self.manifest_path)

    def test_accepts_complete_exact_candidate_evidence(self):
        self.validate()

    def test_rejects_stale_source_build_or_version(self):
        for key, value in (("source_sha", "b" * 40), ("build_run_id", "456"), ("version", "1.2.2")):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.attestation)
                changed[key] = value
                with self.assertRaisesRegex(ValueError, "selected candidate"):
                    self.validate(changed)

    def test_rejects_mixed_target_source_and_build_records(self):
        for key, value in (("source_sha", "b" * 40), ("build_run_id", "456"), ("manifest_sha256", "c" * 64)):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.attestation)
                changed["outcomes"][0][key] = value
                with self.assertRaisesRegex(ValueError, "stale or mismatched"):
                    self.validate(changed)

    def test_changed_candidate_file_is_rejected(self):
        (self.packages / "deb/licasa_1.2.3.deb").write_bytes(b"rebuilt or replaced package")
        with self.assertRaisesRegex(ValueError, "package hashes"):
            self.validate()

    def test_target_cannot_borrow_another_native_runtime_profile(self):
        self.attestation["outcomes"][0]["profile"] = "debian-6.8.2"
        with self.assertRaisesRegex(ValueError, "package mismatch"):
            self.validate()

    def test_changed_support_contract_is_rejected(self):
        self.manifest_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "manifest_sha256"):
            self.validate()

    def test_missing_or_duplicate_target_is_rejected(self):
        for replacement in (self.attestation["outcomes"][:-1], self.attestation["outcomes"] * 2):
            changed = copy.deepcopy(self.attestation)
            changed["outcomes"] = replacement
            with self.assertRaisesRegex(ValueError, "missing, duplicated"):
                self.validate(changed)

    def test_failed_skipped_and_unsupported_do_not_satisfy_supported_target(self):
        for status in ("fail", "skipped", "unsupported", "unknown"):
            changed = copy.deepcopy(self.attestation)
            changed["outcomes"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.validate(changed)

    def test_development_candidate_cannot_silently_skip_qualification(self):
        self.attestation["outcomes"][2]["status"] = "skipped"
        with self.assertRaisesRegex(ValueError, "expected pass"):
            self.validate()

    def test_unsupported_installation_is_distinct_from_pass(self):
        self.attestation["outcomes"][1]["status"] = "pass"
        with self.assertRaisesRegex(ValueError, "expected unsupported"):
            self.validate()

    def test_unsupported_receipt_cannot_borrow_a_different_candidate_appimage(self):
        report = self.reports / "unsupported/raw/report.json"
        changed = json.loads(report.read_text())
        changed["appimage_sha256"] = "c" * 64
        report.write_text(json.dumps(changed))
        item = next(item for item in self.attestation["outcomes"][1]["evidence"] if item["path"].endswith("report.json"))
        item["sha256"] = evidence.digest(report)
        with self.assertRaisesRegex(ValueError, "different candidate AppImage"):
            self.validate()

    def test_nonzero_exit_cannot_be_overridden_by_pass_status(self):
        self.attestation["outcomes"][0]["exit_code"] = 139
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_log_structured_report_or_evidence_file_is_rejected(self):
        for files in ([], self.attestation["outcomes"][0]["evidence"][:1]):
            changed = copy.deepcopy(self.attestation)
            changed["outcomes"][0]["evidence"] = files
            with self.assertRaisesRegex(ValueError, "missing structured evidence"):
                self.validate(changed)
        (self.reports / "supported/raw/report.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing evidence"):
            self.validate()

    def test_tampered_evidence_is_rejected(self):
        (self.reports / "supported/raw/report.json").write_text('{"status":"failed"}')
        with self.assertRaisesRegex(ValueError, "changed evidence"):
            self.validate()

    def test_evidence_paths_cannot_escape_or_borrow_another_target(self):
        for relative in ("../secret", "/etc/passwd", "unsupported/raw/report.json"):
            changed = copy.deepcopy(self.attestation)
            changed["outcomes"][0]["evidence"][0]["path"] = relative
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.validate(changed)

    def test_evidence_symlinks_are_rejected(self):
        report = self.reports / "supported/raw/report.json"
        report.unlink()
        report.symlink_to(self.reports / "unsupported/raw/report.json")
        with self.assertRaisesRegex(ValueError, "Unsafe evidence"):
            self.validate()

    def test_qualifier_must_use_candidate_source_checkout(self):
        with patch.object(evidence.subprocess, "check_output", return_value="b" * 40 + "\n"):
            with self.assertRaisesRegex(ValueError, "candidate"):
                evidence.ensure_candidate_source("a" * 40)

    def test_separate_verifier_provenance_cannot_change_or_escape_its_approved_files(self):
        receipt = {"source_sha": "b" * 40, "changed_files": ["tools/verify_snap_desktop.py"]}
        self.attestation["verification"] = receipt
        self.attestation["outcomes"][0]["verification"] = copy.deepcopy(receipt)
        self.validate()
        for changed in ({"source_sha": "c" * 40, "changed_files": receipt["changed_files"]},
                        {"source_sha": "b" * 40, "changed_files": ["src/main.cpp"]}):
            self.attestation["outcomes"][0]["verification"] = changed
            with self.assertRaisesRegex(ValueError, "verifier provenance"):
                self.validate()

    def test_dirty_candidate_checkout_cannot_claim_exact_source_identity(self):
        with patch.object(evidence.subprocess, "check_output", return_value="a" * 40 + "\n"), \
                patch.object(evidence.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, "uncommitted tracked"):
                evidence.ensure_candidate_source("a" * 40)

    def test_launch_survival_cannot_replace_dialog_interactions(self):
        with self.assertRaisesRegex(ValueError, "dialog interaction matrix"):
            evidence.validate_interactions({"viewer_cold_start": True})

    def test_incomplete_duplicated_or_skipped_dialog_matrix_is_rejected(self):
        complete = json.loads((self.reports / "supported/raw/report.json").read_text())
        for mutate in (
            lambda rows: rows.pop(),
            lambda rows: rows.append(copy.deepcopy(rows[0])),
            lambda rows: rows[0].update(status="skipped"),
            lambda rows: rows[0].update(dialog_backend="native"),
            lambda rows: rows[0]["scenarios"].pop(),
            lambda rows: rows[0]["scenarios"][0].update(status="failed"),
        ):
            changed = copy.deepcopy(complete)
            mutate(changed["dialog_interactions"])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                evidence.validate_interactions(changed)

    def test_stable_notes_cannot_claim_acceptance_is_pending(self):
        for notes in ("Preview release: stable follows qualification.",
                      "Final 1.2.3 package acceptance is pending.",
                      "Qualification pending."):
            with self.subTest(notes=notes), self.assertRaisesRegex(ValueError, "preview or pending"):
                evidence.validate_stable_notes(notes)
        evidence.validate_stable_notes("Package qualifications passed. Physical GPUs remain untested.")

    def test_dialog_runtime_profile_and_library_origins_are_enforced(self):
        complete = json.loads((self.reports / "supported/raw/report.json").read_text())
        for mutate in (
            lambda runtime: runtime.update(qt_version="6.8.2"),
            lambda runtime: runtime["process"].update(mapped_qt_libraries=[]),
            lambda runtime: runtime["process"]["mapped_qt_libraries"].pop(),
            lambda runtime: runtime["process"]["mapped_qt_libraries"].append("/usr/local/lib/libQt6Gui.so.6"),
            lambda runtime: runtime["process"]["mapped_qt_libraries"].append("/usr/lib/x86_64-linux-gnu/../../foreign/libQt6Gui.so.6"),
        ):
            changed = copy.deepcopy(complete)
            mutate(changed["dialog_interactions"][0]["runtime"])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                evidence.validate_target_runtime(changed, self.manifest["targets"][0], self.manifest)

    def test_portable_and_snap_dialogs_cannot_borrow_native_qt(self):
        native = json.loads((self.reports / "supported/raw/report.json").read_text())
        for artifact, prefix in (("appimage", "/tmp/squashfs-root/usr/lib"),
                                 ("rpm", "/opt/licasa/usr/lib"), ("snap", "/snap/licasa/x123/usr/lib")):
            target = dict(self.manifest["targets"][0], artifact=artifact)
            with self.subTest(artifact=artifact), self.assertRaises(ValueError):
                evidence.validate_target_runtime(native, target, self.manifest)
            corrected = copy.deepcopy(native)
            for case in corrected["dialog_interactions"]:
                case["runtime"]["process"]["mapped_qt_libraries"] = [
                    f"{prefix}/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")]
            evidence.validate_target_runtime(corrected, target, self.manifest)

    def accessibility_report(self):
        return {"kind": "accessibility", "target_id": "debian13-mate-accessibility",
                "status": "passed", "desktop": "MATE", "protocol": "x11", "qt_version": "6.4.2",
                "runtime": {"qt_version": "6.4.2", "process": {"mapped_qt_libraries": [
                    f"/usr/lib/x86_64-linux-gnu/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")]}},
                "scenarios": [{"name": name, "status": "passed"}
                              for name in sorted(evidence.REQUIRED_ACCESSIBILITY_SCENARIOS)]}

    def test_accessibility_contract_accepts_complete_executed_mate_at_spi_checks(self):
        target = dict(self.manifest["targets"][0], id="debian13-mate-accessibility", contract="accessibility")
        evidence.validate_report(self.accessibility_report(), target, self.manifest)

    def test_accessibility_is_not_satisfied_by_fallback_launch_or_partial_role_names(self):
        target = dict(self.manifest["targets"][0], id="debian13-mate-accessibility", contract="accessibility")
        fallback = json.loads((self.reports / "supported/raw/report.json").read_text())
        with self.assertRaises(ValueError):
            evidence.validate_report(fallback, target, self.manifest)
        for mutate in (
            lambda report: report.update(status="skipped"),
            lambda report: report.update(status="unsupported"),
            lambda report: report.update(desktop="GNOME"),
            lambda report: report.update(qt_version="6.8.2"),
            lambda report: report["runtime"]["process"].update(mapped_qt_libraries=[]),
            lambda report: report["scenarios"].pop(),
            lambda report: report["scenarios"][0].update(status="unsupported"),
            lambda report: report["scenarios"].append(copy.deepcopy(report["scenarios"][0])),
        ):
            changed = self.accessibility_report()
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                evidence.validate_report(changed, target, self.manifest)

    def reader_smoke(self):
        root = "/tmp/squashfs-root/usr/lib"
        runtime = {"qt_version": "6.4.2", "read_formats": sorted(evidence.REQUIRED_READER_FORMATS),
            "before_decode": {"mapped_optional_libraries": [], "mapped_qt_libraries": [
                f"{root}/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")]},
            "samples": [{"path": "/samples/" + relative, "format": name, "decoded": True,
                "decoded_width": 48, "decoded_height": 36, "raw_development": False,
                "process": {"mapped_qt_libraries": [f"{root}/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")],
                    "mapped_optional_libraries": [f"{root}/licasa/lib/" + {
                        "avif": "libavif.so.16", "heic": "libheif.so.1", "jxl": "libjxl_dec.so.0.12",
                        "dng": "libraw.so.23"}.get(name, "libavif.so.16")]}}
                for name, relative in READER_SAMPLES.items()]}
        return {"reader_smoke": {"status": "passed", "qt_version": "6.4.2",
            "expected_formats": sorted(evidence.REQUIRED_READER_FORMATS),
            "runtime": runtime, "samples": validate_readers(runtime, root, "6.4.2")}}

    def test_final_portable_reader_matrix_cannot_be_replaced_by_jpeg_launch(self):
        target = dict(self.manifest["targets"][0], id="fedora44-appimage", artifact="appimage")
        evidence.validate_reader_smoke(self.reader_smoke(), target, self.manifest)
        for mutate in (
            lambda report: report["reader_smoke"].update(status="skipped"),
            lambda report: report["reader_smoke"].update(samples=[]),
            lambda report: report["reader_smoke"]["samples"].pop(),
            lambda report: report["reader_smoke"]["samples"][0].update(decoded=False),
            lambda report: report["reader_smoke"]["samples"][0].update(decoded_width=0),
            lambda report: report["reader_smoke"]["runtime"].update(qt_version="6.8.2"),
            lambda report: report["reader_smoke"]["runtime"].update(read_formats=["jpeg"]),
            lambda report: report["reader_smoke"]["runtime"]["samples"][0].update(decoded=False),
            lambda report: report["reader_smoke"]["runtime"]["before_decode"].update(mapped_optional_libraries=["/foreign/libavif.so.16"]),
            lambda report: report["reader_smoke"]["samples"][0]["process"].update(mapped_optional_libraries=["/usr/lib/libavif.so.16"]),
        ):
            changed = self.reader_smoke()
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                evidence.validate_reader_smoke(changed, target, self.manifest)

    def test_raw_preview_and_codec_mapping_evidence_are_required(self):
        target = dict(self.manifest["targets"][0], id="fedora44-appimage", artifact="appimage")
        for field, value in (("raw_development", True), ("process", {"mapped_optional_libraries": []})):
            changed = self.reader_smoke()
            raw = next(row for row in changed["reader_smoke"]["samples"] if row["expected_format"] == "dng")
            raw[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evidence.validate_reader_smoke(changed, target, self.manifest)


if __name__ == "__main__":
    unittest.main()
