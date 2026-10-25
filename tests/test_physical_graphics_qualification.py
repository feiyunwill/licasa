#!/usr/bin/env python3
"""Check graphics-run isolation and failure evidence without using a GPU."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RENDER_NODE = Path('/dev/dri/renderD199')
SYS_DEVICE = Path('/sys/class/drm/renderD199/device')


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GraphicsExecutionGuardTests(unittest.TestCase):
    def test_worker_refuses_an_ordinary_host_before_touching_home(self):
        with tempfile.TemporaryDirectory(prefix='licasa-graphics-guard-') as temporary:
            environment = dict(os.environ, HOME=temporary)
            environment.pop('LICASA_PRIVATE_GRAPHICS_TEST', None)
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tools/check_appimage_graphics.py'),
                 '--appimage', 'unused', '--sha256', 'unused',
                 '--render-node', str(RENDER_NODE), '--output', temporary + '/output',
                 '--run-id', 'guard-only'],
                env=environment, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('private graphics runner', result.stderr)
            self.assertFalse(list(Path(temporary).iterdir()))

    def test_card_and_master_paths_are_rejected_before_docker(self):
        module = load_tool('verify_appimage_graphics')
        with tempfile.TemporaryDirectory(prefix='licasa-graphics-bad-node-') as temporary:
            for node in ('/dev/dri/card0', '/dev/dri/controlD64', '/dev/input/event0'):
                with self.subTest(node=node):
                    arguments = ['verify_appimage_graphics.py', '--appimage', 'unused',
                                 '--image', 'unused', '--render-node', node,
                                 '--output', temporary + '/output']
                    with patch.object(sys, 'argv', arguments), redirect_stderr(io.StringIO()), \
                            patch.object(module.subprocess, 'run') as run, \
                            patch.object(module.subprocess, 'check_output') as inspect:
                        with self.assertRaises(SystemExit) as stopped:
                            module.main()
                    self.assertEqual(stopped.exception.code, 2)
                    run.assert_not_called()
                    inspect.assert_not_called()
            self.assertFalse(list(Path(temporary).iterdir()))


class GraphicsFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.module = load_tool('verify_appimage_graphics')
        self.temporary = tempfile.TemporaryDirectory(prefix='licasa-graphics-runner-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.appimage = self.directory / 'fixture.AppImage'
        self.appimage.write_bytes(b'qualification fixture; never executed')
        self.output = self.directory / 'results'
        self.output.mkdir()
        self.report_file = self.output / 'graphics-report.json'
        self.report_file.write_text(json.dumps({'status': 'passed', 'run_id': 'previous-run',
                                               'old_success_marker': True}))
        self.commands = []

    def runner_patches(self, worker, inspect_error=None, nvidia_minor=None,
                       cleanup_status=0, cleanup_error=None):
        """Mock only hardware facts and Docker; keep report I/O real."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        original_stat, original_resolve = Path.stat, Path.resolve
        original_read_text, original_access, original_glob = Path.read_text, os.access, Path.glob
        character_nodes = {RENDER_NODE}
        if nvidia_minor is not None:
            character_nodes.update((Path('/dev/nvidia' + nvidia_minor), Path('/dev/nvidiactl'),
                                    Path('/dev/dri/card7')))

        def stat(path, *args, **kwargs):
            if path in character_nodes:
                return SimpleNamespace(st_gid=os.getgid())
            return original_stat(path, *args, **kwargs)

        def resolve(path, *args, **kwargs):
            if path == SYS_DEVICE / 'driver':
                return Path('/drivers/nvidia' if nvidia_minor is not None else '/drivers/i915')
            if path == SYS_DEVICE and nvidia_minor is not None:
                return Path('/sys/devices/0000:01:00.0')
            return original_resolve(path, *args, **kwargs)

        def read_text(path, *args, **kwargs):
            if path == SYS_DEVICE / 'vendor':
                return '0x8086\n'
            if path == SYS_DEVICE / 'device':
                return '0x1234\n'
            if path == Path('/proc/driver/nvidia/gpus/0000:01:00.0/information'):
                return 'Model: Fixture GPU\nDevice Minor: ' + str(nvidia_minor) + '\n'
            return original_read_text(path, *args, **kwargs)

        def glob(path, pattern):
            if path == SYS_DEVICE / 'drm' and pattern == 'card*':
                return iter([SYS_DEVICE / 'drm/card7'])
            return original_glob(path, pattern)

        def run(command, *args, **kwargs):
            self.commands.append(command)
            if command[:2] == ['docker', 'run']:
                return worker(command)
            if command[:3] == ['docker', 'rm', '--force']:
                if cleanup_error:
                    raise cleanup_error
                return subprocess.CompletedProcess(command, cleanup_status)
            self.fail('Unexpected external process: ' + repr(command))

        arguments = ['verify_appimage_graphics.py', '--appimage', str(self.appimage),
                     '--image', 'fixture-image:local', '--render-node', str(RENDER_NODE),
                     '--output', str(self.output)]
        if nvidia_minor is not None:
            arguments.append('--nvidia')
        stack.enter_context(patch.object(sys, 'argv', arguments))
        stack.enter_context(patch.object(Path, 'is_char_device', lambda path: path in character_nodes))
        stack.enter_context(patch.object(Path, 'stat', stat))
        stack.enter_context(patch.object(Path, 'resolve', resolve))
        stack.enter_context(patch.object(Path, 'read_text', read_text))
        stack.enter_context(patch.object(Path, 'glob', glob))
        stack.enter_context(patch.object(os, 'access',
                                        lambda path, mode: True if Path(path) in character_nodes
                                        else original_access(path, mode)))
        stack.enter_context(patch.object(self.module.subprocess, 'run', side_effect=run))
        stack.enter_context(patch.object(self.module.subprocess, 'check_output',
                                        side_effect=inspect_error,
                                        return_value='sha256:fixture-image\n'))

    def current_worker_report(self, command):
        return {
            'status': 'passed', 'phase': 'worker_complete',
            'run_id': command[command.index('--run-id') + 1],
            'appimage_sha256': command[command.index('--sha256') + 1],
            'render_node': str(RENDER_NODE), 'photo_pattern': 'correct_orientation',
            'visible_photo': True, 'normal_quit': True,
            'diagnostic_exit_status': 0, 'graphics_gate_exit_status': 0,
            'diagnostic': {'renderer': 'Fixture hardware renderer', 'software_retry': False},
            'application': {'renderer': 'Fixture hardware renderer', 'software_retry': False},
            'application_gpu_fds': [str(RENDER_NODE)],
            'capture_calibration': {'status': 'passed', 'photo_pattern': 'correct_orientation',
                                    'fixture_absent_after_close': True},
        }

    def successful_worker(self, command):
        self.report_file.write_text(json.dumps(self.current_worker_report(command)))
        return subprocess.CompletedProcess(command, 0)

    def assert_current_failure(self, *, worker_expected=True):
        report = json.loads(self.report_file.read_text())
        self.assertEqual(report['status'], 'failed', report)
        self.assertTrue(report.get('run_id'), report)
        self.assertNotEqual(report['run_id'], 'previous-run')
        self.assertNotIn('old_success_marker', report)
        self.assertTrue(report.get('error') or report.get('runner_error') or report.get('runner_cleanup_error'), report)
        launches = [command for command in self.commands if command[:2] == ['docker', 'run']]
        self.assertEqual(len(launches), int(worker_expected))
        if worker_expected:
            name = launches[0][launches[0].index('--name') + 1]
            self.assertIn(['docker', 'rm', '--force', name], self.commands)
            self.assertEqual(launches[0][launches[0].index('--run-id') + 1], report['run_id'])
        return report

    def test_timeout_replaces_a_stale_pass_with_current_failure(self):
        def timeout(command):
            raise subprocess.TimeoutExpired(command, 150)

        self.runner_patches(timeout)
        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
            self.module.main()
        report = self.assert_current_failure()
        self.assertIn('timed out', report.get('runner_error', report.get('error', '')))

    def test_missing_worker_report_replaces_a_stale_pass(self):
        def no_report(command):
            self.report_file.unlink(missing_ok=True)
            return subprocess.CompletedProcess(command, 0)

        self.runner_patches(no_report)
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            self.module.main()
        self.assert_current_failure()

    def test_worker_setup_failure_cannot_leave_a_passed_report(self):
        self.runner_patches(lambda command: subprocess.CompletedProcess(command, 1))
        with self.assertRaises(RuntimeError):
            self.module.main()
        report = self.assert_current_failure()
        self.assertIn('did not produce a report', report.get('runner_error', report.get('error', '')))

    def test_image_inspection_failure_replaces_a_stale_pass(self):
        failure = subprocess.CalledProcessError(1, ['docker', 'image', 'inspect'])
        self.runner_patches(lambda command: self.fail('Worker launched after image inspection failure'),
                            inspect_error=failure)
        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
            self.module.main()
        self.assert_current_failure(worker_expected=False)

    def test_missing_appimage_replaces_a_stale_pass_before_image_inspection(self):
        self.appimage.unlink()
        self.runner_patches(lambda command: self.fail('Worker launched with a missing AppImage'))
        with self.assertRaises(FileNotFoundError):
            self.module.main()
        self.assert_current_failure(worker_expected=False)
        self.module.subprocess.check_output.assert_not_called()

    def test_a_copied_worker_pass_from_another_run_is_rejected(self):
        def copied_report(command):
            self.report_file.write_text(json.dumps({
                'status': 'passed', 'phase': 'worker_complete', 'run_id': 'copied-from-another-run',
                'application': {'renderer': 'Copied GPU'}}))
            return subprocess.CompletedProcess(command, 0)

        self.runner_patches(copied_report)
        with self.assertRaises(RuntimeError):
            self.module.main()
        report = self.assert_current_failure()
        self.assertNotEqual(report['run_id'], 'copied-from-another-run')
        self.assertIn('different run', report.get('runner_error', report.get('error', '')))

    def test_current_worker_evidence_passes_inside_a_limited_container_command(self):
        self.runner_patches(self.successful_worker)
        with redirect_stdout(io.StringIO()):
            self.module.main()
        report = json.loads(self.report_file.read_text())
        self.assertEqual(report['status'], 'passed')
        command = next(command for command in self.commands if command[:2] == ['docker', 'run'])
        forwarded_devices = [command[index + 1] for index, word in enumerate(command) if word == '--device']
        self.assertEqual(forwarded_devices, [str(RENDER_NODE)])
        self.assertIn('--read-only', command)
        self.assertEqual(command[command.index('--network') + 1], 'none')
        self.assertEqual(command[command.index('--cap-drop') + 1], 'ALL')
        self.assertEqual(command[command.index('--security-opt') + 1], 'no-new-privileges')
        self.assertNotIn('--privileged', command)
        self.assertNotIn('--pid', command)
        self.assertNotIn('/dev/input', ' '.join(command))
        self.assertFalse(any('DISPLAY=' in word or 'DBUS_SESSION_BUS_ADDRESS=' in word for word in command))

    def test_nvidia_device_matches_the_selected_render_node_instead_of_gpu_zero(self):
        self.runner_patches(self.successful_worker, nvidia_minor='7')
        with redirect_stdout(io.StringIO()):
            self.module.main()
        command = next(command for command in self.commands if command[:2] == ['docker', 'run'])
        forwarded_devices = [command[index + 1] for index, word in enumerate(command) if word == '--device']
        self.assertEqual(set(forwarded_devices), {
            str(RENDER_NODE), '/dev/nvidia7', '/dev/nvidiactl', '/dev/dri/card7:/dev/dri/card7:m'})
        self.assertEqual(len(forwarded_devices), 4)
        self.assertEqual(command[command.index('--nvidia-device') + 1], '/dev/nvidia7')
        self.assertEqual(command[command.index('--primary-metadata') + 1], '/dev/dri/card7')
        self.assertNotIn('/dev/nvidia0', command)

    def test_worker_pass_cannot_override_an_inverted_capture(self):
        def inverted_report(command):
            report = self.current_worker_report(command)
            report['photo_pattern'] = 'vertically_inverted'
            self.report_file.write_text(json.dumps(report))
            return subprocess.CompletedProcess(command, 0)

        self.runner_patches(inverted_report)
        with self.assertRaisesRegex(RuntimeError, 'contradictory qualification evidence'):
            self.module.main()
        report = self.assert_current_failure()
        self.assertEqual(report['photo_pattern'], 'vertically_inverted')

    def test_missing_acceptance_evidence_cannot_pass(self):
        command = ['--run-id', 'evidence-fixture', '--sha256', 'fixture-digest']
        complete = self.current_worker_report(command)
        for field in ('photo_pattern', 'visible_photo', 'normal_quit', 'diagnostic_exit_status',
                      'graphics_gate_exit_status', 'application_gpu_fds', 'capture_calibration',
                      'diagnostic', 'application', 'appimage_sha256', 'render_node'):
            with self.subTest(field=field):
                report = dict(complete)
                del report[field]
                with self.assertRaisesRegex(RuntimeError, 'qualification evidence'):
                    self.module.require_complete_evidence(report, appimage_sha256='fixture-digest',
                                                          render_node=RENDER_NODE)
        for field, value in (('status', 'failed'), ('photo_pattern', 'vertically_inverted'),
                             ('fixture_absent_after_close', False)):
            with self.subTest(calibration_field=field):
                report = dict(complete, capture_calibration=dict(complete['capture_calibration']))
                report['capture_calibration'][field] = value
                with self.assertRaisesRegex(RuntimeError, 'qualification evidence'):
                    self.module.require_complete_evidence(report, appimage_sha256='fixture-digest',
                                                          render_node=RENDER_NODE)

    def test_failed_container_removal_cannot_leave_a_passed_report(self):
        self.runner_patches(self.successful_worker, cleanup_status=1)
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaisesRegex(RuntimeError, 'container cleanup failed'):
            self.module.main()
        report = self.assert_current_failure()
        self.assertIn('removal failed', report['runner_cleanup_error'])
        self.assertEqual(stdout.getvalue(), '')

    def test_container_removal_timeout_cannot_leave_a_passed_report(self):
        self.runner_patches(self.successful_worker,
                            cleanup_error=subprocess.TimeoutExpired(['docker', 'rm'], 15))
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'container cleanup failed'):
            self.module.main()
        report = self.assert_current_failure()
        self.assertIn('timed out', report['runner_cleanup_error'])


class GraphicsWorkerFailureEvidenceTests(unittest.TestCase):
    def test_inverted_photo_is_distinguished_from_absent_pixels_without_altering_capture(self):
        from PIL import Image, ImageDraw

        with patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
            module = load_tool('check_appimage_graphics')
        photo = Image.new('RGB', (480, 360))
        draw = ImageDraw.Draw(photo)
        for index, color in enumerate(module.PHOTO_COLORS):
            x, y = index % 2 * 240, index // 2 * 180
            draw.rectangle((x, y, x + 239, y + 179), fill=color)
        inverted = photo.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        raw_pixels = inverted.tobytes()
        self.assertEqual(module.photo_pattern(photo), 'correct_orientation')
        self.assertEqual(module.photo_pattern(inverted), 'vertically_inverted')
        self.assertEqual(inverted.tobytes(), raw_pixels)
        self.assertEqual(module.photo_pattern(Image.new('RGB', photo.size, 'black')), 'absent')

    def test_hash_mismatch_cannot_attach_previous_run_logs_or_photo(self):
        with patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
            module = load_tool('check_appimage_graphics')
        with tempfile.TemporaryDirectory(prefix='licasa-graphics-stale-worker-') as temporary:
            directory = Path(temporary)
            appimage, output, work = directory / 'fixture.AppImage', directory / 'results', directory / 'work'
            appimage.write_bytes(b'qualification fixture; never executed')
            output.mkdir()
            old_driver = ('OpenGL VENDOR: Old RENDERER: Previous run GPU VERSION: 4.6\n'
                          'Qt Quick backend (sceneGraphInitialized): graphicsApi=OpenGL\n')
            for name in ('diagnostic.log', 'application.log'):
                (output / name).write_text(old_driver)
            (output / 'photo.png').write_bytes(b'previous screenshot')

            def path(value):
                return work if str(value) == '/tmp/licasa-graphics-test' else Path(value)

            arguments = ['check_appimage_graphics.py', '--appimage', str(appimage),
                         '--sha256', 'invalid-digest', '--render-node', str(RENDER_NODE),
                         '--output', str(output), '--run-id', 'hash-mismatch-fixture']
            with patch.object(sys, 'argv', arguments), \
                    patch.object(module, 'Path', side_effect=path), \
                    patch.object(module, 'require_private_graphics_container'), \
                    patch.object(module, 'live_pids', return_value=[]), \
                    patch.object(module.subprocess, 'run') as run, \
                    patch.object(module.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(RuntimeError, 'AppImage hash mismatch'):
                    module.main()
                run.assert_not_called()
                popen.assert_not_called()
            report = json.loads((output / 'graphics-report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertNotIn('Previous run GPU', json.dumps(report))
            self.assertNotIn('graphics_gate_exit_status', report)
            for name in ('diagnostic.log', 'application.log', 'photo.png'):
                self.assertFalse((output / name).exists(), name)

    def exercise_worker(self, *, calibration='upright', application='upright',
                        calibration_closed='absent', software=False, calibration_client=True):
        """Keep real capture/report files; replace only processes and hardware facts."""
        from PIL import Image, ImageDraw
        import hashlib

        with patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
            module = load_tool('check_appimage_graphics')
        temporary = tempfile.TemporaryDirectory(prefix='licasa-graphics-worker-fixture-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        appimage = directory / 'fixture.AppImage'
        appimage.write_bytes(b'qualification fixture; never executed')
        output, work = directory / 'results', directory / 'work'
        renderer = 'llvmpipe' if software else 'Fixture hardware renderer'
        driver_log = ('qt.rhi.general: OpenGL VENDOR: Fixture RENDERER: ' + renderer
                      + ' VERSION: 4.6\n[Licasa] Qt Quick backend (sceneGraphInitialized): '
                      'graphicsApi=OpenGL, sceneGraphBackend=<default>\n')
        photo = Image.new('RGB', (480, 360))
        draw = ImageDraw.Draw(photo)
        for index, color in enumerate(module.PHOTO_COLORS):
            x, y = index % 2 * 240, index // 2 * 180
            draw.rectangle((x, y, x + 239, y + 179), fill=color)
        images = {'upright': photo, 'inverted': photo.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
                  'absent': Image.new('RGB', photo.size, 'black')}
        state = {'time': 0, 'calibration': False, 'calibration_started': False, 'application': False}
        commands = []
        original_glob = Path.glob

        def path(value):
            return work if str(value) == '/tmp/licasa-graphics-test' else Path(value)

        def glob(path, pattern):
            if path == Path('/proc/9999999/fd'):
                return iter([path / '9'])
            return original_glob(path, pattern)

        def popen(command, **kwargs):
            commands.append(command)
            if command[0] == 'weston':
                kind = 'compositor'
            elif command[0] == '/fixture/weston-image':
                kind = 'calibration'
                state.update(calibration=True, calibration_started=True)
            else:
                kind = 'application'
                state['application'] = True
                kwargs['stdout'].write(driver_log)
                kwargs['stdout'].flush()
            return SimpleNamespace(pid=9999999, kind=kind, poll=lambda: None)

        def stop(process):
            if process.kind == 'calibration':
                state['calibration'] = False

        def sleep(seconds):
            state['time'] += max(seconds, 5)

        def run(command, **kwargs):
            commands.append(command)
            if command[0] == 'weston-screenshooter':
                if state['application']:
                    image = images[application]
                elif state['calibration']:
                    image = images[calibration]
                elif state['calibration_started']:
                    image = images[calibration_closed]
                else:
                    image = images['absent']
                image.save(Path(kwargs['cwd']) / 'wayland-screenshot-fixture.png')
            elif command[0].endswith('/licasa_diagnostics'):
                kwargs['stdout'].write('{}\n')
                kwargs['stderr'].write(driver_log)
            elif command[0] == 'sh':
                return subprocess.CompletedProcess(command, int(software), '', '')
            elif command[1:] == ['--quit']:
                state['application'] = False
            elif command[1:] != ['--appimage-extract']:
                self.fail('Unexpected external command: ' + repr(command))
            return subprocess.CompletedProcess(command, 0, '', '')

        arguments = ['check_appimage_graphics.py', '--appimage', str(appimage),
                     '--sha256', hashlib.sha256(appimage.read_bytes()).hexdigest(),
                     '--render-node', str(RENDER_NODE), '--output', str(output),
                     '--run-id', 'worker-capture-fixture']
        failure = None
        with patch.object(sys, 'argv', arguments), \
                patch.object(module, 'Path', side_effect=path), \
                patch.object(module, 'require_private_graphics_container'), \
                patch.object(module, 'live_pids', side_effect=lambda executable: [9999999] if state['application'] else []), \
                patch.object(module, 'stop', side_effect=stop), patch.object(module.os, 'kill'), \
                patch.object(module.os, 'readlink', return_value=str(RENDER_NODE)), \
                patch.object(Path, 'glob', glob), patch.object(Path, 'is_socket', return_value=True), \
                patch.object(module.shutil, 'which', return_value='/fixture/weston-image' if calibration_client else None), \
                patch.object(module.time, 'sleep', side_effect=sleep), \
                patch.object(module.time, 'monotonic', side_effect=lambda: state['time']), \
                patch.object(module.subprocess, 'Popen', side_effect=popen), \
                patch.object(module.subprocess, 'run', side_effect=run):
            try:
                module.main()
            except RuntimeError as error:
                failure = str(error)
        report = json.loads((output / 'graphics-report.json').read_text())
        return SimpleNamespace(report=report, output=output, failure=failure, commands=commands, images=images)

    def assert_raw_capture(self, fixture, filename, orientation):
        from PIL import Image

        with Image.open(fixture.output / filename) as image:
            self.assertEqual(image.convert('RGB').tobytes(), fixture.images[orientation].tobytes())

    def test_upright_independent_capture_and_application_pass(self):
        fixture = self.exercise_worker()
        self.assertIsNone(fixture.failure)
        self.assertEqual(fixture.report['status'], 'passed')
        self.assertEqual(fixture.report['photo_pattern'], 'correct_orientation')
        self.assertEqual(fixture.report['capture_calibration']['status'], 'passed')
        self.assertTrue(fixture.report['capture_calibration']['fixture_absent_after_close'])
        self.assertTrue(fixture.report['normal_quit'])
        self.assert_raw_capture(fixture, 'calibration.png', 'upright')
        self.assert_raw_capture(fixture, 'calibration-closed.png', 'absent')
        self.assert_raw_capture(fixture, 'photo.png', 'upright')

    def test_inverted_independent_capture_stops_before_any_licasa_launch(self):
        fixture = self.exercise_worker(calibration='inverted')
        self.assertIn('capture calibration failed', fixture.failure)
        self.assertEqual(fixture.report['status'], 'failed')
        self.assertEqual(fixture.report['capture_calibration']['status'], 'failed')
        self.assertEqual(fixture.report['capture_calibration']['photo_pattern'], 'vertically_inverted')
        self.assertNotIn('visible_photo', fixture.report)
        self.assertFalse((fixture.output / 'diagnostic.log').exists())
        self.assertFalse((fixture.output / 'application.log').exists())
        self.assert_raw_capture(fixture, 'calibration.png', 'inverted')

    def test_inverted_application_still_fails_after_upright_calibration(self):
        fixture = self.exercise_worker(application='inverted')
        self.assertIn('orientation qualification failed', fixture.failure)
        self.assertEqual(fixture.report['status'], 'failed')
        self.assertEqual(fixture.report['capture_calibration']['status'], 'passed')
        self.assertEqual(fixture.report['photo_pattern'], 'vertically_inverted')
        self.assertEqual(fixture.report['application']['renderer'], 'Fixture hardware renderer')
        self.assertEqual(fixture.report['graphics_gate_exit_status'], 0)
        self.assertIn(str(RENDER_NODE), fixture.report['application_gpu_fds'])
        self.assertNotIn('normal_quit', fixture.report)
        self.assert_raw_capture(fixture, 'photo.png', 'inverted')

    def test_calibration_pixels_must_disappear_before_licasa_launch(self):
        fixture = self.exercise_worker(calibration_closed='upright')
        self.assertIn('remained visible', fixture.failure)
        self.assertEqual(fixture.report['status'], 'failed')
        self.assertEqual(fixture.report['capture_calibration']['status'], 'failed')
        self.assertFalse((fixture.output / 'application.log').exists())
        self.assert_raw_capture(fixture, 'calibration-closed.png', 'upright')

    def test_missing_independent_calibration_client_cannot_pass(self):
        fixture = self.exercise_worker(calibration_client=False)
        self.assertIn('requires weston-image', fixture.failure)
        self.assertEqual(fixture.report['capture_calibration']['status'], 'failed')
        self.assertFalse((fixture.output / 'application.log').exists())

    def test_invisible_photo_still_retains_the_software_driver_failure(self):
        fixture = self.exercise_worker(application='absent', software=True)
        self.assertIn('Photo did not remain visible', fixture.failure)
        self.assertEqual(fixture.report['status'], 'failed')
        self.assertEqual(fixture.report['phase'], 'worker_complete')
        self.assertEqual(fixture.report['diagnostic']['renderer'], 'llvmpipe')
        self.assertEqual(fixture.report['application']['renderer'], 'llvmpipe')
        self.assertEqual(fixture.report['graphics_gate_exit_status'], 1)
        self.assertNotEqual(fixture.report.get('visible_photo'), True)


class PrimaryMetadataDeviceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
            cls.module = load_tool('check_appimage_graphics')

    def test_cgroup_denial_is_required_for_every_open_mode(self):
        denied = PermissionError(errno.EPERM, 'fixture cgroup denial')
        with patch.object(self.module.os, 'open', side_effect=denied) as open_device:
            self.module.require_primary_metadata_only(Path('/dev/dri/card7'))
        self.assertEqual(open_device.call_count, 3)
        self.assertCountEqual([call.args[1] & os.O_ACCMODE for call in open_device.call_args_list],
                              [os.O_RDONLY, os.O_WRONLY, os.O_RDWR])

    def test_any_accessible_open_mode_is_closed_and_rejected(self):
        for accessible_mode in (os.O_RDONLY, os.O_WRONLY, os.O_RDWR):
            with self.subTest(accessible_mode=accessible_mode):
                def open_device(path, flags):
                    if flags & os.O_ACCMODE == accessible_mode:
                        return 9999999
                    raise PermissionError(errno.EPERM, 'fixture cgroup denial')

                with patch.object(self.module.os, 'open', side_effect=open_device), \
                        patch.object(self.module.os, 'close') as close_device:
                    with self.assertRaises(RuntimeError):
                        self.module.require_primary_metadata_only(Path('/dev/dri/card7'))
                    close_device.assert_called_once_with(9999999)

    def test_unix_permissions_cannot_stand_in_for_cgroup_isolation(self):
        denied = PermissionError(errno.EACCES, 'fixture DAC denial')
        with patch.object(self.module.os, 'open', side_effect=denied), \
                patch.object(self.module.os, 'close') as close_device:
            with self.assertRaises(RuntimeError):
                self.module.require_primary_metadata_only(Path('/dev/dri/card7'))
            close_device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
