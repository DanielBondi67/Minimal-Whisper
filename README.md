# Minimal Whisper

<p align="center">
  <img src="icons/minimal-whisper.svg" alt="Minimal Whisper logo" width="128" height="128">
</p>

Offline voice typing with OpenAI Whisper. The app includes settings, model downloads, microphone selection with a live level meter, a tray menu, and an X11 push-to-talk shortcut.

## Support

Supported: Arch Linux, XFCE, X11, PipeWire with WirePlumber and its PulseAudio-compatible `pactl` interface, and a user systemd session. Wayland is not supported.

## Dependencies

Arch packages: `python`, `python-pip`, `pipewire`, `pipewire-audio`, `pipewire-pulse`, `wireplumber`, `xdotool`, and `systemd`; `pavucontrol` is optional. Python packages: `PySide6`, `openai-whisper`, and `python-xlib` (listed in `requirements.txt`).

Install the Arch dependencies, then create the app’s Python environment and install its Python packages:

```bash
sudo pacman -S python python-pip pipewire pipewire-audio pipewire-pulse wireplumber xdotool
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper"
python -m venv "${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv"
"${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv/bin/pip" install -r requirements.txt
./install.sh
```

Start **Minimal Whisper** from the application finder. In Settings, choose an input device and confirm the live meter responds, choose a model, and download it while online. Hold the configured shortcut to record and release it to transcribe. `pactl`, `pw-record`, `xdotool`, Python with PySide6, and a Whisper/Xlib Python environment are checked by the installer.

## Models and languages

The model list is `config/models.json`; a user override can be placed at `${XDG_CONFIG_HOME:-$HOME/.config}/minimal-whisper/models.json`. The app only offers model IDs recognized by the installed `openai-whisper` runtime (or an existing local checkpoint path). To check canonical IDs, run:

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv/bin/python" -c 'import whisper; print("\n".join(whisper.available_models()))'
```

Use an exact returned ID in the JSON `model` field. The UI verifies cached model checksums and marks installed models with a tick; select an installed model and click **Uninstall model** to remove its cache file. **Check for new models** scans the installed Whisper package; update `openai-whisper` first to discover IDs added by a newer release. New IDs are saved to the user model list. Downloads only begin when you click **Download model**. Language options are in `config/languages.json` and can be overridden at `${XDG_CONFIG_HOME:-$HOME/.config}/minimal-whisper/languages.json`.

## Remove

Run `./uninstall.sh`. It removes Minimal Whisper’s desktop, service, icon, and command links. It preserves settings, logs, model downloads, Python environment, and project files.
