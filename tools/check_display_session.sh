#!/bin/sh
# Exercise an installed Licasa viewer and its production-QML diagnostic in a
# virtual X11 or Wayland session. Called from clean-package verification images.
set -eu

if [ "$#" -ne 5 ]; then
    echo "Usage: $0 APP DIAGNOSTIC PHOTO x11|wayland default|software" >&2
    exit 2
fi
app=$1
diagnostic=$2
photo=$3
protocol=$4
mode=$5

case "$protocol:$mode" in
    x11:default|x11:software|wayland:default|wayland:software) ;;
    *) echo "Unsupported display check: $protocol:$mode" >&2; exit 2 ;;
esac

mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "${XDG_CONFIG_HOME:-$HOME/.config}"
chmod 700 "$XDG_RUNTIME_DIR"
unset QT_QUICK_BACKEND QSG_RHI_BACKEND LIBGL_ALWAYS_SOFTWARE
if [ "$mode" = software ]; then
    export QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1
fi

display_pid=
x11_pid=
viewer_pid=
cleanup() {
    [ -z "$viewer_pid" ] || kill "$viewer_pid" 2>/dev/null || true
    [ -z "$display_pid" ] || kill "$display_pid" 2>/dev/null || true
    [ -z "$x11_pid" ] || kill "$x11_pid" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

if [ "$protocol" = x11 ]; then
    export DISPLAY=:90 QT_QPA_PLATFORM=xcb
    Xvfb :90 -ac -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    display_pid=$!
    x11_socket=/tmp/.X11-unix/X90
else
    export DISPLAY=:91 WAYLAND_DISPLAY=wayland-licasa QT_QPA_PLATFORM=wayland
    Xvfb :91 -ac -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    x11_pid=$!
    x11_socket=/tmp/.X11-unix/X91
fi

attempt=0
while [ ! -S "$x11_socket" ] && [ "$attempt" -lt 40 ]; do
    sleep 0.25
    attempt=$((attempt + 1))
done
if [ ! -S "$x11_socket" ]; then
    cat /tmp/xvfb.log >&2
    echo "X11 test display did not start" >&2
    exit 1
fi

if [ "$protocol" = wayland ]; then
    weston --backend=x11 --renderer=pixman --socket="$WAYLAND_DISPLAY" \
        --idle-time=0 >/tmp/weston.log 2>&1 &
    display_pid=$!
    attempt=0
    while [ ! -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] && [ "$attempt" -lt 40 ]; do
        sleep 0.25
        attempt=$((attempt + 1))
    done
    if [ ! -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then
        cat /tmp/weston.log >&2
        echo "Wayland test display did not start" >&2
        exit 1
    fi
fi

# The diagnostic is packaged with the app. Resolve its own private libraries
# from that package, without giving the normal launcher a developer override.
diagnostic_usr=$(dirname "$(dirname "$diagnostic")")
run_diagnostic() (
    export LD_LIBRARY_PATH="$diagnostic_usr/lib/licasa/lib:$diagnostic_usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export QT_PLUGIN_PATH="$diagnostic_usr/plugins:$diagnostic_usr/lib/licasa/plugins:$diagnostic_usr/lib/qt6/plugins"
    export QML2_IMPORT_PATH="$diagnostic_usr/qml:$diagnostic_usr/lib/qt6/qml"
    export QML_IMPORT_PATH="$QML2_IMPORT_PATH"
    if [ "$1" = software ]; then
        export QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1
    fi
    timeout 45 "$diagnostic" --viewer "$photo"
)
find_live_licasa_child() {
    for comm_file in /proc/[0-9]*/comm; do
        [ -r "$comm_file" ] || continue
        pid_dir=${comm_file%/comm}
        candidate_pid=${pid_dir##*/}
        [ "$candidate_pid" != "$viewer_pid" ] || continue
        IFS= read -r candidate_name < "$comm_file" || continue
        [ "$candidate_name" = licasa ] || continue
        read -r stat_pid stat_comm stat_state stat_rest < "$pid_dir/stat" || continue
        if [ "$stat_state" != Z ]; then
            echo "$candidate_pid"
            return 0
        fi
    done
    return 1
}
diagnostic_fallback=false
if run_diagnostic "$mode" >/tmp/licasa-display-report.json \
    2>/tmp/licasa-display-diagnostic.log; then
    :
else
    status=$?
    if [ "$mode" = default ] && [ "$status" -eq 43 ]; then
        diagnostic_fallback=true
        if ! run_diagnostic software >/tmp/licasa-display-report.json \
            2>/tmp/licasa-display-diagnostic.log; then
            cat /tmp/licasa-display-diagnostic.log >&2
            cat /tmp/licasa-display-report.json >&2
            exit 1
        fi
    else
        cat /tmp/licasa-display-diagnostic.log >&2
        cat /tmp/licasa-display-report.json >&2
        exit 1
    fi
fi
for field in first_window_frame_ms first_preview_presented_ms window_exposed_ms; do
    if ! grep -Fq "\"$field\"" /tmp/licasa-display-report.json; then
        cat /tmp/licasa-display-report.json >&2
        echo "Viewer did not report $field" >&2
        exit 1
    fi
done
if grep -Fq '"image_failed": true' /tmp/licasa-display-report.json; then
    cat /tmp/licasa-display-report.json >&2
    echo "Viewer failed to open its first photo" >&2
    exit 1
fi
if ! grep -Fq 'Qt Quick backend (sceneGraphInitialized): graphicsApi=' \
    /tmp/licasa-display-diagnostic.log; then
    cat /tmp/licasa-display-diagnostic.log >&2
    echo "Qt Quick scene graph did not initialize" >&2
    exit 1
fi

"$app" "$photo" >/tmp/licasa-display-app.log 2>&1 &
viewer_pid=$!
app_retried=false
attempt=0
while [ "$attempt" -lt 60 ]; do
    if ! kill -0 "$viewer_pid" 2>/dev/null; then
        if [ "$mode" = default ] && [ "$app_retried" = false ] &&
            grep -Fq 'retrying once with the Qt software renderer' \
                /tmp/licasa-display-app.log; then
            child_pid=
            child_attempt=0
            while [ -z "$child_pid" ] && [ "$child_attempt" -lt 20 ]; do
                child_pid=$(find_live_licasa_child || true)
                [ -n "$child_pid" ] || sleep 0.25
                child_attempt=$((child_attempt + 1))
            done
            if [ -n "$child_pid" ]; then
                viewer_pid=$child_pid
                app_retried=true
            fi
        fi
        if ! kill -0 "$viewer_pid" 2>/dev/null; then
            cat /tmp/licasa-display-app.log >&2
            echo "Installed viewer exited during launch" >&2
            exit 1
        fi
    fi
    if [ "$protocol" = x11 ]; then
        if xwininfo -root -tree 2>/dev/null | grep -q '"Licasa"'; then
            break
        fi
    elif [ "$app_retried" = true ] ||
        grep -Fq 'Qt Quick backend (sceneGraphInitialized): graphicsApi=' \
            /tmp/licasa-display-app.log; then
        break
    fi
    sleep 0.25
    attempt=$((attempt + 1))
done
if [ "$attempt" -ge 60 ]; then
    cat /tmp/licasa-display-app.log >&2
    echo "Installed viewer did not show a window" >&2
    exit 1
fi
sleep 2
if ! kill -0 "$viewer_pid" 2>/dev/null; then
    cat /tmp/licasa-display-app.log >&2
    echo "Installed viewer did not remain open" >&2
    exit 1
fi
if [ "$app_retried" = true ] &&
    ! grep -Fq 'Qt Quick backend (sceneGraphInitialized): graphicsApi=Software' \
        /tmp/licasa-display-app.log; then
    cat /tmp/licasa-display-app.log >&2
    echo "Software retry did not initialize a scene graph" >&2
    exit 1
fi

grep -F 'Qt Quick backend (sceneGraphInitialized): graphicsApi=' \
    /tmp/licasa-display-diagnostic.log | tail -1
echo "Display session passed: $protocol $mode; diagnostic_fallback=$diagnostic_fallback; app_retry=$app_retried"
