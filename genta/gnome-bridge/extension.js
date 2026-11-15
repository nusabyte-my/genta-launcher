import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const XML = `<node><interface name="ai.genta.Desktop"><method name="ActiveApplication"><arg type="s" direction="out"/></method></interface></node>`;

// Deliberately expose only app identity. No titles, keystrokes, clipboard or files.
export default class GentaBridge extends Extension {
    enable() {
        this._export = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._export.export(Gio.DBus.session, '/ai/genta/Desktop');
        this._name = Gio.bus_own_name_on_connection(Gio.DBus.session, 'ai.genta.Desktop', Gio.BusNameOwnerFlags.NONE, null, null);
    }
    ActiveApplication() {
        const window = global.display.focus_window;
        if (!window)
            return '';
        const app = Shell.WindowTracker.get_default().get_window_app(window);
        return app?.get_id() || window.get_wm_class() || '';
    }
    disable() {
        this._export?.unexport();
        this._export = null;
        if (this._name)
            Gio.bus_unown_name(this._name);
        this._name = 0;
    }
}
