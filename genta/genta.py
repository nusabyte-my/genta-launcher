#!/usr/bin/env python3
"""Genta: a local-first productivity workspace for Windows, Linux and Android."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import sys
import time

# GNOME does not implement wlr-layer-shell. XWayland supports positioned tools.
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import (
    QEvent, QLocale, QLockFile, QProcess, QRectF, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QDesktopServices, QFont, QIcon, QKeySequence,
    QPainter, QPen, QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QSlider, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from store import Store
from art import ACCENT, STYLE, DotText, draw_dot_text
from i18n import tr, set_language
from platform_support import active_clock, ipc_address, runtime_dir, WindowsHotkey
from widgets import label, button, clear_layout
from workspace_panels import WorkspacePanels
from kanban import KanbanBoard

ROOT = Path(__file__).resolve().parent
STATUS_FILE = "genta-status.json"


def load_launcher(window):
    """Create the glass app launcher. Genta lives in the launcher repo (genta/), so look one level up first."""
    candidates = [os.environ.get("GENTA_LAUNCHER_DIR"), str(ROOT.parent), str(Path.home() / "launcher")]
    folder = next((c for c in candidates if c and os.path.isdir(os.path.join(c, "glass_launcher"))), None) or ""
    if os.path.isdir(folder) and folder not in sys.path:
        sys.path.append(folder)
    try:
        from glass_launcher import GlassLauncher
    except ImportError as error:
        print(f"Glass launcher unavailable: {error}", file=sys.stderr)
        return None
    return GlassLauncher(window)


class FocusClock:
    """Monotonic active time; excludes system suspend on Windows and Linux."""
    def __init__(self, store, clock=active_clock):
        self.store = store
        self.clock = clock
        self.running = False
        self.elapsed = 0.0
        self.accounted = 0
        self.started = 0.0
        self.duration = 25 * 60
        self.task_id = None
        saved = store.get_setting("focus_snapshot", {})
        if saved:
            self.duration = saved.get("duration", self.duration)
            self.elapsed = float(saved.get("elapsed", 0))
            self.accounted = int(self.elapsed)
            self.task_id = saved.get("task_id")

    def active_seconds(self):
        return self.elapsed + (self.clock() - self.started if self.running else 0)

    def remaining(self):
        elapsed = self.active_seconds()
        return max(0, self.duration - elapsed) if self.duration else elapsed

    def start(self):
        if not self.running:
            self.started = self.clock()
            self.running = True

    def pause(self):
        if self.running:
            self.elapsed = self.active_seconds()
            if self.duration:
                self.elapsed = min(self.elapsed, self.duration)
            self.running = False
        self.checkpoint()

    def checkpoint(self):
        elapsed = self.active_seconds()
        if self.duration:
            elapsed = min(elapsed, self.duration)
        whole = int(elapsed)
        if whole > self.accounted:
            self.store.record_focus(whole - self.accounted, self.task_id)
            self.accounted = whole
        self.store.set_setting("focus_snapshot", {
            "duration": self.duration, "elapsed": elapsed, "task_id": self.task_id,
        })

    def reset(self, duration=None, task_id=None):
        self.pause()
        if duration is not None:
            self.duration = duration
        self.elapsed = 0.0
        self.accounted = 0
        self.task_id = task_id
        self.checkpoint()


class NotesEditor(QPlainTextEdit):
    create_task = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.create_task.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class FocusDial(QWidget):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.setFixedSize(230, 230)
        self.setAccessibleName("Focus session timer")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(13, 13, 204, 204)
        painter.setPen(QPen(QColor("#393939"), 3))
        painter.drawEllipse(rect)
        progress = (min(1, self.clock.active_seconds() / self.clock.duration)
                    if self.clock.duration else (self.clock.active_seconds() % 60) / 60)
        painter.setPen(QPen(QColor(ACCENT), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, -int(progress * 360 * 16))
        value = math.ceil(self.clock.remaining()) if self.clock.duration else int(self.clock.remaining())
        minutes, seconds = divmod(value, 60)
        text = f"{minutes:02}:{seconds:02}"
        scale = min(5, 190 / (len(text) * 6))
        draw_dot_text(painter, text, (230 - len(text) * 6 * scale) / 2, 83, scale)
        painter.setFont(QFont("Adwaita Sans", 10))
        painter.setPen(QColor("#a4a4a0"))
        state = "IN THE FLOW" if self.clock.running else "YOUR TIME, PROTECTED"
        painter.drawText(QRectF(0, 133, 230, 30), Qt.AlignmentFlag.AlignCenter, tr(state))


class TaskRow(QFrame):
    def __init__(self, task, window):
        super().__init__()
        self.setObjectName("card")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        done = QCheckBox()
        done.setChecked(task["done"])
        done.setAccessibleName(tr("Complete {title}").format(title=task["title"]))
        done.toggled.connect(lambda value: window.change_task(task["id"], done=value))
        row.addWidget(done)
        text = QVBoxLayout()
        title = label(task["title"], translate=False)
        title.setWordWrap(True)
        if task["done"]:
            font = title.font()
            font.setStrikeOut(True)
            title.setFont(font)
            title.setStyleSheet("color: #858581")
        text.addWidget(title)
        details = []
        if task["due_at"]:
            due = datetime.fromisoformat(task["due_at"])
            details.append(QLocale().toString(due, "ddd d MMM · HH:mm"))
        elif task["day"]:
            details.append(QLocale().toString(date.fromisoformat(task["day"]), "ddd d MMM"))
        else:
            details.append(tr("Inbox"))
        if task["priority"]:
            details.append(tr("High priority") if task["priority"] == 2 else tr("Priority"))
        if task["project"]:
            details.append(task["project"])
        if task["deadline"]:
            details.append(tr("Deadline") + " " + task["deadline"])
        if task["repeat_rule"] != "none":
            details.append({"daily": tr("Daily"), "weekdays": tr("Weekdays"), "weekly": tr("Weekly"), "monthly": tr("Monthly")}[task["repeat_rule"]])
        text.addWidget(label(" · ".join(details), "muted"))
        row.addLayout(text, 1)
        if not task["done"]:
            row.addWidget(button("↗", lambda: window.focus_task(task), "icon", "Focus on this task"))
        more = button("···", lambda: self.menu(task, window), "icon", "Task actions")
        row.addWidget(more)

    def menu(self, task, window):
        menu = QMenu(self)
        for title, day in [("Move to today", date.today().isoformat()),
                           ("Move to tomorrow", (date.today() + timedelta(days=1)).isoformat()),
                           ("Move to inbox", None)]:
            menu.addAction(tr(title), lambda d=day: window.change_task(task["id"], day=d, due_at=None))
        menu.addAction(tr("Choose date…"), lambda: window.choose_task_date(task))
        menu.addAction(tr("Rename…"), lambda: window.rename_task(task))
        menu.addAction(tr("Task details…"), lambda: window.edit_task_details(task))
        menu.addSeparator()
        for priority, title in enumerate(["Normal priority", "Priority", "High priority"]):
            menu.addAction(tr(title), lambda p=priority: window.change_task(task["id"], priority=p))
        menu.addSeparator()
        menu.addAction(tr("Delete task…"), lambda: window.delete_task(task))
        window.menu_open = True
        menu.exec(QCursor.pos())
        window.menu_open = False


class GentaWindow(WorkspacePanels, QWidget):
    def __init__(self, store):
        super().__init__()
        self._quitting = False
        self.store = store
        self.clock = FocusClock(store)
        self.expanded = True
        self.launcher = None
        self.next_task_title = ""
        self._published_status = None
        self.menu_open = False
        self.active_day = date.today().isoformat()
        self.today = date.today().isoformat()
        self.week_start = date.today()
        self.note_day = date.today().isoformat()
        self.note_dirty = False
        self.clips = []
        self.alerts = []
        self.current_alert = None
        self.last_checkpoint = time.monotonic()
        self.target_screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self.setWindowTitle("Genta")
        self.setWindowIcon(QIcon(str(ROOT / "genta.svg")))
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if sys.platform.startswith("linux"):
            self.setAttribute(Qt.WidgetAttribute.WA_X11NetWmWindowTypeDock)
        self.setStyleSheet(STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.shell = QFrame()
        self.shell.setObjectName("shell")
        outer.addWidget(self.shell)
        self.layout = QVBoxLayout(self.shell)
        self.layout.setContentsMargins(20, 12, 20, 14)
        self.layout.setSpacing(12)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self.layout.addWidget(self.body)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.setSpacing(1)
        brand.addWidget(DotText("GENTA", size=3))
        brand.addWidget(label("A LITTLE SPACE TO GET THINGS DONE", "eyebrow"))
        header.addLayout(brand)
        header.addStretch()
        self.date_label = label(QLocale().toString(date.today(), "dddd, d MMMM"), "muted")
        header.addWidget(self.date_label)
        self.pin = button("Pin", self.pin_changed, "icon", "Keep workspace open")
        self.pin.setCheckable(True)
        self.pin.setChecked(bool(store.get_setting("pinned", False)))
        header.addWidget(self.pin)
        header.addWidget(button("—", self.collapse, "icon", "Collapse workspace · Escape"))
        body.addLayout(header)

        self.alert_frame = QFrame()
        self.alert_frame.setObjectName("card")
        alert_row = QHBoxLayout(self.alert_frame)
        self.alert_text = label("")
        self.alert_text.setWordWrap(True)
        alert_row.addWidget(self.alert_text, 1)
        alert_row.addWidget(button("+5 min", self.snooze_alert))
        alert_row.addWidget(button("Done", self.finish_alert, "primary"))
        body.addWidget(self.alert_frame)
        self.alert_frame.hide()

        nav = QHBoxLayout()
        nav.setSpacing(3)
        self.nav_buttons = []
        self.pages = QStackedWidget()
        for index, title in enumerate(["Today", "Week", "Focus", "Notes", "Calendar", "Music", "Clips", "Insights", "Hermes", "Settings"]):
            tab = button(title, lambda checked=False, i=index: self.switch_tab(i), "nav")
            tab.setCheckable(True)
            self.nav_buttons.append(tab)
            nav.addWidget(tab)
        nav.addStretch()
        body.addLayout(nav)
        body.addWidget(self.pages, 1)
        self.build_tasks()
        self.build_week()
        self.build_focus()
        self.build_notes()
        self.build_calendar()
        self.build_music()
        self.build_clips()
        self.build_insights()
        self.build_ai()
        self.build_settings()
        footer = QHBoxLayout()
        self.status = label("LOCAL FIRST / WINDOWS · LINUX · ANDROID", "eyebrow")
        footer.addWidget(self.status, 1)
        footer.addWidget(label("Ctrl+Enter  note → task    ·    Esc  tuck away", "muted"))
        body.addLayout(footer)
        self.leave_timer = QTimer(self)
        self.leave_timer.setSingleShot(True)
        self.leave_timer.setInterval(900)
        self.leave_timer.timeout.connect(self.maybe_collapse)
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.setInterval(450)
        self.note_timer.timeout.connect(self.save_note)
        self.tick_timer = QTimer(self)
        # Fast ticks only while the workspace is visible; 1 s keeps the
        # top-bar pill and reminders current while it is hidden.
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start()
        self.reminder_timer = QTimer(self)
        self.reminder_timer.setInterval(5000)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start()
        QApplication.clipboard().dataChanged.connect(self.capture_clip)
        escape = QShortcut(QKeySequence("Escape"), self, self.collapse)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.notes.create_task.connect(self.note_to_task)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Genta — your top-edge workspace")
        tray_menu = QMenu()
        tray_menu.addAction("Open launcher", self.toggle_launcher)
        tray_menu.addAction("Open / collapse workspace", self.toggle)
        tray_menu.addAction("Pause / resume focus", self.toggle_focus)
        tray_menu.addSeparator()
        tray_menu.addAction("Quit Genta", self.quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.setup_services()
        self._session_hook_ended = False
        self.switch_tab(0)
        self.position()
        self.update_focus_controls()
        QTimer.singleShot(1000, self.check_reminders)
        # Build the launcher's app index after startup so the first open is instant.
        QTimer.singleShot(1500, self.ensure_launcher)

    def ensure_launcher(self):
        if self.launcher is None:
            self.launcher = load_launcher(self)
        return self.launcher

    def toggle_launcher(self):
        launcher = self.ensure_launcher()
        if launcher is None:
            self.toggle()
        else:
            launcher.toggle()

    def tray_activated(self, reason):
        """Top-bar pill on desktops without the GNOME extension (e.g. COSMIC's status area)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_launcher()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.toggle()

    def set_tip_badge(self, count):
        """Called by the launcher's tip engine; shows a dot on the idle pill when tips are waiting."""
        if count != getattr(self, "_tip_count", 0):
            self._tip_count = count
            self._tray_pill = None
            self.publish_status()

    def update_tray_pill(self, status):
        from PySide6.QtGui import QPixmap
        mode, text = status["mode"], status["text"]
        tips = getattr(self, "_tip_count", 0)
        if mode == "idle":
            if tips:
                pixmap = self.windowIcon().pixmap(64, 64)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ACCENT))
                painter.drawEllipse(QRectF(40, 2, 22, 22))
                painter.end()
                self.tray.setIcon(QIcon(pixmap))
            else:
                self.tray.setIcon(self.windowIcon())
            tip_text = f" · {tips} tip{'s' if tips > 1 else ''}" if tips else ""
            self.tray.setToolTip("Genta" + (f" · next: {text}" if text else "") + tip_text)
            return
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT if mode == "running" else "#f0b429"))
        painter.drawRoundedRect(QRectF(2, 10, size - 4, size - 20), 22, 22)
        font = QFont("Adwaita Sans")
        font.setPixelSize(34)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff" if mode == "running" else "#1b1b1b"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text.split(":")[0])
        painter.end()
        self.tray.setIcon(QIcon(pixmap))
        self.tray.setToolTip(f"Genta · focus {'running' if mode == 'running' else 'paused'} · {text}")

    def publish_status(self):
        """Share a tiny status for the GNOME top-bar pill (no task contents beyond the next title)."""
        if self.clock.running or self.clock.active_seconds():
            value = math.ceil(self.clock.remaining()) if self.clock.duration else int(self.clock.remaining())
            status = {"mode": "running" if self.clock.running else "paused", "text": f"{value // 60:02}:{value % 60:02}"}
        else:
            title = self.next_task_title
            status = {"mode": "idle", "text": title if len(title) <= 32 else title[:31] + "…"}
        status["pid"] = os.getpid()
        if getattr(self, "weather_status", None):
            status["weather"] = self.weather_status
        if getattr(self, "_tip_count", 0):
            status["tips"] = self._tip_count
        if getattr(self, "solat_status", None):
            status["solat"] = self.solat_status
        pill = (status["mode"], status["text"].split(":")[0] if status["mode"] != "idle" else status["text"])
        if pill != getattr(self, "_tray_pill", None):
            self._tray_pill = pill
            self.update_tray_pill(status)
        target = runtime_dir() / STATUS_FILE
        # Rewrite if something removed the file (e.g. an older instance cleaning up on quit).
        if status == self._published_status and target.exists():
            return
        temporary = target.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
            self._published_status = status
        except OSError:
            pass

    def page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.pages.addWidget(widget)
        return layout

    def scroll_list(self, parent_layout):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(host)
        parent_layout.addWidget(scroll, 1)
        return layout

    def build_tasks(self):
        page = self.page()
        top = QHBoxLayout()
        self.tasks_heading = label("Make room for what matters.", "heading")
        top.addWidget(self.tasks_heading, 1)
        self.task_filter = QComboBox()
        self.task_filter.addItems([tr(value) for value in ["Today + overdue", "Inbox", "All tasks"]])
        self.task_filter.currentIndexChanged.connect(self.refresh_tasks)
        top.addWidget(self.task_filter)
        self.project_filter = QComboBox()
        self.project_filter.addItem(tr("All projects"), None)
        self.project_filter.currentIndexChanged.connect(self.refresh_tasks)
        top.addWidget(self.project_filter)
        page.addLayout(top)
        self.task_summary = label("", "muted")
        page.addWidget(self.task_summary)
        composer = QHBoxLayout()
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText(tr("What's on your mind?  Try ‘Call Sam tomorrow at 5pm’"))
        self.task_input.setAccessibleName("New task")
        self.task_input.returnPressed.connect(self.add_task)
        composer.addWidget(self.task_input, 1)
        composer.addWidget(button("+ Add task", self.add_task, "primary"))
        composer.addWidget(button("Speak", self.capture_voice))
        page.addLayout(composer)
        self.task_board = KanbanBoard(self)
        self.task_board.moveRequested.connect(self.move_board_task, Qt.ConnectionType.QueuedConnection)
        self.task_board.completionRequested.connect(self.complete_board_task, Qt.ConnectionType.QueuedConnection)
        self.task_board.openRequested.connect(lambda uid: self.board_task_action(uid, "edit"), Qt.ConnectionType.QueuedConnection)
        self.task_board.focusRequested.connect(lambda uid: self.board_task_action(uid, "focus"), Qt.ConnectionType.QueuedConnection)
        self.task_board.deleteRequested.connect(lambda uid: self.board_task_action(uid, "delete"), Qt.ConnectionType.QueuedConnection)
        page.addWidget(self.task_board, 1)
        actions = QHBoxLayout()
        actions.addWidget(label("Drag cards between columns. Double-click to edit.", "muted"), 1)
        actions.addWidget(button("Plan your day", self.plan_your_day, "primary"))
        actions.addWidget(button("Wrap up", self.wrap_your_day))
        page.addLayout(actions)

    def build_week(self):
        page = self.page()
        row = QHBoxLayout()
        self.week_label = label("", "heading")
        row.addWidget(self.week_label, 1)
        row.addWidget(button("‹", lambda: self.move_week(-7)))
        row.addWidget(button("This week", lambda: self.move_week(0)))
        row.addWidget(button("›", lambda: self.move_week(7)))
        page.addLayout(row)
        page.addWidget(label("Select a day to plan it. Move tasks using their ··· menu.", "muted"))
        self.day_buttons = QHBoxLayout()
        self.day_buttons.setSpacing(5)
        page.addLayout(self.day_buttons)
        composer = QHBoxLayout()
        self.week_input = QLineEdit()
        self.week_input.setPlaceholderText(tr("Add a task to the selected day…"))
        self.week_input.returnPressed.connect(self.add_week_task)
        composer.addWidget(self.week_input, 1)
        composer.addWidget(button("+ Add", self.add_week_task, "primary"))
        page.addLayout(composer)
        self.week_list = self.scroll_list(page)

    def build_focus(self):
        page = self.page()
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label("ONE THING AT A TIME", "eyebrow"))
        left.addWidget(label("Find your flow.", "heading"))
        copy = label("A quiet space for your next small step.\nActive time counts. Pauses don't.", "muted")
        copy.setWordWrap(True)
        left.addWidget(copy)
        self.focus_task_select = QComboBox()
        self.focus_task_select.setAccessibleName("Focus task")
        left.addWidget(self.focus_task_select)
        controls = QHBoxLayout()
        self.focus_mode = QComboBox()
        self.focus_mode.addItems([tr("Countdown"), tr("Stopwatch")])
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 180)
        self.minutes.setValue(max(1, self.clock.duration // 60) if self.clock.duration else 25)
        self.minutes.setSuffix(tr(" min"))
        self.focus_mode.setCurrentIndex(0 if self.clock.duration else 1)
        controls.addWidget(self.focus_mode)
        controls.addWidget(self.minutes)
        left.addLayout(controls)
        self.focus_mode.currentIndexChanged.connect(self.configure_focus)
        self.minutes.valueChanged.connect(self.configure_focus)
        self.focus_task_select.currentIndexChanged.connect(self.focus_selection_changed)
        left.addStretch()
        row.addLayout(left, 1)
        self.dial = FocusDial(self.clock)
        row.addWidget(self.dial)
        page.addLayout(row, 1)
        actions = QHBoxLayout()
        self.focus_start = button("Start focus", self.toggle_focus, "primary")
        actions.addWidget(self.focus_start, 1)
        actions.addWidget(button("+5 minutes", self.extend_focus))
        actions.addWidget(button("Finish session", self.end_focus))
        page.addLayout(actions)
        sound_row = QHBoxLayout()
        sound_row.addWidget(label("Focus sound", "muted"))
        self.sound_select = QComboBox()
        from soundscapes import NAMES
        for name in NAMES:
            self.sound_select.addItem(tr(name), name)
        self.sound_select.setCurrentIndex(max(0, self.sound_select.findData(self.store.get_setting("sound", "Silence"))))
        def sound_changed():
            name = self.sound_select.currentData()
            self.store.set_setting("sound", name)
            self.audio.select(name)
        self.sound_select.currentIndexChanged.connect(sound_changed)
        sound_row.addWidget(self.sound_select, 1)
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(self.store.get_setting("sound_volume", 30))
        volume.setMaximumWidth(140)
        volume.valueChanged.connect(lambda value: (self.store.set_setting("sound_volume", value), self.audio.volume(value)))
        sound_row.addWidget(volume)
        page.addLayout(sound_row)

    def build_notes(self):
        page = self.page()
        row = QHBoxLayout()
        row.addWidget(label("A place for the loose ends.", "heading"), 1)
        self.note_date = QDateEdit()
        self.note_date.setCalendarPopup(True)
        self.note_date.setDate(date.today())
        self.note_date.setDisplayFormat("dd MMM yyyy")
        self.note_date.dateChanged.connect(self.change_note_day)
        row.addWidget(self.note_date)
        row.addWidget(button("History", self.note_history))
        page.addLayout(row)
        page.addWidget(label("Daily notes, saved as you type. Select a line and press Ctrl+Enter to make it a task.", "muted"))
        self.notes = NotesEditor()
        self.notes.setPlaceholderText(tr("A thought, an idea, something to come back to…"))
        self.notes.setPlainText(self.store.note(self.note_day))
        self.notes.textChanged.connect(self.note_changed)
        page.addWidget(self.notes, 1)
        self.note_state = label("All changes saved locally", "muted")
        page.addWidget(self.note_state)

    def build_clips(self):
        page = self.page()
        page.addWidget(label("Keep a little within reach.", "heading"))
        page.addWidget(label("Text clipboard history is opt-in, memory-only, and limited to 20 items.\nSensitive text can be captured. GNOME may restrict background access.", "muted"))
        row = QHBoxLayout()
        self.clip_enabled = QCheckBox(tr("Capture clipboard text"))
        self.clip_enabled.setChecked(bool(self.store.get_setting("clipboard_enabled", False)))
        self.clip_enabled.toggled.connect(self.set_clip_enabled)
        row.addWidget(self.clip_enabled, 1)
        row.addWidget(button("Clear history", self.clear_clips))
        page.addLayout(row)
        self.clip_list = self.scroll_list(page)

    def build_insights(self):
        page = self.page()
        page.addWidget(label("Small steps add up.", "heading"))
        page.addWidget(label("Your progress, from real activity on this device.", "muted"))
        row = QHBoxLayout()
        self.metrics = {}
        for key, title in [("completed_today", "TASKS DONE TODAY"), ("focus_today_seconds", "FOCUS MINUTES TODAY"), ("streak", "DAY COMPLETION STREAK")]:
            card = QFrame()
            card.setObjectName("card")
            content = QVBoxLayout(card)
            self.metrics[key] = label("0", "metric")
            content.addWidget(self.metrics[key])
            content.addWidget(label(title, "eyebrow"))
            row.addWidget(card)
        page.addLayout(row)
        self.history_list = self.scroll_list(page)


    def switch_tab(self, index):
        if hasattr(self, "note_timer"):
            self.save_note()
        self.pages.setCurrentIndex(index)
        for i, tab in enumerate(self.nav_buttons):
            tab.setChecked(i == index)
        if index == 0:
            self.refresh_tasks()
        elif index == 1:
            self.refresh_week()
        elif index == 2:
            self.refresh_focus_tasks()
        elif index == 4:
            self.refresh_calendar_list()
        elif index == 6:
            self.refresh_clips()
        elif index == 7:
            self.refresh_insights()

    def refresh_tasks(self):
        today = date.today().isoformat()
        tasks = self.store.tasks(include_done=True)
        upcoming = [t for t in tasks if not t["done"] and t["day"] and t["day"] <= today]
        self.next_task_title = upcoming[0]["title"] if upcoming else ""
        mode = self.task_filter.currentIndex()
        if mode == 0:
            tasks = [t for t in tasks if ((t["completed_at"] or "")[:10] == today if t["done"] else t["day"] is None or t["day"] <= today)]
        elif mode == 1:
            tasks = [t for t in tasks if t["day"] is None and (not t["done"] or (t["completed_at"] or "")[:10] == today)]
        selected = self.project_filter.currentData()
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem(tr("All projects"), None)
        for project in sorted({t["project"] for t in self.store.tasks() if t["project"]}):
            self.project_filter.addItem(project, project)
        self.project_filter.setCurrentIndex(max(0, self.project_filter.findData(selected)))
        self.project_filter.blockSignals(False)
        if selected:
            tasks = [t for t in tasks if t["project"] == selected]
        open_count = sum(not t["done"] for t in tasks)
        self.task_summary.setText(tr("{open} open · {completed} completed in this view").format(open=open_count, completed=sum(t["done"] for t in tasks)))
        self.task_board.set_tasks(tasks)

    def move_board_task(self, uid, destination):
        task = self.task_board.task(uid)
        if task is None:
            return
        if destination == "done":
            self.change_task(task["id"], done=True)
        elif destination in ("inbox", "planned"):
            self.change_task(task["id"], done=False, day=None if destination == "inbox" else date.today().isoformat(), due_at=None)

    def complete_board_task(self, uid, completed):
        task = self.task_board.task(uid)
        if task is not None:
            self.change_task(task["id"], done=completed)

    def board_task_action(self, uid, action):
        task = self.task_board.task(uid)
        if task is None:
            return
        if action == "edit":
            self.edit_task_details(task)
        elif action == "focus":
            self.focus_task(task)
        elif action == "delete":
            self.delete_task(task)

    def add_task(self):
        day = None if self.task_filter.currentIndex() == 1 else date.today().isoformat()
        if self.attempt(lambda: self.store.add_task(self.task_input.text(), day=day)):
            self.task_input.clear()
            self.refresh_tasks()

    def add_week_task(self):
        if self.attempt(lambda: self.store.add_task(self.week_input.text(), day=self.active_day)):
            self.week_input.clear()
            self.refresh_week()

    def change_task(self, task_id, **changes):
        if self.attempt(lambda: self.store.update_task(task_id, **changes)):
            self.refresh_tasks()
            self.refresh_week()
            if self.current_alert and self.current_alert["id"] == task_id:
                if changes.get("done") or "due_at" in changes:
                    self.clear_alert()
                elif "title" in changes:
                    self.alert_text.setText("REMINDER  ·  " + changes["title"])
            return True
        return False

    def choose_task_date(self, task):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle("Schedule task")
        layout = QVBoxLayout(dialog)
        chooser = QDateEdit()
        chooser.setCalendarPopup(True)
        chooser.setDate(date.fromisoformat(task["day"]) if task["day"] else date.today())
        layout.addWidget(chooser)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec():
            self.change_task(task["id"], day=chooser.date().toString("yyyy-MM-dd"), due_at=None)

    def rename_task(self, task):
        from PySide6.QtWidgets import QInputDialog
        text, accepted = QInputDialog.getText(self, "Rename task", "Task", text=task["title"])
        if accepted:
            self.change_task(task["id"], title=text)

    def delete_task(self, task):
        if QMessageBox.question(self, tr("Delete task?"), tr("Delete “{title}”?").format(title=task['title']), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.attempt(lambda: self.store.delete_task(task["id"]))
            if self.clock.task_id == task["id"]:
                self.clock.task_id = None
            if self.current_alert and self.current_alert["id"] == task["id"]:
                self.clear_alert()
            self.refresh_tasks()
            self.refresh_week()

    def move_week(self, days):
        self.week_start = date.today() if days == 0 else self.week_start + timedelta(days=days)
        self.active_day = self.week_start.isoformat()
        self.refresh_week()

    def select_day(self, day):
        self.active_day = day
        self.refresh_week()

    def refresh_week(self):
        clear_layout(self.day_buttons)
        all_tasks = self.store.tasks(include_done=False)
        self.week_label.setText(QLocale().toString(self.week_start, "d MMM") + " — " + QLocale().toString(self.week_start + timedelta(days=6), "d MMM"))
        for offset in range(7):
            day = self.week_start + timedelta(days=offset)
            iso = day.isoformat()
            count = sum(t["day"] == iso for t in all_tasks)
            b = button(QLocale().toString(day, "ddd\nd") + f"  · {count}", lambda checked=False, d=iso: self.select_day(d), "nav")
            b.setCheckable(True)
            b.setChecked(iso == self.active_day)
            self.day_buttons.addWidget(b)
        inbox = button(tr("Inbox") + "\n" + str(sum(t["day"] is None for t in all_tasks)), lambda: self.select_day(None), "nav")
        inbox.setCheckable(True)
        inbox.setChecked(self.active_day is None)
        self.day_buttons.addWidget(inbox)
        clear_layout(self.week_list)
        tasks = [t for t in all_tasks if t["day"] == self.active_day]
        if not tasks:
            self.week_list.addWidget(label("A little breathing room. Add something for this day.", "muted"))
        for task in tasks:
            self.week_list.addWidget(TaskRow(task, self))

    def refresh_focus_tasks(self):
        selected = self.clock.task_id
        self.focus_task_select.blockSignals(True)
        self.focus_task_select.clear()
        self.focus_task_select.addItem(tr("An open-ended focus session"), None)
        for task in self.store.tasks(include_done=False):
            self.focus_task_select.addItem(task["title"], task["id"])
        index = self.focus_task_select.findData(selected)
        self.focus_task_select.setCurrentIndex(max(0, index))
        self.focus_task_select.blockSignals(False)

    def focus_selection_changed(self):
        if not self.clock.running:
            self.clock.reset(task_id=self.focus_task_select.currentData())

    def configure_focus(self):
        if self.clock.running:
            return
        duration = self.minutes.value() * 60 if self.focus_mode.currentIndex() == 0 else 0
        self.clock.reset(duration=duration, task_id=self.focus_task_select.currentData())
        self.minutes.setEnabled(bool(duration))
        self.dial.update()

    def focus_task(self, task):
        self.clock.reset(task_id=task["id"])
        self.switch_tab(2)
        self.toggle_focus()

    def toggle_focus(self):
        if self.clock.running:
            self.clock.pause()
        else:
            if self.clock.duration and self.clock.remaining() <= 0:
                self.clock.reset(task_id=self.clock.task_id)
            if self.clock.active_seconds() == 0:
                self._session_hook_ended = False
                self.attempt(lambda: self.session_hook("start"))
            self.clock.start()
        self.update_focus_controls()

    def update_focus_controls(self):
        resumable = self.clock.active_seconds() > 0 and (not self.clock.duration or self.clock.remaining() > 0)
        self.focus_start.setText("Pause focus" if self.clock.running else "Resume focus" if resumable else "Start focus")
        self.focus_mode.setEnabled(not self.clock.running)
        self.minutes.setEnabled(not self.clock.running and bool(self.clock.duration))
        self.focus_task_select.setEnabled(not self.clock.running)
        if hasattr(self, "audio"):
            self.audio.set_running(self.clock.running)
        self.dial.update()

    def extend_focus(self):
        if self.clock.duration and self.clock.duration <= 10500:
            self.clock.duration += 300
            self.minutes.blockSignals(True)
            self.minutes.setValue(self.clock.duration // 60)
            self.minutes.blockSignals(False)
            self.clock.checkpoint()
            self.dial.update()

    def end_focus(self):
        if self.clock.active_seconds() and not self._session_hook_ended:
            self.attempt(lambda: self.session_hook("end"))
            self._session_hook_ended = True
        self.clock.reset(task_id=self.clock.task_id)
        self.update_focus_controls()
        self.status.setText("Session saved. Take a breath before your next step.")

    def tick(self):
        # XWayland may omit enter/leave events over custom-painted translucent
        # children. Use one geometry-based detector while the workspace is open.
        if self.isVisible() and self.expanded:
            if self.geometry().contains(QCursor.pos()):
                self.leave_timer.stop()
            elif not self.leave_timer.isActive():
                self.leave_timer.start()
        if self.clock.running:
            if self.clock.duration and self.clock.remaining() <= 0:
                self.clock.pause()
                if not self._session_hook_ended:
                    self.attempt(lambda: self.session_hook("end"))
                    self._session_hook_ended = True
                self.notify("Focus complete", "Your session is saved. Time for a small break.")
                self.status.setText("Focus complete. Your time is saved.")
                self.update_focus_controls()
                self.expand()
            if time.monotonic() - self.last_checkpoint >= 30:
                self.clock.checkpoint()
                self.last_checkpoint = time.monotonic()
            if self.isVisible():
                self.dial.update()
        self.publish_status()
        today = date.today().isoformat()
        if self.today != today:
            if self.note_day == self.today:
                self.note_date.setDate(date.today())
            self.today = today
            self.date_label.setText(QLocale().toString(date.today(), "dddd, d MMMM"))
            self.refresh_tasks()

    def note_changed(self):
        self.note_dirty = True
        self.note_state.setText("Saving…")
        self.note_timer.start()

    def save_note(self):
        if self.note_dirty:
            if self.attempt(lambda: self.store.save_note(self.note_day, self.notes.toPlainText())):
                self.note_dirty = False
                self.note_state.setText("All changes saved locally")

    def change_note_day(self, value):
        self.save_note()
        if self.note_dirty:
            return
        self.note_day = value.toString("yyyy-MM-dd")
        self.notes.blockSignals(True)
        self.notes.setPlainText(self.store.note(self.note_day))
        self.notes.blockSignals(False)

    def note_to_task(self):
        cursor = self.notes.textCursor()
        text = cursor.selectedText().replace("\u2029", " ").strip() or cursor.block().text().strip()
        if self.attempt(lambda: self.store.add_task(text, day=self.note_day)):
            self.note_state.setText("Task created. Original note kept.")
            self.refresh_tasks()

    def set_clip_enabled(self, enabled):
        self.store.set_setting("clipboard_enabled", enabled)
        if not enabled:
            self.clear_clips()

    def capture_clip(self):
        if not self.clip_enabled.isChecked():
            return
        text = QApplication.clipboard().text()
        if text.strip() and len(text) <= 20000:
            if text in self.clips:
                self.clips.remove(text)
            self.clips.insert(0, text)
            del self.clips[20:]
            if self.pages.currentIndex() == 6:
                self.refresh_clips()

    def clear_clips(self):
        self.clips.clear()
        self.screenshot_clips.clear()
        self.refresh_clips()

    def refresh_clips(self):
        clear_layout(self.clip_list)
        if not self.clips:
            self.clip_list.addWidget(label("No captured text. Capture is off by default.", "muted"))
        for text in self.clips:
            card = QFrame()
            card.setObjectName("card")
            row = QHBoxLayout(card)
            preview = label(text[:180].replace("\n", "  "))
            preview.setWordWrap(True)
            row.addWidget(preview, 1)
            row.addWidget(button("Copy", lambda checked=False, t=text: QApplication.clipboard().setText(t)))
            row.addWidget(button("→ Task", lambda checked=False, t=text: self.clip_to_task(t)))
            self.clip_list.addWidget(card)
        if hasattr(self, "screenshot_clips"):
            self.screenshot_rows()

    def clip_to_task(self, text):
        if self.attempt(lambda: self.store.add_task(text, day=date.today().isoformat())):
            self.status.setText("Clipboard text added to today's tasks.")

    def refresh_insights(self):
        self.clock.checkpoint()
        data = self.store.insights()
        for key, widget in self.metrics.items():
            widget.setText(str(data[key] // 60 if key == "focus_today_seconds" else data[key]))
        clear_layout(self.history_list)
        for day in data["history"]:
            row = QHBoxLayout()
            row.addWidget(label(QLocale().toString(date.fromisoformat(day["day"]), "ddd d MMM")), 1)
            row.addWidget(label(tr("{tasks} tasks · {minutes} min focused").format(tasks=day["completed"], minutes=day["focus_seconds"] // 60), "muted"))
            self.history_list.addLayout(row)
        hours = data.get("best_hours", [])
        self.history_list.addWidget(label(tr("Best focus hours") + ": " + (", ".join(tr("{hour}:00 ({minutes} min)").format(hour=hour["hour"], minutes=hour["seconds"] // 60) for hour in hours) if hours else tr("No focus history yet.")), "muted"))

    def check_reminders(self):
        self.alerts.extend(self.store.due_reminders())
        self.show_next_alert()

    def show_next_alert(self):
        if self.current_alert or not self.alerts:
            return
        tasks = {t["id"]: t for t in self.store.tasks(include_done=False)}
        while self.alerts:
            task = self.alerts.pop(0)
            if task["id"] not in tasks:
                continue
            self.current_alert = task
            self.alert_text.setText("REMINDER  ·  " + task["title"])
            self.alert_frame.show()
            self.notify("Task reminder", task["title"])
            self.expand()
            break

    def finish_alert(self):
        if self.current_alert:
            self.change_task(self.current_alert["id"], done=True)

    def snooze_alert(self):
        if self.current_alert:
            due = (datetime.now() + timedelta(minutes=5)).replace(microsecond=0)
            self.change_task(self.current_alert["id"], day=due.date().isoformat(), due_at=due.isoformat())

    def clear_alert(self):
        self.current_alert = None
        self.alert_frame.hide()
        self.show_next_alert()

    def notify(self, title, message):
        if os.name == "nt":
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 7000)
        else:
            QProcess.startDetached("notify-send", ["--app-name=Genta", "--icon=" + str(ROOT / "genta.svg"), title, message])

    def export_data(self):
        self.menu_open = True
        filename, _ = QFileDialog.getSaveFileName(self, tr("Export Genta data"), str(Path.home() / f"genta-{date.today().isoformat()}.json"), "JSON (*.json)")
        self.menu_open = False
        if filename:
            self.save_note()
            self.clock.checkpoint()
            def save():
                with open(filename, "w", encoding="utf-8") as stream:
                    json.dump(self.store.export_data(), stream, ensure_ascii=False, indent=2)
            if self.attempt(save):
                self.status.setText("Local data exported. Clipboard text excluded.")


    def pin_changed(self):
        self.store.set_setting("pinned", self.pin.isChecked())

    def position(self):
        screen = self.target_screen if self.target_screen in QApplication.screens() else QApplication.primaryScreen()
        available = screen.availableGeometry()
        width, height = (min(960, available.width() - 24), min(680, available.height() - 16)) if self.expanded else (262, 38)
        self.setGeometry(available.center().x() - width // 2, available.y() + 7, width, height)

    def expand(self):
        self.leave_timer.stop()
        self.expanded = True
        self.body.show()
        self.layout.setContentsMargins(20, 12, 20, 14)
        self.update_monitors()
        self.position()
        self.show()
        self.raise_()
        self.tick_timer.setInterval(250)
        self.refresh_tasks()

    def collapse(self):
        """Tuck the workspace away. Its pill now lives in the GNOME top bar."""
        if QApplication.activeModalWidget() or self.menu_open:
            return
        self.save_note()
        self.expanded = False
        self.leave_timer.stop()
        self.tick_timer.setInterval(1000)
        self.hide()

    def maybe_collapse(self):
        if not self.pin.isChecked() and not self.current_alert and not self.geometry().contains(QCursor.pos()):
            self.collapse()

    def toggle(self):
        if self.expanded and self.isVisible():
            self.collapse()
        else:
            self.expand()
            self.activateWindow()
            if self.pages.currentIndex() == 0:
                self.task_input.setFocus()


    def attempt(self, operation):
        try:
            operation()
            return True
        except (ValueError, KeyError, OSError) as exc:
            self.status.setText(str(exc))
            return False

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.collapse()

    def quit(self):
        self.save_note()
        if self.note_dirty:
            return
        self._quitting = True
        self.clock.pause()
        if hasattr(self, "hub") and self.hub:
            self.hub.stop()
            self.hub = None
        if hasattr(self, "audio"):
            self.audio.set_running(False)
        if hasattr(self, "glance"):
            self.glance.hide()
        if self.launcher is not None:
            self.launcher.hide()
        if getattr(self, "tip_engine", None) is not None:
            self.tip_engine.stop()
        status_file = runtime_dir() / STATUS_FILE
        try:
            if json.loads(status_file.read_text()).get("pid") == os.getpid():
                status_file.unlink()   # only remove our own status, never a newer instance's
        except (OSError, ValueError):
            pass
        QApplication.instance().quit()


def data_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "genta"


def desktop_entry(background=False):
    launcher = ROOT / "genta"
    escaped = str(launcher).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    flags = " --background" if background else ""
    return f'''[Desktop Entry]\nType=Application\nName=Genta\nComment=Tasks, focus and notes at your screen's top edge\nExec="{escaped}"{flags}\nIcon={ROOT / 'genta.svg'}\nTerminal=false\nCategories=Office;Utility;\nStartupNotify=false\n'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toggle", action="store_true", help="Toggle the running workspace, or start it")
    parser.add_argument("--quit", action="store_true", help="Quit the running workspace")
    parser.add_argument("--launcher", action="store_true", help="Toggle the glass app launcher, or start Genta with it open")
    parser.add_argument("--focus-toggle", action="store_true", help="Pause or resume the running focus session")
    parser.add_argument("--background", action="store_true", help="Start without opening the workspace (for autostart)")
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Genta")
    app.setDesktopFileName("genta")
    app.setQuitOnLastWindowClosed(False)
    runtime = runtime_dir()
    address = ipc_address()
    lock = QLockFile(str(runtime / "genta.lock"))
    if not lock.tryLock(0):
        client = QLocalSocket()
        client.connectToServer(address)
        if not client.waitForConnected(1500):
            print("Genta is starting or its local socket is unavailable.", file=sys.stderr)
            return 1
        command = b"quit" if args.quit else b"launcher" if args.launcher else b"focus_toggle" if args.focus_toggle else b"toggle"
        client.write(command)
        client.waitForBytesWritten(1000)
        client.disconnectFromServer()
        return 0
    if args.quit or args.focus_toggle:
        return 0
    QLocalServer.removeServer(address)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(address):
        print(server.errorString(), file=sys.stderr)
        return 1
    store = Store()
    set_language(store.get_setting("language", "en"))
    window = GentaWindow(store)
    window._hotkey = WindowsHotkey(app, window.toggle)
    clients = set()

    def accept():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            clients.add(client)

            def receive(c=client):
                buffered = c.property("genta_buffer") or b""
                buffered += bytes(c.readAll())
                if len(buffered) > 65536:
                    c.disconnectFromServer()
                    return
                c.setProperty("genta_buffer", buffered)
                command = buffered.decode()
                reply = None
                if command == "quit":
                    window.quit()
                elif command == "toggle":
                    window.toggle()
                elif command == "launcher":
                    window.toggle_launcher()
                elif command == "focus_toggle":
                    window.toggle_focus()
                elif "\n" in command:
                    try:
                        action = json.loads(command)
                        name = action.get("action")
                        if name == "focus_start":
                            seconds = action.get("seconds", 1500)
                            if type(seconds) is not int or not 0 <= seconds <= 10800:
                                raise ValueError("Focus duration must be 0–10800 seconds")
                            if window.clock.active_seconds():
                                window.end_focus()
                            window.clock.reset(duration=seconds)
                            window.toggle_focus()
                        elif name == "focus_pause":
                            window.clock.pause()
                            window.update_focus_controls()
                        elif name == "focus_end":
                            window.end_focus()
                        elif name == "focus_toggle":
                            window.toggle_focus()
                        elif name == "show":
                            window.expand()
                        elif name == "launcher":
                            window.toggle_launcher()
                        elif name == "status":
                            pass
                        else:
                            raise ValueError("Unknown local action")
                        reply = {"running": window.clock.running, "seconds": int(window.clock.remaining()),
                                 "duration": window.clock.duration, "task_id": window.clock.task_id}
                    except (ValueError, TypeError, KeyError) as error:
                        reply = {"error": str(error)}
                else:
                    return
                if reply is not None:
                    c.write(json.dumps(reply).encode() + b"\n")
                    c.flush()
                c.disconnectFromServer()

            client.readyRead.connect(receive)
            client.disconnected.connect(lambda c=client: (clients.discard(c), c.deleteLater()))
            if client.bytesAvailable():
                receive()

    server.newConnection.connect(accept)
    if args.launcher:
        window.collapse()
        window.toggle_launcher()
    elif args.background:
        window.collapse()
    else:
        window.expand()
    window.publish_status()
    print("Genta ready", flush=True)
    try:
        return app.exec()
    finally:
        window.save_note()
        window.clock.pause()
        store.close()
        server.close()
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
