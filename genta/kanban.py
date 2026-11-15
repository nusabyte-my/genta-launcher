"""Task-backed Kanban columns. Moving cards changes existing task fields only."""
from datetime import date, datetime

from PySide6.QtCore import QByteArray, QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPen
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QStyledItemDelegate, QStyle, QVBoxLayout

from i18n import tr
from widgets import label

MIME = 'application/x-genta-task-uid'


def stage(task):
    return 'done' if task['done'] else 'inbox' if task['day'] is None else 'planned'


def caption(task):
    from PySide6.QtCore import QLocale
    if task['due_at']:
        when = QLocale().toString(datetime.fromisoformat(task['due_at']), 'ddd d MMM · HH:mm')
    elif task['day']:
        when = QLocale().toString(date.fromisoformat(task['day']), 'ddd d MMM')
    else:
        when = tr('No scheduled date')
    return ' · '.join(value for value in (task['project'], when) if value)


class CardPainter(QStyledItemDelegate):
    def __init__(self, board, parent):
        super().__init__(parent)
        self.board = board

    def paint(self, painter, option, index):
        task = self.board.task(index.data(Qt.ItemDataRole.UserRole))
        if task is None:
            return
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setBrush(QColor('#292929' if selected or hovered else '#202020'))
        painter.setPen(QPen(QColor('#e84a3c' if selected else '#343434'), 1))
        painter.drawRoundedRect(QRectF(rect), 10, 10)
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        checkbox = QRect(rect.right()-27 if rtl else rect.left()+12, rect.top()+15, 14, 14)
        painter.setBrush(QColor('#e84a3c') if task['done'] else Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#e84a3c' if task['done'] else '#777773'), 1))
        painter.drawRoundedRect(QRectF(checkbox), 3, 3)
        if task['done']:
            painter.setPen(QPen(QColor('#101010'), 1.6))
            painter.drawLine(checkbox.left()+3, checkbox.top()+7, checkbox.left()+6, checkbox.top()+10)
            painter.drawLine(checkbox.left()+6, checkbox.top()+10, checkbox.left()+11, checkbox.top()+4)
        title = rect.adjusted(12 if rtl else 36, 11, -36 if rtl else -12, -49)
        font = QFont(option.font)
        font.setPointSize(10)
        font.setStrikeOut(bool(task['done']))
        painter.setFont(font)
        painter.setPen(QColor('#999995' if task['done'] else '#eeece7'))
        align = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
        painter.drawText(title, align | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, task['title'])
        font.setPointSize(9)
        font.setStrikeOut(False)
        painter.setFont(font)
        painter.setPen(QColor('#a3a39e'))
        detail = rect.adjusted(12, 65, -12, -12)
        text = painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.UserRole+1), Qt.TextElideMode.ElideRight, detail.width())
        painter.drawText(detail, align | Qt.AlignmentFlag.AlignTop, text)
        if task['deadline']:
            painter.setPen(QColor('#e84a3c' if task['deadline'] < date.today().isoformat() and not task['done'] else '#a3a39e'))
            painter.drawText(rect.adjusted(12, 83, -12, -9), align | Qt.AlignmentFlag.AlignTop, tr('Deadline') + ' ' + task['deadline'])
        painter.restore()

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            rect = option.rect.adjusted(2, 2, -2, -2)
            rtl = option.direction == Qt.LayoutDirection.RightToLeft
            checkbox = QRect(rect.right()-31 if rtl else rect.left()+8, rect.top()+11, 22, 22)
            if checkbox.contains(event.position().toPoint()):
                checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
                return model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
            return model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        return False


class CardList(QListWidget):
    def __init__(self, board, key):
        super().__init__(board)
        self.board, self.key = board, key
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSpacing(4)
        self.setMinimumWidth(190)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(False)
        self.setMouseTracking(True)
        self.setStyleSheet('QListWidget { background: transparent; border: none; }')
        self.setItemDelegate(CardPainter(board, self))
        self.itemDoubleClicked.connect(lambda item: board.openRequested.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.itemChanged.connect(self.changed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)

    def changed(self, item):
        if not self.board.updating:
            task = self.board.task(item.data(Qt.ItemDataRole.UserRole))
            checked = item.checkState() == Qt.CheckState.Checked
            if task and checked != bool(task['done']):
                self.board.completionRequested.emit(task['uid'], checked)

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME, QByteArray(item.data(Qt.ItemDataRole.UserRole).encode()))
        drag.setMimeData(mime)
        drag.setPixmap(self.viewport().grab(self.visualItemRect(item)))
        drag.setHotSpot(QPoint(20, 20))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if self.accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def accepts(self, event):
        if not event.mimeData().hasFormat(MIME):
            return False
        try:
            return self.board.task(bytes(event.mimeData().data(MIME)).decode()) is not None
        except UnicodeDecodeError:
            return False

    def dropEvent(self, event):
        if not self.accepts(event):
            event.ignore()
            return
        uid = bytes(event.mimeData().data(MIME)).decode()
        if stage(self.board.task(uid)) != self.key:
            self.board.moveRequested.emit(uid, self.key)
        event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count():
            from PySide6.QtGui import QPainter
            painter = QPainter(self.viewport())
            painter.setPen(QColor('#777773'))
            painter.drawText(self.viewport().rect().adjusted(12, 12, -12, -12), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, tr('Drop a card here'))

    def menu(self, point):
        item = self.itemAt(point)
        if item is None:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction(tr('Task details…'), lambda: self.board.openRequested.emit(uid))
        if not self.board.task(uid)['done']:
            menu.addAction(tr('Focus on this task'), lambda: self.board.focusRequested.emit(uid))
        menu.addSeparator()
        for text, destination in [(tr('Move to inbox'), 'inbox'), (tr('Move to today'), 'planned'), (tr('Mark complete'), 'done')]:
            if destination != self.key:
                menu.addAction(text, lambda checked=False, key=destination: self.board.moveRequested.emit(uid, key))
        menu.addSeparator()
        menu.addAction(tr('Delete task…'), lambda: self.board.deleteRequested.emit(uid))
        menu.exec(self.viewport().mapToGlobal(point))


class KanbanBoard(QFrame):
    moveRequested = Signal(str, str)
    completionRequested = Signal(str, bool)
    openRequested = Signal(str)
    focusRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks = {}
        self.updating = False
        self.columns, self.counts = {}, {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for key, title in [('inbox', tr('Inbox')), ('planned', tr('Planned')), ('done', tr('Done'))]:
            column = QFrame(self)
            column.setObjectName('kanbanColumn')
            column.setStyleSheet('QFrame#kanbanColumn { background: #171717; border: 1px solid #2b2b2b; border-radius: 12px; }')
            layout = QVBoxLayout(column)
            layout.setContentsMargins(8, 10, 8, 8)
            heading = QHBoxLayout()
            heading.addWidget(label(title), 1)
            count = QLabel('0')
            count.setObjectName('muted')
            heading.addWidget(count)
            layout.addLayout(heading)
            cards = CardList(self, key)
            cards.setAccessibleName(title)
            layout.addWidget(cards, 1)
            self.columns[key], self.counts[key] = cards, count
            row.addWidget(column, 1)

    def task(self, uid):
        return self._tasks.get(uid)

    def set_tasks(self, tasks):
        self.updating = True
        try:
            self._tasks = {task['uid']: task for task in tasks}
            for cards in self.columns.values():
                cards.clear()
            for task in tasks:
                item = QListWidgetItem(task['title'])
                item.setData(Qt.ItemDataRole.UserRole, task['uid'])
                detail = caption(task)
                item.setData(Qt.ItemDataRole.UserRole+1, detail)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if task['done'] else Qt.CheckState.Unchecked)
                item.setSizeHint(QSize(0, 112))
                item.setToolTip(task['title'] + '\n' + detail)
                self.columns[stage(task)].addItem(item)
            for key, cards in self.columns.items():
                self.counts[key].setText(str(cards.count()))
        finally:
            self.updating = False
