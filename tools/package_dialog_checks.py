#!/usr/bin/env python3
"""Exercise the final package's production dialogs in disposable containers."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import uuid

from verify_dialog_interactions import REQUIRED_SCENARIOS
from package_dialog_recovery import run_dialog_recovery


ROOT = Path(__file__).resolve().parents[1]


def run_dialog_checks(image: str, *, app: str, diagnostic: str, sample: Path,
                      setup: str = "", qt_version: str = "6.4.2",
                      qml_root: str = "/tmp/squashfs-root/usr/qml",
                      library_root: str = "/tmp/squashfs-root/usr/lib",
                      output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for protocol in ("x11", "wayland"):
        for mode in ("default", "software"):
            artifacts = Path(tempfile.mkdtemp(prefix=f"{protocol}-{mode}-", dir=output_dir.resolve()))
            # Only this new artifact directory is writable by the isolated test user.
            artifacts.chmod(0o777)
            arguments = ["python3", "/qualification/tools/verify_dialog_interactions.py",
                         "--app", app, "--diagnostic", diagnostic, "--sample", "/samples/photo.jpg",
                         "--output", "/reports/report.json", "--protocol", protocol,
                         "--mode", mode, "--dialog-backend", "fallback", "--timeout", "90"]
            runtime = ["python3", "/qualification/tools/qt_runtime.py", "--qml-root", qml_root,
                       "--library-root", library_root, "--qt-version", qt_version,
                       "--output", "/reports/module-inventory.json"]
            session = ["sh", "/qualification/tools/run_dialog_session.sh", protocol, *arguments]
            script = (setup + "\n" + shlex.join(runtime) + " >/reports/module-check.log 2>&1 || "
                      "{ cat /reports/module-check.log >&2; exit 1; };\n"
                      "if " + shlex.join(session) + " >/reports/harness.log 2>&1; then "
                      "cat /reports/report.json; else cat /reports/harness.log >&2; "
                      "test ! -f /reports/report.json || cat /reports/report.json >&2; exit 1; fi")
            container_name = "licasa-dialog-" + uuid.uuid4().hex[:16]
            command = ["docker", "run", "--name", container_name, "--rm", "--network", "none", "--read-only",
                       "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=2g", "--shm-size", "256m",
                       "--user", "65534:65534", "--cap-drop", "ALL",
                       "-e", "HOME=/tmp/home", "-e", "XDG_CONFIG_HOME=/tmp/config",
                       "-e", "XDG_CACHE_HOME=/tmp/cache", "-e", "XDG_STATE_HOME=/tmp/state",
                       "-e", "XDG_RUNTIME_DIR=/tmp/runtime", "-e", "XDG_CURRENT_DESKTOP=MATE",
                       "-e", "LICASA_DIALOG_LOG_DIRECTORY=/reports",
                       "-v", f"{ROOT / 'tools'}:/qualification/tools:ro",
                       "-v", f"{ROOT / 'packaging/qt-runtime-modules.json'}:/qualification/packaging/qt-runtime-modules.json:ro",
                       "-v", f"{sample.resolve(strict=True)}:/samples/photo.jpg:ro",
                       "-v", f"{artifacts}:/reports:rw",
                       image, "sh", "-ec", script]
            try:
                result = subprocess.run(command, text=True, capture_output=True, timeout=150)
            finally:
                # Killing the Docker CLI on timeout does not stop its container.
                # Remove only this invocation's named container and retain logs.
                try:
                    cleanup = subprocess.run(["docker", "rm", "-f", container_name],
                                             text=True, capture_output=True, timeout=20)
                    (artifacts / "container-cleanup.log").write_text(cleanup.stdout + cleanup.stderr)
                except (OSError, subprocess.SubprocessError) as error:
                    (artifacts / "container-cleanup.log").write_text(str(error) + "\n")
            if result.returncode:
                raise RuntimeError(f"Package dialogs failed ({image}, {protocol}, {mode}):\n"
                                   f"{result.stdout}\n{result.stderr}")
            report = json.loads(result.stdout)
            primary = artifacts / "report.json"
            if not primary.is_file() or primary.is_symlink():
                raise RuntimeError(f"Package dialogs did not retain primary evidence: {primary}")
            if json.loads(primary.read_text()) != report:
                raise RuntimeError(f"Package dialog stdout does not match retained evidence: {primary}")
            # Diagnostic paths must remain usable after the container is gone.
            def retained(value):
                if isinstance(value, str):
                    return value.replace("/reports/", str(artifacts) + "/")
                if isinstance(value, list):
                    return [retained(item) for item in value]
                if isinstance(value, dict):
                    return {key: retained(item) for key, item in value.items()}
                return value
            report = retained(report)
            report["artifacts_directory"] = str(artifacts)
            if (report.get("status") != "passed" or report.get("protocol") != protocol
                    or report.get("mode") != mode or report.get("dialog_backend") != "fallback"):
                raise RuntimeError(f"Package dialog report did not prove the requested session: {report}")
            scenarios = report.get("scenarios", [])
            names = [case.get("name") for case in scenarios]
            if (len(set(names)) != len(names)
                    or not (REQUIRED_SCENARIOS | {"application_launch"}).issubset(names)
                    or any(case.get("status") != "passed" for case in scenarios)):
                raise RuntimeError(f"Incomplete package dialog coverage: {report}")
            runtime = report.get("runtime", {})
            if runtime.get("qt_version") != qt_version:
                raise RuntimeError(f"Package dialogs loaded an unexpected Qt version: {runtime.get('qt_version')}")
            mapped = runtime.get("process", {}).get("mapped_qt_libraries", [])
            allowed = [Path(library_root)]
            if qml_root == "/usr/lib/x86_64-linux-gnu/qt6/qml":
                allowed.append(Path("/usr/lib/licasa/lib"))
            if (not mapped or any(not isinstance(path, str) or not Path(path).is_absolute()
                                  or ".." in Path(path).parts
                                  or not any(Path(path).is_relative_to(root) for root in allowed)
                                  for path in mapped)
                    or any(not any(f"libQt6{name}.so." in path for path in mapped)
                           for name in ("Core", "Qml", "Quick"))):
                raise RuntimeError(f"Package dialogs escaped their Qt runtime: {mapped}")
            reports.append(report)
    reports[1]["missing_module_recovery"] = run_dialog_recovery(
        image, app=app, diagnostic=diagnostic, sample=sample, setup=setup,
        qt_version=qt_version, qml_root=qml_root, library_root=library_root, output_dir=output_dir)
    return reports
