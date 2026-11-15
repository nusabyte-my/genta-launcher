"""Extra result rows: open windows (switcher) and a safe calculator."""
from __future__ import annotations

import ast
import math
import operator
import re

from .apps import App, match_score

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10,
          "ln": math.log, "abs": abs, "round": round}
_LOOKS_MATH = re.compile(r"^[\d\s.+\-*/%^()a-z,]+$")


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(arg) for arg in node.args])
    raise ValueError("unsupported")


def calculate(text):
    """Return a formatted result for arithmetic like '2*19.5', '(3+4)^2', 'sqrt(2)', or None."""
    query = text.strip().lower().replace("×", "*").replace("÷", "/")
    if not query or not _LOOKS_MATH.match(query) or not re.search(r"\d", query) or not re.search(r"[+\-*/%^(]", query):
        return None
    try:
        value = _eval(ast.parse(query.replace("^", "**"), mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return f"{value:,.10g}" if abs(value) < 1e15 else f"{value:.6e}"
    return f"{value:,}"


def calculator_row(text):
    result = calculate(text)
    if result is None:
        return None
    return App(id="calc:", path=result.replace(",", ""), name=f"= {result}", generic="", keywords=(), executable="",
               comment="Enter copies the result", icon_names=("accessories-calculator", "org.gnome.Calculator"), icon_path="")


SETTINGS_PAGES = {
    "wireless": ("Wi-Fi", "wifi wireless network internet"), "bluetooth": ("Bluetooth", "bluetooth headphones"),
    "displays": ("Displays", "display monitor screen resolution brightness night light"),
    "sound": ("Sound", "sound audio volume speaker microphone"), "power": ("Power & battery", "power battery sleep"),
    "appearance": ("Appearance", "appearance theme dark light accent"), "wallpaper": ("Wallpaper", "wallpaper background"),
    "dock": ("Dock", "dock taskbar"), "panel": ("Top bar", "panel top bar"), "keyboard": ("Keyboard", "keyboard shortcuts layout"),
    "touchpad": ("Touchpad", "touchpad trackpad gestures"), "mouse": ("Mouse", "mouse cursor"),
    "network": ("Network", "network ethernet proxy"), "vpn": ("VPN", "vpn"), "date-time": ("Date & time", "date time clock timezone"),
    "users": ("Users", "users account password"), "default-apps": ("Default apps", "default apps browser"),
    "startup-apps": ("Startup apps", "startup autostart login"), "about": ("About this computer", "about system info"),
    "workspaces": ("Workspaces", "workspaces virtual desktops"), "accessibility": ("Accessibility", "accessibility a11y"),
}


def settings_rows(query):
    """COSMIC Settings pages matching the query (typing 'wifi' opens Wi-Fi settings)."""
    query = query.strip().casefold()
    if len(query) < 3:
        return []
    rows = []
    for page, (name, words) in SETTINGS_PAGES.items():
        score = max(match_score(query, name), max(match_score(query, w) for w in words.split()))
        if score >= 60:
            rows.append((score, App(id=f"settings:{page}", path="", name=f"{name} settings", generic="", keywords=(),
                                    executable="", comment="Open in COSMIC Settings", icon_names=("preferences-system", "com.system76.CosmicSettings"),
                                    icon_path="")))
    rows.sort(key=lambda pair: -pair[0])
    return [row for _, row in rows[:3]]


def _mime_icon(path):
    """Theme icon names for a file's type (PDF, image, spreadsheet…), from its name only."""
    try:
        from gi.repository import Gio
        content_type, _ = Gio.content_type_guess(str(path), None)
        icon = Gio.content_type_get_icon(content_type)
        names = tuple(icon.get_names()) if hasattr(icon, "get_names") else ()
        return names + ("text-x-generic",)
    except Exception:
        return ("text-x-generic", "application-octet-stream")


def file_rows(query, limit=12):
    """'/name' → files under your home folder (fd: respects .gitignore, skips hidden), newest first."""
    import shutil
    import subprocess
    from pathlib import Path
    pattern = query.strip()
    if len(pattern) < 2 or not shutil.which("fd"):
        return []
    try:
        output = subprocess.run(["fd", "--max-results", "200", "--max-depth", "8", "--type", "f", "--type", "d",
                                 "--ignore-case", "--fixed-strings", pattern, str(Path.home())],
                                capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    paths = [Path(line) for line in output.splitlines() if line]
    paths.sort(key=lambda p: (pattern.casefold() not in p.name.casefold(), -(p.stat().st_mtime if p.exists() else 0)))
    rows = []
    for path in paths[:limit]:
        folder = str(path.parent).replace(str(Path.home()), "~", 1)
        icon = ("folder",) if path.is_dir() else _mime_icon(path)
        rows.append(App(id="file:", path=str(path), name=path.name, generic="", keywords=(), executable="",
                        comment=folder, icon_names=icon, icon_path=""))
    return rows


def clip_rows(clips, query, limit=10):
    """';' → Genta's clipboard history (only if you turned clips on in Genta)."""
    query = query.strip().casefold()
    rows = []
    for text in clips:
        if query and query not in text.casefold():
            continue
        one_line = " ".join(text.split())
        rows.append(App(id="clip:", path=text, name=one_line[:90] + ("…" if len(one_line) > 90 else ""), generic="",
                        keywords=(), executable="", comment=f"{len(text)} characters · Enter copies",
                        icon_names=("edit-paste", "edit-copy"), icon_path=""))
        if len(rows) >= limit:
            break
    return rows


def window_rows(windows, query, apps):
    """Open windows matching the query, as 'Switch to' rows. Icons come from the matching installed app."""
    query = query.strip().casefold()
    icons = {}
    for app in apps:
        stem = app.id.removesuffix(".desktop")
        icons.setdefault(stem.casefold(), app.icon_names)
        icons.setdefault(app.name.casefold(), app.icon_names)
    rows = []
    for window in windows:
        app_id, title = window.get("app_id", ""), window.get("title", "") or window.get("app_id", "")
        if window.get("active") and not query:
            continue
        score = max(match_score(query, title), match_score(query, app_id)) if query else 1
        if score <= 0:
            continue
        names = icons.get(app_id.casefold()) or (app_id, app_id.casefold(), app_id.rsplit(".", 1)[-1].casefold())
        rows.append((score, App(id=f"window:{window['id']}", path="", name=title, generic="", keywords=(), executable=app_id,
                                comment=f"Switch to window · {app_id}", icon_names=tuple(names), icon_path="")))
    rows.sort(key=lambda pair: -pair[0])
    return [row for _, row in rows[:5]]
