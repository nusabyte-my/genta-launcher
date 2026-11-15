"""Example launcher widget: CPU load, memory and uptime from /proc.

Install: cp examples/widgets/system.py ~/.config/genta-launcher/widgets/
Then in the launcher: ＋ Add widget → Reload widget plugins → System.
"""
from pathlib import Path

from glass_launcher.widgets import LauncherWidget


class SystemWidget(LauncherWidget):
    id = "system"
    title = "SYSTEM"
    description = "CPU load, memory and uptime"

    def __init__(self, context):
        super().__init__(context)
        self.value = self.add_label("", "metric", wrap=False)
        self.detail = self.add_label("", "muted")

    def refresh(self):
        self.tick()

    def tick(self):
        load = Path("/proc/loadavg").read_text().split()[0]
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])
        used = (info["MemTotal"] - info["MemAvailable"]) / 1048576
        total = info["MemTotal"] / 1048576
        hours = int(float(Path("/proc/uptime").read_text().split()[0]) // 3600)
        self.value.setText(f"load {load}")
        self.detail.setText(f"{used:.1f} / {total:.1f} GiB memory · up {hours} h")


WIDGETS = [SystemWidget]
