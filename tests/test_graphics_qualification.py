#!/usr/bin/env python3
"""Exercise saved-log graphics qualification without touching a display or GPU."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools/check_display_session.sh"
RETRY = "Graphics initialization failed; retrying once with the Qt software renderer\n"


def graphics_log(api="OpenGL", renderer="Mesa Intel(R) Graphics (RPL-P)"):
    driver = ("qt.rhi.general: OpenGL VENDOR: Intel RENDERER: " + renderer
              + " VERSION: 4.6 (Core Profile) Mesa 26.2\n") if renderer else ""
    return driver + ("[Licasa] Qt Quick backend (sceneGraphInitialized): graphicsApi="
                     + api + ", sceneGraphBackend=<default>, requestedByEnv=<auto>, rhiBased=true\n")


class GraphicsResultTests(unittest.TestCase):
    def assess(self, diagnostic, application, *, fallback=False, policy="require-accelerated"):
        with tempfile.TemporaryDirectory(prefix="licasa-graphics-log-fixture-") as temporary:
            directory = Path(temporary)
            diagnostic_log = directory / "diagnostic.log"
            application_log = directory / "application.log"
            diagnostic_log.write_text(diagnostic)
            application_log.write_text(application)
            return subprocess.run(
                ["sh", str(CHECK), "--check-graphics-results", str(diagnostic_log),
                 str(application_log), str(fallback).lower(), policy],
                capture_output=True, text=True, timeout=5)

    def test_hardware_driver_and_both_initialized_opengl_paths_pass(self):
        for renderer in ("Mesa Intel(R) Graphics (RPL-P)", "NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2"):
            with self.subTest(renderer=renderer):
                result = self.assess(graphics_log(renderer=renderer), graphics_log(renderer=renderer))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Diagnostic graphics: graphicsApi=OpenGL; renderer=" + renderer, result.stdout)
                self.assertIn("Application graphics: graphicsApi=OpenGL; renderer=" + renderer, result.stdout)

    def test_diagnostic_success_cannot_mask_production_software_retry(self):
        application = RETRY + graphics_log("Software", renderer=None)
        result = self.assess(graphics_log(), application)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rejected a software fallback", result.stderr)
        self.assertIn("Diagnostic graphics: graphicsApi=OpenGL", result.stdout)
        self.assertIn("Application graphics: graphicsApi=Software; renderer=unreported; retry=true", result.stdout)
        # The same result remains valid fallback coverage in a no-GPU environment.
        permitted = self.assess(graphics_log(), application, policy="allow-fallback")
        self.assertEqual(permitted.returncode, 0, permitted.stderr)

    def test_opengl_api_with_cpu_driver_cannot_pass_hardware_qualification(self):
        for renderer in ("llvmpipe (LLVM 21.1.8, 256 bits)", "softpipe", "lavapipe",
                         "Software Rasterizer", "OpenSWR", "Google SwiftShader"):
            for failing_path in ("diagnostic", "application"):
                with self.subTest(renderer=renderer, failing_path=failing_path):
                    logs = {"diagnostic": graphics_log(), "application": graphics_log()}
                    logs[failing_path] = graphics_log(renderer=renderer)
                    result = self.assess(**logs)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("rejected a software graphics driver", result.stderr)

    def test_production_backend_is_required_even_when_diagnostic_passes(self):
        for application in ("Failed to load graphics integration plugin\n", graphics_log("Unknown"), graphics_log("Null")):
            with self.subTest(application=application):
                result = self.assess(graphics_log(), application, policy="allow-fallback")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Missing or unusable initialized graphics API", result.stderr)

    def test_missing_hardware_driver_identity_cannot_pass_strict_mode(self):
        for diagnostic, application in ((graphics_log(renderer=None), graphics_log()),
                                        (graphics_log(), graphics_log(renderer=None))):
            result = self.assess(diagnostic, application)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("verifiable hardware renderer", result.stderr)

    def test_software_api_without_retry_is_still_rejected_in_strict_mode(self):
        result = self.assess(graphics_log(), graphics_log("Software", renderer=None))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verifiable hardware renderer", result.stderr)

    def test_explicit_software_and_diagnostic_fallback_coverage_remain_valid(self):
        software = graphics_log("Software", renderer=None)
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                result = self.assess(software, RETRY + software, fallback=fallback, policy="allow-fallback")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("graphicsApi=Software", result.stdout)
                if fallback:
                    strict = self.assess(software, RETRY + software, fallback=True)
                    self.assertNotEqual(strict.returncode, 0)
                    self.assertIn("rejected a software fallback", strict.stderr)

    def test_reported_retry_must_actually_initialize_software(self):
        result = self.assess(graphics_log(), RETRY + graphics_log(), policy="allow-fallback")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Application retry did not initialize the software renderer", result.stderr)

    def test_diagnostic_cannot_receive_an_extra_library_search_override(self):
        source = CHECK.read_text()
        # A separate private-library override previously hid the missing
        # deployment RUNPATH while the actual launcher fell back to software.
        self.assertNotRegex(source, r"(?:export\s+)?LD_LIBRARY_PATH=")
        self.assertIn("unset QT_QUICK_BACKEND QSG_RHI_BACKEND LIBGL_ALWAYS_SOFTWARE LD_LIBRARY_PATH", source)
        subprocess.run(["sh", "-n", str(CHECK)], check=True)


if __name__ == "__main__":
    unittest.main()
