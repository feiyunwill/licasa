#!/usr/bin/env python3
"""Build a Fedora 44 x86_64 RPM from the verified FUSE-free AppImage payload."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "licasa-rpm-builder:fedora44"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "build/qualification/rpm")
    args = parser.parse_args()
    appimage = args.appimage.resolve(strict=True)
    match = re.fullmatch(r"Licasa-(\d+\.\d+\.\d+)-x86_64\.AppImage", appimage.name)
    if not match:
        parser.error("Expected Licasa-<version>-x86_64.AppImage")
    version = match.group(1)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "build", "--file", str(ROOT / "packaging/build-rpm.Dockerfile"),
                    "--tag", IMAGE, str(ROOT / "packaging")], check=True)
    script = (
        "mkdir -p /tmp/rpmbuild/SOURCES /tmp/rpmbuild/RPMS; "
        "cp /input/AppImage /tmp/rpmbuild/SOURCES/Licasa-$1-x86_64.AppImage; "
        "cp /source/tools/stage_portable_package.py /tmp/rpmbuild/SOURCES/; "
        "rpmbuild -bb --define '_topdir /tmp/rpmbuild' "
        "--define \"licasa_version $1\" /source/packaging/licasa-appimage.spec; "
        "cp /tmp/rpmbuild/RPMS/x86_64/*.rpm /output/"
    )
    subprocess.run(["docker", "run", "--rm", "--network", "none",
                    "-v", f"{ROOT}:/source:ro", "-v", f"{appimage}:/input/AppImage:ro",
                    "-v", f"{output}:/output", IMAGE, "sh", "-ec", script, "_", version],
                   check=True)
    packages = list(output.glob(f"licasa-appimage-{version}-1.*.x86_64.rpm"))
    if len(packages) != 1:
        raise RuntimeError(f"Expected one Fedora RPM, found {packages}")
    package = packages[0]
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_name(package.name + ".sha256").write_text(
        f"{digest}  {package.name}\n")
    print(f"Fedora RPM: {package}\nSHA-256: {digest}")


if __name__ == "__main__":
    main()
