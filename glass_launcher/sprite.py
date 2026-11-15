"""The Genta agent: a small dot-matrix bell (genta = bell) that shows what the agent is doing.

States and colours (see style.py):
  idle      grey, slow breathing            watching  grey, one teal dot circles the rim
  thinking  teal, clapper swings            tip       amber, rings with sound-wave dots
  focus     red, fills from the bottom      done      green, sparkle dots (brief)
  error     grey, half the dots dimmed
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

BELL = [
    "......#......",
    ".....###.....",
    "...#######...",
    "..#########..",
    "..#########..",
    ".###########.",
    ".###########.",
    ".###########.",
    "#############",
    "#############",
    ".............",
]
CLAPPER = [(5, 11), (6, 11), (7, 11), (6, 12)]
COLORS = {
    "idle": "#9A9A9A", "watching": "#9A9A9A", "thinking": "#3EC7C2", "tip": "#F0B429",
    "focus": "#D71921", "done": "#5EDB8C", "error": "#6A6A6A",
}
RIM = [(x, y) for y, row in enumerate(BELL) for x, c in enumerate(row)
       if c == "#" and any(not (0 <= y + dy < len(BELL) and 0 <= x + dx < len(row)) or BELL[y + dy][x + dx] != "#"
                           for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
RIM.sort(key=lambda p: math.atan2(p[1] - 6, p[0] - 6))


class BellSprite(QWidget):
    def __init__(self, dot=2.4, parent=None):
        super().__init__(parent)
        self.dot = dot
        self.state = "idle"
        self.progress = 0.0
        self._done_until = 0.0
        self._frame = 0
        size = int(17 * dot)
        self.setFixedSize(size, size)
        self.timer = QTimer(self)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self._advance)

    def set_state(self, state, progress=0.0):
        if time.monotonic() < self._done_until and state not in ("thinking", "error"):
            state = "done"
        if (state, round(progress, 2)) != (self.state, round(self.progress, 2)):
            self.state, self.progress = state, progress
            self.setToolTip({"idle": "Genta is resting", "watching": "Genta is watching your desktop",
                             "thinking": "Genta is thinking", "tip": "Genta has a tip for you",
                             "focus": f"Focus · {int(progress * 100)}%", "done": "Done",
                             "error": "Genta can't see the desktop"}[state])
            self.update()

    def celebrate(self, seconds=2.0):
        self._done_until = time.monotonic() + seconds
        self.set_state("done")

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _advance(self):
        self._frame += 1
        if self.state == "done" and time.monotonic() >= self._done_until:
            self.state = "idle"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(0, 0, 0, 0))
        frame, state = self._frame, self.state
        base = QColor(COLORS[state])
        radius = self.dot * 0.36
        offset_x = (self.width() - 13 * self.dot) / 2
        offset_y = (self.height() - 14 * self.dot) / 2 + self.dot * 0.5
        breathe = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(frame / 7.0))
        swing = {0: 0, 1: 1, 2: 0, 3: -1}[(frame // 3) % 4] if state == "thinking" else 0
        rim_index = (frame // 2) % len(RIM)
        filled_rows = 10 * self.progress

        def dot(x, y, color):
            painter.setBrush(color)
            cx, cy = offset_x + x * self.dot, offset_y + y * self.dot
            painter.drawEllipse(QRectF(cx, cy, radius * 2, radius * 2))

        for y, row in enumerate(BELL):
            for x, c in enumerate(row):
                if c != "#":
                    continue
                color = QColor(base)
                if state in ("idle", "watching"):
                    color.setAlphaF(0.35 + 0.4 * breathe)
                elif state == "focus":
                    color = QColor(base) if (len(BELL) - 1 - y) < filled_rows else QColor(255, 255, 255, 60)
                elif state == "error" and (x + y) % 2:
                    color.setAlphaF(0.25)
                if state == "watching" and (x, y) == RIM[rim_index]:
                    color = QColor(COLORS["thinking"])
                dot(x, y, color)
        for x, y in CLAPPER:
            dot(x + swing, y, base)
        if state == "tip" and (frame // 4) % 2 == 0:
            for x, y in ((-1.5, 3), (-2.5, 5), (14.5, 3), (15.5, 5)):
                dot(x, y, base)
        if state == "done":
            for index, (x, y) in enumerate(((-1.5, 1), (14.5, 1), (-2, 8), (15, 8))):
                if (frame + index) % 3:
                    dot(x, y, base)
