"""Read-only calendar/booking connectors and opt-in Google Routes travel estimates."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import urlparse

import requests

# icalendar, recurring_ical_events and keyring are imported where used (they cost ~12 MiB at startup).

SERVICE = 'Genta'
MAX_BYTES = 10 * 1024 * 1024


def set_secret(name, value):
    import keyring
    if value:
        keyring.set_password(SERVICE, name, value)
    else:
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass


def secret(name):
    import keyring
    return keyring.get_password(SERVICE, name) or ''


def https_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use an HTTPS URL without embedded credentials')
    return value


def download(url, headers=None, params=None):
    https_url(url)
    with requests.get(url, headers=headers, params=params, timeout=(8, 30), stream=True) as response:
        response.raise_for_status()
        result = bytearray()
        for chunk in response.iter_content(65536):
            result.extend(chunk)
            if len(result) > MAX_BYTES:
                raise ValueError('Calendar response exceeds 10 MiB')
        return bytes(result)


def local_iso(value):
    if isinstance(value, datetime):
        return (value.astimezone().replace(tzinfo=None) if value.tzinfo else value).isoformat(timespec='seconds')
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return local_iso(parsed)


def meeting_url(*values):
    for value in values:
        for match in re.findall(r'https://[^\s<>"\]]+', str(value or '')):
            match = match.rstrip('.,;)')
            host = (urlparse(match).hostname or '').lower()
            if any(host == domain or host.endswith('.' + domain) for domain in ('zoom.us', 'meet.google.com', 'teams.microsoft.com', 'teams.live.com', 'webex.com', 'whereby.com', 'cal.com')):
                return match
    return ''


def normalize_event(source, uid, title, start, end, location='', url='', description=''):
    start, end = local_iso(start), local_iso(end)
    return {
        'id': hashlib.sha256(f'{source}:{uid}:{start}'.encode()).hexdigest(),
        'source': source, 'title': str(title or 'Untitled event'), 'start': start, 'end': end,
        'location': str(location or ''), 'join_url': meeting_url(url, location, description),
    }


def from_ics(content, source, start=None):
    start = start or datetime.now().astimezone()
    end = start + timedelta(days=14)
    import icalendar
    import recurring_ical_events
    calendar = icalendar.Calendar.from_ical(content)
    items = recurring_ical_events.of(calendar).between(start, end)
    if len(items) > 10000:
        raise ValueError('Calendar contains more than 10,000 occurrences in two weeks')
    result = []
    for event in items:
        if str(event.get('STATUS', '')).upper() == 'CANCELLED' or not event.get('DTSTART'):
            continue
        begins = event.decoded('DTSTART')
        default_duration = timedelta(days=1) if isinstance(begins, date) and not isinstance(begins, datetime) else timedelta(hours=1)
        finishes = event.decoded('DTEND') if event.get('DTEND') else begins + event.decoded('DURATION', default_duration)
        result.append(normalize_event(source, event.get('UID', ''), event.get('SUMMARY'), begins, finishes, event.get('LOCATION'), event.get('URL'), event.get('DESCRIPTION')))
    return result


class Calendars:
    def __init__(self, store):
        self.store = store
        with store.conn:
            store.conn.execute('CREATE TABLE IF NOT EXISTS calendar_events(id TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL)')
            store.conn.execute('CREATE TABLE IF NOT EXISTS calendar_status(source TEXT PRIMARY KEY, updated_at TEXT, error TEXT)')

    def events(self):
        return sorted([json.loads(row[0]) for row in self.store.conn.execute('SELECT payload FROM calendar_events')], key=lambda event: event['start'])

    def refresh(self):
        errors = []
        sources = self.store.get_setting('calendar_feeds', [])
        for feed in sources:
            name = feed['name']
            try:
                self._replace(name, from_ics(download(secret('ics:' + feed['id'])), name))
            except (ValueError, requests.RequestException) as exc:
                errors.append(self._failed(name, exc))
        for provider, callback in [('Calendly', self.calendly), ('Cal.com', self.calcom)]:
            if self.store.get_setting('calendar_' + provider, False):
                try:
                    self._replace(provider, callback())
                except (ValueError, KeyError, requests.RequestException) as exc:
                    errors.append(self._failed(provider, exc))
        return {'events': self.events(), 'errors': errors}

    def _replace(self, source, events):
        with self.store.conn:
            self.store.conn.execute('DELETE FROM calendar_events WHERE source=?', (source,))
            self.store.conn.executemany('INSERT OR REPLACE INTO calendar_events VALUES (?,?,?)', [(e['id'], source, json.dumps(e)) for e in events])
            self.store.conn.execute('INSERT INTO calendar_status VALUES (?,?,NULL) ON CONFLICT(source) DO UPDATE SET updated_at=excluded.updated_at,error=NULL', (source, datetime.now().isoformat(timespec='seconds')))

    def _failed(self, source, error):
        # Do not persist request URLs (private ICS URLs may contain secret tokens).
        message = f'{source}: {type(error).__name__}. Check the configured feed/token. Cached events may be stale.'
        with self.store.conn:
            self.store.conn.execute('INSERT INTO calendar_status(source,error) VALUES (?,?) ON CONFLICT(source) DO UPDATE SET error=excluded.error', (source, message))
        return message

    def statuses(self):
        return [dict(row) for row in self.store.conn.execute('SELECT * FROM calendar_status')]

    def calendly(self):
        token = secret('calendly')
        if not token:
            raise ValueError('Configure a Calendly personal access token')
        headers = {'Authorization': 'Bearer ' + token}
        user = json.loads(download('https://api.calendly.com/users/me', headers))['resource']['uri']
        now = datetime.now(timezone.utc)
        params = {'user': user, 'min_start_time': now.isoformat(), 'max_start_time': (now + timedelta(days=14)).isoformat(), 'status': 'active', 'count': 100}
        url = 'https://api.calendly.com/scheduled_events'
        events = []
        for _ in range(20):
            data = json.loads(download(url, headers, params))
            for event in data['collection']:
                location = event.get('location') or {}
                events.append(normalize_event('Calendly', event['uri'], event.get('name'), event['start_time'], event['end_time'], location.get('location', ''), location.get('join_url', '')))
            next_page = data.get('pagination', {}).get('next_page')
            if not next_page:
                return events
            if urlparse(next_page).hostname != 'api.calendly.com':
                raise ValueError('Calendly returned an unexpected pagination host')
            url, params = next_page, None
        raise ValueError('Calendly exceeded 20 pages')

    def calcom(self):
        token = secret('calcom')
        if not token:
            raise ValueError('Configure a Cal.com API key')
        headers = {'Authorization': 'Bearer ' + token, 'cal-api-version': '2024-08-13'}
        events = []
        for skip in range(0, 2000, 100):
            data = json.loads(download('https://api.cal.com/v2/bookings', headers, {'status': 'upcoming', 'take': 100, 'skip': skip}))
            rows = data.get('data', [])
            if isinstance(rows, dict):
                rows = rows.get('bookings', [])
            if not isinstance(rows, list):
                raise ValueError('Cal.com returned an invalid booking collection')
            for event in rows:
                if datetime.fromisoformat(event['start'].replace('Z', '+00:00')) > datetime.now(timezone.utc) + timedelta(days=14):
                    continue
                location = event.get('location', '')
                events.append(normalize_event('Cal.com', event.get('uid', event.get('id')), event.get('title'), event['start'], event['end'], location, event.get('meetingUrl', '')))
            if len(rows) < 100:
                return events
        raise ValueError('Cal.com exceeded 20 pages')


def travel_time(origin, destination):
    """Seconds from live Google Routes; addresses leave the device only on request."""
    key = secret('google_routes')
    if not key:
        raise ValueError('Set a Google Routes API key before requesting travel time')
    headers = {'Content-Type': 'application/json', 'X-Goog-Api-Key': key, 'X-Goog-FieldMask': 'routes.duration,routes.distanceMeters'}
    body = {'origin': {'address': origin}, 'destination': {'address': destination}, 'travelMode': 'DRIVE', 'routingPreference': 'TRAFFIC_AWARE', 'departureTime': (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}
    response = requests.post('https://routes.googleapis.com/directions/v2:computeRoutes', json=body, headers=headers, timeout=(8, 30))
    response.raise_for_status()
    routes = response.json().get('routes', [])
    if not routes:
        raise ValueError('Maps did not find a driving route')
    seconds = float(routes[0]['duration'].removesuffix('s'))
    if seconds <= 0:
        raise ValueError('Maps returned an invalid travel time')
    return {'seconds': seconds, 'meters': routes[0].get('distanceMeters'), 'checked_at': datetime.now().isoformat(timespec='seconds')}
