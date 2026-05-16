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
Shared path / hash helpers for the bundled Onboard GNOME Shell extension.

Stdlib-only on purpose: imported by the ./onboard launcher *before* the
GDK backend is chosen, so it must NOT pull in gi or any module that does.
"""

from __future__ import division, print_function, unicode_literals

import os
import sys
import hashlib


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
