"""Small shared widget helpers."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton


def label(text="", name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def push(text, slot, name=None, tip=None):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if name:
        widget.setObjectName(name)
    if tip:
        widget.setToolTip(tip)
    widget.clicked.connect(slot)
    return widget
