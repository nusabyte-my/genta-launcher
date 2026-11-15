from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton
from i18n import tr


class LocalLabel(QLabel):
    def setText(self, text):
        self.setToolTip(text if len(text) > 80 else '')
        super().setText(tr(text))


class LocalButton(QPushButton):
    def setText(self, text):
        super().setText(tr(text))


def label(text, name=None, translate=True):
    widget = LocalLabel(tr(text)) if translate else QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    if name == 'muted':
        widget.setWordWrap(True)
    return widget


def button(text, callback, name=None, tooltip=None):
    widget = LocalButton(tr(text))
    if name:
        widget.setObjectName(name)
    if tooltip:
        widget.setToolTip(tr(tooltip))
        widget.setAccessibleName(tr(tooltip))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            widget = item.widget()
            widget.hide()
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())
