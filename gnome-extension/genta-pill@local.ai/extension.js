import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const LAUNCHER_TITLE = 'Genta launcher';
const MODES = ['running', 'paused', 'idle', 'offline'];

// Reads Genta's tiny status file and talks to Genta's local socket. No other data is accessed.
export default class GentaPill extends Extension {
    enable() {
        const runtime = GLib.get_user_runtime_dir();
        this._statusFile = Gio.File.new_for_path(GLib.build_filenamev([runtime, 'genta-status.json']));
        this._socketPath = GLib.build_filenamev([runtime, 'genta.sock']);
        this._decoder = new TextDecoder();
        this._cancellable = new Gio.Cancellable();
        this._idles = new Set();

        this._button = new PanelMenu.Button(0.0, 'Genta', true);
        this._box = new St.BoxLayout({style_class: 'genta-pill offline', y_align: Clutter.ActorAlign.CENTER});
        this._dot = new St.Widget({style_class: 'genta-pill-dot', y_align: Clutter.ActorAlign.CENTER});
        this._label = new St.Label({text: 'GENTA', style_class: 'genta-pill-label', y_align: Clutter.ActorAlign.CENTER});
        this._label.clutter_text.ellipsize = 3; // Pango.EllipsizeMode.END
        this._box.add_child(this._dot);
        this._box.add_child(this._label);
        this._button.add_child(this._box);
        this._button.connect('button-press-event', (actor, event) => this._clicked(event.get_button()));
        Main.panel.addToStatusArea(this.uuid, this._button, 1, 'center');

        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._refresh();

        // XWayland focus-stealing prevention can leave the launcher without keyboard focus.
        this._windowCreated = global.display.connect('window-created', (display, window) => {
            const id = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._idles.delete(id);
                if (window.get_wm_class() === 'Genta' && window.get_title() === LAUNCHER_TITLE)
                    Main.activateWindow(window);
                return GLib.SOURCE_REMOVE;
            });
            this._idles.add(id);
        });
    }

    _clicked(button) {
        const command = button === 3 ? 'focus_toggle' : button === 2 ? 'toggle' : 'launcher';
        this._send(command);
        return Clutter.EVENT_STOP;
    }

    _send(command) {
        const client = new Gio.SocketClient();
        client.connect_async(Gio.UnixSocketAddress.new(this._socketPath), this._cancellable, (source, result) => {
            let connection;
            try {
                connection = source.connect_finish(result);
            } catch (error) {
                if (command !== 'focus_toggle')
                    this._startGenta(command === 'launcher' ? ['--launcher'] : []);
                return;
            }
            const stream = connection.get_output_stream();
            stream.write_all_async(new TextEncoder().encode(command), GLib.PRIORITY_DEFAULT, this._cancellable, (out, res) => {
                try {
                    out.write_all_finish(res);
                } catch (error) {
                    console.warn(`Genta pill: ${error.message}`);
                }
                connection.close_async(GLib.PRIORITY_DEFAULT, null, null);
            });
        });
    }

    _startGenta(flags) {
        const genta = GLib.find_program_in_path('genta') ||
            GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'genta']);
        try {
            Gio.Subprocess.new([genta, ...flags], Gio.SubprocessFlags.NONE);
        } catch (error) {
            Main.notifyError('Genta', `Could not start Genta: ${error.message}`);
        }
    }

    _refresh() {
        this._statusFile.load_contents_async(this._cancellable, (file, result) => {
            let status = null;
            try {
                const [, contents] = file.load_contents_finish(result);
                status = JSON.parse(this._decoder.decode(contents));
                if (!Number.isInteger(status.pid) || !GLib.file_test(`/proc/${status.pid}`, GLib.FileTest.EXISTS))
                    status = null;
            } catch (error) {
                if (error instanceof GLib.Error && error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                    return;
                status = null;
            }
            this._show(status);
        });
    }

    _show(status) {
        if (!this._box)
            return;
        const mode = status && MODES.includes(status.mode) ? status.mode : 'offline';
        const text = typeof status?.text === 'string' ? status.text : '';
        for (const name of MODES)
            this._box.remove_style_class_name(name);
        this._box.add_style_class_name(mode);
        this._label.text = mode === 'offline' ? 'GENTA' : text || 'GENTA';
    }

    disable() {
        this._cancellable?.cancel();
        this._cancellable = null;
        if (this._timer)
            GLib.source_remove(this._timer);
        this._timer = 0;
        for (const id of this._idles ?? [])
            GLib.source_remove(id);
        this._idles = null;
        if (this._windowCreated)
            global.display.disconnect(this._windowCreated);
        this._windowCreated = 0;
        this._button?.destroy();
        this._button = this._box = this._dot = this._label = null;
        this._statusFile = this._decoder = null;
    }
}
