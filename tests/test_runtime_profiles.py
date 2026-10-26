#!/usr/bin/env python3
"""Fail-closed coverage for distro-private Qt/FFmpeg qualification."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from runtime_profiles import PROFILES, configured_qt_version, profile_work_dir, verify_ffmpeg_sonames
from build_qt_ffmpeg import audit_upstream_fixes


class RuntimeProfiles(unittest.TestCase):
    def test_ubuntu_layout_is_preserved_and_debian_is_isolated(self):
        self.assertEqual(profile_work_dir(Path("deps"), "ubuntu-6.4.2"), Path("deps"))
        self.assertEqual(profile_work_dir(Path("deps"), "debian-6.8.2"), Path("deps/debian-6.8.2"))

    def test_configured_qt_version_reads_both_qt_cmake_layouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            cache = f"Qt6Core_DIR:PATH={path}\n"
            (path / "Qt6CoreConfigVersion.cmake").write_text('set(PACKAGE_VERSION "6.4.2")\n')
            self.assertEqual(configured_qt_version(cache), "6.4.2")
            (path / "Qt6CoreConfigVersionImpl.cmake").write_text('set(PACKAGE_VERSION "6.8.2")\n')
            self.assertEqual(configured_qt_version(cache), "6.8.2")

    def test_missing_configured_qt_is_rejected(self):
        with self.assertRaises(RuntimeError):
            configured_qt_version("Qt6Core_DIR-NOTFOUND\n")

    def test_debian_rejects_ubuntu_ffmpeg_abi(self):
        dynamic = "\n".join(f"(NEEDED) Shared library: [{name}]"
                            for name in PROFILES["ubuntu-6.4.2"]["ffmpeg_sonames"])
        verify_ffmpeg_sonames(dynamic, "ubuntu-6.4.2")
        with self.assertRaisesRegex(RuntimeError, "ABI mismatch"):
            verify_ffmpeg_sonames(dynamic, "debian-6.8.2")

    def test_debian_requires_complete_native_ffmpeg_abi(self):
        names = PROFILES["debian-6.8.2"]["ffmpeg_sonames"]
        dynamic = "\n".join(f"(NEEDED) Shared library: [{name}]" for name in names)
        self.assertEqual(verify_ffmpeg_sonames(dynamic, "debian-6.8.2"), sorted(names))
        with self.assertRaises(RuntimeError):
            verify_ffmpeg_sonames(dynamic.replace(names[0], "libavcodec.so.60"), "debian-6.8.2")

    def test_reviewed_upstream_multimedia_fixes_cannot_change_silently(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "guarded.cpp").write_bytes(b"reviewed source")
            spec = {"verified_upstream_files": {"guarded.cpp":
                    hashlib.sha256(b"reviewed source").hexdigest()}}
            audit_upstream_fixes(source, spec, source, offline=True)
            (source / "guarded.cpp").write_bytes(b"upgraded without reviewing ownership")
            with self.assertRaisesRegex(RuntimeError, "Reviewed upstream Qt fix changed"):
                audit_upstream_fixes(source, spec, source, offline=True)

    def test_quick_source_upgrade_requires_reauditing_texture_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            archive = source / "quick.tar.xz"
            expected = hashlib.sha256(b"reviewed cleanup").hexdigest()
            for contents in (b"reviewed cleanup", b"upgraded source"):
                with tarfile.open(archive, "w:xz") as bundle:
                    member = tarfile.TarInfo("qtdeclarative-everywhere-src-6.8.2/context.cpp")
                    member.size = len(contents)
                    bundle.addfile(member, io.BytesIO(contents))
                spec = {"version": "6.8.2", "quick_source_audit": {
                    "url": "https://example.invalid/quick.tar.xz",
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "file": "context.cpp", "file_sha256": expected}}
                if contents == b"reviewed cleanup":
                    audit_upstream_fixes(source, spec, source, offline=True)
                else:
                    with self.assertRaisesRegex(RuntimeError, "texture cleanup changed"):
                        audit_upstream_fixes(source, spec, source, offline=True)

    def check_cmake_receipt(self, profile, mutate=None, missing=False):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            plugin = path / "libffmpegmediaplugin.so"
            plugin.write_bytes(b"test plugin")
            spec = json.loads((ROOT / PROFILES[profile]["ffmpeg_lock"]).read_text())
            receipt = {
                "runtime_profile": profile, "qt_version": spec["version"],
                "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                "source_sha256": spec["sha256"],
                "distro_patch_sha256": spec.get("debian_patch_sha256", spec.get("ubuntu_patch_sha256")),
                "distro_patches": spec.get("debian_patches", [spec.get("ubuntu_abi_patch")]),
                "required_features": spec["required_features"],
                "disabled_features": spec["disabled_features"],
                "patches": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                            for name in spec["patches"]},
            }
            if profile == "ubuntu-6.4.2":
                receipt["wakeup_qualification"] = {"status": "passed", "harness_sha256":
                    hashlib.sha256((ROOT / "tools/verify_qt_ffmpeg_wakeup.py").read_bytes()).hexdigest()}
            if mutate:
                mutate(receipt)
            if not missing:
                (path / "build.json").write_text(json.dumps(receipt))
            script = path / "verify.cmake"
            script.write_text(f'set(CMAKE_CURRENT_SOURCE_DIR "{ROOT}")\n'
                              f'set(LICASA_RUNTIME_PROFILE "{profile}" CACHE STRING "")\n'
                              f'include("{ROOT}/cmake/LicasaRuntimeProfiles.cmake")\n'
                              f'licasa_verify_ffmpeg_runtime("{plugin}")\n')
            return subprocess.run(["cmake", "-P", str(script)], text=True, capture_output=True)

    def test_cmake_accepts_exact_profiles(self):
        for profile in PROFILES:
            result = self.check_cmake_receipt(profile)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cmake_rejects_missing_or_stale_private_runtime(self):
        self.assertNotEqual(self.check_cmake_receipt("debian-6.8.2", missing=True).returncode, 0)
        for key, value in (("runtime_profile", "ubuntu-6.4.2"), ("qt_version", "6.8.3"),
                           ("source_sha256", "wrong"), ("plugin_sha256", "wrong"),
                           ("patches", {}), ("distro_patch_sha256", "wrong"),
                           ("distro_patches", ["different.patch"]),
                           ("required_features", [])):
            result = self.check_cmake_receipt("debian-6.8.2", lambda item: item.update({key: value}))
            self.assertNotEqual(result.returncode, 0, f"accepted invalid {key}")

    def test_ubuntu_distro_patch_archive_and_selection_are_checked(self):
        for key, value in (("distro_patch_sha256", "wrong"),
                           ("distro_patches", ["different.patch"]),
                           ("disabled_features", [])):
            result = self.check_cmake_receipt("ubuntu-6.4.2", lambda item: item.update({key: value}))
            self.assertNotEqual(result.returncode, 0, f"accepted invalid Ubuntu {key}")

    def test_ubuntu_requires_demuxer_wakeup_patch_and_qualification(self):
        patch = "packaging/patches/qt-6.4-ffmpeg-demuxer-start-wakeup.patch"
        for mutate in (
                lambda item: item["patches"].pop(patch),
                lambda item: item["patches"].update({patch: "wrong"}),
                lambda item: item.pop("wakeup_qualification"),
                lambda item: item.update(wakeup_qualification={"status": "failed"}),
                lambda item: item.update(wakeup_qualification={"status": "passed", "harness_sha256": "wrong"})):
            result = self.check_cmake_receipt("ubuntu-6.4.2", mutate)
            self.assertNotEqual(result.returncode, 0, "accepted unqualified demuxer wakeup behavior")

    def check_quick_receipt(self, mutate=None, *, missing=False, tamper=False, profile="ubuntu-6.4.2"):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            library = path / "libQt6Quick.so.6.4.2"
            library.write_bytes(b"qualified private Quick")
            spec = json.loads((ROOT / "packaging/qt-backport.lock.json").read_text())
            receipt = {"runtime_profile": "ubuntu-6.4.2", "qt_version": spec["version"],
                       "source_sha256": spec["sha256"],
                       "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
                       "patches": {spec["patch"]: hashlib.sha256((ROOT / spec["patch"]).read_bytes()).hexdigest()}}
            if mutate:
                mutate(receipt)
            if not missing:
                (path / "build.json").write_text(json.dumps(receipt))
            if tamper:
                library.write_bytes(b"stock Quick with the same filename")
            script = path / "verify.cmake"
            script.write_text(f'set(LICASA_RUNTIME_PROFILE "{profile}" CACHE STRING "")\n'
                              f'include("{ROOT}/cmake/LicasaRuntimeProfiles.cmake")\n'
                              f'licasa_verify_quick_runtime("{library}")\n')
            return subprocess.run(["cmake", "-P", str(script)], text=True, capture_output=True)

    def test_cmake_accepts_qualified_quick_receipt(self):
        result = self.check_quick_receipt()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cmake_rejects_missing_tampered_or_stale_quick(self):
        self.assertNotEqual(self.check_quick_receipt(missing=True).returncode, 0)
        self.assertNotEqual(self.check_quick_receipt(tamper=True).returncode, 0)
        for key, value in (("runtime_profile", "debian-6.8.2"), ("qt_version", "6.4.3"),
                           ("source_sha256", "wrong"), ("library_sha256", "wrong"),
                           ("patches", {}), ("patches", {"different.patch": "wrong"})):
            result = self.check_quick_receipt(lambda receipt: receipt.update({key: value}))
            self.assertNotEqual(result.returncode, 0, f"accepted invalid Quick {key}")
        spec = json.loads((ROOT / "packaging/qt-backport.lock.json").read_text())
        result = self.check_quick_receipt(lambda receipt: receipt["patches"].update({spec["patch"]: "wrong"}))
        self.assertNotEqual(result.returncode, 0, "accepted changed Quick patch hash")

    def test_debian_cannot_select_a_private_ubuntu_quick_runtime(self):
        self.assertNotEqual(self.check_quick_receipt(profile="debian-6.8.2").returncode, 0)


if __name__ == "__main__":
    unittest.main()
