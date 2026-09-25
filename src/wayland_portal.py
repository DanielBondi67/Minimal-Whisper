"""Wayland global shortcuts and keyboard output via xdg-desktop-portal.

The desktop owns shortcut registration and all keyboard permissions.  No X11
or compositor-private APIs are used in a Wayland session.
"""

import secrets
import ctypes
import ctypes.util

from PySide6.QtCore import QEventLoop, QObject, QTimer, SLOT, Slot
from PySide6.QtDBus import QDBusConnection, QDBusObjectPath


PORTAL = 'org.freedesktop.portal.Desktop'
ROOT = '/org/freedesktop/portal/desktop'
REQUEST = 'org.freedesktop.portal.Request'
SHORTCUTS = 'org.freedesktop.portal.GlobalShortcuts'
REMOTE = 'org.freedesktop.portal.RemoteDesktop'
REGISTRY = 'org.freedesktop.host.portal.Registry'
RESPONSE_SLOT = SLOT('on_response(uint,QVariantMap)').encode()
ACTIVATED_SLOT = SLOT('on_activated(QDBusObjectPath,QString,qulonglong,QVariantMap)').encode()
DEACTIVATED_SLOT = SLOT('on_deactivated(QDBusObjectPath,QString,qulonglong,QVariantMap)').encode()


class PortalError(RuntimeError):
    pass


class _Response(QObject):
    def __init__(self, bus, path):
        super().__init__()
        self.bus = bus
        self.path = str(path)
        self.loop = QEventLoop()
        self.response = None
        self.results = {}
        if not self.bus.connect(PORTAL, self.path, REQUEST, 'Response', self,
                                RESPONSE_SLOT):
            raise PortalError('Could not listen for the desktop portal response')

    @Slot('uint', 'QVariantMap')
    def on_response(self, response, results):
        self.response = int(response)
        self.results = dict(results)
        self.loop.quit()

    def wait(self, timeout_ms=120000):
        if self.response is None:
            QTimer.singleShot(timeout_ms, self.loop.quit)
            self.loop.exec()
        self.bus.disconnect(PORTAL, self.path, REQUEST, 'Response', self,
                            RESPONSE_SLOT)
        if self.response is None:
            raise PortalError('Timed out waiting for desktop permission')
        if self.response != 0:
            raise PortalError('The desktop denied or cancelled the requested permission')
        return self.results


def _path(value):
    return value.path() if isinstance(value, QDBusObjectPath) else str(value)


def _portal_session_id():
    return f'minimal_whisper_{secrets.token_hex(6)}'


class _DBusIter(ctypes.Structure):
    # DBusMessageIter is opaque in libdbus' public API.
    _fields_ = [('storage', ctypes.c_longdouble * 32)]


class _DBus:
    STRING, OBJECT_PATH, UINT32 = ord('s'), ord('o'), ord('u')
    ARRAY, STRUCT, DICT_ENTRY, VARIANT = ord('a'), ord('r'), ord('e'), ord('v')

    def __init__(self):
        library = ctypes.util.find_library('dbus-1')
        if not library:
            raise PortalError('libdbus is unavailable; the Wayland portal backend cannot start')
        self.lib = ctypes.CDLL(library)
        self.lib.dbus_threads_init_default.restype = ctypes.c_int
        self.lib.dbus_threads_init_default()
        self.lib.dbus_bus_get.argtypes = [ctypes.c_int, ctypes.c_void_p]
        self.lib.dbus_bus_get.restype = ctypes.c_void_p
        self.lib.dbus_bus_get_unique_name.argtypes = [ctypes.c_void_p]
        self.lib.dbus_bus_get_unique_name.restype = ctypes.c_char_p
        self.lib.dbus_message_new_method_call.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                                          ctypes.c_char_p, ctypes.c_char_p]
        self.lib.dbus_message_new_method_call.restype = ctypes.c_void_p
        self.lib.dbus_message_iter_init_append.argtypes = [ctypes.c_void_p,
                                                           ctypes.POINTER(_DBusIter)]
        self.lib.dbus_message_iter_append_basic.argtypes = [ctypes.POINTER(_DBusIter),
                                                            ctypes.c_int, ctypes.c_void_p]
        self.lib.dbus_message_iter_append_basic.restype = ctypes.c_int
        self.lib.dbus_message_iter_open_container.argtypes = [ctypes.POINTER(_DBusIter),
                                                              ctypes.c_int, ctypes.c_char_p,
                                                              ctypes.POINTER(_DBusIter)]
        self.lib.dbus_message_iter_open_container.restype = ctypes.c_int
        self.lib.dbus_message_iter_close_container.argtypes = [ctypes.POINTER(_DBusIter),
                                                               ctypes.POINTER(_DBusIter)]
        self.lib.dbus_message_iter_close_container.restype = ctypes.c_int
        self.lib.dbus_connection_send_with_reply_and_block.argtypes = [ctypes.c_void_p,
                                                                       ctypes.c_void_p,
                                                                       ctypes.c_int,
                                                                       ctypes.c_void_p]
        self.lib.dbus_connection_send_with_reply_and_block.restype = ctypes.c_void_p
        self.lib.dbus_message_get_type.argtypes = [ctypes.c_void_p]
        self.lib.dbus_message_get_type.restype = ctypes.c_int
        self.lib.dbus_message_get_error_name.argtypes = [ctypes.c_void_p]
        self.lib.dbus_message_get_error_name.restype = ctypes.c_char_p
        self.lib.dbus_message_iter_init.argtypes = [ctypes.c_void_p,
                                                   ctypes.POINTER(_DBusIter)]
        self.lib.dbus_message_iter_init.restype = ctypes.c_int
        self.lib.dbus_message_iter_get_basic.argtypes = [ctypes.POINTER(_DBusIter),
                                                         ctypes.c_void_p]
        self.lib.dbus_message_unref.argtypes = [ctypes.c_void_p]
        self.lib.dbus_connection_set_exit_on_disconnect.argtypes = [ctypes.c_void_p,
                                                                    ctypes.c_int]
        self.connection = self.lib.dbus_bus_get(0, None)  # DBUS_BUS_SESSION
        if not self.connection:
            raise PortalError('Could not connect to the session D-Bus')
        self.lib.dbus_connection_set_exit_on_disconnect(self.connection, 0)
        self.unique_name = self.lib.dbus_bus_get_unique_name(self.connection).decode()

    @staticmethod
    def _string(value):
        return ctypes.c_char_p(str(value).encode('utf-8'))

    def basic(self, iterator, kind, value):
        storage = self._string(value) if kind in (self.STRING, self.OBJECT_PATH) \
            else ctypes.c_uint32(int(value))
        if not self.lib.dbus_message_iter_append_basic(ctypes.byref(iterator), kind,
                                                        ctypes.byref(storage)):
            raise PortalError('Could not encode a portal D-Bus argument')

    def open(self, iterator, kind, signature=None):
        child = _DBusIter()
        signature = signature.encode() if signature else None
        if not self.lib.dbus_message_iter_open_container(ctypes.byref(iterator), kind,
                                                          signature, ctypes.byref(child)):
            raise PortalError('Could not encode a portal D-Bus container')
        return child

    def close(self, parent, child):
        if not self.lib.dbus_message_iter_close_container(ctypes.byref(parent),
                                                          ctypes.byref(child)):
            raise PortalError('Could not finish a portal D-Bus container')

    def vardict(self, iterator, values):
        array = self.open(iterator, self.ARRAY, '{sv}')
        for key, value in values.items():
            entry = self.open(array, self.DICT_ENTRY)
            self.basic(entry, self.STRING, key)
            variant = self.open(entry, self.VARIANT, 's')
            self.basic(variant, self.STRING, value)
            self.close(entry, variant)
            self.close(array, entry)
        self.close(iterator, array)

    def call(self, interface, method, append, returns_request=False):
        message = self.lib.dbus_message_new_method_call(PORTAL.encode(), ROOT.encode(),
                                                       interface.encode(), method.encode())
        if not message:
            raise PortalError(f'Could not create portal call: {method}')
        try:
            iterator = _DBusIter()
            self.lib.dbus_message_iter_init_append(message, ctypes.byref(iterator))
            append(iterator)
            reply = self.lib.dbus_connection_send_with_reply_and_block(
                self.connection, message, 120000, None)
            if not reply:
                raise PortalError(f'The portal did not reply to {method}')
            try:
                if self.lib.dbus_message_get_type(reply) == 3:
                    name = self.lib.dbus_message_get_error_name(reply)
                    raise PortalError(name.decode() if name else f'{method} failed')
                if returns_request:
                    result = ctypes.c_char_p()
                    values = _DBusIter()
                    if not self.lib.dbus_message_iter_init(reply, ctypes.byref(values)):
                        raise PortalError(f'{method} returned no request handle')
                    self.lib.dbus_message_iter_get_basic(ctypes.byref(values),
                                                         ctypes.byref(result))
                    return result.value.decode() if result.value else None
                return None
            finally:
                self.lib.dbus_message_unref(reply)
        finally:
            self.lib.dbus_message_unref(message)


_DBUS = None


def _dbus():
    global _DBUS
    if _DBUS is None:
        _DBUS = _DBus()
    return _DBUS


def _request(bus, interface, method, append_arguments):
    token = _portal_session_id()
    client = _dbus()
    sender = client.unique_name.replace(':', '_').replace('.', '_')
    request_path = f'{ROOT}/request/{sender}/{token}'
    response = _Response(bus, request_path)
    actual_path = client.call(interface, method, append_arguments(token),
                              returns_request=True)
    if actual_path != request_path:
        raise PortalError(f'Unexpected request path from {method}: {actual_path}')
    return response.wait()


def _options_writer(values):
    def append(token):
        client = _dbus()
        return lambda iterator: client.vardict(
            iterator, {**values, 'handle_token': token})
    return append


def _unwrap(value):
    return value


class GlobalShortcutPortal(QObject):
    def __init__(self, shortcut):
        super().__init__()
        self.bus = QDBusConnection.sessionBus()
        if not self.bus.isConnected():
            raise PortalError('The session D-Bus is unavailable')
        # Newer portals support explicit host-app association; older portals
        # identify the application from the desktop entry/cgroup instead.
        client = _dbus()
        try:
            def register(iterator):
                client.basic(iterator, client.STRING, 'minimal-whisper')
                client.vardict(iterator, {})
            client.call(REGISTRY, 'Register', register)
        except PortalError:
            pass
        self.session = None
        self.shortcut_id = 'push_to_talk'
        self._active_callback = None
        self._inactive_callback = None
        results = _request(self.bus, SHORTCUTS, 'CreateSession',
                           _options_writer({'session_handle_token': _portal_session_id()}))
        self.session = _unwrap(results.get('session_handle'))
        if not self.session:
            raise PortalError('The shortcut portal did not create a session')
        trigger = _portal_trigger(shortcut)
        token = _portal_session_id()
        sender = client.unique_name.replace(':', '_').replace('.', '_')
        request_path = f'{ROOT}/request/{sender}/{token}'
        response = _Response(self.bus, request_path)

        def bind(iterator):
            client.basic(iterator, client.OBJECT_PATH, _path(self.session))
            shortcuts = client.open(iterator, client.ARRAY, '(sa{sv})')
            shortcut = client.open(shortcuts, client.STRUCT)
            client.basic(shortcut, client.STRING, self.shortcut_id)
            client.vardict(shortcut, {
                'description': 'Hold to record and release to transcribe',
                'preferred_trigger': trigger,
            })
            client.close(shortcuts, shortcut)
            client.close(iterator, shortcuts)
            client.basic(iterator, client.STRING, '')
            client.vardict(iterator, {'handle_token': token})

        actual_path = client.call(SHORTCUTS, 'BindShortcuts', bind,
                                  returns_request=True)
        if actual_path != request_path:
            raise PortalError(f'Unexpected request path from BindShortcuts: {actual_path}')
        response.wait()
        if not self.bus.connect(PORTAL, _path(self.session), SHORTCUTS, 'Activated',
                                self, ACTIVATED_SLOT):
            raise PortalError('Could not subscribe to shortcut activation')
        if not self.bus.connect(PORTAL, _path(self.session), SHORTCUTS, 'Deactivated',
                                self, DEACTIVATED_SLOT):
            raise PortalError('Could not subscribe to shortcut release')

    def set_callbacks(self, activated, deactivated):
        self._active_callback = activated
        self._inactive_callback = deactivated

    @Slot(QDBusObjectPath, str, 'qulonglong', 'QVariantMap')
    def on_activated(self, _session, shortcut_id, _timestamp, _options):
        if shortcut_id == self.shortcut_id and self._active_callback:
            self._active_callback()

    @Slot(QDBusObjectPath, str, 'qulonglong', 'QVariantMap')
    def on_deactivated(self, _session, shortcut_id, _timestamp, _options):
        if shortcut_id == self.shortcut_id and self._inactive_callback:
            self._inactive_callback()


def _portal_trigger(shortcut):
    aliases = {'meta': 'SUPER', 'super': 'SUPER', 'ctrl': 'CTRL',
               'control': 'CTRL', 'alt': 'ALT', 'shift': 'SHIFT'}
    parts = [part.strip() for part in shortcut.split('+') if part.strip()]
    if len(parts) < 2:
        raise PortalError('Shortcut must include a modifier and a key')
    converted = []
    for part in parts[:-1]:
        modifier = aliases.get(part.lower())
        if not modifier:
            raise PortalError(f'Unsupported portal shortcut modifier: {part}')
        converted.append(modifier)
    key = parts[-1]
    if len(key) == 1:
        key = key.lower()
    key = {'escape': 'Escape', 'esc': 'Escape', 'return': 'Return',
           'enter': 'Return', 'space': 'space'}.get(key.lower(), key)
    return '+'.join(converted + [key])


class RemoteKeyboardPortal:
    """Request keyboard control; rejected requests fall back to clipboard."""

    def __init__(self):
        self.bus = QDBusConnection.sessionBus()
        self.session = None
        self.error = None
        try:
            client = _dbus()
            results = _request(self.bus, REMOTE, 'CreateSession',
                               _options_writer({'session_handle_token': _portal_session_id()}))
            self.session = _unwrap(results.get('session_handle'))
            if not self.session:
                raise PortalError('The remote desktop portal did not create a session')

            def select_devices(token):
                def append(iterator):
                    client.basic(iterator, client.OBJECT_PATH, _path(self.session))
                    client.basic(iterator, client.UINT32, 2)
                    client.vardict(iterator, {'handle_token': token})
                return append
            _request(self.bus, REMOTE, 'SelectDevices', select_devices)

            def start(token):
                def append(iterator):
                    client.basic(iterator, client.OBJECT_PATH, _path(self.session))
                    client.basic(iterator, client.STRING, '')
                    client.vardict(iterator, {'handle_token': token})
                return append
            _request(self.bus, REMOTE, 'Start', start)
        except PortalError as exc:
            self.error = str(exc)
            self.session = None

    def type_text(self, text):
        if not self.session:
            return False
        try:
            client = _dbus()
            for character in text:
                codepoint = ord(character)
                keysym = codepoint if codepoint < 128 else 0x01000000 | codepoint
                for state in (1, 0):
                    def notify(iterator, key=keysym, pressed=state):
                        client.basic(iterator, client.OBJECT_PATH, _path(self.session))
                        client.vardict(iterator, {})
                        client.basic(iterator, client.UINT32, key)
                        client.basic(iterator, client.UINT32, pressed)
                    client.call(REMOTE, 'NotifyKeyboardKeysym', notify)
            return True
        except PortalError as exc:
            self.error = str(exc)
            return False
