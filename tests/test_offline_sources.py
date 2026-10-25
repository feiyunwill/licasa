#!/usr/bin/env python3
"""Offline preparation must verify all inputs without a hidden network fallback."""

import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from source_cache import fetch_archive
from build_codecs import prepare_archive_source, prepare_git_source, git_output, selected_names


class OfflineSourceTests(unittest.TestCase):
    def test_missing_and_corrupt_archives_never_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with patch("source_cache.urlopen", side_effect=AssertionError("network attempted")):
                with self.assertRaisesRegex(RuntimeError, "Offline source input missing"):
                    fetch_archive(cache, "https://example.invalid/source.tar.gz", "0" * 64,
                                  offline=True)
                (cache / "source.tar.gz").write_bytes(b"corrupted cache")
                with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                    fetch_archive(cache, "https://example.invalid/source.tar.gz", "0" * 64,
                                  offline=True)

    def test_verified_archive_prepares_a_fresh_tree_and_rechecks_cached_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "fixture-1.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo("fixture/README")
                info.size = 5
                bundle.addfile(info, io.BytesIO(b"input"))
            spec = {"version": "1", "url": "https://example.invalid/fixture.tar.gz",
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            source = base / "fresh-source"
            with patch("source_cache.urlopen", side_effect=AssertionError("network attempted")):
                prepare_archive_source("fixture", spec, base, source, offline=True)
                self.assertEqual((source / "README").read_text(), "input")
                archive.write_bytes(b"changed after first extraction")
                with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                    prepare_archive_source("fixture", spec, base, source, offline=True)

    def test_pinned_git_cache_supports_fresh_offline_work_and_rejects_dirty_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            origin = base / "origin"
            origin.mkdir()
            subprocess.run(["git", "init", "--quiet", str(origin)], check=True)
            (origin / "input.txt").write_text("pinned input")
            subprocess.run(["git", "-C", str(origin), "add", "input.txt"], check=True)
            subprocess.run(["git", "-C", str(origin), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.invalid", "commit", "--quiet",
                            "-m", "Local offline source fixture"], check=True)
            spec = {"url": str(origin), "commit": git_output(origin, "rev-parse", "HEAD"),
                    "tree": git_output(origin, "rev-parse", "HEAD^{tree}")}
            cached = base / "cache/git" / f"fixture-{spec['commit']}"
            cached.parent.mkdir(parents=True)
            # Seed an independently prepared source, as a prefetcher would.
            subprocess.run(["git", "clone", "--quiet", str(origin), str(cached)], check=True)
            fresh = base / "fresh"
            prepare_git_source("fixture", spec, fresh, offline=True, source_cache=base / "cache")
            self.assertEqual((fresh / "input.txt").read_text(), "pinned input")
            (cached / "input.txt").write_text("dirty cache")
            with self.assertRaisesRegex(RuntimeError, "dirty pinned source"):
                prepare_git_source("fixture", spec, base / "another-fresh", offline=True,
                                   source_cache=base / "cache")

    def test_selection_includes_transitive_locked_requirements(self):
        lock = {"reader": {"requires": ["decoder"]}, "decoder": {"requires": ["pixels"]},
                "pixels": {}, "unrelated": {}}
        self.assertEqual(selected_names(lock, ["reader"]), {"reader", "decoder", "pixels"})
        with self.assertRaises(ValueError):
            selected_names(lock, ["not-locked"])


if __name__ == "__main__":
    unittest.main()
