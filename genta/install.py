#!/usr/bin/env python3
"""Install per-user desktop entry, launcher, and non-destructive GNOME shortcut."""
import os
from pathlib import Path
import sys

from genta import ROOT, desktop_entry


def main():
    home = Path.home()
    applications = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "applications"
    binaries = home / ".local/bin"
    applications.mkdir(parents=True, exist_ok=True)
    binaries.mkdir(parents=True, exist_ok=True)
    launcher = binaries / "genta"
    target = ROOT / "genta"
    target.chmod(0o755)
    if launcher.exists() or launcher.is_symlink():
        if not launcher.is_symlink() or launcher.resolve() != target:
            raise SystemExit(f"Refusing to replace an unrelated launcher: {launcher}")
    else:
        launcher.symlink_to(target)
    desktop = applications / "genta.desktop"
    if desktop.exists() and desktop.read_text() != desktop_entry():
        raise SystemExit(f"Refusing to overwrite an unrelated desktop entry: {desktop}")
    desktop.write_text(desktop_entry(), encoding="utf-8")
    print(f"Installed launcher: {launcher}\nInstalled desktop entry: {desktop}")
    if "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        from gi.repository import Gio
        bridge = home / ".local/share/gnome-shell/extensions/genta-bridge@local.ai"
        bridge.mkdir(parents=True, exist_ok=True)
        for name in ("metadata.json", "extension.js"):
            source = (ROOT / "gnome-bridge" / name).read_text()
            destination = bridge / name
            if destination.exists() and destination.read_text() != source:
                raise SystemExit("Existing Genta GNOME bridge differs; leaving it unchanged for manual review.")
            destination.write_text(source)
        shell = Gio.Settings.new("org.gnome.shell")
        enabled = list(shell.get_strv("enabled-extensions"))
        if "genta-bridge@local.ai" not in enabled:
            shell.set_strv("enabled-extensions", enabled + ["genta-bridge@local.ai"])
        print("GNOME app-identity bridge installed. Sign out and back in once so GNOME discovers it; app nudges require this activation.")
        media = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
        schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
        path = "/org/gnome/settings-daemon/media-keys/custom-keybindings/genta/"
        paths = list(media.get_strv("custom-keybindings"))
        for existing in paths:
            binding = Gio.Settings.new_with_path(schema, existing)
            if existing != path and binding.get_string("binding").lower() in {"<alt>n", "<mod1>n"}:
                print(f"Alt+N already assigned to {binding.get_string('name')}; leaving it unchanged.")
                print(f"Choose a different shortcut in GNOME Settings for: {launcher} --toggle")
                return 0
        binding = Gio.Settings.new_with_path(schema, path)
        if path in paths and binding.get_string("command") not in {"", f"{launcher} --toggle"}:
            raise SystemExit("Refusing to replace an unrelated shortcut at Genta's settings path")
        binding.set_string("name", "Genta")
        binding.set_string("command", f"{launcher} --toggle")
        binding.set_string("binding", "<Alt>n")
        if path not in paths:
            media.set_strv("custom-keybindings", paths + [path])
        Gio.Settings.sync()
        print("Installed GNOME Alt+N shortcut. Existing shortcuts preserved.")
    else:
        print(f"Add a desktop shortcut for: {launcher} --toggle")
    print("Run genta, or open Genta from your application launcher. Autostart is opt-in in Settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
