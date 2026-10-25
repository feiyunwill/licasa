#!/usr/bin/env python3
"""Stage a Release build as a target-specific Ubuntu or Debian amd64 package."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from qt_runtime import runtime_packages


ROOT = Path(__file__).resolve().parents[1]
QML_RUNTIME = ("libqt6svg6", "qt6-wayland", *runtime_packages())
TARGETS = {
    "ubuntu-24.04": {"os_id": "ubuntu", "version_id": "24.04",
                     "profile": "ubuntu-6.4.2", "qt_version": "6.4.2", "revision_suffix": "",
                     "abi_package": "qt6-base-abi", "runtime_packages": ()},
    "debian-13": {"os_id": "debian", "version_id": "13",
                  "profile": "debian-6.8.2", "qt_version": "6.8.2", "revision_suffix": "+deb13",
                  "abi_package": "qt6-base-private-abi", "runtime_packages": ("qt6-svg-plugins",)},
}
REQUIRED_FEATURES = (
    "LICASA_BUILD_DIAGNOSTICS", "LICASA_ENABLE_HEIF", "LICASA_ENABLE_JXL",
    "LICASA_ENABLE_RAW", "LICASA_ENABLE_AVIF", "LICASA_ENABLE_APNG",
    "LICASA_ENABLE_JP2", "LICASA_ENABLE_MOTION_PLAYBACK",
)


def cache_value(cache: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:[^=]*=(.*)$", cache, re.MULTILINE)
    if not match:
        raise RuntimeError(f"{name} is missing from the CMake cache")
    return match.group(1)


def is_regular_elf(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as candidate:
        return candidate.read(4) == b"\x7fELF"


def validate_target(target: str, cache: str, os_release: str) -> dict:
    spec = TARGETS[target]
    system = dict(line.split("=", 1) for line in os_release.splitlines()
                  if "=" in line and not line.startswith("#"))
    if (system.get("ID", "").strip('"') != spec["os_id"]
            or system.get("VERSION_ID", "").strip('"') != spec["version_id"]):
        raise RuntimeError(f"Build and package {target} inside its matching target userspace")
    if cache_value(cache, "LICASA_RUNTIME_PROFILE") != spec["profile"]:
        raise RuntimeError(f"{target} requires runtime profile {spec['profile']}")
    if target == "ubuntu-24.04" and not cache_value(cache, "LICASA_QT_QUICK_BACKPORT_LIBRARY"):
        raise RuntimeError("The qualified private Qt Quick runtime is required for Ubuntu 24.04")
    return spec


def validate_abi_dependencies(dependencies: list[str], qt_version: str,
                              abi_package: str = "qt6-base-abi") -> None:
    expected = f"{abi_package} (= {qt_version})"
    if expected not in dependencies:
        raise RuntimeError(f"Private Qt runtime must retain its exact ABI dependency: {expected}")


def package_dependencies(stage: Path, work: Path, target: str = "ubuntu-24.04") -> list[str]:
    (work / "debian").mkdir()
    (work / "debian/control").write_text(
        "Source: licasa\nSection: graphics\nPriority: optional\n"
        "Maintainer: Sanxchep\nStandards-Version: 4.7.0\n\n"
        "Package: licasa\nArchitecture: any\nDescription: Image viewer\n"
    )
    elf_files = sorted(path for path in (stage / "usr").rglob("*") if is_regular_elf(path))
    if stage / "usr/bin/licasa" not in elf_files:
        raise RuntimeError("Staged package has no Licasa executable")
    private_libs = stage / "usr/lib/licasa/lib"
    command = [
        "dpkg-shlibdeps", "--ignore-missing-info", "-O", f"-S{stage}",
        f"-l{private_libs}", "-xlicasa", *map(str, elf_files),
    ]
    result = subprocess.run(command, cwd=work, capture_output=True, text=True, check=True)
    marker = "shlibs:Depends="
    line = next((line for line in result.stdout.splitlines() if line.startswith(marker)), None)
    if line is None:
        raise RuntimeError("dpkg-shlibdeps returned no runtime dependencies")
    dependencies = line.removeprefix(marker).split(", ")
    present = {dependency.split(" ", 1)[0] for dependency in dependencies}
    dependencies.extend(name for name in (*QML_RUNTIME, *TARGETS[target]["runtime_packages"])
                        if name not in present)
    return dependencies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/qualification/deb")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--target", choices=tuple(TARGETS), default="ubuntu-24.04")
    args = parser.parse_args()
    if args.revision < 1:
        parser.error("Debian revision must be positive")
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if architecture != "amd64":
        parser.error("Native packages require the target's amd64 userspace")

    build = args.build_dir.resolve(strict=True)
    cache = (build / "CMakeCache.txt").read_text()
    target = validate_target(args.target, cache, Path("/etc/os-release").read_text())
    if cache_value(cache, "CMAKE_BUILD_TYPE") != "Release":
        parser.error("The Debian package requires a Release build")
    if cache_value(cache, "CMAKE_INSTALL_PREFIX") != "/usr":
        parser.error("Configure the package build with CMAKE_INSTALL_PREFIX=/usr")
    if cache_value(cache, "CMAKE_INSTALL_LIBDIR") != "lib":
        parser.error("Configure the package build with CMAKE_INSTALL_LIBDIR=lib")
    disabled = [name for name in REQUIRED_FEATURES if cache_value(cache, name) != "ON"]
    if disabled:
        parser.error("Package build is missing required features: " + ", ".join(disabled))
    if cache_value(cache, "LICASA_QT_FFMPEG_PLUGIN") == "":
        parser.error("The qualified private Qt FFmpeg plugin is required")
    if cache_value(cache, "LICASA_QT_EXTRA_IMAGEFORMATS_DIR") == "":
        parser.error("The matching Qt TIFF/WebP image plugins are required")
    version = cache_value(cache, "CMAKE_PROJECT_VERSION")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~]*", version):
        parser.error(f"Unsupported project version: {version}")
    debian_version = f"{version}-{args.revision}{target['revision_suffix']}"
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"licasa_{debian_version}_{architecture}.deb"

    with tempfile.TemporaryDirectory(prefix="licasa-deb-", dir=output) as temporary:
        work = Path(temporary)
        stage = work / "package"
        stage.mkdir()
        environment = dict(os.environ, DESTDIR=str(stage))
        subprocess.run(["cmake", "--install", str(build)], env=environment, check=True)
        executable = stage / "usr/bin/licasa"
        if not executable.is_file():
            raise RuntimeError("CMake did not install /usr/bin/licasa")
        control_dir = stage / "DEBIAN"
        control_dir.mkdir()
        dependencies = package_dependencies(stage, work, args.target)
        validate_abi_dependencies(dependencies, target["qt_version"], target["abi_package"])
        installed_kib = (
            sum(path.stat().st_size for path in (stage / "usr").rglob("*")
                if path.is_file() and not path.is_symlink())
            + 1023
        ) // 1024
        (control_dir / "control").write_text(
            f"Package: licasa\nVersion: {debian_version}\nArchitecture: {architecture}\n"
            "Maintainer: Sanxchep\nSection: graphics\nPriority: optional\n"
            f"Installed-Size: {installed_kib}\nDepends: {', '.join(dependencies)}\n"
            f"X-Licasa-Target: {args.target}\n"
            f"X-Licasa-Runtime-Profile: {target['profile']}\n"
            "Homepage: https://github.com/sanxchep/licasa\n"
            "Description: Licasa image viewer\n"
            " A Qt image viewer with private HEIF, JPEG XL, RAW and AVIF decoders.\n"
        )
        # Ubuntu's pkgbinarymangler wrapper expects a Debian source package.
        # This deb is assembled inside Snapcraft too, where those source-level
        # hooks print errors despite a successful dpkg-deb build.
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(stage), str(destination)],
            env=dict(os.environ, NO_PKG_MANGLE="1"), check=True)

    with destination.open("rb") as package_file:
        digest = hashlib.file_digest(package_file, "sha256").hexdigest()
    checksum = destination.with_name(destination.name + ".sha256")
    checksum.write_text(f"{digest}  {destination.name}\n")
    print(f"Debian package: {destination}")
    print(f"SHA-256: {digest}")
    print("Runtime dependencies: " + ", ".join(dependencies))


if __name__ == "__main__":
    main()
