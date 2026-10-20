#!/usr/bin/env python3
"""Build an Arch package and an AUR-ready recipe from the checked AppImage."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "licasa-arch-builder:local"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "build/qualification/arch")
    args = parser.parse_args()
    appimage = args.appimage.resolve(strict=True)
    match = re.fullmatch(r"Licasa-(\d+\.\d+\.\d+)-x86_64\.AppImage", appimage.name)
    if not match:
        parser.error("Expected Licasa-<version>-x86_64.AppImage")
    version = match.group(1)
    digest = hashlib.sha256(appimage.read_bytes()).hexdigest()
    template = (ROOT / "packaging/arch/PKGBUILD.in").read_text()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    recipe = output / "PKGBUILD"
    recipe.write_text(template.replace("@VERSION@", version)
                      .replace("@SHA256@", digest))
    recipe_digest = hashlib.sha256(recipe.read_bytes()).hexdigest()
    (output / "PKGBUILD.sha256").write_text(f"{recipe_digest}  PKGBUILD\n")
    subprocess.run(["docker", "build", "--file", str(ROOT / "packaging/build-arch.Dockerfile"),
                    "--tag", IMAGE, str(ROOT / "packaging")], check=True)
    script = (
        "mkdir -p /tmp/archbuild; "
        "cp /input/AppImage /tmp/archbuild/Licasa-$1-x86_64.AppImage; "
        "cp /input/PKGBUILD /tmp/archbuild/PKGBUILD; "
        "chown -R builder:builder /tmp/archbuild; "
        "su builder -c 'cd /tmp/archbuild && makepkg --noconfirm --nodeps'; "
        "cp /tmp/archbuild/*.pkg.tar.zst /output/; "
        "su builder -c 'cd /tmp/archbuild && makepkg --printsrcinfo' > /output/.SRCINFO"
    )
    subprocess.run(["docker", "run", "--rm", "--network", "none",
                    "-v", f"{appimage}:/input/AppImage:ro",
                    "-v", f"{recipe}:/input/PKGBUILD:ro",
                    "-v", f"{output}:/output", IMAGE, "sh", "-ec", script, "_", version],
                   check=True)
    package = output / f"licasa-appimage-{version}-1-x86_64.pkg.tar.zst"
    if not package.is_file():
        raise RuntimeError(f"Arch package was not built: {package}")
    package_digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_name(package.name + ".sha256").write_text(
        f"{package_digest}  {package.name}\n")
    print(f"Arch package: {package}\nSHA-256: {package_digest}")


if __name__ == "__main__":
    main()
