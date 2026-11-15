"""Regression coverage for pause/resume and focus accounting boundaries."""
import tempfile
import unittest
from pathlib import Path

from genta import FocusClock
from store import Store


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "genta.sqlite3")
        self.now = 0.0
        self.clock = FocusClock(self.store, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def focus_seconds(self):
        return self.store.insights()["focus_today_seconds"]

    def test_pause_resume_excludes_idle_and_repeated_checkpoint(self):
        self.clock.start()
        self.now = 10.7
        self.clock.pause()
        self.clock.checkpoint()
        self.assertEqual(self.focus_seconds(), 10)
        self.now = 500
        self.clock.start()
        self.now = 505.5
        self.clock.pause()
        self.assertEqual(self.focus_seconds(), 16)
        self.assertAlmostEqual(self.clock.active_seconds(), 16.2)

    def test_delayed_countdown_completion_never_inflates_time(self):
        self.clock.reset(duration=5)
        self.clock.start()
        self.now = 60
        self.clock.pause()
        self.assertEqual(self.focus_seconds(), 5)
        self.assertEqual(self.clock.remaining(), 0)

    def test_relaunch_restores_paused_without_counting_twice(self):
        self.clock.start()
        self.now = 31.4
        self.clock.checkpoint()
        restored = FocusClock(self.store, clock=lambda: self.now)
        self.assertFalse(restored.running)
        self.assertAlmostEqual(restored.active_seconds(), 31.4)
        restored.checkpoint()
        self.assertEqual(self.focus_seconds(), 31)
        restored.start()
        self.now += 10
        restored.pause()
        self.assertEqual(self.focus_seconds(), 41)

    def test_stopwatch_and_reset_preserve_completed_activity(self):
        self.clock.reset(duration=0)
        self.clock.start()
        self.now = 90
        self.clock.reset(duration=60)
        self.assertEqual(self.focus_seconds(), 90)
        self.assertEqual(self.clock.active_seconds(), 0)
        self.assertEqual(self.clock.remaining(), 60)


if __name__ == "__main__":
    unittest.main()
