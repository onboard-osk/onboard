# -*- coding: utf-8 -*-

# Copyright © 2013-2017 marmuta <marmvta@gmail.com>
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
Global definitions.
"""

from __future__ import division, print_function, unicode_literals

from gi.repository import Gdk

try:
    from enum import Enum
except ImportError as e:
    # Fallback class for python versions before 3.4 (Precise).
    class _EnumMeta(type):

        def __new__(cls, name, bases, namespace):
            class EnumValue(int):
                def __repr__(self):
                    return "<%s.%s: %r>" % (
                        self.__class__.__name__, self.name,
                        int(self))

                def __str__(self):
                    return "%s.%s" % (
                        self.__class__.__name__, self.name)

            members = {}
            for name, value in namespace.items():
                if isinstance(value, int) and not name.startswith("_"):
                    ev = EnumValue(value)
                    ev.name = name
                    namespace[name] = ev
                    members[name] = ev
            result = type.__new__(cls, name, bases, dict(namespace))
            result.__members__ = members
            return result

        def __call__(cls, value):   # noqa: flake8, no self here
            for ev in cls.__members__.values():
                if value == ev:
                    return ev

    class Enum(metaclass=_EnumMeta):
        """
        Simple Enum implementation that emulates python 3.4's Enum interface.

        Supports:
        - class style enum definition
            class xyz(Enum):
                value1 = 1
                value2 = 2

        - name retrieval from enum value
            name = KeySynthEnum.AUTO.name

        - conversion from int to enum value
                enum_value = KeySynthEnum(3)
        """
        pass



# Name of the /dev/uinput device when key-synth is set to uinput.
UINPUT_DEVICE_NAME = "Onboard on-screen keyboard"


class DesktopEnvironmentEnum(Enum):
    (
        Unknown,
        Cinnamon,
        GNOME_Shell,
        GNOME_Classic,
        KDE,
        LXDE,
        LXQT,
        MATE,
        Unity,
        XFCE,
    ) = range(10)


class StatusIconProviderEnum:
    (
        auto,
        GtkStatusIcon,
        AppIndicator,
    ) = range(3)


class StatusIconLeftClickActionEnum:
    (
        menu,
        toggle,
    ) = range(2)


class KeySynthEnum(Enum):
    (
        AUTO,
        XTEST,
        UINPUT,
        ATSPI,
    ) = range(4)


class InputEventSourceEnum:
    (
        GTK,
        XINPUT,
    ) = range(2)


class TouchInputEnum:
    (
        NONE,
        SINGLE,
        MULTI,
    ) = range(3)


class PhysicalLayoutEnum:
    """ Physical key geometry of the alphanumeric block. """
    (
        AUTO,   # derive from the XKB keyboard model (X11 only)
        ISO,    # two-row Enter, narrow left Shift, extra key left of Z
        ANSI,   # single-row Enter, wide left Shift, no extra key
    ) = range(3)

class LearningBehavior:
    (
        NOTHING,
        KNOWN_ONLY,
        ALL,
    ) = range(3)

# auto-show repositioning
class RepositionMethodEnum:
    (
        NONE,
        PREVENT_OCCLUSION,          # Stay put at the user selected home
                                    # position, only move when really necessary.
        REDUCE_POINTER_TRAVEL,      # Move closer to the accessible, but try
                                    # to stay out of top level windows.
    ) = range(3)

# window corners
class Handle:
    NORTH_WEST = Gdk.WindowEdge.NORTH_WEST
    NORTH = Gdk.WindowEdge.NORTH
    NORTH_EAST = Gdk.WindowEdge.NORTH_EAST
    WEST = Gdk.WindowEdge.WEST
    EAST = Gdk.WindowEdge.EAST
    SOUTH_WEST = Gdk.WindowEdge.SOUTH_WEST
    SOUTH = Gdk.WindowEdge.SOUTH
    SOUTH_EAST   = Gdk.WindowEdge.SOUTH_EAST
    class MOVE: pass

Handle.EDGES  =   (Handle.EAST,
                   Handle.SOUTH,
                   Handle.WEST,
                   Handle.NORTH)

Handle.CORNERS =  (Handle.SOUTH_EAST,
                   Handle.SOUTH_WEST,
                   Handle.NORTH_WEST,
                   Handle.NORTH_EAST)

Handle.RESIZERS = (Handle.EAST,
                   Handle.SOUTH_EAST,
                   Handle.SOUTH,
                   Handle.SOUTH_WEST,
                   Handle.WEST,
                   Handle.NORTH_WEST,
                   Handle.NORTH,
                   Handle.NORTH_EAST)

Handle.TOP_RESIZERS = (
                   Handle.EAST,
                   Handle.WEST,
                   Handle.NORTH_WEST,
                   Handle.NORTH,
                   Handle.NORTH_EAST)

Handle.BOTTOM_RESIZERS = (
                   Handle.EAST,
                   Handle.SOUTH_EAST,
                   Handle.SOUTH,
                   Handle.SOUTH_WEST,
                   Handle.WEST)

Handle.RESIZE_MOVE = Handle.RESIZERS + (Handle.MOVE, )
Handle.ALL = Handle.RESIZE_MOVE

Handle.CURSOR_TYPES = {
    Handle.NORTH_WEST : Gdk.CursorType.TOP_LEFT_CORNER,
    Handle.NORTH      : Gdk.CursorType.TOP_SIDE,
    Handle.NORTH_EAST : Gdk.CursorType.TOP_RIGHT_CORNER,
    Handle.WEST       : Gdk.CursorType.LEFT_SIDE,
    Handle.EAST       : Gdk.CursorType.RIGHT_SIDE,
    Handle.SOUTH_WEST : Gdk.CursorType.BOTTOM_LEFT_CORNER,
    Handle.SOUTH      : Gdk.CursorType.BOTTOM_SIDE,
    Handle.SOUTH_EAST : Gdk.CursorType.BOTTOM_RIGHT_CORNER,
    Handle.MOVE       : Gdk.CursorType.FLEUR}

Handle.IDS = {
    Handle.EAST       : "E",
    Handle.SOUTH_WEST : "SW",
    Handle.SOUTH      : "S",
    Handle.SOUTH_EAST : "SE",
    Handle.WEST       : "W",
    Handle.NORTH_WEST : "NW",
    Handle.NORTH      : "N",
    Handle.NORTH_EAST : "NE",
    Handle.MOVE       : "M"}

Handle.RIDS = {
    "E"  : Handle.EAST,
    "SW" : Handle.SOUTH_WEST,
    "S"  : Handle.SOUTH,
    "SE" : Handle.SOUTH_EAST,
    "W"  : Handle.WEST,
    "NW" : Handle.NORTH_WEST,
    "N"  : Handle.NORTH,
    "NE" : Handle.NORTH_EAST,
    "M"  : Handle.MOVE}


class HandleFunction:
    NORMAL = 0
    ASPECT_RATIO = 1


class DockingEdge:
    TOP = 0
    BOTTOM = 3


class DockingMonitor:
    ACTIVE   = 100
    PRIMARY  = 110
    MONITOR0 = 0
    MONITOR1 = 1
    MONITOR2 = 2
    MONITOR3 = 3
    MONITOR4 = 4
    MONITOR5 = 5
    MONITOR6 = 6
    MONITOR7 = 7
    MONITOR8 = 8


class UIMask:
    """ enum of keyboard UI update flags """
    (
        CONTROLLERS,
        SUGGESTIONS,
        LAYOUT,
        LAYERS,
        SIZE,
        REDRAW,
    ) = (1<<bit for bit in range(6))

    ALL = -1

