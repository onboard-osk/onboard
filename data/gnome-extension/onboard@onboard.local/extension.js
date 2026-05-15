// SPDX-License-Identifier: GPL-3.0-or-later
//
// Onboard keyboard window helper -- GNOME Shell extension
// =======================================================
//
// GNOME Mutter implements neither `zwlr_layer_shell_v1` nor a
// per-window focus-rule mechanism (KWin's "acceptfocus=false").
// This extension recognizes two kinds of Onboard window:
//
//   * The keyboard itself, wm_class "onboard":
//       1. Forced above other windows (Meta.Window.make_above), and
//       2. Whenever it accidentally receives keyboard focus, focus
//          is reverted to the previously focused window.
//
//   * Focusable Onboard children dialogs, wm_class prefix "onboard-"
//     (currently only the snippet dialog -- KeyboardWidget.py swaps
//     GLib.set_prgname() to "onboard-dialog" for that reason):
//       1. Activated on map so they actually receive keyboard focus
//          (Mutter on Wayland refuses unsolicited set_focus_on_map
//          without an xdg_activation_v1 token, but Window.activate()
//          synthesizes the missing user-activity stamp).
//       2. The keyboard's make_above flag is dropped while any child
//          is mapped, otherwise the keyboard would stay in
//          META_LAYER_TOP and stack above the child in META_LAYER_NORMAL
//          regardless of transient_for. Re-applied on child unmap.
//       3. Excluded from the focus-bounce, so they can hold focus
//          and become _lastFocus -- meaning subsequent taps on the
//          keyboard bounce focus back to the child instead of to
//          some unrelated previously-focused window.
//
// This lets Onboard run on native GNOME Wayland instead of falling back to XWayland.

import Meta from 'gi://Meta';
import GLib from 'gi://GLib';

const ONBOARD_KEYBOARD_ID = 'onboard';
const ONBOARD_CHILD_PREFIX = 'onboard-';

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
        // Currently-mapped Onboard child windows. Tracked so we know
        // when to un-/re-apply make_above on the keyboard window.
        this._mappedChildren = new Set();

        // Remember the most recently focused non-Onboard-keyboard window
        // so we can hand focus back when the keyboard accidentally gets
        // it. Note: Onboard children are *not* keyboards, so they end up
        // recorded here too -- exactly what we want.
        this._focusId = global.display.connect(
            'notify::focus-window',
            () => this._onFocusChanged());

        // Apply make_above/skip-taskbar (keyboard) or activate+drop-above
        // (child) as soon as we can identify the window. Two-stage match
        // because on Wayland the client sends xdg_toplevel.set_app_id
        // *after* the MetaWindow is created -- so when 'window-created'
        // fires get_wm_class() is still null and a one-shot adopt would
        // silently miss the window. We try once eagerly, and if that
        // doesn't match yet we hook notify::wm-class and retry when the
        // app_id lands.
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
        this._mappedChildren = null;
    }

    _wmClass(win) {
        if (!win)
            return null;
        try {
            const c = win.get_wm_class();
            if (c) return c.toLowerCase();
        } catch (_e) { /* some windows don't have wm_class yet */ }
        try {
            const c = win.get_gtk_application_id?.();
            if (c) return c.toLowerCase();
        } catch (_e) { /* getter may not exist on older Mutter */ }
        return null;
    }

    _isOnboardKeyboard(win) {
        return this._wmClass(win) === ONBOARD_KEYBOARD_ID;
    }

    _isOnboardChild(win) {
        const c = this._wmClass(win);
        return c !== null && c.startsWith(ONBOARD_CHILD_PREFIX);
    }

    _isOnboardAny(win) {
        return this._isOnboardKeyboard(win) || this._isOnboardChild(win);
    }

    _forEachKeyboard(fn) {
        for (const actor of global.get_window_actors()) {
            const w = actor.meta_window;
            if (this._isOnboardKeyboard(w)) {
                try { fn(w); } catch (_e) {}
            }
        }
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
        if (this._isOnboardKeyboard(win))
            return this._adoptKeyboard(win);
        if (this._isOnboardChild(win))
            return this._adoptChild(win);
        return false;
    }

    _adoptKeyboard(win) {
        // Always-on-top.
        // Suppress when a focusable child is currently mapped: in that
        // case the keyboard must stay in META_LAYER_NORMAL so the child
        // stacks above it.
        if (!win.is_above() && this._mappedChildren.size === 0) {
            try { win.make_above(); } catch (_e) {}
        }
        // Skip taskbar / Alt+Tab cycling -- already default for utility
        // windows, but we set it explicitly for the Onboard main window.
        try { win.set_skip_taskbar?.(true); } catch (_e) {}
        return true;
    }

    _adoptChild(win) {
        // Drop the keyboard out of META_LAYER_TOP for the lifetime of
        // this child. Without this, Mutter stacks the keyboard above
        // any normal-layer window regardless of transient_for, and the
        // child appears hidden behind the keyboard.
        const wasEmpty = this._mappedChildren.size === 0;
        this._mappedChildren.add(win);
        if (wasEmpty) {
            this._forEachKeyboard(kbd => {
                if (kbd.is_above())
                    kbd.unmake_above();
            });
        }

        // Activate the child so it actually receives keyboard focus.
        // Mutter on Wayland refuses focus changes that aren't backed by
        // user activity unless they come with an xdg_activation_v1
        // token; Meta.Window.activate(time) synthesizes the missing
        // user-activity stamp.
        try { win.activate(global.get_current_time()); } catch (_e) {}

        win.connect('unmanaged', () => {
            this._mappedChildren.delete(win);
            // Re-apply make_above on the keyboard once the last
            // focusable child is gone.
            if (this._mappedChildren.size === 0) {
                this._forEachKeyboard(kbd => {
                    if (!kbd.is_above()) {
                        try { kbd.make_above(); } catch (_e) {}
                    }
                });
            }
        });
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
        // Bounce *only* when focus lands on the keyboard. A focused
        // Onboard child (snippet dialog) must be allowed to hold focus
        // -- and is recorded as _lastFocus below so the next bounce
        // from the keyboard returns focus to the child.
        if (!this._isOnboardKeyboard(win)) {
            if (win)
                this._lastFocus = win;
            return;
        }

        // The Onboard keyboard got focus. Push it back to wherever the
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
