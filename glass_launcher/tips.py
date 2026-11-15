"""Agentic tips: free local rules plus an occasional Hermes suggestion.

Signals used (all local): focused app id and time on it, Genta focus state, today's
task counts, the next calendar event, battery. For AI tips only a short summary is
sent (app ids, minutes, counts, focus state); never window titles or task text,
unless the user turns on ``launcher_ai_share_titles``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from . import ai
from .desktop_watch import DesktopWatch, battery

CHECK_MS = 30_000
AI_INTERVAL = 15 * 60
SNOOZE = 45 * 60
MUTED_KEY = "launcher_tips_muted"


@dataclass
class Tip:
    id: str            # rule id; muting is per rule
    text: str
    source: str = "Genta"
    action_label: str = ""
    action: Optional[Callable[[], None]] = field(default=None, compare=False, repr=False)


def pretty_app(app_id):
    name = app_id.rsplit(".", 1)[-1] if "." in app_id else app_id
    return {"CosmicTerm": "Terminal", "CosmicFiles": "Files", "CosmicEdit": "Text Editor"}.get(name, name) or "an app"


class TipEngine(QObject):
    changed = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.store = workspace.store
        self.watch = DesktopWatch(self)
        self.tips: list[Tip] = []
        self.ai_tip: Optional[Tip] = None
        self.ai_status = ""
        self._ai_last = 0.0
        self._ai_busy = False
        self._ai_work = None
        self._snoozed = {}
        self._distraction_seen = {}
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_MS)
        self.timer.timeout.connect(self.evaluate)
        self.watch.changed.connect(self._watch_changed)

    def start(self):
        self.watch.start()
        self.timer.start()
        QTimer.singleShot(4000, self.evaluate)

    def stop(self):
        self.timer.stop()
        self.watch.stop()

    # ── user feedback ─────────────────────────────────────────
    def muted(self):
        value = self.store.get_setting(MUTED_KEY, [])
        return set(value) if isinstance(value, list) else set()

    def snooze(self, tip):
        self._snoozed[tip.id] = time.monotonic() + SNOOZE
        self.evaluate()

    def mute(self, tip):
        self.store.set_setting(MUTED_KEY, sorted(self.muted() | {tip.id.split(":")[0]}))  # mute the rule, not one instance
        self.evaluate()

    def unmute_all(self):
        self.store.set_setting(MUTED_KEY, [])
        self.evaluate()

    def run(self, tip):
        if tip.action:
            tip.action()
        self.snooze(tip)

    # ── rules ─────────────────────────────────────────────────
    def _watch_changed(self):
        # React quickly to distraction during focus, otherwise wait for the timer.
        if self.workspace.clock.running:
            self.evaluate()

    def evaluate(self):
        tips = []
        now = time.monotonic()
        workspace, clock, watch = self.workspace, self.workspace.clock, self.watch
        app, minutes = watch.active_app, watch.minutes_on_active()

        distracting = [x.strip().casefold() for x in str(self.store.get_setting("distracting_apps", "")).split(",") if x.strip()]
        if clock.running and app and any(word in app.casefold() for word in distracting) and minutes >= 1:
            tips.append(Tip("distraction", f"You're in {pretty_app(app)} during a focus session. Back to it?"))

        event = self._next_event(minutes=12)
        if event:
            start = event["start"][11:16] if len(event["start"]) >= 16 else ""
            url = event.get("join_url") or ""
            tips.append(Tip(f"meeting:{event.get('id', event['start'])}",
                            f"“{event.get('title') or 'Meeting'}” starts at {start}. Wrap up and get ready.",
                            action_label="Join" if url else "", action=(lambda u=url: QDesktopServices.openUrl(QUrl(u))) if url else None))

        solat = getattr(workspace, "solat", None)
        upcoming = solat.upcoming() if solat is not None and solat.times else None
        if upcoming and 0 < (upcoming[2] - datetime.now()).total_seconds() <= 15 * 60:
            label_text = f"{upcoming[1]} at {upcoming[2]:%H:%M}. Finish this step and get ready."
            tips.append(Tip(f"solat:{upcoming[0]}:{date.today()}", label_text,
                            action_label="Pause focus" if clock.running else "",
                            action=workspace.toggle_focus if clock.running else None))

        weather = getattr(getattr(workspace, "weather", None), "current", None)
        if weather and (weather.get("rain") or 0) >= 80 and datetime.now().hour < 18:
            tips.append(Tip(f"rain:{date.today()}", f"Rain is likely today ({weather['rain']}%) in {weather['place']}. Take an umbrella if you head out."))

        power = battery()
        if power and power[1] and power[0] <= 20:
            remaining = clock.remaining() // 60 if clock.running and clock.duration else 0
            extra = f" Your focus session has {int(remaining)} min left." if remaining else ""
            tips.append(Tip("battery", f"Battery at {power[0]}%. Plug in soon.{extra}"))

        if app and minutes >= 60 and not clock.running and "genta" not in app.casefold():
            tips.append(Tip("long-session", f"{minutes} min in {pretty_app(app)} without a break. Stand up for 2 minutes?",
                            action_label="Start 5-min break", action=lambda: self._start_timer(5 * 60)))

        due, overdue = self._task_counts()
        if overdue:
            tips.append(Tip(f"overdue:{date.today()}", f"{overdue} overdue task{'s' if overdue > 1 else ''}. Move {'them' if overdue > 1 else 'it'} to today or tomorrow?",
                            action_label="Open Genta", action=self._open_workspace))
        focus_today = self._focus_today()
        if due and focus_today == 0 and datetime.now().hour >= 10 and not clock.running:
            first = due[0]
            tips.append(Tip(f"first-focus:{date.today()}", f"No focus time yet today. 25 minutes on “{first['title']}”?",
                            action_label="Start focus", action=lambda t=first: workspace.focus_task(t)))

        if self.ai_tip:
            tips.append(self.ai_tip)
        muted = self.muted()
        visible = [t for t in tips if t.id.split(":")[0] not in muted and self._snoozed.get(t.id, 0) < now]
        if [t.text for t in visible] != [t.text for t in self.tips]:
            self.tips = visible
            self.changed.emit()
            if hasattr(workspace, "set_tip_badge"):
                workspace.set_tip_badge(len(visible))
        self._maybe_ask_ai(app, minutes, due, overdue, focus_today, event)

    def _next_event(self, minutes):
        now = datetime.now()
        try:
            events = self.workspace.calendars.events()
        except Exception:
            return None
        soon = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        stamp = now.isoformat(timespec="seconds")
        return next((e for e in events if len(e.get("start", "")) >= 16 and stamp <= e["start"] <= soon), None)

    def _task_counts(self):
        today = date.today().isoformat()
        try:
            open_tasks = self.store.tasks(include_done=False)
        except Exception:
            return [], 0
        due = [t for t in open_tasks if t["day"] == today]
        overdue = sum(1 for t in open_tasks if t["day"] and t["day"] < today)
        return due, overdue

    def _focus_today(self):
        try:
            return int(self.store.insights().get("focus_today_seconds", 0))
        except Exception:
            return 0

    def _start_timer(self, seconds):
        clock = self.workspace.clock
        if clock.active_seconds():
            self.workspace.end_focus()
        clock.reset(duration=seconds)
        self.workspace.toggle_focus()

    def _open_workspace(self):
        self.workspace.expand()
        self.workspace.activateWindow()

    # ── AI tip (Hermes by default) ────────────────────────────
    def _maybe_ask_ai(self, app, minutes, due, overdue, focus_today, event):
        if not self.store.get_setting("launcher_ai_tips", True) or self._ai_busy:
            return
        if time.monotonic() - self._ai_last < AI_INTERVAL or not self.watch.available():
            return
        provider = self.store.get_setting("launcher_tip_provider", "hermes")
        if provider not in ai.PROVIDERS or provider == "codex":
            provider = "hermes"
        clock = self.workspace.clock
        lines = [
            f"Local time: {datetime.now():%A %H:%M}",
            f"Focused app: {app or 'none'} for {minutes} min",
            f"Open apps: {', '.join(sorted({w.get('app_id', '') for w in self.watch.windows if w.get('app_id')})[:12])}",
            f"Focus timer: {'running' if clock.running else 'idle'}; focused today: {focus_today // 60} min",
            f"Tasks due today: {len(due)}; overdue: {overdue}",
            f"Meeting within 12 min: {'yes' if event else 'no'}",
        ]
        if self.store.get_setting("launcher_ai_share_titles", False):
            lines.append(f"Focused window title: {self.watch.active_title[:80]}")
        prompt = ("Based on this snapshot of my desktop, give ONE short, specific, practical tip (max 25 words) "
                  "to help me work better right now. If nothing is useful, reply exactly: NONE")
        self._ai_busy = True
        self._ai_last = time.monotonic()

        def ask():
            return ai.chat(self.store, provider, prompt, context="\n".join(lines), max_tokens=120)

        def done(text):
            self._ai_busy = False
            text = text.strip().strip('"')
            self.ai_status = ""
            self.ai_tip = None if text.upper().startswith("NONE") else Tip("ai", text[:220], source=ai.PROVIDERS[provider].name)
            self.evaluate()

        def failed(message):
            self._ai_busy = False
            self.ai_status = str(message)

        from desktop_services import background
        self._ai_work = background(ask, done, failed)
