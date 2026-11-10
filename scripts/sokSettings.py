# Copyright © 2009 Chris Jones <tortoise@tortuga>
# Copyright © 2012-2013 marmuta <marmvta@gmail.com>
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

import sys

# Use the interpreter running Onboard, not whatever "python3" is first on
# PATH (e.g. a Linuxbrew python without GObject bindings).
PYTHON_EXECUTABLE = sys.executable

from gi.repository import GLib

def run():
    GLib.spawn_async(argv=[PYTHON_EXECUTABLE,
                           "-cfrom Onboard.settings import Settings\ns = Settings(False)"],
                     flags=GLib.SpawnFlags.SEARCH_PATH)

