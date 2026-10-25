#!/usr/bin/env python3
"""Drive the installed application through AT-SPI in an existing desktop session.

Requires python3-pyatspi, xdotool, DBus and a running MATE session. This is a
bounded contract check for names, roles, actions, values and keyboard focus.
Orca's audible announcements still require a human screen-reader acceptance run.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import pyatspi
    from gi.repository import GLib
    report = {"status": "failed", "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
              "session": "virtual MATE X11" if os.environ.get("LICASA_VIRTUAL_DESKTOP") else "existing desktop",
              "scenarios": [], "coverage_limits": [
                  "AT-SPI contracts and Orca event processing are automated; audible speech order and usability require human acceptance.",
                  "Virtual display results do not qualify physical GPU, display-manager login, audio hardware or Wayland."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    app = None
    log = args.output.with_suffix(".application.log").open("w")
    snapshots = []
    def pump():
        context = GLib.MainContext.default()
        for _ in range(20):
            if not context.pending(): break
            context.iteration(False)
    def nodes(root):
        pending = [root]; seen = 0
        while pending and seen < 2000:
            current = pending.pop(0); seen += 1
            try:
                yield current
                pending.extend(current[index] for index in range(current.childCount))
            except Exception:
                continue
    def desktop():
        pump()
        return pyatspi.Registry.getDesktop(0)
    def licasa():
        for candidate in desktop():
            if candidate and "licasa" in candidate.name.lower(): return candidate
        return None
    def find(name=None, role=None, showing=True):
        root = licasa()
        if root:
            for candidate in nodes(root):
                try:
                    if ((name is None or candidate.name == name) and
                        (role is None or candidate.getRole() == role) and
                        (not showing or candidate.getState().contains(pyatspi.STATE_SHOWING))):
                        return candidate
                except Exception: continue
        return None
    def wait(description, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if app is not None and app.poll() is not None:
                raise RuntimeError(f"Application exited ({app.returncode}) during {description}")
            pump()
            value = predicate()
            if value: return value
            time.sleep(0.1)
        raise RuntimeError("Timed out: " + description)
    def action(control, wanted):
        interface = control.queryAction()
        names = [interface.getName(index) for index in range(interface.nActions)]
        for index, name in enumerate(names):
            if wanted.lower() in name.lower():
                if not interface.doAction(index): raise RuntimeError(f"Action {name} rejected by {control.name}")
                return
        raise RuntimeError(f"No {wanted} action on {control.name}: {names}")
    def key(keys):
        windows = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "^Licasa$"], capture_output=True, text=True, timeout=4)
        if windows.returncode == 0:
            subprocess.run(["xdotool", "windowactivate", "--sync", windows.stdout.splitlines()[-1]], check=True, timeout=4)
        subprocess.run(["xdotool", "key", "--clearmodifiers", keys], check=True, timeout=4)
        time.sleep(0.12)
    def record(name, detail=""):
        report["scenarios"].append({"name": name, "status": "passed", "detail": detail})
    def snapshot(label):
        root = licasa()
        result = []
        if root:
            for control in nodes(root):
                try:
                    state = control.getState()
                    result.append({"name": control.name, "role": control.getRoleName(),
                                   "showing": state.contains(pyatspi.STATE_SHOWING),
                                   "focused": state.contains(pyatspi.STATE_FOCUSED),
                                   "checked": state.contains(pyatspi.STATE_CHECKED)})
                except Exception: continue
        snapshots.append({"label": label, "nodes": result})
    try:
        if "MATE" not in report["desktop"].upper():
            raise RuntimeError("This acceptance run requires a real running MATE session")
        session = subprocess.run(["pgrep", "-x", "mate-session"], capture_output=True, timeout=3)
        if session.returncode: raise RuntimeError("mate-session is not running")
        report["app_sha256"] = hashlib.file_digest(args.app.open("rb"), "sha256").hexdigest()
        env = os.environ.copy()
        env.update(QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1", QT_QUICK_BACKEND="software")
        app = subprocess.Popen([str(args.app.absolute())], env=env, stdout=log, stderr=subprocess.STDOUT)
        wait("AT-SPI application registration", licasa)
        opened = wait("named Open image button", lambda: find("Open image", pyatspi.ROLE_PUSH_BUTTON))
        record("open_button_named_and_actionable")
        snapshot("empty")
        # Start with a keyboard action so focus restoration has a precise target.
        opened.queryComponent().grabFocus()
        action(opened, "press")
        chooser = wait("announced file picker", lambda: find(role=pyatspi.ROLE_DIALOG))
        record("picker_dialog_role", chooser.name)
        snapshot("open picker")
        key("Escape")
        wait("picker closes", lambda: find(role=pyatspi.ROLE_DIALOG) is None)
        wait("Open button focus returns", lambda: find("Open image", pyatspi.ROLE_PUSH_BUTTON).getState().contains(pyatspi.STATE_FOCUSED))
        record("cancel_restores_open_button_focus")
        key("ctrl+o")
        wait("keyboard reopen", lambda: find(role=pyatspi.ROLE_DIALOG))
        key("Escape")
        record("keyboard_open_cancel_reopen")
        # Single-instance command routes a real file-open request through the
        # installed app; it is never a substitute QML image source.
        subprocess.run([str(args.app.absolute()), str(args.sample.absolute())], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=8)
        wait("photo window", lambda: find("Open image", pyatspi.ROLE_PUSH_BUTTON) is None)
        key("F11") if find("Open settings", pyatspi.ROLE_PUSH_BUTTON) is None else None
        settings = wait("settings button", lambda: find("Open settings", pyatspi.ROLE_PUSH_BUTTON))
        action(settings, "press")
        toggle = wait("Info overlay checkbox", lambda: find("Info overlay", pyatspi.ROLE_CHECK_BOX))
        previous = toggle.getState().contains(pyatspi.STATE_CHECKED)
        action(toggle, "toggle")
        wait("checkbox reports changed state", lambda: find("Info overlay", pyatspi.ROLE_CHECK_BOX).getState().contains(pyatspi.STATE_CHECKED) != previous)
        action(find("Info overlay", pyatspi.ROLE_CHECK_BOX), "toggle")
        record("toggle_role_state_action")
        snapshot("settings")
        key("Escape")
        key("e")
        exposure = wait("Exposure slider", lambda: find("Exposure", pyatspi.ROLE_SLIDER))
        values = exposure.queryValue()
        before = values.currentValue
        if values.minimumValue != -1 or values.maximumValue != 1 or values.minimumIncrement <= 0:
            raise RuntimeError("Exposure numeric range or step missing from AT-SPI")
        action(exposure, "increase")
        wait("screen-reader slider action updates actual value", lambda: find("Exposure", pyatspi.ROLE_SLIDER).queryValue().currentValue > before)
        record("adjustment_role_range_step_and_action")
        exposure = find("Exposure", pyatspi.ROLE_SLIDER)
        exposure.queryComponent().grabFocus()
        wait("slider keyboard focus", lambda: find("Exposure", pyatspi.ROLE_SLIDER).getState().contains(pyatspi.STATE_FOCUSED))
        before = exposure.queryValue().currentValue
        key("Right")
        wait("keyboard adjustment", lambda: find("Exposure", pyatspi.ROLE_SLIDER).queryValue().currentValue > before)
        record("adjustment_keyboard_focus_and_value")
        snapshot("editor")
        key("ctrl+shift+s")
        save_dialog = wait("Save As dialog", lambda: find(role=pyatspi.ROLE_DIALOG))
        if not save_dialog.name: raise RuntimeError("Save As dialog has no accessible name")
        record("save_as_dialog_name_and_role", save_dialog.name)
        key("Escape")
        wait("Save As closes", lambda: find(role=pyatspi.ROLE_DIALOG) is None)
        record("save_as_keyboard_cancel")
        key("c")
        crop = wait("named keyboard crop pane", lambda: find("Crop frame", pyatspi.ROLE_PANEL))
        if "arrow" not in crop.description.lower() or "escape" not in crop.description.lower():
            raise RuntimeError("Crop pane does not explain its keyboard controls")
        crop.queryComponent().grabFocus()
        key("alt+shift+Left")
        key("Return")
        wait("keyboard applies crop", lambda: find("Crop frame", pyatspi.ROLE_PANEL) is None)
        record("crop_keyboard_instructions_and_apply")
        # A real failed decode creates the production error notification. Check
        # the alert role/name/detail through the desktop accessibility bus.
        broken = Path(os.environ["HOME"]) / "accessibility-broken.png"
        broken.write_bytes(b"deliberately not a PNG")
        subprocess.run([str(args.app.absolute()), str(broken)], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=8)
        alert = wait("named image-error alert", lambda: find("Image could not be opened", pyatspi.ROLE_ALERT))
        if not alert.description:
            raise RuntimeError("Image-error alert has no actionable detail")
        record("notification_alert_name_and_detail", alert.description)
        snapshot("error alert")
        report["status"] = "passed"
        return 0
    except Exception as error:
        report["error"] = str(error)
        snapshot("failure")
        return 1
    finally:
        if app is not None:
            mappings = Path(f"/proc/{app.pid}/maps")
            if mappings.exists():
                report["process"] = {"mapped_qt_libraries": sorted({line.split(maxsplit=5)[5].strip()
                    for line in mappings.read_text().splitlines() if "libQt6" in line and len(line.split(maxsplit=5)) == 6})}
            subprocess.run([str(args.app.absolute()), "--quit"], stdout=log, stderr=subprocess.STDOUT, timeout=8)
            try: app.wait(timeout=4)
            except subprocess.TimeoutExpired: app.terminate(); app.wait(timeout=4)
        report["atspi_snapshots"] = snapshots
        report["application_log"] = str(args.output.with_suffix(".application.log"))
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        log.close()
        print(json.dumps({key: value for key, value in report.items() if key != "atspi_snapshots"}))

if __name__ == "__main__":
    raise SystemExit(main())
