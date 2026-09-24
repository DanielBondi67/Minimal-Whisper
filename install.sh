#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

link_file() {
    local source_path="$1"
    local target_path="$2"
    mkdir -p "$(dirname -- "$target_path")"
    ln -sfn "$source_path" "$target_path"
}

link_file "$PROJECT_DIR/src/whisper-control.py" "$HOME/.local/bin/whisper-control.py"
link_file "$PROJECT_DIR/src/whisper-ptt.py" "$HOME/.local/bin/whisper-ptt.py"
link_file "$PROJECT_DIR/systemd/openai-whisper-control.service" "$HOME/.config/systemd/user/openai-whisper-control.service"
link_file "$PROJECT_DIR/systemd/openai-whisper-ptt.service" "$HOME/.config/systemd/user/openai-whisper-ptt.service"
link_file "$PROJECT_DIR/desktop/openai-whisper.desktop" "$HOME/.local/share/applications/openai-whisper.desktop"
link_file "$PROJECT_DIR/desktop/openai-whisper-control.autostart.desktop" "$HOME/.config/autostart/openai-whisper-control.desktop"
link_file "$PROJECT_DIR/desktop/openai-whisper-ptt.autostart.desktop" "$HOME/.config/autostart/openai-whisper-ptt.desktop"
link_file "$PROJECT_DIR/icons/openai-whisper.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/openai-whisper.svg"

systemctl --user daemon-reload
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

printf 'Installed links from %s\n' "$PROJECT_DIR"
