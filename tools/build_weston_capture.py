#!/usr/bin/env python3
"""Build only the pinned qualification compositor's corrected GL module."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

from check_weston_capture_source import check


VERSION = '15.0.1'
SOURCE_URL = ('https://gitlab.freedesktop.org/wayland/weston/-/releases/'
              + VERSION + '/downloads/weston-' + VERSION + '.tar.xz')
SOURCE_SHA256 = '551d039bfb0c837ba5a4d027cdb8ee16bded0eedb789821f8025d8a64b791f6d'
ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'packaging/patches/weston-15.0.1-headless-capture-orientation.patch'
OPTIONS = [
    '--buildtype=release', '--prefix=/usr', '--wrap-mode=nodownload', '-Dbackend-default=headless',
    '-Dbackend-drm=false', '-Dbackend-headless=true', '-Dbackend-pipewire=false',
    '-Dbackend-rdp=false', '-Dbackend-vnc=false', '-Dbackend-wayland=false',
    '-Dbackend-x11=false', '-Drenderer-gl=true', '-Drenderer-vulkan=false',
    '-Dxwayland=false', '-Dsystemd=false', '-Dremoting=false', '-Dpipewire=false',
    '-Dshell-desktop=false', '-Dshell-ivi=false', '-Dshell-kiosk=false',
    '-Dshell-lua=false', '-Dcolor-management-lcms=false', '-Dimage-jpeg=false',
    '-Dimage-webp=false', '-Dtools=[]', '-Ddemo-clients=true', '-Dsimple-clients=[]',
    '-Dwcap-decode=false', '-Dtests=false', '-Ddoc=false',
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--module', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    version = subprocess.check_output(['weston', '--version'], text=True).strip()
    if not re.fullmatch(r'weston ' + re.escape(VERSION), version):
        raise RuntimeError('The capture fix requires the installed Weston ' + VERSION)
    if not args.module.is_file():
        raise RuntimeError('The original distro GL renderer must be installed')
    args.work_dir.mkdir(parents=True)
    args.output.mkdir(parents=True)
    archive = args.source_archive or args.work_dir / ('weston-' + VERSION + '.tar.xz')
    if not args.source_archive:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
    if digest(archive) != SOURCE_SHA256:
        raise RuntimeError('Weston source checksum mismatch')
    with tarfile.open(archive) as package:
        package.extractall(args.work_dir, filter='data')
    source = args.work_dir / ('weston-' + VERSION)
    renderer_source = source / 'libweston/renderer-gl/gl-renderer.c'
    baseline_rows = check(renderer_source)
    if all(case['correct_orientation'] for case in baseline_rows):
        raise RuntimeError('Pinned baseline no longer reproduces the headless capture error')
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)], cwd=source, check=True)
    patched_rows = check(renderer_source)
    if not all(case['correct_orientation'] for case in patched_rows):
        raise RuntimeError('Patched SHM capture did not preserve top-to-bottom rows')
    build = args.work_dir / 'build'
    subprocess.run(['meson', 'setup', str(build), str(source), *OPTIONS], check=True)
    target = 'libweston/renderer-gl/gl-renderer.so'
    subprocess.run(['ninja', '-C', str(build), '-j', str(args.jobs), target,
                    'clients/weston-image'], check=True)
    module = args.output / 'gl-renderer.so'
    shutil.copy2(build / target, module)
    subprocess.run(['patchelf', '--remove-rpath', str(module)], check=True)
    client = args.output / 'weston-image'
    shutil.copy2(build / 'clients/weston-image', client)
    subprocess.run(['patchelf', '--remove-rpath', str(client)], check=True)
    package_command = ['rpm', '-qa'] if shutil.which('rpm') else ['pacman', '-Q']
    receipt = {
        'scope': 'qualification_compositor_only; application and screenshot bytes are unchanged',
        'weston_version': VERSION, 'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
        'patch_sha256': digest(PATCH), 'helper_sha256': digest(Path(__file__)),
        'source_regression_helper_sha256': digest(ROOT / 'tools/check_weston_capture_source.py'),
        'original_module': str(args.module), 'original_module_sha256': digest(args.module),
        'patched_module_sha256': digest(module),
        'calibration_client_sha256': digest(client),
        'source_copy_regression': {'baseline': baseline_rows, 'patched': patched_rows},
        'patched_renderer_source_sha256': digest(source / 'libweston/renderer-gl/gl-renderer.c'),
        'meson_options': OPTIONS,
        'build_packages': sorted(subprocess.check_output(package_command, text=True).splitlines()),
    }
    (args.output / 'weston-capture-build.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    main()
