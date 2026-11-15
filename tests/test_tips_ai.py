"""Tip rules and AI query parsing. Run: ~/Desktop/Genta/.venv/bin/python -m unittest discover -s tests"""
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "genta")]

from PySide6.QtCore import QCoreApplication  # noqa: E402

APP = QCoreApplication.instance() or QCoreApplication([])

from store import Store  # noqa: E402
from glass_launcher import ai  # noqa: E402
from glass_launcher.tips import TipEngine  # noqa: E402


class FakeClock:
    def __init__(self, running=False):
        self.running, self.duration, self.task_id = running, 1500, None

    def remaining(self):
        return 900

    def active_seconds(self):
        return 600 if self.running else 0


class FakeCalendars:
    def __init__(self, events=()):
        self._events = list(events)

    def events(self):
        return self._events


class FakeWorkspace:
    def __init__(self, store, running=False, events=()):
        self.store, self.clock, self.calendars = store, FakeClock(running), FakeCalendars(events)
        self.badge = None

    def set_tip_badge(self, count):
        self.badge = count


class TipRules(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "g.sqlite3")
        self.store.set_setting("launcher_ai_tips", False)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def engine(self, **kwargs):
        engine = TipEngine(FakeWorkspace(self.store, **kwargs))
        return engine

    def test_distraction_during_focus(self):
        self.store.set_setting("distracting_apps", "youtube, Opera")
        engine = self.engine(running=True)
        engine.watch.active_app, engine.watch.active_since = "Opera developer", time.monotonic() - 120
        engine.evaluate()
        self.assertTrue(any(t.id == "distraction" for t in engine.tips))
        self.assertEqual(engine.workspace.badge, len(engine.tips))

    def test_no_distraction_without_focus(self):
        self.store.set_setting("distracting_apps", "Opera")
        engine = self.engine(running=False)
        engine.watch.active_app, engine.watch.active_since = "Opera developer", time.monotonic() - 120
        engine.evaluate()
        self.assertFalse(any(t.id == "distraction" for t in engine.tips))

    def test_meeting_soon_and_overdue(self):
        start = (date.today()).isoformat()
        from datetime import datetime
        soon = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
        self.store.add_task("Old thing", day=(date.today() - timedelta(days=3)).isoformat())
        engine = self.engine(events=[{"id": "e1", "title": "Standup", "start": soon, "end": soon, "join_url": "https://meet.example/x"}])
        engine.evaluate()
        ids = [t.id.split(":")[0] for t in engine.tips]
        self.assertIn("meeting", ids)
        self.assertIn("overdue", ids)
        meeting = next(t for t in engine.tips if t.id.startswith("meeting"))
        self.assertEqual(meeting.action_label, "Join")
        self.assertTrue(start)

    def test_mute_and_snooze(self):
        self.store.add_task("Old thing", day=(date.today() - timedelta(days=3)).isoformat())
        engine = self.engine()
        engine.evaluate()
        tip = next(t for t in engine.tips if t.id.startswith("overdue"))
        engine.snooze(tip)
        self.assertFalse(any(t.id == tip.id for t in engine.tips))
        engine._snoozed.clear()
        engine.mute(tip)
        self.assertFalse(any(t.id.startswith("overdue") for t in engine.tips))
        self.assertIn("overdue", self.store.get_setting("launcher_tips_muted"))


class QueryParsing(unittest.TestCase):
    def test_aliases_and_default(self):
        self.assertEqual(ai.parse_query("?grok hi"), ("grok", False, "hi"))
        self.assertEqual(ai.parse_query("?chatgpt hi"), ("gpt", False, "hi"))
        self.assertEqual(ai.parse_query("?meta hi"), ("muse", False, "hi"))
        self.assertEqual(ai.parse_query("? what now", "hermes"), ("hermes", False, "what now"))
        self.assertEqual(ai.parse_query("?codex! fix it"), ("codex", True, "fix it"))
        self.assertEqual(ai.parse_query("?grok! hi"), ("grok", False, "hi"))   # write only applies to codex

    def test_missing_key_message(self):
        store = Store(Path(tempfile.mkdtemp()) / "g.sqlite3")
        original = ai.get_key
        ai.get_key = lambda pid: ""
        try:
            with self.assertRaisesRegex(ValueError, "No Grok key"):
                ai.chat(store, "grok", "hi")
        finally:
            ai.get_key = original
            store.close()


if __name__ == "__main__":
    unittest.main()
