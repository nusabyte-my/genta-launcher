"""Built-in Genta widgets. Each reads and drives the live Genta window (shared process)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import html
from pathlib import Path
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QTextCursor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from art import draw_dot_text
from music_widget import SimpMusic

from ..style import ACCENT, INK
from ..ui import label, push
from . import LauncherWidget


def clock_text(clock):
    value = math.ceil(clock.remaining()) if clock.duration else int(clock.remaining())
    return f"{value // 60:02}:{value % 60:02}"


class DotClock(QWidget):
    def __init__(self, scale=4):
        super().__init__()
        self.text, self.scale, self.color = "25:00", scale, INK
        self.setFixedSize(int(5 * 6 * scale + 2), int(7 * scale + 4))

    def set(self, text, color):
        if (text, color) != (self.text, self.color):
            self.text, self.color = text, color
            self.setAccessibleName(text)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_dot_text(painter, self.text, 1, 2, self.scale, self.color)


def button_row(*buttons):
    row = QHBoxLayout()
    row.setSpacing(6)
    for widget in buttons:
        row.addWidget(widget)
    row.addStretch()
    return row


class FocusWidget(LauncherWidget):
    id, title, description = "focus", "FOCUS", "Genta focus timer: start, pause, +5 min, finish"

    def __init__(self, context):
        super().__init__(context)
        self.dots = DotClock()
        self.body.addWidget(self.dots)
        self.state = self.add_label("", "muted", wrap=False)
        self.toggle_button = push("Start", self.toggle, "primary")
        self.extend_button = push("+5", self.extend, None, "Add five minutes")
        self.end_button = push("Finish", self.finish, None, "Finish and save this session")
        self.body.addLayout(button_row(self.toggle_button, self.extend_button, self.end_button))

    def refresh(self):
        self.tick()

    def tick(self):
        workspace = self.context.workspace
        clock = workspace.clock
        active = clock.running or clock.active_seconds() > 0
        finished = bool(clock.duration) and clock.remaining() <= 0
        task = ""
        if clock.task_id:
            row = self.context.store.conn.execute("SELECT title FROM tasks WHERE id = ?", (clock.task_id,)).fetchone()
            task = row[0] if row else ""
        if clock.running:
            self.dots.set(clock_text(clock), ACCENT)
            self.state.setText(f"Focusing · {task}" if task else "Focusing")
            self.toggle_button.setText("Pause")
        elif active and not finished:
            self.dots.set(clock_text(clock), INK)
            self.state.setText("Paused" + (f" · {task}" if task else ""))
            self.toggle_button.setText("Resume")
        else:
            minutes = (clock.duration or 0) // 60
            self.dots.set(f"{minutes:02}:00" if clock.duration else "00:00", "#66000000")  # QColor takes #AARRGGBB
            self.state.setText(f"Ready · {minutes} min" if clock.duration else "Ready · stopwatch")
            self.toggle_button.setText("Start")
        self.extend_button.setEnabled(active and bool(clock.duration))
        self.end_button.setEnabled(active)

    def toggle(self):
        self.context.workspace.toggle_focus()
        self.tick()

    def extend(self):
        self.context.workspace.extend_focus()
        self.tick()

    def finish(self):
        self.context.workspace.end_focus()
        self.tick()


class TasksWidget(LauncherWidget):
    id, title, description = "tasks", "TODAY", "Today's open tasks, tick them off or start focus"
    LIMIT = 4

    def __init__(self, context):
        super().__init__(context)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(3)
        self.body.addLayout(self.rows)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Add a task — “call Sam at 5pm”")
        self.entry.returnPressed.connect(self.add)
        self.body.addWidget(self.entry)

    def refresh(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        today = date.today().isoformat()
        try:
            tasks = self.context.store.tasks(include_done=False)
        except Exception as error:
            self.context.report(str(error))
            return
        due = [task for task in tasks if task["day"] and task["day"] <= today]
        due.sort(key=lambda task: (task["due_at"] is None, task["due_at"] or "", -task["priority"]))
        if not due:
            self.rows.addWidget(label("Nothing due. Add a small first step.", "muted"))
        for task in due[:self.LIMIT]:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(6)
            check = QCheckBox()
            check.setCursor(Qt.CursorShape.PointingHandCursor)
            check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            check.setToolTip("Mark done")
            check.toggled.connect(lambda done, t=task: self.complete(t, done))
            line.addWidget(check)
            when = f"{task['due_at'][11:16]}  " if task["due_at"] else ""
            title = label(when + task["title"])
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            title.setToolTip(task["title"])
            line.addWidget(title, 1)
            line.addWidget(push("▶", lambda checked=False, t=task: self.focus(t), "icon", "Focus on this task"))
            self.rows.addWidget(row)
        if len(due) > self.LIMIT:
            self.rows.addWidget(label(f"+{len(due) - self.LIMIT} more in Genta", "muted"))

    def complete(self, task, done):
        workspace = self.context.workspace
        if done and workspace.change_task(task["id"], done=True):
            QTimer.singleShot(250, self.refresh)
        elif done:
            self.context.report(workspace.status.text())

    def focus(self, task):
        self.context.workspace.focus_task(task)

    def add(self):
        if add_task(self.context, self.entry.text()):
            self.entry.clear()
            self.refresh()


def add_task(context, text):
    text = text.strip()
    if not text:
        return False
    try:
        context.store.add_task(text, day=date.today().isoformat())
    except (ValueError, KeyError, OSError) as error:
        context.report(str(error))
        return False
    context.workspace.refresh_tasks()
    return True


class CalendarWidget(LauncherWidget):
    id, title, description = "calendar", "NEXT", "Your next calendar event in the coming 24 hours"

    def __init__(self, context):
        super().__init__(context)
        self.when = self.add_label("", "metric", wrap=False)
        self.what = self.add_label("", None)

    def refresh(self):
        now = datetime.now()
        stamp = now.isoformat(timespec="seconds")
        horizon = (now + timedelta(hours=24)).isoformat(timespec="seconds")
        try:
            events = self.context.workspace.calendars.events()
        except Exception:
            events = []
        upcoming = [e for e in events if (e.get("end") or e["start"]) >= stamp and e["start"] <= horizon]
        if not upcoming:
            self.when.setText("Free")
            self.what.setText("No events in the next 24 hours.")
            return
        event = upcoming[0]
        start = event["start"]
        if start <= stamp:
            when = "Now"
        elif len(start) >= 16:
            when = ("Tomorrow " if start[:10] != now.date().isoformat() else "") + start[11:16]
        else:
            when = "All day"
        self.when.setText(when)
        self.what.setText(event.get("title") or "Untitled event")


def fmt_time(microseconds):
    seconds = max(0, int(microseconds // 1_000_000))
    return f"{seconds // 60}:{seconds % 60:02}"


class SeekBar(QWidget):
    """Thin progress bar; click or drag to seek."""

    def __init__(self, on_seek):
        super().__init__()
        self.on_seek = on_seek
        self.fraction = 0.0
        self.enabled = False
        self.setFixedHeight(14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set(self, fraction, enabled):
        fraction = max(0.0, min(1.0, fraction))
        if (fraction, enabled) != (self.fraction, self.enabled):
            self.fraction, self.enabled = fraction, enabled
            self.update()

    def paintEvent(self, event):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0, self.height() / 2 - 2, self.width(), 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawRoundedRect(track, 2, 2)
        done = QRectF(track.left(), track.top(), track.width() * self.fraction, track.height())
        painter.setBrush(QColor(INK))
        painter.drawRoundedRect(done, 2, 2)
        if self.enabled:
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(done.right() - 5, self.height() / 2 - 5, 10, 10)

    def mousePressEvent(self, event):
        if self.enabled and self.width() > 0:
            self.on_seek(event.position().x() / self.width())

    mouseMoveEvent = mousePressEvent


class MusicWidget(LauncherWidget):
    """A SimpMusic mini player inside Genta: artwork, seek, shuffle/repeat, volume.

    COSMIC (Wayland) cannot embed another app's window, so Genta drives SimpMusic over
    MPRIS and can start it minimized; "Show player" brings its full window forward.
    """
    id, title, description = "music", "NOW PLAYING", "SimpMusic mini player: artwork, seek, shuffle, repeat, volume"
    span = 2

    def __init__(self, context):
        super().__init__(context)
        from PySide6.QtGui import QPixmap
        from PySide6.QtNetwork import QNetworkAccessManager
        from PySide6.QtWidgets import QLabel, QSlider
        self.bridge = SimpMusic(self)
        self.network = QNetworkAccessManager(self)
        self.state = {}
        self.art_url = None
        self.reply = None
        self._pending_minimize = 0
        row = QHBoxLayout()
        row.setSpacing(14)
        self.art = QLabel("♪")
        self.art.setFixedSize(88, 88)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setStyleSheet(f"background: {INK}; color: #FFC6E2; border-radius: 14px; font-size: 30pt;")
        row.addWidget(self.art)
        info = QVBoxLayout()
        info.setSpacing(4)
        self.track = label("", "title")
        self.track.setStyleSheet("font-size: 12.5pt;")
        self.artist = label("", "muted", wrap=False)
        self.track.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.artist.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info.addWidget(self.track)
        info.addWidget(self.artist)
        self.seek = SeekBar(self.seek_fraction)
        info.addWidget(self.seek)
        times = QHBoxLayout()
        self.elapsed = label("0:00", "eyebrow")
        self.total = label("0:00", "eyebrow")
        times.addWidget(self.elapsed)
        times.addStretch()
        times.addWidget(self.total)
        info.addLayout(times)
        row.addLayout(info, 1)
        self.body.addLayout(row)

        self.shuffle = push("⤮", self.toggle_shuffle, "icon", "Shuffle")
        self.previous = push("⏮", lambda: self.media("Previous"), "icon", "Previous")
        self.play = push("▶", lambda: self.media("PlayPause"), "primary", "Play / pause")
        self.next = push("⏭", lambda: self.media("Next"), "icon", "Next")
        self.loop = push("↻", self.cycle_loop, "icon", "Repeat: off")
        for widget in (self.shuffle, self.previous, self.play, self.next, self.loop):
            widget.setMinimumWidth(40)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume.setToolTip("Volume")
        self.volume.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: rgba(0,0,0,40); border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {INK}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {INK}; width: 12px; margin: -5px 0; border-radius: 6px; }}")
        self.volume.sliderReleased.connect(lambda: self.bridge.set_property("Volume", self.volume.value() / 100))
        self.start_button = push("Start SimpMusic", self.start_player, "primary", "Start SimpMusic minimized and control it here")
        self.show_button = push("Show player", self.show_player, None, "Bring SimpMusic's full window forward")
        controls = button_row(self.shuffle, self.previous, self.play, self.next, self.loop)
        controls.insertStretch(5)
        controls.addWidget(label("VOL", "eyebrow"))
        controls.addWidget(self.volume)
        self.body.addLayout(controls)
        self.body.addLayout(button_row(self.start_button, self.show_button))
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(1000)
        self.position_timer.timeout.connect(self.update_position)
        self.bridge.changed.connect(self.update_state)
        self.bridge.error.connect(self.artist.setText)
        self.update_state({"available": False})

    def refresh(self):
        self.bridge.start()
        self.position_timer.start()

    def stop(self):
        self.bridge.stop()
        self.position_timer.stop()

    def update_state(self, state):
        self.state = state
        available = state.get("available", False)
        playing = state.get("status") == "Playing"
        if available:
            self.track.setText(state.get("title") or "SimpMusic")
            self.artist.setText(" · ".join(x for x in (state.get("artist"), state.get("album")) if x) or state.get("status", ""))
        else:
            self.track.setText("SimpMusic is not running")
            self.artist.setText("Start it here; Genta keeps it minimized and plays from this card.")
        self.play.setText("⏸" if playing else "▶")
        self.play.setEnabled(available and state.get("can_pause" if playing else "can_play", False))
        self.previous.setEnabled(available and state.get("can_previous", False))
        self.next.setEnabled(available and state.get("can_next", False))
        self.shuffle.setEnabled(available)
        self.loop.setEnabled(available)
        self.shuffle.setText("⤮" + (" ●" if state.get("shuffle") else ""))
        loop = state.get("loop", "None")
        self.loop.setText({"Track": "↻1", "Playlist": "↻ ●"}.get(loop, "↻"))
        self.loop.setToolTip(f"Repeat: {dict(Track='this song', Playlist='all').get(loop, 'off')}")
        volume = state.get("volume", -1)
        self.volume.setEnabled(available and volume >= 0)
        if volume >= 0 and not self.volume.isSliderDown():
            self.volume.setValue(int(round(volume * 100)))
        self.start_button.setVisible(not available)
        self.show_button.setVisible(available)
        self.total.setText(fmt_time(state.get("length", 0)))
        self.load_art(state.get("art", "") if available else "")
        self.update_position()

    def update_position(self):
        length = self.state.get("length", 0)
        if not self.state.get("available") or not length:
            self.seek.set(0, False)
            self.elapsed.setText("0:00")
            return
        position = self.bridge.position()
        self.seek.set(position / length, self.state.get("can_seek", False))
        self.elapsed.setText(fmt_time(position))

    def seek_fraction(self, fraction):
        length = self.state.get("length", 0)
        if length and self.state.get("can_seek"):
            self.bridge.seek_to(int(length * max(0.0, min(1.0, fraction))))
            QTimer.singleShot(200, self.update_position)

    def toggle_shuffle(self):
        self.bridge.set_property("Shuffle", not self.state.get("shuffle", False))

    def cycle_loop(self):
        order = ["None", "Playlist", "Track"]
        current = self.state.get("loop", "None")
        self.bridge.set_property("LoopStatus", order[(order.index(current) + 1) % 3 if current in order else 0])

    def media(self, method):
        try:
            self.bridge.control(method)
        except ValueError as error:
            self.context.report(str(error))

    def load_art(self, url):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QPixmap
        from PySide6.QtNetwork import QNetworkRequest
        if url == self.art_url:
            return
        self.art_url = url
        self.art.setPixmap(QPixmap())
        self.art.setText("♪")
        target = QUrl(url)
        if target.isLocalFile():
            self.set_art(QPixmap(target.toLocalFile()))
        elif target.scheme() == "https":
            request = QNetworkRequest(target)
            request.setTransferTimeout(8000)
            reply = self.network.get(request)
            self.reply = reply

            def done(r=reply, expected=url):
                if expected == self.art_url and r.error() == r.NetworkError.NoError:
                    pixmap = QPixmap()
                    pixmap.loadFromData(r.read(4 * 1024 * 1024))
                    self.set_art(pixmap)
                r.deleteLater()

            reply.finished.connect(done)

    def set_art(self, pixmap):
        from PySide6.QtGui import QPainterPath, QPixmap
        if pixmap.isNull():
            return
        size = self.art.size()
        scaled = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        rounded = QPixmap(size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size.width(), size.height(), 14, 14)
        painter.setClipPath(path)
        painter.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
        painter.end()
        self.art.setPixmap(rounded)

    # ── SimpMusic window ───────────────────────────────────────
    def watch(self):
        engine = getattr(self.context.workspace, "tip_engine", None)
        return engine.watch if engine else None

    def start_player(self):
        self.bridge.open_app()
        self.artist.setText("Starting SimpMusic…")
        self._pending_minimize = 20
        QTimer.singleShot(1000, self._minimize_when_ready)

    def _minimize_when_ready(self):
        watch = self.watch()
        window = watch.find("simpmusic") if watch else None
        if window and watch.minimize(window["id"]):
            QTimer.singleShot(500, self.bridge.refresh)
            return
        self._pending_minimize -= 1
        if self._pending_minimize > 0:
            QTimer.singleShot(1000, self._minimize_when_ready)

    def show_player(self):
        watch = self.watch()
        window = watch.find("simpmusic") if watch else None
        if window and watch.activate(window["id"]):
            self.context.close()
        else:
            self.bridge.open_app()


class NoteWidget(LauncherWidget):
    id, title, description = "note", "NOTE", "Append a line to today's Genta note"

    def __init__(self, context):
        super().__init__(context)
        self.preview = self.add_label("", "muted")
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Jot something down…")
        self.entry.returnPressed.connect(self.append)
        self.body.addWidget(self.entry)

    def refresh(self):
        workspace = self.context.workspace
        today = date.today().isoformat()
        text = workspace.notes.toPlainText() if workspace.note_day == today else workspace.store.note(today)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.preview.setText("\n".join(line if len(line) <= 52 else line[:51] + "…" for line in lines[-2:]) or "Today's note is empty.")

    def append(self):
        text = self.entry.text().strip()
        if not text:
            return
        workspace = self.context.workspace
        today = date.today().isoformat()
        try:
            if workspace.note_day == today:
                # Write through Genta's open editor so its unsaved state never overwrites this line.
                existing = workspace.notes.toPlainText()
                workspace.notes.moveCursor(QTextCursor.MoveOperation.End)
                workspace.notes.insertPlainText(("\n" if existing and not existing.endswith("\n") else "") + text)
                workspace.save_note()
            else:
                existing = workspace.store.note(today)
                workspace.store.save_note(today, existing + ("\n" if existing and not existing.endswith("\n") else "") + text)
        except (ValueError, OSError) as error:
            self.context.report(str(error))
            return
        self.entry.clear()
        self.refresh()


class InsightsWidget(LauncherWidget):
    id, title, description = "insights", "TODAY'S PROGRESS", "Focus minutes, completed tasks and streak"

    def __init__(self, context):
        super().__init__(context)
        self.value = self.add_label("", "metric", wrap=False)
        self.detail = self.add_label("", "muted")

    def refresh(self):
        try:
            data = self.context.store.insights()
        except Exception as error:
            self.context.report(str(error))
            return
        minutes = int(data.get("focus_today_seconds", 0)) // 60
        self.value.setText(f"{minutes} min focused")
        streak = data.get("streak", 0)
        self.detail.setText(f"{data.get('completed_today', 0)} tasks done · {streak}-day streak")


class TipsWidget(LauncherWidget):
    id, title, description = "tips", "TIPS", "Suggestions from what's happening on your desktop"

    def __init__(self, context):
        super().__init__(context)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        self.body.addLayout(self.rows)
        self.status = self.add_label("", "muted")
        self.engine = getattr(context.workspace, "tip_engine", None)
        if self.engine is not None:
            self.engine.changed.connect(self.refresh)

    def refresh(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget() or None
            if widget:
                widget.deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        engine = self.engine
        if engine is None:
            self.status.setText("Tips are unavailable.")
            return
        for tip in engine.tips[:3]:
            text = label(("✦ " if tip.source != "Genta" else "") + tip.text, None, wrap=True)
            text.setToolTip(f"From {tip.source}")
            self.rows.addWidget(text)
            buttons = []
            if tip.action_label:
                buttons.append(push(tip.action_label, lambda checked=False, t=tip: engine.run(t), "primary"))
            buttons.append(push("Not now", lambda checked=False, t=tip: engine.snooze(t), None, "Hide for 45 minutes"))
            buttons.append(push("Never", lambda checked=False, t=tip: engine.mute(t), None, "Stop this kind of tip"))
            holder = QWidget()
            holder.setLayout(button_row(*buttons))
            holder.layout().setContentsMargins(0, 0, 0, 4)
            self.rows.addWidget(holder)
        watching = engine.watch.available()
        notes = []
        if not engine.tips:
            notes.append("All clear. Tips appear here as your day unfolds.")
        if not watching:
            notes.append(engine.watch.error or "Starting the desktop watcher…")
        if engine.ai_status:
            notes.append("AI tips paused: " + engine.ai_status)
        self.status.setText("\n".join(notes))


class TodoWidget(LauncherWidget):
    """A real checklist: Today / Inbox / Upcoming tabs, priority dots, strike-through on done."""
    id, title, description = "todo", "TO-DO", "Checklist with Today / Inbox / Upcoming, priorities and quick add"
    span = 2
    LIMIT = 7
    TABS = ("TODAY", "INBOX", "UPCOMING")

    def __init__(self, context):
        super().__init__(context)
        self.tab = 0
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_buttons = []
        for index, name in enumerate(self.TABS):
            button = push(name, lambda checked=False, i=index: self.select(i))
            self.tab_buttons.append(button)
            tabs.addWidget(button)
        tabs.addStretch()
        self.count = label("", "eyebrow")
        tabs.addWidget(self.count)
        self.body.addLayout(tabs)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        self.body.addLayout(self.rows)
        entry_row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("New to-do — “pay bills friday 9am”, add ! for high priority")
        self.entry.returnPressed.connect(self.add)
        entry_row.addWidget(self.entry, 1)
        entry_row.addWidget(push("Add", self.add, "primary"))
        self.body.addLayout(entry_row)

    def select(self, index):
        self.tab = index
        self.refresh()

    def tasks_for_tab(self, tasks):
        today = date.today().isoformat()
        if self.tab == 0:
            chosen = [t for t in tasks if t["day"] and t["day"] <= today]
        elif self.tab == 1:
            chosen = [t for t in tasks if not t["day"]]
        else:
            chosen = [t for t in tasks if t["day"] and t["day"] > today]
        chosen.sort(key=lambda t: (t["day"] or "", -t["priority"], t["due_at"] is None, t["due_at"] or ""))
        return chosen

    def refresh(self):
        for index, button in enumerate(self.tab_buttons):
            button.setObjectName("primary" if index == self.tab else "")
            button.style().unpolish(button)
            button.style().polish(button)
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            tasks = self.tasks_for_tab(self.context.store.tasks(include_done=False))
        except Exception as error:
            self.context.report(str(error))
            return
        self.count.setText(f"{len(tasks)} OPEN")
        if not tasks:
            self.rows.addWidget(label(("Nothing due today." if self.tab == 0 else "Empty.") + " Add one below.", "muted"))
        today = date.today()
        for task in tasks[:self.LIMIT]:
            self.rows.addWidget(self.row(task, today))
        if len(tasks) > self.LIMIT:
            self.rows.addWidget(label(f"+{len(tasks) - self.LIMIT} more in Genta", "muted"))

    def row(self, task, today):
        widget = QWidget()
        line = QHBoxLayout(widget)
        line.setContentsMargins(0, 2, 0, 2)
        line.setSpacing(8)
        check = QCheckBox()
        check.setCursor(Qt.CursorShape.PointingHandCursor)
        check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        check.setToolTip("Done")
        line.addWidget(check)
        dot = label("●" if task["priority"] else "", None, wrap=False)
        dot.setStyleSheet(f"color: {ACCENT if task['priority'] >= 2 else '#C77700'}; font-size: 9pt;")
        dot.setFixedWidth(10)
        line.addWidget(dot)
        title = label(task["title"], None, wrap=False)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setToolTip(task["title"])
        line.addWidget(title, 1)
        when = ""
        if task["day"]:
            day = date.fromisoformat(task["day"])
            when = "OVERDUE" if day < today else "" if day == today else ("TMRW" if day == today + timedelta(days=1) else day.strftime("%a %-d").upper())
        if task["due_at"]:
            when = (when + " " if when else "") + task["due_at"][11:16]
        if when:
            tag = label(when, "eyebrow", wrap=False)
            if when.startswith("OVERDUE"):
                tag.setStyleSheet(f"color: {ACCENT};")
            line.addWidget(tag)
        line.addWidget(push("▶", lambda checked=False, t=task: self.context.workspace.focus_task(t), "icon", "Focus on this"))

        def done(checked, t=task, w=title):
            if not checked:
                return
            w.setText(f"<s>{html.escape(t['title'])}</s>")
            w.setTextFormat(Qt.TextFormat.RichText)
            if self.context.workspace.change_task(t["id"], done=True):
                QTimer.singleShot(700, self.refresh)

        check.toggled.connect(done)
        return widget

    def add(self):
        text = self.entry.text().strip()
        if not text:
            return
        priority = 2 if text.startswith("!") or text.endswith("!") else 0
        text = text.strip("! ").strip()
        day = None if self.tab == 1 else date.today().isoformat()
        try:
            self.context.store.add_task(text, day=day, priority=priority)
        except (ValueError, KeyError, OSError) as error:
            self.context.report(str(error))
            return
        self.entry.clear()
        self.context.workspace.refresh_tasks()
        self.refresh()


class SolatWidget(LauncherWidget):
    """Waktu solat for a Malaysian JAKIM zone, next prayer countdown, notifications."""
    id, title, description = "solat", "WAKTU SOLAT", "Malaysian prayer times (JAKIM) with reminders; pick a zone or auto"

    def __init__(self, context):
        super().__init__(context)
        self.service = getattr(context.workspace, "solat", None)
        self.next = self.add_label("", "metric", wrap=False)
        self.countdown = self.add_label("", "muted", wrap=False)
        grid = QHBoxLayout()
        grid.setSpacing(4)
        self.cells = {}
        from ..solat import NOTIFY, PRAYERS
        self.order = [(key, name) for key, name in PRAYERS if key in NOTIFY]
        for key, name in self.order:
            cell = QVBoxLayout()
            cell.setSpacing(0)
            top, bottom = label(name.upper(), "eyebrow", wrap=False), label("--:--", None, wrap=False)
            top.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bottom.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(top)
            cell.addWidget(bottom)
            holder = QWidget()
            holder.setLayout(cell)
            self.cells[key] = (holder, top, bottom)
            grid.addWidget(holder, 1)
        self.body.addLayout(grid)
        self.place = label("", "eyebrow", wrap=False)
        self.zone_button = push("Zone", self.choose_zone, None, "Choose a JAKIM zone or auto-detect")
        footer = QHBoxLayout()
        footer.addWidget(self.place, 1)
        footer.addWidget(self.zone_button)
        self.body.addLayout(footer)
        if self.service is not None:
            self.service.changed.connect(self.refresh)

    def refresh(self):
        service = self.service
        if service is None or not service.enabled():
            self.next.setText("—")
            self.countdown.setText("Prayer times are off.")
            return
        if not service.times:
            self.next.setText("…")
            self.countdown.setText(service.error or "Loading prayer times…")
            return
        now = datetime.now()
        upcoming = service.upcoming(now)
        if upcoming:
            key, name, when = upcoming
            minutes = int((when - now).total_seconds() // 60) + 1
            self.next.setText(f"{name.upper()} {when:%H:%M}")
            self.countdown.setText(f"in {minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"in {minutes} min")
        else:
            key = None
            self.next.setText("ISYAK DONE")
            self.countdown.setText(f"Subuh tomorrow · Syuruk today was {service.times['syuruk']:%H:%M}")
        current = service.current(now)
        for cell_key, (holder, top, bottom) in self.cells.items():
            bottom.setText(service.times[cell_key].strftime("%H:%M"))
            active = cell_key == key
            holder.setStyleSheet(f"background: {INK}; border-radius: 10px;" if active else "")
            colour = "#FFFFFF" if active else ""
            top.setStyleSheet(f"color: {colour};" if active else "")
            bottom.setStyleSheet(f"color: {colour}; font-weight: 700;" if active else
                                 ("color: rgba(0,0,0,110);" if current and service.times[cell_key] <= now else ""))
        mode = "auto" if (service.store.get_setting("solat_zone", "auto") or "auto") == "auto" else "manual"
        self.place.setText(f"{service.zone} · {service.place.upper()} · {mode.upper()}")

    def tick(self):
        if self.service and self.service.times:
            self.refresh()

    def choose_zone(self):
        service = self.service
        if service is None:
            return
        if not service.zone_list:
            self.context.report("Loading the JAKIM zone list…")
            service.load_zone_list(self.choose_zone)
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        from ..style import STYLE
        menu = QMenu(self)
        menu.setStyleSheet(STYLE)
        menu.addAction("Auto-detect (from my location)", lambda: service.set_zone("auto"))
        menu.addSeparator()
        states = {}
        for zone in service.zone_list:
            states.setdefault(zone["state"], []).append(zone)
        for state in sorted(states):
            sub = menu.addMenu(state)
            for zone in states[state]:
                district = zone["district"] if len(zone["district"]) <= 60 else zone["district"][:59] + "…"
                sub.addAction(f"{zone['code']}  {district}", lambda c=zone["code"]: service.set_zone(c))
        menu.addSeparator()
        reminder = service.remind_minutes()
        menu.addAction(f"Reminder before prayer: {reminder} min (click to cycle)", self.cycle_reminder)
        pause = service.store.get_setting("solat_pause_focus", True)
        menu.addAction(("✓ " if pause else "") + "Pause focus timer at prayer time",
                       lambda: service.store.set_setting("solat_pause_focus", not pause))
        menu.exec(QCursor.pos())

    def cycle_reminder(self):
        options = [0, 5, 10, 15, 30]
        current = self.service.remind_minutes()
        self.service.store.set_setting("solat_remind_minutes", options[(options.index(current) + 1) % len(options)] if current in options else 10)
        self.context.report(f"Reminder set to {self.service.remind_minutes()} min before each prayer.")


class WeatherWidget(LauncherWidget):
    id, title, description = "weather", "WEATHER", "Current weather from Open-Meteo (set the place with ?weather <city>)"

    def __init__(self, context):
        super().__init__(context)
        self.value = self.add_label("", "metric", wrap=False)
        self.detail = self.add_label("", "muted")
        self.service = getattr(context.workspace, "weather", None)
        if self.service is not None:
            self.service.changed.connect(self.refresh)

    def refresh(self):
        service = self.service
        if service is None:
            self.value.setText("—")
            self.detail.setText("Weather is unavailable.")
            return
        now = service.current
        if not now:
            self.value.setText("…")
            self.detail.setText(service.error or f"Loading weather for {service.location()['name']}…")
            return
        self.value.setText(f"{now['symbol']}  {now['temp']:.0f}°  {now['words']}")
        bits = [now["place"]]
        if now.get("high") is not None and now.get("low") is not None:
            bits.append(f"H {now['high']:.0f}° · L {now['low']:.0f}°")
        if now.get("rain") is not None:
            bits.append(f"rain {now['rain']}%")
        if now.get("feels") is not None:
            bits.append(f"feels {now['feels']:.0f}°")
        self.detail.setText(" · ".join(bits))


class TogglesWidget(LauncherWidget):
    """Quick toggles: Wi-Fi, Bluetooth, Do Not Disturb, dark mode, keep awake, focus."""
    id, title, description = "toggles", "QUICK TOGGLES", "Wi-Fi, Bluetooth, Do Not Disturb, dark mode, keep awake, focus"
    span = 2
    COSMIC = Path.home() / ".config/cosmic"
    DND = COSMIC / "com.system76.CosmicNotifications/v1/do_not_disturb"
    DARK = COSMIC / "com.system76.CosmicTheme.Mode/v1/is_dark"

    def __init__(self, context):
        super().__init__(context)
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(8)
        self.tiles = {}
        specs = [("wifi", "WI-FI", "◉"), ("bluetooth", "BLUETOOTH", "ᛒ"), ("dnd", "DO NOT DISTURB", "☾"),
                 ("dark", "DARK MODE", "◐"), ("awake", "KEEP AWAKE", "☕"), ("focus", "FOCUS", "●")]
        for index, (key, name, glyph) in enumerate(specs):
            tile = push(f"{glyph}\n{name}", lambda checked=False, k=key: self.toggle(k))
            tile.setMinimumHeight(58)
            tile.setCheckable(True)
            grid.addWidget(tile, 0, index)
            self.tiles[key] = tile
        self.body.addLayout(grid)
        self.status = self.add_label("", "muted", wrap=False)
        self.state = {}
        self.inhibitor = getattr(context.workspace, "_genta_inhibitor", None)

    # ── reading state ──────────────────────────────────────────
    def refresh(self):
        import subprocess
        def run(args):
            try:
                return subprocess.run(args, capture_output=True, text=True, timeout=2).stdout
            except (OSError, subprocess.SubprocessError):
                return ""
        self.state["wifi"] = run(["nmcli", "radio", "wifi"]).strip() == "enabled"
        self.state["bluetooth"] = "Powered: yes" in run(["bluetoothctl", "show"])
        self.state["dnd"] = self._read_bool(self.DND, False)
        self.state["dark"] = self._read_bool(self.DARK, self._read_bool(Path("/usr/share/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark"), True))
        inhibitor = getattr(self.context.workspace, "_genta_inhibitor", None)
        self.state["awake"] = inhibitor is not None and inhibitor.state() != inhibitor.ProcessState.NotRunning
        self.state["focus"] = self.context.workspace.clock.running
        self.paint_tiles()

    def tick(self):
        if self.state.get("focus") != self.context.workspace.clock.running:
            self.state["focus"] = self.context.workspace.clock.running
            self.paint_tiles()

    def paint_tiles(self):
        for key, tile in self.tiles.items():
            on = bool(self.state.get(key))
            tile.blockSignals(True)
            tile.setChecked(on)
            tile.blockSignals(False)
            tile.setObjectName("primary" if on else "")
            tile.style().unpolish(tile)
            tile.style().polish(tile)

    @staticmethod
    def _read_bool(path, default):
        try:
            return path.read_text().strip() == "true"
        except OSError:
            return default

    # ── changing state ─────────────────────────────────────────
    def toggle(self, key):
        from PySide6.QtCore import QProcess
        on = not self.state.get(key, False)
        if key == "wifi":
            QProcess.startDetached("nmcli", ["radio", "wifi", "on" if on else "off"])
        elif key == "bluetooth":
            QProcess.startDetached("bluetoothctl", ["power", "on" if on else "off"])
        elif key in ("dnd", "dark"):
            path = self.DND if key == "dnd" else self.DARK
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("true" if on else "false")
        elif key == "awake":
            self.set_awake(on)
        elif key == "focus":
            self.context.workspace.toggle_focus()
        self.state[key] = on
        self.paint_tiles()
        self.status.setText({"wifi": "Wi-Fi", "bluetooth": "Bluetooth", "dnd": "Do Not Disturb", "dark": "Dark mode",
                             "awake": "Keep awake", "focus": "Focus"}[key] + (" on" if on else " off"))
        QTimer.singleShot(1500, self.refresh)

    def set_awake(self, on):
        """Hold a systemd idle/sleep inhibitor for as long as Genta runs (or until turned off)."""
        from PySide6.QtCore import QProcess
        workspace = self.context.workspace
        current = getattr(workspace, "_genta_inhibitor", None)
        if current is not None:
            current.kill()
            current.waitForFinished(500)
            workspace._genta_inhibitor = None
        if on:
            process = QProcess(workspace)
            process.start("systemd-inhibit", ["--what=idle:sleep", "--who=Genta", "--why=Keep awake toggle", "--mode=block",
                                              "sleep", "infinity"])
            workspace._genta_inhibitor = process


class TimelineWidget(LauncherWidget):
    """Today on one line: prayer times, calendar events and timed tasks, with a red NOW marker."""
    id, title, description = "timeline", "TODAY'S TIMELINE", "Prayer times, events and timed tasks on one line with a NOW marker"
    span = 2

    def __init__(self, context):
        super().__init__(context)
        self.canvas = _TimelineCanvas(self)
        self.body.addWidget(self.canvas)
        self.legend = self.add_label("", "eyebrow", wrap=False)
        solat = getattr(context.workspace, "solat", None)
        if solat is not None:
            solat.changed.connect(self.refresh)

    def refresh(self):
        workspace = self.context.workspace
        today = date.today()
        marks = []
        solat = getattr(workspace, "solat", None)
        if solat is not None and solat.times:
            from ..solat import NOTIFY, PRAYERS
            for key, name in PRAYERS:
                if key in NOTIFY and key in solat.times:
                    marks.append(("prayer", solat.times[key], None, name[:3].upper()))
        try:
            for event in workspace.calendars.events():
                start = event.get("start", "")
                if len(start) >= 16 and start[:10] == today.isoformat():
                    end = event.get("end") or start
                    marks.append(("event", datetime.fromisoformat(start[:19]),
                                  datetime.fromisoformat(end[:19]) if len(end) >= 16 else None, event.get("title", "")[:14]))
        except Exception:
            pass
        try:
            for task in self.context.store.tasks(include_done=False):
                if task["due_at"] and task["due_at"][:10] == today.isoformat():
                    marks.append(("task", datetime.fromisoformat(task["due_at"]), None, task["title"][:14]))
        except Exception:
            pass
        self.canvas.marks = marks
        self.canvas.update()
        counts = {kind: sum(1 for m in marks if m[0] == kind) for kind in ("prayer", "event", "task")}
        self.legend.setText(f"■ EVENTS {counts['event']}   ● TASKS {counts['task']}   ▲ SOLAT {counts['prayer']}   │ NOW")

    def tick(self):
        self.canvas.update()


class _TimelineCanvas(QWidget):
    START, END = 6, 23

    def __init__(self, parent):
        super().__init__(parent)
        self.marks = []
        self.setMinimumHeight(100)

    def x_for(self, moment):
        hours = moment.hour + moment.minute / 60
        span = self.END - self.START
        return 12 + (self.width() - 24) * max(0.0, min(1.0, (hours - self.START) / span))

    def paintEvent(self, event):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QColor, QFont, QPen, QPolygonF
        from ..style import MONO
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = 48
        painter.setPen(QPen(QColor(0, 0, 0, 70), 2))
        painter.drawLine(12, mid, self.width() - 12, mid)
        font = QFont(MONO)
        font.setPointSizeF(6.8)
        painter.setFont(font)
        for hour in range(self.START, self.END + 1, 3):
            x = self.x_for(datetime.now().replace(hour=hour, minute=0))
            painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
            painter.drawLine(int(x), mid - 4, int(x), mid + 4)
            painter.drawText(QRectF(x - 16, mid + 38, 32, 12), Qt.AlignmentFlag.AlignCenter, f"{hour:02}")
        lanes = [mid - 30, mid + 8, mid - 44, mid + 22]   # label rows: above, below, higher, lower
        lane_end = [-1e9] * len(lanes)
        for kind, start, end, text in sorted(self.marks, key=lambda m: m[1]):
            x = self.x_for(start)
            painter.setPen(Qt.PenStyle.NoPen)
            if kind == "event":
                width = max(8.0, self.x_for(end) - x) if end else 8.0
                painter.setBrush(QColor(INK))
                painter.drawRoundedRect(QRectF(x, mid - 6, width, 12), 6, 6)
            elif kind == "task":
                painter.setBrush(QColor(INK))
                painter.drawEllipse(QPointF(x, mid), 5, 5)
            else:
                painter.setBrush(QColor("#2E7D4F"))
                painter.drawPolygon(QPolygonF([QPointF(x, mid - 7), QPointF(x - 5, mid + 3), QPointF(x + 5, mid + 3)]))
            painter.setPen(QColor(0, 0, 0, 170))
            caption = painter.fontMetrics().elidedText(f"{text} {start:%H:%M}", Qt.TextElideMode.ElideMiddle, 110)
            width = painter.fontMetrics().horizontalAdvance(caption) + 6
            left = max(0.0, min(self.width() - width, x - width / 2))
            lane = next((i for i, edge in enumerate(lane_end) if edge + 4 < left), min(range(len(lanes)), key=lambda i: lane_end[i]))
            lane_end[lane] = left + width
            painter.drawText(QRectF(left, lanes[lane], width, 14), Qt.AlignmentFlag.AlignCenter, caption)
        now = datetime.now()
        if self.START <= now.hour < self.END:
            x = self.x_for(now)
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawLine(int(x), 6, int(x), mid + 20)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(QPointF(x, 6), 3.5, 3.5)


class HeatmapWidget(LauncherWidget):
    """Focus minutes per day for the last 12 weeks as a dot grid (darker = more focus)."""
    id, title, description = "heatmap", "FOCUS · 12 WEEKS", "Dot-grid heatmap of daily focus minutes over 12 weeks"

    def __init__(self, context):
        super().__init__(context)
        self.canvas = _HeatCanvas(self)
        self.body.addWidget(self.canvas)
        self.summary = self.add_label("", "eyebrow", wrap=False)

    def refresh(self):
        start = date.today() - timedelta(days=7 * 12 - 1)
        try:
            rows = self.context.store.conn.execute(
                "SELECT substr(created_at, 1, 10), SUM(seconds) FROM focus_sessions WHERE created_at >= ? GROUP BY 1",
                (start.isoformat(),)).fetchall()
        except Exception as error:
            self.context.report(str(error))
            rows = []
        minutes = {day: int(total) // 60 for day, total in rows}
        self.canvas.start, self.canvas.minutes = start, minutes
        self.canvas.update()
        total = sum(minutes.values())
        active = sum(1 for value in minutes.values() if value > 0)
        self.summary.setText(f"{total // 60} H {total % 60} MIN · {active} ACTIVE DAYS")


class _HeatCanvas(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.start, self.minutes = date.today(), {}
        self.setMinimumHeight(7 * 13 + 4)

    def paintEvent(self, event):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        cell = 13.0
        column_step = max(cell, (self.width() - 4) / 12)
        offset = self.start.weekday()
        for index in range(7 * 12):
            day = self.start + timedelta(days=index)
            if day > date.today():
                break
            slot = index + offset
            column, row = slot // 7, slot % 7
            if column >= 12:
                break
            value = self.minutes.get(day.isoformat(), 0)
            level = 0 if value == 0 else 1 if value < 25 else 2 if value < 60 else 3 if value < 120 else 4
            color = QColor(INK)
            color.setAlphaF([0.12, 0.35, 0.55, 0.78, 1.0][level])
            if day == date.today():
                color = QColor(ACCENT)
            size = cell * (0.42 if level == 0 else 0.62)
            painter.setBrush(color)
            cx, cy = column * column_step + column_step / 2, row * 13 + 7
            painter.drawEllipse(QRectF(cx - size / 2, cy - size / 2, size, size))


BUILTINS = [FocusWidget, TodoWidget, TasksWidget, TipsWidget, SolatWidget, TimelineWidget, TogglesWidget, WeatherWidget, CalendarWidget,
            MusicWidget, NoteWidget, InsightsWidget, HeatmapWidget]
