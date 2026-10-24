#!/usr/bin/env python3
"""Inspect packaged ELF dependencies without installing or launching Licasa.

The AppImage is unpacked with unsquashfs, never executed. This checks the
current host's library resolution, not a clean desktop installation.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import struct
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def squashfs_offset(data):
    candidates = []
    start = 0
    while (offset := data.find(b"hsqs", start)) != -1:
        start = offset + 1
        if offset + 96 > len(data):
            continue
        block_size = struct.unpack_from("<I", data, offset + 12)[0]
        version = struct.unpack_from("<HH", data, offset + 28)
        used = struct.unpack_from("<Q", data, offset + 40)[0]
        if (version == (4, 0) and 4096 <= block_size <= 1048576
                and block_size & (block_size - 1) == 0 and 96 <= used <= len(data) - offset):
            candidates.append(offset)
    if len(candidates) != 1:
        raise ValueError("Expected exactly one valid embedded SquashFS v4 filesystem")
    return candidates[0]


def inspect(package, expected, output):
    data = package.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise ValueError(f"Checksum mismatch for {package.name}")
    report = {
        "package": package.name, "sha256": digest, "host_architecture": platform.machine(),
        "host_libc": platform.libc_ver(), "clean_environment": False,
        "application_launched": False, "installed": False,
    }
    with tempfile.TemporaryDirectory(prefix="licasa-package-inspection-") as temporary:
        root = Path(temporary) / "payload"
        if package.suffix == ".deb":
            command = ["dpkg-deb", "--extract", str(package), str(root)]
        elif package.suffix == ".AppImage":
            command = ["unsquashfs", "-no-progress", "-processors", "2", "-offset",
                       str(squashfs_offset(data)), "-dest", str(root), str(package)]
        else:
            raise ValueError("Supply a .deb or AppImage release package")
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        result = subprocess.run(
            ["bash", "-c", '. "$1"; licasa_check_runtime_dependencies "$2" "$3"',
             "_", str(ROOT / "packaging/install-licasa-appimage.sh"),
             f"{root}/usr/lib/licasa/lib:{root}/usr/lib", str(root / "usr")],
            capture_output=True, text=True, timeout=120,
        )
        # Keep temporary extraction paths out of the durable evidence.
        details = (result.stdout + result.stderr).replace(str(root), "<package>")
        report["status"] = "passed_host_linkage" if result.returncode == 0 else "failed_host_linkage"
        match = re.search(r"passed \(([0-9]+) ELF files\)", result.stdout)
        if match:
            report["elf_files_checked"] = int(match.group(1))
        report["details"] = details
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(details, end="")
    return 0 if report["status"] == "passed_host_linkage" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        parser.error("Supply the published SHA-256 digest")
    raise SystemExit(inspect(args.package.resolve(strict=True), args.sha256, args.output))


if __name__ == "__main__":
    main()
