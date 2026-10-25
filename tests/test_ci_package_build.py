#!/usr/bin/env python3
"""Keep PR package builds on canonical recipes with isolated, profile-exact caches."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_ci_packages import command_for
from check_package_report import check_report
from qualification_evidence import digest


class CiPackageBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="licasa PR package ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def command(self, target, reuse=False):
        return command_for(target, image="candidate-builder", output=self.root / "packages",
            dependencies=self.root / target, jobs=3, reuse=reuse, source=self.root / "source")

    def test_ubuntu_uses_canonical_recipe_and_private_runtime_locations(self):
        command = self.command("ubuntu-24.04", reuse=True)
        self.assertEqual(command[-3:], ["candidate-builder", "bash", "/source/packaging/verify-fresh-build.sh"])
        self.assertIn("LICASA_REUSE_DEPENDENCIES=true", command)
        self.assertTrue(any(value.endswith(":/source:ro") for value in command))
        self.assertTrue(any(value.endswith(":/work/licasa/build/deps/qt-ffmpeg-runtime-release") for value in command))
        self.assertNotIn("--privileged", command)

    def test_debian_profile_cache_requires_its_receipt_and_never_reuses_ubuntu_paths(self):
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.command("debian-13", reuse=True)
        command = self.command("debian-13")
        script = command[command.index("-ec") + 1]
        self.assertIn("tools/build_debian_deb.py", script)
        self.assertIn("--exclude='./build*'", script)
        self.assertNotIn("cp -a", script)
        self.assertTrue(any("debian-6.8.2/qt-ffmpeg-runtime-release" in value for value in command))
        self.assertIn("--skip-dependencies", self.command("debian-13", reuse=True))

    def test_pr_report_cannot_qualify_a_different_artifact(self):
        package = self.root / "candidate.deb"
        package.write_bytes(b"candidate")
        report = {"package_sha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "different candidate"):
            check_report(report, {}, {}, package)
        report["package_sha256"] = digest(package)
        with patch("check_package_report.validate_report") as strict:
            check_report(report, {"id": "candidate"}, {}, package)
            strict.assert_called_once_with(report, {"id": "candidate"}, {})


if __name__ == "__main__":
    unittest.main()
