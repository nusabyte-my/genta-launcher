"""Genta the bell: a small animated desktop companion that sits on top of the COSMIC dock.

Runs as its own process (``bin/genta-bell``), started by Genta. It draws into a
wlr-layer-shell surface (transparent, above windows, clicks pass through except on
the bell or its bubble) and reads Genta's status file to decide what to show.

Rules (lessons from Clippy): it never speaks first except for a meeting or prayer
within 10 minutes and one morning hello; it sleeps during focus; hides in full
screen and Do Not Disturb; mutes itself for an hour after two dismissed tips.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import math
import mmap
import os
from pathlib import Path
import random
import socket
import struct
import subprocess
import sys
import time
import wave

os.environ["QT_QPA_PLATFORM"] = "offscreen"     # we render to Wayland ourselves
from PySide6.QtCore import QPointF, QRectF, QSocketNotifier, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402
from pywayland.client import Display  # noqa: E402

from .wl.cosmic_toplevel_info_unstable_v1 import ZcosmicToplevelInfoV1  # noqa: E402
from .wl.wayland import WlCompositor, WlSeat, WlShm  # noqa: E402
from .wl.wlr_layer_shell_unstable_v1 import ZwlrLayerShellV1  # noqa: E402

BELL = [
    "......#......", ".....###.....", "...#######...", "..#########..", "..#########..",
    ".###########.", ".###########.", ".###########.", "#############", "#############",
]
CLAPPER = [(5, 10), (6, 10), (7, 10), (6, 11)]
EYES = [(4, 6), (8, 6)]
COLORS = {"idle": "#F2F2F2", "watching": "#F2F2F2", "thinking": "#3EC7C2", "tip": "#F0B429",
          "focus": "#D71921", "prayer": "#5EDB8C", "done": "#5EDB8C", "error": "#7A7A7A", "meeting": "#F0B429"}
INK, PAPER, RED = "#111111", "#F2F2F2", "#D71921"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "genta-launcher/bell.json"
DND = Path.home() / ".config/cosmic/com.system76.CosmicNotifications/v1/do_not_disturb"
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
SIZES = {"small": 3.0, "medium": 4.2, "large": 5.8}
SURFACE_W, SURFACE_H = 340, 210
BTN_LEFT, BTN_RIGHT = 0x110, 0x111


def load_config():
    try:
        data = json.loads(CONFIG.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=1))


def genta(command):
    """Send one command to Genta's local socket; ignore failures (Genta may be restarting)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(RUNTIME / "genta.sock"))
            client.sendall(command.encode())
    except OSError:
        pass


def ding_path():
    """An original, synthesized soft bell tone (two decaying partials), created once."""
    path = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "genta/ding.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        rate, seconds = 22050, 1.2
        frames = bytearray()
        for i in range(int(rate * seconds)):
            t = i / rate
            value = (0.55 * math.sin(2 * math.pi * 1318.5 * t) + 0.25 * math.sin(2 * math.pi * 2637 * t)
                     + 0.12 * math.sin(2 * math.pi * 3951 * t)) * math.exp(-4.2 * t) * min(1.0, t * 400)
            frames += struct.pack("<h", int(value * 9000))
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(bytes(frames))
    return path


class Bell:
    def __init__(self):
        self.app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
        for font in FONT_DIR.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font))
        self.config = load_config()
        self.display = Display()
        self.display.connect()
        registry = self.display.get_registry()
        self.globals = {}
        def on_global(reg, name, interface, version):   # callbacks must return None
            self.globals.setdefault(interface, (name, version))

        registry.dispatcher["global"] = on_global
        self.display.roundtrip()
        bind = lambda iface, cls, version: registry.bind(self.globals[iface][0], cls, min(version, self.globals[iface][1]))
        self.compositor = bind("wl_compositor", WlCompositor, 4)
        self.shm = bind("wl_shm", WlShm, 1)
        self.layer_shell = bind("zwlr_layer_shell_v1", ZwlrLayerShellV1, 4)
        self.seat = bind("wl_seat", WlSeat, 5)
        self.pointer = None
        self.seat.dispatcher["capabilities"] = self._capabilities
        self.fullscreen = set()
        if "zcosmic_toplevel_info_v1" in self.globals:
            self._watch_toplevels(bind("zcosmic_toplevel_info_v1", ZcosmicToplevelInfoV1, 1))

        self.width, self.height = SURFACE_W, SURFACE_H
        self.surface = self.compositor.create_surface()
        self.layer = self.layer_shell.get_layer_surface(self.surface, None, 2, "genta-bell")   # 2 = top layer
        # First configure spans the output (left|right|bottom) to learn its logical width,
        # then the surface shrinks to the bell's box and x comes from the left margin.
        self.layer.set_size(0, self.height)
        self.layer.set_anchor(2 | 4 | 8)
        self.measured = False
        self.layer.set_exclusive_zone(0)      # sit just above the dock's reserved area
        self.layer.set_keyboard_interactivity(0)
        self.layer.dispatcher["configure"] = self._configure
        def layer_closed(*_):
            self.app.quit()

        self.layer.dispatcher["closed"] = layer_closed
        self.output_width = 1920
        self.x = int(self.config.get("x", -1))
        self.surface.commit()
        self._buffers = self._make_buffers()
        self.configured = False

        # Behaviour state
        self.status = {}
        self.state = "idle"
        self.frame = 0
        self.hop_t = None            # 0..1 progress of a hop, None when grounded
        self.hop_dx = 0.0
        self.hops_left = 0
        self.next_hop = time.monotonic() + 3600
        self.blink_until = 0.0
        self.next_blink = time.monotonic() + 3
        self.look = 0.0
        self.bubble = None           # {"text", "buttons": [(label, action)], "until"}
        self.button_rects = []
        self.muted_until = float(self.config.get("muted_until", 0))
        self.dismissals = 0
        self.last_tip_id = None
        self.announced = set()
        self.pointer_pos = None
        self.drag_origin = None
        self.drag_moved = False
        self.done_seen = 0.0
        self.hidden = False

        self.notifier = QSocketNotifier(self.display.get_fd(), QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self._wayland_ready)
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.read_status)
        self.status_timer.start(1000)
        self.read_status()
        self.display.flush()
        demo = os.environ.get("GENTA_BELL_DEMO")     # for screenshots: tip | menu | hello
        if demo:
            QTimer.singleShot(800, {"tip": self.show_tip, "menu": self.show_menu,
                                    "hello": lambda: (self.config.pop("hello", None), self.morning_hello(None))}.get(demo, lambda: None))

    # ── Wayland plumbing ───────────────────────────────────────
    def _wayland_ready(self):
        if self.display.dispatch(block=True) == -1:
            self.app.quit()
        self.display.flush()

    def _make_buffers(self):
        stride = self.width * 4
        size = stride * self.height
        fd = os.memfd_create("genta-bell", os.MFD_CLOEXEC)
        os.ftruncate(fd, size * 2)
        self.memory = mmap.mmap(fd, size * 2)
        pool = self.shm.create_pool(fd, size * 2)
        buffers = []
        for index in range(2):
            buffer = pool.create_buffer(index * size, self.width, self.height, stride, WlShm.format.argb8888.value)
            entry = {"buffer": buffer, "offset": index * size, "busy": False}
            def released(_, e=entry):
                e["busy"] = False

            buffer.dispatcher["release"] = released
            buffers.append(entry)
        os.close(fd)
        self.image = QImage(self.width, self.height, QImage.Format.Format_ARGB32_Premultiplied)
        return buffers

    def _configure(self, layer, serial, width, height):
        layer.ack_configure(serial)
        if os.environ.get("GENTA_BELL_DEBUG"):
            print("configure", width, height, "measured" if self.measured else "first", flush=True)
        if not self.measured:
            self.measured = True
            if width > 0:
                self.output_width = width
            layer.set_size(self.width, self.height)
            layer.set_anchor(2 | 4)           # bottom | left
            self.place()
            return
        self.configured = True
        self.place()

    def _capabilities(self, seat, caps):
        if caps & 1 and self.pointer is None:      # pointer
            self.pointer = seat.get_pointer()
            def enter(p, serial, surface, x, y):
                self._pointer_move(x, y)

            def leave(p, serial, surface):
                self.pointer_pos = None

            def motion(p, t, x, y):
                self._pointer_move(x, y)

            self.pointer.dispatcher["enter"] = enter
            self.pointer.dispatcher["leave"] = leave
            self.pointer.dispatcher["motion"] = motion
            self.pointer.dispatcher["button"] = self._pointer_button

    def _watch_toplevels(self, info):
        self._handles = []

        def on_toplevel(_, handle):
            self._handles.append(handle)

            def on_state(_, raw):
                values = struct.unpack(f"{len(bytes(raw)) // 4}I", bytes(raw))
                if 3 in values and 2 in values:       # fullscreen and focused
                    self.fullscreen.add(id(handle))
                else:
                    self.fullscreen.discard(id(handle))

            handle.dispatcher["state"] = on_state
            def closed(_):
                self.fullscreen.discard(id(handle))

            handle.dispatcher["closed"] = closed

        info.dispatcher["toplevel"] = on_toplevel

    def place(self):
        """Left margin from the saved x (default: just right of the dock's centre)."""
        if self.x < 0 or self.x > self.output_width:
            self.x = self.output_width // 2 + min(300, self.output_width // 5)   # near the dock's right end; drag to move
        x = max(0, min(self.output_width - self.width, int(self.x - self.width / 2)))
        self.layer.set_margin(0, 0, 0, x)
        self.surface.commit()

    # ── input ─────────────────────────────────────────────────
    def bell_rect(self):
        dot = SIZES.get(self.config.get("size", "medium"), 4.2)
        w, h = 13 * dot + 8, 13 * dot + 8
        lift = self.hop_offset()
        return QRectF(self.width / 2 - w / 2 + self.hop_shift(), self.height - h - 4 - lift, w, h)

    def _pointer_move(self, x, y):
        self.pointer_pos = (float(x), float(y))
        if self.drag_origin is not None:
            dx = x - self.drag_origin[0]
            if abs(dx) > 4:
                self.drag_moved = True
            if self.drag_moved:
                self.x = int(self.drag_origin[1] + dx)
                self.place()

    def _pointer_button(self, pointer, serial, t, button, state):
        if self.pointer_pos is None:
            return
        x, y = self.pointer_pos
        if state == 1:            # pressed
            for rect, action in self.button_rects:
                if rect.contains(QPointF(x, y)):
                    action()
                    return
            if button == BTN_LEFT and self.bell_rect().contains(QPointF(x, y)):
                self.drag_origin = (x, self.x)
                self.drag_moved = False
            elif button == BTN_RIGHT and self.bell_rect().contains(QPointF(x, y)):
                self.show_menu()
        elif state == 0 and button == BTN_LEFT and self.drag_origin is not None:
            moved = self.drag_moved
            self.drag_origin = None
            if moved:
                self.config["x"] = self.x
                save_config(self.config)
            else:
                self.clicked()

    def clicked(self):
        tip = self.status.get("bell", {}).get("tip")
        if self.bubble:
            self.bubble = None
        elif tip:
            self.show_tip(manual=True)
        else:
            genta("launcher")

    # ── bubbles ───────────────────────────────────────────────
    def say(self, text, buttons=(), seconds=12):
        self.bubble = {"text": text, "buttons": list(buttons), "until": time.monotonic() + seconds}

    def show_tip(self, manual=False):
        bell = self.status.get("bell", {})
        tip_id, text = bell.get("tip_id"), bell.get("tip")
        if not text:
            return
        buttons = []
        if bell.get("tip_action"):
            buttons.append((bell["tip_action"], lambda: (genta(f"tip_run:{tip_id}"), self.dismiss(False))))
        buttons += [("Not now", lambda: (genta(f"tip_snooze:{tip_id}"), self.dismiss(True))),
                    ("Never", lambda: (genta(f"tip_mute:{tip_id}"), self.dismiss(True)))]
        self.say(text, buttons, seconds=20 if manual else 12)

    def dismiss(self, counted):
        self.bubble = None
        if counted:
            self.dismissals += 1
            if self.dismissals >= 2:       # two in a row → quiet for an hour
                self.mute(3600, "Okay, I'll be quiet for an hour.")
                self.dismissals = 0
        else:
            self.dismissals = 0
            self.celebrate()

    def mute(self, seconds, message=None):
        self.muted_until = time.time() + seconds
        self.config["muted_until"] = self.muted_until
        save_config(self.config)
        self.bubble = None
        if message:
            self.say(message, seconds=3)

    def show_menu(self):
        config = self.config

        def size():
            order = ["small", "medium", "large"]
            config["size"] = order[(order.index(config.get("size", "medium")) + 1) % 3]
            save_config(config)
            self.bubble = None

        def sound():
            config["sound"] = not config.get("sound", False)
            save_config(config)
            self.say("Sound on." if config["sound"] else "Sound off.", seconds=2)
            if config["sound"]:
                self.ding()

        tomorrow = datetime.combine(date.today(), datetime.min.time()).timestamp() + 86400 + 5 * 3600
        self.say("Genta bell", [
            ("Mute 1 h", lambda: self.mute(3600)),
            ("Mute today", lambda: self.mute(tomorrow - time.time())),
            (f"Size: {config.get('size', 'medium')}", size),
            ("Sound: " + ("on" if config.get("sound") else "off"), sound),
            ("Hide bell", lambda: (genta("bell_off"), self.app.quit())),
        ], seconds=10)

    def ding(self):
        if not self.config.get("sound", False):
            return
        player = next((p for p in ("pw-play", "paplay", "aplay") if subprocess.run(["which", p], capture_output=True).returncode == 0), None)
        if player:
            subprocess.Popen([player, str(ding_path())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── behaviour ─────────────────────────────────────────────
    def read_status(self):
        try:
            status = json.loads((RUNTIME / "genta-status.json").read_text())
            alive = Path(f"/proc/{int(status.get('pid', 0))}").exists()
        except (OSError, ValueError, TypeError):
            status, alive = {}, False
        self.status = status if alive else {}
        self.genta_alive = alive
        self.decide()

    def quiet_reason(self):
        if self.fullscreen:
            return "fullscreen"
        try:
            if DND.read_text().strip() == "true":
                return "dnd"
        except OSError:
            pass
        if time.time() < self.muted_until:
            return "muted"
        return None

    def decide(self):
        bell = self.status.get("bell", {})
        quiet = self.quiet_reason()
        self.hidden = quiet in ("fullscreen", "dnd")
        solat = self.status.get("solat") or {}
        prayer_soon = False
        if solat.get("time"):
            try:
                when = datetime.combine(date.today(), datetime.strptime(solat["time"], "%H:%M").time())
                prayer_soon = 0 <= (when - datetime.now()).total_seconds() <= 600
            except ValueError:
                pass
        if not self.genta_alive:
            state = "error"
        elif bell.get("thinking"):
            state = "thinking"
        elif self.status.get("mode") == "running":
            state = "focus"
        elif bell.get("meeting_soon"):
            state = "meeting"
        elif prayer_soon:
            state = "prayer"
        elif bell.get("tip"):
            state = "tip"
        else:
            state = "watching" if self.pointer_pos else "idle"
        if bell.get("done", 0) > self.done_seen:
            self.done_seen = bell["done"]
            if time.time() - self.done_seen < 10:
                self.celebrate()
        if state != self.state:
            previous, self.state = self.state, state
            if state == "tip" and quiet is None and bell.get("tip_id") != self.last_tip_id:
                self.last_tip_id = bell.get("tip_id")
                self.wobble()
                self.ding()
            if state in ("meeting", "prayer") and quiet is None:
                key = (state, solat.get("time") if state == "prayer" else bell.get("meeting_soon"))
                if key not in self.announced:
                    self.announced.add(key)
                    text = (f"{solat.get('name', 'Prayer')} at {solat.get('time')}. Time to wrap up."
                            if state == "prayer" else f"Meeting soon: {bell.get('meeting_soon')}")
                    self.say(text, [("OK", lambda: self.dismiss(False))], seconds=15)
                    self.ding()
        self.morning_hello(quiet)

    def morning_hello(self, quiet):
        today = date.today().isoformat()
        if quiet or self.config.get("hello") == today or datetime.now().hour < 5 or not self.genta_alive:
            return
        self.config["hello"] = today
        save_config(self.config)
        bell = self.status.get("bell", {})
        greeting = "Selamat pagi!" if datetime.now().hour < 12 else "Hello again!"
        todo = bell.get("todo", 0)
        self.say(f"{greeting} {todo} to-do{'s' if todo != 1 else ''} today. Click me any time.", seconds=8)
        self.hop(2, 0)

    def wobble(self):
        self.wobble_until = time.monotonic() + 1.2

    def celebrate(self):
        self.celebrate_until = time.monotonic() + 2.0
        self.hop(2, 0)

    def hop(self, count, dx):
        if self.hop_t is None:
            self.hops_left = count
            self.hop_dx = dx
            self.hop_t = 0.0

    def hop_offset(self):
        if self.hop_t is None:
            return 0.0
        return math.sin(math.pi * self.hop_t) * 26

    def hop_shift(self):
        return 0.0

    def tick(self):
        now = time.monotonic()
        self.frame += 1
        if self.bubble and now > self.bubble["until"]:
            self.bubble = None
        if now > self.next_blink:
            self.blink_until = now + 0.14
            self.next_blink = now + random.uniform(2.5, 6.0)
        if self.state not in ("focus", "error") and now > self.next_hop and not self.quiet_reason():
            self.next_hop = now + 3600 + random.uniform(-600, 600)
            self.hop(3, random.choice((-1, 1)) * 18)
        if self.hop_t is not None:
            self.hop_t += 0.07
            if self.hop_t >= 1.0:
                self.x += int(self.hop_dx)
                self.hops_left -= 1
                self.hop_t = 0.0 if self.hops_left > 0 else None
                if self.hop_dx:
                    self.place()
        if self.pointer_pos:
            self.look = max(-1.0, min(1.0, (self.pointer_pos[0] - self.width / 2) / 120))
        else:
            self.look *= 0.9
        if self.configured:
            self.render()

    # ── drawing ───────────────────────────────────────────────
    def render(self):
        entry = next((b for b in self._buffers if not b["busy"]), None)
        if entry is None:
            return
        image = self.image
        image.fill(Qt.GlobalColor.transparent)
        rects = []
        if not self.hidden:
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.draw_bell(painter)
            if self.bubble:
                rects = self.draw_bubble(painter)
            painter.end()
        self.button_rects = rects
        size = self.width * 4 * self.height
        self.memory[entry["offset"]:entry["offset"] + size] = bytes(image.constBits())[:size]
        region = self.compositor.create_region()
        if not self.hidden:
            r = self.bell_rect()
            region.add(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
            for rect, _ in rects:
                region.add(int(rect.x()) - 2, int(rect.y()) - 2, int(rect.width()) + 4, int(rect.height()) + 4)
            if self.bubble:
                b = self._bubble_rect
                region.add(int(b.x()), int(b.y()), int(b.width()), int(b.height()))
        self.surface.set_input_region(region)
        region.destroy()
        self.surface.attach(entry["buffer"], 0, 0)
        self.surface.damage_buffer(0, 0, self.width, self.height)
        self.surface.commit()
        entry["busy"] = True
        self.display.flush()

    def draw_bell(self, painter):
        dot = SIZES.get(self.config.get("size", "medium"), 4.2)
        now = time.monotonic()
        state = self.state
        base = QColor(COLORS.get(state, PAPER))
        rect = self.bell_rect()
        # Squash on landing / stretch at take-off.
        squash = 1.0
        if self.hop_t is not None:
            squash = 1.0 + 0.10 * math.cos(math.pi * self.hop_t * 2) if self.hop_t < 0.15 or self.hop_t > 0.85 else 0.93
        breathe = 0.5 + 0.5 * math.sin(self.frame / 22)
        tilt = 0.0
        if state == "prayer":
            tilt = 10 * (0.5 + 0.5 * math.sin(self.frame / 40))
        elif state == "error":
            tilt = -14
        elif getattr(self, "wobble_until", 0) > now:
            tilt = 9 * math.sin((self.wobble_until - now) * 22)
        cx, bottom = rect.center().x(), rect.bottom() - 4
        painter.save()
        painter.translate(cx, bottom)
        painter.rotate(tilt)
        painter.scale(1.0 / squash, squash)
        painter.translate(-6.5 * dot, -12.5 * dot)
        painter.setPen(Qt.PenStyle.NoPen)
        # soft shadow / glow so it reads on any wallpaper
        glow = QColor(0, 0, 0, 110)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(1 * dot, 12.2 * dot + self.hop_offset() / squash, 11 * dot, 1.6 * dot))
        radius = dot * 0.42
        for y, row in enumerate(BELL):
            for x, c in enumerate(row):
                if c != "#":
                    continue
                colour = QColor(base)
                if state in ("idle", "watching"):
                    colour.setAlphaF(0.72 + 0.28 * breathe)
                elif state == "error" and (x + y) % 2:
                    colour.setAlphaF(0.35)
                elif state == "focus":
                    colour.setAlphaF(0.85)
                painter.setBrush(QColor(0, 0, 0, 55))     # faint outline so white dots read on light wallpapers
                painter.drawEllipse(QPointF(x * dot + dot / 2, y * dot + dot / 2 + 0.5), radius + 0.9, radius + 0.9)
                painter.setBrush(colour)
                painter.drawEllipse(QPointF(x * dot + dot / 2, y * dot + dot / 2), radius, radius)
        # eyes
        closed = state == "focus" or now < self.blink_until
        look = self.look if state == "watching" else 0.0
        painter.setBrush(QColor(INK))
        for ex, ey in EYES:
            centre = QPointF(ex * dot + dot / 2 + look * dot * 0.5, ey * dot + dot / 2)
            if closed:
                painter.drawRoundedRect(QRectF(centre.x() - dot * 0.55, centre.y() - dot * 0.12, dot * 1.1, dot * 0.24), 1, 1)
            else:
                painter.drawEllipse(centre, dot * 0.5, dot * 0.62)
        # clapper (swings while thinking or wobbling)
        swing = 0
        if state == "thinking":
            swing = (-1, 0, 1, 0)[(self.frame // 4) % 4]
        elif getattr(self, "wobble_until", 0) > now:
            swing = (-1, 1)[(self.frame // 2) % 2]
        painter.setBrush(QColor(RED if state != "error" else "#555555"))
        for x, y in CLAPPER:
            painter.drawEllipse(QPointF((x + swing) * dot + dot / 2, y * dot + dot / 2), radius, radius)
        # extras
        if state == "tip" and (self.frame // 10) % 2 == 0:
            painter.setBrush(base)
            for x, y in ((-1.6, 2.5), (-2.6, 4.5), (14.6, 2.5), (15.6, 4.5)):
                painter.drawEllipse(QPointF(x * dot, y * dot), radius, radius)
        if getattr(self, "celebrate_until", 0) > now:
            painter.setBrush(QColor(COLORS["done"]))
            for index, (x, y) in enumerate(((-1.5, 0.5), (14.5, 0.5), (-2.2, 6), (15.2, 6), (6.5, -2))):
                if (self.frame + index * 3) % 6 < 4:
                    painter.drawEllipse(QPointF(x * dot, y * dot), radius * 1.1, radius * 1.1)
        if state == "focus":
            painter.setPen(QColor(base))
            font = QFont("Space Mono")
            font.setPixelSize(int(dot * 3))
            painter.setFont(font)
            z = (self.frame // 12) % 3
            painter.drawText(QPointF(12.5 * dot, -0.5 * dot - z * dot), "z" * (z + 1))
        painter.restore()

    def draw_bubble(self, painter):
        bubble = self.bubble
        rect_bell = self.bell_rect()
        font = QFont("Space Grotesk")
        font.setPixelSize(13)
        mono = QFont("Space Mono")
        mono.setPixelSize(10)
        metrics = QFontMetrics(font)
        width = self.width - 16
        text_rect = metrics.boundingRect(0, 0, width - 24, 200, Qt.TextFlag.TextWordWrap, bubble["text"])
        button_h = 22 if bubble["buttons"] else 0
        rows, row, row_width, mono_metrics = [], [], 0, QFontMetrics(mono)
        for label, action in bubble["buttons"]:
            w = mono_metrics.horizontalAdvance(label.upper()) + 18
            if row and row_width + w > width - 24:
                rows.append(row)
                row, row_width = [], 0
            row.append((label, action, w))
            row_width += w + 6
        if row:
            rows.append(row)
        height = text_rect.height() + 22 + len(rows) * (button_h + 6)
        top = max(2.0, rect_bell.top() - height - 12)
        box = QRectF(8, top, width, height)
        self._bubble_rect = box
        path = QPainterPath()
        path.addRoundedRect(box, 14, 14)
        tail = QPainterPath()
        tx = rect_bell.center().x()
        tail.moveTo(tx - 8, box.bottom() - 1)
        tail.lineTo(tx, box.bottom() + 9)
        tail.lineTo(tx + 8, box.bottom() - 1)
        path = path.united(tail)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawPath(path.translated(0, 3))
        painter.setBrush(QColor(PAPER))
        painter.drawPath(path)
        painter.setPen(QColor(INK))
        painter.setFont(font)
        painter.drawText(QRectF(box.x() + 12, box.y() + 10, width - 24, text_rect.height() + 4),
                         Qt.TextFlag.TextWordWrap, bubble["text"])
        rects = []
        y = box.y() + text_rect.height() + 18
        painter.setFont(mono)
        for row in rows:
            x = box.x() + 12
            for index, (label, action, w) in enumerate(row):
                r = QRectF(x, y, w, button_h)
                primary = index == 0 and len(bubble["buttons"]) > 1 and label not in ("Mute 1 h",)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(INK) if primary else QColor(0, 0, 0, 22))
                painter.drawRoundedRect(r, 11, 11)
                painter.setPen(QColor("#FFFFFF") if primary else QColor(INK))
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, label.upper())
                rects.append((r, action))
                x += w + 6
            y += button_h + 6
        return rects

    def run(self):
        return self.app.exec()


def main():
    if not os.environ.get("WAYLAND_DISPLAY"):
        print("genta-bell needs a Wayland session", file=sys.stderr)
        return 1
    bell = Bell()
    code = bell.run()
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
