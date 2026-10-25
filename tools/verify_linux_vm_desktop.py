#!/usr/bin/env python3
"""Qualify an existing disposable VM's normal GNOME/Plasma Wayland login session.

Uses guest desktop activation and QEMU screenshots; never connects to a host
display, login session, or GPU. Provision and mark a disposable guest first.
"""

import argparse
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time

from verify_snap_desktop import pattern_present

ROOT = Path(__file__).resolve().parents[1]


def qmp_command(path, command, arguments):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(15)
        connection.connect(str(path))
        stream = connection.makefile('rwb')
        json.loads(stream.readline())
        for name, params in [('qmp_capabilities', {}), (command, arguments)]:
            stream.write((json.dumps({'execute': name, 'arguments': params}) + '\n').encode())
            stream.flush()
            while True:
                response = json.loads(stream.readline())
                if 'error' in response:
                    raise RuntimeError(str(response['error']))
                if 'return' in response:
                    break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--known-hosts', type=Path, required=True)
    parser.add_argument('--user', default='qualifier')
    parser.add_argument('--qmp', type=Path, required=True)
    parser.add_argument('--qemu-shared-directory', default='/vm')
    parser.add_argument('--desktop', choices=('gnome', 'kde'), required=True)
    parser.add_argument('--variant', choices=('appimage', 'native', 'portable'), default='appimage')
    parser.add_argument('--install-home', type=Path)
    parser.add_argument('--guest-appimage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    ssh = ['ssh', '-p', str(args.port), '-i', str(args.identity.resolve()), '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=10', '-o', 'UserKnownHostsFile=' + str(args.known_hosts.resolve()),
           f'{args.user}@127.0.0.1']
    worker = (ROOT / 'tools/check_linux_session.py').read_text()

    def action(name):
        arguments = ['python3', '-c', worker, '--desktop', args.desktop, '--variant', args.variant,
                     '--appimage', str(args.guest_appimage)]
        if args.install_home:
            arguments += ['--install-home', str(args.install_home)]
        command = shlex.join(arguments + [name])
        completed = subprocess.run(ssh + [command], capture_output=True, text=True, timeout=45)
        (output / (name + '.stdout')).write_text(completed.stdout)
        (output / (name + '.stderr')).write_text(completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'Guest {name} failed; see {name}.stderr')
        return json.loads(completed.stdout)

    def capture(name):
        from PIL import Image
        # This file is in the QEMU-only shared directory, never a host display.
        temporary = args.qmp.resolve().parent / 'qualification-screen.ppm'
        qmp_command(args.qmp, 'screendump', {'filename': args.qemu_shared_directory + '/' + temporary.name})
        with Image.open(temporary) as frame:
            frame.save(output / (name + '.png'))
            return frame.convert('RGB').copy()

    def visible(name):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if pattern_present(capture(name)):
                time.sleep(1)
                if pattern_present(capture(name)) and action('status')['application_pids']:
                    return
            time.sleep(0.5)
        raise RuntimeError('Expected photo did not remain visible: ' + name)

    report = {'desktop': args.desktop, 'variant': args.variant,
              'environment': 'normal_wayland_login_in_disposable_qemu_vm',
              'physical_gpu_coverage': 'not_tested', 'routes': {}}
    try:
        report['session'] = action('prepare')
        action('quit')
        deadline = time.monotonic() + 5
        while pattern_present(capture('before')) and time.monotonic() < deadline:
            time.sleep(0.2)
        if pattern_present(capture('before')):
            raise RuntimeError('Photo fixture already visible before activation')
        action('menu')
        report['routes']['desktop_entry'] = 'passed'
        action('handler')
        visible('file-handler')
        report['routes']['file_handler'] = 'passed_visible_photo'
        action('quit')
        action('photo')
        visible('desktop-photo')
        report['routes']['desktop_entry_photo'] = 'passed_visible_photo'
        action('quit')
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        (output / 'desktop-report.json').write_text(json.dumps(report, indent=2) + '\n')
        try:
            (output / 'application-logs.json').write_text(json.dumps(action('logs'), indent=2) + '\n')
        except Exception as error:
            (output / 'log-collection-error.txt').write_text(str(error))
    print(f'{args.desktop} {args.variant}: normal desktop activation and visible photos passed')


if __name__ == '__main__':
    main()
