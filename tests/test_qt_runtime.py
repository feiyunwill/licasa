#!/usr/bin/env python3
"""Reject incomplete or mismatched runtime imports before creating a package."""

from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import qt_runtime
import package_deb


class RuntimeClosureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="licasa-qml-runtime-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.qml = self.root / "qml"
        self.lib = self.root / "lib"
        self.lib.mkdir()
        for module in qt_runtime.requirements()["modules"]:
            directory = qt_runtime.module_directory(self.qml, module["uri"])
            directory.mkdir(parents=True)
            plugins = module["plugins"]["6.4.2"]
            (directory / "qmldir").write_text(f"module {module['uri']}\n" +
                                             "".join(f"optional plugin {name}\n" for name in plugins))
            for name in plugins:
                (directory / f"lib{name}.so").write_bytes(b"fixture plugin")
            for name in module.get("libraries", []):
                (self.lib / name).write_bytes(b"fixture companion library")

    def test_full_closure_produces_an_inventory(self):
        report = qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")
        self.assertEqual(report["status"], "passed")
        folder = next(m for m in report["modules"] if m["uri"] == "Qt.labs.folderlistmodel")
        self.assertIn("libQt6LabsFolderListModel.so.6", folder["files"])

    def test_missing_fallback_module_is_rejected(self):
        shutil.rmtree(qt_runtime.module_directory(self.qml, "Qt.labs.folderlistmodel"))
        with self.assertRaisesRegex(RuntimeError, "Missing QML runtime file"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_missing_fallback_style_modules_are_rejected_for_both_profiles(self):
        for version in ("6.4.2", "6.8.2"):
            for uri in ("QtQuick.Controls.Basic", "QtQuick.Controls.Basic.impl"):
                with self.subTest(version=version, uri=uri):
                    isolated = self.root / (version + "-" + uri)
                    shutil.copytree(self.qml, isolated)
                    directory = qt_runtime.module_directory(isolated, uri)
                    shutil.rmtree(directory)
                    with self.assertRaisesRegex(RuntimeError, re.escape(str(directory / "qmldir"))):
                        qt_runtime.validate_runtime(isolated, self.lib, version)

    def test_optional_plugin_still_required_in_shared_runtime(self):
        (self.qml / "QtQuick/Dialogs/libqtquickdialogsplugin.so").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing QML runtime file"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_removed_plugin_declaration_and_binary_cannot_hide_missing_runtime(self):
        directory = self.qml / "Qt/labs/folderlistmodel"
        (directory / "qmldir").write_text("module Qt.labs.folderlistmodel\n")
        (directory / "libqmlfolderlistmodelplugin.so").unlink()
        with self.assertRaisesRegex(RuntimeError, "plugin identity mismatch"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_different_plugin_with_matching_module_uri_is_rejected(self):
        directory = self.qml / "Qt/labs/folderlistmodel"
        (directory / "qmldir").write_text("module Qt.labs.folderlistmodel\nplugin unrelated\n")
        (directory / "libunrelated.so").write_bytes(b"wrong plugin")
        with self.assertRaisesRegex(RuntimeError, "plugin identity mismatch"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_duplicate_plugin_declarations_are_rejected(self):
        directory = self.qml / "Qt/labs/folderlistmodel"
        with (directory / "qmldir").open("a") as metadata:
            metadata.write("plugin qmlfolderlistmodelplugin\n")
        with self.assertRaisesRegex(RuntimeError, "plugin identity mismatch"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_explicit_plugin_contracts_cover_both_profiles(self):
        requirements = qt_runtime.requirements()
        for module in requirements["modules"]:
            self.assertEqual(set(module["plugins"]), {"6.4.2", "6.8.2"})
        for version in requirements["qt_versions"]:
            with self.subTest(version=version):
                self.assertEqual(qt_runtime.validate_runtime(self.qml, self.lib, version)["status"], "passed")

    def test_qtqml_itself_is_included_in_the_runtime_closure(self):
        module = next(module for module in qt_runtime.requirements()["modules"] if module["uri"] == "QtQml")
        self.assertTrue(module["deploy_explicitly"])
        self.assertIn("qml6-module-qtqml", qt_runtime.runtime_packages())

    def test_wrong_module_under_expected_path_is_rejected(self):
        (self.qml / "Qt/labs/folderlistmodel/qmldir").write_text("module Qt.labs.settings\n")
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_host_library_symlink_cannot_satisfy_bundle(self):
        path = self.lib / "libQt6LabsFolderListModel.so.6"
        path.unlink()
        outside = self.root / "host-library.so"
        outside.write_bytes(b"host Qt")
        path.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "escapes its runtime root"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.4.2")

    def test_unknown_qt_profile_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "no qualified runtime-module profile"):
            qt_runtime.validate_runtime(self.qml, self.lib, "6.5.0")


class NativeTargetContractTests(unittest.TestCase):
    @staticmethod
    def cache(profile):
        return (f"LICASA_RUNTIME_PROFILE:STRING={profile}\n"
                "LICASA_QT_QUICK_BACKPORT_LIBRARY:FILEPATH=/private/libQt6Quick.so.6.4.2\n")

    def test_ubuntu_payload_cannot_be_labeled_debian(self):
        with self.assertRaisesRegex(RuntimeError, "requires runtime profile"):
            package_deb.validate_target("debian-13", self.cache("ubuntu-6.4.2"),
                                        'ID=debian\nVERSION_ID="13"\n')

    def test_debian_build_must_run_in_debian_userspace(self):
        with self.assertRaisesRegex(RuntimeError, "matching target userspace"):
            package_deb.validate_target("debian-13", self.cache("debian-6.8.2"),
                                        'ID=ubuntu\nVERSION_ID="24.04"\n')

    def test_debian_native_uses_distinct_revision(self):
        target = package_deb.validate_target("debian-13", self.cache("debian-6.8.2"),
                                             'ID=debian\nVERSION_ID="13"\n')
        self.assertEqual(target["revision_suffix"], "+deb13")

    def test_private_qt_abi_cannot_be_weakened(self):
        for dependencies in (["libqt6core6t64 (>= 6.8.2)"], ["qt6-base-abi (= 6.4.2)"],
                             ["qt6-base-abi (= 6.8.2)"]):
            with self.subTest(dependencies=dependencies), self.assertRaisesRegex(RuntimeError, "exact ABI"):
                package_deb.validate_abi_dependencies(dependencies, "6.8.2", "qt6-base-private-abi")
        package_deb.validate_abi_dependencies(["qt6-base-private-abi (= 6.8.2)"], "6.8.2",
                                              package_deb.TARGETS["debian-13"]["abi_package"])
        package_deb.validate_abi_dependencies(["qt6-base-abi (= 6.4.2)"], "6.4.2",
                                              package_deb.TARGETS["ubuntu-24.04"]["abi_package"])

    def test_native_runtime_includes_transitive_dialog_dependency(self):
        self.assertIn("qml6-module-qt-labs-folderlistmodel", package_deb.QML_RUNTIME)

    def test_debian_svg_plugin_split_is_explicit(self):
        self.assertIn("qt6-svg-plugins", package_deb.TARGETS["debian-13"]["runtime_packages"])
        self.assertNotIn("qt6-svg-plugins", package_deb.TARGETS["ubuntu-24.04"]["runtime_packages"])


if __name__ == "__main__":
    unittest.main()
