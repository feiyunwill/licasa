#!/usr/bin/env python3
"""Resolve an exact source identity for artifact-only hosted verification."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

from check_support_manifest import ROOT


def validate_requested_source(source_sha: str, expected_sha: str, actual_sha: str) -> None:
    for value in (source_sha, expected_sha, actual_sha):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("Candidate verification requires full source commit hashes")
    if source_sha != expected_sha or actual_sha != expected_sha:
        raise ValueError("Selected workflow source differs from the explicitly requested commit")


def source_version(cmake: str, snap: str) -> str:
    versions = re.findall(r"project\(\s*Licasa\s+VERSION\s+(\d+\.\d+\.\d+)\b", cmake)
    snap_versions = re.findall(r"^version:\s*['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", snap, re.M)
    if len(versions) != 1 or snap_versions != versions:
        raise ValueError("CMake and Snap must declare the same exact candidate version")
    return versions[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    validate_requested_source(args.source_sha, args.expected_sha, actual)
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"],
                               cwd=ROOT, text=True).strip():
        raise ValueError("Hosted candidate source must have no uncommitted or untracked files")
    version = source_version((ROOT / "CMakeLists.txt").read_text(), (ROOT / "snap/snapcraft.yaml").read_text())
    matrix = subprocess.check_output(["python3", "tools/check_support_manifest.py", "--matrix"],
                                    cwd=ROOT, text=True).strip()
    with args.github_output.open("a") as output:
        output.write(f"sha={actual}\nversion={version}\nmatrix={matrix}\n")


if __name__ == "__main__":
    main()
