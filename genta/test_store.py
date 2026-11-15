"""Consumer-visible task, note, reminder and activity regression tests."""
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from store import Store, parse_capture

NOW = datetime(2026, 11, 14, 12, 0)


class FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 11, 14)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'genta.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.addCleanup(patch.stopall)
        patch('store.date', FixedDate).start()
        patch('store.datetime', FixedDatetime).start()

    def test_capture_suffix_dates_and_clock_boundaries(self):
        self.assertEqual(parse_capture('Call Sam tomorrow at 5pm', NOW), ('Call Sam', '2026-11-15', '2026-11-15T17:00:00'))
        self.assertEqual(parse_capture('Review today at 12am', NOW), ('Review', '2026-11-14', '2026-11-14T00:00:00'))
        self.assertEqual(parse_capture('Review at 9', NOW), ('Review', '2026-11-15', '2026-11-15T09:00:00'))
        self.assertEqual(parse_capture('Review saturday at 10am', NOW), ('Review', '2026-11-21', '2026-11-21T10:00:00'))
        self.assertEqual(parse_capture('Review monday', NOW), ('Review', '2026-11-16', None))
        self.assertEqual(parse_capture('Discuss today report', NOW), ('Discuss today report', None, None))
        self.assertEqual(parse_capture('Lunch at 12pm', NOW), ('Lunch', '2026-11-14', '2026-11-14T12:00:00'))
        for text in ['', 'Task at 25:00', 'Task at 0pm', 'Task today at 5:99pm']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_capture(text, NOW)

    def test_inbox_priority_date_capture_and_explicit_reminder(self):
        inbox = self.store.add_task("Inbox item")
        priority = self.store.add_task('Important', day='2026-11-14', priority=2)
        captured = self.store.add_task('Call tomorrow at 5pm', day='2026-11-14')
        explicit = self.store.add_task('Call tomorrow at 5pm', due_at='2026-11-20T09:00:00')
        self.assertEqual([t['id'] for t in self.store.tasks(None)], [inbox['id']])
        self.assertEqual(self.store.tasks()[0]['id'], priority['id'])
        self.assertEqual(captured['due_at'], '2026-11-15T17:00:00')
        self.assertEqual((explicit['day'], explicit['due_at']), ('2026-11-20', '2026-11-20T09:00:00'))

    def test_completion_reopen_and_history_use_completion_day(self):
        task = self.store.add_task('Overdue task', day='2026-11-01')
        complete = self.store.update_task(task['id'], done=True)
        self.assertEqual(complete['completed_at'], '2026-11-14T12:00:00')
        self.assertEqual(self.store.insights()['completed_today'], 1)
        self.assertEqual(self.store.insights()['streak'], 1)
        self.assertEqual(self.store.tasks(include_done=False), [])
        reopened = self.store.update_task(task['id'], done=False)
        self.assertIsNone(reopened['completed_at'])
        self.assertEqual(self.store.insights()['completed_today'], 0)

    def test_notes_and_preferences_survive_a_new_connection(self):
        self.store.save_note('2026-11-14', 'Line one\nLine two — saved')
        self.store.set_setting('pinned', True)
        with self.assertRaises(ValueError):
            self.store.save_note(None, 'No day')
        other = Store(self.path)
        try:
            self.assertEqual(other.note('2026-11-14'), 'Line one\nLine two — saved')
            self.assertTrue(other.get_setting('pinned'))
            self.assertEqual(other.note('2026-11-13'), '')
        finally:
            other.close()

    def test_two_connections_claim_reminders_once_and_snooze_rearms(self):
        due = self.store.add_task('Due', due_at='2026-11-14T11:00:00')
        self.store.add_task('Future', due_at='2026-11-14T13:00:00')
        done = self.store.add_task('Done already', due_at='2026-11-14T10:00:00')
        self.store.update_task(done['id'], done=True)
        other = Store(self.path)
        try:
            self.assertEqual([t['id'] for t in self.store.due_reminders(NOW)], [due['id']])
            self.assertEqual(other.due_reminders(NOW), [])
            other.update_task(due['id'], due_at='2026-11-14T12:05:00')
            self.assertEqual(self.store.due_reminders(NOW), [])
            self.assertEqual([t['id'] for t in other.due_reminders(NOW + timedelta(minutes=6))], [due['id']])
        finally:
            other.close()
        restarted = Store(self.path)
        try:
            self.assertEqual(restarted.due_reminders(NOW + timedelta(minutes=6)), [])
        finally:
            restarted.close()

    def test_scheduling_clears_obsolete_time_and_invalid_input_is_atomic(self):
        task = self.store.add_task('Scheduled', due_at='2026-11-14T11:00:00')
        moved = self.store.update_task(task['id'], day=None)
        self.assertIsNone(moved['day'])
        self.assertIsNone(moved['due_at'])
        for changes in [{'title': ''}, {'day': '2026-99-01'}, {'priority': 3}, {'done': 'yes'}, {'due_at': '2026-11-14T10:00:00+02:00'}, {'extra': 'unknown'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.update_task(task['id'], **changes)
        self.assertEqual(self.store.tasks()[0], moved)

    def test_focus_is_actual_activity_and_survives_task_deletion(self):
        task = self.store.add_task('Focus task')
        self.store.record_focus(75, task['id'])
        self.store.record_focus(25)
        self.store.delete_task(task['id'])
        self.assertEqual(self.store.insights()['focus_today_seconds'], 100)
        self.assertIsNone(self.store.export_data()['focus_sessions'][0]['task_id'])
        self.assertEqual(sum(d['focus_seconds'] for d in self.store.insights()['history']), 100)
        for seconds in [0, -1, 1.5, True]:
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.store.record_focus(seconds)


if __name__ == '__main__':
    unittest.main()
