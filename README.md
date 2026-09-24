# Minimal Whisper

User-level XFCE/X11 controls for the locally installed OpenAI Whisper push-to-talk setup.

The repository contains the tray/settings UI, the X11 push-to-talk listener, and the user-level systemd and desktop-entry templates. The local Whisper virtual environment, downloaded model weights, runtime state, and personal settings are intentionally kept outside Git.

The settings app auto-saves its model, language, shortcut, and theme. The compact recording overlay can be dragged; its position persists and can be reset to bottom-center from Settings.

## Runtime locations

- Project source: `~/Projects/Minimal Whisper`
- Settings: `~/.config/openai-whisper/settings.json`
- Example defaults: `settings.example.json` (`Meta` represents the Super key in Qt's portable shortcut format).
- Whisper model cache: `~/.cache/whisper`
- Runtime scripts in `~/.local/bin` link to `src/` in this repository.
- User services, desktop launchers, and the icon link to the corresponding project files.
- XFCE's systray hidden-item preference remains in `~/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml` because it belongs to this desktop profile.

## Install or restore links

Run `./install.sh` from this directory. It recreates the runtime symlinks and reloads the user systemd unit definitions without restarting or stopping either service.

The UI uses system Python with PySide6. The listener uses the existing Whisper virtual environment, which provides Whisper and Python-Xlib. Model files and the virtual environment are not stored in this repository.

## Version control

Check changes with `git status` and review them with `git diff`. The initial project snapshot is committed locally; no remote repository is configured.
