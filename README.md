<p align="center">
<img src="assets/licasa.png" width="512" alt="Licasa logo"/>
</p>

[![Build and verify](https://github.com/sanxchep/licasa/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sanxchep/licasa/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/sanxchep/licasa?label=release)](https://github.com/sanxchep/licasa/releases)
[![Commit activity](https://img.shields.io/github/commit-activity/m/sanxchep/licasa?label=commit%20activity)](https://github.com/sanxchep/licasa/graphs/commit-activity)
[![License](https://img.shields.io/github/license/sanxchep/licasa)](LICENSE)

# Licasa

Licasa is an image viewer and editor for the Linux desktop. It opens a photo from your file manager in an image-sized fullscreen window or in a floating mode.

**Licasa is an independent open-source project and is not affiliated with or endorsed by Google.**

## Try Licasa

**[Download a release](https://github.com/sanxchep/licasa/releases)** · [0.7.1 release notes](packaging/release-notes/v0.7.1.md)

- View photos fullscreen or in a floating window, then crop, adjust, and export them.
- Open JPEG, PNG, TIFF, WebP, HEIC/HEIF, JPEG XL, AVIF, JPEG 2000, and RAW previews.
- Play Motion Photos and animated APNG files.

If you try it, [report a bug or suggest a feature](https://github.com/sanxchep/licasa/issues/new).

## Install

Download the files for your system from the same [GitHub release](https://github.com/sanxchep/licasa/releases). The commands below use 0.7.1; substitute the filenames shown on the release page for another version. Each package has a matching `.sha256` file. Run the commands in the folder containing your downloads.

### Ubuntu 24.04 (amd64)

Download `licasa_0.7.1-1_amd64.deb` and its `.sha256` file. APT installs the required system libraries:

```sh
sha256sum -c licasa_0.7.1-1_amd64.deb.sha256
sudo apt install ./licasa_0.7.1-1_amd64.deb
```

### Fedora 44 (x86-64)

Download `licasa-appimage-0.7.1-1.fc44.x86_64.rpm` and its `.sha256` file:

```sh
sha256sum -c licasa-appimage-0.7.1-1.fc44.x86_64.rpm.sha256
sudo dnf install ./licasa-appimage-0.7.1-1.fc44.x86_64.rpm
```

### Arch Linux (x86-64)

Download `licasa-appimage-0.7.1-1-x86_64.pkg.tar.zst` and its `.sha256` file:

```sh
sha256sum -c licasa-appimage-0.7.1-1-x86_64.pkg.tar.zst.sha256
sudo pacman -U ./licasa-appimage-0.7.1-1-x86_64.pkg.tar.zst
```

The release also includes a `PKGBUILD` for people who prefer to build the Arch package themselves. The `.pkg.tar.zst` file above is ready to install.

### AppImage (recent x86-64 desktops)

Download `Licasa-0.7.1-x86_64.AppImage`, `install-licasa-appimage.sh`, and both `.sha256` files:

```sh
sha256sum -c Licasa-0.7.1-x86_64.AppImage.sha256
sha256sum -c install-licasa-appimage.sh.sha256
bash install-licasa-appimage.sh ./Licasa-0.7.1-x86_64.AppImage
```

This installs Licasa for your user without FUSE and adds an application-menu launcher and file-manager **Open With** entry. The AppImage is built on Ubuntu 24.04, so older Linux systems may not run it.

The AppImage requires 64-bit Intel or AMD Linux with glibc and compatible desktop libraries. ARM and 32-bit systems need a different package. The installer explains missing system tools, extraction failures, and missing or incompatible libraries before replacing an existing installation. If a library check fails, keep the error output when reporting the problem.

### Snap (systems with Snapd)

For automatic Store updates, install from the stable channel:

```sh
sudo snap install licasa
```

The GitHub release also contains `licasa_0.7.1_amd64.snap` and its `.sha256` file for a direct installation:

```sh
sha256sum -c licasa_0.7.1_amd64.snap.sha256
sudo snap install --dangerous ./licasa_0.7.1_amd64.snap
```

A [locally installed Snap](https://snapcraft.io/docs/explanation/snap-development/install-modes/) does not receive Store updates automatically. Use the Store channel if you want automatic updates.

In **Settings**, turn on **Background process** if you want faster repeat opens. Licasa then stays ready when you close its window and starts in the background at your next desktop login, including after a reboot. This is off by default. Turning it off removes the login startup entry; closing Licasa then exits it. This works through your desktop session, so it does not start before you log in.

If Licasa has trouble, its private error log is at `$XDG_STATE_HOME/licasa/errors.log` (normally `~/.local/state/licasa/errors.log`; inside the Snap's user data for Snap installs). It records warnings and errors, keeps one rotated copy, and never uploads anything. Log messages can include photo paths, so review the file before attaching it to a bug report.

## Update

For Debian, Fedora, Arch, or AppImage, download the newer files and repeat the matching installation steps above with the new filenames. Close Licasa before updating an AppImage installation; the installer replaces its per-user files and launcher.

Store-installed Snaps update automatically. To switch an existing Snap to the beta channel, run `sudo snap refresh licasa --beta`. A directly installed `.snap` file requires another manual download and `sudo snap install --dangerous ./<new-file>.snap` after checking its checksum.

## Uninstall

Use the command for the package you installed:

```sh
sudo apt remove licasa             # Ubuntu .deb
sudo dnf remove licasa-appimage    # Fedora RPM
sudo pacman -R licasa-appimage     # Arch package
sudo snap remove licasa            # Snap
```

For the AppImage installation, remove `~/.local/opt/licasa-appimage`, `~/.local/share/applications/licasa-appimage.desktop`, and `~/.local/share/icons/hicolor/512x512/apps/licasa-appimage.png`. Delete only the paths created by the AppImage installer.

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
(cd build/deb-output && sha256sum -c licasa_0.7.1-1_amd64.deb.sha256)
(cd build/deb-output && sha256sum -c Licasa-0.7.1-x86_64.AppImage.sha256)
(cd build/deb-output && sha256sum -c install-licasa-appimage.sh.sha256)
```

## License

Licasa's first-party source is licensed under the [BSD 2-Clause License](LICENSE). Third-party components and test fixtures have their own licenses, which are retained with the distributed materials.
