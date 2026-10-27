#!/usr/bin/env python3
"""Exercise lifecycle control in private offscreen processes, never a host install.

This is the source/process gate. It deliberately makes no package-manager,
Store-refresh, desktop-login, power-loss or final-package qualification claim.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import select
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any


PROTOCOL = 1
MAXIMUM_MESSAGE_BYTES = 16 * 1024
RESULT_CODES = {
    "ready": 0, "accepted": 0, "absent": 3, "busy": 4,
    "wrong-installation": 5, "wrong-generation": 5, "unsupported": 6,
    "access-denied": 7, "timeout": 8, "broken": 9,
}
DEFAULT_SEEDS = (1729, 2718, 31415)


class QualificationFailure(RuntimeError):
    """An observed process or protocol result violated a required assertion."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_reply(data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAXIMUM_MESSAGE_BYTES:
        raise QualificationFailure("Empty or oversized lifecycle reply")
    try:
        result = json.loads(data)
    except (UnicodeDecodeError, ValueError) as error:
        raise QualificationFailure("Lifecycle reply is not JSON") from error
    if not isinstance(result, dict) or result.get("protocol") != PROTOCOL:
        raise QualificationFailure("Unsupported lifecycle reply protocol")
    if result.get("result") not in RESULT_CODES:
        raise QualificationFailure("Unknown lifecycle result")
    return result


def validate_reply(reply: dict[str, Any], identity: dict[str, Any],
                   returncode: int | None = None, running: bool = False) -> None:
    if reply.get("installationId") != identity.get("installationId"):
        raise QualificationFailure("Reply belongs to another installation")
    if returncode is not None and RESULT_CODES[reply["result"]] != returncode:
        raise QualificationFailure("Lifecycle result and exit code disagree")
    if running:
        if not isinstance(reply.get("pid"), int) or reply["pid"] <= 0:
            raise QualificationFailure("Running reply has no process identity")
        if not isinstance(reply.get("startIdentity"), str) or not reply["startIdentity"]:
            raise QualificationFailure("Running reply has no process start identity")
        if reply.get("generation") != identity.get("generation"):
            raise QualificationFailure("Running reply has the wrong generation")


def process_start_identity(pid: int) -> str | None:
    """Inspect Linux start identity without treating PID reuse as continued life."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        tail = stat[stat.rfind(")") + 2:].split()
        if tail[0] == "Z":
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"{boot}:{tail[19]}"
    except (OSError, IndexError):
        return None


def wait_for_exit(process: subprocess.Popen, timeout: float) -> int:
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise QualificationFailure("Stop acknowledged but owned process is still alive") from error


class EndpointWatch:
    """Block on private-directory events rather than sleep/poll startup loops."""

    def __init__(self, endpoint: Path):
        self.endpoint = endpoint
        library = ctypes.CDLL(None, use_errno=True)
        self.fd = library.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        # Creation/move of the socket is observable even on a very fast launch.
        if library.inotify_add_watch(self.fd, os.fsencode(endpoint.parent), 0x100 | 0x80) < 0:
            os.close(self.fd)
            raise OSError(ctypes.get_errno(), "inotify_add_watch")

    def wait(self, process: subprocess.Popen, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not self.endpoint.exists():
            if process.poll() is not None:
                raise QualificationFailure("Viewer exited before creating its endpoint")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise QualificationFailure("Viewer did not create its endpoint before deadline")
            os.read(self.fd, 64 * 1024)

    def close(self) -> None:
        os.close(self.fd)


def socket_request(endpoint: Path, request: dict[str, Any] | bytes,
                   timeout: float) -> dict[str, Any] | None:
    payload = (json.dumps(request, separators=(",", ":")).encode() + b"\n"
               if isinstance(request, dict) else request)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(endpoint))
        client.sendall(payload)
        output = bytearray()
        while b"\n" not in output:
            chunk = client.recv(min(4096, MAXIMUM_MESSAGE_BYTES + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAXIMUM_MESSAGE_BYTES:
                raise QualificationFailure("Socket response exceeds the protocol bound")
        return decode_reply(bytes(output)) if output else None


class ProcessCampaign:
    def __init__(self, binary: Path, output: Path, timeout: float):
        self.binary = binary.resolve(strict=True)
        self.output = output
        self.timeout = timeout
        self.trace: list[dict[str, Any]] = []
        self.processes: list[subprocess.Popen] = []
        self.handles = []
        self.identity: dict[str, Any] = {}
        self.environment: dict[str, str] = {}
        self.started = time.monotonic()
        self.private_directory: tempfile.TemporaryDirectory | None = None

    def event(self, action: str, **values: Any) -> None:
        item = {"monotonic_ms": round((time.monotonic() - self.started) * 1000, 3),
                "action": action, **values}
        self.trace.append(item)
        with (self.output / "trace.jsonl").open("a") as stream:
            stream.write(json.dumps(item, sort_keys=True) + "\n")

    def configure(self, root: Path) -> None:
        self.environment = dict(os.environ)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                     "SNAP", "SNAP_NAME", "SNAP_INSTANCE_NAME", "SNAP_USER_DATA",
                     "SNAP_USER_COMMON", "APPIMAGE", "APPDIR"):
            self.environment.pop(name, None)
        for key, directory in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                               ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                               ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "run")):
            path = root / directory
            path.mkdir(mode=0o700)
            self.environment[key] = str(path)
        self.environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                                QSG_RHI_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic")

    def control(self, operation: str, expected: tuple[int, ...] = (0,),
                headless: bool = False) -> dict[str, Any]:
        environment = dict(self.environment)
        if headless:
            environment["QT_QPA_PLATFORM"] = "licasa-intentionally-missing-platform"
        started = time.monotonic()
        try:
            completed = subprocess.run([str(self.binary), "--lifecycle", operation],
                                       env=environment, capture_output=True,
                                       timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            self.event(operation, failure="command deadline exceeded")
            raise QualificationFailure(f"{operation} exceeded its deadline") from error
        self.event(operation, returncode=completed.returncode,
                   elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                   stdout=completed.stdout.decode(errors="replace"),
                   stderr=completed.stderr.decode(errors="replace"))
        if completed.returncode not in expected:
            raise QualificationFailure(f"{operation}: unexpected exit {completed.returncode}")
        reply = decode_reply(completed.stdout)
        if self.identity:
            validate_reply(reply, self.identity, completed.returncode)
        return reply

    def start(self, background: bool) -> subprocess.Popen:
        name = f"viewer-{len(self.processes):03d}.log"
        handle = (self.output / name).open("wb")
        self.handles.append(handle)
        watch = EndpointWatch(Path(self.identity["endpoint"]))
        try:
            process = subprocess.Popen([str(self.binary)] + (["--background"] if background else []),
                                       env=self.environment, stdout=handle, stderr=handle)
            self.processes.append(process)
            self.event("launch", pid=process.pid, background=background, log=name)
            watch.wait(process, self.timeout)
        finally:
            watch.close()
        reply = self.control("status")
        validate_reply(reply, self.identity, running=True)
        if reply["pid"] != process.pid:
            raise QualificationFailure("Launch was served by an unexpected process")
        actual_start = process_start_identity(process.pid)
        if reply["startIdentity"] != actual_start:
            raise QualificationFailure("Reported process start identity is incorrect")
        return process

    def stop(self, process: subprocess.Popen) -> None:
        self.control("prepare-update")
        self.control("stop-for-update")
        code = wait_for_exit(process, self.timeout)
        self.event("observed-process-exit", pid=process.pid, returncode=code)
        if code != 0:
            raise QualificationFailure("Cooperative stop did not exit normally")
        if process_start_identity(process.pid) is not None:
            raise QualificationFailure("Owned process survives cooperative stop")
        self.control("status", expected=(3,))
        self.control("cancel-update", expected=(0, 3))

    def duplicate_background(self, process: subprocess.Popen) -> None:
        completed = subprocess.run([str(self.binary), "--background"], env=self.environment,
                                   capture_output=True, timeout=self.timeout)
        self.event("duplicate-background", returncode=completed.returncode,
                   stdout=completed.stdout.decode(errors="replace"),
                   stderr=completed.stderr.decode(errors="replace"))
        if completed.returncode != 0:
            raise QualificationFailure("Duplicate background launch failed")
        reply = self.control("status")
        if reply["pid"] != process.pid:
            raise QualificationFailure("Duplicate startup replaced the resident process")

    def prepared_launch_is_blocked(self, process: subprocess.Popen) -> None:
        self.control("prepare-update")
        completed = subprocess.run([str(self.binary)], env=self.environment,
                                   capture_output=True, timeout=self.timeout)
        self.event("launch-during-prepare", returncode=completed.returncode,
                   stdout=completed.stdout.decode(errors="replace"),
                   stderr=completed.stderr.decode(errors="replace"))
        reply = self.control("status")
        if (completed.returncode == 0 or process.poll() is not None or
                reply["pid"] != process.pid or reply.get("visibleWindows", 0) != 0):
            raise QualificationFailure("Prepared gate admitted a launch or changed process ownership")
        self.control("cancel-update")

    def visible_busy_and_explicit_quit(self) -> None:
        process = self.start(background=False)
        deadline = time.monotonic() + self.timeout
        for _ in range(100):
            # Each bounded IPC operation records real state; no settling sleep
            # converts mere socket readiness into a visible-window assertion.
            reply = self.control("status")
            if reply.get("visibleWindows", 0) >= 1:
                break
            if process.poll() is not None or time.monotonic() >= deadline:
                raise QualificationFailure("Visible launch did not reach a visible window")
        else:
            raise QualificationFailure("Visible launch exceeded its bounded readiness probes")
        self.control("prepare-update", expected=(4,))
        self.control("stop-for-update", expected=(4,))
        if process.poll() is not None:
            raise QualificationFailure("Busy visible viewer was stopped by update control")
        completed = subprocess.run([str(self.binary), "--quit"], env=self.environment,
                                   capture_output=True, timeout=self.timeout)
        self.event("explicit-quit", returncode=completed.returncode,
                   stdout=completed.stdout.decode(errors="replace"),
                   stderr=completed.stderr.decode(errors="replace"))
        if completed.returncode != 0 or wait_for_exit(process, self.timeout) != 0:
            raise QualificationFailure("Explicit Quit did not exit the visible process normally")
        self.control("status", expected=(3,))

    def negative_requests(self) -> None:
        endpoint = Path(self.identity["endpoint"])
        base = {"protocol": PROTOCOL, "installationId": self.identity["installationId"]}
        for fields, result in (({"installationId": "f" * 32}, "wrong-installation"),
                               ({"protocol": 999}, "unsupported"),
                               ({"operation": "unknown-operation"}, "unsupported")):
            request = {**base, "operation": "status", **fields}
            reply = socket_request(endpoint, request, self.timeout)
            self.event("negative-request", request=request, reply=reply)
            if reply is None or reply.get("result") != result:
                raise QualificationFailure(f"Request was not rejected as {result}")
        reply = socket_request(endpoint, b"x" * (MAXIMUM_MESSAGE_BYTES + 1) + b"\n",
                               self.timeout)
        self.event("oversized-request", reply=reply)
        if reply is not None and reply.get("result") in ("ready", "accepted"):
            raise QualificationFailure("Oversized request was accepted")
        self.control("health")

    def enable_background(self) -> None:
        """Exercise adoption of an exact owned legacy opt-in in private config."""
        entry = (Path(self.environment["SNAP_USER_DATA"]) / ".config/autostart/licasa-background.desktop"
                 if "SNAP_NAME" in self.environment else
                 Path(self.environment["XDG_CONFIG_HOME"]) / "autostart/licasa-background.desktop")
        entry.parent.mkdir(parents=True, exist_ok=True)
        quoted = str(self.binary)
        for before, after in (("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"),
                              ("$", "\\$"), ("%", "%%")):
            quoted = quoted.replace(before, after)
        command = "licasa" if "SNAP_NAME" in self.environment else f'"{quoted}"'
        entry.write_text("[Desktop Entry]\nType=Application\nName=Licasa\n"
                         "Comment=Start Licasa in background at login\n"
                         f'Exec={command} --background\n'
                         "Terminal=false\nX-Licasa-Background=1\n")

    def early_quit_preserves_legacy_consent(self) -> None:
        """A quick normal Quit must complete deferred startup-file adoption."""
        original_environment, original_identity = self.environment, self.identity
        root = Path(self.private_directory.name) / "early-quit"
        root.mkdir(mode=0o700)
        self.identity = {}
        self.configure(root)
        try:
            self.identity = self.control("identity", headless=True)
            self.enable_background()
            process = self.start(background=False)
            directory = Path(self.environment["XDG_CONFIG_HOME"]) / "autostart"
            legacy = directory / "licasa-background.desktop"
            owned = directory / f'licasa-background-{self.identity["installationId"]}.desktop'
            if not legacy.is_file() or owned.exists():
                raise QualificationFailure("Early-Quit fixture missed the deferred-adoption window")
            self.control("quit")
            if wait_for_exit(process, self.timeout) != 0:
                raise QualificationFailure("Early visible Quit did not exit normally")
            if legacy.exists() or not owned.is_file():
                raise QualificationFailure("Early Quit lost or failed to adopt the legacy opt-in")
            process = self.start(background=True)
            if not self.control("status").get("backgroundEnabled"):
                raise QualificationFailure("Early Quit did not preserve durable background consent")
            self.stop(process)
            self.event("early-quit-legacy-consent", result="passed",
                       deferred_before_quit=True, normal_exit=True, consent_survived_restart=True)
        finally:
            self.environment, self.identity = original_environment, original_identity

    def generation_successor(self) -> None:
        """Replace only a private ELF copy; inert suffix gives a distinct receipt."""
        original_binary, original_identity = self.binary, self.identity
        artifact_root = Path(self.private_directory.name) / "successor-artifact"
        artifact_root.mkdir(mode=0o700)
        fixture = artifact_root / "licasa"
        shutil.copy2(original_binary, fixture)
        try:
            self.binary = fixture
            self.identity = {}
            self.identity = self.control("identity", headless=True)
            old_generation = self.identity["generation"]
            old_installation = self.identity["installationId"]
            self.enable_background()
            process = self.start(background=True)
            successor = artifact_root / "next-generation"
            shutil.copy2(fixture, successor)
            with successor.open("ab") as stream:
                stream.write(b"\nLICASA_LIFECYCLE_INERT_TEST_SUCCESSOR_1\n")
            successor.replace(fixture)
            self.identity = self.control("identity", headless=True)
            if (self.identity["installationId"] != old_installation or
                    self.identity["generation"] == old_generation):
                raise QualificationFailure("Test successor did not retain scope/change generation")
            status = self.control("status")
            if status["generation"] != old_generation or status["pid"] != process.pid:
                raise QualificationFailure("Status concealed the surviving predecessor generation")
            self.control("health", expected=(5,))
            self.event("deleted-executable-predecessor", predecessor=old_generation,
                       successor=self.identity["generation"], pid=process.pid,
                       artifact_kind="private-copy-with-inert-test-suffix")
            self.stop(process)
            replacement = self.start(background=True)
            self.control("health")
            self.stop(replacement)
        finally:
            self.binary, self.identity = original_binary, original_identity

    def emulated_snap_idle(self) -> None:
        """Real-time finite warmth check, explicitly outside Snap confinement."""
        original_environment, original_identity = self.environment, self.identity
        snap_root = Path(self.private_directory.name) / "snap-fixture"
        snap_root.mkdir(mode=0o700)
        self.identity = {}
        self.configure(snap_root)
        user_data = snap_root / "revision-data"
        user_data.mkdir(mode=0o700)
        user_common = snap_root / "common"
        user_common.mkdir(mode=0o700)
        self.environment.update(SNAP_NAME="licasa", SNAP_INSTANCE_NAME="licasa",
                                SNAP_REVISION="lifecycle-fixture-1", SNAP_USER_DATA=str(user_data),
                                SNAP_USER_COMMON=str(user_common))
        try:
            self.identity = self.control("identity", headless=True)
            self.enable_background()
            started = time.monotonic()
            process = self.start(background=True)
            status = self.control("status")
            if not status.get("backgroundEnabled") or not 0 < status.get("idleRemainingMs", -1) <= 60000:
                raise QualificationFailure("Snap fixture did not acquire its finite hidden lease")
            try:
                process.wait(timeout=max(0.01, 58 - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                pass
            else:
                raise QualificationFailure("Snap fixture exited before its 60-second idle lease")
            before = self.control("status")
            self.duplicate_background(process)
            after = self.control("status")
            if after["idleRemainingMs"] > before["idleRemainingMs"]:
                raise QualificationFailure("A background probe extended the hidden idle lease")
            code = wait_for_exit(process, max(0.01, 70 - (time.monotonic() - started)))
            elapsed = time.monotonic() - started
            self.event("emulated-snap-idle-exit", returncode=code, elapsed_seconds=elapsed,
                       idle_lease_seconds=60, drain_upper_bound_seconds=max(0, elapsed - 60),
                       confinement="not_tested", store_auto_refresh="not_tested")
            if code != 0 or not 60 <= elapsed <= 70:
                raise QualificationFailure("Finite Snap fixture idle/drain deadline failed")
            self.control("status", expected=(3,))
            entry = user_data / ".config/autostart/licasa-background.desktop"
            if not entry.is_file():
                raise QualificationFailure("Idle expiry removed the accepted background startup")
            consent = user_common / "licasa-background.ini"
            before = consent.read_text()
            if before.count("enabled=true") != 1:
                raise QualificationFailure("Snap fixture has no single persistent common opt-in")
            consent.write_text(before.replace("enabled=true", "enabled=false"))
            restored = snap_root / "restored-old-revision-data"
            restored.mkdir(mode=0o700)
            self.environment.update(SNAP_REVISION="lifecycle-restored-revision",
                                    SNAP_USER_DATA=str(restored))
            self.enable_background()
            completed = subprocess.run([str(self.binary), "--background"], env=self.environment,
                                       capture_output=True, timeout=self.timeout)
            self.event("emulated-snap-restored-revision-opt-out", returncode=completed.returncode,
                       stdout=completed.stdout.decode(errors="replace"),
                       stderr=completed.stderr.decode(errors="replace"),
                       consent_sha256=sha256(consent), snapd_revert="not_tested")
            if completed.returncode != 0 or "enabled=true" in consent.read_text():
                raise QualificationFailure("Restored revision resurrected an opted-out background")
            self.control("status", expected=(3,))
            restored_entry = restored / ".config/autostart/licasa-background.desktop"
            if restored_entry.exists():
                raise QualificationFailure("Restored owned startup survived common opt-out")
        finally:
            self.environment, self.identity = original_environment, original_identity

    def disabled_background_stays_off(self) -> None:
        original_environment = self.environment
        off_root = Path(self.private_directory.name) / "disabled-fixture"
        off_root.mkdir(mode=0o700)
        self.configure(off_root)
        try:
            for stale_registration in (False, True):
                if stale_registration:
                    self.enable_background()
                completed = subprocess.run([str(self.binary), "--background"],
                                           env=self.environment, capture_output=True,
                                           timeout=self.timeout)
                self.event("background-off-launch", stale_registration=stale_registration,
                           returncode=completed.returncode,
                           stdout=completed.stdout.decode(errors="replace"),
                           stderr=completed.stderr.decode(errors="replace"))
                if completed.returncode != 0:
                    raise QualificationFailure("Opted-out background startup did not exit normally")
                self.control("status", expected=(3,))
        finally:
            self.environment = original_environment

    def tampered_endpoint(self) -> None:
        endpoint = Path(self.identity["endpoint"])
        endpoint.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        sentinel = endpoint.parent / "unrelated-file"
        sentinel.write_text("must remain unchanged\n")
        endpoint.symlink_to(sentinel)
        try:
            self.control("status", expected=(7, 9), headless=True)
            if not endpoint.is_symlink() or sentinel.read_text() != "must remain unchanged\n":
                raise QualificationFailure("Control modified a tampered endpoint or its target")
            self.event("tampered-socket-preserved")
        finally:
            if endpoint.is_symlink():
                endpoint.unlink()

    def seed(self, seed: int, operations: int) -> None:
        generator = random.Random(seed)
        process = self.start(background=True)
        prepared = False
        counts: dict[str, int] = {}
        for index in range(operations):
            action = generator.choice(("status", "health", "prepare-update", "cancel-update",
                                       "wrong-installation", "unknown-protocol", "stop-relaunch"))
            counts[action] = counts.get(action, 0) + 1
            self.event("seed-choice", seed=seed, index=index, choice=action)
            if action == "stop-relaunch":
                self.stop(process)
                process = self.start(background=True)
                prepared = False
            elif action in ("wrong-installation", "unknown-protocol"):
                request = {"protocol": 999 if action == "unknown-protocol" else PROTOCOL,
                           "installationId": ("f" * 32 if action == "wrong-installation"
                                              else self.identity["installationId"]),
                           "operation": "stop-for-update"}
                reply = socket_request(Path(self.identity["endpoint"]), request, self.timeout)
                expected = "unsupported" if action == "unknown-protocol" else "wrong-installation"
                self.event("seed-rejection", seed=seed, index=index, reply=reply)
                if reply is None or reply.get("result") != expected or process.poll() is not None:
                    raise QualificationFailure("Invalid control request affected the resident")
            else:
                reply = self.control(action)
                validate_reply(reply, self.identity, running=True)
                if reply["pid"] != process.pid:
                    raise QualificationFailure("Seeded action changed process ownership")
                prepared = action == "prepare-update" or (prepared and action != "cancel-update")
        self.stop(process)
        self.event("seed-complete", seed=seed, operations=operations,
                   action_counts=counts, prepared_at_end=prepared)

    def run(self, seeds: list[int], operations: int, snap_idle: bool = False) -> None:
        self.private_directory = tempfile.TemporaryDirectory(prefix="licasa-lc-")
        self.configure(Path(self.private_directory.name))
        self.identity = self.control("identity", headless=True)
        if self.identity.get("generation") != sha256(self.binary):
            raise QualificationFailure("Build generation is not the selected executable hash")
        if not Path(self.identity["endpoint"]).is_relative_to(
                Path(self.environment["XDG_RUNTIME_DIR"])):
            raise QualificationFailure("Endpoint escaped the isolated runtime directory")
        self.control("status", expected=(3,), headless=True)
        self.control("health", expected=(3,), headless=True)
        self.early_quit_preserves_legacy_consent()
        self.tampered_endpoint()
        self.disabled_background_stays_off()
        self.enable_background()
        process = self.start(background=True)
        self.duplicate_background(process)
        self.negative_requests()
        self.prepared_launch_is_blocked(process)
        self.stop(process)
        for seed in seeds:
            self.seed(seed, operations)
        self.generation_successor()
        self.visible_busy_and_explicit_quit()
        if snap_idle:
            self.emulated_snap_idle()

    def cleanup(self) -> None:
        # Outcome is written before cleanup. These actions cannot qualify stop.
        for process in self.processes:
            if process.poll() is None:
                self.event("failure-cleanup", pid=process.pid, signal="TERM")
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    self.event("failure-cleanup", pid=process.pid, signal="KILL")
                    process.kill()
                    process.wait(timeout=self.timeout)
        for handle in self.handles:
            handle.close()
        if self.private_directory is not None:
            self.private_directory.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="New evidence directory; existing output is never overwritten")
    parser.add_argument("--seeds", nargs="+", type=int, default=list(DEFAULT_SEEDS))
    parser.add_argument("--operations", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--emulated-snap-idle", action="store_true",
                        help="Also observe a real 60-second lease; no confinement/Store claim")
    args = parser.parse_args()
    if args.operations < 1 or not 0 < args.timeout <= 120:
        parser.error("operations must be positive and timeout must be in (0, 120]")
    args.output.mkdir(parents=True, exist_ok=False)
    campaign = ProcessCampaign(args.binary, args.output, args.timeout)
    report: dict[str, Any] = {
        "schema_version": 1, "scope": "isolated-source-process-lifecycle",
        "protocol": PROTOCOL, "binary_sha256": sha256(args.binary),
        "verifier_sha256": sha256(Path(__file__)), "prng": "python-random-MT19937-v3",
        "python_version": platform.python_version(),
        "support_manifest_sha256": sha256(
            Path(__file__).resolve().parents[1] / "packaging/support-matrix.json"),
        "seeds": args.seeds, "operations_per_seed": args.operations,
        "package_manager_coverage": "not_tested", "store_auto_refresh": "not_tested",
        "desktop_login_reboot": "not_tested", "physical_gpu_coverage": "not_tested",
        "result": "failed",
    }
    try:
        campaign.run(args.seeds, args.operations, args.emulated_snap_idle)
        report.update(result="passed", identity=campaign.identity)
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["events_before_cleanup"] = len(campaign.trace)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        campaign.cleanup()
    print(json.dumps({"result": report["result"], "report": str(args.output / "report.json")}))
    if report["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
