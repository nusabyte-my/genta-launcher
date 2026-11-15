"""Installed applications: XDG .desktop discovery, fuzzy ranking and frecency."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

# Genta forces XWayland for itself; launched apps must use the session default.
UNSET_FOR_CHILDREN = ("QT_QPA_PLATFORM",)
HALF_LIFE = 14 * 86400
MAX_USAGE_ENTRIES = 300
_WORD = re.compile(r"[\W_]+")


def application_dirs():
    """Directories in XDG precedence order: user data first, then system data."""
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    ordered = []
    for base in [home, *(Path(part) for part in system.split(":") if part)]:
        folder = base / "applications"
        if folder not in ordered:
            ordered.append(folder)
    return ordered


@dataclass(frozen=True)
class App:
    id: str
    path: str
    name: str
    generic: str
    keywords: tuple
    executable: str
    comment: str
    icon_names: tuple
    icon_path: str


def _icon(info):
    icon = info.get_icon()
    if isinstance(icon, Gio.ThemedIcon):
        return tuple(icon.get_names()), ""
    if isinstance(icon, Gio.FileIcon):
        return (), icon.get_file().get_path() or ""
    return (), ""


def _load(path):
    """PyGObject raises TypeError when the constructor returns NULL."""
    try:
        return Gio.DesktopAppInfo.new_from_filename(str(path))
    except TypeError:
        return None


def scan(dirs=None):
    """Return visible apps. A higher-precedence entry masks lower ones, even when hidden."""
    seen, apps = set(), []
    for folder in dirs or application_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.desktop")):
            desktop_id = path.relative_to(folder).as_posix().replace("/", "-")
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            info = _load(path)
            # None covers Hidden=true, a missing TryExec binary, and invalid files.
            if info is None or not info.should_show():
                continue
            names, icon_path = _icon(info)
            apps.append(App(
                id=desktop_id,
                path=str(path),
                name=info.get_display_name() or info.get_name() or desktop_id,
                generic=info.get_generic_name() or "",
                keywords=tuple(info.get_keywords() or ()),
                executable=Path(info.get_executable() or "").name,
                comment=info.get_description() or "",
                icon_names=names,
                icon_path=icon_path,
            ))
    return apps


def match_score(query, text):
    """Score 0–100 for how well an already case-folded query matches text."""
    if not text:
        return 0
    value = text.casefold()
    if value == query:
        return 100
    if value.startswith(query):
        return 90
    index = value.find(query)
    if index > 0 and not value[index - 1].isalnum():
        return 80
    words = [word for word in _WORD.split(value) if word]
    if len(query) > 1 and "".join(word[0] for word in words).startswith(query):
        return 75
    if index >= 0:
        return 60
    position, gaps = -1, 0
    for char in query:
        found = value.find(char, position + 1)
        if found < 0:
            return 0
        gaps += found - position - 1
        position = found
    return max(1, 40 - gaps)


def typo_distance(a, b, limit):
    """Damerau–Levenshtein distance (adjacent swaps count as 1), stopping early past limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2, previous = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if previous2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def typo_score(query, text):
    """Small score when a 4+ letter query is a near-miss of a word (frifox → Firefox)."""
    if len(query) < 4 or not text:
        return 0
    limit = 1 if len(query) <= 4 else 2
    best = limit + 1
    for word in [w for w in _WORD.split(text.casefold()) if w]:
        best = min(best, typo_distance(query, word[:len(query) + limit], limit), typo_distance(query, word[:len(query)], limit))
    return 30 - 8 * best if best <= limit else 0


def app_score(app, query):
    best = match_score(query, app.name)
    if best < 100:
        best = max(best, match_score(query, app.generic) * 0.8, match_score(query, app.executable) * 0.7,
                   *(match_score(query, keyword) * 0.75 for keyword in app.keywords))
    if best == 0:
        best = max(typo_score(query, app.name), typo_score(query, app.generic) * 0.8, typo_score(query, app.executable) * 0.8)
    return best


class Usage:
    """Exponentially decaying launch counts, stored as {desktop_id: [count, last_epoch]}."""

    def __init__(self, data=None):
        self.data = {}
        for key, value in (data or {}).items():
            if isinstance(key, str) and isinstance(value, list) and len(value) == 2 and all(isinstance(x, (int, float)) for x in value):
                self.data[key] = [float(value[0]), float(value[1])]

    def weight(self, app_id, now=None):
        entry = self.data.get(app_id)
        if not entry:
            return 0.0
        now = time.time() if now is None else now
        return entry[0] * 0.5 ** (max(0.0, now - entry[1]) / HALF_LIFE)

    def record(self, app_id, now=None):
        now = time.time() if now is None else now
        self.data[app_id] = [self.weight(app_id, now) + 1.0, now]
        if len(self.data) > MAX_USAGE_ENTRIES:
            for key in sorted(self.data, key=lambda k: self.weight(k, now))[:len(self.data) - MAX_USAGE_ENTRIES]:
                del self.data[key]


def rank(apps, query, usage):
    query = query.strip().casefold()
    now = time.time()
    if not query:
        return sorted(apps, key=lambda app: (-usage.weight(app.id, now), app.name.casefold()))
    scored = []
    for app in apps:
        score = app_score(app, query)
        if score > 0:
            scored.append((score + min(12.0, 3.0 * usage.weight(app.id, now)), app))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name.casefold()))
    return [app for _, app in scored]


def launch(app):
    info = _load(app.path)
    if info is None:
        raise OSError(f"{app.name} is no longer installed")
    context = Gio.AppLaunchContext()
    for name in UNSET_FOR_CHILDREN:
        context.unsetenv(name)
    try:
        info.launch([], context)
    except GLib.Error as error:
        raise OSError(f"Could not open {app.name}: {error.message}") from error
