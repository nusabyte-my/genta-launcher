"""Regression: Ctrl+Enter creates a task without inserting a newline."""
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from genta import GentaWindow
from store import Store


class NotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['genta-tests', '-platform', 'offscreen'])

    def test_ctrl_enter_converts_current_line_and_preserves_note(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'notes.sqlite3')
            window = GentaWindow(store)
            try:
                window.notes.setPlainText('Keep this idea')
                key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier, '\r')
                self.app.sendEvent(window.notes, key)
                window.save_note()
                self.assertEqual([t['title'] for t in store.tasks()], ['Keep this idea'])
                self.assertEqual(store.note(window.note_day), 'Keep this idea')
            finally:
                window.tick_timer.stop()
                window.reminder_timer.stop()
                window.note_timer.stop()
                window.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                store.close()


if __name__ == '__main__':
    unittest.main()
