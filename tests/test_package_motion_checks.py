#!/usr/bin/env python3
"""Packaged Motion qualification must fail closed without real playback evidence."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_motion_checks import FFMPEG, REQUIRED_CONTROLS, make_fixture, validate_controls
import package_motion_checks as checks


class PackagedMotionTests(unittest.TestCase):
    def controls(self, version="6.8.2", rotation=0):
        values = {name: True for name in REQUIRED_CONTROLS}
        values.update(final_session_available=False, final_session_active=False,
                      final_session_playing=False, final_control_present=False,
                      saved_frame_width=240 if rotation else 320,
                      saved_frame_height=320 if rotation else 240)
        values["process_after_clear"] = {
            "mapped_optional_libraries": [
                "/usr/lib/licasa/plugins/multimedia/libffmpegmediaplugin.so",
                f"/usr/lib/x86_64-linux-gnu/libQt6Multimedia.so.{version}",
                *(f"/usr/lib/x86_64-linux-gnu/{name}.1.2" for name in FFMPEG[version])],
            "mapped_qt_libraries": [f"/usr/lib/x86_64-linux-gnu/libQt6{name}.so.{version}"
                                    for name in ("Core", "Qml", "Quick")],
        }
        return values

    def test_fixture_is_deterministic_and_bounds_the_embedded_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ("first.jpg", "second.jpg"))
            report = make_fixture(first, 90)
            self.assertEqual(report, make_fixture(second, 90))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            clip = (ROOT / "tests/test-assets/modern/motion/qt-runtime-90deg.mp4").read_bytes()
            content = first.read_bytes()
            self.assertEqual(content[report["video_offset"]:], clip)
            self.assertGreater(report["video_offset"], 0)
            self.assertEqual(content[report["video_offset"] - 2:report["video_offset"]], b"\xff\xd9")
            self.assertIn(f'Item:Length="{len(clip)}"'.encode(), content)
            self.assertTrue(report["embedded_custom_io"])

    def test_corrupt_motion_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            source = assets / "modern/motion"
            source.mkdir(parents=True)
            (source / "qt-runtime-0deg.mp4").write_bytes(b"corrupt")
            shutil.copy2(ROOT / "tests/test-assets/modern/motion/sha256.json", source / "sha256.json")
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                make_fixture(assets / "fixture.jpg", 0, assets)

    def test_complete_native_evidence_matches_both_exact_profiles(self):
        for version in FFMPEG:
            for rotation in (0, 90):
                validate_controls(self.controls(version, rotation), "/usr", version, rotation)

    def test_motion_cannot_pass_with_a_still_image_only(self):
        for name in ("play_from_visible_control", "scrub_presentation_frame_visible",
                     "captured_frame_saved_and_decoded", "clear_removes_motion_authority"):
            evidence = self.controls()
            evidence[name] = False
            with self.assertRaisesRegex(RuntimeError, "Incomplete packaged Motion lifecycle"):
                validate_controls(evidence, "/usr", "6.8.2", 0)

    def test_rotated_output_and_teardown_are_required(self):
        with self.assertRaisesRegex(RuntimeError, "orientation"):
            validate_controls(self.controls(), "/usr", "6.8.2", 90)
        evidence = self.controls()
        evidence["final_session_active"] = True
        with self.assertRaisesRegex(RuntimeError, "survived"):
            validate_controls(evidence, "/usr", "6.8.2", 0)

    def test_wrong_backend_profile_and_host_libraries_are_rejected(self):
        for replacement in (
                "/usr/lib/x86_64-linux-gnu/qt6/plugins/multimedia/libffmpegmediaplugin.so",
                "/tmp/host/libffmpegmediaplugin.so"):
            evidence = self.controls()
            evidence["process_after_clear"]["mapped_optional_libraries"][0] = replacement
            with self.assertRaises(RuntimeError):
                validate_controls(evidence, "/usr", "6.8.2", 0)
        with self.assertRaises(RuntimeError):
            validate_controls(self.controls("6.4.2"), "/usr", "6.8.2", 0)

    def test_direct_diagnostic_loads_the_portable_runtime_and_keeps_session_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            diagnostic = root / "diagnostic"
            diagnostic.write_bytes(b"production binary identity")
            appdir = root / "usr"
            private = appdir / "lib/licasa/plugins/multimedia/libffmpegmediaplugin.so"
            private.parent.mkdir(parents=True)
            private.write_bytes(b"private plugin identity")
            environment = {"DISPLAY": ":93", "WAYLAND_DISPLAY": "nested",
                           "LD_LIBRARY_PATH": "/unrelated/host/lib"}
            args = SimpleNamespace(output=root / "report.json", diagnostic=diagnostic,
                fixture=root / "motion.jpg", runtime_root=str(appdir), qt_version="6.4.2",
                mode="software", protocol="x11", rotation_degrees=0)
            def run(command, **kwargs):
                self.assertEqual(kwargs["env"]["LD_LIBRARY_PATH"],
                    f"{appdir}/lib/licasa/lib:{appdir}/lib:/unrelated/host/lib")
                self.assertEqual(kwargs["env"]["DISPLAY"], ":93")
                self.assertEqual(kwargs["env"]["WAYLAND_DISPLAY"], "nested")
                if "--formats" in command:
                    return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                        "qt_version": "6.4.2", "samples": [{"decoded": True}], "before_decode": {
                            "mapped_qt_libraries": [f"{appdir}/lib/libQt6{name}.so.6"
                                                    for name in ("Core", "Qml", "Quick")]}}))
                (root / "motion-frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")
                return SimpleNamespace(returncode=0, stdout=json.dumps(self.controls("6.4.2")), stderr="")
            with patch.object(checks.subprocess, "run", side_effect=run), \
                    patch.object(checks, "validate_controls", return_value={"private_ffmpeg_plugin": str(private)}):
                checks.installed_check(args, environment=environment)
            self.assertEqual(environment["LD_LIBRARY_PATH"], "/unrelated/host/lib")
            stock = root / "stock-ffmpeg.so"
            stock.write_bytes(b"unqualified distro backend")
            with patch.object(checks.subprocess, "run", side_effect=run), \
                    patch.object(checks, "validate_controls", return_value={"private_ffmpeg_plugin": str(stock)}):
                with self.assertRaisesRegex(RuntimeError, "different FFmpeg plugin"):
                    checks.installed_check(args, environment=environment)

    def test_portable_sonames_require_actual_matching_version_and_library_probes(self):
        controls = self.controls("6.4.2")
        process = controls["process_after_clear"]
        process["mapped_qt_libraries"] = [f"/opt/licasa/usr/lib/libQt6{name}.so.6" for name in ("Core", "Qml", "Quick")]
        process["mapped_optional_libraries"] = ["/opt/licasa/usr/plugins/multimedia/libffmpegmediaplugin.so",
            "/opt/licasa/usr/lib/libQt6Multimedia.so.6", *["/opt/licasa/usr/lib/" + name for name in FFMPEG["6.4.2"]]]
        probe = {"qt_version": "6.4.2", "before_decode": {"mapped_qt_libraries": process["mapped_qt_libraries"]}}
        validate_controls(controls, "/opt/licasa/usr", "6.4.2", 0, probe)
        for changed in (None, {**probe, "qt_version": "6.8.2"},
                        {**probe, "before_decode": {"mapped_qt_libraries": []}},
                        {**probe, "before_decode": {"mapped_qt_libraries": ["/usr/lib/libQt6Core.so.6"]}}):
            with self.subTest(probe=changed), self.assertRaises(RuntimeError):
                validate_controls(controls, "/opt/licasa/usr", "6.4.2", 0, changed)


if __name__ == "__main__":
    unittest.main()
