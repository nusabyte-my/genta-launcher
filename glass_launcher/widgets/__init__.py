"""Launcher widgets: a small base class, the built-in catalog and user plugins.

Write your own widget in ``~/.config/genta-launcher/widgets/<name>.py``::

    from glass_launcher.widgets import LauncherWidget

    class Hello(LauncherWidget):
        id = "hello"
        title = "HELLO"
        description = "A friendly greeting"

        def __init__(self, context):
            super().__init__(context)
            self.text = self.add_label("Hi!")

        def refresh(self):          # launcher opened
            self.text.setText("Hi again!")

    WIDGETS = [Hello]

Plugins are ordinary Python running inside Genta with your user's rights.
Only put files you wrote or trust in that folder.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from ..ui import label, push

PLUGIN_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "genta-launcher/widgets"
DEFAULT_LAYOUT = ["focus", "solat", "todo", "tips", "weather", "music"]


@dataclass
class WidgetContext:
    """What a widget may use: the live Genta window and two launcher callbacks."""
    workspace: object
    report: Callable[[str], None]       # show an error line in the launcher
    close: Callable[[], None]           # hide the launcher (e.g. after opening something)

    @property
    def store(self):
        return self.workspace.store


class LauncherWidget(QFrame):
    """Base class. Set ``id``/``title``/``description`` and build UI in ``__init__``."""
    id = ""
    title = ""
    description = ""
    menu_requested = Signal()

    def __init__(self, context: WidgetContext):
        super().__init__()
        from ..style import card_style, pastel_for
        self.context = context
        self.setObjectName("card")
        self.setMinimumHeight(138)
        self.tone = pastel_for(self.id)
        self.setStyleSheet(card_style(self.tone))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 10, 14)
        outer.setSpacing(7)
        header = QHBoxLayout()
        header.addWidget(label("●  " + (self.title or self.id.upper()), "eyebrow"), 1)
        header.addWidget(push("⋯", self.menu_requested.emit, "ghost", "Move or remove this widget"))
        outer.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(7)
        outer.addLayout(self.body)
        outer.addStretch()

    def add_label(self, text="", name=None, wrap=True):
        widget = label(text, name, wrap)
        self.body.addWidget(widget)
        return widget

    # Lifecycle hooks; all optional.
    def refresh(self):
        """Launcher opened, or data may have changed."""

    def tick(self):
        """Once per second while the launcher is open."""

    def stop(self):
        """Launcher hidden; release timers, sessions, devices."""

    def contextMenuEvent(self, event):
        self.menu_requested.emit()


def load_plugins(folder=PLUGIN_DIR):
    """Return ({id: class}, [error messages]) from plugin files. Errors never stop the launcher."""
    found, errors = {}, []
    if not folder.is_dir():
        return found, errors
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = f"genta_launcher_widget_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            classes = list(getattr(module, "WIDGETS", [])) or [getattr(module, "WIDGET")]
        except Exception as error:  # a broken plugin must not break the launcher
            errors.append(f"{path.name}: {error}")
            continue
        for cls in classes:
            if isinstance(cls, type) and issubclass(cls, LauncherWidget) and cls.id:
                found.setdefault(cls.id, cls)
            else:
                errors.append(f"{path.name}: {cls!r} is not a LauncherWidget with an id")
    return found, errors


def catalog():
    """Built-in widgets first; a plugin cannot replace a built-in id."""
    from .builtin import BUILTINS
    plugins, errors = load_plugins()
    widgets = {cls.id: cls for cls in BUILTINS}
    for key, cls in plugins.items():
        if key in widgets:
            errors.append(f"{cls.__module__}: id '{key}' is already used by a built-in widget")
        else:
            widgets[key] = cls
    return widgets, errors
