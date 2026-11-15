"""SimpMusic media controls: Linux MPRIS, Windows system media sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from i18n import tr

try:
    from PySide6.QtDBus import QDBusMessage
except ImportError:  # Windows builds may omit Qt D-Bus.
    QDBusMessage = object

ROOT = Path(__file__).resolve().parent


class SimpMusic(QObject):
    changed = Signal(dict)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = None
        self.last = None
        self.process = None
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.signal_refresh = QTimer(self)
        self.signal_refresh.setSingleShot(True)
        self.signal_refresh.setInterval(120)
        self.signal_refresh.timeout.connect(self.refresh)
        if sys.platform.startswith('linux'):
            from PySide6.QtCore import SLOT
            from PySide6.QtDBus import QDBusConnection
            self.bus = QDBusConnection.sessionBus()
            # Push updates from MPRIS; keep a slow poll only as a safety net.
            if self.bus.connect('', '/org/mpris/MediaPlayer2', 'org.freedesktop.DBus.Properties',
                                'PropertiesChanged', self, SLOT('_properties_changed(QDBusMessage)')):
                self.timer.setInterval(10000)
        else:
            self.bus = None

    @Slot(QDBusMessage)
    def _properties_changed(self, message):
        if self.timer.isActive():
            self.signal_refresh.start()

    def start(self):
        self.refresh()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def _linux_interface(self, service, interface):
        from PySide6.QtDBus import QDBusInterface
        target = QDBusInterface(service, '/org/mpris/MediaPlayer2', interface, self.bus)
        target.setTimeout(250)
        return target

    def refresh(self):
        if os.name == 'nt':
            self._windows('status')
            return
        if self.bus is None:
            self._publish({'available': False, 'reason': tr('Media controls require Linux or Windows.')})
            return
        reply = self.bus.interface().registeredServiceNames()
        names = reply.value() if reply.isValid() else []
        # Reuse the known SimpMusic session instead of querying every player each refresh.
        chosen = self.service if self.service in names else None
        for name in ([] if chosen else names):
            if not name.startswith('org.mpris.MediaPlayer2.'):
                continue
            if 'simpmusic' in name.lower():
                chosen = name
                break
            identity = self._linux_interface(name, 'org.mpris.MediaPlayer2').property('Identity')
            if identity and 'simpmusic' in str(identity).lower():
                chosen = name
                break
        self.service = chosen
        if not chosen:
            self._publish({'available': False, 'reason': tr('SimpMusic is not exposing a media session. Open it and play a track.')})
            return
        player = self._linux_interface(chosen, 'org.mpris.MediaPlayer2.Player')
        metadata = player.property('Metadata') or {}
        if hasattr(metadata, 'variant'):
            metadata = metadata.variant()
        if not isinstance(metadata, dict):
            # Qt may expose a{sv} as a QDBusArgument; use the typed Properties reply.
            from PySide6.QtDBus import QDBusArgument
            if isinstance(metadata, QDBusArgument):
                metadata.beginMap()
                values = {}
                while not metadata.atEnd():
                    metadata.beginMapEntry()
                    key = metadata.asVariant()
                    value = metadata.asVariant()
                    if hasattr(value, 'variant'):
                        value = value.variant()
                    values[str(key)] = value
                    metadata.endMapEntry()
                metadata.endMap()
                metadata = values
            else:
                metadata = {}
        artists = metadata.get('xesam:artist', [])
        if isinstance(artists, str):
            artists = [artists]
        self._publish({
            'available': bool(player.isValid()),
            'title': str(metadata.get('xesam:title', 'SimpMusic')),
            'artist': ', '.join(str(a) for a in artists),
            'album': str(metadata.get('xesam:album', '')),
            'art': str(metadata.get('mpris:artUrl', '')),
            'status': str(player.property('PlaybackStatus') or 'Stopped'),
            'can_play': bool(player.property('CanPlay')),
            'can_pause': bool(player.property('CanPause')),
            'can_next': bool(player.property('CanGoNext')),
            'can_previous': bool(player.property('CanGoPrevious')),
            # Mini-player extras (Linux MPRIS). Position is read separately via position().
            'length': int(metadata.get('mpris:length', 0) or 0),
            'track_id': str(metadata.get('mpris:trackid', '')),
            'can_seek': bool(player.property('CanSeek')),
            'shuffle': bool(player.property('Shuffle')),
            'loop': str(player.property('LoopStatus') or 'None'),
            'volume': float(player.property('Volume') if player.property('Volume') is not None else -1),
        })

    def position(self):
        """Current playback position in microseconds (MPRIS does not signal it, so poll)."""
        if os.name == 'nt' or not self.service or self.bus is None:
            return 0
        value = self._linux_interface(self.service, 'org.mpris.MediaPlayer2.Player').property('Position')
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def seek_to(self, microseconds):
        if os.name == 'nt' or not self.service or not (self.last or {}).get('track_id'):
            return
        from PySide6.QtDBus import QDBusObjectPath
        player = self._linux_interface(self.service, 'org.mpris.MediaPlayer2.Player')
        player.call('SetPosition', QDBusObjectPath(self.last['track_id']), int(max(0, microseconds)))
        QTimer.singleShot(150, self.refresh)

    def set_property(self, name, value):
        """Shuffle (bool), LoopStatus ('None'/'Track'/'Playlist') or Volume (0.0–1.0)."""
        if name not in ('Shuffle', 'LoopStatus', 'Volume') or os.name == 'nt' or not self.service:
            return
        from PySide6.QtDBus import QDBusInterface, QDBusVariant
        properties = QDBusInterface(self.service, '/org/mpris/MediaPlayer2', 'org.freedesktop.DBus.Properties', self.bus)
        properties.setTimeout(250)
        properties.call('Set', 'org.mpris.MediaPlayer2.Player', name, QDBusVariant(value))
        QTimer.singleShot(150, self.refresh)

    def _publish(self, state):
        if state != self.last:
            self.last = state
            self.changed.emit(state)

    def control(self, method):
        if method not in ('PlayPause', 'Next', 'Previous'):
            raise ValueError('Unsupported media action')
        if os.name == 'nt':
            self._windows(method)
        elif self.service:
            from PySide6.QtDBus import QDBusMessage
            reply = self._linux_interface(self.service, 'org.mpris.MediaPlayer2.Player').call(method)
            if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                self.error.emit(reply.errorMessage())
            else:
                QTimer.singleShot(150, self.refresh)

    def _windows(self, action):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        helper = ROOT / 'windows_media.ps1'
        process = QProcess(self)
        self.process = process
        process.setProgram('powershell.exe')
        process.setArguments(['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-Action', action])
        def finished(code, status):
            text = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace').strip()
            if code == 0:
                try:
                    self._publish(json.loads(text))
                except ValueError:
                    self.error.emit('Windows media service returned an invalid response.')
            else:
                self._publish({'available': False, 'reason': 'Windows media session unavailable.'})
            process.deleteLater()
            self.process = None
        process.finished.connect(finished)
        process.errorOccurred.connect(lambda error: self.error.emit(process.errorString()))
        process.start()

    def open_app(self):
        if os.name == 'nt':
            if not QDesktopServices.openUrl(QUrl('simpmusic://')):
                self.error.emit(tr('Install SimpMusic and register its simpmusic:// protocol.'))
        else:
            desktop = Path.home() / '.local/share/applications/simpmusic.desktop'
            if desktop.exists() and shutil.which('gio'):
                subprocess.Popen(['gio', 'launch', str(desktop)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                QDesktopServices.openUrl(QUrl('simpmusic://'))


class MusicWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.bridge = SimpMusic(self)
        self.art_url = ''
        self.network = QNetworkAccessManager(self)
        self.reply = None
        row = QHBoxLayout(self)
        self.art = QLabel('SM')
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setFixedSize(64, 64)
        self.art.setStyleSheet('background:#232323;border-radius:10px;color:#e84a3c;font-size:22px;')
        row.addWidget(self.art)
        info = QVBoxLayout()
        self.title = QLabel('SimpMusic')
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.artist = QLabel(tr('Open SimpMusic to connect its media session.'))
        self.artist.setObjectName('muted')
        self.artist.setTextFormat(Qt.TextFormat.PlainText)
        self.artist.setWordWrap(True)
        info.addWidget(self.title)
        info.addWidget(self.artist)
        row.addLayout(info, 1)
        self.previous = QPushButton(tr('Previous'))
        self.play = QPushButton(tr('Play / pause'))
        self.next = QPushButton(tr('Next'))
        self.open = QPushButton(tr('Open SimpMusic'))
        for control, method in [(self.previous, 'Previous'), (self.play, 'PlayPause'), (self.next, 'Next')]:
            control.setEnabled(False)
            control.clicked.connect(lambda checked=False, m=method: self.bridge.control(m))
            row.addWidget(control)
        self.open.clicked.connect(self.bridge.open_app)
        row.addWidget(self.open)
        self.bridge.changed.connect(self.update_state)
        self.bridge.error.connect(self.artist.setText)

    def update_state(self, state):
        available = state.get('available', False)
        self.title.setText(state.get('title', 'SimpMusic'))
        if available:
            status = {'Playing': tr('Playing'), 'Paused': tr('Paused'), 'Stopped': tr('Stopped')}.get(state.get('status'), state.get('status', ''))
            self.artist.setText(' · '.join(x for x in [state.get('artist'), state.get('album'), status] if x))
        else:
            self.artist.setText(tr(state.get('reason', tr('No media session'))))
        self.previous.setEnabled(available and state.get('can_previous', False))
        self.next.setEnabled(available and state.get('can_next', False))
        playing = state.get('status') == 'Playing'
        self.play.setText(tr('Pause') if playing else tr('Play'))
        self.play.setEnabled(available and state.get('can_pause' if playing else 'can_play', False))
        url = state.get('art', '')
        if url != self.art_url:
            self.art_url = url
            self.art.clear()
            self.art.setText('SM')
            parsed = QUrl(url)
            if parsed.isLocalFile():
                self._artwork(QPixmap(parsed.toLocalFile()))
            elif parsed.scheme() == 'https':
                request = QNetworkRequest(parsed)
                request.setTransferTimeout(8000)
                reply = self.network.get(request)
                self.reply = reply
                reply.finished.connect(lambda r=reply, expected=url: self._downloaded(r, expected))

    def _downloaded(self, reply, expected):
        if expected == self.art_url and reply.error() == reply.NetworkError.NoError:
            content = reply.read(2 * 1024 * 1024)
            pixmap = QPixmap()
            pixmap.loadFromData(content)
            self._artwork(pixmap)
        reply.deleteLater()
        if self.reply is reply:
            self.reply = None

    def _artwork(self, pixmap):
        if not pixmap.isNull():
            self.art.setPixmap(pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))

    def showEvent(self, event):
        self.bridge.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.bridge.stop()
        super().hideEvent(event)
