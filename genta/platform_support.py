"""Small, explicit Windows/Linux platform boundaries."""
import hashlib
import os
from pathlib import Path
import time

from PySide6.QtCore import QAbstractNativeEventFilter


if os.name == 'nt':
    import ctypes
    _unbiased = ctypes.windll.kernel32.QueryUnbiasedInterruptTimePrecise
    _unbiased.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _unbiased.restype = None


def active_clock():
    if os.name == 'nt':
        value = ctypes.c_ulonglong()
        _unbiased(ctypes.byref(value))
        return value.value / 10_000_000
    return time.monotonic()


def runtime_dir():
    path = Path(os.environ.get('XDG_RUNTIME_DIR', os.environ.get('LOCALAPPDATA', str(Path.home() / '.cache'))))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def ipc_address():
    if os.name == 'nt':
        identity = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
        return 'genta-' + identity
    return str(runtime_dir() / 'genta.sock')


class WindowsHotkey(QAbstractNativeEventFilter):
    def __init__(self, application, callback):
        super().__init__()
        self.application, self.callback = application, callback
        self.registered = False
        if os.name == 'nt':
            import ctypes
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, 0x4731, 0x4001, 0x4E))
            if self.registered:
                application.installNativeEventFilter(self)
                application.aboutToQuit.connect(self.close)

    def nativeEventFilter(self, event_type, message):
        if os.name == 'nt':
            import ctypes
            import ctypes.wintypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == 0x4731:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(None, 0x4731)
            self.application.removeNativeEventFilter(self)
            self.registered = False
