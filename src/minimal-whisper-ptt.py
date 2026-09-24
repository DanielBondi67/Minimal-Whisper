#!/home/archbtw/.local/opt/openai-whisper/bin/python
import gc
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from Xlib import X, XK, display, error

CONFIG = Path.home() / '.config/minimal-whisper/settings.json'
LEGACY_CONFIG = Path.home() / '.config/openai-whisper/settings.json'
STATE = Path.home() / '.local/state/minimal-whisper/status.json'
DEFAULTS = {'model': 'base', 'language': 'auto', 'theme': 'dark',
            'shortcut': 'Meta+Ctrl+Y'}


def load_settings():
    try:
        config = CONFIG if CONFIG.exists() else LEGACY_CONFIG
        settings = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    return {**DEFAULTS, **settings}


SETTINGS = load_settings()
MODEL = SETTINGS['model']
LANGUAGE = SETTINGS['language']
PYTHON = Path.home() / '.local/opt/openai-whisper/bin/whisper'
CACHE = Path.home() / '.cache/whisper'
LOG = Path.home() / '.local/state/minimal-whisper/ptt.log'


def log(message):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%F %T")} {message}\n')
        f.flush()


def set_state(state, detail=''):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {'state': state, 'detail': detail, 'model': MODEL,
               'language': LANGUAGE, 'updated': time.time()}
    tmp = STATE.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(payload), encoding='utf-8')
        tmp.replace(STATE)
    except OSError as exc:
        log(f'could not update UI status: {exc!r}')


def notify(title, body):
    # Status is shown in the tray UI and floating recording overlay instead.
    return None


def shortcut_parts(sequence, dpy):
    parts = [part.strip() for part in sequence.split('+') if part.strip()]
    if len(parts) < 2:
        raise ValueError('Shortcut must include at least one modifier and one key')
    modifier_names = {'ctrl': X.ControlMask, 'control': X.ControlMask,
                      'meta': X.Mod4Mask, 'super': X.Mod4Mask,
                      'alt': X.Mod1Mask, 'shift': X.ShiftMask}
    modifiers = 0
    for part in parts[:-1]:
        mask = modifier_names.get(part.lower())
        if mask is None:
            raise ValueError(f'Unsupported shortcut modifier: {part}')
        modifiers |= mask
    key_name = parts[-1]
    aliases = {'esc': 'Escape', 'enter': 'Return', 'backspace': 'BackSpace',
               'pageup': 'Page_Up', 'pagedown': 'Page_Down', 'space': 'space'}
    key_name = aliases.get(key_name.lower(), key_name)
    if len(key_name) == 1:
        key_name = key_name.lower()
    keycode = dpy.keysym_to_keycode(XK.string_to_keysym(key_name))
    if not keycode:
        raise ValueError(f'Could not map shortcut key {parts[-1]!r} in the current X11 layout')
    return keycode, modifiers


class Dictation:
    def __init__(self):
        self.temp = None
        self.wav = None
        self.recorder = None
        self.pressed = False

    def start(self):
        self.temp = tempfile.TemporaryDirectory(prefix='minimal-whisper-')
        self.wav = Path(self.temp.name) / 'recording.wav'
        logfile = LOG.open('a', encoding='utf-8')
        try:
            self.recorder = subprocess.Popen(
                ['pw-record', '--rate', '16000', '--channels', '1', '--format', 's16', str(self.wav)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=logfile,
            )
        finally:
            logfile.close()
        time.sleep(0.15)
        if self.recorder.poll() is not None:
            raise RuntimeError('pw-record exited before capture started; see ~/.local/state/minimal-whisper/ptt.log')
        self.pressed = True
        set_state('recording')
        log(f'recording started model={MODEL}')
        notify('Recording', 'Release the shortcut to transcribe')

    def stop_and_transcribe(self):
        proc, wav, temp = self.recorder, self.wav, self.temp
        self.recorder = self.wav = self.temp = None
        self.pressed = False
        if proc is None:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=3)
        if not wav.exists() or wav.stat().st_size < 2048:
            notify('No recording', 'No audio was captured. Check the default microphone source.')
            log('recording was empty or too short')
            temp.cleanup()
            return

        notify('Transcribing', 'Speech is being transcribed locally')
        set_state('transcribing')
        command = [str(PYTHON), str(wav), '--model', MODEL, '--model_dir', str(CACHE),
                   '--device', 'cpu', '--fp16', 'False', '--verbose', 'False',
                   '--output_format', 'txt', '--output_dir', temp.name]
        if LANGUAGE and LANGUAGE != 'auto':
            command.extend(['--language', LANGUAGE])
        try:
            with LOG.open('a', encoding='utf-8') as f:
                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                        stdout=f, stderr=f, check=False)
            output = wav.with_suffix('.txt')
            text = output.read_text(encoding='utf-8').strip() if output.exists() else ''
            if result.returncode != 0:
                raise RuntimeError(f'Whisper exited with status {result.returncode}; see ~/.local/state/minimal-whisper/ptt.log')
            if not text:
                notify('No speech detected', 'Try again, speaking clearly into the default microphone.')
                log('transcription completed with no text')
                set_state('listening')
                return
            subprocess.run(['xclip', '-selection', 'clipboard'], input=text, text=True, check=True)
            time.sleep(0.15)
            subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+v'], check=True)
            log(f'transcription pasted ({len(text)} characters)')
            notify('Done', 'Transcription pasted')
            set_state('listening')
        except Exception as exc:
            log(f'error: {exc!r}')
            notify('Whisper error', str(exc))
            set_state('error', str(exc))
        finally:
            temp.cleanup()
            gc.collect()
            if STATE.exists():
                try:
                    current = json.loads(STATE.read_text(encoding='utf-8'))
                    if current.get('state') == 'transcribing':
                        set_state('listening')
                except (OSError, ValueError):
                    set_state('listening')

    def cancel(self):
        proc, temp = self.recorder, self.temp
        self.recorder = self.wav = self.temp = None
        self.pressed = False
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=2)
        if temp is not None:
            temp.cleanup()
        set_state('stopped')
        log('recording cancelled during shutdown')


def main():
    if MODEL not in ('base', 'base.en'):
        print('WHISPER_PTT_MODEL must be base or base.en', file=sys.stderr)
        return 2
    dpy = display.Display()
    root = dpy.screen().root
    keycode, base_mods = shortcut_parts(SETTINGS['shortcut'], dpy)
    variants = (base_mods, base_mods | X.LockMask,
                base_mods | X.Mod2Mask, base_mods | X.LockMask | X.Mod2Mask)
    try:
        for mods in variants:
            root.grab_key(keycode, mods, False, X.GrabModeAsync, X.GrabModeAsync)
        dpy.sync()
    except error.BadAccess as exc:
        raise RuntimeError(f'{SETTINGS["shortcut"]} is already grabbed by another application') from exc

    log(f'listening on {SETTINGS["shortcut"]} (layout keycode {keycode}), model={MODEL}')
    set_state('listening')
    notify('Ready', f'Hold {SETTINGS["shortcut"]} to dictate')
    dictation = Dictation()

    def handle_term(_signum, _frame):
        dictation.cancel()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_term)
    try:
        while True:
            event = dpy.next_event()
            if event.type == X.KeyPress and event.detail == keycode:
                if not dictation.pressed:
                    try:
                        dictation.start()
                    except Exception as exc:
                        log(f'recording error: {exc!r}')
                        set_state('error', str(exc))
                        notify('Recording error', str(exc))
            elif event.type == X.KeyRelease and event.detail == keycode and dictation.pressed:
                # X11 synthesizes release/press pairs for autorepeat; ignore those while Y remains down.
                keymap = dpy.query_keymap()
                if keymap[keycode // 8] & (1 << (keycode % 8)):
                    continue
                dictation.stop_and_transcribe()
    except KeyboardInterrupt:
        pass
    finally:
        for mods in variants:
            root.ungrab_key(keycode, mods)
        dpy.sync()
        if dictation.recorder is not None:
            dictation.stop_and_transcribe()
        dpy.close()
        set_state('stopped')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f'fatal: {exc!r}')
        set_state('error', str(exc))
        notify('Shortcut failed', str(exc))
        raise
