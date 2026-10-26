"""Exact private-Qt build identities for the supported native userspaces.

A profile selects source inputs, not relaxed compatibility constraints. Ubuntu's
paths and defaults intentionally remain stable for portable package consumers.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "ubuntu-6.4.2"
PROFILES = {
    DEFAULT_PROFILE: {
        "qt_version": "6.4.2",
        "ffmpeg_lock": "packaging/qt-ffmpeg.lock.json",
        "quick_lock": "packaging/qt-backport.lock.json",
        "target": "ubuntu-24.04",
        "ffmpeg_sonames": ("libavcodec.so.60", "libavformat.so.60", "libavutil.so.58",
                            "libswresample.so.4", "libswscale.so.7"),
    },
    "debian-6.8.2": {
        "qt_version": "6.8.2",
        "ffmpeg_lock": "packaging/qt-ffmpeg-debian.lock.json",
        "quick_lock": None,
        "target": "debian-13",
        "ffmpeg_sonames": ("libavcodec.so.61", "libavformat.so.61", "libavutil.so.59",
                            "libswresample.so.5", "libswscale.so.8"),
    },
}


def add_runtime_profile_argument(parser):
    parser.add_argument("--runtime-profile", choices=tuple(PROFILES), default=DEFAULT_PROFILE,
                        help="Exact distro Qt runtime; never mix outputs across profiles")


def profile_work_dir(work, profile):
    """Keep the historical Ubuntu layout and isolate every additional profile."""
    return work if profile == DEFAULT_PROFILE else work / profile


def configured_qt_version(cache):
    configured = re.search(r"^Qt6Core_DIR:PATH=(.+)$", cache, re.MULTILINE)
    if not configured:
        raise RuntimeError("Configured Qt Core directory missing from CMake cache")
    directory = Path(configured.group(1))
    version_file = directory / "Qt6CoreConfigVersionImpl.cmake"
    if not version_file.is_file():
        version_file = directory / "Qt6CoreConfigVersion.cmake"
    version = re.search(r'set\(PACKAGE_VERSION "([^"]+)"\)', version_file.read_text())
    if not version:
        raise RuntimeError(f"Qt version missing from {version_file}")
    return version.group(1)


def verify_ffmpeg_sonames(dynamic_section, profile):
    needed = set(re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic_section))
    expected = set(PROFILES[profile]["ffmpeg_sonames"])
    actual = {name for name in needed if name.startswith(
        ("libavcodec.", "libavformat.", "libavutil.", "libswresample.", "libswscale."))}
    if actual != expected:
        raise RuntimeError(f"{profile} FFmpeg ABI mismatch: expected {sorted(expected)}, got {sorted(actual)}")
    return sorted(actual)
