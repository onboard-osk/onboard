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
Shared path / hash / status helpers for the bundled Onboard GNOME Shell extension.

Stdlib-only on purpose: imported by the ./onboard launcher *before* the
GDK backend is chosen, so it must NOT pull in gi or any module that does.
"""

from __future__ import division, print_function, unicode_literals

import os
import sys
import time
import hashlib
import subprocess


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
