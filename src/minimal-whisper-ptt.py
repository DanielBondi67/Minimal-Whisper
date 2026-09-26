#!/usr/bin/env python3
import hashlib
import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_backend import AudioBackend
from audio_waveform import pcm_waveform_levels, wav_data_offset
from dictation_operation import DictationOperation, OperationCancelled
from shortcut_cancel import ShortcutReleaseGate
from text_output import normalize_transcription

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config'))
STATE_HOME = Path(os.environ.get('XDG_STATE_HOME', HOME / '.local/state'))
CACHE_HOME = Path(os.environ.get('XDG_CACHE_HOME', HOME / '.cache'))
CONFIG = CONFIG_HOME / 'minimal-whisper/settings.json'
LEGACY_CONFIG = CONFIG_HOME / 'openai-whisper/settings.json'
STATE = STATE_HOME / 'minimal-whisper/status.json'
PTT_PID = STATE_HOME / 'minimal-whisper/ptt.pid'
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
KEYBOARD_PORTAL = None
_STATE_LOCK = threading.RLock()
_STATE_REVISION = 0

def log(message):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%F %T")} {message}\n')
        f.flush()


def set_state(state, detail='', waveform=None, operation_id=None):
    global _STATE_REVISION
    STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {'state': state, 'detail': detail, 'model': MODEL,
               'language': LANGUAGE, 'updated': time.time(), 'operation_id': operation_id}
    if waveform is not None:
        payload['waveform'] = waveform
    with _STATE_LOCK:
        _STATE_REVISION += 1
        revision = _STATE_REVISION
        tmp = STATE.with_name(
            f'.{STATE.name}.{os.getpid()}.{threading.get_ident()}.{revision}.tmp')
        try:
            tmp.write_text(json.dumps(payload), encoding='utf-8')
            tmp.replace(STATE)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            log(f'could not update UI status: {exc!r}')


def notify(title, body):
    # Status is shown in the tray UI and floating recording overlay instead.
    return None


def insert_transcription(text, dictation, operation):
    global KEYBOARD_PORTAL

    def cancelled():
        return (dictation.cancel_pending.is_set()
                or not dictation.is_current(operation))

    if cancelled():
        return None
    text = normalize_transcription(text)
    is_wayland = (os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
                  or bool(os.environ.get('WAYLAND_DISPLAY')))
    if is_wayland:
        from PySide6.QtGui import QGuiApplication
        portal_error = ''
        try:
            if KEYBOARD_PORTAL is None:
                from wayland_portal import RemoteKeyboardPortal
                KEYBOARD_PORTAL = RemoteKeyboardPortal()
            if KEYBOARD_PORTAL.type_text(text, cancelled=cancelled):
                return 'typed through the Remote Desktop portal'
            portal_error = KEYBOARD_PORTAL.error or ''
        except Exception as exc:
            portal_error = str(exc)
        if cancelled():
            return None
        QGuiApplication.clipboard().setText(text)
        operation.clipboard_text = text
        if cancelled():
            return None
        detail = 'Copied transcription to clipboard; paste it yourself.'
        if portal_error:
            detail += f' Keyboard portal: {portal_error}'
        return detail
    process = operation.spawn(
        ['xdotool', 'type', '--clearmodifiers', '--delay', '0', '--', text],
        stdin=subprocess.DEVNULL)
    while process.poll() is None:
        if dictation.cancel_pending.wait(0.01) or cancelled():
            dictation.cancel_current()
            return None
    if cancelled():
        return None
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    return 'typed through the X11 backend'


def shortcut_parts(sequence, dpy):
    from Xlib import X, XK
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
        self.active = None
        self.cancel_pending = threading.Event()
        self._lock = threading.RLock()
        self._completed = queue.SimpleQueue()
        self._delivering = False

    @property
    def pressed(self):
        return self.active is not None and self.active.stage == 'recording'

    def is_current(self, operation):
        return self.active is operation and not operation.cancelled.is_set()

    def publish(self, operation, state, detail='', waveform=None):
        with self._lock:
            if not self.is_current(operation):
                return False
            set_state(state, detail, waveform, operation.id)
            return True

    def start(self):
        # Repeated presses while transcribing never create another recording.
        if self.active is not None or self.cancel_pending.is_set():
            return
        operation = DictationOperation()
        with self._lock:
            self.active = operation
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            command = AudioBackend.detect().recording_command(
                SETTINGS.get('audio_source'), operation.wav, 16000, 1)
            with LOG.open('a', encoding='utf-8') as logfile:
                operation.recorder = operation.spawn(
                    command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=logfile)
            self.publish(operation, 'recording', waveform=[0.0] * 17)
            threading.Thread(target=self.monitor_waveform,
                             args=(operation,), daemon=True).start()
            log(f'recording started operation={operation.id} model={MODEL}')
        except Exception:
            self._finish(operation)
            raise

    def monitor_waveform(self, operation):
        offset = None
        while not operation.waveform_stop.wait(0.05):
            if not self.is_current(operation):
                return
            if operation.recorder.poll() is not None:
                with self._lock:
                    if not operation.waveform_stop.is_set():
                        self._complete(operation, error=RuntimeError(
                            f'Recorder exited before capture stopped; see {LOG}'))
                return
            try:
                with operation.wav.open('rb') as recording:
                    if offset is None:
                        offset = wav_data_offset(recording)
                    if offset is None:
                        continue
                    recording.seek(offset)
                    data = recording.read()
                offset += len(data)
                data = data[-3200:]
                usable = len(data) & ~1
                levels = pcm_waveform_levels(data[:usable]) if usable else [0.0] * 17
                # Serialize the final recording update with the transition to
                # transcribing, so a waveform update cannot revive the old phase.
                with self._lock:
                    if not operation.waveform_stop.is_set():
                        self.publish(operation, 'recording', waveform=levels)
            except FileNotFoundError:
                continue
            except OSError as exc:
                log(f'could not read live recording waveform: {exc!r}')

    def stop_and_transcribe(self):
        with self._lock:
            operation = self.active
            if operation is None or operation.stage != 'recording':
                return
            operation.stage = 'transcribing'
            operation.waveform_stop.set()
            self.publish(operation, 'transcribing')
        operation.worker = threading.Thread(
            target=self._transcribe, args=(operation,), daemon=True)
        operation.worker.start()

    def _transcription_command(self, operation, model):
        command = [sys.executable, '-m', 'whisper', str(operation.wav), '--model', model,
                   '--model_dir', str(CACHE), '--device', 'cpu', '--fp16', 'False',
                   '--verbose', 'False', '--output_format', 'txt',
                   '--output_dir', str(operation.directory)]
        if LANGUAGE and LANGUAGE != 'auto':
            command.extend(['--language', LANGUAGE])
        return command

    def _transcribe(self, operation):
        try:
            operation.check()
            try:
                operation.recorder.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            operation.recorder.wait(timeout=8)
            operation.check()
            if not operation.wav.exists() or operation.wav.stat().st_size < 2048:
                self._complete(operation, text='')
                return
            model = self.verify_model_is_downloaded(operation)
            operation.check()
            with LOG.open('a', encoding='utf-8') as logfile:
                process = operation.spawn(
                    self._transcription_command(operation, model),
                    stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile)
                return_code = process.wait()
            operation.check()
            if return_code != 0:
                raise RuntimeError(f'Whisper exited with status {return_code}; see {LOG}')
            output = operation.wav.with_suffix('.txt')
            text = normalize_transcription(
                output.read_text(encoding='utf-8') if output.exists() else '')
            self._complete(operation, text=text)
        except OperationCancelled:
            return
        except Exception as exc:
            self._complete(operation, error=exc)

    def _complete(self, operation, text='', error=None):
        with self._lock:
            if self.is_current(operation):
                self._completed.put((operation, text, error))

    def poll(self):
        # Called by the input event loop, never by the inference thread. Signal
        # handlers only set the event, avoiding re-entrant subprocess/file work.
        if self.cancel_pending.is_set():
            self.cancel_current()
            return
        # Portal permission dialogs run a nested Qt event loop. It may process
        # cancellation and new recordings, but must not start nested delivery.
        if self._delivering:
            return
        while not self._completed.empty():
            operation, text, error = self._completed.get()
            if not self.is_current(operation):
                continue
            if self.cancel_pending.is_set():
                self.cancel_current()
                return
            if error is not None:
                self._finish(operation)
                set_state('error', str(error))
                log(f'operation={operation.id} error: {error!r}')
                continue
            detail = ''
            try:
                if text:
                    self.publish(operation, 'delivering')
                    self._delivering = True
                    try:
                        detail = insert_transcription(text, self, operation)
                    finally:
                        self._delivering = False
                    if not self.is_current(operation):
                        return
                    if detail is None or self.cancel_pending.is_set():
                        self.cancel_current()
                        return
                    log(f'operation={operation.id} transcription delivered ({len(text)} characters)')
                else:
                    log(f'operation={operation.id} transcription completed with no text')
                self._finish(operation)
                set_state('listening', detail if detail.startswith('Copied') else '')
            except Exception as exc:
                if not self.is_current(operation):
                    return
                if self.cancel_pending.is_set():
                    self.cancel_current()
                    return
                self._finish(operation)
                set_state('error', str(exc))
                log(f'operation={operation.id} delivery error: {exc!r}')

    def _finish(self, operation):
        with self._lock:
            if self.active is operation:
                self.active = None
            operation.close()

    def verify_model_is_downloaded(self, operation):
        local_model = Path(MODEL)
        if local_model.is_absolute():
            if not local_model.is_file() or local_model.stat().st_size == 0:
                raise RuntimeError(f'Local model file is missing or empty: {local_model}')
            return str(local_model)
        if self.model_metadata is None:
            process = operation.spawn(
                [sys.executable, '-c', MODEL_METADATA_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output, errors = process.communicate(timeout=30)
            operation.check()
            if process.returncode:
                raise RuntimeError(f'Could not read the Whisper model catalog: {errors}')
            self.model_metadata = json.loads(output)
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
                operation.check()
                digest.update(chunk)
        operation.check()
        if digest.hexdigest() != info['sha256']:
            raise RuntimeError(
                f'Model {MODEL!r} is incomplete or corrupt. Use Download model in Settings to retry.')
        self.verified_checkpoint = signature
        return str(checkpoint)

    def cancel_current(self):
        with self._lock:
            operation = self.active
            self.active = None
            if operation is not None:
                operation.close()
                if operation.clipboard_text is not None:
                    from PySide6.QtGui import QGuiApplication
                    clipboard = QGuiApplication.clipboard()
                    if clipboard.text() == operation.clipboard_text:
                        clipboard.clear()
                log(f'operation={operation.id} cancelled; audio and output discarded')
            while not self._completed.empty():
                self._completed.get()
            self.cancel_pending.clear()
            set_state('listening', 'Cancelled; recording discarded.')

    def cancel(self):
        self.cancel_current()
        set_state('stopped')


def main():
    if os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland' or os.environ.get('WAYLAND_DISPLAY'):
        return main_wayland()
    if MODEL not in AVAILABLE_MODELS:
        print(f'Model {MODEL!r} is not listed in {MODEL_CONFIG} or config/models.json',
              file=sys.stderr)
        return 2
    from Xlib import X, display, error
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
    PTT_PID.parent.mkdir(parents=True, exist_ok=True)
    PTT_PID.write_text(f'{os.getpid()}\n', encoding='ascii')
    set_state('listening')
    notify('Ready', f'Hold {SETTINGS["shortcut"]} to dictate')
    dictation = Dictation()
    shortcut_gate = ShortcutReleaseGate()

    stopping = threading.Event()

    def handle_term(_signum, _frame):
        stopping.set()
        dictation.cancel_pending.set()

    def handle_cancel(_signum, _frame):
        dictation.cancel_pending.set()

    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGUSR1, handle_cancel)
    try:
        while not stopping.is_set():
            if dictation.cancel_pending.is_set():
                shortcut_gate.cancel_held_shortcut(shortcut_gate.held)
            dictation.poll()
            if not dpy.pending_events():
                select.select([dpy.fileno()], [], [], 0.02)
                continue
            event = dpy.next_event()
            if event.type == X.KeyPress and event.detail == keycode:
                keymap = dpy.query_keymap()
                physically_down = bool(keymap[keycode // 8] & (1 << (keycode % 8)))
                if (not physically_down or event.state & base_mods != base_mods
                        or not shortcut_gate.press()):
                    continue
                try:
                    dictation.start()
                except Exception as exc:
                    log(f'recording error: {exc!r}')
                    set_state('error', str(exc))
            elif event.type == X.KeyRelease and event.detail == keycode:
                keymap = dpy.query_keymap()
                physically_down = bool(keymap[keycode // 8] & (1 << (keycode % 8)))
                if shortcut_gate.release(physically_down) and dictation.pressed:
                    dictation.stop_and_transcribe()
    except KeyboardInterrupt:
        pass
    finally:
        dictation.cancel()
        for mods in variants:
            root.ungrab_key(keycode, mods)
        dpy.sync()
        dpy.close()
        PTT_PID.unlink(missing_ok=True)

    return 0


def main_wayland():
    if MODEL not in AVAILABLE_MODELS:
        print(f'Model {MODEL!r} is not listed in {MODEL_CONFIG} or config/models.json',
              file=sys.stderr)
        return 2
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QTimer
    from wayland_portal import GlobalShortcutPortal

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.setApplicationName('Minimal Whisper')
    try:
        shortcuts = GlobalShortcutPortal(SETTINGS['shortcut'])
    except Exception as exc:
        message = (f'Could not register the push-to-talk shortcut with the XDG Global '
                   f'Shortcuts portal: {exc}')
        print(message, file=sys.stderr)
        log(message)
        set_state('error', message)
        return 2
    dictation = Dictation()
    shortcut_gate = ShortcutReleaseGate()

    def pressed():
        tick()
        if not shortcut_gate.press():
            return
        if not dictation.pressed:
            try:
                dictation.start()
            except Exception as exc:
                log(f'recording error: {exc!r}')
                set_state('error', str(exc))

    def released():
        tick()
        if not shortcut_gate.release():
            return
        if dictation.pressed:
            dictation.stop_and_transcribe()

    shortcuts.set_callbacks(pressed, released)
    PTT_PID.parent.mkdir(parents=True, exist_ok=True)
    PTT_PID.write_text(f'{os.getpid()}\n', encoding='ascii')
    set_state('listening', 'Wayland shortcut managed by XDG portal')
    log(f'listening through XDG Global Shortcuts portal: {SETTINGS["shortcut"]}')

    stopping = threading.Event()

    def handle_term(_signum, _frame):
        stopping.set()
        dictation.cancel_pending.set()

    def handle_cancel(_signum, _frame):
        dictation.cancel_pending.set()

    def tick():
        if dictation.cancel_pending.is_set():
            shortcut_gate.cancel_held_shortcut(shortcut_gate.held)
        dictation.poll()
        if stopping.is_set():
            app.quit()

    # Keep Python signal handling and result delivery responsive inside Qt.
    timer = QTimer(app)
    timer.timeout.connect(tick)
    timer.start(20)
    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGUSR1, handle_cancel)
    try:
        return app.exec()
    finally:
        dictation.cancel()
        PTT_PID.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f'fatal: {exc!r}')
        set_state('error', str(exc))
        notify('Shortcut failed', str(exc))
        raise
