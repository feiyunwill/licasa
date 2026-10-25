#!/usr/bin/env python3
"""Check the AppImage's real graphics path in a private, guarded container."""

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from verify_snap_desktop import PHOTO_COLORS, pattern_present, stop


def require_primary_metadata_only(path):
    for mode in (os.O_RDONLY, os.O_WRONLY, os.O_RDWR):
        try:
            descriptor = os.open(path, mode | os.O_CLOEXEC)
        except OSError as error:
            if error.errno != errno.EPERM:
                raise RuntimeError('Primary DRM metadata must have device-cgroup I/O denial (EPERM)') from error
        else:
            os.close(descriptor)
            raise RuntimeError('Primary DRM node permits I/O; refusing to start graphics processes')


def require_private_graphics_container(render_node, nvidia_device=None, primary_metadata=None):
    capabilities = re.search(r'^CapEff:\s*(\w+)', Path('/proc/self/status').read_text(), re.M)
    nodes = sorted(Path('/dev/dri').glob('*')) if Path('/dev/dri').exists() else []
    vendor_nodes = {str(path) for path in Path('/dev').glob('nvidia*')}
    expected_vendor_nodes = {str(nvidia_device), '/dev/nvidiactl'} if nvidia_device else set()
    expected_drm_nodes = sorted([render_node] + ([primary_metadata] if primary_metadata else []))
    if (os.environ.get('LICASA_PRIVATE_GRAPHICS_TEST') != '1' or not Path('/.dockerenv').is_file()
            or os.getuid() == 0 or not os.statvfs('/').f_flag & os.ST_RDONLY
            or not capabilities or int(capabilities.group(1), 16)
            or Path('/dev/input').exists() or nodes != expected_drm_nodes
            or vendor_nodes != expected_vendor_nodes
            or (nvidia_device is not None and not re.fullmatch(r'/dev/nvidia[0-9]+', str(nvidia_device)))
            or (primary_metadata is not None and (not nvidia_device
                or not re.fullmatch(r'/dev/dri/card[0-9]+', str(primary_metadata)) or not primary_metadata.is_char_device()))
            or not re.fullmatch(r'/dev/dri/renderD[0-9]+', str(render_node))
            or not render_node.is_char_device() or not os.access(render_node, os.R_OK | os.W_OK)
            or any(os.environ.get(name) for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'))):
        raise RuntimeError('Use the private graphics runner: non-root, read-only container, one render node, no host display/input.')
    if primary_metadata:
        require_primary_metadata_only(primary_metadata)


def live_pids(executable):
    result = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if proc.stat().st_uid == os.getuid() and (proc / 'exe').resolve() == executable:
                result.append(int(proc.name))
        except (OSError, RuntimeError):
            continue
    return result


def graphics_fields(log):
    apis = re.findall(r'Qt Quick backend \(sceneGraphInitialized\): graphicsApi=(\w+)', log)
    renderers = re.findall(r'OpenGL VENDOR: .* RENDERER: (.*?) VERSION: ', log)
    return {'api': apis[-1] if apis else 'unreported',
            'renderer': renderers[-1] if renderers else 'unreported',
            'software_retry': 'retrying once with the Qt software renderer' in log}


def photo_pattern(image):
    from PIL import Image

    if pattern_present(image):
        return 'correct_orientation'
    if pattern_present(image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)):
        return 'vertically_inverted'
    return 'absent'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appimage', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--render-node', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--nvidia-device', type=Path)
    parser.add_argument('--primary-metadata', type=Path)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    require_private_graphics_container(args.render_node, args.nvidia_device, args.primary_metadata)
    from PIL import Image, ImageDraw

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    for name in ('diagnostic.json', 'diagnostic.log', 'application.log', 'compositor.log',
                 'extraction.log', 'graphics-gate.log', 'calibration.log', 'before.png',
                 'calibration.png', 'calibration-closed.png', 'photo.png'):
        (output / name).unlink(missing_ok=True)
    report = {'status': 'failed', 'run_id': args.run_id, 'appimage_sha256': args.sha256, 'render_node': str(args.render_node),
              'environment': 'isolated_distribution_userspace_on_host_kernel_with_physical_render_node',
              'host_display_access': False, 'forced_qt_platform': False, 'forced_qt_backend': False,
              'capture_calibration': {'status': 'not_run', 'client': 'weston-image'}}
    if args.primary_metadata:
        report['primary_node_metadata_only'] = {'path': str(args.primary_metadata),
                                              'read_write_open_denied': 'EPERM in all three modes'}
    work = Path('/tmp/licasa-graphics-test')
    work.mkdir()
    env = dict(os.environ)
    for name in ('QT_QPA_PLATFORM', 'QT_QUICK_BACKEND', 'QSG_RHI_BACKEND', 'QSG_RHI_PREFER_SOFTWARE_RENDERER',
                 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORMTHEME', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH',
                 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LIBGL_ALWAYS_SOFTWARE', 'MESA_LOADER_DRIVER_OVERRIDE'):
        env.pop(name, None)
    home, runtime = work / 'home', work / 'runtime'
    home.mkdir()
    runtime.mkdir(mode=0o700)
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(runtime), XDG_SESSION_TYPE='wayland',
               XDG_CONFIG_HOME=str(home / 'config'), XDG_CACHE_HOME=str(home / 'cache'),
               XDG_DATA_HOME=str(home / 'data'), XDG_STATE_HOME=str(home / 'state'),
               WAYLAND_DISPLAY='wayland-licasa-graphics', QSG_INFO='1',
               QT_LOGGING_RULES='qt.rhi.general.debug=true')
    processes, logs = [], []
    executable = work / 'squashfs-root/usr/bin/licasa'

    def launch(command, name):
        log = (output / name).open('w')
        logs.append(log)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        return process

    def capture(name):
        shots = work / 'shots'
        shots.mkdir(exist_ok=True)
        for path in shots.glob('wayland-screenshot-*.png'):
            path.unlink()
        completed = subprocess.run(['weston-screenshooter'], env=env, cwd=shots,
                                   capture_output=True, text=True, timeout=10)
        if completed.returncode:
            raise RuntimeError('Private compositor capture failed: ' + completed.stderr)
        files = list(shots.glob('wayland-screenshot-*.png'))
        if len(files) != 1:
            raise RuntimeError('Private compositor did not produce exactly one screenshot')
        shutil.copy2(files[0], output / (name + '.png'))
        with Image.open(files[0]) as image:
            return image.convert('RGB').copy()

    def record_graphics():
        gpu_fds = set()
        for pid in live_pids(executable):
            for fd in (Path('/proc') / str(pid) / 'fd').glob('*'):
                try:
                    target = os.readlink(fd)
                    if target.startswith(('/dev/dri/', '/dev/nvidia')):
                        gpu_fds.add(target)
                except OSError:
                    continue
        report['application_gpu_fds'] = sorted(gpu_fds)
        for kind in ('diagnostic', 'application'):
            path = output / (kind + '.log')
            if path.is_file():
                report[kind] = graphics_fields(path.read_text(errors='replace'))
        if all((output / (kind + '.log')).is_file() for kind in ('diagnostic', 'application')):
            checked = subprocess.run(['sh', '/source/tools/check_display_session.sh', '--check-graphics-results',
                                      str(output / 'diagnostic.log'), str(output / 'application.log'),
                                      'false', 'require-accelerated'], env=env, capture_output=True, text=True)
            (output / 'graphics-gate.log').write_text(checked.stdout + checked.stderr)
            report['graphics_gate_exit_status'] = checked.returncode
        return gpu_fds

    try:
        if hashlib.sha256(args.appimage.read_bytes()).hexdigest() != args.sha256:
            raise RuntimeError('AppImage hash mismatch')
        image_path = work / 'Licasa.AppImage'
        shutil.copy2(args.appimage, image_path)
        image_path.chmod(0o755)
        with (output / 'extraction.log').open('w') as log:
            subprocess.run([str(image_path), '--appimage-extract'], cwd=work, env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=45, check=True)
        picture = work / 'photo with spaces 日本語.png'
        image = Image.new('RGB', (480, 360))
        draw = ImageDraw.Draw(image)
        for index, color in enumerate(PHOTO_COLORS):
            x, y = index % 2 * 240, index // 2 * 180
            draw.rectangle((x, y, x + 239, y + 179), fill=color)
        image.save(picture)
        # Only this private compositor exposes capture, on its private 0700
        # runtime socket. It has no host display, input or modesetting device.
        compositor = launch(['weston', '--backend=headless', '--renderer=gl', '--fake-seat', '--width=1280', '--height=800',
                             '--no-config', '--socket=' + env['WAYLAND_DISPLAY'], '--idle-time=0',
                             '--debug', '--log=/dev/stderr'], 'compositor.log')
        deadline = time.monotonic() + 15
        while not (runtime / env['WAYLAND_DISPLAY']).is_socket() and compositor.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if not (runtime / env['WAYLAND_DISPLAY']).is_socket():
            raise RuntimeError('Private compositor did not start')
        if photo_pattern(capture('before')) != 'absent':
            raise RuntimeError('Photo fixture was visible before launch')
        calibration_report = report['capture_calibration']
        calibration_report.update(status='failed', screenshot='calibration.png')
        calibration_client = shutil.which('weston-image')
        if not calibration_client:
            raise RuntimeError('Independent capture calibration requires weston-image')
        calibration_report['executable'] = calibration_client
        calibration = launch([calibration_client, str(picture)], 'calibration.log')
        deadline = time.monotonic() + 15
        while (time.monotonic() < deadline and compositor.poll() is None
               and calibration.poll() is None):
            calibration_report['photo_pattern'] = photo_pattern(capture('calibration'))
            if calibration_report['photo_pattern'] == 'correct_orientation':
                break
            if calibration_report['photo_pattern'] == 'vertically_inverted':
                raise RuntimeError('Independent compositor capture is vertically inverted; capture calibration failed')
            time.sleep(0.2)
        else:
            raise RuntimeError('Independent capture calibration photo did not become visible')
        stop(calibration)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and compositor.poll() is None:
            if photo_pattern(capture('calibration-closed')) == 'absent':
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Independent calibration photo remained visible after its client stopped')
        calibration_report.update(status='passed', fixture_absent_after_close=True)
        appdir = work / 'squashfs-root'
        diagnostic_env = dict(env, QT_PLUGIN_PATH=f'{appdir}/usr/plugins:{appdir}/usr/lib/licasa/plugins',
                              QML_IMPORT_PATH=f'{appdir}/usr/qml', QML2_IMPORT_PATH=f'{appdir}/usr/qml')
        with (output / 'diagnostic.json').open('w') as stdout, (output / 'diagnostic.log').open('w') as stderr:
            diagnostic = subprocess.run([str(appdir / 'usr/bin/licasa_diagnostics'), '--viewer', str(picture)],
                                        env=diagnostic_env, stdout=stdout, stderr=stderr, timeout=45)
        report['diagnostic_exit_status'] = diagnostic.returncode
        application = launch([str(appdir / 'AppRun'), str(picture)], 'application.log')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and compositor.poll() is None:
            # Keep the raw PNG and the failed orientation gate intact. A
            # captured but inverted photo is distinct from missing pixels.
            report['photo_pattern'] = photo_pattern(capture('photo'))
            if live_pids(executable) and report['photo_pattern'] == 'correct_orientation':
                time.sleep(1)
                if pattern_present(capture('photo')):
                    break
            time.sleep(0.2)
        else:
            if report.get('photo_pattern') == 'vertically_inverted':
                raise RuntimeError('Photo is visible in a vertically inverted capture; orientation qualification failed')
            raise RuntimeError('Photo did not remain visible')
        report['visible_photo'] = True
        gpu_fds = record_graphics()
        physical_fd = str(args.render_node) in gpu_fds or (args.nvidia_device and str(args.nvidia_device) in gpu_fds)
        if diagnostic.returncode or report.get('graphics_gate_exit_status') != 0 or not physical_fd:
            raise RuntimeError('Acceleration gate failed; see graphics-gate.log and the retained process logs')
        subprocess.run([str(appdir / 'AppRun'), '--quit'], env=env, capture_output=True, timeout=10, check=True)
        deadline = time.monotonic() + 5
        while live_pids(executable) and time.monotonic() < deadline:
            time.sleep(0.1)
        if live_pids(executable):
            raise RuntimeError('Normal quit did not stop the viewer')
        report.update(status='passed', normal_quit=True)
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        cleanup_errors = []
        if 'application' not in report:
            try:
                record_graphics()
            except Exception as error:
                report['graphics_collection_error'] = str(error)
        for pid in live_pids(executable):
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        for process in reversed(processes):
            try:
                stop(process)
            except Exception as error:
                cleanup_errors.append(str(error))
        for log in logs:
            log.close()
        if cleanup_errors:
            report.update(status='failed', cleanup_errors=cleanup_errors)
        report['phase'] = 'worker_complete'
        (output / 'graphics-report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
