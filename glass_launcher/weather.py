"""Current weather from Open-Meteo (free, no API key). Only latitude/longitude are sent.

Location: setting ``weather_location`` = {"name", "lat", "lon"}; defaults to Kuala Lumpur
(matches this machine's Asia/Kuala_Lumpur timezone). Change it with ``?weather <city>``.
"""
from __future__ import annotations

import requests
from PySide6.QtCore import QObject, QTimer, Signal

DEFAULT = {"name": "Kuala Lumpur", "lat": 3.139, "lon": 101.6869}
REFRESH_MS = 20 * 60 * 1000

# WMO weather codes → (symbol, short description). Monochrome symbols render in panel fonts.
CODES = {
    0: ("☀", "Clear"), 1: ("☀", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁", "Cloudy"),
    45: ("≋", "Fog"), 48: ("≋", "Fog"),
    51: ("☂", "Drizzle"), 53: ("☂", "Drizzle"), 55: ("☂", "Drizzle"),
    61: ("☂", "Rain"), 63: ("☂", "Rain"), 65: ("☂", "Heavy rain"),
    66: ("☂", "Freezing rain"), 67: ("☂", "Freezing rain"),
    71: ("❄", "Snow"), 73: ("❄", "Snow"), 75: ("❄", "Heavy snow"), 77: ("❄", "Snow"),
    80: ("☂", "Showers"), 81: ("☂", "Showers"), 82: ("☂", "Heavy showers"),
    85: ("❄", "Snow showers"), 86: ("❄", "Snow showers"),
    95: ("⚡", "Thunderstorm"), 96: ("⚡", "Thunderstorm"), 99: ("⚡", "Thunderstorm"),
}


def fetch(location):
    response = requests.get("https://api.open-meteo.com/v1/forecast", timeout=(5, 15), params={
        "latitude": location["lat"], "longitude": location["lon"],
        "current": "temperature_2m,apparent_temperature,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 1, "timezone": "auto"})
    response.raise_for_status()
    data = response.json()
    current, daily = data["current"], data.get("daily", {})
    code = int(current.get("weather_code", 0))
    symbol, words = CODES.get(code, ("·", "—"))
    if symbol == "☀" and not current.get("is_day", 1):
        symbol = "☾"
    first = lambda key: (daily.get(key) or [None])[0]
    return {"temp": round(float(current["temperature_2m"]), 1), "feels": current.get("apparent_temperature"),
            "symbol": symbol, "words": words, "high": first("temperature_2m_max"), "low": first("temperature_2m_min"),
            "rain": first("precipitation_probability_max"), "place": location["name"]}


def geocode(city):
    response = requests.get("https://geocoding-api.open-meteo.com/v1/search", timeout=(5, 15),
                            params={"name": city, "count": 1})
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        raise ValueError(f"No place called “{city}” found")
    place = results[0]
    name = ", ".join(x for x in (place.get("name"), place.get("country_code")) if x)
    return {"name": name, "lat": place["latitude"], "lon": place["longitude"]}


class WeatherService(QObject):
    changed = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.store = workspace.store
        self.current = None
        self.error = ""
        self._work = None
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    def location(self):
        value = self.store.get_setting("weather_location", None)
        return value if isinstance(value, dict) and {"lat", "lon", "name"} <= value.keys() else DEFAULT

    def start(self):
        if self.store.get_setting("weather_enabled", True):
            self.refresh()
            self.timer.start()

    def refresh(self):
        from desktop_services import background
        location = self.location()

        def done(result):
            self.current, self.error = result, ""
            self.workspace.weather_status = {"temp": result["temp"], "symbol": result["symbol"]}
            self.workspace.publish_status()
            self.changed.emit()

        def failed(message):
            self.error = str(message)
            self.changed.emit()

        self._work = background(lambda: fetch(location), done, failed)

    def set_city(self, city, finished):
        from desktop_services import background

        def done(location):
            self.store.set_setting("weather_location", location)
            self.refresh()
            finished(f"Weather location set to {location['name']}.")

        self._work = background(lambda: geocode(city), done, lambda message: finished(str(message)))
