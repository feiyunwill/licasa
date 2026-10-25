"""Verified source archives shared by the dependency preparation tools."""

import hashlib
from pathlib import Path
from urllib.request import urlopen


def add_source_arguments(parser):
    parser.add_argument("--source-cache", type=Path,
                        help="Shared cache of verified archives and pinned Git sources")
    parser.add_argument("--offline", action="store_true",
                        help="Require cached inputs; never download or fetch")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Verify and prepare all source inputs without compiling")


def fetch_archive(directory, url, digest, *, name=None, algorithm="sha256",
                  offline=False, limit=256 * 1024 * 1024):
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / (name or url.rsplit("/", 1)[1])
    if not archive.is_file():
        if offline:
            raise RuntimeError(f"Offline source input missing: {archive}")
        partial = archive.with_name(archive.name + ".partial")
        try:
            with urlopen(url, timeout=60) as response, partial.open("wb") as output:
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > limit:
                        raise RuntimeError(f"Source archive exceeded size bound: {url}")
                    output.write(block)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    if archive.stat().st_size > limit:
        raise RuntimeError(f"Source archive exceeded size bound: {archive}")
    with archive.open("rb") as content:
        actual = hashlib.file_digest(content, algorithm).hexdigest()
    if actual != digest:
        raise RuntimeError(f"Checksum mismatch: {archive} (expected {digest}, got {actual})")
    return archive
