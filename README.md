# Minimal Whisper

<p align="center">
  <img src="icons/minimal-whisper.svg" alt="Minimal Whisper logo" width="128" height="128">
</p>

Offline voice typing powered by [OpenAI Whisper](https://github.com/openai/whisper). Minimal Whisper includes model downloads, microphone selection and a live level meter, an optional tray icon, and push-to-talk.

## Support

Minimal Whisper targets Linux graphical sessions, across distributions and desktop environments, when the session provides the required interfaces:

| Session | Shortcut and text output | Overlay and tray |
| --- | --- | --- |
| X11 | Global shortcut and text insertion through Xlib/`xdotool` | Movable overlay; tray is optional |
| Wayland | XDG Global Shortcuts portal for shortcut press/release and Remote Desktop portal for keyboard input. Portal backend support and user permission are required. If keyboard input is unavailable or declined, the transcription is copied to the clipboard. | Compositor controls window placement; manual positioning is unavailable. Tray is optional. |

Audio capture supports PipeWire (`pw-record`) and PulseAudio (`parecord`). Startup uses `systemd --user` when available, with XDG desktop autostart as a fallback. The app uses XDG configuration, data, and state paths. A desktop without a compatible Wayland portal can use the X11 session if available; other session and audio backends are not currently supported.

## Dependencies

Dependencies: Python 3, PySide6, [OpenAI Whisper](https://github.com/openai/whisper) (`openai-whisper`), `python-xlib`, `ffmpeg`, and one recording client (`pw-record` for PipeWire or `parecord` for PulseAudio). X11 additionally needs `xdotool`. Wayland additionally needs PySide6.QtDBus, `libdbus-1`, and a portal backend with Global Shortcuts and Remote Desktop support. `pactl` is optional and enables friendly source names; without it, PipeWire uses `wpctl` for source discovery. `pavucontrol` and a user systemd manager are optional.

For Arch with PipeWire/X11, install the system dependencies (use PulseAudio equivalents for audio, or add your desktop's compatible portal backend for Wayland), then clone this repository and install the Python environment:

```bash
sudo pacman -S python python-pip python-virtualenv ffmpeg pipewire pipewire-audio pipewire-pulse wireplumber xdotool
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper"
python -m venv "${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv"
"${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv/bin/pip" install -r requirements.txt
./install.sh
```

Run `./install.sh`, then start **Minimal Whisper** from the application finder. Choose a microphone and model in Settings, download the model while online, then hold the shortcut to record and release it to transcribe. Wayland shortcut and keyboard support depends on the desktop’s portal backend and its permission prompts.

## Models and languages

The model list is `config/models.json`; a user override can be placed at `${XDG_CONFIG_HOME:-$HOME/.config}/minimal-whisper/models.json`. The app only offers model IDs recognized by the installed `openai-whisper` runtime (or an existing local checkpoint path). To check canonical IDs, run:

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv/bin/python" -c 'import whisper; print("\n".join(whisper.available_models()))'
```

Use an exact returned ID in the JSON `model` field. The UI verifies cached model checksums and marks installed models with a tick; select an installed model and click **Uninstall model** to remove its cache file. **Check for new models** scans the installed Whisper package; update `openai-whisper` first to discover IDs added by a newer release. New IDs are saved to the user model list. Downloads only begin when you click **Download model**. Language options are in `config/languages.json` and can be overridden at `${XDG_CONFIG_HOME:-$HOME/.config}/minimal-whisper/languages.json`.

## Remove

Run `./uninstall.sh`. It removes Minimal Whisper’s desktop, service, icon, and command links. It preserves settings, logs, model downloads, Python environment, and project files.

## License

Minimal Whisper is distributed under the [MIT License](LICENSE).
