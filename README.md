# Minimal Whisper

Minimal Whisper is a user-level XFCE/X11 app for offline voice typing with OpenAI Whisper.

The repository contains the tray/settings UI, the X11 push-to-talk listener, and the user-level systemd and desktop-entry templates. The Whisper Python environment and model weights, runtime state, and personal settings are intentionally kept outside Git.

The settings app auto-saves its model, language, shortcut, and theme. The compact recording overlay can be dragged; its position persists and can be reset to bottom-center from Settings.

Available transcription models include Tiny (the smallest multilingual Whisper model), Base (multilingual), and Base English. Tiny must be downloaded once before first use; model files are stored in `~/.cache/whisper`.

The language selector includes Automatic, English, German, Japanese, and Russian. To add or rename choices, edit `config/languages.json` in the project. It is a JSON list of labels and Whisper language codes; keep an `auto` entry for automatic detection. You can also create `~/.config/minimal-whisper/languages.json` in the same format to customize choices without editing the project. That user file takes precedence over the bundled list. The `base.en` model only supports English, so the settings app switches the language back to English when that model is selected.

## Runtime locations

- Project source: `~/Projects/Minimal Whisper`
- Settings: `~/.config/minimal-whisper/settings.json`
- Example defaults: `settings.example.json` (`Meta` represents the Super key in Qt's portable shortcut format).
- Whisper model cache: `~/.cache/whisper`
- Language choices: `config/languages.json` (optional user override: `~/.config/minimal-whisper/languages.json`)
- Runtime scripts in `~/.local/bin` link to `src/` in this repository.
- User services, desktop launchers, and the icon link to the corresponding project files.
- XFCE's systray hidden-item preference remains in `~/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml` because it belongs to this desktop profile.

## Install or restore links

Run `./install.sh` from this directory. It updates the runtime links and user systemd units, migrates existing settings without deleting the old copies, and carries forward any services that were running.

The UI uses system Python with PySide6. The listener uses the existing Whisper virtual environment at `~/.local/opt/openai-whisper`, which provides Whisper and Python-Xlib. That path and the Python package retain the engine’s name; model files and the virtual environment are not stored in this repository.

## Version control

Check changes with `git status` and review them with `git diff`. The initial project snapshot is committed locally; no remote repository is configured.
