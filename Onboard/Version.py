# -*- coding: utf-8 -*-

# Copyright © 2016 marmuta <marmvta@gmail.com>
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

import gi


def require_gi_versions():
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkX11', '3.0')
    gi.require_version('Pango', '1.0')
    gi.require_version('PangoCairo', '1.0')

    # Atspi is not required
    try:
        gi.require_version('Atspi', '2.0')
    except ValueError:
        pass

    # AyatanaAppIndicator3 is not required
    try:
        gi.require_version('AyatanaAppIndicator3', '0.1')
    except ValueError:
        pass

