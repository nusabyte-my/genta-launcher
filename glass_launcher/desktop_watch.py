"""What is happening on the desktop: focused app, open windows, battery. Local only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

HELPER = Path(__file__).resolve().parent.parent / "bin/genta-desktop-watch"


def battery():
    """(percent, discharging) or None when there is no battery."""
    for supply in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            percent = int((supply / "capacity").read_text().strip())
            status = (supply / "status").read_text().strip()
        except (OSError, ValueError):
            continue
        return percent, status == "Discharging"
    return None


class DesktopWatch(QObject):
    """Runs the Wayland helper and keeps the active app and how long it has been focused."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.windows = []
        self.active_app = ""
        self.active_title = ""
        self.active_since = time.monotonic()
        self.error = ""
        self.process = None
        self._buffer = b""
        self._restarts = 0
        self.restart_timer = QTimer(self)
        self.restart_timer.setSingleShot(True)
        self.restart_timer.timeout.connect(self.start)

    def available(self):
        return bool(self.windows) and not self.error

    def start(self):
        if not sys.platform.startswith("linux") or not os.environ.get("WAYLAND_DISPLAY"):
            self.error = "Desktop watching needs a Wayland session (COSMIC)."
            return
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.remove("QT_QPA_PLATFORM")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.start(sys.executable, [str(HELPER)])

    def stop(self):
        self.restart_timer.stop()
        if self.process is not None:
            self.process.finished.disconnect(self._finished)
            self.process.kill()
            self.process.waitForFinished(500)
            self.process = None

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "error" in message:
                self.error = str(message["error"])
                continue
            self.error = ""
            self._restarts = 0
            self.windows = [w for w in message.get("windows", []) if isinstance(w, dict)]
            active = next((w for w in self.windows if w.get("active")), None)
            app = (active or {}).get("app_id", "")
            if app != self.active_app:
                self.active_app = app
                self.active_since = time.monotonic()
            self.active_title = (active or {}).get("title", "")
            self.changed.emit()

    def _finished(self, *args):
        self.process = None
        if self._restarts < 5 and not self.error:
            self._restarts += 1
            self.restart_timer.start(3000 * self._restarts)

    def minutes_on_active(self):
        return int((time.monotonic() - self.active_since) // 60) if self.active_app else 0

    def activate(self, window_id):
        """Bring a window to the front via the helper (COSMIC toplevel management)."""
        return self._command("activate", window_id)

    def minimize(self, window_id):
        return self._command("minimize", window_id)

    def find(self, word):
        """First window whose app id or title contains word (case-insensitive)."""
        word = word.casefold()
        return next((w for w in self.windows if word in (w.get("app_id", "") + " " + w.get("title", "")).casefold()), None)

    def _command(self, verb, window_id):
        if self.process is None or self.process.state() != QProcess.ProcessState.Running:
            return False
        if not any(w.get("id") == window_id for w in self.windows):
            return False
        self.process.write(f"{verb} {int(window_id)}\n".encode())
        return True
