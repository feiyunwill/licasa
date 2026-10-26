#!/usr/bin/env python3
"""Installed reader qualification rejects incomplete or host-supplied evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_reader_checks import CODEC_MARKERS, READER_SAMPLES, run_reader_checks, validate_readers

RUNTIME_ROOT = "/opt/licasa/usr/lib"


def evidence():
    qt = [f"{RUNTIME_ROOT}/libQt6{name}.so.6.4.2" for name in ("Core", "Qml", "Quick")]
    samples = []
    for name, relative in READER_SAMPLES.items():
        codecs = [RUNTIME_ROOT + "/" + CODEC_MARKERS[name][0] + "1"] if name in CODEC_MARKERS else []
        samples.append({"path": "/samples/" + relative, "decoded": True, "format": name,
                        "decoded_width": 1, "decoded_height": 1, "raw_development": False,
                        "process": {"mapped_optional_libraries": codecs, "mapped_qt_libraries": qt[:]}})
    return {"qt_version": "6.4.2", "read_formats": list(READER_SAMPLES), "samples": samples,
            "before_decode": {"mapped_qt_libraries": qt, "mapped_optional_libraries": []}}


class PackagedReaderTests(unittest.TestCase):
    def test_complete_matrix_uses_the_actual_expected_formats(self):
        checked = validate_readers(evidence(), RUNTIME_ROOT, "6.4.2")
        self.assertEqual(len(checked), 12)
        self.assertEqual([sample["expected_format"] for sample in checked], list(READER_SAMPLES))

    def test_each_reader_must_be_advertised_and_really_decode_its_fixture(self):
        for index, name in enumerate(READER_SAMPLES):
            report = evidence()
            report["read_formats"].remove(name)
            with self.assertRaisesRegex(RuntimeError, "Missing installed reader"):
                validate_readers(report, RUNTIME_ROOT, "6.4.2")
            for field, value in (("path", "/samples/other.png"), ("decoded", False),
                                 ("format", "different"), ("decoded_width", 0), ("decoded_height", 0)):
                report = evidence()
                report["samples"][index][field] = value
                with self.assertRaisesRegex(RuntimeError, "decode failed"):
                    validate_readers(report, RUNTIME_ROOT, "6.4.2")
        report = evidence()
        report["samples"].pop()
        with self.assertRaisesRegex(RuntimeError, "complete required sample"):
            validate_readers(report, RUNTIME_ROOT, "6.4.2")

    def test_lazy_codecs_and_qt_must_be_the_packaged_libraries(self):
        for name in CODEC_MARKERS:
            index = list(READER_SAMPLES).index(name)
            for paths in ([], ["/usr/lib/lib" + name + ".so"], [RUNTIME_ROOT + "/../libhost.so"]):
                report = evidence()
                report["samples"][index]["process"]["mapped_optional_libraries"] = paths
                with self.assertRaises(RuntimeError):
                    validate_readers(report, RUNTIME_ROOT, "6.4.2")
        for where in ("before_decode", "sample"):
            report = evidence()
            process = report["before_decode"] if where == "before_decode" else report["samples"][0]["process"]
            process["mapped_qt_libraries"] = ["/usr/lib/libQt6Quick.so.6.4.2"]
            with self.assertRaisesRegex(RuntimeError, "escaped"):
                validate_readers(report, RUNTIME_ROOT, "6.4.2")
        report = evidence()
        report["samples"][0]["process"]["mapped_qt_libraries"] = []
        with self.assertRaisesRegex(RuntimeError, "lacks actual Core"):
            validate_readers(report, RUNTIME_ROOT, "6.4.2")
        report = evidence()
        report["before_decode"]["mapped_optional_libraries"] = [RUNTIME_ROOT + "/libavif.so.1"]
        with self.assertRaisesRegex(RuntimeError, "before the first decode"):
            validate_readers(report, RUNTIME_ROOT, "6.4.2")

    def test_raw_preview_and_exact_qt_profile_are_required(self):
        for value in (True, None):
            report = evidence()
            report["samples"][list(READER_SAMPLES).index("dng")]["raw_development"] = value
            with self.assertRaisesRegex(RuntimeError, "embedded-preview"):
                validate_readers(report, RUNTIME_ROOT, "6.4.2")
        with self.assertRaisesRegex(RuntimeError, "unexpected Qt"):
            validate_readers(evidence(), RUNTIME_ROOT, "6.8.2")

    def test_success_persists_real_runtime_and_always_removes_exact_container(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(evidence()) if command[1] == "run" else "removed", "runtime log")
        with tempfile.TemporaryDirectory() as temporary, patch("package_reader_checks.subprocess.run", side_effect=run):
            report = run_reader_checks("installed-image", diagnostic="/opt/licasa/usr/bin/licasa_diagnostics",
                                       runtime_root=RUNTIME_ROOT, output_dir=Path(temporary))
            self.assertEqual(report["status"], "passed")
            artifacts = Path(report["evidence_directory"])
            self.assertEqual(json.loads((artifacts / "stdout.json").read_text()), evidence())
            self.assertTrue((artifacts / "runtime.log").is_file())
        name = calls[0][calls[0].index("--name") + 1]
        self.assertEqual(calls[1], ["docker", "rm", "-f", name])
        self.assertIn("--read-only", calls[0])
        self.assertIn("none", calls[0])

    def test_failed_or_timed_out_runtime_cannot_pass_and_is_cleaned_up(self):
        for timeout in (False, True):
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if command[1] == "run":
                    if timeout:
                        raise subprocess.TimeoutExpired(command, 180)
                    return subprocess.CompletedProcess(command, 139, "", "crash")
                return subprocess.CompletedProcess(command, 0, "", "")
            with tempfile.TemporaryDirectory() as temporary, patch("package_reader_checks.subprocess.run", side_effect=run):
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                    run_reader_checks("installed-image", diagnostic="/opt/licasa/usr/bin/licasa_diagnostics",
                                      runtime_root=RUNTIME_ROOT, output_dir=Path(temporary))
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1], ["docker", "rm", "-f", calls[0][calls[0].index("--name") + 1]])


if __name__ == "__main__":
    unittest.main()
