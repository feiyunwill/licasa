#!/usr/bin/env python3
"""Qualify frame transforms and custom Qt AVIO ownership under ASan/UBSan.

Only the focused test executable is instrumented. LeakSanitizer also tracks
allocations inside the exact release plugin, so both successful and failed
custom-input teardown are checked in the bytes destined for the package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from runtime_profiles import PROFILES, add_runtime_profile_argument

ROOT = Path(__file__).resolve().parents[1]


def cmake_literal(value):
    if "]=]" in str(value):
        raise RuntimeError("Unsupported CMake path delimiter")
    return f"[=[{value}]=]"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_profile_argument(parser)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 4))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    plugin = args.plugin.resolve(strict=True)
    work = args.work_dir.resolve()
    source, build = work / "source", work / "build"
    source.mkdir(parents=True, exist_ok=True)
    profile = PROFILES[args.runtime_profile]
    # The same CMake receipt guard used by production prevents substituting an
    # unrelated or stale plugin while retaining passing sanitizer evidence.
    (source / "CMakeLists.txt").write_text(f'''cmake_minimum_required(VERSION 3.21)
project(LicasaQtRuntimeMemory LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
set(LICASA_RUNTIME_PROFILE {cmake_literal(args.runtime_profile)} CACHE STRING "")
include({cmake_literal(ROOT / "cmake/LicasaRuntimeProfiles.cmake")})
licasa_verify_ffmpeg_runtime({cmake_literal(plugin)})
find_package(Qt6 {profile["qt_version"]} EXACT REQUIRED COMPONENTS Gui Multimedia Test)
add_executable(qt_runtime_memory {cmake_literal(ROOT / "tests/tst_qt_custom_input.cpp")})
target_link_libraries(qt_runtime_memory PRIVATE Qt6::Gui Qt6::Multimedia Qt6::Test)
target_compile_definitions(qt_runtime_memory PRIVATE
    {cmake_literal('LICASA_TEST_ASSET_DIR="' + str(ROOT / 'tests/test-assets') + '"')})
target_compile_options(qt_runtime_memory PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
target_link_options(qt_runtime_memory PRIVATE -fsanitize=address,undefined)
''')
    for command in (["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja"],
                    ["cmake", "--build", str(build), "--parallel", str(args.jobs)]):
        subprocess.run(command, check=True)
    staged = build / "plugins/multimedia/libffmpegmediaplugin.so"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plugin, staged)
    environment = dict(os.environ)
    environment.update(QT_MEDIA_BACKEND="ffmpeg", QT_FFMPEG_DECODING_HW_DEVICE_TYPES=",",
                       ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
                       UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    for name, directory in (("XDG_CACHE_HOME", work / "cache"),
                            ("XDG_CONFIG_HOME", work / "config"),
                            ("XDG_RUNTIME_DIR", work / "runtime")):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment[name] = str(directory)
    result = subprocess.run([str(build / "qt_runtime_memory"), "-platform", "offscreen"],
                            env=environment, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=150)
    print(result.stdout, end="", flush=True)
    (work / "test.log").write_text(result.stdout)
    (work / "report.json").write_text(json.dumps({
        "status": "passed" if result.returncode == 0 else "failed",
        "runtime_profile": args.runtime_profile, "qt_version": profile["qt_version"],
        "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256((ROOT / "tests/tst_qt_custom_input.cpp").read_bytes()).hexdigest(),
        "sanitizers": ["address", "undefined", "leak"], "exit_code": result.returncode,
        "instrumented_component": "focused test executable",
        "release_plugin_instrumented": False,
        "release_plugin_allocation_tracking": "LeakSanitizer observes the actual packaged plugin's allocations",
        "inputs_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ("tools/verify_qt_runtime_memory.py",
                                       "tests/tst_qt_custom_input.cpp",
                                       "src/media/motion/video_frame_image.h",
                                       "cmake/LicasaRuntimeProfiles.cmake",
                                       profile["ffmpeg_lock"])},
    }, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError(f"Qt runtime memory qualification failed; see {work / 'test.log'}")
    print(f"Qt runtime memory qualification passed: {work / 'report.json'}")


if __name__ == "__main__":
    main()
