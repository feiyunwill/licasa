#!/usr/bin/env python3
"""Check the public repository's documentation boundary."""

import argparse
from pathlib import PurePosixPath
import subprocess


PRIVATE_DIRECTORIES = {"docs", ".agents", ".codex", ".idea", "private-validation"}
DOCUMENT_EXTENSIONS = {".md", ".mdx", ".markdown", ".mdown", ".rst", ".adoc", ".org"}


def public_path(name):
    path = PurePosixPath(name)
    if PRIVATE_DIRECTORIES.intersection(path.parts):
        return False
    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        return True
    return name == "README.md" or (
        path.parts[:2] == ("packaging", "release-notes")
        and len(path.parts) == 3
        and path.suffix == ".md"
    )


def git(*arguments):
    return subprocess.check_output(["git", *arguments])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also inspect all ancestor trees")
    parser.add_argument("revisions", nargs="*", default=["HEAD"])
    args = parser.parse_args()
    revisions = args.revisions or ["HEAD"]
    if any(ref.startswith("-") for ref in revisions):
        parser.error("revisions cannot start with a dash")
    if args.history:
        revisions = git("rev-list", *revisions, "--").decode().splitlines()
    paths = set()
    for revision in revisions:
        paths.update(git("ls-tree", "-r", "--name-only", "-z", revision).split(b"\0"))
    violations = sorted(name.decode(errors="surrogateescape") for name in paths
                        if name and not public_path(name.decode(errors="surrogateescape")))
    if violations:
        print("Keep internal documents in staging; public documentation is limited to README and release notes.")
        for name in violations:
            print(name)
        raise SystemExit(1)
    print("Public file policy passed.")


if __name__ == "__main__":
    main()
