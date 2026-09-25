#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
BIN_HOME="$HOME/.local/bin"
UNIT_HOME="$CONFIG_HOME/systemd/user"
APP_HOME="$DATA_HOME/applications"
ICON_HOME="$DATA_HOME/icons/hicolor"

if [[ "${XDG_SESSION_TYPE:-}" == wayland ]]; then
    echo 'Minimal Whisper supports X11 only. Wayland sessions are not supported.' >&2
    exit 1
fi

missing=()
for command in systemctl pactl pw-record xclip xdotool python3; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if ((${#missing[@]})); then
    printf 'Missing required commands: %s\n' "${missing[*]}" >&2
    echo 'See README.md for the Arch package and Python dependency setup.' >&2
    exit 1
fi

source "$PROJECT_DIR/lib/runtime.sh"
UI_PYTHON="$(minimal_whisper_resolve_ui_python)" || {
    echo 'PySide6 is missing. Install requirements.txt into Minimal Whisper’s Python environment first.' >&2
    exit 1
}
PTT_PYTHON="$(minimal_whisper_resolve_python)" || {
    echo 'openai-whisper and python-xlib are missing. Install requirements.txt into Minimal Whisper’s Python environment first.' >&2
    exit 1
}

mkdir -p "$CONFIG_HOME/minimal-whisper" "$STATE_HOME/minimal-whisper"
for name in settings.json languages.json models.json; do
    [[ -e "$CONFIG_HOME/minimal-whisper/$name" ]] ||
        [[ ! -f "$CONFIG_HOME/openai-whisper/$name" ]] ||
        cp -p "$CONFIG_HOME/openai-whisper/$name" "$CONFIG_HOME/minimal-whisper/$name"
done
if [[ ! -e "$STATE_HOME/minimal-whisper/status.json" && -f "$STATE_HOME/openai-whisper/status.json" ]]; then
    cp -p "$STATE_HOME/openai-whisper/status.json" "$STATE_HOME/minimal-whisper/status.json"
fi
if [[ ! -e "$STATE_HOME/minimal-whisper/ptt.log" && -f "$STATE_HOME/whisper-ptt.log" ]]; then
    cp -p "$STATE_HOME/whisper-ptt.log" "$STATE_HOME/minimal-whisper/ptt.log"
fi

control_was_active=0
ptt_was_active=0
systemctl --user is-active --quiet minimal-whisper-control.service && control_was_active=1 || true
systemctl --user is-active --quiet minimal-whisper-ptt.service && ptt_was_active=1 || true
systemctl --user stop openai-whisper-control.service openai-whisper-ptt.service >/dev/null 2>&1 || true
systemctl --user disable openai-whisper-control.service openai-whisper-ptt.service >/dev/null 2>&1 || true

link_file() {
    local source_path="$1" target_path="$2"
    mkdir -p "$(dirname -- "$target_path")"
    ln -sfn "$source_path" "$target_path"
}

link_file "$PROJECT_DIR/src/minimal-whisper-control.py" "$BIN_HOME/minimal-whisper-control.py"
link_file "$PROJECT_DIR/src/minimal-whisper-ptt.py" "$BIN_HOME/minimal-whisper-ptt.py"
link_file "$PROJECT_DIR/bin/minimal-whisper-control" "$BIN_HOME/minimal-whisper-control"
link_file "$PROJECT_DIR/bin/minimal-whisper-ptt" "$BIN_HOME/minimal-whisper-ptt"
link_file "$PROJECT_DIR/systemd/minimal-whisper-control.service" "$UNIT_HOME/minimal-whisper-control.service"
link_file "$PROJECT_DIR/systemd/minimal-whisper-ptt.service" "$UNIT_HOME/minimal-whisper-ptt.service"
link_file "$PROJECT_DIR/desktop/minimal-whisper.desktop" "$APP_HOME/minimal-whisper.desktop"
link_file "$PROJECT_DIR/desktop/minimal-whisper-control.autostart.desktop" "$CONFIG_HOME/autostart/minimal-whisper-control.desktop"
link_file "$PROJECT_DIR/desktop/minimal-whisper-ptt.autostart.desktop" "$CONFIG_HOME/autostart/minimal-whisper-ptt.desktop"
link_file "$PROJECT_DIR/icons/minimal-whisper.svg" "$ICON_HOME/scalable/apps/minimal-whisper.svg"
if [[ ! -e "$ICON_HOME/index.theme" && -r /usr/share/icons/hicolor/index.theme ]]; then
    ln -s /usr/share/icons/hicolor/index.theme "$ICON_HOME/index.theme"
fi

# Remove only the old app's symlinked integration files; leave user data untouched.
for old_path in \
    "$BIN_HOME/whisper-control.py" "$BIN_HOME/whisper-ptt.py" \
    "$UNIT_HOME/openai-whisper-control.service" "$UNIT_HOME/openai-whisper-ptt.service" \
    "$APP_HOME/openai-whisper.desktop" \
    "$CONFIG_HOME/autostart/openai-whisper-control.desktop" \
    "$CONFIG_HOME/autostart/openai-whisper-ptt.desktop" \
    "$ICON_HOME/scalable/apps/openai-whisper.svg"; do
    [[ ! -L "$old_path" ]] || rm -- "$old_path"
done

panel_config="$CONFIG_HOME/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml"
if [[ -f "$panel_config" ]]; then
    sed -i -e 's/value="openai-whisper\.desktop"/value="minimal-whisper.desktop"/g' \
        -e 's/value="OpenAI Whisper"/value="Minimal Whisper"/g' "$panel_config"
fi

systemctl --user daemon-reload
systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE \
    XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME >/dev/null 2>&1 || true
((control_was_active)) && systemctl --user restart minimal-whisper-control.service || true
((ptt_was_active)) && systemctl --user restart minimal-whisper-ptt.service || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_HOME" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f "$ICON_HOME" >/dev/null 2>&1 || true

printf 'Installed Minimal Whisper from %s\n' "$PROJECT_DIR"
printf 'Python environments: UI=%s; listener=%s\n' "$UI_PYTHON" "$PTT_PYTHON"
