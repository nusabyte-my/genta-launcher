"""Feature panels and service lifecycle for the native Genta workspace."""
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.parse import urlencode
import uuid

from PySide6.QtCore import QDate, QDateTime, QFileSystemWatcher, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QImage, QPixmap
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDateEdit, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget

from calendar_service import Calendars, secret, set_secret, travel_time, https_url
from desktop_services import CameraPreview, Glance, VoiceRecorder, active_application, background
from hermes_bridge import AIClient
from i18n import LANGUAGES, tr
from music_widget import MusicWidget
from soundscapes import FocusAudio, NAMES
from store import Store
from sync_service import SyncHub, pair_device, synchronize
from widgets import label, button, clear_layout

ROOT = Path(__file__).resolve().parent


class WorkspacePanels:
    def setup_services(self):
        self.calendars = Calendars(self.store)
        self.audio = FocusAudio(ROOT / 'assets/sounds', self)
        self.audio.volume(self.store.get_setting('sound_volume', 30))
        self.audio.select(self.store.get_setting('sound', 'Silence'))
        self.hub = None
        self.glance = Glance(self)
        self.works = set()
        self._nudge_busy = False
        self._nudge_last = {}
        self._sync_busy = False
        self._calendar_busy = False
        self._data_version = self.store.conn.execute('PRAGMA data_version').fetchone()[0]
        self.travel_checked = set()
        self._screenshot_seen = set()
        self.screenshot_clips = []
        self.clip_watcher = QFileSystemWatcher(self)
        self.clip_watcher.directoryChanged.connect(lambda path: QTimer.singleShot(300, self.scan_screenshots))
        self.configure_screenshot_watch()
        self.poll = QTimer(self)
        self.poll.setInterval(5000)
        self.poll.timeout.connect(self.poll_services)
        self.poll.start()
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(30000)
        self.sync_timer.timeout.connect(self.sync_now)
        if self.store.get_setting('sync_endpoint'):
            self.sync_timer.start()
        self.calendar_timer = QTimer(self)
        self.calendar_timer.setInterval(15 * 60 * 1000)
        self.calendar_timer.timeout.connect(self.refresh_calendars)
        if self.store.get_setting('calendar_feeds') or self.store.get_setting('calendar_Calendly') or self.store.get_setting('calendar_Cal.com'):
            self.calendar_timer.start()
            QTimer.singleShot(2000, self.refresh_calendars)
        QApplication.instance().screenAdded.connect(lambda screen: self.update_monitors())
        QApplication.instance().screenRemoved.connect(lambda screen: self.update_monitors())
        self.update_monitors()
        if self.store.get_setting('sync_hub_enabled', False):
            try:
                self.hub = SyncHub(self.store.path, self.store.get_setting('sync_host', '127.0.0.1'))
                self.hub.start()
            except (ValueError, OSError) as error:
                self.status.setText(str(error))

    def track_work(self, work):
        self.works.add(work)
        def retire(result):
            self.works.discard(work)
        work.signals.finished.connect(retire)
        work.signals.failed.connect(retire)
        return work

    def run_work(self, action, completed):
        return self.track_work(background(action, completed, self.status.setText))

    def poll_services(self):
        version = self.store.conn.execute('PRAGMA data_version').fetchone()[0]
        if version != self._data_version:
            self._data_version = version
            self.refresh_tasks()
            self.refresh_week()
            if not self.note_dirty:
                self.notes.blockSignals(True)
                self.notes.setPlainText(self.store.note(self.note_day))
                self.notes.blockSignals(False)
        if self.glance.isVisible():
            self.glance.refresh()
        self.check_calendar_alerts()
        if self.clock.running and self.store.get_setting('nudge_enabled', False) and not self._nudge_busy:
            self._nudge_busy = True
            def got_app(name):
                self._nudge_busy = False
                matches = [x.strip().casefold() for x in self.store.get_setting('distracting_apps', '').split(',') if x.strip()]
                if name and any(value in name.casefold() for value in matches) and time.monotonic() - self._nudge_last.get(name, -300) > 300:
                    self._nudge_last[name] = time.monotonic()
                    self.notify(tr('Return to your focus'), f'{name} · ' + tr('One small step at a time.'))
                    self.status.setText(tr('A distraction nudge was sent.'))
            work = background(active_application, got_app, lambda message: (setattr(self, '_nudge_busy', False), self.status.setText(message)))
            self.track_work(work)

    def plan_your_day(self):
        result = self.store.plan_day(self.calendars.events(), end_hour=self.store.get_setting('day_end_hour', 18))
        self.refresh_tasks()
        self.refresh_week()
        self.status.setText(tr('Scheduled {scheduled} tasks; {remaining} did not fit.').format(scheduled=len(result['scheduled']), remaining=len(result['unscheduled'])))

    def wrap_your_day(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Wrap up your day'))
        layout = QVBoxLayout(dialog)
        insights = self.store.insights()
        layout.addWidget(label(tr('{done} tasks completed · {minutes} active focus minutes today.').format(done=insights['completed_today'], minutes=insights['focus_today_seconds'] // 60)))
        layout.addWidget(label('Move unfinished tasks and clear their old reminder times:', 'muted'))
        def move(destination):
            count = self.store.wrap_up(destination)
            self.refresh_tasks()
            self.refresh_week()
            self.status.setText(tr('Moved {count} unfinished tasks.').format(count=count))
            dialog.accept()
        layout.addWidget(button('Tomorrow', lambda: move('tomorrow'), 'primary'))
        layout.addWidget(button('Inbox', lambda: move('inbox')))
        dialog.exec()

    def edit_task_details(self, task):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Task details'))
        layout = QFormLayout(dialog)
        title = QLineEdit(task['title'])
        layout.addRow(tr('Task'), title)
        inbox = QCheckBox(tr('Keep in Inbox'))
        inbox.setChecked(task['day'] is None)
        day = QDateEdit()
        day.setCalendarPopup(True)
        day.setDate(date.fromisoformat(task['day']) if task['day'] else date.today())
        layout.addRow(inbox, day)
        has_due = QCheckBox(tr('Reminder'))
        has_due.setChecked(bool(task['due_at']))
        due = QDateTimeEdit()
        due.setCalendarPopup(True)
        due.setDateTime(datetime.fromisoformat(task['due_at']) if task['due_at'] else datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1))
        layout.addRow(has_due, due)
        has_deadline = QCheckBox(tr('Deadline'))
        has_deadline.setChecked(bool(task['deadline']))
        deadline = QDateEdit()
        deadline.setCalendarPopup(True)
        deadline.setDate(date.fromisoformat(task['deadline']) if task['deadline'] else date.today())
        layout.addRow(has_deadline, deadline)
        project = QComboBox()
        project.setEditable(True)
        project.addItems(sorted({t['project'] for t in self.store.tasks() if t['project']}))
        project.setCurrentText(task['project'])
        layout.addRow(tr('Project'), project)
        repeat = QComboBox()
        for rule, title_text in [('none', 'No repeat'), ('daily', 'Daily'), ('weekdays', 'Weekdays'), ('weekly', 'Weekly'), ('monthly', 'Monthly')]:
            repeat.addItem(tr(title_text), rule)
        repeat.setCurrentIndex(max(0, repeat.findData(task['repeat_rule'])))
        layout.addRow(tr('Repeats'), repeat)
        estimate = QSpinBox()
        estimate.setRange(1, 480)
        estimate.setValue(task['estimate_minutes'])
        estimate.setSuffix(tr(' min'))
        layout.addRow(tr('Estimated duration'), estimate)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addRow(actions)
        actions.rejected.connect(dialog.reject)
        def save():
            changes = {'title': title.text(), 'day': None if inbox.isChecked() else day.date().toString('yyyy-MM-dd'), 'due_at': due.dateTime().toString('yyyy-MM-ddTHH:mm:ss') if has_due.isChecked() else None, 'deadline': deadline.date().toString('yyyy-MM-dd') if has_deadline.isChecked() else None, 'project': project.currentText(), 'repeat_rule': repeat.currentData(), 'estimate_minutes': estimate.value()}
            if self.change_task(task['id'], **changes):
                dialog.accept()
        actions.accepted.connect(save)
        dialog.exec()

    def capture_voice(self):
        dialog = VoiceRecorder(self)
        self._voice_dialog = dialog
        def recorded(path):
            dialog.transcribe.setEnabled(False)
            dialog.record.setEnabled(False)
            dialog.state.setText(tr('Transcribing with Hermes…'))
            def complete(text):
                self.task_input.setText(text)
                self.switch_tab(0)
                dialog.accept()
                self.status.setText(tr('Speech captured. Review the text, then add the task.'))
                dialog.temp.cleanup()
            def failed(message):
                dialog.busy = False
                if not dialog.isVisible():
                    dialog.temp.cleanup()
                dialog.state.setText(message)
                dialog.transcribe.setEnabled(True)
                dialog.record.setEnabled(True)
            work = background(lambda: AIClient.transcribe(path), complete, failed)
            self.track_work(work)
        dialog.recorded.connect(recorded)
        dialog.show()

    def note_history(self):
        rows = self.store.conn.execute('SELECT text FROM note_versions WHERE day=? ORDER BY id DESC LIMIT 50', (self.note_day,)).fetchall()
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Note history'))
        layout = QVBoxLayout(dialog)
        choices = QComboBox()
        for index, row in enumerate(rows):
            choices.addItem(f'{index + 1} · ' + row['text'][:60].replace('\n', ' '), row['text'])
        layout.addWidget(choices)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        layout.addWidget(preview)
        choices.currentIndexChanged.connect(lambda index: preview.setPlainText(choices.currentData() or ''))
        preview.setPlainText(choices.currentData() or '')
        def restore():
            self.notes.setPlainText(choices.currentData() or '')
            self.save_note()
            dialog.accept()
        layout.addWidget(button('Restore as a new version', restore, 'primary'))
        dialog.resize(560, 420)
        dialog.exec()

    def build_calendar(self):
        page = self.page()
        top = QHBoxLayout()
        top.addWidget(label('The next two weeks.', 'heading'), 1)
        top.addWidget(button('Connect calendars', self.configure_calendars))
        top.addWidget(button('Refresh', self.refresh_calendars))
        top.addWidget(button('Camera look', self.camera_look))
        page.addLayout(top)
        page.addWidget(label('Read-only Google/Outlook ICS feeds, Calendly and Cal.com. Your calendar is never edited.', 'muted'))
        self.calendar_state = label('No calendar connected.', 'muted')
        page.addWidget(self.calendar_state)
        self.calendar_list = self.scroll_list(page)

    def configure_calendars(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Connect calendars'))
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('Google: Calendar settings → Integrate calendar → Secret address in iCal format.\nOutlook: Calendar settings → Shared calendars → Publish calendar → ICS.\nPrivate calendar URLs and API keys are kept in the system keyring.', 'muted'))
        form = QFormLayout()
        name = QLineEdit()
        url = QLineEdit()
        url.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr('Calendar name'), name)
        form.addRow(tr('Private HTTPS ICS URL'), url)
        layout.addLayout(form)
        existing = QVBoxLayout()
        layout.addLayout(existing)
        def refresh_feeds():
            clear_layout(existing)
            for feed in self.store.get_setting('calendar_feeds', []):
                row = QHBoxLayout()
                row.addWidget(label(feed['name'], translate=False), 1)
                def remove(f=feed):
                    feeds = [entry for entry in self.store.get_setting('calendar_feeds', []) if entry['id'] != f['id']]
                    self.store.set_setting('calendar_feeds', feeds)
                    set_secret('ics:' + f['id'], '')
                    self.calendars._replace(f['name'], [])
                    refresh_feeds()
                row.addWidget(button('Remove', remove))
                existing.addLayout(row)
        def add_feed():
            def action():
                title = name.text().strip()
                if not title:
                    raise ValueError(tr('Give the calendar a name'))
                value = https_url(url.text().strip())
                feeds = self.store.get_setting('calendar_feeds', [])
                if any(feed['name'] == title for feed in feeds) or title in ('Calendly', 'Cal.com'):
                    raise ValueError(tr('Choose a unique calendar name'))
                uid = str(uuid.uuid4())
                set_secret('ics:' + uid, value)
                self.store.set_setting('calendar_feeds', feeds + [{'id': uid, 'name': title}])
                name.clear()
                url.clear()
                refresh_feeds()
            self.attempt(action)
        layout.addWidget(button('Add read-only feed', add_feed, 'primary'))
        refresh_feeds()
        options = QFormLayout()
        booking_controls = []
        for title, key in [('Calendly', 'calendly'), ('Cal.com', 'calcom')]:
            enabled = QCheckBox(tr('Enable') + ' ' + title)
            enabled.setChecked(self.store.get_setting('calendar_' + title, False))
            token = QLineEdit()
            token.setEchoMode(QLineEdit.EchoMode.Password)
            token.setPlaceholderText(tr('New token; leave blank to keep the saved token'))
            options.addRow(enabled, token)
            booking_controls.append((title, key, enabled, token))
        layout.addLayout(options)
        def save():
            def action():
                for title, key, enabled, token in booking_controls:
                    if token.text().strip():
                        set_secret(key, token.text().strip())
                    if enabled.isChecked() and not secret(key):
                        raise ValueError(tr('Add an API token before enabling this connector'))
                    self.store.set_setting('calendar_' + title, enabled.isChecked())
                    if not enabled.isChecked():
                        self.calendars._replace(title, [])
                self.calendar_timer.start()
                self.refresh_calendars()
                dialog.accept()
            self.attempt(action)
        layout.addWidget(button('Save and refresh', save, 'primary'))
        dialog.resize(660, 560)
        dialog.exec()

    def refresh_calendars(self):
        if not hasattr(self, 'calendars') or self._calendar_busy:
            return
        self._calendar_busy = True
        self.calendar_state.setText(tr('Refreshing calendars…'))
        def query():
            store = Store(self.store.path)
            try:
                return Calendars(store).refresh()
            finally:
                store.close()
        def ready(result):
            self._calendar_busy = False
            self.calendar_state.setText('\n'.join(result['errors']) if result['errors'] else tr('Calendars refreshed.'))
            self.refresh_calendar_list()
        def failed(message):
            self._calendar_busy = False
            self.calendar_state.setText(message)
        work = background(query, ready, failed)
        self.track_work(work)

    def refresh_calendar_list(self):
        clear_layout(self.calendar_list)
        events = self.calendars.events()
        upcoming = [event for event in events if datetime.fromisoformat(event['end']) >= datetime.now()]
        if not upcoming:
            self.calendar_list.addWidget(label('No upcoming events. Connect a calendar or refresh.', 'muted'))
        for event in upcoming:
            card = QFrame()
            card.setObjectName('card')
            row = QHBoxLayout(card)
            info = QVBoxLayout()
            info.addWidget(label(event['title'], translate=False))
            info.addWidget(label(event['start'].replace('T', ' ') + '  ·  ' + event['source'], 'muted', translate=False))
            if event['location']:
                info.addWidget(label(event['location'], 'muted', translate=False))
            row.addLayout(info, 1)
            if event['join_url']:
                row.addWidget(button('Join', lambda checked=False, e=event: QDesktopServices.openUrl(QUrl(e['join_url'])), 'primary'))
            if event['location'] and not event['location'].startswith(('https://', 'http://')):
                row.addWidget(button('Maps', lambda checked=False, e=event: QDesktopServices.openUrl(QUrl('https://www.google.com/maps/dir/?' + urlencode({'api': 1, 'destination': e['location']})))))
                row.addWidget(button('Time to leave', lambda checked=False, e=event: self.calculate_leave(e)))
            self.calendar_list.addWidget(card)

    def calculate_leave(self, event):
        origin = self.store.get_setting('maps_origin', '')
        if not origin:
            self.status.setText(tr('Set your starting address and Google Routes key in Settings first.'))
            return
        self.status.setText(tr('Requesting live travel time from Google Routes…'))
        def ready(result):
            leave = datetime.fromisoformat(event['start']) - timedelta(seconds=result['seconds'], minutes=self.store.get_setting('maps_buffer', 10))
            alerts = self.store.get_setting('travel_alerts', {})
            alerts[event['id']] = {'title': event['title'], 'leave': leave.isoformat(), 'start': event['start']}
            self.store.set_setting('travel_alerts', alerts)
            self.status.setText(tr('Leave at {time}; live driving estimate {minutes} minutes.').format(time=leave.strftime('%H:%M'), minutes=round(result['seconds'] / 60)))
        self.run_work(lambda: travel_time(origin, event['location']), ready)

    def check_calendar_alerts(self):
        now = datetime.now()
        seen = self.store.get_setting('meeting_alerted', [])
        before = len(seen)
        for event in self.calendars.events():
            delta = (datetime.fromisoformat(event['start']) - now).total_seconds()
            if 0 <= delta <= 300 and event['id'] not in seen:
                seen.append(event['id'])
                self.notify(tr('Meeting starts soon'), event['title'])
                self.status.setText(tr('Meeting starts soon') + ': ' + event['title'])
                self.expand()
        if len(seen) != before:
            self.store.set_setting('meeting_alerted', seen[-500:])
        alerts = self.store.get_setting('travel_alerts', {})
        changed = False
        for uid, alert in list(alerts.items()):
            if datetime.fromisoformat(alert['start']) < now:
                del alerts[uid]
                changed = True
            elif datetime.fromisoformat(alert['leave']) <= now and uid not in self.travel_checked:
                self.travel_checked.add(uid)
                self.notify(tr('Time to leave'), alert['title'])
                self.expand()
        if changed:
            self.store.set_setting('travel_alerts', alerts)

    def camera_look(self):
        dialog = CameraPreview(self)
        dialog.show()
        self._camera_dialog = dialog

    def build_music(self):
        page = self.page()
        page.addWidget(label('Your soundtrack, within reach.', 'heading'))
        page.addWidget(label('Live SimpMusic controls through Linux MPRIS or Windows system media sessions.', 'muted'))
        self.music_widget = MusicWidget()
        page.addWidget(self.music_widget)
        page.addStretch()

    def build_ai(self):
        page = self.page()
        page.addWidget(label('A little help from Hermes.', 'heading'))
        page.addWidget(label('Uses your local Hermes proxy and its configured provider. Content you send may leave this device. Suggestions do not automatically change tasks.', 'muted'))
        self.ai_prompt = QPlainTextEdit()
        self.ai_prompt.setPlaceholderText(tr('Ask for help breaking down a task or planning your next step…'))
        self.ai_prompt.setMaximumHeight(100)
        page.addWidget(self.ai_prompt)
        row = QHBoxLayout()
        self.ai_context = QCheckBox(tr("Include today's tasks and note"))
        row.addWidget(self.ai_context, 1)
        self.ai_send = button('Ask Hermes', self.ask_hermes, 'primary')
        row.addWidget(self.ai_send)
        page.addLayout(row)
        self.ai_answer = QPlainTextEdit()
        self.ai_answer.setReadOnly(True)
        page.addWidget(self.ai_answer, 1)
        page.addWidget(button('Selected answer text → task', self.ai_to_task))

    def ask_hermes(self):
        prompt = self.ai_prompt.toPlainText().strip()
        if not prompt:
            return
        context = None
        if self.ai_context.isChecked():
            context = {'tasks': self.store.tasks(date.today().isoformat(), include_done=False), 'note': self.store.note(date.today().isoformat())}
        self.ai_send.setEnabled(False)
        self.ai_answer.setPlainText(tr('Hermes is thinking…'))
        def ready(answer):
            self.ai_send.setEnabled(True)
            self.ai_answer.setPlainText(answer)
        def failed(message):
            self.ai_send.setEnabled(True)
            self.ai_answer.setPlainText(message)
        work = background(lambda: AIClient().chat(prompt, context), ready, failed)
        self.track_work(work)

    def ai_to_task(self):
        text = self.ai_answer.textCursor().selectedText().replace('\u2029', ' ').strip()
        if self.attempt(lambda: self.store.add_task(text, day=date.today().isoformat())):
            self.refresh_tasks()
            self.status.setText(tr('Selected suggestion added as a task.'))

    def configure_screenshot_watch(self):
        previous = self.clip_watcher.directories()
        if previous:
            self.clip_watcher.removePaths(previous)
        if not self.store.get_setting('screenshots_enabled', False):
            return
        root = Path(self.store.get_setting('screenshots_folder', str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)) / 'Screenshots')))
        if not root.is_dir():
            self.status.setText(tr('Choose an existing screenshot folder in Settings.'))
            return
        self._screenshot_seen = {str(path) for path in root.iterdir() if path.is_file()}
        self.clip_watcher.addPath(str(root))

    def scan_screenshots(self):
        if not self.store.get_setting('screenshots_enabled', False):
            return
        for folder in self.clip_watcher.directories():
            for path in Path(folder).iterdir():
                if str(path) in self._screenshot_seen or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                    continue
                if path.is_file() and path.stat().st_size <= 20 * 1024 * 1024 and not QImage(str(path)).isNull():
                    self._screenshot_seen.add(str(path))
                    self.screenshot_clips.insert(0, str(path))
                    self.screenshot_clips = self.screenshot_clips[:20]
                    if self.pages.currentIndex() == 6:
                        self.refresh_clips()

    def screenshot_rows(self):
        for path in self.screenshot_clips:
            row = QHBoxLayout()
            thumbnail = label('')
            thumbnail.setPixmap(QPixmap(path).scaled(84, 52, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            row.addWidget(thumbnail)
            row.addWidget(label(Path(path).name, 'muted', translate=False), 1)
            row.addWidget(button('Copy image', lambda checked=False, p=path: QApplication.clipboard().setImage(QImage(p))))
            row.addWidget(button('Open', lambda checked=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p))))
            self.clip_list.addLayout(row)

    def update_monitors(self):
        """Follow the pointer's monitor; the pill itself now lives in the GNOME top bar."""
        if not self.isVisible():
            self.target_screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def session_hook(self, phase):
        command = self.store.get_setting('hook_' + phase, [])
        if command:
            if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
                raise ValueError(tr('Session hooks must be JSON argument arrays'))
            subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def show_glance(self):
        self.glance.refresh()
        self.glance.show()

    def sync_now(self):
        if self._sync_busy or not self.store.get_setting('sync_endpoint'):
            return
        self._sync_busy = True
        def ready(result):
            self._sync_busy = False
            self.poll_services()
            self.status.setText(tr('Synchronized: {sent} outgoing and {received} incoming events.').format(**result))
        def failed(message):
            self._sync_busy = False
            self.status.setText(message)
        work = background(lambda: synchronize(self.store.path), ready, failed)
        self.track_work(work)

    def pairing_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Pair your devices'))
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('Choose one reachable LAN address for this hub. Pair Windows/Linux/Android using the protected JSON below. Never share it publicly. Enable only on a trusted network; do not port-forward it.', 'muted'))
        addresses = QComboBox()
        for address in QNetworkInterface.allAddresses():
            value = address.toString()
            if ':' not in value and not value.startswith('127.') and value != '0.0.0.0':
                addresses.addItem(value)
        if not addresses.count():
            addresses.addItem('127.0.0.1')
        layout.addWidget(addresses)
        code = QPlainTextEdit()
        code.setReadOnly(True)
        layout.addWidget(code)
        def start():
            def action():
                if self.hub:
                    self.hub.stop()
                self.hub = SyncHub(self.store.path, addresses.currentText())
                pairing = self.hub.start()
                self.store.set_setting('sync_hub_enabled', True)
                self.store.set_setting('sync_host', addresses.currentText())
                code.setPlainText(json.dumps(pairing, indent=2))
            self.attempt(action)
        layout.addWidget(button('Enable local hub and show pairing code', start, 'primary'))
        def stop():
            if self.hub:
                self.hub.stop()
                self.hub = None
            self.store.set_setting('sync_hub_enabled', False)
            code.clear()
        layout.addWidget(button('Stop hub', stop))
        peer = QPlainTextEdit()
        peer.setPlaceholderText(tr('Or paste a pairing code from your other desktop…'))
        peer.setMaximumHeight(110)
        layout.addWidget(peer)
        def connect():
            try:
                pairing = json.loads(peer.toPlainText())
            except ValueError as error:
                self.status.setText(str(error))
                return
            def action():
                store = Store(self.store.path)
                try:
                    pair_device(store, pairing)
                finally:
                    store.close()
            def ready(result):
                self.sync_timer.start()
                self.sync_now()
                dialog.accept()
            self.run_work(action, ready)
        layout.addWidget(button('Pair this desktop as a client', connect))
        dialog.resize(650, 560)
        dialog.exec()

    def mcp_connect(self, client):
        config = {'command': sys.executable, 'args': [str(ROOT / 'genta_cli.py'), 'mcp']}
        if client == 'copy':
            QApplication.clipboard().setText(json.dumps({'mcpServers': {'genta': config}}, indent=2))
            self.status.setText(tr('MCP configuration copied.'))
            return
        path = Path.home() / '.cursor/mcp.json' if client == 'cursor' else Path(os.environ.get('APPDATA', Path.home() / '.config')) / 'Claude/claude_desktop_config.json'
        def save():
            data = json.loads(path.read_text()) if path.exists() else {}
            servers = data.setdefault('mcpServers', {})
            if 'genta' in servers and servers['genta'] != config:
                raise ValueError(tr('An existing Genta MCP entry differs. Use Copy MCP config to review it manually.'))
            servers['genta'] = config
            path.parent.mkdir(parents=True, exist_ok=True)
            target = path.with_suffix(path.suffix + '.genta-new')
            target.write_text(json.dumps(data, indent=2), encoding='utf-8')
            target.chmod(0o600)
            target.replace(path)
            self.status.setText(tr('MCP connected. Restart the client to load Genta.'))
        if QMessageBox.question(self, tr('Connect MCP'), tr('Add Genta to this client’s MCP configuration? Existing servers are preserved.')) == QMessageBox.StandardButton.Yes:
            self.attempt(save)

    def build_settings(self):
        page = self.page()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setSpacing(12)
        scroll.setWidget(host)
        page.addWidget(scroll)
        layout.addWidget(label('Make yourself at home.', 'heading'))
        layout.addWidget(label('Genta · Windows, Linux and native Android. Local planner, optional account connectors. Original dot-matrix interface; no proprietary brand assets.', 'muted'))
        top = QHBoxLayout()
        top.addWidget(button('Desktop widget', self.show_glance))
        top.addWidget(button('Pair Android / desktop', self.pairing_dialog))
        top.addWidget(button('Sync now', self.sync_now))
        top.addWidget(button('Android APK folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / 'dist')))))
        layout.addLayout(top)
        form = QFormLayout()
        language = QComboBox()
        for code, name in LANGUAGES.items():
            language.addItem(name, code)
        language.setCurrentIndex(max(0, language.findData(self.store.get_setting('language', 'en'))))
        def language_changed():
            self.store.set_setting('language', language.currentData())
            self.status.setText(tr('Restart Genta to apply the language.'))
        language.currentIndexChanged.connect(language_changed)
        form.addRow(tr('Language'), language)
        end_hour = QSpinBox()
        end_hour.setRange(0, 23)
        end_hour.setValue(self.store.get_setting('day_end_hour', 18))
        end_hour.valueChanged.connect(lambda value: self.store.set_setting('day_end_hour', value))
        form.addRow(tr('Plan day ends at hour'), end_hour)
        nudge = QCheckBox(tr('Nudge when distracting apps are active'))
        nudge.setChecked(self.store.get_setting('nudge_enabled', False))
        nudge.toggled.connect(lambda value: self.store.set_setting('nudge_enabled', value))
        form.addRow(nudge)
        apps = QLineEdit(self.store.get_setting('distracting_apps', ''))
        apps.setPlaceholderText(tr('Comma-separated application names, e.g. browser, discord'))
        apps.editingFinished.connect(lambda: self.store.set_setting('distracting_apps', apps.text()))
        form.addRow(tr('Distracting apps'), apps)
        for phase, title in [('start', 'Session start command'), ('end', 'Session end command')]:
            command = QLineEdit(json.dumps(self.store.get_setting('hook_' + phase, [])))
            command.setPlaceholderText('["program", "argument"]')
            def save_hook(p=phase, edit=command):
                def action():
                    value = json.loads(edit.text())
                    if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
                        raise ValueError(tr('Use a JSON array of command arguments; no shell is invoked.'))
                    self.store.set_setting('hook_' + p, value)
                self.attempt(action)
            command.editingFinished.connect(save_hook)
            form.addRow(tr(title), command)
        origin = QLineEdit(self.store.get_setting('maps_origin', ''))
        origin.editingFinished.connect(lambda: self.store.set_setting('maps_origin', origin.text()))
        form.addRow(tr('Maps starting address'), origin)
        route_key = QLineEdit()
        route_key.setEchoMode(QLineEdit.EchoMode.Password)
        route_key.setPlaceholderText(tr('New Google Routes key; blank keeps the saved key'))
        route_key.editingFinished.connect(lambda: self.attempt(lambda: set_secret('google_routes', route_key.text())) if route_key.text() else None)
        form.addRow(tr('Google Routes API key'), route_key)
        layout.addLayout(form)
        layout.addWidget(label('Maps receives your starting/destination addresses only when you click Time to leave. Calendar tokens and pairing secrets use the OS keyring. App nudges read app identity only, not typed contents.', 'muted'))
        row = QHBoxLayout()
        row.addWidget(button('Connect calendars', self.configure_calendars))
        row.addWidget(button('Connect Claude', lambda: self.mcp_connect('claude')))
        row.addWidget(button('Connect Cursor', lambda: self.mcp_connect('cursor')))
        row.addWidget(button('Copy MCP config', lambda: self.mcp_connect('copy')))
        layout.addLayout(row)
        capture = QCheckBox(tr('Capture new screenshots from my screenshot folder'))
        capture.setChecked(self.store.get_setting('screenshots_enabled', False))
        capture.toggled.connect(lambda value: (self.store.set_setting('screenshots_enabled', value), self.configure_screenshot_watch()))
        layout.addWidget(capture)
        def choose_folder():
            folder = QFileDialog.getExistingDirectory(self, tr('Screenshot folder'))
            if folder:
                self.store.set_setting('screenshots_folder', folder)
                self.configure_screenshot_watch()
        layout.addWidget(button('Choose screenshot folder', choose_folder))
        self.auto_start = QCheckBox(tr('Start Genta when I sign in'))
        self.auto_start.setChecked(self.autostart_path().exists())
        self.auto_start.toggled.connect(self.set_autostart)
        layout.addWidget(self.auto_start)
        row = QHBoxLayout()
        row.addWidget(button('Export local data…', self.export_data))
        row.addWidget(button('Open data folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.path.parent)))))
        row.addWidget(button('Quit Genta', self.quit))
        layout.addLayout(row)
        layout.addWidget(label('Artcraft logo/favicon/sprite generation is paused by your choice; no paid renders were submitted. Clipboard and screenshot capture are opt-in. Camera and microphone start only after your explicit action.', 'muted'))
        layout.addStretch()

    @staticmethod
    def autostart_path():
        if os.name == 'nt':
            return Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup/Genta.cmd'
        return Path.home() / '.config/autostart/genta.desktop'

    def set_autostart(self, enabled):
        target = self.autostart_path()
        if enabled:
            target.parent.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                target.write_text('@echo off\r\nstart "" "' + sys.executable.replace('python.exe', 'pythonw.exe') + '" "' + str(ROOT / 'genta.py') + '"\r\n')
            else:
                from genta import desktop_entry
                target.write_text(desktop_entry(background=True), encoding='utf-8')
        else:
            target.unlink(missing_ok=True)
