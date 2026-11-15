#!/usr/bin/env python3
"""Install or remove the Genta launcher integration for GNOME (replaces rofi).

  ./install.py              install (idempotent)
  ./install.py --uninstall  restore the settings saved at install time

Changes made, all per-user:
  * top-bar pill extension  ~/.local/share/gnome-shell/extensions/genta-pill@local.ai (symlink)
  * Super+Space             opens Genta; rofi shortcuts are removed (Genta replaces rofi)
  * Blur My Shell           blurs only the window titled "Genta launcher"
  * Genta autostart         starts in the background (no workspace pop-up at sign-in)
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

REPO = Path(__file__).resolve().parent
HOME = Path.home()
UUID = "genta-pill@local.ai"
EXTENSION_LINK = HOME / ".local/share/gnome-shell/extensions" / UUID
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "genta-launcher/install-state.json"
KEYBINDING_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/genta-launcher/"
BLUR_SCHEMA = "org.gnome.shell.extensions.blur-my-shell.applications"
BLUR_RULE = "title:Genta launcher"
BLUR_KEYS = ("blur", "dynamic-opacity", "opacity", "whitelist")
ROFI_DIR = HOME / ".config/rofi"
AUTOSTART = HOME / ".config/autostart/genta.desktop"


def settings(schema, path=None):
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema, True) is None:
        return None
    return Gio.Settings.new_with_path(schema, path) if path else Gio.Settings.new(schema)


def binding_at(path):
    return Gio.Settings.new_with_path(KEYBINDING_SCHEMA, path)


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def install_extension(state):
    target = REPO / "gnome-extension" / UUID
    if EXTENSION_LINK.is_symlink() and EXTENSION_LINK.resolve() == target:
        pass
    elif EXTENSION_LINK.exists() or EXTENSION_LINK.is_symlink():
        sys.exit(f"Refusing to replace {EXTENSION_LINK}; remove it manually first.")
    else:
        EXTENSION_LINK.parent.mkdir(parents=True, exist_ok=True)
        EXTENSION_LINK.symlink_to(target)
    shell = Gio.Settings.new("org.gnome.shell")
    enabled = list(shell.get_strv("enabled-extensions"))
    if UUID not in enabled:
        shell.set_strv("enabled-extensions", enabled + [UUID])
    disabled = list(shell.get_strv("disabled-extensions"))
    if UUID in disabled:
        shell.set_strv("disabled-extensions", [x for x in disabled if x != UUID])
    print(f"Top-bar pill extension linked: {EXTENSION_LINK}")


def install_shortcut(state):
    media = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
    paths = list(media.get_strv("custom-keybindings"))
    command = f"{REPO / 'bin/genta-launcher'} launcher"
    for path in paths:
        if path == KEYBINDING_PATH:
            continue
        other = binding_at(path)
        if other.get_string("binding").lower() == "<super>space" and "rofi" not in other.get_string("command"):
            print(f"Super+Space belongs to “{other.get_string('name')}”; leaving it. Bind {command} manually.")
            return
    binding = binding_at(KEYBINDING_PATH)
    binding.set_string("name", "Genta")
    binding.set_string("command", command)
    binding.set_string("binding", "<Super>space")
    if KEYBINDING_PATH not in paths:
        media.set_strv("custom-keybindings", paths + [KEYBINDING_PATH])
    free_input_switch(state)
    print("Super+Space opens Genta.")


def remove_rofi(state):
    """Genta replaces rofi: drop rofi shortcuts and Genta's rofi theme link (saved for --uninstall)."""
    media = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
    paths = list(media.get_strv("custom-keybindings"))
    kept = []
    for path in paths:
        entry = binding_at(path)
        if path != KEYBINDING_PATH and "rofi" in entry.get_string("command"):
            saved = {key: entry.get_string(key) for key in ("name", "command", "binding")}
            if state.get("rofi_binding", {}).get("path") == path:
                saved["binding"] = state["rofi_binding"]["binding"]  # its original key before install
            state.setdefault("rofi_entries", {})[path] = saved
            for key in ("name", "command", "binding"):
                entry.reset(key)
            print(f"Removed rofi shortcut “{saved['name']}”.")
        else:
            kept.append(path)
    if kept != paths:
        media.set_strv("custom-keybindings", kept)
    config, link = ROFI_DIR / "config.rasi", ROFI_DIR / "glass.rasi"
    if "rofi_theme" in state and config.exists():
        config.write_text(config.read_text().replace(str(link), state.pop("rofi_theme")))
    if link.is_symlink() and REPO in link.resolve().parents:
        link.unlink()
    if shutil.which("rofi"):
        print("rofi is still installed. Remove the package with:  sudo pacman -Rns rofi")
        print(f"(Your rofi config in {ROFI_DIR} is left untouched; delete it if you no longer want it.)")


def free_input_switch(state):
    """GNOME's input-source switcher grabs Super+Space first. Free it only with a single layout."""
    wm = Gio.Settings.new("org.gnome.desktop.wm.keybindings")
    current = list(wm.get_strv("switch-input-source"))
    sources = Gio.Settings.new("org.gnome.desktop.input-sources").get_value("sources").unpack()
    if "<Super>space" in current:
        if len(sources) > 1:
            print("Super+Space also switches keyboard layouts; remove it in Settings → Keyboard if the launcher doesn't open.")
            return
        state.setdefault("switch_input_source", current)
        wm.set_strv("switch-input-source", [key for key in current if key != "<Super>space"])
        print("Removed Super+Space from layout switching (only one layout is configured).")


def install_blur(state):
    blur = settings(BLUR_SCHEMA)
    if blur is None:
        print("Blur My Shell not found; the launcher stays translucent without background blur.")
        return
    state.setdefault("blur", {key: blur.get_value(key).unpack() for key in BLUR_KEYS})
    whitelist = list(blur.get_strv("whitelist"))
    if BLUR_RULE not in whitelist:
        blur.set_strv("whitelist", whitelist + [BLUR_RULE])
    blur.set_boolean("blur", True)
    # The launcher paints its own tint; dynamic opacity would hide the blur while it is focused.
    blur.set_boolean("dynamic-opacity", False)
    blur.set_int("opacity", 255)
    print("Blur My Shell: blur enabled for the launcher window only (whitelist).")


def install_autostart(state):
    if not AUTOSTART.exists():
        return
    lines = AUTOSTART.read_text().splitlines()
    changed = False
    for number, line in enumerate(lines):
        if line.startswith("Exec=") and "genta" in line and "--background" not in line:
            state.setdefault("autostart_exec", line)
            lines[number] = line + " --background"
            changed = True
    if changed:
        AUTOSTART.write_text("\n".join(lines) + "\n")
        print("Genta autostart now starts in the background.")


def on_cosmic():
    return "COSMIC" in os.environ.get("XDG_CURRENT_DESKTOP", "")


def install():
    if on_cosmic():
        import cosmic_setup
        os.chmod(REPO / "bin/genta-launcher", 0o755)
        state = load_state()
        install_autostart(state)
        save_state(state)
        cosmic_setup.install(REPO)
        print("Restart Genta (genta --quit, then genta --background) to load the new launcher.")
        return
    os.chmod(REPO / "bin/genta-launcher", 0o755)
    state = load_state()
    install_extension(state)
    install_shortcut(state)
    install_blur(state)
    remove_rofi(state)
    install_autostart(state)
    save_state(state)
    print(f"\nSaved previous settings to {STATE}.")
    print("Restart Genta (genta --quit, then genta --background) to load the new launcher.")
    print("Sign out and back in once so GNOME Shell loads the new top-bar pill extension.")


def uninstall():
    import cosmic_setup
    cosmic_setup.uninstall()
    state = load_state()
    if on_cosmic() or settings("org.gnome.shell") is None:
        if "autostart_exec" in state and AUTOSTART.exists():
            lines = [state["autostart_exec"] if line.startswith("Exec=") else line for line in AUTOSTART.read_text().splitlines()]
            AUTOSTART.write_text("\n".join(lines) + "\n")
        print("GNOME is not running; its saved settings were left for a GNOME session.")
        return
    shell = Gio.Settings.new("org.gnome.shell")
    shell.set_strv("enabled-extensions", [x for x in shell.get_strv("enabled-extensions") if x != UUID])
    if EXTENSION_LINK.is_symlink():
        EXTENSION_LINK.unlink()
    media = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
    media.set_strv("custom-keybindings", [p for p in media.get_strv("custom-keybindings") if p != KEYBINDING_PATH])
    binding = binding_at(KEYBINDING_PATH)
    for key in ("name", "command", "binding"):
        binding.reset(key)
    entries = state.get("rofi_entries", {})
    for path, saved in entries.items():
        entry = binding_at(path)
        for key, value in saved.items():
            entry.set_string(key, value)
    if entries:
        current = list(media.get_strv("custom-keybindings"))
        media.set_strv("custom-keybindings", current + [path for path in entries if path not in current])
    if "switch_input_source" in state:
        Gio.Settings.new("org.gnome.desktop.wm.keybindings").set_strv("switch-input-source", state["switch_input_source"])
    blur = settings(BLUR_SCHEMA)
    if blur is not None and "blur" in state:
        saved = state["blur"]
        blur.set_strv("whitelist", [x for x in saved["whitelist"] if x != BLUR_RULE])
        blur.set_boolean("blur", saved["blur"])
        blur.set_boolean("dynamic-opacity", saved["dynamic-opacity"])
        blur.set_int("opacity", saved["opacity"])
    if "autostart_exec" in state and AUTOSTART.exists():
        lines = [state["autostart_exec"] if line.startswith("Exec=") else line for line in AUTOSTART.read_text().splitlines()]
        AUTOSTART.write_text("\n".join(lines) + "\n")
    STATE.unlink(missing_ok=True)
    print("Genta launcher integration removed; earlier settings (including rofi shortcuts) restored.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true")
    uninstall() if parser.parse_args().uninstall else install()
