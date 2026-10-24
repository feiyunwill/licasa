#!/usr/bin/env python3
"""Exercise installer failures with fake packages and loader tools, without a GUI."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging/install-licasa-appimage.sh"
BASH = shutil.which("bash")


class AppImageInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="licasa-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.user_home = self.work / "home with spaces"
        self.tools = self.work / "tools"
        self.tools.mkdir()
        self.environment = dict(os.environ)
        for name in ("XDG_DATA_HOME", "BASH_ENV", "ENV", "LD_PRELOAD", "LD_AUDIT"):
            self.environment.pop(name, None)
        self.environment.update(
            HOME=str(self.user_home), PATH=f"{self.tools}:/usr/bin:/bin",
            TMPDIR=str(self.work), CHECKED_FILES=str(self.work / "checked-files"),
            APP_STARTED=str(self.work / "app-started"),
            LD_LIBRARY_PATH="/unrelated/developer/libraries",
        )
        self.write_tool("uname", """case "$1" in
  -s) echo "${TEST_OS:-Linux}" ;;
  -m) echo "${TEST_ARCH:-x86_64}" ;;
  *) exit 1 ;;
esac
""")
        self.write_tool("getconf", """case "$1" in
  LONG_BIT) echo "${TEST_BITS:-64}" ;;
  GNU_LIBC_VERSION) echo "${TEST_LIBC:-glibc 2.39}" ;;
  *) exit 1 ;;
esac
""")
        self.write_tool("ldd", """printf '%s|%s\\n' "$LD_LIBRARY_PATH" "$1" >> "$CHECKED_FILES"
if [[ "$1" == *"${FAILING_FILE:-no-such-file}" ]]; then
  case "${LOADER_FAILURE:-missing}" in
    missing) echo 'libmissing.so.1 => not found'; exit 0 ;;
    version) echo "version GLIBC_2.39 not found" >&2; exit 1 ;;
    version-status-zero) echo "libc.so.6: version GLIBC_2.39 not found" >&2; exit 0 ;;
    status) echo 'loader inspection failed' >&2; exit 2 ;;
  esac
fi
echo 'libc.so.6 => /lib/libc.so.6 (0x1234)'
""")
        self.write_tool("update-desktop-database", 'exit "${CACHE_STATUS:-0}"\n')
        self.appimage = self.write_tool("Licasa fixture.AppImage", """if [[ "$1" != --appimage-extract ]]; then exit 99; fi
if [[ "${EXTRACTION_FAILURE:-0}" == 1 ]]; then
  echo 'fixture extraction failed' >&2
  exit 1
fi
mkdir -p squashfs-root/usr/{bin,lib/licasa/lib,plugins/platforms,share/applications,share/icons/hicolor/512x512/apps}
printf '\\177ELFfixture' > squashfs-root/usr/bin/licasa
printf '\\177ELFfixture' > 'squashfs-root/usr/plugins/platforms/reader with spaces.so'
printf 'not a binary' > squashfs-root/usr/lib/licasa/lib/LICENSE
printf '#!/bin/sh\\necho started > "$APP_STARTED"\\n' > squashfs-root/AppRun
chmod +x squashfs-root/AppRun
printf '[Desktop Entry]\\nMimeType=image/jpeg;\\n' > squashfs-root/usr/share/applications/licasa.desktop
printf 'fixture icon' > squashfs-root/usr/share/icons/hicolor/512x512/apps/licasa.png
""")

    def write_tool(self, name, body):
        path = self.tools / name
        path.write_text(f"#!{BASH}\nset -eu\n{body}")
        path.chmod(0o755)
        return path

    def run_installer(self, **environment):
        completed = subprocess.run(
            [BASH, str(INSTALLER), str(self.appimage)],
            env=dict(self.environment, **environment), capture_output=True, text=True,
            timeout=10,
        )
        self.assertFalse((self.work / "app-started").exists(), "Installer launched the app")
        return completed

    def existing_installation(self):
        target = self.user_home / ".local/opt/licasa-appimage"
        target.mkdir(parents=True)
        paths = {
            target / ".licasa-appimage-install": "existing installation marker",
            target / "AppRun": "existing application",
            self.user_home / ".local/share/applications/licasa-appimage.desktop": "existing launcher",
            self.user_home / ".local/share/icons/hicolor/512x512/apps/licasa-appimage.png": "existing icon",
        }
        for path, contents in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        return paths

    def assert_preserved(self, paths):
        for path, contents in paths.items():
            self.assertEqual(path.read_text(), contents)
        self.assertFalse(list((self.user_home / ".local/opt").glob(".licasa-*")))

    def test_unsupported_platform_stops_before_creating_installation(self):
        for environment in ({"TEST_ARCH": "aarch64"}, {"TEST_BITS": "32"},
                            {"TEST_OS": "Darwin"}):
            with self.subTest(environment=environment):
                result = self.run_installer(**environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("64-bit x86 Linux (Intel or AMD)", result.stderr)
                self.assertFalse(self.user_home.exists())

    def test_non_glibc_system_has_explanation(self):
        result = self.run_installer(TEST_LIBC="musl")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("glibc-based Linux desktop", result.stderr)
        self.assertFalse(self.user_home.exists())

    def test_missing_system_tool_has_explanation(self):
        limited_tools = self.work / "limited-tools"
        limited_tools.mkdir()
        for name in ("uname", "getconf"):
            (limited_tools / name).symlink_to(self.tools / name)
        result = self.run_installer(PATH=str(limited_tools))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("system tool 'ldd' is missing", result.stderr)
        self.assertFalse(self.user_home.exists())

    def test_library_failures_preserve_existing_application_launcher_and_icon(self):
        paths = self.existing_installation()
        for failure in ("missing", "version", "version-status-zero", "status"):
            with self.subTest(failure=failure):
                result = self.run_installer(FAILING_FILE="reader with spaces.so",
                                            LOADER_FAILURE=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Missing or incompatible runtime libraries", result.stderr)
                self.assertIn("before replacing", result.stderr)
                self.assert_preserved(paths)

    def test_extraction_failure_preserves_existing_installation(self):
        paths = self.existing_installation()
        result = self.run_installer(EXTRACTION_FAILURE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not extract the AppImage", result.stderr)
        self.assertIn("SHA-256", result.stderr)
        self.assertIn("fixture extraction failed", result.stderr)
        self.assert_preserved(paths)

    def test_success_checks_nested_plugins_and_reinstalls_without_launch(self):
        for _ in range(2):
            result = self.run_installer()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Runtime library check passed (2 ELF files)", result.stdout)
        checks = (self.work / "checked-files").read_text().splitlines()
        self.assertEqual(len(checks), 4)
        self.assertTrue(any("reader with spaces.so" in line for line in checks))
        for line in checks:
            library_path, _ = line.split("|", 1)
            self.assertNotIn("/unrelated/developer", library_path)
            self.assertIn("squashfs-root/usr/lib/licasa/lib:", library_path)
        launcher = self.user_home / ".local/share/applications/licasa-appimage.desktop"
        self.assertIn(f'Exec="{self.user_home}/.local/opt/licasa-appimage/AppRun" %f',
                      launcher.read_text())

    def test_desktop_cache_failure_reports_success_with_warning(self):
        result = self.run_installer(CACHE_STATUS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Licasa was installed", result.stderr)
        self.assertIn("Installed Licasa", result.stdout)

    def test_home_path_containing_not_found_is_not_a_missing_library(self):
        self.user_home = self.work / "home not found"
        result = self.run_installer(HOME=str(self.user_home))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sourcing_checker_does_not_start_installation(self):
        result = subprocess.run(
            [BASH, "-c", '. "$1"; declare -F licasa_check_runtime_dependencies',
             "_", str(INSTALLER)], env=self.environment, capture_output=True, text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.user_home.exists())
        self.assertFalse((self.work / "checked-files").exists())

    def test_missing_scan_paths_and_empty_scans_fail(self):
        empty = self.work / "empty"
        empty.mkdir()
        for path in (empty, self.work / "missing"):
            with self.subTest(path=path):
                result = subprocess.run(
                    [BASH, "-c", '. "$1"; licasa_check_runtime_dependencies /private "$2"',
                     "_", str(INSTALLER), str(path)], env=self.environment,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
