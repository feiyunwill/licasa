# Patch only the qualification compositor; never ship it in the application.
ARG BASE_IMAGE=licasa-appimage-arch-verify:local
FROM ${BASE_IMAGE} AS capture-builder
USER 0:0
RUN pacman -Syu --noconfirm --needed gcc meson ninja pkgconf patch patchelf wayland wayland-protocols \
    pixman libxkbcommon libinput libevdev libdrm libdisplay-info libglvnd mesa cairo libpng \
    pango systemd seatd && pacman -Scc --noconfirm
COPY tools/build_weston_capture.py /recipe/tools/
COPY tools/check_weston_capture_source.py /recipe/tools/
COPY packaging/patches/weston-15.0.1-headless-capture-orientation.patch /recipe/packaging/patches/
RUN python3 /recipe/tools/build_weston_capture.py --work-dir /work --output /capture \
    --module /usr/lib/libweston-15/gl-renderer.so

# Add graphics test tools only after the minimal-runtime installation gates.
FROM ${BASE_IMAGE} AS capture-control
USER 0:0
RUN python3 -c 'from PIL import Image' || \
    (pacman -Syu --noconfirm --needed python python-pillow && pacman -Scc --noconfirm)
RUN pacman -S --noconfirm --needed ttf-dejavu && fc-cache -f && pacman -Scc --noconfirm
COPY --from=capture-builder /capture/weston-image /usr/local/bin/weston-image
RUN ldd /usr/local/bin/weston-image | tee /tmp/client-linkage && ! grep -q 'not found' /tmp/client-linkage

FROM capture-control
COPY --from=capture-builder /capture/gl-renderer.so /usr/lib/libweston-15/gl-renderer.so
COPY --from=capture-builder /capture/weston-capture-build.json /usr/share/licasa-qualification/
RUN ldd /usr/lib/libweston-15/gl-renderer.so | tee /tmp/capture-linkage && ! grep -q 'not found' /tmp/capture-linkage
