import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(Path.home() / "Desktop/Genta")]

from PySide6.QtCore import QCoreApplication  # noqa: E402

APP = QCoreApplication.instance() or QCoreApplication([])

from store import Store  # noqa: E402
from glass_launcher.solat import SolatService  # noqa: E402


class Clock:
    running = False

    def __init__(self):
        self.toggled = 0


class Workspace:
    def __init__(self, store):
        self.store, self.clock = store, Clock()
        self.notes, self.published = [], 0
        self.solat_status = None

    def notify(self, title, message):
        self.notes.append(title)

    def publish_status(self):
        self.published += 1

    def toggle_focus(self):
        self.clock.toggled += 1


class SolatTiming(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "g.sqlite3")
        self.workspace = Workspace(self.store)
        self.service = SolatService(self.workspace)
        now = datetime.now().replace(second=0, microsecond=0)
        self.service.day = date.today()
        self.service.place = "Test"
        self.service.times = {
            "imsak": now - timedelta(hours=3), "fajr": now - timedelta(hours=2), "syuruk": now - timedelta(hours=1),
            "dhuhr": now - timedelta(minutes=2), "asr": now + timedelta(minutes=8), "maghrib": now + timedelta(hours=3),
            "isha": now + timedelta(hours=4),
        }

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_upcoming_and_current(self):
        self.assertEqual(self.service.upcoming()[0], "asr")
        self.assertEqual(self.service.current()[0], "dhuhr")

    def test_notifications_once(self):
        self.store.set_setting("solat_remind_minutes", 10)
        self.service.tick()
        self.service.tick()
        self.assertEqual(len(self.workspace.notes), 2)
        self.assertTrue(any(n.startswith("Asar in ") for n in self.workspace.notes))
        self.assertIn("Waktu Zohor", self.workspace.notes)
        self.assertEqual(self.workspace.solat_status["name"], "Asar")

    def test_pauses_focus_at_prayer_time(self):
        self.workspace.clock.running = True
        self.service.tick()
        self.assertEqual(self.workspace.clock.toggled, 1)


if __name__ == "__main__":
    unittest.main()
