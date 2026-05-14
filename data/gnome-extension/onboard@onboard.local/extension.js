// SPDX-License-Identifier: GPL-3.0-or-later
//
// Onboard keyboard window helper -- GNOME Shell extension
// =======================================================
//
// GNOME Mutter implements neither `zwlr_layer_shell_v1` nor a
// per-window focus-rule mechanism (KWin's "acceptfocus=false").
// This extension watches for windows whose wm_class is "Onboard" and:
//
//   1. Forces them above other windows (Meta.Window.make_above), and
//   2. Reverts keyboard focus to the previously focused window when
//      they accidentally receive it.
//
// This lets Onboard run on native GNOME Wayland instead of falling back to XWayland.
//
// Compatible with GNOME Shell 45+ (ES module entry point).

import Meta from 'gi://Meta';
import GLib from 'gi://GLib';

const ONBOARD_ID = 'onboard';

// On every enable() we hash our own extension.js bytes and write the
// digest to ~/.cache/onboard/extension-build-id. Onboard's launcher
// reads the marker on the next startup and compares it to the hash of
// the bundled extension.js -- if they differ, the running gnome-shell
// is still executing a *previous* build of this extension. gnome-shell
// on Wayland imports caches the extension for the lifetime of the session,
// so a `cp` of a new extension.js into the install dir has no effect on the
// running shell. The launcher falls back to XWayland until the user
// logs out and back in, at which point the freshly-loaded enable()
// rewrites the marker with the new build-id and Onboard returns to
// native Wayland on the launch after that.
function writeBuildIdMarker() {
    try {
        let ownPath;
        try {
            [ownPath] = GLib.filename_from_uri(import.meta.url);
        } catch (_e) { return; }
        const [ok, bytes] = GLib.file_get_contents(ownPath);
        if (!ok) return;
        const buildId = GLib.compute_checksum_for_data(
            GLib.ChecksumType.SHA256, bytes).substring(0, 16);
        const cacheDir = GLib.build_filenamev([
            GLib.get_user_cache_dir(), 'onboard']);
        GLib.mkdir_with_parents(cacheDir, 0o755);
        const markerPath = GLib.build_filenamev([
            cacheDir, 'extension-build-id']);
        GLib.file_set_contents(markerPath, buildId);
    } catch (_e) { /* never break enable() */ }
}

export default class OnboardExtension {
    enable() {
        writeBuildIdMarker();
        this._lastFocus = null;
        this._focusBouncePending = false;

        // Remember the most recently focused non-Onboard window so we can
        // hand focus back when Onboard accidentally gets it.
        this._focusId = global.display.connect(
            'notify::focus-window',
            () => this._onFocusChanged());

        // Apply make_above/skip-taskbar to the Onboard window as soon as
        // we can identify it. Two-stage match because on Wayland the
        // client sends xdg_toplevel.set_app_id *after* the MetaWindow
        // is created -- so when 'window-created' fires get_wm_class()
        // is still null and a one-shot adopt would silently miss the
        // window. We try once eagerly, and if that doesn't match yet
        // we hook notify::wm-class and retry when the app_id lands.
        this._createdId = global.display.connect(
            'window-created',
            (_disp, win) => this._watchWindow(win));

        // Apply to windows already open at enable time -- covers the case
        // where the user enables the extension while Onboard is running.
        // wm_class is guaranteed to be set on these, so the eager path
        // in _watchWindow() catches them without needing the notify hook.
        for (const actor of global.get_window_actors()) {
            this._watchWindow(actor.meta_window);
        }
    }

    disable() {
        if (this._focusId) {
            global.display.disconnect(this._focusId);
            this._focusId = 0;
        }
        if (this._createdId) {
            global.display.disconnect(this._createdId);
            this._createdId = 0;
        }
        this._lastFocus = null;
        this._focusBouncePending = false;
    }

    _isOnboard(win) {
        if (!win)
            return false;
        try {
            if (win.get_wm_class()?.toLowerCase() === ONBOARD_ID)
                return true;
        } catch (_e) { /* some windows don't have wm_class yet */ }
        try {
            if (win.get_gtk_application_id?.()?.toLowerCase() === ONBOARD_ID)
                return true;
        } catch (_e) { /* getter may not exist on older Mutter */ }
        return false;
    }

    _watchWindow(win) {
        if (this._adopt(win))
            return;
        // Not Onboard yet, but wm_class is set lazily on Wayland --
        // listen for the app_id to arrive, then retry adoption.
        let notifyId = 0;
        notifyId = win.connect('notify::wm-class', () => {
            if (this._adopt(win)) {
                win.disconnect(notifyId);
                notifyId = 0;
            }
        });
        // Don't leak the notify subscription if the window vanishes
        // before its wm_class is ever set.
        win.connect('unmanaged', () => {
            if (notifyId) {
                try { win.disconnect(notifyId); } catch (_e) {}
                notifyId = 0;
            }
        });
    }

    _adopt(win) {
        if (!this._isOnboard(win))
            return false;
        // Always-on-top -- same role as KWin "above=true,aboverule=2"
        if (!win.is_above()) {
            try { win.make_above(); } catch (_e) {}
        }
        // Skip taskbar / Alt+Tab cycling -- already default for utility
        // windows, but we set it explicitly for the Onboard main window.
        try { win.set_skip_taskbar?.(true); } catch (_e) {}
        return true;
    }

    _onFocusChanged() {
        // Stop infinite ping-pong: when we re-focus _lastFocus below
        // it'll fire this handler again with focus = _lastFocus, which
        // we must not treat as user activity (else _lastFocus becomes
        // self-referential).
        if (this._focusBouncePending) {
            this._focusBouncePending = false;
            return;
        }

        const win = global.display.focus_window;
        if (!this._isOnboard(win)) {
            // Remember the new non-Onboard focus target for next time.
            if (win)
                this._lastFocus = win;
            return;
        }

        // The Onboard window got focus. Push it back to wherever the
        // user actually was. This is the Mutter equivalent of KWin's
        // "acceptfocus=false" rule -- slightly racier (we revert
        // *after* focus is granted instead of refusing it up front),
        // but functionally equivalent for typing scenarios.
        if (this._lastFocus &&
            !this._lastFocus.minimized &&
            this._lastFocus.get_workspace?.()) {
            this._focusBouncePending = true;
            try {
                this._lastFocus.focus(global.get_current_time());
            } catch (_e) {
                this._focusBouncePending = false;
            }
        }
    }
}
