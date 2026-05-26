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
Shared functions for the bundled Onboard GNOME Shell extension.

Stdlib-only on purpose: imported by the ./onboard launcher *before* the
GDK backend is chosen, so it must NOT pull in gi or any module that does.
"""

from __future__ import division, print_function, unicode_literals

import os
import sys
import time
import hashlib
import logging
import subprocess


_logger = logging.getLogger(__name__)


GNOME_EXTENSION_UUID = "onboard@onboard.local"

# Path (relative to XDG_CACHE_HOME) of the marker file the GNOME
# extension writes on every enable(); used to detect a stale running build.
GNOME_BUILD_ID_MARKER_RELPATH = "onboard/extension-build-id"


def find_bundled_extension_source(uuid):
    """
    Return the absolute path to the bundled extension source tree for
    ``uuid``, or None.

    Search order:
      1. ``<project>/data/gnome-extension/<uuid>/`` (source-checkout
         layout, relative to this module's location)
      2. ``<sys.prefix>/share/onboard/gnome-extension/<uuid>/``
      3. ``/usr/share/onboard/gnome-extension/<uuid>/``
      4. ``/usr/local/share/onboard/gnome-extension/<uuid>/``
    """
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


def build_id_for_file(path):
    """
    Return the SHA-256 prefix (16 hex chars) of the file at ``path``,
    or None on read error. Used as the build-id for the GNOME Shell
    extension: same bytes -> same id, on both the Python install side
    and the JS enable() side. Truncated to 16 chars because it's used
    purely for equality comparison, not security.
    """
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def is_gnome_extension_enabled(uuid=GNOME_EXTENSION_UUID):
    """
    True if a GNOME Shell extension with the given UUID is currently
    enabled (per `gnome-extensions list --enabled`). Returns False on
    any error -- the result is advisory; callers should not depend on
    it for correctness.
    """
    try:
        out = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            check=True, timeout=2,
            capture_output=True, text=True).stdout
    except (FileNotFoundError, subprocess.SubprocessError,
            subprocess.TimeoutExpired, OSError):
        return False
    return uuid in out.split()


def running_extension_build_id(timeout=0.0):
    """
    Return the build-id the currently-loaded GNOME extension wrote on
    enable(), or None if no marker is present within ``timeout``
    seconds.

    The marker write happens inside gnome-shell *after*
    ``gnome-extensions enable`` has returned, so right after a fresh
    install callers may need to wait briefly (timeout > 0) for the
    extension's enable() to run. At startup (timeout=0) the marker is
    either already on disk from a previous session or it isn't.
    """
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


def is_gnome_extension_installed_and_current(uuid=GNOME_EXTENSION_UUID,
                                             timeout=0.0):
    """
    True iff the bundled GNOME extension is enabled AND the running
    gnome-shell is executing the same build (extension.js bytes) as
    the bundled source on disk.

    Mutter on Wayland imports each extension's JS once per session
    and caches the module, so after an Onboard upgrade the
    freshly-copied extension.js sits on disk while the running shell
    keeps executing the previous build. We compare the SHA-256
    prefix of the bundled extension.js against the marker file the
    extension's enable() writes on load.

    ``timeout`` is forwarded to :func:`running_extension_build_id` --
    keep at 0 for startup polling, pass a small positive value
    immediately after enabling the extension.
    """
    if not is_gnome_extension_enabled(uuid):
        return False
    src = find_bundled_extension_source(uuid)
    if src is None:
        return False
    bundled_id = build_id_for_file(os.path.join(src, "extension.js"))
    if bundled_id is None:
        return False
    return running_extension_build_id(timeout=timeout) == bundled_id


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

def _detect_gnome_shell_major_version():
    """
    Return the integer major version of the running gnome-shell
    (e.g. 48) or None if it can't be determined.

    Used by :func:`install_gnome_extension` so we can inject the
    running shell-version into metadata.json's allow-list and the
    extension doesn't silently auto-disable after every GNOME bump.
    """
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

    src = find_bundled_extension_source(uuid)
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
        r = subprocess.run(["gnome-extensions", "enable", uuid],
                           timeout=3, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        _logger.warning("install_gnome_extension: "
                        "'gnome-extensions enable %s' could not run: %s",
                        uuid, e)
        return False
    if r.returncode != 0:
        stderr = (r.stderr or "").strip() or "(no stderr)"
        if "does not exist" in stderr.lower():
            _logger.warning(
                "Onboard GNOME Shell extension '%s' was just written "
                "to %s but 'gnome-extensions enable' reports: %s. This "
                "is normal: usually gnome-shell only scans extension directories "
                "at session start. Log out and log back "
                "in to load the extension; Onboard will then run on "
                "native Wayland. Falling back to XWayland for this "
                "session.", uuid, dst, stderr)
        else:
            _logger.warning(
                "install_gnome_extension: 'gnome-extensions enable %s' "
                "exited %d: %s", uuid, r.returncode, stderr)
        return False

    # Verify it actually loaded -- it'll silently auto-disable if the
    # shell-version match fails despite our patching, or if
    # extension.js has a syntax error etc.
    enabled = is_gnome_extension_enabled(uuid)
    if not enabled:
        _logger.warning(
            "Onboard GNOME Shell extension '%s' installed at %s and "
            "'gnome-extensions enable' returned success, but the "
            "extension is not in the enabled list afterwards. "
            "gnome-shell silently auto-disables on shell-version "
            "mismatch or a load-time error in extension.js -- check "
            "'journalctl --user -t gnome-shell' for the actual error.",
            uuid, dst)
        return False

    # Verify the running gnome-shell is executing the build we just
    # wrote to disk, not a cached previous build.
    bundled_id = build_id_for_file(os.path.join(dst, "extension.js"))
    poll_timeout = 0.3 if enable_was_noop else 2.0
    running_id = running_extension_build_id(timeout=poll_timeout)
    if running_id is None:
        _logger.warning(
            "Onboard GNOME Shell extension at %s is listed as "
            "enabled but a successful start was not detected after %.1fs (no "
            "build-id marker at $XDG_CACHE_HOME/%s). "
            "Falling back to XWayland for this session.",
            dst, poll_timeout, GNOME_BUILD_ID_MARKER_RELPATH)
        return False
    if bundled_id and running_id != bundled_id:
        _logger.warning(
            "Onboard GNOME Shell extension at %s is build %r on "
            "disk but the running gnome-shell is executing build "
            "%r (cached from a previous Onboard release). "
            "gnome-shell on Wayland cannot reload extension JS at "
            "runtime; log out and log back in to pick up the new "
            "build. Falling back to XWayland until then.",
            dst, bundled_id, running_id)
        return False

    _logger.info("Onboard GNOME Shell extension '%s' installed "
                 "and enabled at %s (build %s)",
                 uuid, dst, bundled_id or "?")
    return True
