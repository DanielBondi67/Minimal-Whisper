#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="$HOME/.local/bin"
UNIT_HOME="$CONFIG_HOME/systemd/user"
APP_HOME="$DATA_HOME/applications"
ICON_HOME="$DATA_HOME/icons/hicolor"

systemctl --user stop minimal-whisper-control.service minimal-whisper-ptt.service >/dev/null 2>&1 || true
systemctl --user disable minimal-whisper-control.service minimal-whisper-ptt.service >/dev/null 2>&1 || true

remove_project_link() {
    local path="$1"
    if [[ -L "$path" ]]; then
        local target
        target="$(readlink -f -- "$path" 2>/dev/null || true)"
        if [[ "$target" == "$PROJECT_DIR/"* ]]; then
            rm -- "$path"
        fi
    fi
}

for name in minimal-whisper-control minimal-whisper-ptt \
            minimal-whisper-control.py minimal-whisper-ptt.py; do
    remove_project_link "$BIN_HOME/$name"
done
for name in minimal-whisper-control.service minimal-whisper-ptt.service; do
    remove_project_link "$UNIT_HOME/$name"
done
remove_project_link "$APP_HOME/minimal-whisper.desktop"
remove_project_link "$CONFIG_HOME/autostart/minimal-whisper-control.desktop"
remove_project_link "$CONFIG_HOME/autostart/minimal-whisper-ptt.desktop"
remove_project_link "$ICON_HOME/scalable/apps/minimal-whisper.svg"

systemctl --user daemon-reload >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_HOME" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f "$ICON_HOME" >/dev/null 2>&1 || true

cat <<'EOF'
Minimal Whisper integration has been removed.
Settings, Whisper model files, the Python environment, logs, and the project directory were preserved.
EOF
