FROM debian:trixie-slim

# A real MATE session and AT-SPI bus, with no system Qt/QML installation that
# could accidentally repair the portable package's runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 python3-pyatspi at-spi2-core dbus-x11 \
    mate-session-manager marco mate-settings-daemon mate-panel caja orca \
    xvfb xauth xdotool x11-utils fonts-dejavu-core libgomp1 \
    libgl1 libegl1 libopengl0 libglx-mesa0 libfontconfig1 libxkbcommon0 libx11-xcb1 \
    libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 \
    libxcb-render-util0 libxcb-randr0 libxcb-xinerama0 \
    && rm -rf /var/lib/apt/lists/*
