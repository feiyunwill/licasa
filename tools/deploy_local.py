#!/usr/bin/env python3
"""Install an already-built Licasa tree into the current user's local prefix."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from lifecycle_update import (InstallationGate, LifecycleError, install_overlay,
                              install_local_launcher, owned_processes)


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def desktop_exec_argument(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("$", "\\$").replace("`", "\\`").replace("%", "%%")
    # Desktop string decoding precedes command parsing. Retain argument
    # escapes through both steps, as in the AppImage installer.
    return '"' + value.replace("\\", "\\\\") + '"'


def wait_for_local_processes(binary: Path, timeout: float) -> set[int]:
    """Wait for the launched Local viewer, including a deleted executable."""
    deadline = time.monotonic() + timeout
    while True:
        processes = set(owned_processes(binary))
        if processes or time.monotonic() >= deadline:
            return processes
        time.sleep(0.1)


def launch_local_copy(binary: Path) -> None:
    launcher = binary.with_name("licasa-launch")
    command = [str(launcher if launcher.is_file() else binary)]
    print(f"Launching {binary}", flush=True)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    running = wait_for_local_processes(binary, timeout=10)
    if process.poll() is not None or process.pid not in running:
        raise SystemExit("Local Licasa did not start after installation.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install a built Licasa tree into ~/.local and refresh desktop metadata."
    )
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--allow-disable-motion-playback", action="store_true",
                        help="Allow replacing a motion-enabled local install with a build without playback")
    args = parser.parse_args()

    build_dir = args.build_dir.expanduser().resolve()
    prefix = args.prefix.expanduser().resolve()

    if not build_dir.is_dir() or not (build_dir / "CMakeCache.txt").is_file():
        raise SystemExit(f"Not a configured CMake build directory: {build_dir}")
    if os.geteuid() == 0:
        raise SystemExit("Refusing to deploy the local development build as root.")

    motion_backend = prefix / "lib" / "licasa" / "plugins" / "motion" / "liblicasa_motion_photo_backend.so"
    motion_enabled = "LICASA_ENABLE_MOTION_PLAYBACK:BOOL=ON" in (
        build_dir / "CMakeCache.txt").read_text(encoding="utf-8").splitlines()
    if motion_backend.is_file() and not motion_enabled and not args.allow_disable_motion_playback:
        raise SystemExit(
            "This build disables Live Photo playback, but the current local install supports it. "
            "Choose a motion-enabled build or pass --allow-disable-motion-playback explicitly."
        )

    binary = prefix / "bin" / "licasa"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    # Build the entire candidate before asking the running installation to
    # stop. DESTDIR keeps this operation away from the active viewer.
    with tempfile.TemporaryDirectory(prefix=".licasa-local-stage.", dir=prefix.parent) as temporary:
        staging = Path(temporary)
        subprocess.run(["cmake", "--install", str(build_dir), "--prefix", str(prefix)],
                       env=dict(os.environ, DESTDIR=str(staging)), check=True)
        staged_prefix = staging / str(prefix).lstrip("/")
        if not (staged_prefix / "bin/licasa").is_file():
            raise SystemExit("The staged Local runtime has no viewer executable; installation was not changed.")
        install_local_launcher(staged_prefix)
        desktop_file = staged_prefix / "share/applications/licasa.desktop"
        # A Snap install adds a second Licasa launcher. Make the development
        # build explicit and pin it to this prefix so the two are distinguishable.
        if desktop_file.is_file():
            entries = desktop_file.read_text(encoding="utf-8").splitlines()
            launcher = prefix / "bin/licasa-launch"
            replacements = {"Name": "Licasa (Local)", "TryExec": str(launcher),
                            "Exec": f'bash {desktop_exec_argument(launcher)} %f'}
            updated = []
            for entry in entries:
                key = entry.partition("=")[0]
                updated.append(f"{key}={replacements[key]}" if key in replacements else entry)
            desktop_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
        try:
            with InstallationGate(prefix) as gate:
                install_overlay(gate, staged_prefix, binary)
        except LifecycleError as error:
            raise SystemExit(str(error)) from error

    desktop_dir = prefix / "share/applications"
    update_desktop_database = shutil.which("update-desktop-database")
    if update_desktop_database and desktop_dir.is_dir():
        run([update_desktop_database, str(desktop_dir)])

    print(f"\nLicasa deployed locally: {binary}")
    if args.launch:
        launch_local_copy(binary)
    else:
        print("Background startup is deferred to the next desktop login.")


if __name__ == "__main__":
    main()
