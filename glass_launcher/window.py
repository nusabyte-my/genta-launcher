"""Genta: Ulauncher-style glass launcher with customizable Genta widgets, hosted inside the Genta process.

Empty query  → your widget board (add, move, remove widgets).
Typing       → app results list (Alt+1…9 opens a row directly).
``+ text``   → add a task for today.   ``> command`` → run a shell command.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shlex
import subprocess

from PySide6.QtCore import QAbstractListModel, QEvent, QFileSystemWatcher, QLocale, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLineEdit, QListView, QMenu, QMessageBox, QScrollArea, QStyle,
    QStyledItemDelegate, QVBoxLayout, QWidget,
)

from art import DotText

from . import ai
from .apps import App, UNSET_FOR_CHILDREN, Usage, application_dirs, launch, rank, scan
from .style import (ACCENT, BODY, BORDER, FILL, INK, MONO, MUTED, PAPER, RADIUS, ROW, SCRIM, STYLE, TEXT, VISIBLE_ROWS, WIDTH,
                    card_style, load_fonts)
from .ui import label, push
from .widgets import DEFAULT_LAYOUT, PLUGIN_DIR, WidgetContext, catalog
from .widgets.builtin import add_task
from .tips import TipEngine
from .extras import calculator_row, clip_rows, file_rows, settings_rows, window_rows
from .weather import WeatherService
from .solat import SolatService
from .sprite import BellSprite

HEADER = 28
NOTCH = 9          # popover arrow size
PANEL_GUESS = 34   # COSMIC top bar height, used only if the window overlaps the panel
TITLE = "Genta launcher"   # internal X11 title: Blur My Shell and the pill match it; the workspace is "Genta"
ICON = 38
COLUMNS = 2
LAYOUT_KEY = "launcher_widgets"


def icon_dirs():
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    bases = [home, *(Path(part) for part in system.split(":") if part)]
    return [Path.home() / ".icons", *(base / "icons" for base in bases)], [base / "pixmaps" for base in bases]


def configure_icon_theme():
    """Pip-installed Qt only searches its own resources; add XDG icon folders and GNOME's theme."""
    themes, _ = icon_dirs()
    paths = QIcon.themeSearchPaths()
    QIcon.setThemeSearchPaths(paths + [str(path) for path in themes if path.is_dir() and str(path) not in paths])
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            theme = Gio.Settings.new("org.gnome.desktop.interface").get_string("icon-theme")
            if theme and QIcon.themeName() != theme:
                QIcon.setThemeName(theme)
    except (ImportError, ValueError):
        pass
    QIcon.setFallbackThemeName("hicolor")


def pixmap_icon(name):
    for folder in icon_dirs()[1]:
        for suffix in (".png", ".svg", ".xpm"):
            candidate = folder / (name + suffix)
            if candidate.is_file():
                return QIcon(str(candidate))
    return QIcon()


class AppModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.apps = []
        self.query = ""
        self.alt_held = False
        self.icons = {}

    def actionable(self, ordinal):
        """Row number of the Nth non-header row, or None."""
        rows = [i for i, app in enumerate(self.apps) if not app.id.startswith("header:")]
        return rows[ordinal] if ordinal < len(rows) else None

    def ordinal(self, row):
        return sum(1 for app in self.apps[:row] if not app.id.startswith("header:"))

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self.apps[index.row()].id.startswith("header:"):
            return Qt.ItemFlag.NoItemFlags
        return flags

    def set_apps(self, apps):
        self.beginResetModel()
        self.apps = list(apps)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.apps)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        app = self.apps[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return app.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return app.comment or app.generic or app.name
        if role == Qt.ItemDataRole.UserRole:
            return app.comment or app.generic or app.executable
        if role == Qt.ItemDataRole.DecorationRole:
            return self.icon(app)
        return None

    def icon(self, app):
        """Icons load lazily: the view only asks for rows that are on screen."""
        icon = self.icons.get(app.id)
        if icon is None:
            icon = QIcon(app.icon_path) if app.icon_path else QIcon()
            for name in app.icon_names:
                if not icon.isNull():
                    break
                icon = QIcon.fromTheme(name)
            for name in app.icon_names:
                if not icon.isNull():
                    break
                icon = pixmap_icon(name)
            if icon.isNull():
                icon = QIcon.fromTheme("application-x-executable")
            self.icons[app.id] = icon
        return icon


def matched_positions(query, text):
    """Character positions in text that match the query (prefix, word, substring, then subsequence)."""
    query, value = query.strip().casefold(), text.casefold()
    if not query:
        return set()
    index = value.find(query)
    if index >= 0:
        return set(range(index, index + len(query)))
    positions, start = set(), 0
    for char in query:
        found = value.find(char, start)
        if found < 0:
            return set()
        positions.add(found)
        start = found + 1
    return positions


KIND_TAGS = {"window:": "WINDOW", "calc:": "CALC", "ask:": "ASK AI", "file:": "FILE", "settings:": "SETTINGS", "clip:": "CLIP"}


def open_path(path):
    """Open a file with its default app, without leaking Genta's XWayland setting to the child."""
    from gi.repository import Gio, GLib
    context = Gio.AppLaunchContext()
    context.unsetenv("QT_QPA_PLATFORM")
    try:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), context)
    except GLib.Error:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class ResultDelegate(QStyledItemDelegate):
    """Nothing-style row: selected = white pill with dark ink; matched letters in red; mono caps details."""

    def __init__(self, view, model):
        super().__init__(view)
        self.model = model

    def sizeHint(self, option, index):
        header = self.model.apps[index.row()].id.startswith("header:")
        return QSize(option.rect.width(), HEADER if header else ROW)

    def paint(self, painter, option, index):
        app = self.model.apps[index.row()]
        if app.id.startswith("header:"):
            painter.save()
            font = QFont(MONO)
            font.setPointSizeF(7.5)
            painter.setFont(font)
            painter.setPen(QColor(MUTED))
            rect = option.rect.adjusted(16, 6, -16, 0)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "●  " + app.name)
            painter.restore()
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 3, -2, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        ink = QColor(INK) if selected else QColor(TEXT)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TEXT) if selected else QColor(255, 255, 255, 18))
            painter.drawRoundedRect(QRectF(rect), 16, 16)
        app = self.model.apps[index.row()]
        tag = next((label for prefix, label in KIND_TAGS.items() if app.id.startswith(prefix)), "")
        icon_rect = QRect(rect.left() + 14, rect.center().y() - ICON // 2, ICON, ICON)
        index.data(Qt.ItemDataRole.DecorationRole).paint(painter, icon_rect)
        number = self.model.ordinal(index.row()) + 1
        shortcut = "↵" if selected else (f"ALT {number}" if number <= 9 and self.model.alt_held else "")
        hint_width = 64
        left = icon_rect.right() + 16
        width = rect.right() - left - hint_width - 12

        name_font = QFont(BODY)
        name_font.setPointSizeF(11.5)
        name_font.setWeight(QFont.Weight.Medium)
        painter.setFont(name_font)
        metrics = painter.fontMetrics()
        name = metrics.elidedText(index.data(), Qt.TextElideMode.ElideRight, width)
        hits = matched_positions(self.model.query, name) if not tag or tag == "WINDOW" else set()
        x, baseline = left, rect.top() + 26
        for position, char in enumerate(name):
            painter.setPen(QColor(ACCENT) if position in hits else ink)
            painter.drawText(x, baseline, char)
            x += metrics.horizontalAdvance(char)

        mono = QFont(MONO)
        mono.setPointSizeF(7.8)
        painter.setFont(mono)
        detail_color = QColor(0, 0, 0, 150) if selected else QColor(MUTED)
        detail = (index.data(Qt.ItemDataRole.UserRole) or "").upper()
        if tag:
            tag_width = painter.fontMetrics().horizontalAdvance(tag) + 12
            tag_rect = QRectF(left, rect.top() + 33, tag_width, 16)
            painter.setPen(QPen(QColor(ACCENT), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(tag_rect, 8, 8)
            painter.setPen(QColor(ACCENT))
            painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag)
            detail_left = left + tag_width + 8
        else:
            detail_left = left
        painter.setPen(detail_color)
        detail = painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, rect.right() - detail_left - hint_width - 12)
        painter.drawText(QRect(detail_left, rect.top() + 33, width, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, detail)
        if shortcut:
            painter.setPen(QColor(ACCENT) if selected else QColor(255, 255, 255, 80))
            hint_font = QFont(MONO)
            hint_font.setPointSizeF(12 if selected else 7.8)
            painter.setFont(hint_font)
            painter.drawText(QRect(rect.right() - hint_width - 12, rect.top(), hint_width, rect.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, shortcut)
        painter.restore()


class SearchField(QLineEdit):
    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Alt:
            self.launcher.set_alt(False)
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.launcher.set_alt(False)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key.Key_Alt:
            self.launcher.set_alt(True)
        if modifiers & Qt.KeyboardModifier.AltModifier and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            self.launcher.activate_row(key - Qt.Key.Key_1)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.launcher.move_selection(key)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.launcher.activate()
        else:
            super().keyPressEvent(event)


class WidgetBoard(QScrollArea):
    """A two-column grid of widgets with add / move / remove, saved in Genta's settings."""

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        self.store = launcher.store
        self.context = WidgetContext(launcher.workspace, lambda message: launcher.show_hint(message, error=True), launcher.hide)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        inner = QWidget()
        self.setWidget(inner)
        self.grid = QGridLayout(inner)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setSpacing(10)
        self.instances = {}
        self.reload_catalog()

    def reload_catalog(self):
        self.catalog, errors = catalog()
        saved = self.store.get_setting(LAYOUT_KEY, None)
        layout = saved if isinstance(saved, list) else DEFAULT_LAYOUT
        self.layout_ids = [key for key in dict.fromkeys(layout) if isinstance(key, str) and key in self.catalog]
        if isinstance(saved, list):
            self.offer_new_widgets()
        for instance in self.instances.values():
            instance.stop()
            instance.deleteLater()
        self.instances = {}
        self.rebuild()
        if errors:
            self.launcher.show_hint("Widget plugin problem: " + "; ".join(errors), error=True)

    # New built-ins are added once to boards saved before they existed (removing them later sticks).
    NEW_WIDGETS = [("tips", 2, None), ("todo", 1, "tasks"), ("solat", 3, None)]

    def offer_new_widgets(self):
        offered = set(self.store.get_setting("launcher_widgets_offered", []) or [])
        if self.store.get_setting("launcher_tips_offered", False):
            offered.add("tips")
        changed = False
        for key, position, replaces in self.NEW_WIDGETS:
            if key in offered or key not in self.catalog:
                continue
            offered.add(key)
            if key in self.layout_ids:
                continue
            if replaces in self.layout_ids:
                self.layout_ids[self.layout_ids.index(replaces)] = key   # the to-do list supersedes Today
            else:
                self.layout_ids.insert(min(position, len(self.layout_ids)), key)
            changed = True
        self.store.set_setting("launcher_widgets_offered", sorted(offered))
        if changed:
            self.save()

    def save(self):
        self.store.set_setting(LAYOUT_KEY, self.layout_ids)

    def instance(self, key):
        widget = self.instances.get(key)
        if widget is None:
            try:
                widget = self.catalog[key](self.context)
            except Exception as error:  # plugin constructors must not break the board
                self.launcher.show_hint(f"Widget '{key}' failed: {error}", error=True)
                return None
            widget.menu_requested.connect(lambda k=key: self.widget_menu(k))
            self.instances[key] = widget
        return widget

    def rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() and item.widget() not in self.instances.values():
                item.widget().deleteLater()
        for key in list(self.instances):
            if key not in self.layout_ids:
                self.instances.pop(key).deleteLater()
        position = 0
        for key in self.layout_ids:
            widget = self.instance(key)
            if widget is None:
                continue
            span = max(1, min(COLUMNS, getattr(widget, "span", 1)))
            if span > 1 and position % COLUMNS:
                position += COLUMNS - position % COLUMNS   # wide widgets start a new row
            self.grid.addWidget(widget, position // COLUMNS, position % COLUMNS, 1, span)
            widget.show()
            position += span
        add = push("＋  Add widget", self.add_menu, "add", "Add a widget to this board")
        self.grid.addWidget(add, position // COLUMNS, position % COLUMNS)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)

    def run(self, method):
        for key, widget in list(self.instances.items()):
            try:
                getattr(widget, method)()
            except Exception as error:
                self.launcher.show_hint(f"Widget '{key}' failed: {error}", error=True)

    def add_menu(self):
        menu = QMenu(self)
        available = [cls for key, cls in self.catalog.items() if key not in self.layout_ids]
        for cls in available:
            action = menu.addAction(f"{cls.title.title() or cls.id}  —  {cls.description}")
            action.triggered.connect(lambda checked=False, k=cls.id: self.add_widget(k))
        if not available:
            menu.addAction("All widgets are on the board").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Open widgets folder…", self.open_folder)
        menu.addAction("Reload widget plugins", self.reload_catalog)
        menu.addAction("Reset to default board", self.reset_layout)
        self.popup(menu)

    def widget_menu(self, key):
        menu = QMenu(self)
        index = self.layout_ids.index(key)
        menu.addAction("Move earlier", lambda: self.move_widget(key, -1)).setEnabled(index > 0)
        menu.addAction("Move later", lambda: self.move_widget(key, 1)).setEnabled(index < len(self.layout_ids) - 1)
        menu.addSeparator()
        menu.addAction("Remove widget", lambda: self.remove_widget(key))
        self.popup(menu)

    def popup(self, menu):
        menu.setStyleSheet(STYLE)
        menu.exec(QCursor.pos())

    def add_widget(self, key):
        self.layout_ids.append(key)
        self.save()
        self.rebuild()
        widget = self.instances.get(key)
        if widget:
            widget.refresh()
        self.launcher.fit()

    def remove_widget(self, key):
        self.layout_ids.remove(key)
        widget = self.instances.get(key)
        if widget:
            widget.stop()
        self.save()
        self.rebuild()
        self.launcher.fit()

    def move_widget(self, key, step):
        index = self.layout_ids.index(key)
        target = max(0, min(len(self.layout_ids) - 1, index + step))
        self.layout_ids.insert(target, self.layout_ids.pop(index))
        self.save()
        self.rebuild()

    def reset_layout(self):
        self.layout_ids = [key for key in DEFAULT_LAYOUT if key in self.catalog]
        self.save()
        self.rebuild()
        self.run("refresh")
        self.launcher.fit()

    def open_folder(self):
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        example = PLUGIN_DIR / "_example.py.txt"
        if not example.exists():
            from . import widgets
            example.write_text(widgets.__doc__.split("::", 1)[1].split("Plugins are", 1)[0].strip("\n") + "\n")
        self.launcher.hide()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PLUGIN_DIR)))

    def preferred_height(self):
        self.widget().adjustSize()
        return min(500, self.widget().sizeHint().height() + 2)


class AnswerPanel(QFrame):
    """Shows an AI answer, or a live Codex job, with Copy / Add as task / Stop."""

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        self.setObjectName("card")
        self.setStyleSheet(card_style(PAPER))
        self.job = None
        self.work = None
        self.loader = QTimer(self)
        self.loader.setInterval(160)
        self.loader.timeout.connect(self._tick_loader)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        self.title = label("", "eyebrow")
        layout.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.text = label("", None, wrap=True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.text)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.copy_button = push("Copy", self.copy)
        self.task_button = push("Add as task", self.to_task)
        self.stop_button = push("Stop", self.stop)
        for widget in (self.copy_button, self.task_button, self.stop_button):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)

    def busy(self):
        return self.job is not None or self.loader.isActive()

    def start_loader(self, title):
        self._loader_frame = 0
        self._loader_title = title
        self.loader.start()
        self._tick_loader()

    def _tick_loader(self):
        frames = ["●○○○", "○●○○", "○○●○", "○○○●", "○○●○", "○●○○"]
        self._loader_frame += 1
        self.show_state(self._loader_title, "THINKING  " + frames[self._loader_frame % len(frames)], False)
        self.copy_button.setEnabled(False)
        self.task_button.setEnabled(False)

    def preferred_height(self):
        self.text.adjustSize()
        return min(380, max(120, self.text.sizeHint().height() + 92))

    def show_state(self, title, body, running):
        self.title.setText(title)
        self.text.setText(body)
        self.stop_button.setVisible(running)
        self.copy_button.setEnabled(not running)
        self.task_button.setEnabled(not running)
        self.show()
        self.launcher.fit()

    def run_chat(self, provider, prompt):
        name = ai.PROVIDERS[provider].name
        self.start_loader(name.upper())
        store = self.launcher.store

        def done(answer):
            self.loader.stop()
            self.show_state(name.upper(), answer, False)
            self.launcher.bell.celebrate()

        def failed(message):
            self.loader.stop()
            self.show_state(name.upper(), str(message), False)

        from desktop_services import background
        self.work = background(lambda: ai.chat(store, provider, prompt), done, failed)

    def run_codex(self, prompt, folder, write):
        if self.job is not None:
            self.launcher.show_hint("A Codex job is already running. Stop it first.", error=True)
            return
        title = f"CODEX · {'CAN EDIT FILES' if write else 'READ-ONLY'} · {folder}"
        try:
            self.job = ai.CodexJob(prompt, folder, write, self)
        except ValueError as error:
            self.show_state("CODEX", str(error), False)
            return
        self.log = ""

        def output(chunk):
            self.log = (self.log + chunk)[-4000:]
            self.show_state(title, self.log.strip(), True)
            self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

        def finished(message, ok):
            self.job = None
            if not ok:
                tail = self.log.strip()[-600:]
                if "401" in tail or "Unauthorized" in tail:
                    message = "Codex is not signed in. Run  codex login  in a terminal, then try again."
                elif tail:
                    message = f"{message}\n\n{tail}"
            self.show_state(title + (" · DONE" if ok else " · FAILED"), message, False)
            if ok:
                self.launcher.bell.celebrate()

        self.job.output.connect(output)
        self.job.finished.connect(finished)
        self.show_state(title, "Starting Codex…", True)
        self.job.start()

    def stop(self):
        if self.job is not None:
            self.job.cancel()

    def copy(self):
        QGuiApplication.clipboard().setText(self.text.text())
        self.launcher.show_hint("Copied.")

    def to_task(self):
        first = next((line.strip(" -•*") for line in self.text.text().splitlines() if line.strip(" -•*")), "")
        if first and add_task(self.launcher.board.context, first[:200]):
            self.launcher.show_hint(f"Task added: {first[:60]}")


class KeysDialog(QDialog):
    """API keys go to the OS keyring; models and defaults to Genta settings."""

    def __init__(self, launcher):
        super().__init__(launcher)
        self.launcher = launcher
        self.store = launcher.store
        self.setWindowTitle("Genta · AI providers")
        self.setStyleSheet(STYLE + "QDialog { background: #1d1d22; }")
        form = QFormLayout(self)
        self.keys, self.models = {}, {}
        models = self.store.get_setting("ai_models", {}) or {}
        for pid in ("gpt", "grok", "muse"):
            provider = ai.PROVIDERS[pid]
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText("saved in keyring" if ai.get_key(pid) else provider.key_hint)
            model = QLineEdit(models.get(pid, ""))
            model.setPlaceholderText(provider.default_model)
            form.addRow(f"{provider.name} key", key)
            form.addRow(f"{provider.name} model", model)
            self.keys[pid], self.models[pid] = key, model
        self.default = QComboBox()
        self.tips = QComboBox()
        for pid in ("hermes", "gpt", "grok", "muse"):
            self.default.addItem(ai.PROVIDERS[pid].name, pid)
            self.tips.addItem(ai.PROVIDERS[pid].name, pid)
        self.default.setCurrentIndex(max(0, self.default.findData(self.store.get_setting("launcher_ai_default", "hermes"))))
        self.tips.setCurrentIndex(max(0, self.tips.findData(self.store.get_setting("launcher_tip_provider", "hermes"))))
        form.addRow("Default for ?", self.default)
        form.addRow("AI tips use", self.tips)
        self.ai_tips = QCheckBox("Ask the AI for a tip every 15 minutes")
        self.ai_tips.setChecked(bool(self.store.get_setting("launcher_ai_tips", True)))
        form.addRow(self.ai_tips)
        self.titles = QCheckBox("Include the focused window title in AI tips (off = app names only)")
        self.titles.setChecked(bool(self.store.get_setting("launcher_ai_share_titles", False)))
        form.addRow(self.titles)
        self.codex = QLineEdit(self.store.get_setting("codex_workdir", "~/Projects"))
        form.addRow("Codex folder", self.codex)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self):
        try:
            for pid, field in self.keys.items():
                if field.text().strip():
                    ai.set_key(pid, field.text().strip())
        except Exception as error:
            QMessageBox.warning(self, "Genta", f"Could not save to the keyring: {error}")
            return
        self.store.set_setting("ai_models", {pid: f.text().strip() for pid, f in self.models.items() if f.text().strip()})
        self.store.set_setting("launcher_ai_default", self.default.currentData())
        self.store.set_setting("launcher_tip_provider", self.tips.currentData())
        self.store.set_setting("launcher_ai_tips", self.ai_tips.isChecked())
        self.store.set_setting("launcher_ai_share_titles", self.titles.isChecked())
        self.store.set_setting("codex_workdir", self.codex.text().strip() or "~/Projects")
        self.launcher.show_hint("AI settings saved.")
        self.accept()


class RecentStrip(QWidget):
    """Your most-used apps as a row of icons, shown above the widgets when the search is empty."""
    LIMIT = 8

    def __init__(self, launcher):
        super().__init__()
        self.launcher = launcher
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(2, 0, 2, 2)
        self.row.setSpacing(6)
        self.setFixedHeight(64)

    def populate(self):
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        launcher = self.launcher
        weights = [(launcher.usage.weight(app.id), app) for app in launcher.apps]
        recent = [app for weight, app in sorted(weights, key=lambda pair: -pair[0]) if weight > 0.05][:self.LIMIT]
        if not recent:
            return False
        from PySide6.QtWidgets import QToolButton
        for app in recent:
            button = QToolButton()
            button.setIcon(launcher.model.icon(app))
            button.setIconSize(QSize(36, 36))
            button.setFixedSize(56, 56)
            button.setToolTip(app.name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setStyleSheet("QToolButton { background: rgba(255,255,255,12); border: 1px solid rgba(255,255,255,22); border-radius: 16px; }"
                                 "QToolButton:hover { background: rgba(255,255,255,40); }")
            button.clicked.connect(lambda checked=False, a=app: launcher.launch_app(a))
            self.row.addWidget(button)
        self.row.addStretch()
        self.row.addWidget(label("RECENT", "footer"))
        return True


class GlassLauncher(QWidget):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.store = workspace.store
        self.usage = Usage(self.store.get_setting("launcher_usage", {}))
        self.apps = []
        self._was_active = False
        self._slide = 0.0
        self._file_hits = {}
        configure_icon_theme()
        load_fonts()
        self.setWindowTitle(TITLE)
        self.setWindowIcon(workspace.windowIcon())
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.card = QWidget(self)
        self.card.setFixedWidth(WIDTH)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.bell = BellSprite()
        header.addWidget(self.bell)
        header.addSpacing(8)
        brand = DotText("GENTA", size=2)
        brand.setToolTip("Genta")
        header.addWidget(brand)
        header.addSpacing(10)
        self.search = SearchField(self)
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search apps   ·   + task   ·   > command")
        header.addWidget(self.search, 1)
        self.clock = label("", "clock")
        self.date = label("", "date")
        stamp = QVBoxLayout()
        stamp.setSpacing(0)
        stamp.addWidget(self.clock, 0, Qt.AlignmentFlag.AlignRight)
        stamp.addWidget(self.date, 0, Qt.AlignmentFlag.AlignRight)
        header.addLayout(stamp)
        root.addLayout(header)
        rule = QFrame()
        rule.setObjectName("rule")
        root.addWidget(rule)
        self.hint = label("", "hint", wrap=True)
        self.hint.hide()
        root.addWidget(self.hint)

        self.model = AppModel(self)
        self.results = QListView()
        self.results.setModel(self.model)
        self.results.setItemDelegate(ResultDelegate(self.results, self.model))
        self.results.setUniformItemSizes(False)
        self.results.setMouseTracking(True)
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.results.viewport().setAutoFillBackground(False)
        self.results.clicked.connect(self.launch_index)
        root.addWidget(self.results)
        if getattr(workspace, "tip_engine", None) is None:
            workspace.tip_engine = TipEngine(workspace, self)
            workspace.tip_engine.start()
        if getattr(workspace, "weather", None) is None:
            workspace.weather = WeatherService(workspace, self)
            workspace.weather.start()
        if getattr(workspace, "solat", None) is None:
            workspace.solat = SolatService(workspace, self)
            workspace.solat.start()
        self.board = WidgetBoard(self)
        self.recent = RecentStrip(self)
        root.addWidget(self.recent)
        root.addWidget(self.board)
        self.answer = AnswerPanel(self)
        self.answer.hide()
        root.addWidget(self.answer)
        self.footer = label("", "footer")
        root.addWidget(self.footer)

        self.search.textChanged.connect(self.filter)
        self.file_timer = QTimer(self)
        self.file_timer.setSingleShot(True)
        self.file_timer.setInterval(180)
        self.file_timer.timeout.connect(self.search_files)
        QShortcut(QKeySequence("Escape"), self, self.escape)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.rescan_timer = QTimer(self)
        self.rescan_timer.setSingleShot(True)
        self.rescan_timer.setInterval(600)
        self.rescan_timer.timeout.connect(self.rescan)
        self.watcher = QFileSystemWatcher(self)
        folders = [str(folder) for folder in application_dirs() if folder.is_dir()]
        if folders:
            self.watcher.addPaths(folders)
        self.watcher.directoryChanged.connect(lambda path: self.rescan_timer.start())
        self.rescan()

    # ── query ──────────────────────────────────────────────────
    def rescan(self):
        self.apps = scan()
        self.model.icons.clear()
        if self.search.text():
            self.filter(self.search.text())

    def mode(self, text=None):
        text = (self.search.text() if text is None else text).lstrip()
        if not text:
            return "board"
        return {"+": "task", ">": "run", "?": "ask", "/": "files", ";": "clips"}.get(text[0], "apps")

    def filter(self, text):
        mode = self.mode(text)
        argument = text.lstrip()[1:].strip()
        self.show_hint("")
        if mode != "ask":
            self.answer.hide()
        if mode == "task":
            self.show_hint(f"↵  Add to today: {argument}" if argument else "Type a task after +, e.g. + call Sam at 5pm")
        elif mode == "run":
            self.show_hint(f"↵  Run: {argument}" if argument else "Type a command after >, e.g. > htop")
        elif mode == "ask":
            self.show_hint(self.ask_hint(text))
        elif mode == "files":
            self.model.query = argument
            self.file_timer.start()
        elif mode == "clips":
            clips = list(getattr(self.workspace, "clips", []) or [])
            self.model.query = argument
            self.model.set_apps(clip_rows(clips, argument))
            if self.model.rowCount():
                self.results.setCurrentIndex(self.model.index(0))
            elif not clips:
                self.show_hint("Clipboard history is off or empty. Turn on Clips in the Genta workspace (Clips tab).")
            else:
                self.show_hint("No clip matches that.")
        elif mode == "apps":
            rows = rank(self.apps, text, self.usage)
            engine = getattr(self.workspace, "tip_engine", None)
            if engine is not None and engine.watch.windows:
                rows = window_rows(engine.watch.windows, text, self.apps) + rows
            calc = calculator_row(text)
            self.model.query = text
            ask = App(id="ask:", path="", name=f"Ask about “{text.strip()}”", generic="", keywords=(), executable="",
                      comment=ai.PROVIDERS[self.store.get_setting("launcher_ai_default", "hermes")].name,
                      icon_names=("dialog-question-symbolic", "help-about"), icon_path="")
            windows = [r for r in rows if r.id.startswith("window:")]
            programs = [r for r in rows if not r.id.startswith("window:")]
            # Documents and folders join the same search (fd runs after a short pause in typing).
            files = self._file_hits.get(text.strip(), []) if len(text.strip()) >= 3 else []
            if len(text.strip()) >= 3 and text.strip() not in self._file_hits:
                self.file_timer.start()
            groups = [("WINDOWS", windows), ("APPS", programs[:8] if files else programs), ("FILES", files),
                      ("ACTIONS", ([calc] if calc else []) + settings_rows(text) + [ask])]
            groups = [(name, items) for name, items in groups if items]
            combined = []
            for name, items in groups:
                if len(groups) > 1:
                    combined.append(App(id=f"header:{name}", path="", name=name, generic="", keywords=(), executable="",
                                        comment="", icon_names=(), icon_path=""))
                combined.extend(items)
            self.model.set_apps(combined)
            first = self.model.actionable(0)
            if first is not None:
                self.results.setCurrentIndex(self.model.index(first))
                QTimer.singleShot(0, self.results.scrollToTop)   # keep the first section header visible
            else:
                self.show_hint("No matching apps or windows.   Tip: > runs a command, ? asks AI")
        self.results.setVisible(mode in ("apps", "clips", "files") and self.model.rowCount() > 0)
        self.board.setVisible(mode == "board")
        self.recent.setVisible(mode == "board" and self.recent.populate())
        self.footer.setText({
            "board": "Type to search   ·   ⋯ on a widget to move or remove   ·   Esc close",
            "apps": "↵ open   ·   hold Alt for 1…9   ·   ↑ ↓ move   ·   Esc clear",
            "ask": "↵ ask   ·   ?gpt ?grok ?muse ?codex ?hermes   ·   ?keys   ·   Esc clear",
            "files": "↵ open   ·   / searches your home folder   ·   Esc clear",
            "clips": "↵ copy   ·   ; searches clipboard history   ·   Esc clear",
        }.get(mode, "↵ confirm   ·   Esc clear"))
        self.fit()

    def ask_hint(self, text):
        body = text.lstrip()[1:].strip()
        if body.split(" ", 1)[0].lower() == "keys":
            return "↵  Set API keys and models for ChatGPT, Grok and Meta Muse"
        if body.split(" ", 1)[0].lower() == "layout":
            return "↵  ?layout bar = float under the top bar (default) · ?layout center = middle of the screen"
        if body.split(" ", 1)[0].lower() == "solat":
            city = body[5:].strip()
            return f"↵  Set the prayer-time zone from: {city}" if city else "Type a town after ?solat, e.g. ?solat Shah Alam (or ?solat auto)"
        if body.split(" ", 1)[0].lower() == "weather":
            city = body[7:].strip()
            return f"↵  Set weather location to: {city}" if city else "Type a city after ?weather, e.g. ?weather Petaling Jaya"
        provider, write, prompt = ai.parse_query(text, self.store.get_setting("launcher_ai_default", "hermes"))
        name = ai.PROVIDERS[provider].name
        if not prompt:
            return f"Ask {name} anything.   ?gpt · ?grok · ?muse · ?codex (read-only) · ?codex! (can edit files)"
        if provider == "codex":
            folder = self.codex_dir()
            return f"↵  Codex {'(CAN EDIT FILES in ' + str(folder) + ')' if write else '(read-only) in ' + str(folder)}: {prompt}"
        return f"↵  Ask {name}: {prompt}"

    def set_solat_place(self, place):
        from .solat import zone_for
        from .weather import geocode
        from desktop_services import background

        def lookup():
            location = geocode(place)
            return zone_for(location["lat"], location["lon"])

        def done(result):
            self.workspace.solat.set_zone(result[0], result[1])
            self.show_hint(f"Prayer zone set to {result[0]} · {result[1]}.")

        self._solat_work = background(lookup, done, lambda message: self.show_hint(str(message), error=True))

    def codex_dir(self):
        folder = Path(os.path.expanduser(self.store.get_setting("codex_workdir", "~/Projects")))
        return folder if folder.is_dir() else Path.home()

    def ask(self):
        text = self.search.text()
        if text.lstrip()[1:].strip().split(" ", 1)[0].lower() == "keys":
            self.search.clear()
            KeysDialog(self).exec()
            return
        if text.lstrip()[1:].strip().split(" ", 1)[0].lower() == "layout":
            choice = text.lstrip()[1:].strip()[6:].strip().lower()
            if choice in ("bar", "center", "centre"):
                self.store.set_setting("launcher_position", "center" if choice.startswith("cent") else "bar")
                self.search.clear()
                self.fit()
                self.slide_in()
            return
        if text.lstrip()[1:].strip().split(" ", 1)[0].lower() == "solat":
            place = text.lstrip()[1:].strip()[5:].strip()
            if place.lower() == "auto":
                self.workspace.solat.set_zone("auto")
                self.show_hint("Prayer zone: auto-detect from your location.")
            elif place:
                self.show_hint("Finding the JAKIM zone…")
                self.set_solat_place(place)
            return
        if text.lstrip()[1:].strip().split(" ", 1)[0].lower() == "weather":
            city = text.lstrip()[1:].strip()[7:].strip()
            if city:
                self.show_hint("Looking up the place…")
                self.workspace.weather.set_city(city, lambda message: self.show_hint(message))
            return
        provider, write, prompt = ai.parse_query(text, self.store.get_setting("launcher_ai_default", "hermes"))
        if not prompt:
            return
        if provider == "codex":
            self.answer.run_codex(prompt, self.codex_dir(), write)
        else:
            self.answer.run_chat(provider, prompt)
        self.fit()

    def fit(self):
        """Grow and shrink like Ulauncher, keeping the card's top edge fixed."""
        if self.results.isVisibleTo(self):
            heights = [HEADER if app.id.startswith("header:") else ROW for app in self.model.apps]
            self.results.setFixedHeight(sum(heights[:VISIBLE_ROWS + 1]) + 4)
        if self.board.isVisibleTo(self):
            self.board.setFixedHeight(self.board.preferred_height())
        if self.answer.isVisibleTo(self):
            self.answer.setFixedHeight(self.answer.preferred_height())
        layout = self.card.layout()
        layout.activate()
        self.card.setFixedHeight(layout.sizeHint().height())
        self.place_card()
        self.update()

    def docked(self):
        """'bar' (default): float just under the top bar's Genta clock, like a panel popover. 'center': Spotlight-style."""
        return self.store.get_setting("launcher_position", "bar") != "center"

    def card_top(self):
        if not self.docked():
            return int(self.height() * 0.16)
        # A maximized window starts below the panel; if it doesn't (panel hidden), leave room for it.
        screen_top = self.screen().geometry().top() if self.screen() else 0
        return NOTCH + 6 + max(0, PANEL_GUESS - (self.geometry().top() - screen_top))

    def place_card(self):
        self.card.move((self.width() - WIDTH) // 2, self.card_top() + int(self._slide))

    def slide_in(self):
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        animation = QVariantAnimation(self)
        animation.setStartValue(-18.0 if self.docked() else 12.0)
        animation.setEndValue(0.0)
        animation.setDuration(170)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: (setattr(self, "_slide", value), self.place_card(), self.update()))
        self._animation = animation
        animation.start()

    def resizeEvent(self, event):
        self.place_card()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        # The window covers the screen (compositors may ignore X11 positions); outside the card = close.
        if not self.card.geometry().contains(event.position().toPoint()):
            self.hide()
        else:
            super().mousePressEvent(event)

    def move_selection(self, key):
        rows = [i for i, app in enumerate(self.model.apps) if not app.id.startswith("header:")]
        if not rows or not self.results.isVisible():
            return
        step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Backtab: -1, Qt.Key.Key_Down: 1, Qt.Key.Key_Tab: 1,
                Qt.Key.Key_PageUp: -VISIBLE_ROWS, Qt.Key.Key_PageDown: VISIBLE_ROWS}[key]
        current = self.results.currentIndex().row() if self.results.currentIndex().isValid() else rows[0]
        position = rows.index(current) if current in rows else 0
        target = self.model.index(rows[min(len(rows) - 1, max(0, position + step))])
        self.results.setCurrentIndex(target)
        self.results.scrollTo(target)

    def activate_row(self, ordinal):
        """Alt+N opens the Nth result, not counting section headers."""
        row = self.model.actionable(ordinal)
        if self.mode() in ("apps", "files", "clips") and row is not None:
            self.launch_index(self.model.index(row))

    def search_files(self):
        if self.mode() == "apps":
            query = self.search.text().strip()
            if len(query) >= 3 and query not in self._file_hits:
                self._file_hits = {query: file_rows(query, limit=4)}   # keep only the latest query
                if self._file_hits[query]:
                    selected = self.results.currentIndex().row()
                    self.filter(self.search.text())
                    if 0 <= selected < self.model.rowCount():
                        self.results.setCurrentIndex(self.model.index(selected))
            return
        if self.mode() != "files":
            return
        query = self.search.text().lstrip()[1:].strip()
        rows = file_rows(query)
        self.model.set_apps(rows)
        if rows:
            self.results.setCurrentIndex(self.model.index(0))
            self.show_hint("")
        else:
            self.show_hint("Type at least 2 letters of a file or folder name." if len(query) < 2 else "No files match.")
        self.results.setVisible(bool(rows))
        self.fit()

    def set_alt(self, held):
        """Show ALT 1–9 hints only while Alt is held (cleaner rows otherwise)."""
        if self.model.alt_held != held:
            self.model.alt_held = held
            self.results.viewport().update()

    def activate(self):
        mode = self.mode()
        argument = self.search.text().lstrip()[1:].strip()
        if mode == "task":
            if add_task(self.board.context, argument):
                self.search.clear()
                self.show_hint("Task added to today.")
                self.board.run("refresh")
        elif mode == "run":
            self.run_command(argument)
        elif mode == "ask":
            self.ask()
        elif mode in ("apps", "files", "clips"):
            index = self.results.currentIndex()
            if not index.isValid() and self.model.rowCount():
                index = self.model.index(0)
            if index.isValid():
                self.launch_index(index)

    def run_command(self, command):
        """Run exactly what the user typed, through their shell, detached from Genta."""
        if not command:
            return
        environment = {key: value for key, value in os.environ.items() if key not in UNSET_FOR_CHILDREN}
        shell = os.environ.get("SHELL") or "/bin/sh"
        try:
            subprocess.Popen([shell, "-c", command], cwd=str(Path.home()), env=environment, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            self.show_hint(f"Could not run {shlex.quote(command)}: {error}", error=True)
            return
        self.hide()

    def launch_index(self, index):
        app = self.model.apps[index.row()]
        if app.id.startswith("header:"):
            return
        if app.id == "ask:":
            self.search.setText("? " + self.search.text().strip())
            self.ask()
            return
        if app.id.startswith("settings:"):
            from PySide6.QtCore import QProcess
            QProcess.startDetached("cosmic-settings", [app.id.split(":", 1)[1]])
            self.hide()
            return
        if app.id == "file:":
            open_path(app.path)
            self.hide()
            return
        if app.id == "clip:":
            QGuiApplication.clipboard().setText(app.path)
            self.show_hint("Copied to the clipboard.")
            self.hide()
            return
        if app.id == "calc:":
            QGuiApplication.clipboard().setText(app.path)
            self.show_hint(f"Copied {app.path}")
            return
        if app.id.startswith("window:"):
            engine = getattr(self.workspace, "tip_engine", None)
            if engine is not None and engine.watch.activate(int(app.id.split(":")[1])):
                self.hide()
            else:
                self.show_hint("Could not switch to that window.", error=True)
            return
        self.launch_app(app)

    def launch_app(self, app):
        try:
            launch(app)
        except OSError as error:
            self.show_hint(str(error), error=True)
            return
        self.usage.record(app.id)
        try:
            self.store.set_setting("launcher_usage", self.usage.data)
        except Exception:
            pass  # ranking memory is optional; never block a launch on it
        self.hide()

    # ── window ─────────────────────────────────────────────────
    def show_hint(self, message, error=False):
        self.hint.setProperty("error", "true" if error else "false")
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)
        self.hint.setText(message)
        self.hint.setVisible(bool(message))

    def tick(self):
        now = datetime.now()
        self.clock.setText(QLocale().toString(now.time(), "HH:mm"))
        self.date.setText(QLocale().toString(now.date(), "ddd d MMM").upper())
        if self.board.isVisible():
            self.board.run("tick")
        self.update_bell()

    def update_bell(self):
        engine = getattr(self.workspace, "tip_engine", None)
        clock = self.workspace.clock
        if self.answer.busy():
            self.bell.set_state("thinking")
        elif clock.running:
            fraction = 1 - clock.remaining() / clock.duration if clock.duration else 0.0
            self.bell.set_state("focus", max(0.0, min(1.0, fraction)))
        elif engine is not None and engine.tips:
            self.bell.set_state("tip")
        elif engine is not None and engine.watch.available():
            self.bell.set_state("watching")
        elif engine is not None and engine.watch.error:
            self.bell.set_state("error")
        else:
            self.bell.set_state("idle")

    def search_for(self, text=""):
        """Open (or keep open) and put text in the search box, cursor at the end."""
        if not self.isVisible():
            self.present()
        self.search.setText(text)
        self.search.setCursorPosition(len(text))
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.present()

    def present(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self._was_active = False
        self.search.clear()
        self.filter("")
        self.board.run("refresh")
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self._slide = 0.0
        self.fit()
        self.tick()
        self.timer.start()
        # Maximized: COSMIC ignores X11 positions, caps floating sizes, and drops alpha when full screen.
        self.showMaximized()
        self.slide_in()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def escape(self):
        if self.search.text():
            self.search.clear()
        else:
            self.hide()

    def hideEvent(self, event):
        self.timer.stop()
        self.board.run("stop")
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active:
                QTimer.singleShot(150, self._hide_if_inactive)
        super().changeEvent(event)

    def _hide_if_inactive(self):
        if self.isVisible() and not self.isActiveWindow() and not QApplication.activePopupWidget() and not QApplication.activeModalWidget():
            self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scrim = QColor(*SCRIM)
        if self.docked():
            scrim.setAlpha(36)   # popover: barely dim the desktop
        painter.fillRect(self.rect(), scrim)
        bounds = QRectF(self.card.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Soft floating shadow.
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((18, 14), (12, 22), (7, 32), (3, 40)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(bounds.adjusted(-spread, -spread + 6, spread, spread + 10), RADIUS + spread, RADIUS + spread)
        path = QPainterPath()
        path.addRoundedRect(bounds, RADIUS, RADIUS)
        if self.docked():
            # Notch pointing up at the Genta clock in the centre of the top bar.
            centre = bounds.center().x()
            notch = QPainterPath()
            notch.moveTo(centre - NOTCH * 1.4, bounds.top() + 1)
            notch.lineTo(centre, bounds.top() - NOTCH)
            notch.lineTo(centre + NOTCH * 1.4, bounds.top() + 1)
            notch.closeSubpath()
            path = path.united(notch)
        painter.fillPath(path, QColor(*FILL))
        sheen = QLinearGradient(bounds.topLeft(), bounds.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(0.3, QColor(255, 255, 255, 6))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, sheen)
        painter.setPen(QPen(QColor(*BORDER), 1))
        painter.drawPath(path)
