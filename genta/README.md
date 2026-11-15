# Genta

Genta means bell/chime: a Nusantara-inspired reminder to return to what matters. Independent productivity software for **Windows, Linux, and native Android**, inspired by [NotchOwl's published behavior](https://www.notchowl.com), with original code and a monochrome/red dot-matrix interface.

## Install and run

### CachyOS / Arch

```sh
sudo pacman -S python pyside6 python-gobject xorg-xwayland libnotify
cd ~/Desktop/Genta
./install.sh
./genta
```

Dependencies live in `.venv`, not system Python. The launcher uses that environment. Installation preserves existing GNOME shortcuts and adds **Alt+N** when available. Keep this source directory in place. Autostart stays off until enabled in Settings.

GNOME Wayland uses XWayland for positioned top-edge windows. Each connected monitor can have its own pill; hovering another pill moves the workspace there. Pin keeps the workspace open. Escape and Alt+N collapse/open it. Quit from Settings, the tray menu, or `genta --quit`.

**GNOME distraction nudges:** installation adds `genta-bridge@local.ai`. GNOME must discover a newly installed local extension after **signing out and back in once**. The extension exposes only the focused app ID—not titles, keystrokes, clipboard contents, or files. No shell restart or forced logout is performed. Enable nudges and list distracting app names in Genta Settings afterward. X11 and Hyprland use their native identity APIs.

### Windows

Install Python 3.12 or later and copy the source folder, excluding `.venv`, `.tools`, and `android/build` (which contains private development/build state), then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

The installer creates a private environment and a per-user Start-menu shortcut. `genta.cmd` opens the app. **Alt+N** is registered only while Genta runs, and only if another app does not own it. Autostart is optional. Windows code is supplied; this workstation cannot validate Windows-specific UI, WinRT media sessions, or shortcut behavior.

### Android

Native Java companion, Android 8/API 26 or newer. Source is under `android/`; the built, signature-verified APK is `dist/genta-android.apk`. Rebuild with the private Android SDK under `.tools/android`:

```sh
./android/build.sh
```

The APK is signed with a private, local **development key**, not a Play Store release key. Install it on your own device with your normal APK installation flow. The app provides offline tasks and notes, task repeats/deadlines/projects, speech capture through Android's speech service, reminders with Snooze/Done, focus timing, a home-screen task widget, share-to-task, launcher voice/focus shortcuts, device sync, and SimpMusic media controls.

Grant notification permission for reminders. Grant **Alarms and reminders** access for precise times; without it Android can deliver reminders later. Background sync runs approximately every 15 minutes, subject to battery policy; foreground/manual sync is immediate. SimpMusic controls require explicit Android notification-access permission. No such permission is enabled automatically.

## Plan and capture

- Type tasks naturally: `Call Sam tomorrow at 5pm`. Trailing English date/time phrases are supported; typed natural-language parsing is not translated into 25 grammars.
- **Speak** records only after pressing Record. Review the Hermes transcript before adding the task. Hermes's configured speech provider may upload the recording; no microphone records in the background.
- Today includes overdue tasks. Use task **··· → Task details** for a project, deadline, reminder, estimate, or daily/weekdays/weekly/monthly repeat. Completing a repeating occurrence creates the next occurrence once.
- **Plan your day** places today's untimed tasks into free slots around cached calendar events and existing timed tasks, up to the configured end hour. It reports work that did not fit rather than inventing free time.
- **Wrap up** explicitly moves unfinished tasks to Tomorrow or Inbox and clears their obsolete reminder times.
- Week contains seven days and an Inbox. Reschedule through task actions.
- Daily notes autosave. Ctrl+Enter turns selected text/current line into a task without removing the note. History retains earlier local and synchronized note versions.
- Due reminders queue in the workspace. Done completes the task; Snooze moves its reminder five minutes forward. Desktop reminders require the app to remain running.

## Focus

Countdown or stopwatch, task linking, pause/resume, add five minutes, finish. Active-time clocks exclude system suspend on Windows/Linux. Checkpoints every 30 seconds limit crash loss; restored sessions are paused.

Four **original synthesized** soundscapes—brown noise, pink noise, rain, ocean—are included as PCM WAV loops. Silence is the default. The volume control uses a squared gain curve with headroom. Android sound playback is limited to its active Focus screen; its reminder alarm can complete the countdown in the background.

Optional start/end hooks are **JSON argument arrays**, for example `["program", "argument"]`. No shell is invoked implicitly. Hooks run on a new session and once on completion/finish, not on every pause/resume. You choose executable commands; do not paste untrusted ones.

Insights use actual recorded completions and focus seconds, with streaks, seven-day history and best focus hours. No sample productivity figures are shipped. The compact pill retains the running timer. Settings opens a draggable desktop glance widget.

## Calendar, calls and travel

Connections are optional and read-only:

- Google Calendar: **Settings → Integrate calendar → Secret address in iCal format**.
- Outlook: **Calendar settings → Shared calendars → Publish calendar → ICS**.
- Calendly: personal access token; [official events API](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/list-scheduled-events).
- Cal.com: API key; [official bookings API](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings).

Add feeds/keys through Calendar → Connect calendars. Private feed URLs and provider keys stay in the operating-system keyring, not planner exports. Two weeks of recurring ICS events and supported meeting URLs are shown. Join opens the actual meeting URL. Failed refreshes explicitly label cached data as potentially stale.

**Time to leave** uses live [Google Routes](https://developers.google.com/maps/documentation/routes) driving estimates. Set a starting address and Routes API key in Settings, then click Time to leave on a physical-location event. The origin/destination leave the device only for that request; Maps may charge your account. The calculated departure time includes the configured buffer. This is not an invented travel-time estimate.

Camera look opens an off-by-default local preview. Press Start preview to activate the camera; close the window or press Stop to release it. No video is recorded or sent.

Account-backed Google/Outlook feeds, Calendly, Cal.com and Routes need **your credentials/feed URLs** before live account verification is possible.

## SimpMusic

The Music tab shows real title/artist/album/artwork and supported playback controls. Linux uses SimpMusic's **MPRIS** service; Windows uses its **system media session**. Android uses authorized media-session callbacks after you grant notification access, without repeatedly rebuilding the view on a timer. Controls are not simulated; unavailable controls are disabled or the app explicitly reports an absent session.

SimpMusic must be installed separately and expose an active media session. Open SimpMusic starts the installed app. A stopped session can have no track metadata or artwork until you play a track.

## Sync and device pairing

Choose **one reachable desktop as your hub** in Settings → Pair Android / desktop. Enable the hub on one explicit LAN interface. Copy its protected pairing JSON to another desktop or the Android companion. This includes a random token and SHA-256 certificate fingerprint; never post it publicly.

- HTTPS peer identity is pinned **before** credentials or planner data are sent.
- Desktop pairing tokens use the OS keyring; Android encrypts its token with Android Keystore.
- Planner changes are immutable, idempotent synchronization events.
- Independent task fields merge with per-field Lamport clocks. Deleted task tombstones prevent stale edits from reviving deleted work.
- Concurrent note versions are retained for recovery.
- Offline edits queue locally. The hub must become reachable to exchange them. There is no hosted relay, automatic port forwarding, or advertised internet-wide sync.

Only tasks, daily notes and recorded focus activity synchronize. Clipboard/screenshot history, executable hooks, API keys and running focus control do not. Calendar accounts are configured per desktop.

## Hermes and MCP

Hermes owns provider authentication; Genta does not copy `.env`, OAuth credentials or provider keys. Start the loopback proxy when needed:

```sh
hermes proxy start --provider nous --host 127.0.0.1 --port 8645
```

Hermes tab sends your question to the configured model. Including today's planner context is opt-in. Replies are suggestions; select text and explicitly create a task to apply one. The bridge uses inference, not a full agent with auto-approved terminal tools.

Speech uses Hermes's published, isolated runtime command and its existing transcription implementation. Configure it with `hermes tools` if needed. Non-default providers depend on what your installed Hermes proxy/speech runtime supports.

Settings can connect Claude/Cursor or copy a stdio MCP configuration. The server command is:

```sh
.venv/bin/python genta_cli.py mcp
```

MCP tools manage tasks, notes, insights, planning/wrap-up and the running desktop focus timer. No public MCP port is opened. CLI automation is also available:

```sh
.venv/bin/python genta_cli.py add "Review the plan tomorrow at 9am"
.venv/bin/python genta_cli.py focus-start --seconds 1500
.venv/bin/python genta_cli.py focus-pause
```

## Clips, languages and privacy

Text clips and screenshot capture are **off by default**. Text history is memory-only, capped at 20 entries; disabling clears it. Screenshot capture watches only the folder you choose and only new image files. Sensitive clipboard text/images can be captured if you enable these features. Capture is never represented as system-wide access that the compositor does not grant.

English is the default. All **25 UI catalogs** contain the same **289 source strings**, shared with Android. Dates use the selected desktop locale; Arabic uses right-to-left layout. Translations are machine-assisted and need human linguistic review: initial catalogs used Hermes, with missing strings completed through Google's public text-translation endpoint. The development generator sends static UI source strings only, never personal planner data. Source/provider/system errors can remain in their original language.

Data lives in `$XDG_DATA_HOME/genta/genta.sqlite3` on Linux and `%LOCALAPPDATA%\genta\genta.sqlite3` on Windows. Dates use **local, naive ISO timestamps**; existing reminders are not automatically translated when changing time zones. Android stores private offline app data. Backups/export contain personal planner content; keep them private. Exports do not contain clipboard history, account keys or pairing tokens.

## Visual asset status

The interface uses original code-authored dot glyphs, near-black/off-white panels and a red accent. No proprietary Nothing font, logo or phone assets are copied.

**Artcraft logo, favicon and animated sprite are explicitly on hold at your request.** Live estimates were 22 credits for the logo master and 16 for an eight-frame sprite sheet. **No paid job was submitted.** Existing icon assets remain in use; no placeholder is labeled as an Artcraft output. `assets/manifest.json` records the brief, estimate, provenance and hold.

## Verification

```sh
.venv/bin/python -m unittest test_store test_focus test_notes test_expansion test_voice test_lifecycle -v
```

The 22 isolated tests cover persistence, natural dates, note conversion, focus accounting, recurrence, planning capacity, note-version preservation, tombstones, idempotent focus sync, recurring ICS/Join parsing, real pinned-TLS sync, cancelled-recording deletion and explicit Quit. Normal window close still collapses the workspace.

Actual Linux-window scenarios exercised typed tasks, Plan/Wrap, task details, note conversion/history, snooze, focus accounting/hooks, all four muted audio streams, screenshot capture, glance, hover, all 25 locale layouts, Calendar Join/Maps controls and the live SimpMusic MPRIS session. Hermes inference, Hermes speech transcription and stdio MCP were exercised against real processes. Missing travel credentials produce a setup instruction, not a fabricated departure time.

The signed APK was installed and opened on an Android 16/API 36 emulator. Native task/note creation and pairing exchanged real data bidirectionally with a Linux SQLite hub over pinned HTTPS; Android's media-permission-off state was exercised. Windows-specific execution, real phone speech/notification/media permissions and physical camera activation still require those devices/actions. GNOME nudges require the installed bridge to activate after the next login. Do not treat source/build or emulator verification as physical-device verification.

## Uninstall

Quit Genta. Remove its launcher, desktop/Start-menu shortcut and optional autostart entry. On GNOME, remove the Genta keyboard shortcut and disable/remove `genta-bridge@local.ai`. Remove only Genta's own files; your database and keyring entries remain unless you explicitly remove them.

## Changelog

### 0.2.0

Windows/Linux boundaries and native Android companion; opt-in pinned sync; repeats, deadlines, projects and free-slot planning; wrap-up; versioned notes; original focus soundscapes and session hooks; read-only calendar/booking connectors, Join, Routes and camera preview; SimpMusic widget; screenshot clips, monitor pills, desktop/Android widgets; Hermes inference/STT; stdio MCP; shared localization; original dot-matrix theme. Artcraft paid assets remain user-held.

Explicit Quit now closes the event loop rather than being vetoed by collapse-on-close. Cancelling a voice dialog deletes its temporary recording. Android music updates follow media-session events, preserve track metadata, and honor transport capabilities.

### 0.1.0

Initial independent Linux planner, focus timer, notes, reminders, insights and optional text clips.

