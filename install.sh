#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Preserve the existing user configuration while moving it to the new app name.
mkdir -p "$HOME/.config/minimal-whisper" "$HOME/.local/state/minimal-whisper"
if [[ ! -e "$HOME/.config/minimal-whisper/settings.json" && -f "$HOME/.config/openai-whisper/settings.json" ]]; then
    cp -p "$HOME/.config/openai-whisper/settings.json" "$HOME/.config/minimal-whisper/settings.json"
fi
if [[ ! -e "$HOME/.config/minimal-whisper/languages.json" && -f "$HOME/.config/openai-whisper/languages.json" ]]; then
    cp -p "$HOME/.config/openai-whisper/languages.json" "$HOME/.config/minimal-whisper/languages.json"
fi
if [[ ! -e "$HOME/.config/minimal-whisper/models.json" && -f "$HOME/.config/openai-whisper/models.json" ]]; then
    cp -p "$HOME/.config/openai-whisper/models.json" "$HOME/.config/minimal-whisper/models.json"
fi
if [[ ! -e "$HOME/.local/state/minimal-whisper/status.json" && -f "$HOME/.local/state/openai-whisper/status.json" ]]; then
    cp -p "$HOME/.local/state/openai-whisper/status.json" "$HOME/.local/state/minimal-whisper/status.json"
fi
if [[ ! -e "$HOME/.local/state/minimal-whisper/ptt.log" && -f "$HOME/.local/state/whisper-ptt.log" ]]; then
    cp -p "$HOME/.local/state/whisper-ptt.log" "$HOME/.local/state/minimal-whisper/ptt.log"
fi

old_control_active=0
old_ptt_active=0
systemctl --user is-active --quiet openai-whisper-control.service && old_control_active=1 || true
systemctl --user is-active --quiet openai-whisper-ptt.service && old_ptt_active=1 || true
systemctl --user stop openai-whisper-control.service openai-whisper-ptt.service >/dev/null 2>&1 || true

link_file() {
    local source_path="$1"
    local target_path="$2"
    mkdir -p "$(dirname -- "$target_path")"
    ln -sfn "$source_path" "$target_path"
}

link_file "$PROJECT_DIR/src/minimal-whisper-control.py" "$HOME/.local/bin/minimal-whisper-control.py"
link_file "$PROJECT_DIR/src/minimal-whisper-ptt.py" "$HOME/.local/bin/minimal-whisper-ptt.py"
link_file "$PROJECT_DIR/systemd/minimal-whisper-control.service" "$HOME/.config/systemd/user/minimal-whisper-control.service"
link_file "$PROJECT_DIR/systemd/minimal-whisper-ptt.service" "$HOME/.config/systemd/user/minimal-whisper-ptt.service"
link_file "$PROJECT_DIR/desktop/minimal-whisper.desktop" "$HOME/.local/share/applications/minimal-whisper.desktop"
link_file "$PROJECT_DIR/desktop/minimal-whisper-control.autostart.desktop" "$HOME/.config/autostart/minimal-whisper-control.desktop"
link_file "$PROJECT_DIR/desktop/minimal-whisper-ptt.autostart.desktop" "$HOME/.config/autostart/minimal-whisper-ptt.desktop"
link_file "$PROJECT_DIR/icons/minimal-whisper.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/minimal-whisper.svg"

# Remove stale links installed by the former OpenAI Whisper-branded version.
for old_path in \
    "$HOME/.local/bin/whisper-control.py" "$HOME/.local/bin/whisper-ptt.py" \
    "$HOME/.config/systemd/user/openai-whisper-control.service" \
    "$HOME/.config/systemd/user/openai-whisper-ptt.service" \
    "$HOME/.local/share/applications/openai-whisper.desktop" \
    "$HOME/.config/autostart/openai-whisper-control.desktop" \
    "$HOME/.config/autostart/openai-whisper-ptt.desktop" \
    "$HOME/.local/share/icons/hicolor/scalable/apps/openai-whisper.svg"; do
    [[ ! -L "$old_path" ]] || rm -- "$old_path"
done

# Keep XFCE's recent-app entry and hidden status-icon preference attached to the renamed app.
panel_config="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml"
if [[ -f "$panel_config" ]]; then
    sed -i \
        -e 's/value="openai-whisper\.desktop"/value="minimal-whisper.desktop"/g' \
        -e 's/value="OpenAI Whisper"/value="Minimal Whisper"/g' \
        "$panel_config"
fi

systemctl --user daemon-reload
if (( old_control_active )); then
    systemctl --user start minimal-whisper-control.service
fi
if (( old_ptt_active )); then
    systemctl --user start minimal-whisper-ptt.service
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

printf 'Installed links from %s\n' "$PROJECT_DIR"
