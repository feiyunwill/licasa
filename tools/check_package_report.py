#!/usr/bin/env python3
"""Check a PR package report without pretending it is release qualification."""

import argparse
import json
from pathlib import Path

from check_support_manifest import load_manifest
from qualification_evidence import digest, validate_report


def check_report(report: dict, target: dict, manifest: dict, package: Path) -> None:
    reported_hash = report.get("package_sha256", report.get("appimage_sha256", report.get("rpm_sha256", report.get("artifact_sha256"))))
    if reported_hash != digest(package):
        raise ValueError("PR qualification report describes a different candidate package")
    validate_report(report, target, manifest)


def write_checked_report(report: dict, *, target_id: str, package: Path, output: Path) -> None:
    """Retain raw evidence, then require CI's full contract before success."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    manifest = load_manifest()
    target = next(row for row in manifest["targets"] if row["id"] == target_id)
    check_report(report, target, manifest, package)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_manifest()
    target = next(row for row in manifest["targets"] if row["id"] == args.target)
    check_report(json.loads(args.report.read_text()), target, manifest, args.package)
    print(f"PR candidate {args.target}: report and artifact agree")


if __name__ == "__main__":
    main()
