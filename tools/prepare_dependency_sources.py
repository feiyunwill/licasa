#!/usr/bin/env python3
"""Prepare verified dependency inputs before entering a network-free build."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--libraw-rawspeed3", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    cache, work = args.source_cache.resolve(), args.work_dir.resolve()
    common = ["--source-cache", str(cache), "--prepare-only"]
    if args.offline:
        common.append("--offline")
    codecs = ["--libraw-rawspeed3"] if args.libraw_rawspeed3 else []
    commands = [
        ("build_codecs.py", ["--work-dir", str(work / "codecs"), *codecs]),
        ("build_qt_ffmpeg.py", ["--work-dir", str(work / "qt")]),
        ("build_qt_backport.py", ["--work-dir", str(work / "qt")]),
    ]
    locks = ["packaging/codecs.lock.json", "packaging/qt-ffmpeg.lock.json",
             "packaging/qt-backport.lock.json"]
    for script, options in commands:
        subprocess.run([sys.executable, str(ROOT / "tools" / script), *common, *options], check=True)
    inventory = {
        "runtime_profile": "ubuntu-6.4.2",
        "rawspeed3": args.libraw_rawspeed3,
        "locks": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in locks},
        "patches": {},
        "archives": {str(p.relative_to(cache)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((cache / "archives").iterdir()) if p.is_file()},
        "git": {},
    }
    def record_patches(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("patch", "patches"):
                    for name in ([child] if isinstance(child, str) else child):
                        inventory["patches"][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                else:
                    record_patches(child)
    for name in locks:
        record_patches(json.loads((ROOT / name).read_text()))
    for repo in sorted((cache / "git").glob("*")):
        inventory["git"][str(repo.relative_to(cache))] = {
            key: subprocess.check_output(["git", "-C", str(repo), "rev-parse", ref],
                                         text=True).strip()
            for key, ref in (("commit", "HEAD"), ("tree", "HEAD^{tree}"))
        }
    receipt = work / "dependency-inputs.json"
    receipt.write_text(json.dumps(inventory, indent=2) + "\n")
    print(f"Verified dependency inventory: {receipt}")


if __name__ == "__main__":
    main()
