# -*- coding: utf-8 -*-

# Copyright © 2026 Onboard contributors
#
# This file is part of Onboard.
#
# Onboard is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Onboard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Wayland-related runtime helpers.

This module isolates all Wayland / GtkLayerShell concerns so the rest of the
code base doesn't have to repeat the same try/except dance.
"""

from __future__ import division, print_function, unicode_literals

import os
import logging

import gi
from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import Gdk, Gio, GLib

_logger = logging.getLogger(__name__)


_layer_shell = None
_layer_shell_checked = False


def is_wayland():
    """
    Return True if Onboard is currently running on a Wayland session.

    Detection is done via the GDK backend of the default display, falling back
    to the ``XDG_SESSION_TYPE`` / ``WAYLAND_DISPLAY`` env vars when GDK has not
    been initialised yet (e.g. during very early startup or unit tests).
    """
    display = Gdk.Display.get_default()
    if display is not None:
        type_name = type(display).__name__
        # Accept both GdkWaylandDisplay and the introspected name variants.
        if "Wayland" in type_name:
            return True
        if "X11" in type_name:
            return False

    # Fallback: env-based detection. Useful before GDK is initialised.
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return False


def get_session_type_label():
    """ Short human-readable session label for log lines. """
    if is_wayland():
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")[0] or \
                  os.environ.get("DESKTOP_SESSION", "") or "unknown"
        return "Wayland ({})".format(desktop)
    return "X11"


def is_kde_plasma():
    """
    True if we're running inside a KDE Plasma session. Used to enable the
    KWin-window-rule path (regular toplevel + acceptfocus rule) instead of
    gtk-layer-shell, which preserves the user's ability to drag/resize the
    keyboard window. Other Wayland compositors don't expose an equivalent
    rule mechanism, so they still have to use layer-shell.
    """
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" in desktop or "Plasma" in desktop:
        return True
    if os.environ.get("KDE_FULL_SESSION") == "true":
        return True
    return False


def install_kwin_rule(app_id="onboard",
                     rule_name="onboard",
                     description="Onboard on-screen keyboard"):
    """
    Idempotently install a KWin window rule that marks the Onboard window
    as not-focus-stealing and always-on-top. Returns True on success.

    The rule is written to ``~/.config/kwinrulesrc`` (user scope, no sudo).
    KWin is asked to reload via D-Bus; if that fails the change still
    takes effect on next KWin reconfigure / next login.

    The function compares the desired configuration against what's already on
    disk and only rewrites the file (and triggers ``KWin reconfigure``) when
    something actually changed. Self-healing -- if the user deletes the rule
    by hand it is recreated -- without spamming sync clients or stomping on
    custom edits between runs.

    This is the same trick `vboard <https://github.com/archisman-panigrahi/vboard>`_
    uses on KDE Wayland to keep its keyboard window draggable while still
    refusing keyboard focus.
    """
    import configparser
    import io
    import subprocess

    cfg_dir = os.environ.get("XDG_CONFIG_HOME") or \
              os.path.expanduser("~/.config")
    cfg_file = os.path.join(cfg_dir, "kwinrulesrc")

    parser = configparser.ConfigParser(interpolation=None,
                                        allow_no_value=True)
    parser.optionxform = str  # preserve case (KWin keys are CamelCase)

    current_text = ""
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                current_text = f.read()
            parser.read_string(current_text)
        except (OSError, configparser.Error) as e:
            _logger.warning("Could not parse %s: %s -- aborting "
                            "KWin rule install", cfg_file, e)
            return False

    if not parser.has_section("General"):
        parser.add_section("General")
    if not parser.has_section(rule_name):
        parser.add_section(rule_name)

    # Drop any stale per-desktop overrides ([$d]) and legacy title matches
    for opt in list(parser.options(rule_name)):
        if opt.endswith("[$d]"):
            parser.remove_option(rule_name, opt)
    for legacy in ("title", "titlematch"):
        parser.remove_option(rule_name, legacy)

    # KWin's rule format stores the Wayland app_id under the historical
    # 'wmclass' keys. 'wmclassmatch=1' = exact match, 'wmclasscomplete=false'
    # = match against the resource class only (single token).
    # 'aboverule=2' / 'acceptfocusrule=2' = "force this value" (vs. "do once",
    # "remember", etc.).
    values = {
        "Description": description,
        "Enabled": "true",
        "wmclass": app_id,
        "wmclassmatch": "1",
        "wmclasscomplete": "false",
        # Only match Normal windows (NormalMask = 1). Without this,
        # the rule also catches Dialog (=32) / Utility (=64) windows of
        # the same app -- and forcing acceptfocus=false on those leaves
        # e.g. the "New snippet" dialog unable to receive keyboard input.
        "types": "1",
        "typesrule": "2",
        "above": "true",
        "aboverule": "2",
        "acceptfocus": "false",
        "acceptfocusrule": "2",
        "skiptaskbar": "true",
        "skiptaskbarrule": "2",
        "skippager": "true",
        "skippagerrule": "2",
    }
    for k, v in values.items():
        parser.set(rule_name, k, v)

    rules = [r.strip()
             for r in parser.get("General", "rules", fallback="").split(",")
             if r.strip()]
    if rule_name not in rules:
        rules.append(rule_name)
    parser.set("General", "rules", ",".join(rules))
    parser.set("General", "count", str(len(rules)))

    # Render the desired state to a string and bail out early if it matches
    # what's already on disk -- avoids touching mtime (sync churn) and saves
    # a needless KWin reconfigure round-trip on every Onboard start.
    buf = io.StringIO()
    parser.write(buf)
    new_text = buf.getvalue()

    if new_text == current_text:
        _logger.debug("KWin window rule '%s' already up to date at %s",
                      rule_name, cfg_file)
        return True

    # Atomic write: tmp file in same dir + rename, so a crash mid-write
    # can't leave kwinrulesrc truncated.
    tmp_file = cfg_file + ".onboard.tmp"
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp_file, cfg_file)
    except OSError as e:
        _logger.warning("Could not write %s: %s", cfg_file, e)
        try:
            os.unlink(tmp_file)
        except OSError:
            pass
        return False

    # Hot-reload KWin so the rule takes effect immediately. Only fired when
    # we actually changed the file.
    for cmd in (["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
                ["qdbus", "org.kde.KWin", "/KWin", "reconfigure"],
                ["dbus-send", "--session", "--dest=org.kde.KWin",
                 "/KWin", "org.kde.KWin.reconfigure"]):
        try:
            subprocess.run(cmd, check=True, timeout=2,
                           capture_output=True)
            break
        except (FileNotFoundError, subprocess.SubprocessError,
                subprocess.TimeoutExpired):
            continue

    _logger.info("KWin window rule '%s' updated at %s "
                 "(app_id=%s, acceptfocus=false, above=true)",
                 rule_name, cfg_file, app_id)
    return True


def _get_layer_shell():
    """ Lazy-import GtkLayerShell. Returns the module or None. """
    global _layer_shell, _layer_shell_checked
    if _layer_shell_checked:
        return _layer_shell
    _layer_shell_checked = True
    try:
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
        _layer_shell = GtkLayerShell
        _logger.debug("GtkLayerShell loaded")
    except (ValueError, ImportError) as e:
        _logger.info("GtkLayerShell not available: {}".format(e))
        _layer_shell = None
    return _layer_shell


def is_layer_shell_supported_by_compositor():
    """
    True only when the running Wayland compositor actually exposes
    ``zwlr_layer_shell_v1``. Distinct from :func:`is_layer_shell_available`,
    which only checks whether the GtkLayerShell typelib is importable.

    Mutter (GNOME) advertises no such global, so this returns False
    there even though the library is installed system-wide on most distros.
    """
    ls = _get_layer_shell()
    if ls is None:
        return False
    if not hasattr(ls, "is_supported"):
        return False
    try:
        return bool(ls.is_supported())
    except Exception:
        return True


def is_layer_shell_available():
    """
    True if we can usefully use gtk-layer-shell on this system -- the
    typelib is importable *and* the running compositor advertises the
    protocol.
    """
    return is_wayland() and is_layer_shell_supported_by_compositor()


def is_gnome_shell():
    """
    True if we are running inside a GNOME Shell session (Mutter,
    either Wayland or Xorg).
    """
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "GNOME" in desktop:
        return True
    if os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return True
    # Fallback: probe D-Bus (covers VirtualBox / nested sessions where
    # XDG_CURRENT_DESKTOP is not set but GNOME Shell is running).
    try:
        import dbus
        bus = dbus.SessionBus()
        if bus.name_has_owner("org.gnome.Shell"):
            return True
    except Exception:
        pass
    return False


def init_layer_shell(window, namespace="onboard"):
    """
    Turn ``window`` into a layer-shell surface (idempotent).

    Returns True on success, False if layer-shell is unavailable or if the
    window is already realised (layer-shell must be initialised before
    realisation).
    """
    ls = _get_layer_shell()
    if ls is None:
        return False

    # Guard against the GNOME-Mutter scenario where the typelib is
    # installed but the compositor doesn't expose ``zwlr_layer_shell_v1``.
    if not ls.is_supported():
        _logger.info("init_layer_shell(): compositor does not expose "
                     "zwlr_layer_shell_v1 -- skipping (no layer surface)")
        return False

    if ls.is_layer_window(window):
        return True

    if window.get_realized():
        # Once a Gdk window has been created, gtk-layer-shell can no longer
        # promote it to a layer surface. The caller should arrange for
        # init_layer_shell() to be called before realize().
        _logger.warning("init_layer_shell(): window already realised; "
                        "layer-shell not applied")
        return False

    ls.init_for_window(window)
    ls.set_namespace(window, namespace)

    # Sit above normal application windows but below regular OVERLAY surfaces
    # like notifications. TOP is what every other on-screen keyboard
    # (squeekboard, maliit-keyboard, wvkbd) uses.
    ls.set_layer(window, ls.Layer.TOP)

    # Never take keyboard focus -- this is the Wayland equivalent of the X
    # ``set_accept_focus(False)`` call that the CHANGELOG flagged as the
    # original Wayland blocker.
    ls.set_keyboard_mode(window, ls.KeyboardMode.NONE)

    _logger.debug("init_layer_shell(): namespace=%s layer=TOP "
                  "keyboard=NONE", namespace)
    return True


def set_anchor_bottom(window, expand=False):
    """
    Anchor a layer-shell window to the bottom edge.
    If ``expand`` is True, also anchor left+right so the surface spans the
    full output width (matches the X "expanded dock" behavior).
    """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    ls.set_anchor(window, ls.Edge.BOTTOM, True)
    ls.set_anchor(window, ls.Edge.TOP, False)
    ls.set_anchor(window, ls.Edge.LEFT, expand)
    ls.set_anchor(window, ls.Edge.RIGHT, expand)


def set_anchor_top(window, expand=False):
    """ Anchor a layer-shell window to the top edge. """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    ls.set_anchor(window, ls.Edge.TOP, True)
    ls.set_anchor(window, ls.Edge.BOTTOM, False)
    ls.set_anchor(window, ls.Edge.LEFT, expand)
    ls.set_anchor(window, ls.Edge.RIGHT, expand)


def clear_anchors(window):
    """ Detach the window from screen edges (i.e. floating mode). """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT, ls.Edge.RIGHT):
        ls.set_anchor(window, edge, False)


def set_exclusive_zone(window, height):
    """
    Replacement for ``_NET_WM_STRUT_PARTIAL`` on Wayland.
    ``height`` -- pixels of vertical screen space the keyboard reserves
    (i.e. workareas of maximized apps shrink by this much).

    Pass 0 to clear / -1 to opt out and let other surfaces overlap us.
    """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    ls.set_exclusive_zone(window, int(height))


def is_layer_window(window):
    """ True if ``window`` was successfully turned into a layer surface. """
    ls = _get_layer_shell()
    if ls is None:
        return False
    return ls.is_layer_window(window)


def set_layer_keyboard_interactive(window, interactive):
    """
    Toggle a layer-shell window between ON_DEMAND and NONE keyboard
    modes. No-op on non-layer windows or when gtk-layer-shell is
    unavailable.

    The layer surface is created with KeyboardMode.NONE so it never
    steals focus from the app being typed into. Promote to ON_DEMAND
    when a focusable child (snippet dialog, ...) needs to receive
    input, and drop back to NONE when it closes.
    """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    mode = ls.KeyboardMode.ON_DEMAND if interactive else ls.KeyboardMode.NONE
    ls.set_keyboard_mode(window, mode)


def diagnose_uinput_event_device(device_name="Onboard on-screen keyboard",
                                 wait_seconds=0.5):
    """
    After ``select_backend(BACKEND_UINPUT)`` has succeeded, verify that the
    resulting ``/dev/input/eventN`` is actually readable by the current user.
    Returns ``(ok, message)``.

    On Wayland the kernel-level uinput open can succeed (so Onboard *thinks*
    everything is fine) while the synthesized events still go nowhere because
    the new event device is owned ``root:input 0660`` and the compositor,
    running as the user, can't read from it. This function detects exactly
    that failure mode and returns an actionable error string.
    """
    import os
    import glob
    import time

    # Give udev a moment to finish processing the new device.
    time.sleep(wait_seconds)

    candidate = None
    newest_mtime = 0
    for path in glob.glob("/dev/input/event*"):
        try:
            with open("/sys/class/input/{}/device/name"
                      .format(os.path.basename(path))) as f:
                if f.read().strip() == device_name:
                    st = os.stat(path)
                    if st.st_mtime > newest_mtime:
                        newest_mtime = st.st_mtime
                        candidate = path
        except (OSError, IOError):
            continue

    if candidate is None:
        return (False,
                "uinput device '{}' not found under /dev/input/event*; "
                "udev may not have processed the new device yet"
                .format(device_name))

    if not os.access(candidate, os.R_OK):
        return (False,
                "Found {} but it is not readable by the current user. "
                "The Wayland compositor needs read access "
                "to receive injected keystrokes. Install the udev rule to "
                "/etc/udev/rules.d/72-onboard-uinput.rules and run "
                "'sudo udevadm control --reload-rules && "
                "sudo udevadm trigger /dev/uinput', then restart Onboard."
                .format(candidate))

    # Note: there's no runtime check for the 'power-switch' udev tag here
    # any more. systemd-logind only EVIOCGRABs power-switch-tagged devices
    # that actually advertise KEY_POWER/KEY_SLEEP/etc. — we strip those
    # capabilities at uinput-creation time in osk_uinput.c, so the tag is
    # harmless on us. The previous TAG-=power-switch in our rule was purely
    # defensive and was dropped along with the runtime check.

    return (True, "uinput event device {} is readable".format(candidate))


# ---------------------------------------------------------------------------
# KDE layout-change watcher
# ---------------------------------------------------------------------------
#
# On Wayland the keymap-state signals Onboard listens to on X11 are silent
# for windows that refuse keyboard focus, so when the user switches layout
# via the KDE taskbar/tray the keyboard window never learns about it
# and the key labels stay on the previous layout until e.g. the next pointer enter
# happens to repaint the surface.
#
# KDE Plasma has a focus-independent session-bus signal for this
# exact use case. The contract is identical on X11 and Wayland,
# on X11 it is hosted by the kded "keyboard" module, on Wayland by KWin itself.
#
# Quirk: KWin only registers ``/Layouts`` when at least two layouts are
# configured -- before that the bus name has no owner, and after the user
# adds a second layout the name appears. We therefore also watch
# NameOwnerChanged so we can attach late if the interface shows up
# mid-session.

_KDE_KBD_BUS_NAME  = "org.kde.keyboard"
_KDE_KBD_OBJECT    = "/Layouts"
_KDE_KBD_INTERFACE = "org.kde.KeyboardLayouts"


class KdeLayoutWatcher:
    """
    Subscribes to KDE's ``org.kde.KeyboardLayouts.layoutChanged`` signal
    so Onboard learns about layout switches even when its window has no
    keyboard focus.

    The ``callback`` is invoked as ``callback(new_index)`` on every
    ``layoutChanged`` *or* ``layoutListChanged`` emission and on initial
    attach. ``new_index`` is the *authoritative* layout index reported
    by KDE (queried via ``getLayout()`` so we get the same value
    regardless of whether the signal carried it). It is ``-1`` only if
    the D-Bus query unexpectedly fails -- callers should treat that as
    "trigger a refresh but don't sync the cached xkb_state".

    The index lets the caller bypass our local wl_keyboard-fed
    ``xkb_state``, which is stale because Onboard's surfaces don't take
    keyboard focus -- subscribers can push the new index into
    ``vk.lock_group(index)`` so subsequent ``labels_from_keycode()``
    lookups query the right layout.

    Callers are still expected to debounce expensive work;
    ``OnboardGtk.cb_group_changed`` does so via the ``_reload_layout_timer``.

    Construction is safe even when KDE Plasma is not running or the
    ``/Layouts`` interface has not been registered yet (single-layout
    configurations); all D-Bus errors are caught and logged, never
    raised. A NameOwnerChanged watch handles late binding when the user
    adds a second layout at runtime.

    Call ``stop()`` to release subscriptions before quitting.
    """

    def __init__(self, callback):
        self._callback = callback
        self._bus = None
        self._proxy = None
        self._signal_handler_id = 0
        self._name_owner_sub_id = 0

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            _logger.warning("KdeLayoutWatcher: cannot acquire session "
                            "bus: %s", e.message)
            return

        # Late-binding watch first, so that if /Layouts isn't registered
        # yet (single-layout config) and the user adds a second layout
        # later we still get the binding.
        try:
            self._name_owner_sub_id = self._bus.signal_subscribe(
                "org.freedesktop.DBus",        # sender
                "org.freedesktop.DBus",        # interface
                "NameOwnerChanged",            # member
                "/org/freedesktop/DBus",       # path
                _KDE_KBD_BUS_NAME,             # arg0 filter
                Gio.DBusSignalFlags.NONE,
                self._on_name_owner_changed,
            )
        except GLib.Error as e:
            _logger.warning("KdeLayoutWatcher: NameOwnerChanged "
                            "subscribe failed: %s", e.message)

        self._try_attach_proxy()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def stop(self):
        """ Drop the proxy + NameOwnerChanged subscription. """
        self._detach_proxy()
        if self._bus is not None and self._name_owner_sub_id:
            try:
                self._bus.signal_unsubscribe(self._name_owner_sub_id)
            except GLib.Error:
                pass
            self._name_owner_sub_id = 0
        self._bus = None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _try_attach_proxy(self):
        """
        Create the proxy and check whether the bus name actually has an
        owner. ``Gio.DBusProxy.new_sync`` with the default flags happily
        returns a proxy for a non-existent name; we treat
        ``name_owner == None`` as "interface not registered yet" and
        wait for NameOwnerChanged.
        """
        if self._proxy is not None:
            return                                     # already attached

        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,                                  # interface info
                _KDE_KBD_BUS_NAME,
                _KDE_KBD_OBJECT,
                _KDE_KBD_INTERFACE,
                None,                                  # cancellable
            )
        except GLib.Error as e:
            _logger.debug("KdeLayoutWatcher: proxy creation failed: %s",
                          e.message)
            return

        if proxy.get_name_owner() is None:
            _logger.info("KdeLayoutWatcher: %s not yet registered "
                         "(single-layout config?); waiting for it to "
                         "appear", _KDE_KBD_BUS_NAME)
            return

        self._proxy = proxy
        self._signal_handler_id = proxy.connect(
            "g-signal", self._on_g_signal)
        _logger.info("KdeLayoutWatcher: attached to %s %s %s",
                     _KDE_KBD_BUS_NAME, _KDE_KBD_OBJECT,
                     _KDE_KBD_INTERFACE)
        # Initial sync: the user may have switched layout before Onboard
        # finished starting; without this they'd see the wrong labels
        # until the next switch.
        self._fire("initial attach")

    def _detach_proxy(self):
        if self._proxy is not None and self._signal_handler_id:
            try:
                self._proxy.disconnect(self._signal_handler_id)
            except Exception:
                pass
        self._signal_handler_id = 0
        self._proxy = None

    def _query_current_index(self):
        """
        Ask KDE which layout is currently active. Returns the int index
        or ``-1`` on error. We always query rather than trusting the
        signal payload because ``layoutListChanged`` carries no index
        and the initial attach has none either.
        """
        if self._proxy is None:
            return -1
        try:
            result = self._proxy.call_sync(
                "getLayout",
                None,                          # parameters
                Gio.DBusCallFlags.NO_AUTO_START,
                500,                           # timeout ms
                None,                          # cancellable
            )
        except GLib.Error as e:
            _logger.warning("KdeLayoutWatcher: getLayout() failed: %s",
                            e.message)
            return -1
        try:
            (idx,) = result.unpack()
            return int(idx)
        except Exception:
            _logger.warning("KdeLayoutWatcher: unexpected getLayout() "
                            "reply shape: %s", result)
            return -1

    def _fire(self, reason):
        idx = self._query_current_index()
        _logger.debug("KdeLayoutWatcher: %s -> index %d", reason, idx)
        try:
            self._callback(idx)
        except Exception:
            _logger.exception("KdeLayoutWatcher: callback raised")

    def _on_g_signal(self, _proxy, _sender, signal_name, _parameters):
        # Both signals warrant a refresh: 'layoutChanged' is the normal
        # switch event, 'layoutListChanged' fires when the user edits
        # the set of configured layouts -- either case means our cached
        # group <-> language mapping is stale.
        if signal_name in ("layoutChanged", "layoutListChanged"):
            self._fire(signal_name)

    def _on_name_owner_changed(self, _bus, _sender, _object,
                               _interface, _signal, parameters):
        try:
            name, _old_owner, new_owner = parameters.unpack()
        except Exception:
            return
        if name != _KDE_KBD_BUS_NAME:
            return
        if new_owner:
            _logger.debug("KdeLayoutWatcher: %s appeared (owner=%s); "
                          "attaching", name, new_owner)
            self._try_attach_proxy()
            # Also trigger an immediate sync -- between us missing the
            # prior layoutChanged events and now, the active group may
            # already differ from what Onboard has cached.
            if self._proxy is not None:
                self._fire("NameOwnerChanged (acquired)")
        else:
            _logger.debug("KdeLayoutWatcher: %s went away", name)
            self._detach_proxy()
