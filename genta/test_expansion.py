from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calendar_service import from_ics
from store import Store
from sync_service import SyncHub, paired_request


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.a = Store(Path(self.temp.name) / 'a.sqlite3')
        self.b = Store(Path(self.temp.name) / 'b.sqlite3')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def test_repeating_completion_creates_one_next_occurrence(self):
        task = self.a.add_task('Daily task', day=date.today().isoformat(), repeat_rule='daily', project='Genta', estimate_minutes=15, deadline=date.today().isoformat())
        self.a.update_task(task['id'], done=True)
        self.a.update_task(task['id'], done=True)
        opened = self.a.tasks(include_done=False)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]['project'], 'Genta')
        self.assertEqual(opened[0]['estimate_minutes'], 15)
        self.assertGreater(opened[0]['day'], date.today().isoformat())
        self.a.update_task(task['id'], done=False)
        self.a.update_task(task['id'], done=True)
        self.assertEqual(len(self.a.tasks(include_done=False)), 1)

    def test_day_plan_respects_meetings_existing_slots_and_capacity(self):
        now = datetime(2026, 11, 14, 9)
        a = self.a.add_task('First', day='2026-11-14', priority=2, estimate_minutes=30)
        b = self.a.add_task('Second', day='2026-11-14', estimate_minutes=30)
        c = self.a.add_task('Too large', day='2026-11-14', estimate_minutes=180)
        events = [{'start': '2026-11-14T09:10:00', 'end': '2026-11-14T10:00:00'}]
        result = self.a.plan_day(events, end_hour=11, now=now)
        self.assertEqual(result['scheduled'], [a['id'], b['id']])
        self.assertEqual(result['unscheduled'], [c['id']])
        tasks = {t['id']: t for t in self.a.tasks()}
        self.assertEqual(tasks[a['id']]['due_at'], '2026-11-14T10:00:00')
        self.assertEqual(tasks[b['id']]['due_at'], '2026-11-14T10:30:00')
        self.assertIsNone(tasks[c['id']]['due_at'])

    def test_sync_merges_independent_fields_and_preserves_note_versions(self):
        created = self.a.add_task('Original title', project='Original project')
        self.a.save_note('2026-11-14', 'Original note')
        self.b.ingest_events(self.a.sync_events())
        local_b = self.b.tasks()[0]
        self.a.update_task(created['id'], title='New title')
        self.b.update_task(local_b['id'], project='New project')
        self.a.save_note('2026-11-14', 'Desktop A note')
        self.b.save_note('2026-11-14', 'Desktop B note')
        self.a.ingest_events(self.b.sync_events())
        self.b.ingest_events(self.a.sync_events())
        for store in (self.a, self.b):
            self.assertEqual(store.tasks()[0]['title'], 'New title')
            self.assertEqual(store.tasks()[0]['project'], 'New project')
            texts = {row['text'] for row in store.export_data()['note_versions']}
            self.assertTrue({'Original note', 'Desktop A note', 'Desktop B note'} <= texts)
        self.assertEqual(self.a.note('2026-11-14'), self.b.note('2026-11-14'))

    def test_sync_delete_tombstone_prevents_stale_edits_reviving_tasks(self):
        task = self.a.add_task('A task')
        self.b.ingest_events(self.a.sync_events())
        self.b.update_task(self.b.tasks()[0]['id'], title='Stale edit')
        self.a.delete_task(task['id'])
        self.a.ingest_events(self.b.sync_events())
        self.b.ingest_events(self.a.sync_events())
        self.assertEqual(self.a.tasks(), [])
        self.assertEqual(self.b.tasks(), [])

    def test_synced_completion_and_focus_are_idempotent(self):
        task = self.a.add_task('Finish this')
        self.a.update_task(task['id'], done=True)
        self.a.record_focus(75, task['id'])
        events = self.a.sync_events()
        self.b.ingest_events(events)
        self.b.ingest_events(events)
        self.assertTrue(self.b.tasks()[0]['done'])
        self.assertEqual(self.b.insights()['focus_today_seconds'], 75)
        self.assertEqual(self.b.insights()['completed_today'], 1)

    def test_wrap_up_explicitly_clears_obsolete_reminders(self):
        task = self.a.add_task('Unfinished', day=date.today().isoformat(), due_at=datetime.now().replace(microsecond=0).isoformat())
        self.assertEqual(self.a.wrap_up('inbox'), 1)
        result = self.a.tasks(None)[0]
        self.assertEqual(result['id'], task['id'])
        self.assertIsNone(result['due_at'])

    def test_recurring_ics_and_join_link(self):
        content = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:meeting\r\nDTSTART:20261115T100000Z\r\nDTEND:20261115T103000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Daily review\r\nDESCRIPTION:Join https://meet.google.com/abc-defg-hij\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
        events = from_ics(content, 'Calendar', datetime(2026, 11, 14, tzinfo=timezone.utc))
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e['join_url'] == 'https://meet.google.com/abc-defg-hij' for e in events))
        self.assertEqual(len({e['id'] for e in events}), 3)

    def test_pinned_tls_sync_rejects_changed_identity_before_writing(self):
        with patch('sync_service.secret', return_value='isolated-test-token'):
            hub = SyncHub(self.a.path, '127.0.0.1', port=0)
        pairing = hub.start()
        self.addCleanup(hub.stop)
        task = self.b.add_task('Transferred over real TLS')
        wrong = {**pairing, 'fingerprint': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'certificate changed'):
            paired_request(wrong, {'events': self.b.sync_events(), 'cursor': 0})
        self.assertEqual(self.a.tasks(), [])
        result = paired_request(pairing, {'events': self.b.sync_events(), 'cursor': 0})
        self.assertTrue(result['events'])
        self.assertEqual(self.a.tasks()[0]['uid'], task['uid'])
        self.assertEqual(self.a.tasks()[0]['title'], 'Transferred over real TLS')


if __name__ == '__main__':
    unittest.main()
