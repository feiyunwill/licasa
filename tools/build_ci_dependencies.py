#!/usr/bin/env python3
"""Keep source-check dependency build settings independent of CI scheduling."""

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command_for(component: str, preset: str, jobs: int) -> list[str]:
    if preset not in {"release", "asan", "ubsan"} or jobs < 1:
        raise ValueError("Expected a supported preset and positive job count")
    if component == "quick":
        # All three source presets previously used this identical Release Qt
        # build. The application and codec sanitizer builds remain distinct.
        return [sys.executable, "tools/build_qt_backport.py", "--jobs", str(jobs)]
    if component != "codecs":
        raise ValueError("Expected quick or codecs")
    command = [sys.executable, "tools/build_codecs.py", "--prefix",
               str(ROOT / "build/codecs/prefix"), "--jobs", str(jobs)]
    if preset != "release":
        command += ["--sanitizer", {"asan": "address", "ubsan": "undefined"}[preset]]
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("quick", "codecs"))
    parser.add_argument("--preset", default="release", choices=("release", "asan", "ubsan"))
    parser.add_argument("--jobs", type=int, required=True)
    args = parser.parse_args()
    subprocess.run(command_for(args.component, args.preset, args.jobs), cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
