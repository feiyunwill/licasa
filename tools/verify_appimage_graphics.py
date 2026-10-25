#!/usr/bin/env python3
"""Test a release AppImage on a physical render device without host display access.

Use a prepared Fedora/Arch qualification image with Python/Pillow and Weston.
This qualifies distribution userspace on the host kernel, not a native desktop
login or the distribution's kernel/driver installation.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def require_complete_evidence(report, *, appimage_sha256, render_node, nvidia_device=None):
    calibration = report.get('capture_calibration', {})
    expected_device = (str(render_node), nvidia_device)
    if (report.get('appimage_sha256') != appimage_sha256
            or report.get('render_node') != str(render_node)
            or report.get('photo_pattern') != 'correct_orientation'
            or report.get('visible_photo') is not True or report.get('normal_quit') is not True
            or type(report.get('diagnostic_exit_status')) is not int or report['diagnostic_exit_status'] != 0
            or type(report.get('graphics_gate_exit_status')) is not int or report['graphics_gate_exit_status'] != 0
            or calibration.get('status') != 'passed'
            or calibration.get('photo_pattern') != 'correct_orientation'
            or calibration.get('fixture_absent_after_close') is not True
            or any(report.get(kind, {}).get('software_retry') is not False
                   for kind in ('diagnostic', 'application'))
            or not any(device and device in report.get('application_gpu_fds', []) for device in expected_device)):
        raise RuntimeError('Passed graphics worker report has missing or contradictory qualification evidence')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appimage', type=Path, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--render-node', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--nvidia', action='store_true', help='Use a matching NVIDIA userspace image and its control/device nodes')
    args = parser.parse_args()
    node = args.render_node
    if (not re.fullmatch(r'/dev/dri/renderD[0-9]+', str(node)) or not node.is_char_device()
            or not os.access(node, os.R_OK | os.W_OK)):
        parser.error('Choose an already accessible render node; card/master/input nodes are not accepted')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = 'licasa-private-graphics-' + uuid.uuid4().hex[:12]
    report_file = output / 'graphics-report.json'
    report = {'status': 'failed', 'run_id': name, 'phase': 'runner_initialized', 'render_node': str(node)}
    report_file.write_text(json.dumps(report, indent=2) + '\n')
    try:
        appimage = args.appimage.resolve(strict=True)
        appimage_sha256 = hashlib.sha256(appimage.read_bytes()).hexdigest()
        image_id = subprocess.check_output(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'], text=True).strip()
        device = Path('/sys/class/drm') / node.name / 'device'
        command = ['docker', 'run', '--name', name, '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--pids-limit', '256', '--memory', '2g', '--cpus', '2',
                   '--user', f'{os.getuid()}:{os.getgid()}', '--group-add', str(node.stat().st_gid),
                   '--device', str(node), '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=1g', '--shm-size', '256m',
                   '-e', 'LICASA_PRIVATE_GRAPHICS_TEST=1', '-v', f'{ROOT}:/source:ro',
                   '-v', f'{appimage}:/test/Licasa.AppImage:ro', '-v', f'{output}:/results:rw',
                   '--entrypoint', 'python3', args.image, '/source/tools/check_appimage_graphics.py',
                   '--appimage', '/test/Licasa.AppImage', '--sha256', appimage_sha256,
                   '--render-node', str(node), '--output', '/results', '--run-id', name]
        nvidia_device = None
        if args.nvidia:
            if (device / 'driver').resolve().name != 'nvidia':
                raise RuntimeError('--nvidia requires the NVIDIA render node')
            info = (Path('/proc/driver/nvidia/gpus') / device.resolve().name / 'information').read_text()
            minor = re.search(r'^Device Minor:\s*(\d+)\s*$', info, re.M)
            if not minor:
                raise RuntimeError('Cannot map the selected NVIDIA render node to its device minor')
            nvidia_device = '/dev/nvidia' + minor.group(1)
            for vendor_node in (nvidia_device, '/dev/nvidiactl'):
                path = Path(vendor_node)
                if not path.is_char_device() or not os.access(path, os.R_OK | os.W_OK):
                    raise RuntimeError('NVIDIA device nodes must already be accessible')
                command[2:2] = ['--device', vendor_node, '--group-add', str(path.stat().st_gid)]
            command += ['--nvidia-device', nvidia_device]
            primaries = [path for path in (device / 'drm').glob('card*') if re.fullmatch(r'card[0-9]+', path.name)]
            if len(primaries) != 1:
                raise RuntimeError('Cannot identify exactly one primary DRM metadata node for the selected NVIDIA GPU')
            primary = Path('/dev/dri') / primaries[0].name
            # NVIDIA's EGL device lookup stats this identity before its render
            # node. cgroup m-only permits metadata, never read/write/master I/O.
            command[2:2] = ['--device', f'{primary}:{primary}:m', '--group-add', str(primary.stat().st_gid)]
            command += ['--primary-metadata', str(primary)]
        with (output / 'worker.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=150)
        worker_report = json.loads(report_file.read_text())
        if worker_report.get('run_id') != name:
            raise RuntimeError('Graphics worker report belongs to a different run')
        if worker_report.get('phase') != 'worker_complete':
            raise RuntimeError('Graphics worker did not produce a report; see worker.log')
        report = worker_report
        report.update(container_image_id=image_id, host_kernel=os.uname().release,
                      driver=(device / 'driver').resolve().name,
                      pci_vendor=(device / 'vendor').read_text().strip(), pci_device=(device / 'device').read_text().strip())
        report_file.write_text(json.dumps(report, indent=2) + '\n')
        if result.returncode or report['status'] != 'passed':
            raise RuntimeError(report.get('error', 'Graphics qualification failed'))
        require_complete_evidence(report, appimage_sha256=appimage_sha256,
                                  render_node=node, nvidia_device=nvidia_device)
    except Exception as error:
        report.update(status='failed', runner_error=str(error))
        report_file.write_text(json.dumps(report, indent=2) + '\n')
        raise
    finally:
        was_passed = report.get('status') == 'passed'
        try:
            cleanup = subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=15)
            if cleanup.returncode:
                raise RuntimeError('Private graphics container removal failed')
        except Exception as error:
            report.update(status='failed', runner_cleanup_error=str(error))
            report_file.write_text(json.dumps(report, indent=2) + '\n')
            if was_passed:
                raise RuntimeError('Private graphics container cleanup failed') from error
    print(f'Physical graphics passed: {report["application"]["renderer"]}; no software fallback; visible photo and normal quit')


if __name__ == '__main__':
    main()
