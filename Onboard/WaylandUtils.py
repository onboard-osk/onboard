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


# ---------------------------------------------------------------------------
# GNOME Shell extension installer.
# ---------------------------------------------------------------------------
#
# GNOME Mutter implements neither something like `kwinrulesrc` nor
# `zwlr_layer_shell_v1`, leaving Onboard with no native-Wayland
# focus-protection mechanism on Mutter. This extension is the Mutter
# analogue of the KWin rule that install_kwin_rule() writes on KDE:
# a per-window `acceptfocus=false` + `above=true` enforced from
# Shell-extension code.

GNOME_EXTENSION_UUID = "onboard@onboard.local"


def _detect_gnome_shell_major_version():
    """
    Return the integer major version of the running gnome-shell
    (e.g. 48) or None if it can't be determined.

    Used by :func:`install_gnome_extension` so we can inject the
    running shell-version into metadata.json's allow-list and the
    extension doesn't silently auto-disable after every GNOME bump.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["gnome-shell", "--version"],
            check=True, timeout=2,
            capture_output=True, text=True).stdout
    except (FileNotFoundError, subprocess.SubprocessError,
            subprocess.TimeoutExpired, OSError):
        return None
    # Expected format: "GNOME Shell 48.1"
    parts = out.strip().split()
    if len(parts) < 3:
        return None
    try:
        return int(parts[2].split(".")[0])
    except (ValueError, IndexError):
        return None


def is_gnome_extension_enabled(uuid=GNOME_EXTENSION_UUID):
    """
    True if a GNOME Shell extension with the given UUID is currently
    enabled (per `gnome-extensions list --enabled`). Returns False on
    any error -- the result is advisory; callers should not depend on
    it for correctness.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            check=True, timeout=2,
            capture_output=True, text=True).stdout
    except (FileNotFoundError, subprocess.SubprocessError,
            subprocess.TimeoutExpired, OSError):
        return False
    return uuid in out.split()


def _find_bundled_gnome_extension_source(uuid):
    """
    Locate the bundled extension source tree. Supports two layouts:

      (i)  source checkout: <project>/data/gnome-extension/<uuid>/
      (ii) system-wide install: <prefix>/share/onboard/gnome-extension/<uuid>/

    Returns the absolute path or None.
    """
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "data", "gnome-extension", uuid),
        os.path.join(sys.prefix, "share", "onboard",
                     "gnome-extension", uuid),
        os.path.join("/usr", "share", "onboard",
                     "gnome-extension", uuid),
        os.path.join("/usr/local", "share", "onboard",
                     "gnome-extension", uuid),
    ]
    for p in candidates:
        if os.path.isdir(p):
            return os.path.abspath(p)
    return None


def _installed_metadata_lists_shell_version(install_dir, shell_version):
    """
    True if `install_dir/metadata.json` is parseable AND already lists
    `shell_version` in its `shell-version` array. Used as the
    "user has explicitly opted out" heuristic in install_gnome_extension():
    if the extension is installed, the installed metadata is current
    for the running shell, and yet the extension is disabled, we
    treat that as a deliberate user choice rather than something to
    auto-correct.

    Returns False on any error (missing file, bad JSON, missing key).
    Also False when shell_version is None (we can't tell, so we err
    on the side of letting the caller proceed with a re-install).
    """
    if shell_version is None:
        return False
    import json
    try:
        with open(os.path.join(install_dir, "metadata.json"),
                  "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    versions = [str(v) for v in meta.get("shell-version", [])]
    return str(shell_version) in versions


def _write_if_changed(dst_path, new_bytes):
    """
    Write ``new_bytes`` to ``dst_path`` atomically iff the file is
    missing or its current bytes differ. Avoids needlessly bumping
    mtime when the on-disk content is already current.

    Atomic write (tmp + rename) avoids leaving a half-written
    extension file on disk if the process gets killed mid-copy.
    """
    try:
        with open(dst_path, "rb") as f:
            if f.read() == new_bytes:
                return
    except OSError:
        pass  # missing or unreadable -> rewrite
    tmp = dst_path + ".onboard.tmp"
    with open(tmp, "wb") as f:
        f.write(new_bytes)
    os.replace(tmp, dst_path)


# Path (relative to XDG_CACHE_HOME) of the marker file the GNOME
# extension writes on every enable() so the launcher can detect a
# stale running build. See data/gnome-extension/.../extension.js
# `writeBuildIdMarker()` for the writer side.
GNOME_BUILD_ID_MARKER_RELPATH = "onboard/extension-build-id"


def _build_id_for_file(path):
    """
    Return the SHA-256 prefix (16 hex chars) of the file at ``path``,
    or None on read error. Used as the build-id for the GNOME Shell
    extension: same bytes -> same id, on both the Python install side
    and the JS enable() side. Truncated to 16 chars because it's used
    purely for equality comparison, not security.
    """
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def _running_extension_build_id(timeout=2.0):
    """
    Return the build-id the currently-loaded GNOME extension wrote on
    enable(), or None if no marker is present within ``timeout``
    seconds.

    The marker write happens inside gnome-shell *after*
    ``gnome-extensions enable`` has returned, so on fresh installs we
    may need to wait briefly for the extension's enable() to run. On
    repeat launches the marker is already on disk and the first
    open() succeeds immediately.
    """
    import time
    cache_dir = (os.environ.get("XDG_CACHE_HOME") or
                 os.path.expanduser("~/.cache"))
    marker = os.path.join(cache_dir, GNOME_BUILD_ID_MARKER_RELPATH)
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        try:
            with open(marker, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)


def install_gnome_extension(uuid=GNOME_EXTENSION_UUID):
    """
    Idempotently install the bundled Onboard GNOME Shell extension
    into the per-user extensions directory and ask gnome-shell to
    enable it.

    Returns True only when the extension is installed *and* enabled
    after the call. False on any installation error, or if
    gnome-extensions(1) refused to enable it (most commonly because
    the user's GNOME Shell version is outside the supported range
    after our shell-version injection failed).

    Self-healing: if the user deletes the extension by hand it is
    recreated on next Onboard launch. If the installed metadata.json
    is stale relative to the running gnome-shell (i.e. shell got
    upgraded) we re-patch and re-enable it.

    Stale-shell detection: if the extension is enabled but the
    running gnome-shell is executing a *previous* build of the
    extension's JS (Mutter imports caches extension for the session lifetime,
    so an Onboard upgrade that ships fixed extension code can sit on
    disk while the shell keeps running the buggy build), we return
    False so the launcher's auto-XWayland fallback takes
    over on this launch. The user picks up the new build on next
    session restart.

    Opt-out: if the extension is already installed, its metadata.json
    already lists the running shell version, and yet the extension
    is disabled, we treat that as a deliberate user choice (most
    likely via gnome-extensions(1) or the GNOME Extensions app) and
    return False without re-enabling. The launcher's auto-XWayland
    fallback (Phase A) then takes over on this and every subsequent
    launch, until the user re-enables the extension by hand.
    """
    import json
    import shutil
    import subprocess

    src = _find_bundled_gnome_extension_source(uuid)
    if src is None:
        _logger.warning("install_gnome_extension: bundled extension "
                        "source not found for uuid=%s", uuid)
        return False

    dst_dir = os.path.expanduser(
        "~/.local/share/gnome-shell/extensions")
    dst = os.path.join(dst_dir, uuid)

    # Patch metadata.json on the way in so the running gnome-shell
    # version is always in the allow-list. Without this, the
    # extension would silently auto-disable after every GNOME bump
    # unless we ship a new metadata.json release in lockstep.
    shell_version = _detect_gnome_shell_major_version()

    # Opt-out check: only relevant when the extension is already
    # installed. If the installed metadata is up to date for the
    # running shell and the extension is still disabled, the user
    # turned it off on purpose -- don't fight them.
    if (os.path.isdir(dst)
            and not is_gnome_extension_enabled(uuid)
            and _installed_metadata_lists_shell_version(
                dst, shell_version)):
        _logger.info(
            "Onboard GNOME Shell extension '%s' is installed but "
            "disabled by the user (metadata is current for "
            "shell-version=%s); respecting opt-out, returning False "
            "so the launcher's XWayland fallback takes over.",
            uuid, shell_version)
        return False

    try:
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            sp = os.path.join(src, name)
            dp = os.path.join(dst, name)
            if name == "metadata.json" and shell_version is not None:
                with open(sp, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                versions = set(str(v) for v in
                               meta.get("shell-version", []))
                versions.add(str(shell_version))
                meta["shell-version"] = sorted(
                    versions,
                    key=lambda v: int(v) if v.isdigit() else 0)
                new_bytes = json.dumps(meta, indent=2).encode("utf-8")
                _write_if_changed(dp, new_bytes)
            elif os.path.isdir(sp):
                # Copy nested dirs if any (we don't ship
                # any today, but locales/ etc. may be added later).
                if os.path.isdir(dp):
                    shutil.rmtree(dp)
                shutil.copytree(sp, dp)
            else:
                with open(sp, "rb") as f:
                    new_bytes = f.read()
                _write_if_changed(dp, new_bytes)
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("install_gnome_extension: copy failed: %s", e)
        return False

    # Ask gnome-extensions(1) to enable it. No-op if already enabled.
    enable_was_noop = is_gnome_extension_enabled(uuid)
    try:
        subprocess.run(["gnome-extensions", "enable", uuid],
                       check=True, timeout=3, capture_output=True)
    except (FileNotFoundError, subprocess.SubprocessError,
            subprocess.TimeoutExpired, OSError) as e:
        _logger.warning("install_gnome_extension: "
                        "'gnome-extensions enable %s' failed: %s",
                        uuid, e)
        return False

    # Verify it actually loaded -- it'll silently auto-disable if the
    # shell-version match fails despite our patching, or if
    # extension.js has a syntax error etc.
    enabled = is_gnome_extension_enabled(uuid)
    if not enabled:
        _logger.warning("Onboard GNOME Shell extension '%s' installed "
                        "at %s but did not enable (shell-version "
                        "mismatch or extension load error)",
                        uuid, dst)
        return False

    # Verify the running gnome-shell is executing the build we just
    # wrote to disk, not a cached previous build.
    bundled_id = _build_id_for_file(os.path.join(dst, "extension.js"))
    running_id = _running_extension_build_id(
        timeout=0.3 if enable_was_noop else 2.0)
    if bundled_id and running_id != bundled_id:
        _logger.warning(
            "Onboard GNOME Shell extension at %s is build %r on disk "
            "but the running gnome-shell is executing build %r. "
            "gnome-shell on Wayland cannot reload extension JS at "
            "runtime; log out and log back in to load the new build. "
            "Falling back to XWayland for focus protection until "
            "then.", dst, bundled_id, running_id)
        return False

    _logger.info("Onboard GNOME Shell extension '%s' installed "
                 "and enabled at %s (build %s)",
                 uuid, dst, bundled_id or "?")
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
    return False


# Keys used internally to remember the per-window layer-shell state. We cannot
# store these on the window object directly because GTK objects refuse foreign
# attributes; we use a weak dict instead.
import weakref
_layer_state = weakref.WeakValueDictionary()


class _LayerShellState:
    """ Holds the layer-shell flags we currently track for a single window. """
    def __init__(self):
        self.exclusive_zone = 0
        self.anchored = False
        self.expanded = False


def _get_state(window):
    state = _layer_state.get(id(window))
    if state is None:
        state = _LayerShellState()
        # WeakValueDictionary needs a strong ref *somewhere* -- stash it on the
        # window via gobject data. ``set_data`` keeps the python object alive.
        try:
            window.set_data("onboard-layer-shell-state", state)
        except Exception:
            pass
        _layer_state[id(window)] = state
    return state


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

    _get_state(window)  # initialise tracking
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
    _get_state(window).anchored = True
    _get_state(window).expanded = expand


def set_anchor_top(window, expand=False):
    """ Anchor a layer-shell window to the top edge. """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    ls.set_anchor(window, ls.Edge.TOP, True)
    ls.set_anchor(window, ls.Edge.BOTTOM, False)
    ls.set_anchor(window, ls.Edge.LEFT, expand)
    ls.set_anchor(window, ls.Edge.RIGHT, expand)
    _get_state(window).anchored = True
    _get_state(window).expanded = expand


def clear_anchors(window):
    """ Detach the window from screen edges (i.e. floating mode). """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    for edge in (ls.Edge.TOP, ls.Edge.BOTTOM, ls.Edge.LEFT, ls.Edge.RIGHT):
        ls.set_anchor(window, edge, False)
    _get_state(window).anchored = False
    _get_state(window).expanded = False


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
    _get_state(window).exclusive_zone = int(height)


def is_layer_window(window):
    """ True if ``window`` was successfully turned into a layer surface. """
    ls = _get_layer_shell()
    if ls is None:
        return False
    return ls.is_layer_window(window)


def set_keyboard_mode(window, mode):
    """
    Change the keyboard-interactivity mode of a layer-shell ``window``.

    ``mode`` must be a ``GtkLayerShell.KeyboardMode`` value
    (NONE / ON_DEMAND / EXCLUSIVE).
    No-op when the window isn't a layer surface or layer-shell isn't
    available.
    """
    ls = _get_layer_shell()
    if ls is None or not ls.is_layer_window(window):
        return
    ls.set_keyboard_mode(window, mode)


def get_keyboard_mode_on_demand():
    """
    Return the ``GtkLayerShell.KeyboardMode.ON_DEMAND`` enum value, or
    ``None`` when gtk-layer-shell isn't available. Lets callers stay
    agnostic to the GtkLayerShell import.
    """
    ls = _get_layer_shell()
    if ls is None:
        return None
    return ls.KeyboardMode.ON_DEMAND


def get_keyboard_mode_none():
    """
    Return the ``GtkLayerShell.KeyboardMode.NONE`` enum value, or
    ``None`` when gtk-layer-shell isn't available.
    """
    ls = _get_layer_shell()
    if ls is None:
        return None
    return ls.KeyboardMode.NONE


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
                "The Wayland compositor (running as you) needs read access "
                "to receive injected keystrokes. Install the udev rule "
                "/lib/udev/rules.d/72-onboard-uinput.rules and run "
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
