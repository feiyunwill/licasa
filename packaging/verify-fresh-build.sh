#!/usr/bin/env bash
set -euo pipefail

mkdir -p /work/licasa
tar -C /source --exclude='./build*' --exclude='./cmake-build-*' \
  --exclude='./.git' --exclude='./.idea' \
  --exclude='./private-validation' --exclude='./CMakeUserPresets.json' \
  -cf - . | tar -C /work/licasa -xf -
cd /work/licasa
dependency_args=()
# Only the hosted release workflow sets this after an exact, image-keyed hit.
# Application compilation, CTest and packaging always run from fresh sources.
if [ "${LICASA_REUSE_DEPENDENCIES:-false}" = true ]; then
  dependency_args+=(--skip-dependencies)
fi
python3 tools/build_ubuntu_deb.py --jobs "${LICASA_MAX_JOBS:-3}" \
  "${dependency_args[@]}" --output-dir /output
version="$(sed -nE '/^[[:space:]]*VERSION [0-9]+\.[0-9]+\.[0-9]+/s/^[[:space:]]*VERSION ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' CMakeLists.txt)"
python3 tools/package_appimage.py "/output/licasa_${version}-1_amd64.deb" --output-dir /output
install -m 0755 packaging/install-licasa-appimage.sh /output/install-licasa-appimage.sh
(cd /output && sha256sum install-licasa-appimage.sh > install-licasa-appimage.sh.sha256)
chown --reference=/source /output/licasa_*.deb /output/licasa_*.deb.sha256 \
  /output/Licasa-*.AppImage /output/Licasa-*.AppImage.sha256 \
  /output/install-licasa-appimage.sh /output/install-licasa-appimage.sh.sha256
