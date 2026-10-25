#!/usr/bin/env python3
"""Decode the common reader matrix from an installed final portable package."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import uuid

from verify_installed_release import SAMPLES

ROOT = Path(__file__).resolve().parents[1]
READER_SAMPLES = {
    **SAMPLES,
    "tiff": "formats/core-matrix/tiff/micro-1x1.tiff",
    "webp": "formats/core-matrix/webp/micro-1x1.webp",
    "jp2": "formats/additional-probes/jpeg-2000.jp2",
}
CODEC_MARKERS = {"avif": ("libavif.",), "heic": ("libheif.",),
                 "jxl": ("libjxl.", "libjxl_dec."), "dng": ("libraw.", "libraw_r.")}


def checked_mappings(paths: object, root: Path, context: str) -> list[str]:
    if (not isinstance(paths, list) or any(not isinstance(path, str) or not Path(path).is_absolute()
            or ".." in Path(path).parts or not Path(path).is_relative_to(root) for path in paths)):
        raise RuntimeError(f"{context} escaped its installed runtime: {paths}")
    return paths


def validate_readers(runtime: dict, runtime_root: str, qt_version: str) -> list[dict]:
    if runtime.get("qt_version") != qt_version:
        raise RuntimeError("Reader smoke loaded an unexpected Qt profile")
    root = Path(runtime_root)
    advertised = {str(value).lower() for value in runtime.get("read_formats", [])}
    missing = set(READER_SAMPLES) - advertised
    if missing:
        raise RuntimeError(f"Missing installed reader formats: {sorted(missing)}")
    before = runtime.get("before_decode", {})
    if before.get("mapped_optional_libraries") != []:
        raise RuntimeError("Optional codecs loaded before the first decode")
    qt = checked_mappings(before.get("mapped_qt_libraries"), root, "Qt reader libraries")
    if any(not any(f"libQt6{name}.so." in path for path in qt) for name in ("Core", "Qml", "Quick")):
        raise RuntimeError("Reader smoke lacks actual Core/Qml/Quick library mappings")
    samples = runtime.get("samples", [])
    if len(samples) != len(READER_SAMPLES):
        raise RuntimeError("Reader smoke did not decode the complete required sample matrix")
    checked = []
    for (expected, relative), sample in zip(READER_SAMPLES.items(), samples, strict=True):
        if (sample.get("path") != "/samples/" + relative or sample.get("decoded") is not True
                or sample.get("format") != expected or sample.get("decoded_width", 0) <= 0
                or sample.get("decoded_height", 0) <= 0):
            raise RuntimeError(f"Installed {expected} decode failed or described another sample: {sample}")
        process = sample.get("process", {})
        mapped = checked_mappings(process.get("mapped_optional_libraries"), root, f"{expected} codec libraries")
        qt = checked_mappings(process.get("mapped_qt_libraries"), root, f"{expected} Qt libraries")
        if any(not any(f"libQt6{name}.so." in path for path in qt) for name in ("Core", "Qml", "Quick")):
            raise RuntimeError(f"{expected} decode lacks actual Core/Qml/Quick library mappings")
        if expected in CODEC_MARKERS and not any(
                any(marker in Path(path).name for marker in CODEC_MARKERS[expected]) for path in mapped):
            raise RuntimeError(f"{expected} decode lacks an actual private codec library mapping")
        if expected == "dng" and sample.get("raw_development") is not False:
            raise RuntimeError("RAW smoke must exercise the embedded-preview path")
        checked.append({**sample, "expected_format": expected})
    return checked


def run_reader_checks(image: str, *, diagnostic: str, runtime_root: str,
                      output_dir: Path, setup: str = "", qt_version: str = "6.4.2") -> dict:
    for relative in READER_SAMPLES.values():
        if not (ROOT / "tests/test-assets" / relative).is_file():
            raise RuntimeError(f"Missing required reader fixture: {relative}")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="readers-", dir=output_dir.resolve()))
    artifacts.chmod(0o777)
    name = "licasa-readers-" + uuid.uuid4().hex[:16]
    invocation = [diagnostic, "--formats", "--megapixels", "25",
                  *("/samples/" + relative for relative in READER_SAMPLES.values())]
    # The selected package owns both library paths; no host Qt/plugin override
    # can provide a reader that its installed payload omitted.
    script = (setup + "\nexport LD_LIBRARY_PATH=" + shlex.quote(runtime_root + "/licasa/lib:" + runtime_root)
              + ";\nexec " + shlex.join(invocation))
    command = ["docker", "run", "--name", name, "--rm", "--network", "none", "--read-only",
               "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--user", "65534:65534", "--cap-drop", "ALL",
               "-e", "QT_QPA_PLATFORM=offscreen", "-e", "QT_QUICK_BACKEND=software",
               "-e", "HOME=/tmp/home", "-e", "XDG_RUNTIME_DIR=/tmp/runtime",
               "-e", "XDG_CONFIG_HOME=/tmp/config", "-e", "XDG_CACHE_HOME=/tmp/cache",
               "-e", "QML_IMPORT_PATH=/no-host-qml", "-e", "QML2_IMPORT_PATH=/no-host-qml2",
               "-v", f"{ROOT / 'tests/test-assets'}:/samples:ro", image, "sh", "-ec", script]
    (artifacts / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        (artifacts / "stdout.json").write_text(error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or "")
        (artifacts / "runtime.log").write_text(error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr or "")
        raise RuntimeError(f"Installed reader matrix timed out ({image}); see {artifacts}") from error
    finally:
        try:
            cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=20)
            (artifacts / "container-cleanup.log").write_text(cleanup.stdout + cleanup.stderr)
        except (OSError, subprocess.SubprocessError) as error:
            (artifacts / "container-cleanup.log").write_text(str(error) + "\n")
    (artifacts / "stdout.json").write_text(result.stdout)
    (artifacts / "runtime.log").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"Installed reader matrix failed ({image}): {result.returncode}; see {artifacts}")
    runtime = json.loads(result.stdout)
    report = {"status": "passed", "qt_version": qt_version, "expected_formats": sorted(READER_SAMPLES),
              "samples": validate_readers(runtime, runtime_root, qt_version), "runtime": runtime,
              "evidence_directory": str(artifacts), "exit_code": result.returncode}
    (artifacts / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
