#!/usr/bin/env python3
import gc
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from Xlib import X, XK, display, error

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config'))
STATE_HOME = Path(os.environ.get('XDG_STATE_HOME', HOME / '.local/state'))
CACHE_HOME = Path(os.environ.get('XDG_CACHE_HOME', HOME / '.cache'))
CONFIG = CONFIG_HOME / 'minimal-whisper/settings.json'
LEGACY_CONFIG = CONFIG_HOME / 'openai-whisper/settings.json'
STATE = STATE_HOME / 'minimal-whisper/status.json'
MODEL_CONFIG = CONFIG_HOME / 'minimal-whisper/models.json'
LEGACY_MODEL_CONFIG = CONFIG_HOME / 'openai-whisper/models.json'
BUILTIN_MODEL_CONFIG = Path(__file__).resolve().parent.parent / 'config/models.json'
DEFAULTS = {'model': 'base', 'language': 'auto', 'theme': 'dark',
            'shortcut': 'Meta+Ctrl+Y', 'scale_percent': 100,
            'audio_source': ''}


def load_settings():
    try:
        config = CONFIG if CONFIG.exists() else LEGACY_CONFIG
        settings = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    return {**DEFAULTS, **settings}


def load_models():
    for path in (MODEL_CONFIG, LEGACY_MODEL_CONFIG, BUILTIN_MODEL_CONFIG):
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
            models = {str(item['model']) for item in entries
                      if isinstance(item, dict) and item.get('model')}
            if models:
                return models
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return {'small', 'base', 'base.en'}


SETTINGS = load_settings()
MODEL = SETTINGS['model']
LANGUAGE = SETTINGS['language']
AVAILABLE_MODELS = load_models()
CACHE = CACHE_HOME / 'whisper'
LOG = STATE_HOME / 'minimal-whisper/ptt.log'
MODEL_METADATA_SCRIPT = '''
import json, os, urllib.parse, whisper
print(json.dumps({name: {'file': os.path.basename(urllib.parse.urlsplit(url).path),
                         'sha256': url.rstrip('/').split('/')[-2]}
                  for name, url in whisper._MODELS.items()}))
'''


def load_model_metadata():
    try:
        result = subprocess.run([sys.executable, '-c', MODEL_METADATA_SCRIPT],
                                capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f'Could not read the installed Whisper model catalog: {exc}') from exc


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
    def __init__(self, model_metadata=None):
        self.model_metadata = model_metadata
        self.verified_checkpoint = None
        self.temp = None
        self.wav = None
        self.recorder = None
        self.pressed = False

    def start(self):
        self.temp = tempfile.TemporaryDirectory(prefix='minimal-whisper-')
        self.wav = Path(self.temp.name) / 'recording.wav'
        logfile = LOG.open('a', encoding='utf-8')
        try:
            command = ['pw-record']
            source = SETTINGS.get('audio_source')
            if source:
                command.extend(['--target', source])
            command.extend(['--rate', '16000', '--channels', '1', '--format', 's16', str(self.wav)])
            self.recorder = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=logfile,
            )
        finally:
            logfile.close()
        time.sleep(0.15)
        if self.recorder.poll() is not None:
            raise RuntimeError(f'pw-record exited before capture started; see {LOG}')
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
        try:
            transcription_model = self.verify_model_is_downloaded()
        except RuntimeError as exc:
            message = str(exc)
            log(message)
            set_state('error', message)
            temp.cleanup()
            return

        command = [sys.executable, '-m', 'whisper', str(wav), '--model', transcription_model,
                   '--model_dir', str(CACHE),
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
                raise RuntimeError(f'Whisper exited with status {result.returncode}; see {LOG}')
            if not text:
                notify('No speech detected', 'Try again, speaking clearly into the default microphone.')
                log('transcription completed with no text')
                set_state('listening')
                return
            subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '0', '--', text],
                           check=True)
            log(f'transcription typed ({len(text)} characters)')
            notify('Done', 'Transcription inserted')
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

    def verify_model_is_downloaded(self):
        local_model = Path(MODEL)
        if local_model.is_absolute():
            if not local_model.is_file() or local_model.stat().st_size == 0:
                raise RuntimeError(f'Local model file is missing or empty: {local_model}')
            return str(local_model)
        if self.model_metadata is None:
            self.model_metadata = load_model_metadata()
        info = self.model_metadata.get(MODEL)
        if not info:
            raise RuntimeError(f'Model {MODEL!r} is not supported by the installed Whisper runtime.')
        checkpoint = CACHE / info['file']
        if not checkpoint.is_file():
            raise RuntimeError(
                f'Model {MODEL!r} is not downloaded. Open Settings and click Download model first.')
        stat = checkpoint.stat()
        signature = (str(checkpoint), stat.st_size, stat.st_mtime_ns)
        if signature == self.verified_checkpoint:
            return str(checkpoint)
        digest = hashlib.sha256()
        with checkpoint.open('rb') as model_file:
            for chunk in iter(lambda: model_file.read(8 * 1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != info['sha256']:
            raise RuntimeError(
                f'Model {MODEL!r} is incomplete or corrupt. Use Download model in Settings to retry.')
        self.verified_checkpoint = signature
        return str(checkpoint)

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
    if os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        print('Minimal Whisper supports X11 only; Wayland is not supported.', file=sys.stderr)
        return 2
    if MODEL not in AVAILABLE_MODELS:
        print(f'Model {MODEL!r} is not listed in {MODEL_CONFIG} or config/models.json',
              file=sys.stderr)
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
