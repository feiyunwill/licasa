#!/usr/bin/env bash
# Black-box install/update/rollback/remove checks in a disposable container home.
set -euo pipefail
if [[ $# -lt 2 || $# -gt 3 || ${HOME:-} != /tmp/licasa-install-test ]]; then
  echo 'Use this check only with HOME=/tmp/licasa-install-test in a disposable container.' >&2
  exit 2
fi
installer=$1
appimage=$2
baseline_installer=${3:-$installer}
source "$installer"
data="$HOME/data with spaces"
config="$HOME/config with spaces"
target="$HOME/.local/opt/licasa-appimage"
desktop="$data/applications/licasa-appimage.desktop"
icon="$data/icons/hicolor/512x512/apps/licasa-appimage.png"
mkdir -p "$config/licasa" "$data/applications" "$HOME/fault-tools"
printf 'keep settings\n' > "$config/licasa/settings.ini"
printf 'keep photo\n' > "$HOME/photo.jpg"
printf 'keep other application\n' > "$data/applications/other.desktop"
for install_script in "$baseline_installer" "$installer"; do
  XDG_DATA_HOME="$data" XDG_CONFIG_HOME="$config" bash "$install_script" "$appimage"
done
test -f "$desktop"
test -f "$icon"
licasa_same_file "$installer" "$target/install-licasa-appimage.sh"

# Make the previous integration distinguishable even when checking the same
# published payload twice. Failure must restore these exact prior bytes.
cp "$icon" "$HOME/original-icon"
printf 'previous icon\n' > "$icon"
printf 'previous application\n' > "$target/rollback-sentinel"
cp "$desktop" "$HOME/previous.desktop"
export LICASA_CHECK_REAL_MV="$(command -v mv)"
export LICASA_CHECK_DESKTOP="$desktop"
export LICASA_CHECK_FAULT="$HOME/fault-fired"
cat > "$HOME/fault-tools/mv" <<'EOF'
#!/usr/bin/env bash
if [[ ${@: -1} == "$LICASA_CHECK_DESKTOP" && ! -e $LICASA_CHECK_FAULT ]]; then
  touch "$LICASA_CHECK_FAULT"
  echo 'Qualification: injected desktop replacement failure' >&2
  exit 73
fi
exec "$LICASA_CHECK_REAL_MV" "$@"
EOF
chmod +x "$HOME/fault-tools/mv"
status=0
XDG_DATA_HOME="$data" XDG_CONFIG_HOME="$config" PATH="$HOME/fault-tools:$PATH" \
  bash "$installer" "$appimage" || status=$?
test "$status" -eq 73
licasa_same_file "$HOME/previous.desktop" "$desktop"
test "$(< "$icon")" = 'previous icon'
test "$(< "$target/rollback-sentinel")" = 'previous application'
cp "$HOME/original-icon" "$icon"

# The copied remover must use recorded locations even with no XDG overrides.
(
  unset XDG_DATA_HOME XDG_CONFIG_HOME
  bash "$target/install-licasa-appimage.sh" --uninstall
)
test ! -e "$target"
test ! -e "$desktop"
test ! -e "$icon"
test "$(< "$config/licasa/settings.ini")" = 'keep settings'
test "$(< "$HOME/photo.jpg")" = 'keep photo'
test "$(< "$data/applications/other.desktop")" = 'keep other application'
test -z "$(find "$data" -name '.licasa-desktop.*' -o -name '.licasa-icon.*')"
echo 'AppImage lifecycle passed: fresh install, reinstall, failed-update rollback, custom-XDG uninstall and user-data preservation.'
