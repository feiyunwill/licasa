#!/usr/bin/env python3
"""Check normal Snap launches in nested GNOME/Plasma on a disposable hosted VM."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


PHOTO_COLORS = ((238, 32, 32), (32, 220, 32), (32, 64, 238), (238, 210, 32))


def pattern_present(image):
    """Require substantial, correctly arranged areas of all four fixture colors."""
    image = image.convert("RGB")
    groups = [[] for _ in PHOTO_COLORS]
    for y in range(0, image.height, 4):
        for x in range(0, image.width, 4):
            pixel = image.getpixel((x, y))
            for index, color in enumerate(PHOTO_COLORS):
                if max(abs(a - b) for a, b in zip(pixel, color)) <= 30:
                    groups[index].append((x, y))
                    break
    if min(map(len, groups)) < 128:
        return False
    centers = [(sum(x for x, _ in group) / len(group),
                sum(y for _, y in group) / len(group)) for group in groups]
    red, green, blue, yellow = centers
    width, height = green[0] - red[0], blue[1] - red[1]
    return (width > 30 and height > 30
            and abs(red[1] - green[1]) < height * 0.25
            and abs(blue[1] - yellow[1]) < height * 0.25
            and abs(red[0] - blue[0]) < width * 0.25
            and abs(green[0] - yellow[0]) < width * 0.25)


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def licasa_processes():
    result = []
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            if (directory.stat().st_uid == os.getuid()
                    and str((directory / "exe").resolve()).startswith("/snap/licasa/")
                    and (directory / "comm").read_text().strip() == "licasa"):
                result.append(int(directory.name))
        except (OSError, RuntimeError):
            continue
    return result


def wait_until(predicate, description, processes=(), timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if any(process.poll() is not None for process in processes):
            raise RuntimeError(f"A desktop component exited while waiting for {description}")
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {description}")


def close_snap(app, environment):
    try:
        result = subprocess.run(["snap", "run", "licasa", "--quit"], env=environment,
                                capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError("The normal Snap quit command failed")
        wait_until(lambda: not licasa_processes(), "normal Snap shutdown", timeout=10)
    finally:
        stop(app)
        # A graphics retry can detach. Only this Snap's owned processes are considered.
        for pid in licasa_processes():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def bus_owner(name, environment):
    result = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
         "--object-path", "/org/freedesktop/DBus", "--method",
         "org.freedesktop.DBus.NameHasOwner", name],
        env=environment, capture_output=True, text=True, timeout=5,
    )
    return result.returncode == 0 and "true" in result.stdout


def screenshot(display, destination):
    from PIL import Image

    subprocess.run(["import", "-display", display, "-window", "root", str(destination)],
                   check=True, capture_output=True, timeout=15)
    with Image.open(destination) as image:
        return image.convert("RGB")


def qualify(args):
    from PIL import Image, ImageDraw

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if licasa_processes():
        raise RuntimeError("Use a fresh VM with no Licasa process already running")
    listed = subprocess.check_output(["snap", "list", "licasa"], text=True).splitlines()
    if len(listed) != 2 or listed[1].split()[1] != args.version:
        raise RuntimeError("The installed Snap does not match the selected release")
    source = Path("/snap/licasa/current/usr/share/licasa/source-commit").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise RuntimeError("The installed Snap has no valid source receipt")
    picture = Path.home() / "Pictures/licasa-desktop-qualification.png"
    if picture.exists():
        raise RuntimeError("Refusing to replace an existing desktop-qualification photo")
    picture.parent.mkdir(exist_ok=True)
    image = Image.new("RGB", (480, 360))
    painter = ImageDraw.Draw(image)
    for index, color in enumerate(PHOTO_COLORS):
        x, y = (index % 2) * 240, (index // 2) * 180
        painter.rectangle((x, y, x + 239, y + 179), fill=color)
    image.save(picture)
    cpu = Path("/proc/cpuinfo").read_text()
    cpu_model = re.search(r"^model name\s*:\s*(.+)$", cpu, re.MULTILINE)
    report = {
        "desktop": args.desktop, "snap_version": args.version, "snap_revision": listed[1].split()[2],
        "package_source_commit": source, "cpu_model": cpu_model.group(1) if cpu_model else "unknown",
        "display_environment": "nested_desktop_on_hosted_vm",
        "physical_gpu_coverage": "not_tested", "cases": {},
    }
    connections = subprocess.check_output(["snap", "connections", "licasa"], text=True)
    (output / "snap-connections.txt").write_text(connections)
    environment = dict(os.environ)
    for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH", "QML_IMPORT_PATH",
                 "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QUICK_BACKEND",
                 "QSG_RHI_BACKEND", "QT_LOGGING_RULES", "QT_QPA_PLATFORM", "WAYLAND_DISPLAY"):
        environment.pop(name, None)
    # Snap's normal desktop wrapper expects the standard per-user runtime directory.
    runtime = Path(f"/run/user/{os.getuid()}")
    runtime.mkdir(mode=0o700, exist_ok=True)
    if runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
        raise RuntimeError("The desktop runtime directory must be private and owned by the test user")
    bus_path = runtime / "bus"
    environment.update(XDG_RUNTIME_DIR=str(runtime), LIBGL_ALWAYS_SOFTWARE="1",
                       DBUS_SESSION_BUS_ADDRESS=f"unix:path={bus_path}",
                       XDG_CONFIG_HOME=str(output / "config"),
                       XDG_CURRENT_DESKTOP="GNOME" if args.desktop == "gnome" else "KDE")
    logs = []

    def launch(command, name, env):
        log = (output / name).open("w")
        logs.append(log)
        return subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)

    xvfb = None
    session_bus = None
    try:
        # A bus in /tmp is hidden by Snap's private mount namespace. Use the
        # standard per-user location, just as a normal desktop session does.
        if not bus_path.exists():
            session_bus = launch(["dbus-daemon", "--session", "--nofork", "--nopidfile",
                                  f"--address=unix:path={bus_path}"], "session-bus.log", environment)
            wait_until(lambda: bus_path.is_socket(), "session bus", (session_bus,))
        elif not bus_path.is_socket():
            raise RuntimeError("The standard session bus path is not a socket")
        wait_until(lambda: bus_owner("org.freedesktop.DBus", environment), "usable session bus")
        if any(bus_owner(name, environment) for name in ("org.gnome.Shell", "org.kde.plasmashell")):
            raise RuntimeError("Refusing to attach to an existing desktop session")
        report["session_bus"] = "started_standard_user_bus" if session_bus else "existing_standard_user_bus"
        display_number = 93
        if Path(f"/tmp/.X11-unix/X{display_number}").exists():
            raise RuntimeError("Refusing to attach to an existing X server")
        display = f":{display_number}"
        environment["DISPLAY"] = display
        xvfb = launch(["Xvfb", display, "-screen", "0", "1600x1000x24", "-nolisten", "tcp", "-ac"],
                      "xvfb.log", environment)
        wait_until(lambda: Path(f"/tmp/.X11-unix/X{display_number}").is_socket(), "Xvfb", (xvfb,))
        renderer = subprocess.check_output(["glxinfo", "-B"], env=environment, text=True)
        (output / "renderer.txt").write_text(renderer)
        if not any(name in renderer.lower() for name in ("llvmpipe", "softpipe", "swrast")):
            raise RuntimeError("The hosted desktop must use software rendering")
        for protocol in ("x11", "wayland"):
            processes = []
            session_env = dict(environment)
            session_env["XDG_SESSION_TYPE"] = protocol
            socket = f"licasa-{args.desktop}-{os.getpid()}"
            if (runtime / socket).exists():
                raise RuntimeError("Refusing to reuse an existing Wayland socket")
            try:
                if args.desktop == "gnome":
                    command = ["gnome-shell", "--x11"] if protocol == "x11" else [
                        "gnome-shell", "--nested", "--wayland", "--no-x11", f"--wayland-display={socket}"]
                    processes.append(launch(command, f"{protocol}-gnome.log", session_env))
                    owner = "org.gnome.Shell"
                elif protocol == "x11":
                    session_env["QT_QPA_PLATFORM"] = "xcb"
                    processes.append(launch(["kwin_x11"], "x11-kwin.log", session_env))
                    processes.append(launch(["plasmashell"], "x11-plasma.log", session_env))
                    owner = "org.kde.plasmashell"
                else:
                    processes.append(launch(
                        ["kwin_wayland", "--x11-display", display, "--socket", socket,
                         "--width", "1280", "--height", "800", "--no-lockscreen",
                         "--exit-with-session", shutil.which("plasmashell")],
                        "wayland-plasma.log", session_env))
                    owner = "org.kde.plasmashell"
                if protocol == "wayland":
                    wait_until(lambda: (runtime / socket).is_socket(), "Wayland socket", processes)
                    session_env["WAYLAND_DISPLAY"] = socket
                wait_until(lambda: bus_owner(owner, session_env), "desktop shell", processes)
                for mode in ("default", "software"):
                    case = f"{protocol}-{mode}"
                    app_env = dict(session_env, QT_QPA_PLATFORM="xcb" if protocol == "x11" else "wayland")
                    if mode == "software":
                        app_env["QT_QUICK_BACKEND"] = "software"
                    before = screenshot(display, output / f"{case}-before.png")
                    if pattern_present(before):
                        raise RuntimeError("The fixture is already on screen before launch")
                    app = launch(["snap", "run", "licasa", str(picture)], f"{case}-app.log", app_env)
                    try:
                        wait_until(lambda: pattern_present(screenshot(display, output / f"{case}.png")),
                                   f"visible photo in normal Snap launch ({case})", processes, timeout=30)
                        time.sleep(2)
                        if (not licasa_processes()
                                or not pattern_present(screenshot(display, output / f"{case}.png"))):
                            raise RuntimeError(f"Snap did not keep its photo visible for {case}")
                        log = (output / f"{case}-app.log").read_text()
                        backend = re.search(r"sceneGraphInitialized\): graphicsApi=(\w+)", log)
                        if not backend or (mode == "software" and backend.group(1) != "Software"):
                            raise RuntimeError(f"No matching initialized renderer for {case}")
                        report["cases"][case] = {"status": "passed", "visible_photo": True,
                                                 "graphics_api": backend.group(1),
                                                 "software_retry": "retrying once with the Qt software renderer" in log,
                                                 "normal_snap_launcher": True}
                        print(f"{args.desktop} {case}: normal Snap launch presented the photo", flush=True)
                    finally:
                        close_snap(app, app_env)
            finally:
                for process in reversed(processes):
                    stop(process)
        report["status"] = "passed_nested_desktop_checks"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        stop(xvfb)
        stop(session_bus)
        for log in logs:
            log.close()
        (output / "desktop-report.json").write_text(json.dumps(report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", choices=("gnome", "kde"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_ACTIONS") != "true"):
        parser.error("Desktop checks may run only on a disposable GitHub-hosted VM")
    qualify(args)


if __name__ == "__main__":
    main()
