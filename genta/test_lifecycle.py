"""Closing collapses the workspace; explicit Quit must exit its event loop."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LifecycleTests(unittest.TestCase):
    def test_explicit_quit_exits_after_normal_close_collapses(self):
        root = Path(__file__).resolve().parent
        script = '''
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from genta import GentaWindow
from store import Store
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
store = Store(Path(sys.argv[1]) / 'planner.sqlite3')
window = GentaWindow(store)
window.show()
window.expand()
window.close()
assert not window.isVisible() and not window.expanded  # collapsed = hidden; the pill is in the top bar
QTimer.singleShot(0, window.quit)
QTimer.singleShot(3000, lambda: app.exit(23))
result = app.exec()
store.close()
raise SystemExit(result)
'''
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, '-c', script, folder], cwd=root,
                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
