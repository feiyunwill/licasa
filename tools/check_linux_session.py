#!/usr/bin/env python3
"""Desktop activation worker, exclusively for a marked disposable QEMU/KVM guest."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def require_disposable_vm():
    marker = Path('/etc/licasa-disposable-qualification')
    kind = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True).stdout.strip()
    if (os.getuid() == 0 or kind not in ('kvm', 'qemu') or not marker.is_file()
            or marker.is_symlink() or marker.stat().st_uid != 0 or marker.stat().st_mode & 0o022):
        raise RuntimeError('Use a non-root account in a marked disposable QEMU/KVM guest.')


def session_environment(desktop):
    name = 'gnome-shell' if desktop == 'gnome' else 'kwin_wayland'
    running = False
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            running |= proc.stat().st_uid == os.getuid() and (proc / 'comm').read_text().strip() == name
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    if not running:
        raise RuntimeError(f'No logged-in {desktop} compositor for this user')
    env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}')
    env['DBUS_SESSION_BUS_ADDRESS'] = f"unix:path={env['XDG_RUNTIME_DIR']}/bus"
    # Read the session's activation environment without inspecting compositor
    # memory/environ (KWin can be non-dumpable due to its file capabilities).
    result = subprocess.run(['systemctl', '--user', 'show-environment'], env=env,
                            capture_output=True, text=True, check=True)
    env.update(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if (env.get('XDG_SESSION_TYPE') != 'wayland' or not env.get('WAYLAND_DISPLAY')
            or any(env.get(key) for key in ('QT_QPA_PLATFORM', 'QT_QUICK_BACKEND', 'QSG_RHI_BACKEND'))):
        raise RuntimeError('Expected a normal Wayland login session without forced Qt backends.')
    return env


def application_pids(executable):
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            actual = (proc / 'exe').resolve()
            matches = actual == executable if executable else actual.name == 'licasa' and str(actual).startswith('/tmp/.mount_')
            if proc.stat().st_uid == os.getuid() and matches:
                found.append(int(proc.name))
        except (OSError, RuntimeError):
            continue
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', choices=('gnome', 'kde'), required=True)
    parser.add_argument('--variant', choices=('appimage', 'native', 'portable'), default='appimage')
    parser.add_argument('--install-home', type=Path)
    parser.add_argument('--appimage', type=Path, required=True)
    parser.add_argument('action', choices=('prepare', 'status', 'menu', 'handler', 'photo', 'quit', 'logs'))
    args = parser.parse_args()
    require_disposable_vm()
    env = session_environment(args.desktop)
    home = Path(env['HOME'])
    output = home / 'licasa-session-qualification'
    output.mkdir(exist_ok=True)
    picture = output / 'photo with spaces 日本語.png'
    payload = (args.install_home or home) / '.local/opt/licasa-appimage' if args.variant == 'appimage' else Path('/opt/licasa')
    executable = payload / 'usr/bin/licasa'
    launcher = payload / 'AppRun' if args.variant == 'appimage' else Path('/usr/bin/licasa')
    desktop_id = 'licasa-appimage.desktop' if args.variant == 'appimage' else 'licasa.desktop'
    if args.variant == 'portable':
        launcher = args.appimage
        executable = None
        desktop_id = 'licasa-portable-qualification.desktop'
    if not launcher.is_file() or (executable is not None and not executable.is_file()):
        raise RuntimeError('Install the package in the disposable guest before testing')
    result = {'action': args.action, 'desktop': args.desktop, 'variant': args.variant}
    command = None
    if args.action == 'prepare':
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (480, 360))
        draw = ImageDraw.Draw(image)
        for index, color in enumerate(((240, 24, 24), (24, 216, 24), (24, 48, 240), (240, 216, 24))):
            x, y = index % 2 * 240, index // 2 * 180
            draw.rectangle((x, y, x + 239, y + 179), fill=color)
        image.save(picture)
        if args.variant == 'portable':
            # Test fixture for menu/file activation of the raw, FUSE-mounted
            # AppImage. The regular installation routes use the shipped entries.
            value = str(args.appimage)
            for before, after in [('\\', '\\\\'), ('"', '\\"'), ('$', '\\$'), ('`', '\\`'), ('%', '%%')]:
                value = value.replace(before, after)
            value = value.replace('\\', '\\\\')
            entry = Path(env.get('XDG_DATA_HOME', str(home / '.local/share'))) / 'applications' / desktop_id
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text('[Desktop Entry]\nType=Application\nName=Licasa portable qualification\n'
                             f'Exec=env "{value}" %f\nTerminal=false\nMimeType=image/png;\n')
            subprocess.run(['update-desktop-database', str(entry.parent)], env=env, check=True, capture_output=True)
        subprocess.run(['xdg-mime', 'default', desktop_id, 'image/png'], env=env, check=True, capture_output=True)
        result.update(session=env['XDG_SESSION_TYPE'], desktop_environment=env.get('XDG_CURRENT_DESKTOP'),
                      kernel=os.uname().release,
                      drm_drivers=sorted({path.resolve().name for path in Path('/sys/class/drm').glob('card*/device/driver')}),
                      forced_qt_platform=False, forced_qt_quick_backend=False,
                      appimage_sha256=hashlib.sha256(args.appimage.read_bytes()).hexdigest(),
                      os_release=Path('/etc/os-release').read_text(),
                      shell_version=subprocess.check_output(
                          ['gnome-shell' if args.desktop == 'gnome' else 'kwin_wayland', '--version'], env=env, text=True).strip())
    elif args.action == 'menu':
        command = (['gtk-launch', desktop_id] if args.desktop == 'gnome'
                   else ['kstart', '--application', desktop_id.removesuffix('.desktop')])
    elif args.action == 'handler':
        command = ['gio', 'open', str(picture)] if args.desktop == 'gnome' else ['kioclient', 'exec', str(picture)]
    elif args.action == 'photo':
        command = ['gtk-launch', desktop_id, picture.as_uri()]
    elif args.action == 'quit':
        command = [str(launcher), '--quit']
    elif args.action == 'logs':
        result['logs'] = {path.name: path.read_text(errors='replace')[-100000:]
                          for path in (home / '.local/state/licasa').glob('*.log')}
    if command:
        with (output / f'{args.desktop}-{args.variant}-{args.action}.log').open('a') as log:
            completed = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=20)
        if completed.returncode:
            raise RuntimeError(f'{args.action} activation failed with status {completed.returncode}')
        deadline = time.monotonic() + 20
        while bool(application_pids(executable)) == (args.action == 'quit') and time.monotonic() < deadline:
            time.sleep(0.1)
        if bool(application_pids(executable)) == (args.action == 'quit'):
            raise RuntimeError(f'Application did not {"stop" if args.action == "quit" else "start"}')
        result['command'] = command
    result['application_pids'] = application_pids(executable)
    result['application_executables'] = [str((Path('/proc') / str(pid) / 'exe').resolve())
                                       for pid in result['application_pids']]
    print(json.dumps(result))


if __name__ == '__main__':
    main()
