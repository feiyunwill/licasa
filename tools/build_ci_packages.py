#!/usr/bin/env python3
"""Build PR package candidates with the canonical distro recipes and exact caches."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEBIAN_SNAPSHOT = r'''
set -o pipefail
mkdir -p /work/licasa
tar -C /source --exclude='./build*' --exclude='./cmake-build-*' --exclude='./.git' \
    --exclude='./.idea' --exclude='./private-validation' --exclude='./CMakeUserPresets.json' \
    -cf - . | tar -C /work/licasa -xf -
cd /work/licasa
exec python3 tools/build_debian_deb.py --output-dir /output "$@"
'''


def command_for(target: str, *, image: str, output: Path, dependencies: Path,
                jobs: int, reuse: bool, source: Path = ROOT) -> list[str]:
    if target not in {"ubuntu-24.04", "debian-13"} or jobs < 1:
        raise ValueError("Expected an implemented package target and positive job count")
    output.mkdir(parents=True, exist_ok=True)
    dependencies.mkdir(parents=True, exist_ok=True)
    command = ["docker", "run", "--rm", "-v", f"{source.resolve()}:/source:ro",
               "-v", f"{output.resolve()}:/output"]
    if target == "ubuntu-24.04":
        mappings = {"codecs": "/work/licasa/build/codecs/release-prefix",
                    "quick": "/work/licasa/build/deps/qt-backport-runtime",
                    "ffmpeg": "/work/licasa/build/deps/qt-ffmpeg-runtime-release"}
    else:
        mappings = {"codecs": "/work/licasa/build/debian-13/codecs/release-prefix",
                    "ffmpeg": "/work/licasa/build/debian-13/qt/debian-6.8.2/qt-ffmpeg-runtime-release"}
        receipt = dependencies / "native-dependencies.json"
        if reuse and not receipt.is_file():
            raise ValueError("A reused Debian cache requires its native dependency provenance")
        receipt.touch(exist_ok=True)
        command += ["-v", f"{receipt.resolve()}:/work/licasa/build/debian-13/native-dependencies.json"]
    for name, destination in mappings.items():
        path = dependencies / name
        path.mkdir(parents=True, exist_ok=True)
        command += ["-v", f"{path.resolve()}:{destination}"]
    if target == "ubuntu-24.04":
        command += ["-e", f"LICASA_MAX_JOBS={jobs}", "-e", f"LICASA_REUSE_DEPENDENCIES={str(reuse).lower()}",
                    image, "bash", "/source/packaging/verify-fresh-build.sh"]
    else:
        command += [image, "bash", "-ec", DEBIAN_SNAPSHOT, "_", "--jobs", str(jobs)]
        if reuse:
            command.append("--skip-dependencies")
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("ubuntu-24.04", "debian-13"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--reuse-dependencies", action="store_true")
    args = parser.parse_args()
    subprocess.run(command_for(args.target, image=args.image, output=args.output,
        dependencies=args.dependencies, jobs=args.jobs, reuse=args.reuse_dependencies), cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
