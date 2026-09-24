#!/usr/bin/python
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF, QPoint, QPointF
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QKeySequenceEdit, QMainWindow, QMenu, QPushButton, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

HOME = Path.home()
SETTINGS = HOME / '.config/minimal-whisper/settings.json'
LEGACY_SETTINGS = HOME / '.config/openai-whisper/settings.json'
STATE = HOME / '.local/state/minimal-whisper/status.json'
LEGACY_STATE = HOME / '.local/state/openai-whisper/status.json'
LANGUAGE_CONFIG = HOME / '.config/minimal-whisper/languages.json'
LEGACY_LANGUAGE_CONFIG = HOME / '.config/openai-whisper/languages.json'
BUILTIN_LANGUAGE_CONFIG = Path(__file__).resolve().parent.parent / 'config/languages.json'
MODEL_CONFIG = HOME / '.config/minimal-whisper/models.json'
LEGACY_MODEL_CONFIG = HOME / '.config/openai-whisper/models.json'
BUILTIN_MODEL_CONFIG = Path(__file__).resolve().parent.parent / 'config/models.json'
SERVICE = 'minimal-whisper-ptt.service'
DEFAULTS = {'model': 'base', 'language': 'auto', 'theme': 'dark',
            'shortcut': 'Meta+Ctrl+Y', 'overlay_position': None}

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


def service_state():
    try:
        result = subprocess.run(['systemctl', '--user', 'is-active', SERVICE],
                                capture_output=True, text=True, timeout=2)
        return result.stdout.strip() or 'stopped'
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


def service_action(action):
    try:
        return subprocess.run(['systemctl', '--user', action, SERVICE],
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
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = color
        self.phase = 0.0
        self.setMinimumSize(76, 24)

    def advance(self):
        self.phase += 0.24
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.color, 2.2, Qt.PenStyle.SolidLine,
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
    def __init__(self, theme, position=None, position_changed=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.theme = theme
        self.saved_position = position
        self.position_changed = position_changed
        self.position_initialized = False
        self.drag_offset = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(236, 54)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(13, 7, 13, 7)
        self.layout.setSpacing(8)
        self.dot = QLabel('●')
        self.label = QLabel('LISTENING')
        for child in (self.dot, self.label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.setStyleSheet('font-size: 10px; font-weight: 700; letter-spacing: 1px;')
        self.wave = Waveform(QColor(THEMES[theme]['accent']))
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
        self.dot.setStyleSheet(f'color: {foreground}; font-size: 10px;')
        self.label.setStyleSheet(
            f'color: {foreground}; font-size: 9px; font-weight: 700; letter-spacing: 1px;')
        self.wave.color = QColor(colors['accent'])
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.background_color)
        painter.drawRoundedRect(QRectF(self.rect()), 14, 14)
        painter.end()

    def show_bottom_center(self):
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
                      area.y() + area.height() - self.height() - 26)

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

    def reset_position(self):
        self.saved_position = None
        self.position_initialized = True
        self.move(self.clamp_position(self.bottom_center_position()))
        if self.position_changed:
            self.position_changed(None)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            desired = event.globalPosition().toPoint() - self.drag_offset
            self.move(self.clamp_position(desired))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
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
        self.setWindowTitle('Minimal Whisper Settings')
        self.setWindowIcon(make_icon(self.settings['theme']))
        self.setFixedWidth(430)
        self.build_ui()
        self.apply_theme()

    def build_ui(self):
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_settings)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(18)

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
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setVerticalSpacing(13)
        self.model = QComboBox()
        self.models = read_models()
        for label, model_id in self.models:
            self.model.addItem(label, model_id)
        self.model.setCurrentIndex(max(0, self.model.findData(self.settings['model'])))
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
        self.theme_toggle = QPushButton()
        self.theme_toggle.setObjectName('themeToggle')
        self.theme_toggle.setCheckable(True)
        self.theme_toggle.setChecked(self.settings['theme'] == 'dark')
        self.update_theme_button()
        form.addRow('Model', self.model)
        form.addRow('Language', self.language)
        form.addRow('Shortcut', self.shortcut)
        form.addRow('Theme', self.theme_toggle)
        self.reset_overlay = QPushButton('Reset to bottom-center')
        self.reset_overlay.setObjectName('secondary')
        form.addRow('Indicator position', self.reset_overlay)
        layout.addWidget(card)

        self.hotkey = QLabel()
        self.hotkey.setObjectName('hotkey')
        self.hotkey.setWordWrap(True)
        self.update_hotkey_hint()
        layout.addWidget(self.hotkey)

        self.message = QLabel('Saved')
        self.message.setObjectName('muted')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        self.toggle = QPushButton('Stop Whisper' if service_state() == 'active' else 'Start Whisper')
        self.toggle.setObjectName('secondary')
        buttons.addWidget(self.toggle)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.setCentralWidget(root)
        self.toggle.clicked.connect(self.toggle_service)
        self.shortcut.keySequenceChanged.connect(lambda _sequence: self.update_hotkey_hint())
        self.model.currentIndexChanged.connect(lambda _index: self.schedule_save())
        self.language.currentIndexChanged.connect(lambda _index: self.schedule_save())
        self.shortcut.keySequenceChanged.connect(self.shortcut_changed)
        self.theme_toggle.clicked.connect(self.toggle_theme)
        self.reset_overlay.clicked.connect(self.app.reset_overlay_position)

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
            'shortcut': shortcut,
            'overlay_position': self.app.settings.get('overlay_position'),
        }

    def apply_theme(self):
        c = THEMES[self.settings['theme']]
        self.update_theme_button()
        self.update_hotkey_hint()
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {c['window']}; color: {c['text']}; font-size: 13px; }}
            QLabel#title {{ font-size: 24px; font-weight: 700; }}
            QLabel#muted {{ color: {c['muted']}; }}
            QLabel#badge {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 12px; padding: 7px 10px; color: {c['text']}; font-size: 11px; }}
            QFrame#card {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 14px; }}
            QComboBox {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 8px 10px; min-width: 190px; }}
            QComboBox QAbstractItemView {{ background: {c['panel']}; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
            QKeySequenceEdit {{ background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 7px 9px; min-width: 190px; }}
            QLabel#hotkey {{ color: {c['muted']}; padding: 4px 2px; }}
            QPushButton {{ border: 1px solid {c['line']}; border-radius: 9px; padding: 10px 14px; font-weight: 600; }}
            QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
            QPushButton#secondary {{ background: {c['raised']}; }}
            QPushButton#themeToggle {{ background: {c['raised']}; min-width: 190px; text-align: left; }}
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
            for key in ('model', 'language', 'shortcut')
        )
        was_running = listener_settings_changed and service_state() == 'active'
        self.message.setText('Applying…' if was_running else 'Saved')
        if was_running:
            result = service_action('restart')
            if isinstance(result, Exception) or result.returncode:
                detail = str(result) if isinstance(result, Exception) else result.stderr.strip()
                self.message.setText(f'Saved, but could not apply: {detail or "unknown error"}')
            else:
                self.message.setText('Saved')
        self.app.refresh_theme()
        self.app.refresh_status()

    def toggle_service(self):
        active = service_state() == 'active'
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
        self.window = MainWindow(self)
        self.overlay = Overlay(self.settings['theme'], self.settings.get('overlay_position'),
                               self.overlay_position_changed)
        self.tray = QSystemTrayIcon(make_icon(self.settings['theme']), app)
        self.menu = QMenu()
        self.status_action = self.menu.addAction('Minimal Whisper: checking…')
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction('Settings')
        self.toggle_action = self.menu.addAction('Stop Whisper')
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction('Quit tray app')
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip('Minimal Whisper · checking status')
        self.tray.activated.connect(self.tray_activated)
        self.settings_action.triggered.connect(self.show_settings)
        self.toggle_action.triggered.connect(self.toggle_service)
        self.quit_action.triggered.connect(app.quit)
        self.tray.show()
        self.last_state = None
        self.timer = QTimer(app)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(1000)
        self.refresh_status()
        if open_settings:
            self.show_settings()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_settings()

    def show_settings(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def toggle_service(self):
        action = 'stop' if service_state() == 'active' else 'start'
        service_action(action)
        QTimer.singleShot(600, self.refresh_status)

    def overlay_position_changed(self, position):
        self.settings['overlay_position'] = position
        self.window.settings['overlay_position'] = position
        self.window.schedule_save(300)

    def reset_overlay_position(self):
        self.overlay.reset_position()

    def refresh_theme(self):
        self.tray.setIcon(make_icon(self.settings['theme']))
        self.window.settings = self.settings
        self.window.setWindowIcon(make_icon(self.settings['theme']))
        self.window.apply_theme()
        self.overlay.apply_theme(self.settings['theme'])

    def refresh_status(self):
        current = read_json(STATE, {'state': 'stopped'})
        state = current.get('state', 'stopped')
        running = service_state() == 'active'
        if not running and state not in ('stopped',):
            state = 'stopped'
        self.overlay.hide()
        if state == 'recording':
            self.overlay.show_bottom_center()
        elif state == 'transcribing':
            self.overlay.label.setText('TRANSCRIBING')
            self.overlay.show_bottom_center()
        elif self.overlay.label.text() != 'LISTENING':
            self.overlay.label.setText('LISTENING')

        label = {'recording': 'Recording', 'transcribing': 'Transcribing',
                 'listening': 'Ready', 'error': 'Needs attention',
                 'stopped': 'Stopped'}.get(state, state.title())
        badge = f'●  {label}'
        self.window.state_badge.setText(badge)
        self.window.toggle.setText('Stop Whisper' if running else 'Start Whisper')
        self.menu.actions()[0].setText(f'Minimal Whisper: {label}')
        self.toggle_action.setText('Stop Whisper' if running else 'Start Whisper')
        model = current.get('model', self.settings['model'])
        self.tray.setToolTip(f'Minimal Whisper · {label} · {model}')


def main():
    open_settings = '--background' not in sys.argv
    os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
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
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print('No system tray is available in this desktop session.', file=sys.stderr)
    controller = Controller(app, open_settings)

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
