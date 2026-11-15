"""Native Qt desktop services shared by Windows and Linux."""
from __future__ import annotations

import ctypes
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QCursor, QImage, QPixmap
# Qt Multimedia (FFmpeg, PipeWire) costs several MiB; load it only when recording or previewing.
def _media():
    from PySide6 import QtMultimedia
    return QtMultimedia

from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from widgets import label, button


class Result(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Work(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.signals = Result()

    def run(self):
        try:
            result = self.action()
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(result)


def background(action, finished, failed):
    work = Work(action)
    work.signals.finished.connect(finished)
    work.signals.failed.connect(failed)
    QThreadPool.globalInstance().start(work)
    return work


class VoiceRecorder(QDialog):
    """Explicit push-to-talk. Recording never starts without pressing Record."""
    recorded = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Capture a task by voice')
        self.temp = tempfile.TemporaryDirectory(prefix='genta-voice-')
        self.busy = False
        self.finished.connect(self.release_recording)
        self.path = str(Path(self.temp.name) / 'capture.wav')
        layout = QVBoxLayout(self)
        layout.addWidget(label('Speech uses your configured Hermes speech provider.\nCloud providers receive this recording. Nothing records in the background.', 'muted'))
        self.state = label('Microphone is off.')
        layout.addWidget(self.state)
        self.capture = _media().QMediaCaptureSession(self)
        self.input = _media().QAudioInput(_media().QMediaDevices.defaultAudioInput(), self)
        self.capture.setAudioInput(self.input)
        self.recorder = _media().QMediaRecorder(self)
        self.capture.setRecorder(self.recorder)
        format = _media().QMediaFormat(_media().QMediaFormat.FileFormat.Wave)
        format.setAudioCodec(_media().QMediaFormat.AudioCodec.Wave)
        self.recorder.setMediaFormat(format)
        self.recorder.setAudioSampleRate(16000)
        self.recorder.setAudioChannelCount(1)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(self.path))
        self.recorder.errorOccurred.connect(lambda error, message: self.state.setText(message))
        self.recorder.recorderStateChanged.connect(self.changed)
        self.record = button('Record', self.toggle, 'primary')
        layout.addWidget(self.record)
        self.transcribe = button('Use recording', self.use)
        self.transcribe.setEnabled(False)
        layout.addWidget(self.transcribe)
        self.limit = QTimer(self)
        self.limit.setSingleShot(True)
        self.limit.setInterval(60000)
        self.limit.timeout.connect(self.stop)

    def toggle(self):
        if self.recorder.recorderState() == _media().QMediaRecorder.RecorderState.RecordingState:
            self.stop()
        else:
            self.transcribe.setEnabled(False)
            self.recorder.record()
            self.limit.start()

    def stop(self):
        self.limit.stop()
        self.recorder.stop()

    def changed(self, state):
        recording = state == _media().QMediaRecorder.RecorderState.RecordingState
        self.record.setText('Stop recording' if recording else 'Record again')
        self.state.setText('Recording… maximum one minute.' if recording else 'Microphone is off. Use the recording or record again.')
        self.transcribe.setEnabled(not recording and Path(self.path).is_file())

    def use(self):
        if Path(self.path).is_file():
            self.busy = True
            self.recorded.emit(self.path)

    def release_recording(self, result):
        self.stop()
        if not self.busy:
            self.temp.cleanup()


class CameraPreview(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(lambda result: self.stop())
        self.setWindowTitle('Before your call')
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.state = label('Camera is off. Preview stays on this device; nothing is recorded.', 'muted')
        layout.addWidget(self.state)
        self.video = __import__("PySide6.QtMultimediaWidgets", fromlist=["QVideoWidget"]).QVideoWidget()
        layout.addWidget(self.video, 1)
        devices = _media().QMediaDevices.videoInputs()
        self.camera = _media().QCamera(devices[0], self) if devices else None
        if self.camera:
            self.session = _media().QMediaCaptureSession(self)
            self.session.setCamera(self.camera)
            self.session.setVideoOutput(self.video)
            self.camera.errorOccurred.connect(lambda error, message: self.state.setText(message))
            layout.addWidget(button('Start preview', self.start, 'primary'))
            layout.addWidget(button('Stop camera', self.stop))
        else:
            self.state.setText('No camera is available.')

    def start(self):
        if self.camera:
            self.camera.start()
            self.state.setText('Local preview only. Close this window to turn the camera off.')

    def stop(self):
        if self.camera:
            self.camera.stop()
            self.state.setText('Camera is off.')



class Glance(QWidget):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.drag_offset = None
        self.setWindowTitle('Genta glance')
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(workspace.styleSheet())
        layout = QVBoxLayout(self)
        frame = QFrame()
        frame.setObjectName('shell')
        layout.addWidget(frame)
        content = QVBoxLayout(frame)
        top = QHBoxLayout()
        top.addWidget(label('GENTA / TODAY', 'eyebrow'), 1)
        top.addWidget(button('×', self.hide, 'icon', 'Hide widget'))
        content.addLayout(top)
        self.tasks = label('', translate=False)
        self.tasks.setWordWrap(True)
        content.addWidget(self.tasks)
        self.timer = label('', 'metric', translate=False)
        content.addWidget(self.timer)
        content.addWidget(button('Open workspace', workspace.expand, 'primary'))
        self.setFixedSize(340, 260)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - 355, screen.bottom() - 275)

    def refresh(self):
        open_tasks = [task['title'] for task in self.workspace.store.tasks(include_done=False) if task['day'] and task['day'] <= datetime.now().date().isoformat()]
        self.tasks.setText('\n'.join('· ' + title for title in open_tasks[:4]) or 'No open tasks today.')
        seconds = int(self.workspace.clock.remaining())
        self.timer.setText(f'{seconds // 60:02}:{seconds % 60:02}')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        self.drag_offset = None


def active_application():
    """Return only the app identity, never window titles or typed contents."""
    if os.name == 'nt':
        import ctypes.wintypes as w
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = w.HWND
        user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        pid = w.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = w.HANDLE
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ''
        try:
            size = w.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).stem
            return ''
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith('linux'):
        if 'GNOME' in os.environ.get('XDG_CURRENT_DESKTOP', ''):
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                result = bus.call_sync('ai.genta.Desktop', '/ai/genta/Desktop', 'ai.genta.Desktop', 'ActiveApplication', None, GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 1000, None)
                return result.unpack()[0]
            except GLib.Error:
                raise ValueError('Enable the Genta GNOME bridge for app nudges. If newly installed, sign out and back in once.') from None
        if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            import json
            result = subprocess.run(['hyprctl', 'activewindow', '-j'], capture_output=True, text=True, timeout=2, check=True)
            return json.loads(result.stdout).get('class', '')
        if os.environ.get('XDG_SESSION_TYPE') == 'x11':
            from Xlib.display import Display
            display = Display()
            try:
                root = display.screen().root
                active = root.get_full_property(display.intern_atom('_NET_ACTIVE_WINDOW'), 0)
                if active and active.value[0]:
                    window = display.create_resource_object('window', int(active.value[0]))
                    identity = window.get_wm_class()
                    return identity[-1] if identity else ''
                return ''
            finally:
                display.close()
        raise ValueError('This Wayland compositor needs an app-identity integration for nudges. GNOME and Hyprland are supported.')
    raise ValueError('App nudges are supported on Windows and Linux')
