<p align="center">
<img src="assets/licasa.png" width="512" alt="Licasa logo"/>
</p>

[![Build and verify](https://github.com/sanxchep/licasa/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sanxchep/licasa/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/sanxchep/licasa?label=release)](https://github.com/sanxchep/licasa/releases)
[![Commit activity](https://img.shields.io/github/commit-activity/m/sanxchep/licasa?label=commit%20activity)](https://github.com/sanxchep/licasa/graphs/commit-activity)
[![License](https://img.shields.io/github/license/sanxchep/licasa)](LICENSE)

# Licasa

Licasa is an image viewer and editor for the Linux desktop inspired by Google's Picasa Photo Viewer. It opens a photo from your file manager in an image-sized fullscreen window or in a floating mode.

**Licasa is an independent open-source project and is not affiliated with or endorsed by Google.**

## Try Licasa

**[Download published releases](https://github.com/sanxchep/licasa/releases)** · [0.8.0 release notes and measured results](packaging/release-notes/v0.8.0.md)

**0.8.0** combines 27 runtime optimisations with installation and graphics fixes.
- View photos fullscreen or in a floating window, then crop, adjust, and export them.
- Open JPEG, PNG, TIFF, WebP, HEIC/HEIF, JPEG XL, AVIF, JPEG 2000, and RAW previews.
- Play Motion Photos and animated APNG files.

If you try it, [report a bug or suggest a feature](https://github.com/sanxchep/licasa/issues/new).

## In action

**Open a photo** — From the desktop into the fullscreen viewer.

![Opening a photo in Licasa at original speed](assets/demos/open-photo.gif)

**Resize a floating window** — Open a photo, move it, and resize it on the desktop.

![Opening, moving, and resizing a Licasa floating window at original speed](assets/demos/floating-resize.gif)

**Keep multiple windows open** — Open additional views and arrange floating windows.

![Opening additional Licasa floating windows and arranging four views at original speed](assets/demos/multiple-windows.gif)

## Screenshots

<details>
<summary>Fullscreen viewer, floating windows, and photo editor</summary>

**Fullscreen viewer** — View your photos with minimal controls.

![Licasa displaying a mountain photo in fullscreen mode](assets/screenshots/fullscreen-viewer.png)

**Floating windows** — Keep multiple photos open on your desktop.

![Licasa displaying three floating photo windows on the desktop](assets/screenshots/floating-windows.png)

**Photo editor** — Adjust light, color, and detail, then save or export.

![Licasa photo editor with light, color, and detail controls beside a mountain photo](assets/screenshots/photo-editor.png)

</details>

## Install

The native Ubuntu `.deb` is built for **Ubuntu 24.04 only**. It cannot be installed on Debian by changing dependency names: the JPEG, FFmpeg, and Qt binary interfaces differ. Do not force its dependencies or mix Ubuntu repositories into Debian.

The support contract below describes qualification required for a new candidate from this source tree. It does not retroactively certify published 0.8.0 packages. The Debian 13 native target is in development until its complete candidate qualification passes; no Debian-native 0.8.0 download is available. The published 0.8.0 AppImage has a known fallback file-picker defect on Debian, tracked in [issue #3](https://github.com/sanxchep/licasa/issues/3).

<!-- support-matrix:start -->
| Package / target | Architecture / libc baseline | Qt runtime | Candidate qualification |
| --- | --- | --- | --- |
| ubuntu-deb / Ubuntu 24.04 | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| debian-deb / Debian 13 (Trixie) | x86-64 / glibc ≥ 2.41 | 6.8.2 | In development; not a published support claim |
| debian-deb / Debian 13 / MATE X11 / native GTK file picker | x86-64 / glibc ≥ 2.41 | 6.8.2 | In development; not a published support claim |
| appimage / Ubuntu 24.04 | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| appimage / Ubuntu 26.04 | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| appimage / Debian 13 (Trixie) | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| appimage / Debian 13 / MATE X11 / AT-SPI keyboard accessibility | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| appimage / Fedora 44 | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| appimage / Arch Linux (rolling; tested image recorded in evidence) | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| rpm / Fedora 44 | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| arch / Arch Linux (rolling; tested image recorded in evidence) | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| snap / Ubuntu 24.04 / GNOME X11 and Wayland | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
| snap / Ubuntu 24.04 / Plasma X11 and Wayland | x86-64 / glibc ≥ 2.39 | 6.4.2 | Required before stable |
<!-- support-matrix:end -->

The table is generated from [the support manifest](packaging/support-matrix.json). ARM, 32-bit, musl-based systems, older glibc releases, and unlisted native distribution targets are outside this contract. A supported target requires a passing report for the exact candidate; skipped, failed, and unsupported outcomes are recorded separately. Container checks cover the named userspace with virtual displays, while the Snap jobs exercise nested GNOME/Plasma sessions. The MATE target starts MATE and AT-SPI for keyboard, role, state, value, and focus checks. A separate Debian-native target confirms GTK chooser widgets and their matching Qt platform theme, then selects a photo, cancels and reopens dialogs, preserves edits, and decodes Save As output. Physical graphics, other native dialog providers and Wayland portals, native desktop login sessions, and spoken screen-reader announcement order require separate desktop evidence. Automated accessibility checks do not establish conformance.

### Ubuntu 24.04 (amd64)

Download `licasa_0.8.0-1_amd64.deb` and its `.sha256` file. APT installs the required system libraries:

```sh
sha256sum -c licasa_0.8.0-1_amd64.deb.sha256
sudo apt install ./licasa_0.8.0-1_amd64.deb
```

### Fedora 44 (x86-64)

Download `licasa-appimage-0.8.0-1.fc44.x86_64.rpm` and its `.sha256` file:

```sh
sha256sum -c licasa-appimage-0.8.0-1.fc44.x86_64.rpm.sha256
sudo dnf install ./licasa-appimage-0.8.0-1.fc44.x86_64.rpm
```

### Arch Linux (x86-64)

Download `licasa-appimage-0.8.0-1-x86_64.pkg.tar.zst` and its `.sha256` file:

```sh
sha256sum -c licasa-appimage-0.8.0-1-x86_64.pkg.tar.zst.sha256
sudo pacman -U ./licasa-appimage-0.8.0-1-x86_64.pkg.tar.zst
```

The release also includes a `PKGBUILD` for people who prefer to build the Arch package themselves. The `.pkg.tar.zst` file above is ready to install.

### AppImage (qualified x86-64 glibc desktops)

Download `Licasa-0.8.0-x86_64.AppImage`, `install-licasa-appimage.sh`, and both `.sha256` files:

```sh
sha256sum -c Licasa-0.8.0-x86_64.AppImage.sha256
sha256sum -c install-licasa-appimage.sh.sha256
bash install-licasa-appimage.sh ./Licasa-0.8.0-x86_64.AppImage
```

The AppImage requires 64-bit Intel or AMD Linux with glibc and compatible desktop libraries. ARM and 32-bit systems need a different package. The installer explains missing system tools, extraction failures, and missing or incompatible libraries before replacing an existing installation. If a library check fails, keep the error output when reporting the problem.

If installation or startup fails, the updated installer can produce a support report before Licasa starts:

```sh
bash install-licasa-appimage.sh --diagnose ./Licasa-0.8.0-x86_64.AppImage > licasa-support.txt
```

You can omit the AppImage filename to inspect an existing per-user or native installation. This does not start Licasa, execute the downloaded AppImage, extract it, or use the network. It reports the system, desktop/session, missing tools, FUSE availability, destination permissions/mount flags, and installed-library failures. Environment values and photo paths are omitted; review the report before sharing. Exit status 1 means it found a problem; 0 means the checks it could perform passed, not a guarantee that a graphical launch will work.

Installation and removal also retain a private log at `$XDG_STATE_HOME/licasa/installation.log` (normally `~/.local/state/licasa/installation.log`), with one rotated copy. It includes local installation paths and error output. Nothing is uploaded. The diagnostics and logging described here require the updated installer from this source revision; the previously published 0.7.1 installer does not include them.

### Snap (systems with Snapd)

For automatic Store updates, install from the stable channel:

```sh
sudo snap install licasa
```

A directly installed candidate Snap uses `licasa_0.8.0_amd64.snap` and its `.sha256` file:

```sh
sha256sum -c licasa_0.8.0_amd64.snap.sha256
sudo snap install --dangerous ./licasa_0.8.0_amd64.snap
```

In **Settings**, turn on **Background process** if you want faster repeat opens. Licasa then stays ready when you close its window and starts in the background at your next desktop login, including after a reboot. This is off by default. Turning it off removes the login startup entry; closing Licasa then exits it. This works through your desktop session, so it does not start before you log in.

If Licasa has trouble, its private error log is at `$XDG_STATE_HOME/licasa/errors.log` (normally `~/.local/state/licasa/errors.log`; inside the Snap's user data for Snap installs). It records warnings and errors, keeps one rotated copy, and never uploads anything. Log messages can include photo paths, so review the file before attaching it to a bug report.

## Update

For Ubuntu `.deb`, Fedora, Arch, or AppImage, download the newer files and repeat the matching installation steps above with the new filenames. Match native packages to their distribution target. Close Licasa before updating an AppImage installation; the installer replaces its per-user files and launcher.

Store-installed Snaps update automatically. To switch an existing Snap to the beta channel, run `sudo snap refresh licasa --beta`. A directly installed `.snap` file requires another manual download and `sudo snap install --dangerous ./<new-file>.snap` after checking its checksum.

## Uninstall

Use the command for the package you installed:

```sh
sudo apt remove licasa             # Ubuntu .deb
sudo dnf remove licasa-appimage    # Fedora RPM
sudo pacman -R licasa-appimage     # Arch package
sudo snap remove licasa            # Snap
```

For a per-user AppImage installation, use its saved removal script:

```sh
bash "$HOME/.local/opt/licasa-appimage/install-licasa-appimage.sh" --uninstall
```

## Build the Linux packages

From the repository root, with Docker installed:

```sh
docker build -f packaging/build-deb.Dockerfile -t licasa-deb-builder packaging
mkdir -p build/deb-output
docker run --rm -v "$PWD:/source:ro" -v "$PWD/build/deb-output:/output" \
  licasa-deb-builder bash /source/packaging/verify-fresh-build.sh
```

The `.deb`, AppImage, and SHA-256 files appear in `build/deb-output/`. Docker is required only to build these packages from source. To check the local files:

```sh
(cd build/deb-output && sha256sum -c licasa_0.8.0-1_amd64.deb.sha256)
(cd build/deb-output && sha256sum -c Licasa-0.8.0-x86_64.AppImage.sha256)
(cd build/deb-output && sha256sum -c install-licasa-appimage.sh.sha256)
```

## Verify desktop launches

CI checks source builds, sanitizers, installed packages and desktop integration. Complete candidate verification covers every target in the support manifest before release.

For the Debian-native candidate, build inside its own userspace:

```sh
docker build -f packaging/build-debian.Dockerfile -t licasa-debian13-builder packaging
mkdir -p build/qualification/debian13
docker run --rm -v "$PWD:/source:ro" -v "$PWD/build/qualification/debian13:/output" \
  licasa-debian13-builder bash -ec '
    set -o pipefail
    mkdir -p /work/licasa
    tar -C /source --exclude="./build*" --exclude="./cmake-build-*" \
      --exclude="./.git" --exclude="./.idea" --exclude="./private-validation" \
      --exclude="./CMakeUserPresets.json" -cf - . | tar -C /work/licasa -xf -
    cd /work/licasa
    python3 tools/build_debian_deb.py --jobs 3 --output-dir /output'
```

The Debian package is distinctly named `licasa_<version>-1+deb13_amd64.deb`. Its private codecs and multimedia plugin are rebuilt against Debian 13; the Ubuntu package and its portable derivatives keep their existing Qt profile. After the build, use `tools/verify_deb_container.py <package> --target debian-13` for clean APT installation, decoder, runtime-isolation, and dialog checks. Full candidate qualification remains required before claiming support.

Maintainers can use `tools/verify_linux_vm_desktop.py` with a disposable QEMU/KVM guest running a normal GNOME or Plasma Wayland login through GDM or SDDM. Install the desktop, Python/Pillow, GTK's `gtk-launch`, desktop-file-utils, xdg-utils, and the appropriate GNOME/KDE file-handler tools in the guest. Install the candidate AppImage with the updated installer and install the native package separately. Keep Qt's platform and graphics backend overrides unset.

Inside that disposable guest only, create its qualification marker:

```sh
sudo install -m 644 /dev/null /etc/licasa-disposable-qualification
```

Expose the guest's SSH port on localhost and QEMU's QMP socket in a dedicated results directory shared with QEMU as `/vm`. Use a task-specific SSH key and known-hosts file. From the source checkout on the host:

```sh
python3 tools/verify_linux_vm_desktop.py \
  --port 46601 --identity ./vm/key --known-hosts ./vm/known_hosts \
  --qmp ./vm/qmp.sock --desktop gnome --variant appimage \
  --guest-appimage /home/qualifier/Licasa-0.8.0-x86_64.AppImage \
  --output ./reports/gnome-appimage
```

Repeat for `--desktop kde` and `--variant native` or `portable`. The `appimage` variant checks the per-user installation; `portable` checks the raw FUSE-mounted AppImage using a temporary test desktop entry inside the guest. `--install-home` can select an AppImage installation made under a deliberately awkward test path. Each case checks desktop-entry activation, opening a photo through the desktop's default file handler, reopening with a photo, and normal shutdown. Reports include versions, artifact hashes and screenshots; black frames or a running process alone do not pass. Failures retain their report and output.

The worker refuses ordinary hosts, containers, root accounts, and unmarked guests. The runner does not provision a VM or connect to the host's display, session bus or GPU. Discard the guest after collecting evidence. VM results cover the tested desktop session and virtual graphics device; physical graphics drivers and other host policies still need separate qualification.

### Physical graphics checks

Automatic Wayland launches prefer OpenGL ES to avoid a reproduced black-window failure in the bundled Qt desktop OpenGL path on NVIDIA. Explicit Qt graphics selections remain honored; X11 keeps its existing defaults. The diagnostic uses the same selection as the viewer.

`tools/verify_appimage_graphics.py` checks a real GPU through a private Weston Wayland session. First run the Fedora/Arch AppImage installation qualification above the graphics layer, then build the matching test image:

```sh
python3 tools/verify_appimage_fedora.py ./build/deb-output/Licasa-0.8.0-x86_64.AppImage \
  --output ./reports/fedora-installation.json
docker build -f packaging/verify-graphics-fedora.Dockerfile -t licasa-graphics-fedora .
python3 tools/verify_appimage_graphics.py \
  --appimage ./build/deb-output/Licasa-0.8.0-x86_64.AppImage \
  --image licasa-graphics-fedora --render-node /dev/dri/renderD129 \
  --output ./reports/fedora-graphics
```

Choose an already accessible render node for the GPU being tested. For Arch, use `tools/verify_appimage_desktops.py` for installation qualification and `packaging/verify-graphics-arch.Dockerfile` for the graphics image. The graphics images build a checksum-pinned Weston 15.0.1 GL capture module with a narrow asynchronous row-order fix. This corrects an independently reproduced headless screenshot inversion on NVIDIA; the module belongs only to the qualification image and is never bundled with Licasa. The build retains source, patch and module hashes in `/usr/share/licasa-qualification/weston-capture-build.json`.

The runner forwards that render device to a non-root container with a read-only root, no capabilities, and no network, host display, input devices or session bus. It clears rendering/library overrides and first checks raw capture orientation with an independent `weston-image` fixture, then closes that fixture and verifies it has disappeared. It checks both the diagnostic and normal AppRun launcher, their actual renderer names, the application's GPU descriptors, visible photo pixels and normal shutdown. Missing or inverted calibration prevents application qualification; raw PNGs remain unchanged. Software rendering, llvmpipe, missing evidence or a software retry fails the accelerated gate. Explicit software/fallback checks remain separate in the ordinary package qualification.

`--nvidia` requires a test image with NVIDIA userspace matching the installed kernel driver. It maps the selected GPU's NVIDIA device/control nodes and exposes its primary DRM node as metadata only, with device-cgroup read/write access denied and checked before graphics starts. It grants no primary-node or modesetting I/O. A renderer name alone still cannot pass the photo assertion. An inverted photo is reported separately from absent pixels and still fails orientation qualification; raw captures are retained unchanged. These tests qualify the distribution's userspace against the current host kernel/GPU driver; they do not replace a native GNOME/KDE login test with that distribution's kernel. Use a fresh output directory for each artifact/environment comparison; failed runs retain an explicitly failed report.

## License

Licasa's first-party source is licensed under the [BSD 2-Clause License](LICENSE). Third-party components and test fixtures have their own licenses, which are retained with the distributed materials.
