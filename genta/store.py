"""Local SQLite storage. Dates and datetimes use local, naive ISO values."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import calendar
import uuid

WEEKDAYS = {name: index for index, name in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
DAY_WORDS = "today|tomorrow|" + "|".join(WEEKDAYS)
CAPTURE = re.compile(
    rf"\s+(?:(?P<day>{DAY_WORDS})(?:\s+at\s+(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s*(?P<ampm>am|pm)?)?|at\s+(?P<time_hour>\d{{1,2}})(?::(?P<time_minute>\d{{2}}))?\s*(?P<time_ampm>am|pm)?)\s*$",
    re.IGNORECASE,
)


def valid_day(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Day must be YYYY-MM-DD or Inbox")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Day must be YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError("Day must be YYYY-MM-DD")
    return value


def valid_due(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Reminder must be a local ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Reminder must be a local ISO datetime") from None
    if parsed.tzinfo is not None or "T" not in value:
        raise ValueError("Reminder must be a local datetime without a timezone")
    return parsed.replace(microsecond=0).isoformat()


def valid_priority(value):
    if type(value) is not int or value not in (0, 1, 2):
        raise ValueError("Priority must be 0, 1, or 2")
    return value


def valid_title(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Write a task first")
    return value.strip()


def parse_capture(text, now=None):
    """Recognize only a trailing date/time phrase; leave other prose alone."""
    text = valid_title(text)
    now = now or datetime.now()
    match = CAPTURE.search(text)
    if not match:
        return text, None, None
    title = valid_title(text[:match.start()])
    word = (match.group("day") or "").lower()
    day = now.date()
    if word == "tomorrow":
        day += timedelta(days=1)
    elif word in WEEKDAYS:
        day += timedelta(days=(WEEKDAYS[word] - day.weekday()) % 7)
    hour = match.group("hour") or match.group("time_hour")
    due = None
    if hour is not None:
        hour = int(hour)
        minute = int(match.group("minute") or match.group("time_minute") or 0)
        ampm = (match.group("ampm") or match.group("time_ampm") or "").lower()
        if minute > 59 or (ampm and not 1 <= hour <= 12) or (not ampm and not 0 <= hour <= 23):
            raise ValueError("Use a valid time, e.g. 5pm or 17:30")
        if ampm:
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        due = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        if due < now:
            if not word:
                due += timedelta(days=1)
            elif word in WEEKDAYS and day == now.date():
                due += timedelta(days=7)
        day = due.date()
    return title, day.isoformat(), due.isoformat() if due else None


class Store:
    def __init__(self, path=None):
        if path is None:
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) if os.name == "nt" else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            path = base / "genta/genta.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Create privately without truncating an existing database.
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                day TEXT,
                due_at TEXT,
                priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 2),
                done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
                completed_at TEXT,
                reminded INTEGER NOT NULL DEFAULT 0 CHECK(reminded IN (0,1)),
                created_at TEXT NOT NULL,
                CHECK((done = 0 AND completed_at IS NULL) OR (done = 1 AND completed_at IS NOT NULL))
            );
            CREATE INDEX IF NOT EXISTS tasks_day ON tasks(day, done);
            CREATE INDEX IF NOT EXISTS tasks_due ON tasks(due_at) WHERE done = 0 AND reminded = 0;
            CREATE TABLE IF NOT EXISTS notes (
                day TEXT PRIMARY KEY,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS focus_sessions (
                id INTEGER PRIMARY KEY,
                seconds INTEGER NOT NULL CHECK(seconds > 0),
                task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS focus_date ON focus_sessions(created_at);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self._migrate()

    def close(self):
        self.conn.close()

    @staticmethod
    def _task(row):
        task = dict(row)
        task["done"] = bool(task["done"])
        task["reminded"] = bool(task["reminded"])
        return task

    def _get_task(self, task_id):
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ? AND deleted = 0", (task_id,)).fetchone()
        if row is None:
            raise KeyError("This task no longer exists")
        return self._task(row)

    def add_task(self, text, day=None, due_at=None, priority=0, **extra):
        day = valid_day(day)
        explicit_due = valid_due(due_at)
        priority = valid_priority(priority)
        title, parsed_day, parsed_due = parse_capture(text)
        if parsed_day:
            day = parsed_day
        due_at = explicit_due if explicit_due is not None else parsed_due
        if due_at is not None:
            day = datetime.fromisoformat(due_at).date().isoformat()
        now = datetime.now().isoformat(timespec="seconds")
        fields = self._extra_fields(extra)
        uid = str(uuid.uuid4())
        with self.conn:
            result = self.conn.execute(
                "INSERT INTO tasks(title,day,due_at,priority,created_at,uid) VALUES (?,?,?,?,?,?)",
                (title, day, due_at, priority, now, uid),
            )
            if fields:
                assignments = ", ".join(f"{key}=?" for key in fields)
                self.conn.execute(f"UPDATE tasks SET {assignments} WHERE id=?", [*fields.values(), result.lastrowid])
            self._emit("task", uid, self._sync_task(self._get_task(result.lastrowid)))
        return self._get_task(result.lastrowid)

    def tasks(self, day="all", include_done=True):
        conditions = ["deleted = 0"]
        params = []
        if day != "all":
            valid_day(day)
            if day is None:
                conditions.append("day IS NULL")
            else:
                conditions.append("day = ?")
                params.append(day)
        if not include_done:
            conditions.append("done = 0")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.conn.execute(
            "SELECT * FROM tasks" + where + " ORDER BY priority DESC, created_at, id", params,
        ).fetchall()
        return [self._task(row) for row in rows]

    def update_task(self, task_id, **changes):
        allowed = {"title", "day", "due_at", "priority", "done", "deadline", "project", "repeat_rule", "estimate_minutes"}
        if not changes.keys() <= allowed:
            raise ValueError("Unsupported task field")
        previous = self._get_task(task_id)
        if not changes:
            return self._get_task(task_id)
        values = dict(changes)
        values.update(self._extra_fields({k: values[k] for k in ("deadline", "project", "repeat_rule", "estimate_minutes") if k in values}))
        if "title" in values:
            values["title"] = valid_title(values["title"])
        if "day" in values:
            values["day"] = valid_day(values["day"])
        if "priority" in values:
            values["priority"] = valid_priority(values["priority"])
        if "due_at" in values:
            values["due_at"] = valid_due(values["due_at"])
            values["reminded"] = 0
            if values["due_at"]:
                values["day"] = datetime.fromisoformat(values["due_at"]).date().isoformat()
        elif "day" in values:
            # Moving to a date/inbox removes an old time rather than leaving inconsistent dates.
            values["due_at"] = None
            values["reminded"] = 0
        if "done" in values:
            if type(values["done"]) is not bool:
                raise ValueError("Completion must be true or false")
            old = self._get_task(task_id)
            values["completed_at"] = (
                old["completed_at"] or datetime.now().isoformat(timespec="seconds")
                if values["done"] else None
            )
            values["done"] = int(values["done"])
            if not values["done"]:
                values["reminded"] = 0
        # Column names originate only from the fixed allowlist above and internal fields.
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.conn:
            self.conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", [*values.values(), task_id])
            self._emit("task", previous["uid"], {k: v for k, v in values.items() if k != "reminded"})
            if changes.get("done") and not previous["done"]:
                self._repeat_next(self._get_task(task_id))
        return self._get_task(task_id)

    def delete_task(self, task_id):
        task = self._get_task(task_id)
        with self.conn:
            self.conn.execute("UPDATE tasks SET deleted=1 WHERE id=?", (task_id,))
            self.conn.execute("UPDATE focus_sessions SET task_id=NULL WHERE task_id=?", (task_id,))
            self._emit("task", task["uid"], {"deleted": 1})

    def note(self, day):
        if valid_day(day) is None:
            raise ValueError("A note needs a day")
        row = self.conn.execute("SELECT text FROM notes WHERE day = ?", (day,)).fetchone()
        return row[0] if row else ""

    def save_note(self, day, text):
        if valid_day(day) is None or not isinstance(text, str):
            raise ValueError("A note needs a day and text")
        with self.conn:
            self.conn.execute("INSERT INTO notes(day,text) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET text=excluded.text", (day, text))
            self._emit("note", day, {"text": text})

    def record_focus(self, seconds, task_id=None):
        if type(seconds) is not int or seconds <= 0:
            raise ValueError("Focus time must be positive whole seconds")
        if task_id is not None and not self.conn.execute("SELECT 1 FROM tasks WHERE id=? AND deleted=0", (task_id,)).fetchone():
            task_id = None
        with self.conn:
            self.conn.execute(
                "INSERT INTO focus_sessions(seconds,task_id,created_at) VALUES (?,?,?)",
                (seconds, task_id, datetime.now().isoformat(timespec="seconds")),
            )
            row = self.conn.execute("SELECT * FROM focus_sessions WHERE id=last_insert_rowid()").fetchone()
            uid = str(uuid.uuid4())
            self.conn.execute("UPDATE focus_sessions SET uid=? WHERE id=?", (uid, row["id"]))
            task_uid = self._get_task(task_id)["uid"] if task_id else None
            self._emit("focus", uid, {"seconds": seconds, "task_uid": task_uid, "created_at": row["created_at"]})

    def insights(self):
        today = date.today()
        start = (today - timedelta(days=6)).isoformat()
        completed = dict(self.conn.execute(
            "SELECT substr(completed_at,1,10), count(*) FROM tasks WHERE done=1 AND deleted=0 GROUP BY substr(completed_at,1,10)",
        ).fetchall())
        focused = dict(self.conn.execute(
            "SELECT substr(created_at,1,10), sum(seconds) FROM focus_sessions WHERE created_at>=? GROUP BY substr(created_at,1,10)", (start,),
        ).fetchall())
        history = []
        for offset in range(7):
            day = (today - timedelta(days=6-offset)).isoformat()
            history.append({"day": day, "completed": completed.get(day, 0), "focus_seconds": focused.get(day, 0)})
        streak = 0
        cursor = today if completed.get(today.isoformat()) else today - timedelta(days=1)
        while completed.get(cursor.isoformat()):
            streak += 1
            cursor -= timedelta(days=1)
        return {
            "completed_today": completed.get(today.isoformat(), 0),
            "focus_today_seconds": focused.get(today.isoformat(), 0),
            "streak": streak,
            "history": history,
            "best_hours": [dict(row) for row in self.conn.execute("SELECT substr(created_at,12,2) AS hour, sum(seconds) AS seconds FROM focus_sessions GROUP BY hour ORDER BY seconds DESC, hour LIMIT 3")],
        }

    def get_setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        if not isinstance(key, str) or not key:
            raise ValueError("A setting needs a name")
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.conn:
            self.conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encoded))

    def due_reminders(self, now=None):
        now = now or datetime.now()
        if now.tzinfo is not None:
            raise ValueError("Reminder checks use local time")
        stamp = now.isoformat(timespec="seconds")
        # Serialize claimers across processes, then commit before returning alerts.
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE deleted=0 AND done=0 AND reminded=0 AND due_at IS NOT NULL AND due_at<=? ORDER BY due_at,id", (stamp,),
            ).fetchall()
            self.conn.executemany("UPDATE tasks SET reminded=1 WHERE id=?", [(row["id"],) for row in rows])
        result = [self._task(row) for row in rows]
        for task in result:
            task["reminded"] = True
        return result

    def export_data(self):
        return {
            "version": 2,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "tasks": self.tasks(),
            "notes": [dict(row) for row in self.conn.execute("SELECT * FROM notes ORDER BY day")],
            "focus_sessions": [dict(row) for row in self.conn.execute("SELECT * FROM focus_sessions ORDER BY id")],
            "note_versions": [dict(row) for row in self.conn.execute("SELECT * FROM note_versions ORDER BY id")],
            "settings": {row["key"]: json.loads(row["value"]) for row in self.conn.execute("SELECT * FROM settings") if not row["key"].startswith(("sync_", "secret_", "hermes_"))},
        }

    def _migrate(self):
        """Idempotent additive migration; retain existing user tasks and notes."""
        with self.conn:
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(tasks)")}
            for name, definition in {
                "uid": "TEXT", "deadline": "TEXT", "project": "TEXT NOT NULL DEFAULT ''",
                "repeat_rule": "TEXT NOT NULL DEFAULT 'none'",
                "estimate_minutes": "INTEGER NOT NULL DEFAULT 25", "deleted": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            focus_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(focus_sessions)")}
            if "uid" not in focus_columns:
                self.conn.execute("ALTER TABLE focus_sessions ADD COLUMN uid TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS tasks_uid ON tasks(uid)")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS focus_uid ON focus_sessions(uid)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS sync_events(seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, device TEXT NOT NULL, clock INTEGER NOT NULL, kind TEXT NOT NULL, entity TEXT NOT NULL, values_json TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS sync_fields(kind TEXT NOT NULL, entity TEXT NOT NULL, field TEXT NOT NULL, clock INTEGER NOT NULL, device TEXT NOT NULL, PRIMARY KEY(kind,entity,field))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS note_versions(id INTEGER PRIMARY KEY, day TEXT NOT NULL, text TEXT NOT NULL, event_id TEXT UNIQUE NOT NULL)")
            if self.get_setting("sync_device") is None:
                self.set_setting("sync_device", str(uuid.uuid4()))
            for row in self.conn.execute("SELECT id FROM tasks WHERE uid IS NULL").fetchall():
                uid = str(uuid.uuid4())
                self.conn.execute("UPDATE tasks SET uid=? WHERE id=?", (uid, row["id"]))
                self._emit("task", uid, self._sync_task(self._get_task(row["id"])))
            for row in self.conn.execute("SELECT * FROM focus_sessions WHERE uid IS NULL").fetchall():
                uid = str(uuid.uuid4())
                self.conn.execute("UPDATE focus_sessions SET uid=? WHERE id=?", (uid, row["id"]))
                task_uid = self._get_task(row["task_id"])["uid"] if row["task_id"] else None
                self._emit("focus", uid, {"seconds": row["seconds"], "task_uid": task_uid, "created_at": row["created_at"]})
            if not self.get_setting("sync_notes_seeded", False):
                for row in self.conn.execute("SELECT * FROM notes").fetchall():
                    self._emit("note", row["day"], {"text": row["text"]})
                self.set_setting("sync_notes_seeded", True)
            self.conn.execute("PRAGMA user_version=2")

    @staticmethod
    def _extra_fields(values):
        result = dict(values)
        if not result.keys() <= {"deadline", "project", "repeat_rule", "estimate_minutes"}:
            raise ValueError("Unsupported task field")
        if "deadline" in result:
            result["deadline"] = valid_day(result["deadline"])
        if "project" in result:
            if not isinstance(result["project"], str) or len(result["project"]) > 100:
                raise ValueError("Project must be text, up to 100 characters")
            result["project"] = result["project"].strip()
        if "repeat_rule" in result and result["repeat_rule"] not in ("none", "daily", "weekdays", "weekly", "monthly"):
            raise ValueError("Repeat must be none, daily, weekdays, weekly, or monthly")
        if "estimate_minutes" in result:
            value = result["estimate_minutes"]
            if type(value) is not int or not 1 <= value <= 480:
                raise ValueError("Task duration must be 1–480 minutes")
        return result

    def _repeat_next(self, task):
        rule = task["repeat_rule"]
        if rule == "none":
            return
        base = max(date.fromisoformat(task["day"]) if task["day"] else date.today(), date.today())
        if rule in ("daily", "weekdays"):
            next_day = base + timedelta(days=1)
            while rule == "weekdays" and next_day.weekday() >= 5:
                next_day += timedelta(days=1)
        elif rule == "weekly":
            next_day = base + timedelta(days=7)
        else:
            year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
            next_day = date(year, month, min(base.day, calendar.monthrange(year, month)[1]))
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"genta:{task['uid']}:{next_day.isoformat()}"))
        if self.conn.execute("SELECT 1 FROM tasks WHERE uid=?", (uid,)).fetchone():
            return
        due = datetime.combine(next_day, datetime.fromisoformat(task["due_at"]).time()).isoformat() if task["due_at"] else None
        deadline = None
        if task["deadline"]:
            offset = (date.fromisoformat(task["deadline"]) - base).days
            deadline = (next_day + timedelta(days=offset)).isoformat()
        values = {k: task[k] for k in ("title", "priority", "project", "repeat_rule", "estimate_minutes")}
        values.update(day=next_day.isoformat(), due_at=due, deadline=deadline, done=0, completed_at=None, deleted=0, created_at=datetime.now().isoformat(timespec="seconds"))
        columns = list(values) + ["uid"]
        self.conn.execute(f"INSERT INTO tasks({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [*values.values(), uid])
        self._emit("task", uid, values)

    def plan_day(self, events=(), end_hour=18, now=None):
        """Place untimed tasks in free slots. Return scheduled/unscheduled task IDs."""
        now = (now or datetime.now()).replace(second=0, microsecond=0)
        if not 0 <= end_hour <= 23:
            raise ValueError("End hour must be 0–23")
        end = now.replace(hour=end_hour, minute=0)
        busy = []
        for event in events:
            start, finish = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
            if start.tzinfo:
                start = start.astimezone().replace(tzinfo=None)
            if finish.tzinfo:
                finish = finish.astimezone().replace(tzinfo=None)
            busy.append((start, finish))
        for task in self.tasks(include_done=False):
            if task["due_at"]:
                start = datetime.fromisoformat(task["due_at"])
                busy.append((start, start + timedelta(minutes=task["estimate_minutes"])))
        busy.sort()
        cursor = now + timedelta(minutes=1)
        candidates = [t for t in self.tasks(include_done=False) if t["day"] and t["day"] <= now.date().isoformat() and not t["due_at"]]
        candidates.sort(key=lambda t: (-t["priority"], t["deadline"] or "9999", t["id"]))
        result = {"scheduled": [], "unscheduled": []}
        for task in candidates:
            duration = timedelta(minutes=task["estimate_minutes"])
            slot = cursor
            for start, finish in busy:
                if slot + duration <= start:
                    break
                if slot < finish and slot + duration > start:
                    slot = finish
            if slot + duration > end:
                result["unscheduled"].append(task["id"])
                continue
            self.update_task(task["id"], day=slot.date().isoformat(), due_at=slot.isoformat())
            busy.append((slot, slot + duration))
            busy.sort()
            cursor = slot + duration
            result["scheduled"].append(task["id"])
        return result

    def wrap_up(self, destination):
        day = None if destination == "inbox" else (date.today() + timedelta(days=1)).isoformat()
        if destination not in ("inbox", "tomorrow"):
            raise ValueError("Choose inbox or tomorrow")
        moved = 0
        for task in self.tasks(include_done=False):
            if task["day"] and task["day"] <= date.today().isoformat():
                self.update_task(task["id"], day=day, due_at=None)
                moved += 1
        return moved

    @staticmethod
    def _sync_task(task):
        return {k: v for k, v in task.items() if k not in ("id", "uid", "reminded")}

    def _emit(self, kind, entity, values):
        clock = int(self.get_setting("sync_clock", 0)) + 1
        device = self.get_setting("sync_device")
        self.conn.execute("INSERT INTO settings(key,value) VALUES ('sync_clock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(clock),))
        event_id = str(uuid.uuid4())
        self.conn.execute("INSERT INTO sync_events(event_id,device,clock,kind,entity,values_json) VALUES (?,?,?,?,?,?)", (event_id, device, clock, kind, entity, json.dumps(values, ensure_ascii=False)))
        for field in values:
            self.conn.execute("INSERT INTO sync_fields VALUES (?,?,?,?,?) ON CONFLICT(kind,entity,field) DO UPDATE SET clock=excluded.clock,device=excluded.device", (kind, entity, field, clock, device))
        if kind == "note":
            self.conn.execute("INSERT OR IGNORE INTO note_versions(day,text,event_id) VALUES (?,?,?)", (entity, values["text"], event_id))

    def sync_events(self, after=0, limit=500):
        if type(after) is not int or after < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid sync cursor or page size")
        rows = self.conn.execute("SELECT * FROM sync_events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["values"] = json.loads(item.pop("values_json"))
            events.append(item)
        return events

    def ingest_events(self, events):
        """Merge per-field Lamport clocks; retain every note version for recovery."""
        if not isinstance(events, list) or len(events) > 500:
            raise ValueError("A sync page must contain at most 500 events")
        with self.conn:
            for event in events:
                event_id, device, clock = event["event_id"], event["device"], event["clock"]
                kind, entity, values = event["kind"], event["entity"], event["values"]
                uuid.UUID(event_id)
                uuid.UUID(device)
                if type(clock) is not int or not 0 < clock < 2**53 or not isinstance(values, dict):
                    raise ValueError("Invalid sync event")
                if self.conn.execute("SELECT 1 FROM sync_events WHERE event_id=?", (event_id,)).fetchone():
                    continue
                if kind == "task":
                    uuid.UUID(entity)
                    allowed = {"title", "day", "due_at", "priority", "done", "completed_at", "created_at", "deadline", "project", "repeat_rule", "estimate_minutes", "deleted"}
                    if not values.keys() <= allowed:
                        raise ValueError("Unsupported synchronized task field")
                    values = dict(values)
                    if "title" in values:
                        values["title"] = valid_title(values["title"])
                    if "day" in values:
                        values["day"] = valid_day(values["day"])
                    if "due_at" in values:
                        values["due_at"] = valid_due(values["due_at"])
                    if "priority" in values:
                        values["priority"] = valid_priority(values["priority"])
                    values.update(self._extra_fields({k: v for k, v in values.items() if k in ("deadline", "project", "repeat_rule", "estimate_minutes")}))
                    for flag in ("done", "deleted"):
                        if flag in values and values[flag] not in (0, 1, False, True):
                            raise ValueError("Invalid synchronized state")
                    for stamp in ("created_at", "completed_at"):
                        if values.get(stamp):
                            valid_due(values[stamp])
                elif kind == "note":
                    if valid_day(entity) is None or set(values) != {"text"} or not isinstance(values["text"], str):
                        raise ValueError("Invalid synchronized note")
                    self.conn.execute("INSERT INTO note_versions(day,text,event_id) VALUES (?,?,?)", (entity, values["text"], event_id))
                elif kind == "focus":
                    uuid.UUID(entity)
                    if set(values) != {"seconds", "task_uid", "created_at"} or type(values["seconds"]) is not int or not 0 < values["seconds"] <= 86400:
                        raise ValueError("Invalid synchronized focus time")
                    valid_due(values["created_at"])
                else:
                    raise ValueError("Unknown synchronization entity")
                if len(json.dumps(values)) > 200000:
                    raise ValueError("Synchronized content is too large")
                merged = {}
                for field, value in values.items():
                    old = self.conn.execute("SELECT clock,device FROM sync_fields WHERE kind=? AND entity=? AND field=?", (kind, entity, field)).fetchone()
                    if old is None or (clock, device) > (old["clock"], old["device"]):
                        merged[field] = value
                        self.conn.execute("INSERT INTO sync_fields VALUES (?,?,?,?,?) ON CONFLICT(kind,entity,field) DO UPDATE SET clock=excluded.clock,device=excluded.device", (kind, entity, field, clock, device))
                if kind == "task" and merged:
                    exists = self.conn.execute("SELECT id,deleted FROM tasks WHERE uid=?", (entity,)).fetchone()
                    if not exists:
                        if not values.get("title"):
                            raise ValueError("A new synchronized task needs its creation event first")
                        self.conn.execute("INSERT INTO tasks(title,created_at,uid) VALUES (?,?,?)", (values["title"], values.get("created_at", datetime.now().isoformat(timespec="seconds")), entity))
                    elif exists["deleted"]:
                        merged["deleted"] = 1
                    if "due_at" in merged:
                        merged["reminded"] = 0
                    assignments = ",".join(f"{key}=?" for key in merged)
                    self.conn.execute(f"UPDATE tasks SET {assignments} WHERE uid=?", [*merged.values(), entity])
                elif kind == "note" and merged:
                    self.conn.execute("INSERT INTO notes(day,text) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET text=excluded.text", (entity, merged["text"]))
                elif kind == "focus":
                    task = self.conn.execute("SELECT id FROM tasks WHERE uid=? AND deleted=0", (values["task_uid"],)).fetchone()
                    self.conn.execute("INSERT OR IGNORE INTO focus_sessions(seconds,task_id,created_at,uid) VALUES (?,?,?,?)", (values["seconds"], task["id"] if task else None, values["created_at"], entity))
                self.conn.execute("INSERT INTO sync_events(event_id,device,clock,kind,entity,values_json) VALUES (?,?,?,?,?,?)", (event_id, device, clock, kind, entity, json.dumps(values, ensure_ascii=False)))
                maximum = max(clock, int(self.get_setting("sync_clock", 0)))
                self.conn.execute("INSERT INTO settings(key,value) VALUES ('sync_clock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(maximum),))
