#!/usr/bin/env bash
# Install an AppImage as an extracted, FUSE-free per-user desktop application.
set -euo pipefail

# Shared with clean-package checks, before any desktop test tools are installed.
# Only the dynamic loader inspects these files; no viewer or display is started.
licasa_check_runtime_dependencies() (
  set -euo pipefail
  if [[ $# -lt 2 ]]; then
    echo "A library path and application files are required for the runtime check." >&2
    return 2
  fi
  local library_path=$1
  shift
  local check_work file magic output status checked=0 failed=0
  if ! check_work=$(mktemp -d); then
    echo "Could not create temporary files for the runtime library check." >&2
    return 1
  fi
  trap 'rm -rf -- "$check_work"' EXIT
  if ! find "$@" -type f -print0 > "$check_work/files"; then
    echo "Could not inspect all installed application files." >&2
    return 1
  fi
  while IFS= read -r -d '' file; do
    if [[ ! -r $file ]]; then
      printf 'Cannot read application file: %s\n' "$file" >&2
      failed=1
      continue
    fi
    magic=""
    LC_ALL=C read -r -N 4 magic < "$file" || true
    [[ $magic == $'\x7fELF' ]] || continue
    checked=$((checked + 1))
    status=0
    LC_ALL=C LD_PRELOAD= LD_AUDIT= LD_LIBRARY_PATH="$library_path" \
      ldd "$file" > "$check_work/ldd" 2>&1 || status=$?
    output=$(< "$check_work/ldd")
    if [[ $status -ne 0 || $output == *"=> not found"* || $output == *": version "*" not found"* ]]; then
      printf 'Missing or incompatible runtime libraries for %s:\n%s\n' "$file" "$output" >&2
      failed=1
    fi
  done < "$check_work/files"
  if [[ $checked -eq 0 ]]; then
    echo "No application binaries were found to check." >&2
    return 1
  fi
  if [[ $failed -ne 0 ]]; then
    echo "Licasa needs the libraries listed above before it can run." >&2
    echo "Use a compatible glibc-based Linux desktop; this build uses Ubuntu 24.04 as its library baseline." >&2
    return 1
  fi
  printf 'Runtime library check passed (%s ELF files).\n' "$checked"
)

# Package verification sources the library check without running the installer.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
  return 0
fi

if [[ $# -ne 1 || ! -f $1 ]]; then
  echo "Usage: $0 Licasa-<version>-x86_64.AppImage" >&2
  echo "Download the AppImage and this installer from the same Licasa release." >&2
  echo "Run this installer as your normal desktop user; it does not need sudo or FUSE." >&2
  exit 2
fi

for required_command in uname getconf ldd find cp chmod mkdir mktemp mv rm rmdir sed; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Cannot install Licasa: the required system tool '$required_command' is missing." >&2
    echo "Install your distribution's standard shell, file and glibc tools, then retry." >&2
    exit 1
  fi
done
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 || $(getconf LONG_BIT) != 64 ]]; then
  echo "This AppImage requires 64-bit x86 Linux (Intel or AMD)." >&2
  echo "ARM and 32-bit systems cannot use this package." >&2
  exit 1
fi
if ! libc_version=$(getconf GNU_LIBC_VERSION 2>/dev/null) || [[ $libc_version != glibc\ * ]]; then
  echo "This AppImage requires a glibc-based Linux desktop." >&2
  echo "Systems using another C library, such as Alpine Linux with musl, need a compatible package." >&2
  exit 1
fi

appimage="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
install_home="${HOME:?HOME is required}"
if [[ $install_home != /* ]]; then
  echo "HOME must be an absolute path for a per-user desktop installation." >&2
  exit 2
fi
data_home="${XDG_DATA_HOME:-$install_home/.local/share}"
if [[ $data_home != /* ]]; then
  echo "XDG_DATA_HOME must be an absolute path" >&2
  exit 2
fi
apps="$install_home/.local/opt"
target="$apps/licasa-appimage"
desktop_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/512x512/apps"
desktop="$desktop_dir/licasa-appimage.desktop"
icon="$icon_dir/licasa-appimage.png"

if [[ -e $target && ! -f $target/.licasa-appimage-install ]]; then
  echo "Refusing to replace an installation not created by this script: $target" >&2
  exit 1
fi

mkdir -p "$apps" "$desktop_dir" "$icon_dir"
work="$(mktemp -d "$apps/.licasa-appimage.XXXXXXXX")"
backup=""
committed=0
moved_new=0
cleanup() {
  if [[ $committed -eq 0 ]]; then
    if [[ $moved_new -eq 1 ]]; then
      rm -rf -- "$target"
    fi
    if [[ -n $backup ]]; then
      mv "$backup" "$target"
    fi
  fi
  rm -rf -- "$work"
}
trap cleanup EXIT

cp -- "$appimage" "$work/Licasa.AppImage"
chmod u+x "$work/Licasa.AppImage"
if ! (cd "$work" && ./Licasa.AppImage --appimage-extract >extract.log 2>&1); then
  echo "Could not extract the AppImage. It may be damaged, incompatible with this system, or stored on a filesystem that blocks execution." >&2
  echo "Download it again from the official release, check its SHA-256 file, and retry from a writable executable location." >&2
  while IFS= read -r line; do printf '%s\n' "$line" >&2; done < "$work/extract.log"
  exit 1
fi
extracted="$work/squashfs-root"
source_desktop="$extracted/usr/share/applications/licasa.desktop"
source_icon="$extracted/usr/share/icons/hicolor/512x512/apps/licasa.png"
for required in "$extracted/AppRun" "$extracted/usr/bin/licasa" "$source_desktop" "$source_icon"; do
  if [[ ! -f $required ]]; then
    echo "The AppImage is missing $required" >&2
    exit 1
  fi
done
if ! licasa_check_runtime_dependencies \
    "$extracted/usr/lib/licasa/lib:$extracted/usr/lib" "$extracted/usr"; then
  echo "Installation stopped before replacing any existing Licasa application." >&2
  echo "Detected C library: $libc_version. Qt and image codecs should come from the AppImage; FUSE is not required." >&2
  exit 1
fi
mime="$(sed -n 's/^MimeType=//p' "$source_desktop")"
if [[ -z $mime || $mime == *$'\n'* ]]; then
  echo "The AppImage has no valid desktop MIME list" >&2
  exit 1
fi

# Desktop Entry Exec values require quoted paths and escaped special characters.
exec_path="$target/AppRun"
exec_path="${exec_path//\\/\\\\}"
exec_path="${exec_path//\"/\\\"}"
exec_path="${exec_path//\$/\\\$}"
tick=$'\x60'
exec_path="${exec_path//$tick/\\$tick}"
exec_path="${exec_path//%/%%}"
printf '%s\n' 'Installed by packaging/install-licasa-appimage.sh' > "$extracted/.licasa-appimage-install"
desktop_new="$work/licasa-appimage.desktop"
cat > "$desktop_new" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Licasa (AppImage)
Comment=Image viewer
Exec="$exec_path" %f
Terminal=false
Categories=Graphics;Viewer;
MimeType=$mime
Icon=licasa-appimage
StartupNotify=false
EOF

if [[ -e $target ]]; then
  backup="$(mktemp -d "$apps/.licasa-backup.XXXXXXXX")"
  rmdir "$backup"
  mv "$target" "$backup"
fi
mv "$extracted" "$target"
moved_new=1
cp "$target/usr/share/icons/hicolor/512x512/apps/licasa.png" "$icon"
mv "$desktop_new" "$desktop"
committed=1
if [[ -n $backup ]]; then
  rm -rf -- "$backup"
  backup=""
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  if ! update-desktop-database "$desktop_dir"; then
    echo "Licasa was installed, but the desktop file cache could not be refreshed. Sign out and back in if the launcher is missing." >&2
  fi
fi
echo "Installed Licasa in $target"
echo "Find 'Licasa (AppImage)' in your application menu or use Open With in your file manager."
echo "A graphical desktop is needed to open windows; installation did not start the application."
