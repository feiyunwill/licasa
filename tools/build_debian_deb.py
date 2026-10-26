#!/usr/bin/env python3
"""Build the full-featured Debian 13 amd64 package in its native userspace.

Use packaging/build-debian.Dockerfile. Every private codec is rebuilt against
Debian libraries in an isolated tree; Ubuntu outputs are never reused.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "debian-6.8.2"


def run(*command):
    print("+ " + " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 4))
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build/debian-13")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/debian-13/application")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/qualification/debian13")
    parser.add_argument("--source-cache", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--skip-dependencies", action="store_true",
                        help="Reuse private dependencies built in this Debian userspace")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    system = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                  if "=" in line)
    if (system.get("ID", "").strip('"') != "debian"
            or system.get("VERSION_ID", "").strip('"') != "13"
            or platform.machine() != "x86_64"):
        parser.error("The qualified target is Debian 13 amd64; use packaging/build-debian.Dockerfile")
    required = ("cmake", "ninja", "meson", "nasm", "patch", "git", "pkg-config",
                "dpkg-architecture", "dpkg-shlibdeps", "dpkg-deb", "ctest")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        parser.error("Missing build tools: " + ", ".join(missing))
    python, jobs = sys.executable, str(args.jobs)
    work, build = args.work_dir.resolve(), args.build_dir.resolve()
    codecs = work / "codecs"
    qt = work / "qt"
    source_options = ["--source-cache", str(args.source_cache.resolve())] if args.source_cache else []
    if args.offline:
        source_options.append("--offline")
    identity = {"target": "debian-13", "runtime_profile": PROFILE, "architecture": "amd64"}
    receipt = work / "native-dependencies.json"
    if args.skip_dependencies:
        if not receipt.is_file() or json.loads(receipt.read_text()) != identity:
            parser.error("--skip-dependencies requires this target's native-dependencies.json")
    else:
        run(python, "tools/build_codecs.py", "--work-dir", codecs, "--jobs", jobs, *source_options)
        run(python, "tools/build_qt_ffmpeg.py", "--work-dir", qt,
            "--runtime-profile", PROFILE, "--jobs", jobs, *source_options)
        work.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(identity, indent=2) + "\n")
    codec_prefix = codecs / "release-prefix"
    ffmpeg = qt / PROFILE / "qt-ffmpeg-runtime-release/libffmpegmediaplugin.so"
    for path in (codec_prefix, ffmpeg):
        if not path.exists():
            parser.error(f"Missing private build output: {path}")
    multiarch = subprocess.check_output(["dpkg-architecture", "-qDEB_HOST_MULTIARCH"],
                                        text=True).strip()
    image_plugins = Path("/usr/lib") / multiarch / "qt6/plugins/imageformats"
    image_license = Path("/usr/share/doc/qt6-image-formats-plugins/copyright")
    if not (image_plugins / "libqsvg.so").is_file():
        parser.error("Debian requires qt6-svg-plugins for the SVG reader; rebuild the native builder image")
    run("cmake", "-S", ROOT, "-B", build, "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_INSTALL_PREFIX=/usr",
        "-DCMAKE_INSTALL_LIBDIR=lib", "-DBUILD_TESTING=ON",
        "-DLICASA_BUILD_DIAGNOSTICS=ON", "-DLICASA_ENABLE_HEIF=ON",
        "-DLICASA_ENABLE_JXL=ON", "-DLICASA_ENABLE_RAW=ON",
        "-DLICASA_ENABLE_AVIF=ON", "-DLICASA_ENABLE_APNG=ON",
        "-DLICASA_ENABLE_JP2=ON", "-DLICASA_ENABLE_MOTION_PLAYBACK=ON",
        f"-DLICASA_RUNTIME_PROFILE={PROFILE}", f"-DCMAKE_PREFIX_PATH={codec_prefix}",
        "-DLICASA_QT_QUICK_BACKPORT_LIBRARY=", f"-DLICASA_QT_FFMPEG_PLUGIN={ffmpeg}",
        f"-DLICASA_QT_EXTRA_IMAGEFORMATS_DIR={image_plugins}",
        f"-DLICASA_QT_EXTRA_IMAGEFORMATS_LICENSE={image_license}")
    run("cmake", "--build", build, "--parallel", jobs)
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (
        str(codec_prefix / "lib"), environment.get("LD_LIBRARY_PATH"))))
    print(f"+ ctest --test-dir {build} --output-on-failure", flush=True)
    subprocess.run(["ctest", "--test-dir", str(build), "--output-on-failure"],
                   cwd=ROOT, env=environment, check=True)
    run(python, "tools/verify_qt_runtime_memory.py", "--runtime-profile", PROFILE,
        "--plugin", ffmpeg, "--work-dir", work / "qt-memory", "--jobs", jobs)
    evidence = args.output_dir.resolve() / "qt-runtime-memory"
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "test.log"):
        shutil.copy2(work / "qt-memory" / name, evidence / name)
    run(python, "tools/package_deb.py", "--build-dir", build,
        "--output-dir", args.output_dir.resolve(), "--target", "debian-13")


if __name__ == "__main__":
    main()
