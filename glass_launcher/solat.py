"""Malaysian prayer times (waktu solat) with notifications.

Source: JAKIM e-Solat (official, www.e-solat.gov.my) by zone code, e.g. WLY01.
Fallback and zone lookup: api.waktusolat.app (open data mirrored from JAKIM).
Zone: setting ``solat_zone`` = "auto" (from the weather/location coordinates) or a JAKIM code.
Only the zone code, or coordinates for auto-detect, leave the machine.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json

import requests
from PySide6.QtCore import QObject, QTimer, Signal

JAKIM = "https://www.e-solat.gov.my/index.php"
MIRROR = "https://api.waktusolat.app"
PRAYERS = [("imsak", "Imsak"), ("fajr", "Subuh"), ("syuruk", "Syuruk"), ("dhuhr", "Zohor"),
           ("asr", "Asar"), ("maghrib", "Maghrib"), ("isha", "Isyak")]
NOTIFY = {"fajr", "dhuhr", "asr", "maghrib", "isha"}       # the five daily prayers
DEFAULT_ZONE = "WLY01"


def fetch_day(zone, day=None):
    """{key: datetime} for one day. JAKIM first, mirror on failure."""
    day = day or date.today()
    try:
        response = requests.get(JAKIM, timeout=(5, 15), params={
            "r": "esolatApi/TakwimSolat", "period": "date" if day != date.today() else "today", "zone": zone,
            **({"date": day.isoformat()} if day != date.today() else {})})
        response.raise_for_status()
        entry = response.json()["prayerTime"][0]
        return {key: datetime.combine(day, datetime.strptime(entry[key], "%H:%M:%S").time()) for key, _ in PRAYERS}, "JAKIM e-Solat"
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
        pass
    response = requests.get(f"{MIRROR}/v2/solat/{zone}", timeout=(5, 15), params={"year": day.year, "month": day.month})
    response.raise_for_status()
    entry = next(p for p in response.json()["prayers"] if int(p["day"]) == day.day)
    return {key: datetime.fromtimestamp(int(entry[key])) for key, _ in PRAYERS}, "waktusolat.app"


def zone_for(lat, lon):
    response = requests.get(f"{MIRROR}/zones/{lat}/{lon}", timeout=(5, 15))
    response.raise_for_status()
    data = response.json()
    return data["zone"], data.get("district") or data["zone"]


def zones():
    response = requests.get(f"{MIRROR}/zones", timeout=(5, 20))
    response.raise_for_status()
    return [{"code": z["jakimCode"], "state": z["negeri"], "district": z["daerah"]} for z in response.json()]


class SolatService(QObject):
    changed = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.store = workspace.store
        self.times = {}
        self.day = None
        self.source = ""
        self.zone = ""
        self.place = ""
        self.error = ""
        self.zone_list = self.store.get_setting("solat_zones_cache", []) or []
        self._notified = set()
        self._work = []
        self.timer = QTimer(self)
        self.timer.setInterval(20_000)
        self.timer.timeout.connect(self.tick)

    # ── settings ───────────────────────────────────────────────
    def enabled(self):
        return bool(self.store.get_setting("solat_enabled", True))

    def remind_minutes(self):
        return int(self.store.get_setting("solat_remind_minutes", 10) or 0)

    def set_zone(self, code, name=None):
        self.store.set_setting("solat_zone", code)
        self.store.set_setting("solat_zone_name", name or next((z["district"] for z in self.zone_list if z["code"] == code), code))
        self.day = None
        self.refresh()

    # ── lifecycle ──────────────────────────────────────────────
    def start(self):
        if self.enabled():
            self.refresh()
            self.timer.start()

    def run(self, action, done, failed=None):
        from desktop_services import background
        work = background(action, done, failed or self._failed)
        self._work = [w for w in self._work if w is not None][-4:] + [work]

    def _failed(self, message):
        self.error = str(message)
        self.changed.emit()

    def refresh(self):
        setting = self.store.get_setting("solat_zone", "auto") or "auto"
        if setting == "auto":
            weather = getattr(self.workspace, "weather", None)
            location = weather.location() if weather else {"lat": 3.139, "lon": 101.6869}

            def detect():
                try:
                    return zone_for(location["lat"], location["lon"])
                except Exception:
                    return DEFAULT_ZONE, "Kuala Lumpur"

            self.run(detect, lambda result: self._load(*result))
        else:
            name = self.store.get_setting("solat_zone_name", None) or setting
            self._load(setting, name)

    def _load(self, zone, place):
        self.zone, self.place = zone, place
        today = date.today()
        self.run(lambda: fetch_day(zone, today), lambda result: self._loaded(today, *result))

    def _loaded(self, day, times, source):
        self.times, self.day, self.source, self.error = times, day, source, ""
        self.changed.emit()
        self.tick()

    def load_zone_list(self, finished):
        def done(result):
            self.zone_list = result
            self.store.set_setting("solat_zones_cache", result)
            finished()

        self.run(zones, done)

    # ── timing ─────────────────────────────────────────────────
    def upcoming(self, now=None):
        """(key, name, datetime) of the next of the five prayers today (or None after Isyak)."""
        now = now or datetime.now()
        for key, name in PRAYERS:
            when = self.times.get(key)
            if key in NOTIFY and when and when > now:
                return key, name, when
        return None

    def current(self, now=None):
        now = now or datetime.now()
        past = [(key, name, when) for key, name in PRAYERS if key in NOTIFY and (when := self.times.get(key)) and when <= now]
        return past[-1] if past else None

    def tick(self):
        if not self.enabled():
            return
        now = datetime.now()
        if self.day != date.today() and self.day is not None:
            self.day = None
            self._notified.clear()
            self.refresh()
            return
        status = None
        nxt = self.upcoming(now)
        if nxt:
            status = {"name": nxt[1], "time": nxt[2].strftime("%H:%M")}
        if getattr(self.workspace, "solat_status", None) != status:
            self.workspace.solat_status = status
            self.workspace.publish_status()
        lead = self.remind_minutes()
        for key, name in PRAYERS:
            when = self.times.get(key)
            if key not in NOTIFY or not when:
                continue
            if lead and timedelta(0) < when - now <= timedelta(minutes=lead) and (key, "soon") not in self._notified:
                self._notified.add((key, "soon"))
                self.workspace.notify(f"{name} in {int((when - now).total_seconds() // 60) + 1} min",
                                      f"{when:%H:%M} · {self.place}. Time to wrap up.")
            if timedelta(0) <= now - when < timedelta(minutes=5) and (key, "now") not in self._notified:
                self._notified.add((key, "now"))
                self.workspace.notify(f"Waktu {name}", f"It is now {name} ({when:%H:%M}) · {self.place}")
                if self.store.get_setting("solat_pause_focus", True) and self.workspace.clock.running:
                    self.workspace.toggle_focus()
        self.changed.emit()
