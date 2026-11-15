"""COSMIC desktop integration: Genta replaces cosmic-launcher, mac-style floating dock, glass top bar.

COSMIC stores each setting as one RON file under ~/.config/cosmic/<component>/<version>/<key>.
System defaults live in /usr/share/cosmic. We only write user overrides, and back up any
user file we replace, so ``--uninstall`` restores the exact previous state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

HOME = Path.home()
USER = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "cosmic"
SYSTEM = Path("/usr/share/cosmic")
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "genta-launcher"
BACKUP = STATE_DIR / "cosmic-backup"
MANIFEST = STATE_DIR / "cosmic-manifest.json"

SHORTCUTS = "com.system76.CosmicSettings.Shortcuts/v1"
DOCK = "com.system76.CosmicPanel.Dock/v1"
PANEL = "com.system76.CosmicPanel.Panel/v1"
THEME_KEYS = [f"{component}/v2/{key}" for component in ("com.system76.CosmicTheme.Dark", "com.system76.CosmicTheme.Dark.Builder")
              for key in ("frosted_panel",)]
APPLET_ID = "dev.genta.CosmicAppletGenta"
APPLET_DESKTOP = HOME / ".local/share/applications" / f"{APPLET_ID}.desktop"


def applet_binary(repo):
    return repo / "cosmic-applet/target/release/genta-applet"


def applet_built(repo):
    return applet_binary(repo).is_file()


def install_applet(repo):
    """Register the Genta panel applet (build it first: cargo build --release in cosmic-applet/)."""
    if not applet_built(repo):
        print("COSMIC: Genta clock applet not built; keeping COSMIC's clock. Build: cd cosmic-applet && cargo build --release")
        return
    APPLET_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    APPLET_DESKTOP.write_text(
        "[Desktop Entry]\nType=Application\nName=Genta clock\n"
        "Comment=Time, date, weather and Genta focus timer\n"
        f"Exec={applet_binary(repo)}\nIcon={repo / 'genta' / 'genta.svg'}\nTerminal=false\n"
        "Categories=COSMIC;\nKeywords=COSMIC;Applet;Genta;Time;Weather;\nNoDisplay=true\n"
        "X-CosmicApplet=true\nX-HostWaylandDisplay=true\n")
    print("COSMIC: Genta clock applet installed in the top bar centre (replaces COSMIC's clock).")


def settings(repo):
    command = f"{repo / 'bin/genta-launcher'} launcher"
    actions = (SYSTEM / SHORTCUTS / "system_actions").read_text()
    if 'Launcher: "cosmic-launcher"' not in actions:
        raise SystemExit("Unexpected COSMIC system_actions format; not changing the launcher key.")
    values = {
        # Super, Super+/ and the panel launcher action now open Genta.
        f"{SHORTCUTS}/system_actions": actions.replace('Launcher: "cosmic-launcher"', f'Launcher: "{command}"'),
        # cosmic-comp only reloads system_actions on restart; custom shortcuts apply live and win over defaults.
        f"{SHORTCUTS}/custom": "{\n"
            f'    (modifiers: [Super]): Spawn("{command}"),\n'
            f'    (modifiers: [Super], key: "slash"): Spawn("{command}"),\n'
            f'    (modifiers: [Super], key: "space"): Spawn("{command}"),\n'
            "}\n",
        # Mac-style dock: floating, rounded, translucent, apps then minimized windows.
        f"{DOCK}/anchor_gap": "true",
        f"{DOCK}/margin": "10",
        f"{DOCK}/padding": "6",
        f"{DOCK}/spacing": "2",
        f"{DOCK}/border_radius": "22",
        f"{DOCK}/opacity": "0.72",
        f"{DOCK}/expand_to_edges": "false",
        f"{DOCK}/size": "L",
        f"{DOCK}/plugins_center": 'Some(["com.system76.CosmicPanelAppButton", "com.system76.CosmicAppList", "com.system76.CosmicAppletMinimize"])',
        # Glass top bar; "Applications" is replaced by Genta (Super), no logo.
        f"{PANEL}/opacity": "0.6",
        # Genta clock (time · date · weather · focus) replaces COSMIC's centre clock.
        f"{PANEL}/plugins_center": f'Some(["{APPLET_ID}"])' if applet_built(repo) else (SYSTEM / PANEL / "plugins_center").read_text(),
        f"{PANEL}/plugins_wings": (SYSTEM / PANEL / "plugins_wings").read_text().replace(
            '["com.system76.CosmicPanelWorkspacesButton", "com.system76.CosmicPanelAppButton"]',
            '["com.system76.CosmicPanelWorkspacesButton"]'),
    }
    for key in THEME_KEYS:
        values[key] = "true"  # COSMIC native blur for the top bar and dock
    return values


def install(repo):
    install_applet(repo)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    for key, value in settings(repo).items():
        target = USER / key
        if key not in manifest:  # first time: remember what was there
            if target.exists():
                saved = BACKUP / key
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                manifest[key] = "backup"
            else:
                manifest[key] = "absent"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print("COSMIC: Super opens Genta (cosmic-launcher is no longer bound).")
    print("COSMIC: floating mac-style dock and glass top bar applied; the Genta pill is in the status area.")


def uninstall():
    if not MANIFEST.exists():
        return
    for key, kind in json.loads(MANIFEST.read_text()).items():
        target = USER / key
        if kind == "backup" and (BACKUP / key).exists():
            shutil.copy2(BACKUP / key, target)
        else:
            target.unlink(missing_ok=True)
    MANIFEST.unlink()
    shutil.rmtree(BACKUP, ignore_errors=True)
    APPLET_DESKTOP.unlink(missing_ok=True)
    print("COSMIC: launcher key, dock, top bar and frosted settings restored.")
