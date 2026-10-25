# Keep the already-matched driver userspace. Only the private test compositor
# and its independent calibration client come from the distro-matched builder.
ARG CAPTURE_IMAGE
ARG BASE_IMAGE
FROM ${CAPTURE_IMAGE} AS capture
FROM ${BASE_IMAGE} AS capture-control
USER 0:0
COPY --from=capture /usr/local/bin/weston-image /usr/local/bin/weston-image
# Keep the ordinary desktop fonts used by the distro's calibration environment.
# The matching NVIDIA userspace itself remains the original BASE_IMAGE.
COPY --from=capture /usr/share/fonts/ /usr/share/fonts/
COPY --from=capture /usr/share/licenses/ /usr/share/licenses/
RUN fc-cache -f
RUN ldd /usr/local/bin/weston-image | tee /tmp/client-linkage && ! grep -q 'not found' /tmp/client-linkage

FROM capture-control
ARG MODULE_LIBDIR=/usr/lib64
COPY --from=capture /usr/lib*/libweston-15/gl-renderer.so ${MODULE_LIBDIR}/libweston-15/gl-renderer.so
COPY --from=capture /usr/share/licasa-qualification/weston-capture-build.json /usr/share/licasa-qualification/
RUN ldd ${MODULE_LIBDIR}/libweston-15/gl-renderer.so | tee /tmp/capture-linkage && ! grep -q 'not found' /tmp/capture-linkage
