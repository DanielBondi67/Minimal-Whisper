#!/usr/bin/env python3
import json
import math
import os
import signal
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_backend import AudioBackend, AudioBackendError

from PySide6.QtCore import Qt, QTimer, QRectF, QPoint, QPointF, QProcess, QUrl
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket, QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QKeySequenceEdit, QMainWindow, QMenu, QPushButton, QSystemTrayIcon,
    QMessageBox, QProgressBar, QVBoxLayout, QWidget,
)

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config'))
DATA_HOME = Path(os.environ.get('XDG_DATA_HOME', HOME / '.local/share'))
STATE_HOME = Path(os.environ.get('XDG_STATE_HOME', HOME / '.local/state'))
CACHE_HOME = Path(os.environ.get('XDG_CACHE_HOME', HOME / '.cache'))
SETTINGS = CONFIG_HOME / 'minimal-whisper/settings.json'
LEGACY_SETTINGS = CONFIG_HOME / 'openai-whisper/settings.json'
STATE = STATE_HOME / 'minimal-whisper/status.json'
PTT_PID = STATE_HOME / 'minimal-whisper/ptt.pid'
CONTROL_PID = STATE_HOME / 'minimal-whisper/control.pid'
LEGACY_STATE = STATE_HOME / 'openai-whisper/status.json'
LANGUAGE_CONFIG = CONFIG_HOME / 'minimal-whisper/languages.json'
LEGACY_LANGUAGE_CONFIG = CONFIG_HOME / 'openai-whisper/languages.json'
BUILTIN_LANGUAGE_CONFIG = Path(__file__).resolve().parent.parent / 'config/languages.json'
MODEL_CONFIG = CONFIG_HOME / 'minimal-whisper/models.json'
LEGACY_MODEL_CONFIG = CONFIG_HOME / 'openai-whisper/models.json'
BUILTIN_MODEL_CONFIG = Path(__file__).resolve().parent.parent / 'config/models.json'
SERVICE = 'minimal-whisper-ptt.service'
DEFAULTS = {'model': 'base', 'language': 'auto', 'theme': 'dark',
            'shortcut': 'Meta+Ctrl+Y', 'overlay_position': None,
            'scale_percent': 100, 'audio_source': ''}
SCALE_PRESETS = (75, 100, 125, 150)


def normalized_scale(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 100
    return min(SCALE_PRESETS, key=lambda preset: abs(preset - value))

THEMES = {
    'dark': {
        'window': '#111111', 'panel': '#1c1c1c', 'raised': '#252525',
        'text': '#f4f4f4', 'muted': '#a1a1a1', 'line': '#353535',
        'accent': '#ffffff', 'accent_text': '#111111', 'good': '#e6e6e6',
    },
    'light': {
        'window': '#f5f5f5', 'panel': '#ffffff', 'raised': '#eeeeee',
        'text': '#151515', 'muted': '#686868', 'line': '#d7d7d7',
        'accent': '#171717', 'accent_text': '#ffffff', 'good': '#202020',
    },
}


def read_json(path, default):
    candidates = [path]
    if path == SETTINGS:
        candidates.append(LEGACY_SETTINGS)
    elif path == STATE:
        candidates.append(LEGACY_STATE)
    for candidate in candidates:
        try:
            return {**default, **json.loads(candidate.read_text(encoding='utf-8'))}
        except (OSError, ValueError, TypeError):
            continue
    return dict(default)


def read_languages():
    """Load editable language choices, falling back to the bundled defaults."""
    for path in (LANGUAGE_CONFIG, LEGACY_LANGUAGE_CONFIG, BUILTIN_LANGUAGE_CONFIG):
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
            languages = [(str(item['label']), str(item['code'])) for item in entries
                         if isinstance(item, dict) and item.get('label') and item.get('code')]
            if languages and any(code == 'auto' for _, code in languages):
                return languages
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return [('Automatic', 'auto'), ('English', 'en'), ('German', 'de'),
            ('Japanese', 'ja'), ('Russian', 'ru')]


def read_models():
    """Load editable model labels and Whisper model identifiers."""
    for path in (MODEL_CONFIG, LEGACY_MODEL_CONFIG, BUILTIN_MODEL_CONFIG):
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
            models = [(str(item['label']), str(item['model'])) for item in entries
                      if isinstance(item, dict) and item.get('label') and item.get('model')]
            if models:
                return models
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return [('Small · multilingual · 244M parameters', 'small'),
            ('Base · multilingual', 'base'), ('Base English · faster', 'base.en')]


WHISPER_METADATA_SCRIPT = '''
import json, os, urllib.parse, whisper
models = {}
for name in whisper.available_models():
    url = whisper._MODELS[name]
    models[name] = {'file': os.path.basename(urllib.parse.urlsplit(url).path),
                    'url': url, 'sha256': url.rstrip('/').split('/')[-2]}
print(json.dumps(models))
'''

MODEL_CACHE_CHECK_SCRIPT = '''
import hashlib, json, os, sys, whisper
root = sys.argv[1]
valid = set()
checked = {}
for name, url in whisper._MODELS.items():
    filename = os.path.basename(url.split('?')[0])
    path = os.path.join(root, filename)
    if path not in checked:
        if not os.path.isfile(path):
            checked[path] = False
        else:
            digest = hashlib.sha256()
            with open(path, 'rb') as model:
                for chunk in iter(lambda: model.read(8 * 1024 * 1024), b''):
                    digest.update(chunk)
            checked[path] = digest.hexdigest() == url.rstrip('/').split('/')[-2]
    if checked[path]:
        valid.add(name)
print(json.dumps(sorted(valid)))
'''


def whisper_python_candidates():
    candidates = []
    if os.environ.get('MINIMAL_WHISPER_PYTHON'):
        candidates.append(os.environ['MINIMAL_WHISPER_PYTHON'])
    candidates.extend((DATA_HOME / 'minimal-whisper/venv/bin/python',
                       HOME / '.local/opt/openai-whisper/bin/python'))
    if shutil.which('python3'):
        candidates.append(shutil.which('python3'))
    candidates.append(sys.executable)
    seen = set()
    for candidate in candidates:
        candidate = str(candidate)
        if candidate not in seen and Path(candidate).is_file():
            seen.add(candidate)
            yield candidate


def load_whisper_metadata():
    for candidate in whisper_python_candidates():
        try:
            result = subprocess.run([candidate, '-c', WHISPER_METADATA_SCRIPT],
                                    capture_output=True, text=True, timeout=30, check=False)
            if result.returncode == 0:
                return candidate, json.loads(result.stdout), ''
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    return None, {}, 'Whisper is unavailable in the configured Python environments.'


def human_size(size):
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'


def read_audio_sources():
    try:
        return AudioBackend.detect().list_sources()
    except AudioBackendError:
        return '', []


def write_settings(data):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temp.replace(SETTINGS)


def shortcut_label(sequence):
    label = QKeySequence.fromString(
        sequence, QKeySequence.SequenceFormat.PortableText
    ).toString(QKeySequence.SequenceFormat.NativeText)
    return label.replace('Meta', 'Super').replace('+', ' + ')


def valid_shortcut_sequence(sequence):
    parts = [part.strip().lower() for part in sequence.split('+') if part.strip()]
    modifiers = {'ctrl', 'control', 'meta', 'super', 'alt', 'shift'}
    return len(parts) >= 2 and all(part in modifiers for part in parts[:-1]) \
        and parts[-1] not in modifiers


def user_systemd_available():
    if not shutil.which('systemctl'):
        return False
    try:
        subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True,
                       text=True, timeout=2, check=True)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    except subprocess.CalledProcessError:
        return False


def fallback_listener_pid():
    try:
        pid = int(PTT_PID.read_text(encoding='ascii').strip())
        os.kill(pid, 0)
        command = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(
            'utf-8', errors='replace')
        if 'minimal-whisper-ptt' in command:
            return pid
    except (OSError, ValueError):
        pass
    return None


def service_state():
    if user_systemd_available():
        try:
            result = subprocess.run(['systemctl', '--user', 'is-active', SERVICE],
                                    capture_output=True, text=True, timeout=2)
            return result.stdout.strip() or 'stopped'
        except (OSError, subprocess.TimeoutExpired):
            return 'unknown'
    return 'active' if fallback_listener_pid() else 'stopped'


def service_running():
    return service_state() in {'active', 'activating', 'reloading', 'deactivating'}


def service_action(action, background=False):
    if not user_systemd_available():
        pid = fallback_listener_pid()
        try:
            if action == 'start':
                if pid:
                    return subprocess.CompletedProcess([], 0, '', '')
                launcher = HOME / '.local/bin/minimal-whisper-ptt'
                if not launcher.is_file():
                    raise FileNotFoundError(f'Listener launcher not found: {launcher}')
                log_path = STATE_HOME / 'minimal-whisper/ptt.log'
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open('a', encoding='utf-8') as log_file:
                    process = subprocess.Popen([str(launcher)], stdin=subprocess.DEVNULL,
                                               stdout=log_file, stderr=log_file,
                                               start_new_session=True)
                PTT_PID.parent.mkdir(parents=True, exist_ok=True)
                PTT_PID.write_text(f'{process.pid}\n', encoding='ascii')
                return subprocess.CompletedProcess([], 0, '', '')
            if action == 'stop':
                if pid:
                    os.kill(pid, signal.SIGTERM)
                    if not background:
                        deadline = time.monotonic() + 3
                        while fallback_listener_pid() and time.monotonic() < deadline:
                            time.sleep(0.05)
                return subprocess.CompletedProcess([], 0, '', '')
            if action == 'restart':
                if pid:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 3
                    while fallback_listener_pid() and time.monotonic() < deadline:
                        time.sleep(0.05)
                return service_action('start', background=background)
            raise ValueError(f'Unsupported listener action: {action}')
        except (OSError, ValueError) as exc:
            return exc
    try:
        command = ['systemctl', '--user']
        if background:
            command.append('--no-block')
        command.extend((action, SERVICE))
        return subprocess.run(command,
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return exc


def make_icon(theme='dark'):
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QColor('#111111' if theme == 'dark' else '#f2f2f2')
    fg = QColor('#ffffff' if theme == 'dark' else '#111111')
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 18, 18)
    p.setPen(QPen(fg, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x, h in zip((18, 27, 36, 45), (13, 27, 19, 10)):
        p.drawLine(QPointF(x, 32-h/2), QPointF(x, 32+h/2))
    p.end()
    return QIcon(pix)


class Waveform(QWidget):
    def __init__(self, color, scale=1.0, parent=None):
        super().__init__(parent)
        self.color = color
        self.scale = scale
        self.phase = 0.0
        self.setMinimumSize(round(76 * scale), round(24 * scale))

    def advance(self):
        self.phase += 0.24
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.color, 2.2 * self.scale, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        mid = self.height() / 2
        count = 17
        step = self.width() / (count + 1)
        for i in range(count):
            envelope = 0.28 + 0.72 * abs(math.sin(i * 0.39 + self.phase * 0.2))
            wave = abs(math.sin(i * 0.74 + self.phase))
            half = 2 + envelope * wave * (self.height() * 0.42)
            x = step * (i + 1)
            p.drawLine(QPointF(x, mid - half), QPointF(x, mid + half))
        p.end()


class Overlay(QWidget):
    def __init__(self, theme, position=None, position_changed=None, scale_percent=100):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.theme = theme
        self.positioning_supported = not (os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
                                           or bool(os.environ.get('WAYLAND_DISPLAY')))
        self.saved_position = position
        self.position_changed = position_changed
        self.scale = max(0.5, min(2.0, int(scale_percent) / 100))
        self.position_initialized = False
        self.drag_offset = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(round(236 * self.scale), round(54 * self.scale))
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(*(round(n * self.scale) for n in (13, 7, 13, 7)))
        self.layout.setSpacing(round(8 * self.scale))
        self.dot = QLabel('●')
        self.label = QLabel('LISTENING')
        for child in (self.dot, self.label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.setStyleSheet(
            f'font-size: {round(10 * self.scale)}px; font-weight: 700; '
            f'letter-spacing: {round(self.scale)}px;')
        self.wave = Waveform(QColor(THEMES[theme]['accent']), self.scale)
        self.wave.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.layout.addWidget(self.dot)
        self.layout.addWidget(self.label)
        self.layout.addWidget(self.wave, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.wave.advance)
        self.timer.start(40)
        self.apply_theme(theme)

    def apply_theme(self, theme):
        self.theme = theme
        colors = THEMES[theme]
        foreground = colors['text']
        self.background_color = QColor(colors['panel'])
        self.setStyleSheet(f'QWidget {{ background: transparent; color: {foreground}; }}')
        self.dot.setStyleSheet(
            f'color: {foreground}; font-size: {round(10 * self.scale)}px;')
        self.label.setStyleSheet(
            f'color: {foreground}; font-size: {round(9 * self.scale)}px; '
            f'font-weight: 700; letter-spacing: {round(self.scale)}px;')
        self.wave.color = QColor(colors['accent'])
        self.update()

    def apply_scale(self, scale_percent):
        position = self.pos() if self.position_initialized else self.saved_position
        self.scale = max(0.5, min(2.0, int(scale_percent) / 100))
        self.setFixedSize(round(236 * self.scale), round(54 * self.scale))
        self.layout.setContentsMargins(*(round(n * self.scale) for n in (13, 7, 13, 7)))
        self.layout.setSpacing(round(8 * self.scale))
        self.wave.scale = self.scale
        self.wave.setMinimumSize(round(76 * self.scale), round(24 * self.scale))
        self.apply_theme(self.theme)
        if self.position_initialized and self.positioning_supported:
            clamped = self.clamp_position(position)
            self.move(clamped)
            if clamped != position:
                self.saved_position = {'x': clamped.x(), 'y': clamped.y()}
                if self.position_changed:
                    self.position_changed(self.saved_position)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.background_color)
        radius = 14 * self.scale
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        painter.end()

    def show_bottom_center(self):
        if not self.positioning_supported:
            self.show()
            return
        if not self.position_initialized:
            position = self.saved_position
            if isinstance(position, dict) and all(k in position for k in ('x', 'y')):
                desired = QPoint(int(position['x']), int(position['y']))
            else:
                desired = self.bottom_center_position()
            self.position_initialized = True
        else:
            desired = self.pos()
        clamped = self.clamp_position(desired)
        if clamped != desired:
            self.saved_position = {'x': clamped.x(), 'y': clamped.y()}
            if self.position_changed:
                self.position_changed(self.saved_position)
        self.move(clamped)
        self.show()

    def bottom_center_position(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return QPoint(0, 0)
        area = screen.availableGeometry()
        return QPoint(area.x() + (area.width() - self.width()) // 2,
                      area.y() + area.height() - self.height() - round(26 * self.scale))

    def clamp_position(self, position):
        screens = QApplication.screens()
        if not screens:
            return position
        center = QPoint(position.x() + self.width() // 2,
                        position.y() + self.height() // 2)

        def distance_to_screen(screen):
            rect = screen.availableGeometry()
            dx = max(rect.left() - center.x(), 0, center.x() - rect.right())
            dy = max(rect.top() - center.y(), 0, center.y() - rect.bottom())
            return dx * dx + dy * dy

        area = min(screens, key=distance_to_screen).availableGeometry()
        max_x = max(area.left(), area.right() - self.width() + 1)
        max_y = max(area.top(), area.bottom() - self.height() + 1)
        return QPoint(max(area.left(), min(position.x(), max_x)),
                      max(area.top(), min(position.y(), max_y)))

    def keep_on_screen(self, *_args):
        if not self.positioning_supported or not self.position_initialized:
            return
        current = self.pos()
        clamped = self.clamp_position(current)
        if clamped != current:
            self.move(clamped)
            self.saved_position = {'x': clamped.x(), 'y': clamped.y()}
            if self.position_changed:
                self.position_changed(self.saved_position)

    def reset_position(self):
        if not self.positioning_supported:
            return
        self.saved_position = None
        self.position_initialized = True
        self.move(self.clamp_position(self.bottom_center_position()))
        if self.position_changed:
            self.position_changed(None)

    def mousePressEvent(self, event):
        if not self.positioning_supported:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.positioning_supported:
            return super().mouseMoveEvent(event)
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            desired = event.globalPosition().toPoint() - self.drag_offset
            self.move(self.clamp_position(desired))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.positioning_supported:
            return super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.drag_offset is not None:
            self.drag_offset = None
            self.position_initialized = True
            self.saved_position = {'x': self.x(), 'y': self.y()}
            if self.position_changed:
                self.position_changed(self.saved_position)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = read_json(SETTINGS, DEFAULTS)
        self.settings['scale_percent'] = normalized_scale(
            self.settings.get('scale_percent', 100))
        self.whisper_python, self.whisper_model_info, self.whisper_error = load_whisper_metadata()
        self.invalid_model_ids = []
        self.setWindowTitle('Minimal Whisper Settings')
        self.setWindowIcon(make_icon(self.settings['theme']))
        self.build_ui()
        self.apply_scale()
        self.apply_theme()

    def build_ui(self):
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_settings)
        root = QWidget()
        layout = QVBoxLayout(root)
        self.root_layout = layout

        heading = QHBoxLayout()
        titlebox = QVBoxLayout()
        title = QLabel('Minimal Whisper')
        title.setObjectName('title')
        subtitle = QLabel('Offline voice typing')
        subtitle.setObjectName('muted')
        titlebox.addWidget(title)
        titlebox.addWidget(subtitle)
        heading.addLayout(titlebox)
        heading.addStretch(1)
        self.state_badge = QLabel('●  Checking…')
        self.state_badge.setObjectName('badge')
        heading.addWidget(self.state_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(heading)

        card = QFrame()
        card.setObjectName('card')
        columns = QHBoxLayout(card)
        self.column_layout = columns
        self.column_forms = []

        def make_column(title_text):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            title = QLabel(title_text)
            title.setObjectName('sectionTitle')
            column_layout.addWidget(title)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            column_layout.addLayout(form)
            columns.addWidget(column, 1, Qt.AlignmentFlag.AlignTop)
            self.column_forms.append(form)
            return form

        input_form = make_column('INPUT & APPEARANCE')
        model_form = make_column('TRANSCRIPTION')
        self.model = QComboBox()
        self.all_models = read_models()
        self.models = [(label, model_id) for label, model_id in self.all_models
                       if model_id in self.whisper_model_info
                       or (Path(model_id).is_absolute() and Path(model_id).is_file())]
        valid_ids = {model_id for _label, model_id in self.models}
        self.invalid_model_ids = [model_id for _label, model_id in self.all_models
                                  if model_id not in valid_ids]
        for label, model_id in self.models:
            self.model.addItem(label, model_id)
        self.model.setEnabled(bool(self.models))
        self.model.setCurrentIndex(max(0, self.model.findData(self.settings['model'])))
        self.check_models_button = QPushButton('Check for new models')
        self.model_status = QLabel()
        self.model_status.setObjectName('muted')
        self.model_status.setWordWrap(True)
        self.model_catalog_status = QLabel()
        self.model_catalog_status.setObjectName('muted')
        self.model_catalog_status.setWordWrap(True)
        model_files_row = QWidget()
        model_files_layout = QVBoxLayout(model_files_row)
        model_files_layout.setContentsMargins(0, 0, 0, 0)
        model_files_layout.setSpacing(6)
        self.model_download_button = QPushButton('Download model')
        self.model_download_progress = QProgressBar()
        self.model_download_progress.setRange(0, 100)
        self.model_download_progress.hide()
        model_download_row = QHBoxLayout()
        model_download_row.addWidget(self.model_download_button)
        model_download_row.addWidget(self.model_download_progress, 1)
        model_files_layout.addWidget(self.model_status)
        model_files_layout.addWidget(self.model_catalog_status)
        model_files_layout.addLayout(model_download_row)
        self.language = QComboBox()
        self.languages = read_languages()
        for label, code in self.languages:
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.settings['language'])))
        self.shortcut = QKeySequenceEdit()
        self.shortcut.setMaximumSequenceLength(1)
        self.shortcut.setKeySequence(QKeySequence.fromString(
            self.settings['shortcut'], QKeySequence.SequenceFormat.PortableText))
        self.shortcut.setToolTip('Press the keys you want to use. Meta is the Super key on Linux.')
        self.audio_source = QComboBox()
        self.audio_refresh = QPushButton('Refresh')
        self.audio_meter_toggle = QPushButton('Monitor input')
        self.audio_meter_toggle.setCheckable(True)
        audio_row = QWidget()
        audio_layout = QHBoxLayout(audio_row)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        audio_layout.setSpacing(8)
        audio_layout.addWidget(self.audio_source, 1)
        self.theme_toggle = QPushButton()
        self.theme_toggle.setObjectName('themeToggle')
        self.theme_toggle.setCheckable(True)
        self.theme_toggle.setChecked(self.settings['theme'] == 'dark')
        self.update_theme_button()
        model_form.addRow('Model', self.model)
        model_form.addRow(self.check_models_button)
        model_form.addRow('Model files', model_files_row)
        model_form.addRow('Language', self.language)
        input_form.addRow('Microphone', audio_row)
        audio_actions = QWidget()
        audio_actions_layout = QHBoxLayout(audio_actions)
        audio_actions_layout.setContentsMargins(0, 0, 0, 0)
        audio_actions_layout.addWidget(self.audio_refresh)
        audio_actions_layout.addWidget(self.audio_meter_toggle, 1)
        input_form.addRow(audio_actions)
        self.audio_level = QProgressBar()
        self.audio_level.setRange(0, 100)
        self.audio_level.setValue(0)
        self.audio_level.setFormat('%p%')
        self.audio_level_text = QLabel('Microphone stays idle until monitoring or recording starts.')
        self.audio_level_text.setObjectName('muted')
        input_form.addRow('Input level', self.audio_level)
        input_form.addRow(self.audio_level_text)
        input_form.addRow('Shortcut', self.shortcut)
        input_form.addRow('Theme', self.theme_toggle)
        self.scale_selector = QComboBox()
        for percent in (75, 100, 125, 150):
            self.scale_selector.addItem(f'{percent}%', percent)
        nearest_scale = normalized_scale(self.settings.get('scale_percent', 100))
        self.scale_selector.setCurrentIndex(self.scale_selector.findData(nearest_scale))
        input_form.addRow('UI scale', self.scale_selector)
        self.reset_overlay = QPushButton('Reset to bottom-center')
        self.reset_overlay.setObjectName('secondary')
        wayland_session = (os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
                           or bool(os.environ.get('WAYLAND_DISPLAY')))
        self.reset_overlay.setEnabled(not wayland_session)
        input_form.addRow('Indicator position', self.reset_overlay)
        if wayland_session:
            position_note = QLabel('The Wayland compositor chooses top-level window placement; dragging and saved positioning are unavailable.')
            position_note.setObjectName('muted')
            position_note.setWordWrap(True)
            input_form.addRow(position_note)
        layout.addWidget(card)

        self.hotkey = QLabel()
        self.hotkey.setObjectName('hotkey')
        self.hotkey.setWordWrap(True)
        self.update_hotkey_hint()
        layout.addWidget(self.hotkey)

        self.message = QLabel('Saved')
        self.message.setObjectName('muted')
        self.message.setWordWrap(True)
        buttons = QHBoxLayout()
        self.toggle = QPushButton('Stop Whisper' if service_running() else 'Start Whisper')
        self.toggle.setObjectName('secondary')
        buttons.addWidget(self.toggle)
        buttons.addWidget(self.message, 1)
        layout.addLayout(buttons)
        self.setCentralWidget(root)
        self.toggle.clicked.connect(self.toggle_service)
        self.check_models_button.clicked.connect(self.check_for_new_models)
        self.shortcut.keySequenceChanged.connect(lambda _sequence: self.update_hotkey_hint())
        self.model.currentIndexChanged.connect(self.model_changed)
        self.language.currentIndexChanged.connect(lambda _index: self.schedule_save())
        self.scale_selector.currentIndexChanged.connect(self.scale_changed)
        self.audio_source.currentIndexChanged.connect(self.audio_source_changed)
        self.audio_refresh.clicked.connect(self.refresh_audio_sources)
        self.audio_meter_toggle.toggled.connect(self.monitor_input_changed)
        self.shortcut.keySequenceChanged.connect(self.shortcut_changed)
        self.theme_toggle.clicked.connect(self.toggle_theme)
        self.reset_overlay.clicked.connect(self.app.reset_overlay_position)
        self.model_download_button.clicked.connect(self.download_selected_model)
        self.model_download_process = QProcess(self)
        self.model_download_process.readyReadStandardError.connect(self.model_download_output)
        self.model_download_process.finished.connect(self.model_download_finished)
        self.model_cache_check_process = QProcess(self)
        self.model_cache_check_process.finished.connect(self.model_cache_check_finished)
        self.model_catalog_process = QProcess(self)
        self.model_catalog_process.finished.connect(self.model_catalog_check_finished)
        self.model_network = QNetworkAccessManager(self)
        self.model_remote_sizes = {}
        self.model_size_checked = set()
        self.verified_models = set()
        self.model_cache_checking = False
        self.model_cache_rescan_requested = False
        self.downloading_model = None
        self.resume_listener_after_model_change = False
        self.resume_from_model = None
        self.audio_meter_process = QProcess(self)
        self.audio_meter_process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.audio_meter_process.readyReadStandardOutput.connect(self.read_audio_meter)
        self.audio_meter_process.errorOccurred.connect(self.audio_meter_error)
        self.audio_meter_buffer = bytearray()
        self.refresh_audio_sources()
        self.refresh_model_cache()
        self.update_model_status()

    @staticmethod
    def model_download_script():
        return ('import sys, whisper; '
                'whisper._download(whisper._MODELS[sys.argv[1]], sys.argv[2], in_memory=False)')

    def model_changed(self, _index):
        self.update_model_marks()
        self.update_model_status()
        self.update_service_controls(service_running())
        self.schedule_save()

    def selected_model_is_downloading(self):
        return (self.model_download_process.state() != QProcess.ProcessState.NotRunning
                and self.model.currentData() == self.downloading_model)

    def update_service_controls(self, running):
        blocked = self.selected_model_is_downloading()
        self.toggle.setEnabled(not blocked)
        self.toggle.setText('Model downloading' if blocked else
                            ('Stop Whisper' if running else 'Start Whisper'))

    @staticmethod
    def model_label(model_id):
        if model_id.endswith('.en'):
            return f'{model_id[:-3].title()} · English'
        return f'{model_id.replace("-", " ").title()} · multilingual'

    def update_model_marks(self):
        selected = self.model.currentData()
        self.model.blockSignals(True)
        for index, (label, model_id) in enumerate(self.models):
            installed = (model_id in self.verified_models or
                         (Path(model_id).is_absolute() and Path(model_id).is_file()))
            self.model.setItemText(index, f'✓  {label}' if installed else label)
        self.model.setCurrentIndex(max(0, self.model.findData(selected)))
        self.model.blockSignals(False)

    def refresh_model_cache(self):
        if not self.whisper_python:
            self.verified_models = set()
            self.model_catalog_status.setText(self.whisper_error)
            return
        if self.model_cache_check_process.state() != QProcess.ProcessState.NotRunning:
            self.model_cache_rescan_requested = True
            return
        self.model_cache_rescan_requested = False
        self.model_cache_checking = True
        self.model_catalog_status.setText('Checking downloaded model files…')
        self.model_download_button.setEnabled(False)
        self.model_cache_check_process.start(
            self.whisper_python,
            ['-c', MODEL_CACHE_CHECK_SCRIPT, str(CACHE_HOME / 'whisper')])

    def model_cache_check_finished(self, exit_code, _exit_status):
        self.model_cache_checking = False
        output = bytes(self.model_cache_check_process.readAllStandardOutput())
        if exit_code == 0:
            try:
                self.verified_models = set(json.loads(output.decode('utf-8')))
                self.model_catalog_status.clear()
            except (ValueError, UnicodeDecodeError):
                self.verified_models = set()
                self.model_catalog_status.setText('Could not read model cache status.')
        else:
            self.verified_models = set()
            self.model_catalog_status.setText('Could not verify downloaded model files.')
        self.update_model_marks()
        self.update_model_status()
        if self.model_cache_rescan_requested:
            QTimer.singleShot(100, self.refresh_model_cache)

    def check_for_new_models(self):
        if not self.whisper_python:
            self.model_catalog_status.setText(self.whisper_error)
            return
        if self.model_catalog_process.state() != QProcess.ProcessState.NotRunning:
            return
        self.check_models_button.setEnabled(False)
        self.check_models_button.setText('Checking…')
        self.model_catalog_status.setText('Checking models supported by installed OpenAI Whisper…')
        self.model_catalog_process.start(self.whisper_python, ['-c', WHISPER_METADATA_SCRIPT])

    def model_catalog_check_finished(self, exit_code, _exit_status):
        self.check_models_button.setEnabled(True)
        self.check_models_button.setText('Check for new models')
        output = bytes(self.model_catalog_process.readAllStandardOutput())
        if exit_code != 0:
            self.model_catalog_status.setText('Could not check the installed Whisper model catalog.')
            return
        try:
            metadata = json.loads(output.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            self.model_catalog_status.setText('Could not read the installed Whisper model catalog.')
            return
        self.whisper_model_info = metadata
        configured = {model_id for _label, model_id in self.all_models}
        new_models = [(self.model_label(model_id), model_id)
                      for model_id in metadata if model_id not in configured]
        if new_models:
            self.all_models.extend(new_models)
            MODEL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            temporary = MODEL_CONFIG.with_suffix('.tmp')
            temporary.write_text(json.dumps(
                [{'label': label, 'model': model_id} for label, model_id in self.all_models],
                indent=2) + '\n', encoding='utf-8')
            temporary.replace(MODEL_CONFIG)
            selected = self.model.currentData()
            self.models.extend(new_models)
            self.model.blockSignals(True)
            for label, model_id in new_models:
                self.model.addItem(label, model_id)
            self.model.setCurrentIndex(max(0, self.model.findData(selected)))
            self.model.blockSignals(False)
            self.model_catalog_status.setText(
                'Added to your model list: ' + ', '.join(model_id for _, model_id in new_models))
        else:
            self.model_catalog_status.setText(
                'No new model IDs found. Update openai-whisper to check a newer catalog.')
        self.refresh_model_cache()
        self.update_model_status()

    def update_model_status(self):
        model_id = self.model.currentData()
        download_running = self.model_download_process.state() != QProcess.ProcessState.NotRunning
        downloading_selected = download_running and model_id == self.downloading_model
        self.model_download_button.setText(
            'Cancel download' if downloading_selected else 'Download model')
        self.model_download_button.setEnabled(downloading_selected)
        if not model_id:
            self.model_status.setText(self.whisper_error or 'No valid models are configured.')
            return
        if model_id in self.whisper_model_info:
            model_info = self.whisper_model_info[model_id]
            checkpoint = CACHE_HOME / 'whisper' / model_info['file']
            if model_id in self.verified_models:
                self.model_status.setText(f'Installed · {human_size(checkpoint.stat().st_size)}')
                self.model_download_button.setText('Uninstall model')
                self.model_download_button.setEnabled(
                    not self.model_cache_checking and not downloading_selected
                    and read_json(STATE, {'state': 'stopped'}).get('state') != 'transcribing')
            else:
                size = self.model_remote_sizes.get(model_id)
                size_text = human_size(size) if size else 'checking download size…'
                status = 'Downloading…' if downloading_selected else 'Not installed'
                if checkpoint.is_file() and checkpoint.stat().st_size > 0:
                    if not downloading_selected:
                        status = f'Incomplete or invalid download ({human_size(checkpoint.stat().st_size)})'
                self.model_status.setText(
                    f'{status}' if downloading_selected else f'{status} · download size {size_text}')
                self.model_download_button.setEnabled(
                    downloading_selected or (not self.model_cache_checking and not download_running))
                if model_id not in self.model_size_checked:
                    self.request_model_size(model_id, model_info['url'])
        else:
            checkpoint = Path(model_id)
            self.model_download_button.setText('Managed cache only')
            self.model_download_button.setEnabled(False)
            self.model_status.setText(
                f'Installed local model · {human_size(checkpoint.stat().st_size)}'
                if checkpoint.is_file() else self.whisper_error)
        warnings = []
        if self.settings.get('model') not in {configured for _, configured in self.models}:
            warnings.append('The saved model is unavailable; choose a listed model.')
        if self.invalid_model_ids:
            warnings.append('Ignored unknown model IDs: ' + ', '.join(self.invalid_model_ids))
        if warnings:
            self.model_status.setText(self.model_status.text() + '\n' + ' '.join(warnings))

    def request_model_size(self, model_id, url):
        self.model_size_checked.add(model_id)
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(8000)
        reply = self.model_network.head(request)
        reply.finished.connect(lambda model_id=model_id, reply=reply:
                               self.model_size_finished(model_id, reply))

    def model_size_finished(self, model_id, reply):
        try:
            size = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
            if size:
                self.model_remote_sizes[model_id] = int(size)
        except (TypeError, ValueError):
            pass
        reply.deleteLater()
        if self.model.currentData() == model_id:
            self.update_model_status()

    def download_selected_model(self):
        if self.model_download_process.state() != QProcess.ProcessState.NotRunning:
            if self.model.currentData() != self.downloading_model:
                return
            self.model_download_button.setText('Cancelling…')
            self.model_download_button.setEnabled(False)
            self.model_download_process.terminate()
            return
        model_id = self.model.currentData()
        if model_id in self.verified_models:
            self.uninstall_selected_model()
            return
        info = self.whisper_model_info.get(model_id)
        if not self.whisper_python or not info:
            self.model_status.setText('This model cannot be downloaded by the installed Whisper version.')
            return
        self.downloading_model = model_id
        self.resume_listener_after_model_change = service_running()
        self.resume_from_model = model_id if self.resume_listener_after_model_change else None
        if self.resume_listener_after_model_change:
            service_action('stop', background=True)
        self.model_download_progress.setValue(0)
        self.model_download_progress.show()
        self.model_download_button.setText('Cancel download')
        self.model_download_button.setEnabled(True)
        self.model_status.setText('Downloading…')
        cache = CACHE_HOME / 'whisper'
        cache.mkdir(parents=True, exist_ok=True)
        self.model_download_process.start(
            self.whisper_python,
            ['-c', self.model_download_script(), model_id, str(cache)])
        self.update_service_controls(False)

    def uninstall_selected_model(self):
        model_id = self.model.currentData()
        info = self.whisper_model_info.get(model_id)
        if not info or model_id not in self.verified_models:
            return
        if read_json(STATE, {'state': 'stopped'}).get('state') == 'transcribing':
            self.model_status.setText('Wait until transcription finishes before uninstalling this model.')
            return
        checkpoint = CACHE_HOME / 'whisper' / info['file']
        answer = QMessageBox.question(
            self, 'Uninstall model',
            f'Remove {model_id} ({human_size(checkpoint.stat().st_size)}) from the local model cache?')
        if answer != QMessageBox.StandardButton.Yes:
            return
        if service_running():
            self.resume_listener_after_model_change = True
            self.resume_from_model = model_id
            service_action('stop', background=True)
        try:
            checkpoint.unlink()
        except OSError as exc:
            self.model_status.setText(f'Could not uninstall model: {exc}')
            return
        shared_file_models = {name for name, data in self.whisper_model_info.items()
                              if data['file'] == info['file']}
        self.verified_models.difference_update(shared_file_models)
        self.update_model_marks()
        self.model_catalog_status.setText(f'Removed {model_id} from the local model cache.')
        self.update_model_status()
        self.update_service_controls(False)

    def model_download_output(self):
        output = bytes(self.model_download_process.readAllStandardError()).decode(
            'utf-8', errors='replace')
        percentages = re.findall(r'(\d{1,3})%', output)
        if percentages:
            self.model_download_progress.setValue(min(100, int(percentages[-1])))
            self.model_catalog_status.setText(
                f'Downloading {self.downloading_model}… {percentages[-1]}%')
            if self.model.currentData() == self.downloading_model:
                self.model_status.setText(f'Downloading… {percentages[-1]}%')

    def model_download_finished(self, exit_code, _exit_status):
        model_id = self.downloading_model
        self.downloading_model = None
        self.model_download_progress.hide()
        if exit_code == 0:
            self.model_catalog_status.setText(f'Downloaded {model_id}; verifying checkpoint…')
            self.refresh_model_cache()
        else:
            self.model_catalog_status.setText(
                f'Download of {model_id} was cancelled or failed; it is not available yet.')
        self.update_model_status()
        self.update_service_controls(service_running())

    def refresh_audio_sources(self):
        selected = self.audio_source.currentData()
        if selected is None:
            selected = self.settings.get('audio_source', '')
        default, devices = read_audio_sources()
        self.audio_source.blockSignals(True)
        self.audio_source.clear()
        self.audio_source.addItem('System default' + (' · current default' if default else ''), '')
        for description, source_name in devices:
            label = description + (' · default' if source_name == default else '')
            self.audio_source.addItem(label, source_name)
        selected_index = self.audio_source.findData(selected)
        if selected and selected_index < 0:
            self.audio_source.addItem(f'Unavailable · {selected}', selected)
            selected_index = self.audio_source.count() - 1
        self.audio_source.setCurrentIndex(max(0, selected_index))
        self.audio_source.blockSignals(False)
        if not devices:
            self.audio_level_text.setText('No microphone sources found. Check the audio server and recording permissions.')
        elif self.audio_meter_process.state() == QProcess.ProcessState.NotRunning:
            self.audio_level_text.setText('Microphone stays idle until monitoring or recording starts.')

    def audio_source_changed(self, _index):
        source = self.audio_source.currentData() or ''
        self.settings['audio_source'] = source
        self.app.settings['audio_source'] = source
        self.schedule_save()
        if self.audio_meter_toggle.isChecked():
            self.start_audio_meter()

    def monitor_input_changed(self, enabled):
        if enabled:
            self.audio_meter_toggle.setText('Stop monitoring')
            self.start_audio_meter()
        else:
            self.audio_meter_toggle.setText('Monitor input')
            self.stop_audio_meter()

    def start_audio_meter(self):
        if self.audio_meter_process.state() != QProcess.ProcessState.NotRunning:
            self.audio_meter_process.terminate()
            self.audio_meter_process.waitForFinished(500)
        source = self.audio_source.currentData()
        try:
            command = AudioBackend.detect().meter_command(source, 16000, 1)
        except AudioBackendError as exc:
            self.audio_level_text.setText(str(exc))
            return
        self.audio_meter_buffer.clear()
        self.audio_level.setValue(0)
        self.audio_level_text.setText('Listening for microphone level…')
        self.audio_meter_process.start(command[0], command[1:])

    def stop_audio_meter(self):
        if self.audio_meter_process.state() != QProcess.ProcessState.NotRunning:
            self.audio_meter_process.terminate()
            if not self.audio_meter_process.waitForFinished(1000):
                self.audio_meter_process.kill()
                self.audio_meter_process.waitForFinished(500)
        self.audio_level.setValue(0)
        self.audio_level_text.setText('Microphone stays idle until monitoring or recording starts.')

    def read_audio_meter(self):
        self.audio_meter_buffer.extend(bytes(self.audio_meter_process.readAllStandardOutput()))
        usable = len(self.audio_meter_buffer) & ~1
        if usable < 64:
            return
        data = bytes(self.audio_meter_buffer[:usable])
        del self.audio_meter_buffer[:usable]
        count = usable // 2
        samples = struct.unpack('<' + 'h' * count, data)
        rms = math.sqrt(sum(sample * sample for sample in samples) / count) / 32768
        decibels = 20 * math.log10(max(rms, 0.000001))
        self.audio_level.setValue(max(0, min(100, round((decibels + 60) * 100 / 60))))

    def audio_meter_error(self, _error):
        if self.isVisible():
            self.audio_level_text.setText(
                'Microphone meter unavailable. Check PipeWire and the selected input source.')

    def showEvent(self, event):
        super().showEvent(event)

    def hideEvent(self, event):
        self.audio_meter_toggle.blockSignals(True)
        self.audio_meter_toggle.setChecked(False)
        self.audio_meter_toggle.setText('Monitor input')
        self.audio_meter_toggle.blockSignals(False)
        self.stop_audio_meter()
        super().hideEvent(event)

    def update_theme_button(self):
        self.theme_toggle.setText('Dark · click to switch' if self.theme_toggle.isChecked()
                                  else 'Light · click to switch')

    def update_hotkey_hint(self):
        sequence = self.shortcut.keySequence().toString(
            QKeySequence.SequenceFormat.PortableText)
        self.hotkey.setText(f'Hold  {shortcut_label(sequence)}  to record, then release to transcribe')

    def toggle_theme(self, dark_enabled):
        self.settings['theme'] = 'dark' if dark_enabled else 'light'
        self.app.settings['theme'] = self.settings['theme']
        self.update_theme_button()
        self.apply_theme()
        self.app.refresh_theme()
        self.schedule_save(150)

    def scale_changed(self, _index):
        percent = self.scale_selector.currentData()
        self.settings['scale_percent'] = percent
        self.app.settings['scale_percent'] = percent
        self.apply_scale()
        if hasattr(self.app, 'overlay'):
            self.app.overlay.apply_scale(percent)
        self.schedule_save()

    def apply_scale(self):
        self.scale_factor = self.scale_selector.currentData() / 100
        self.setFixedWidth(round(900 * self.scale_factor))
        self.root_layout.setContentsMargins(*(
            round(n * self.scale_factor) for n in (24, 20, 24, 18)))
        self.root_layout.setSpacing(round(14 * self.scale_factor))
        self.column_layout.setContentsMargins(*(
            round(n * self.scale_factor) for n in (28, 24, 28, 24)))
        self.column_layout.setSpacing(round(28 * self.scale_factor))
        for form in self.column_forms:
            form.setVerticalSpacing(round(10 * self.scale_factor))
            form.setHorizontalSpacing(round(12 * self.scale_factor))
        self.apply_theme()
        # Updating font and layout metrics does not resize an already visible
        # QMainWindow. Recompute the content hint so scale-downs shrink the
        # window vertically as well as horizontally.
        central = self.centralWidget()
        if central is not None:
            central.layout().activate()
            central.adjustSize()
        self.adjustSize()

    def schedule_save(self, delay=350):
        self.message.setText('Saving…')
        self._save_timer.start(delay)

    def shortcut_changed(self, sequence):
        portable = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        self.update_hotkey_hint()
        if not valid_shortcut_sequence(portable):
            self.message.setText('Enter a complete shortcut to save.')
            return
        self.schedule_save(700)

    def collect_settings(self):
        shortcut = self.shortcut.keySequence().toString(
            QKeySequence.SequenceFormat.PortableText)
        if not valid_shortcut_sequence(shortcut):
            shortcut = self.settings.get('shortcut', DEFAULTS['shortcut'])
        selected_model = self.model.currentData()
        selected_language = self.language.currentData()
        if selected_model == 'base.en' and selected_language not in ('auto', 'en'):
            selected_language = 'en'
            self.language.blockSignals(True)
            self.language.setCurrentIndex(self.language.findData('en'))
            self.language.blockSignals(False)
        return {
            'model': selected_model,
            'language': selected_language,
            'theme': 'dark' if self.theme_toggle.isChecked() else 'light',
            'scale_percent': self.scale_selector.currentData(),
            'audio_source': self.audio_source.currentData() or '',
            'shortcut': shortcut,
            'overlay_position': self.app.settings.get('overlay_position'),
        }

    def apply_theme(self):
        c = THEMES[self.settings['theme']]
        px = lambda value: max(1, round(value * self.scale_factor))
        self.update_theme_button()
        self.update_hotkey_hint()
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {c['window']}; color: {c['text']}; font-size: {px(13)}px; }}
            QLabel#title {{ font-size: {px(24)}px; font-weight: 700; }}
            QLabel#sectionTitle {{ color: {c['muted']}; font-size: {px(10)}px; font-weight: 700; letter-spacing: {px(1)}px; }}
            QLabel#muted {{ color: {c['muted']}; }}
            QLabel#badge {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: {px(12)}px; padding: {px(7)}px {px(10)}px; color: {c['text']}; font-size: {px(11)}px; }}
            QFrame#card {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: {px(14)}px; }}
            QComboBox {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: {px(8)}px; padding: {px(8)}px {px(10)}px; min-width: {px(160)}px; }}
            QComboBox::drop-down {{ width: 0px; border: none; }}
            QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
            QComboBox QAbstractItemView {{ background: {c['panel']}; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
            QKeySequenceEdit {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: {px(8)}px; padding: {px(7)}px {px(9)}px; min-width: {px(190)}px; }}
            QProgressBar {{ border: 1px solid {c['line']}; border-radius: {px(5)}px; padding: {px(1)}px; height: {px(14)}px; background: {c['raised']}; text-align: center; }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: {px(4)}px; }}
            QLabel#hotkey {{ color: {c['muted']}; padding: {px(4)}px {px(2)}px; }}
            QPushButton {{ border: 1px solid {c['line']}; border-radius: {px(9)}px; padding: {px(9)}px {px(12)}px; min-height: {px(18)}px; font-weight: 600; }}
            QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
            QPushButton#secondary {{ background: {c['raised']}; }}
            QPushButton#themeToggle {{ background: {c['raised']}; min-width: {px(190)}px; text-align: left; }}
            QPushButton:hover {{ border-color: {c['text']}; }}
        """)

    def save_settings(self):
        updated = self.collect_settings()
        self._save_timer.stop()
        previous = self.settings
        try:
            write_settings(updated)
        except OSError as exc:
            self.message.setText(f'Could not save settings: {exc}')
            return
        self.settings = updated
        self.app.settings.update(updated)
        listener_settings_changed = any(
            previous.get(key, DEFAULTS[key]) != updated[key]
            for key in ('model', 'language', 'shortcut', 'audio_source')
        )
        running = service_running()
        selected_downloading = self.selected_model_is_downloading()
        restart_for_selection = (self.resume_listener_after_model_change
                                 and updated['model'] != self.resume_from_model
                                 and not selected_downloading)
        was_running = listener_settings_changed and running
        if selected_downloading:
            if running:
                service_action('stop', background=True)
            self.message.setText('Choose another model while this download is in progress.')
        elif was_running:
            self.resume_listener_after_model_change = False
            self.resume_from_model = None
            self.message.setText('Applying in background…')
            result = service_action('restart', background=True)
            if isinstance(result, Exception) or result.returncode:
                detail = str(result) if isinstance(result, Exception) else result.stderr.strip()
                self.message.setText(f'Saved, but could not apply: {detail or "unknown error"}')
            else:
                self.message.setText('Saved; Whisper is restarting in the background.')
        elif restart_for_selection:
            result = service_action('start', background=True)
            self.resume_listener_after_model_change = False
            self.resume_from_model = None
            self.message.setText('Saved; Whisper is starting with the selected model.')
            if isinstance(result, Exception) or result.returncode:
                detail = str(result) if isinstance(result, Exception) else result.stderr.strip()
                self.message.setText(f'Saved, but could not start Whisper: {detail or "unknown error"}')
        else:
            self.message.setText('Saved')
        self.app.refresh_theme()
        self.app.refresh_status()
        self.update_service_controls(service_running())

    def toggle_service(self):
        if self.selected_model_is_downloading():
            self.message.setText('Choose another model while this download is in progress.')
            return
        active = service_running()
        result = service_action('stop' if active else 'start')
        if isinstance(result, Exception) or result.returncode:
            detail = str(result) if isinstance(result, Exception) else result.stderr.strip()
            self.message.setText(f'Could not change Whisper state: {detail or "unknown error"}')
        else:
            self.message.setText('Whisper stopped.' if active else 'Whisper started.')
        self.app.refresh_status()


class Controller:
    def __init__(self, app, open_settings=False):
        self.app = app
        self.settings = read_json(SETTINGS, DEFAULTS)
        self.settings['scale_percent'] = normalized_scale(
            self.settings.get('scale_percent', 100))
        self.window = MainWindow(self)
        self.overlay = Overlay(self.settings['theme'], self.settings.get('overlay_position'),
                               self.overlay_position_changed,
                               self.settings.get('scale_percent', 100))
        for screen in app.screens():
            screen.geometryChanged.connect(self.overlay.keep_on_screen)
            screen.availableGeometryChanged.connect(self.overlay.keep_on_screen)
        app.screenAdded.connect(self.screen_added)
        app.screenRemoved.connect(self.overlay.keep_on_screen)
        self.tray = (QSystemTrayIcon(make_icon(self.settings['theme']), app)
                     if QSystemTrayIcon.isSystemTrayAvailable() else None)
        self.menu = QMenu()
        self.status_action = self.menu.addAction('Minimal Whisper: checking…')
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction('Settings')
        self.toggle_action = self.menu.addAction('Stop Whisper')
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction('Quit tray app')
        if self.tray:
            self.tray.setContextMenu(self.menu)
            self.tray.setToolTip('Minimal Whisper · checking status')
            self.tray.activated.connect(self.tray_activated)
        self.settings_action.triggered.connect(self.show_settings)
        self.toggle_action.triggered.connect(self.toggle_service)
        self.quit_action.triggered.connect(app.quit)
        if self.tray:
            self.tray.show()
        self.last_state = None
        self.timer = QTimer(app)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(1000)
        self.refresh_status()
        if open_settings:
            self.show_settings()

    def screen_added(self, screen):
        screen.geometryChanged.connect(self.overlay.keep_on_screen)
        screen.availableGeometryChanged.connect(self.overlay.keep_on_screen)
        QTimer.singleShot(0, self.overlay.keep_on_screen)

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_settings()

    def show_settings(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def toggle_service(self):
        if self.window.selected_model_is_downloading():
            return
        action = 'stop' if service_running() else 'start'
        service_action(action)
        QTimer.singleShot(600, self.refresh_status)

    def overlay_position_changed(self, position):
        self.settings['overlay_position'] = position
        self.window.settings['overlay_position'] = position
        self.window.schedule_save(300)

    def reset_overlay_position(self):
        self.overlay.reset_position()

    def refresh_theme(self):
        if self.tray:
            self.tray.setIcon(make_icon(self.settings['theme']))
        self.window.settings = self.settings
        self.window.setWindowIcon(make_icon(self.settings['theme']))
        self.window.apply_theme()
        self.overlay.apply_theme(self.settings['theme'])

    def refresh_status(self):
        current = read_json(STATE, {'state': 'stopped'})
        state = current.get('state', 'stopped')
        running = service_running()
        if not running and state not in ('stopped',):
            state = 'stopped'
        elif running and state == 'stopped':
            state = 'listening'
        visible_state = state in ('recording', 'transcribing')
        if visible_state:
            new_text = 'TRANSCRIBING' if state == 'transcribing' else 'LISTENING'
            if self.overlay.label.text() != new_text:
                self.overlay.label.setText(new_text)
            if not self.overlay.isVisible():
                self.overlay.show_bottom_center()
        elif self.overlay.isVisible():
            self.overlay.hide()
        if not visible_state and self.overlay.label.text() != 'LISTENING':
            self.overlay.label.setText('LISTENING')

        label = {'recording': 'Recording', 'transcribing': 'Transcribing',
                 'listening': 'Ready', 'error': 'Needs attention',
                 'stopped': 'Stopped'}.get(state, state.title())
        badge = f'●  {label}'
        detail = current.get('detail', '')
        if state == 'listening' and detail.startswith('Copied transcription'):
            badge += ' · copied to clipboard; paste it yourself'
        self.window.state_badge.setText(badge)
        self.window.update_service_controls(running)
        self.menu.actions()[0].setText(f'Minimal Whisper: {label}')
        blocked = self.window.selected_model_is_downloading()
        self.toggle_action.setEnabled(not blocked)
        self.toggle_action.setText('Model downloading' if blocked else
                                   ('Stop Whisper' if running else 'Start Whisper'))
        model = current.get('model', self.settings['model'])
        if self.tray:
            tooltip = f'Minimal Whisper · {label} · {model}'
            if state == 'listening' and detail.startswith('Copied transcription'):
                tooltip += ' · clipboard ready to paste'
            self.tray.setToolTip(tooltip)


def main():
    open_settings = '--background' not in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName('Minimal Whisper')
    app.setQuitOnLastWindowClosed(False)
    socket = QLocalSocket()
    socket.connectToServer('minimal-whisper-control')
    if socket.waitForConnected(1000):
        if open_settings:
            socket.write(b'show')
            socket.waitForBytesWritten(500)
        return 0
    QLocalServer.removeServer('minimal-whisper-control')
    server = QLocalServer()
    if not server.listen('minimal-whisper-control'):
        print(f'Could not create the Minimal Whisper instance socket: {server.errorString()}',
              file=sys.stderr)
        return 1
    controller = Controller(app, open_settings)
    CONTROL_PID.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_PID.write_text(f'{os.getpid()}\n', encoding='ascii')

    def remove_pidfile():
        try:
            if int(CONTROL_PID.read_text(encoding='ascii').strip()) == os.getpid():
                CONTROL_PID.unlink()
        except (OSError, ValueError):
            pass

    app.aboutToQuit.connect(remove_pidfile)

    def receive_request():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            if client.waitForReadyRead(500):
                request = bytes(client.readAll()).decode('utf-8', errors='replace')
                if request == 'show':
                    controller.show_settings()
            client.disconnectFromServer()

    server.newConnection.connect(receive_request)
    app._whisper_instance_server = server
    app._whisper_controller = controller
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
