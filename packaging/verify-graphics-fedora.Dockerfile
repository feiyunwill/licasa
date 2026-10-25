# Patch only the qualification compositor; never ship it in the application.
ARG BASE_IMAGE=licasa-appimage-fedora-verify:local
FROM ${BASE_IMAGE} AS capture-builder
USER 0:0
RUN dnf install -y --setopt=install_weak_deps=False gcc gcc-c++ meson ninja-build patch patchelf \
    wayland-devel wayland-protocols-devel pixman-devel libxkbcommon-devel libinput-devel \
    libevdev-devel libdrm-devel libdisplay-info-devel mesa-libEGL-devel mesa-libGLES-devel \
    mesa-libgbm-devel cairo-devel pango-devel libpng-devel systemd-devel libseat-devel && dnf clean all
COPY tools/build_weston_capture.py /recipe/tools/
COPY tools/check_weston_capture_source.py /recipe/tools/
COPY packaging/patches/weston-15.0.1-headless-capture-orientation.patch /recipe/packaging/patches/
RUN python3 /recipe/tools/build_weston_capture.py --work-dir /work --output /capture \
    --module /usr/lib64/libweston-15/gl-renderer.so

# Add graphics test tools only after the minimal-runtime installation gates.
FROM ${BASE_IMAGE} AS capture-control
USER 0:0
RUN python3 -c 'from PIL import Image' || \
    (dnf install -y --setopt=install_weak_deps=False python3-pillow && dnf clean all)
COPY --from=capture-builder /capture/weston-image /usr/local/bin/weston-image
RUN ldd /usr/local/bin/weston-image | tee /tmp/client-linkage && ! grep -q 'not found' /tmp/client-linkage

FROM capture-control
COPY --from=capture-builder /capture/gl-renderer.so /usr/lib64/libweston-15/gl-renderer.so
COPY --from=capture-builder /capture/weston-capture-build.json /usr/share/licasa-qualification/
RUN ldd /usr/lib64/libweston-15/gl-renderer.so | tee /tmp/capture-linkage && ! grep -q 'not found' /tmp/capture-linkage
