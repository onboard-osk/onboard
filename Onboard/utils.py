# -*- coding: utf-8 -*-

# Copyright © 2007-2009 Chris Jones <tortoise@tortuga>
# Copyright © 2008, 2010 Francesco Fumanti <francesco.fumanti@gmx.net>
# Copyright © 2012 Gerd Kohlberger <lowfi@chello.at>
# Copyright © 2011-2016 marmuta <marmvta@gmail.com>
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

from __future__ import division, print_function, unicode_literals

import sys
import os
import time
import re
import colorsys
import gettext
import subprocess
from math import pi, sin, cos, sqrt, log, ceil
from contextlib import contextmanager

import logging
from functools import reduce

# GLib is imported lazily inside some functions so that
# `import Onboard.utils` (triggered by Onboard/__init__.py) stays gi-free.
# The ./onboard launcher relies on this to avoid pulling gi into the parent
# process before it has finished choosing the GDK backend.

_logger = logging.getLogger("utils")


# keycodes
class KeyCode:
    Return   = 36
    KP_Enter = 104
    C        = 54


class Modifiers:
    # 1      2     4    8    16     32    64     128
    SHIFT, CAPS, CTRL, ALT, NUMLK, MOD3, SUPER, ALTGR = \
               (1<<bit for bit in range(8))

# modifiers affecting labels
LABEL_MODIFIERS = Modifiers.SHIFT | \
                  Modifiers.CAPS | \
                  Modifiers.NUMLK | \
                  Modifiers.ALTGR

modifiers = {"shift":1,
             "caps":2,
             "control":4,
             "mod1":8,   # Left Alt
             "mod2":16,  # NumLk
             "mod3":32,
             "mod4":64,  # Super
             "mod5":128, # Alt Gr
            }

modGroups = {"SHIFT" : ["LFSH"],
             "CTRL"  : ["LCTL"],
            }

modList = [["LWIN", ("Win",64)],
           ["RTSH", ("⇧", 1)],
           ["LFSH", ("⇧", 1)],
           ["RALT", ("Alt Gr", 128)],
           ["LALT", ("Alt", 8)],
           ["RCTL", ("Ctrl", 4)],
           ["LCTL", ("Ctrl", 4)],
           ["CAPS", ("CAPS", 2)],
           ["NMLK", ("Nm\nLk",16)]]

modDic = dict(modList)

otherDic = {"RWIN" : "Win",
            "MENU" : "Menu",
            "BKSP" : "⇦",
            "RTRN" : "Return",
            "TAB" : "Tab",
            "INS":"Ins",
            "HOME":"Hm",
            "PGUP": "Pg\nUp",
            "DELE":"Del",
            "END":"End",
            "PGDN":"Pg\nDn",
            "UP":  "↑",
            "DOWN":"↓",
            "LEFT" : "←",
            "RGHT" : "→",
            "KP0" : "0",
            "KP1" : "1",
            "KP2" : "2",
            "KP3" : "3",
            "KP4" : "4",
            "KP5" : "5",
            "KP6" : "6",
            "KP7" : "7",
            "KP8" : "8",
            "KP9" : "9",
            "KPDL":"Del",
            "KPEN": "Ent" }

funcKeys = (("ESC",65307),
            ("F1",65470),
            ("F2",65471),
            ("F3",65472),
            ("F4", 65473),
            ("F5", 65474),
            ("F6",65475),
            ("F7",65476),
            ("F8",65477),
            ("F9",65478),
            ("F10",65479),
            ("F11", 65480),
            ("F12", 65481),
            ("Prnt", 65377),
            ("Scroll", 65300),
            ("Pause", 65299))

keysyms = {"space" : 0x0020,
           "kp_space" : 65408,
           "insert" : 0xff63,
           "home" : 0xff50,
           "page_up" : 0xff55,
           "page_down" : 0xff56,
           "end" :0xff57,
           "delete" : 0xffff,
           "return" : 65293,
           "backspace" : 65288,
           "left" : 0xff51,
           "up" : 0xff52,
           "right" : 0xff53,
           "down" : 0xff54,}

def get_keysym_from_name(name):
    return keysyms[name]

def parse_key_combination(combo, avaliable_key_ids = None):
    r"""
    Parses a key combination into a list of modifier masks and key_ids.
    The key-id part of the combo may contain a regex pattern.

    Doctests:

    # modifiers
    >>> parse_key_combination(["TAB"], ["TAB"])
    [('TAB', 0)]
    >>> parse_key_combination(["LALT", "TAB"], ["TAB"])
    [('TAB', 8)]
    >>> parse_key_combination(["LALT", "LFSH", "TAB"], ["TAB"])
    [('TAB', 9)]
    >>> parse_key_combination(["LWIN", "RTSH", "LFSH", "RALT", "LALT", "RCTL", "LCTL", "CAPS", "NMLK", "TAB"], ["TAB"])
    [('TAB', 223)]

    # modifier groups
    >>> parse_key_combination(["CTRL", "SHIFT", "TAB"], ["TAB"])
    [('TAB', 5)]

    # regex
    >>> parse_key_combination([r"F\d+"], ["TAB", "F1", "F2", "F3", "F9"])
    [('F1', 0), ('F2', 0), ('F3', 0), ('F9', 0)]
    """
    modifiers = combo[:-1]
    key_pattern = combo[-1]

    # find modifier mask
    mod_mask = parse_modifier_strings(modifiers)
    if mod_mask is None:
        return None

    # match regex key id with all available ids
    results = []
    pattern = re.compile(key_pattern)
    for key_id in avaliable_key_ids:
        match = pattern.match(key_id)
        if match and match.group() == key_id:
            results.append((key_id, mod_mask))

    return results

def parse_modifier_strings(modifiers):
    """ Build modifier mask from modifier strings. """
    mod_mask = 0
    for modifier in modifiers:
        m = modDic.get(modifier)
        if not m is None:
            mod_mask |= m[1]
        else:
            group = modGroups.get(modifier)
            if not group is None:
                for mod in group:
                    mod_mask |= modDic[mod][1]
            else:
                _logger.warning("unrecognized modifier '{}'; try one of {}" \
                                .format(modifier, ",".join(m[0] for m in modList)))
                mod_mask = None
                break

    return mod_mask

def run_script(script):
    a =__import__(script)
    a.run()

def toprettyxml(domdoc):
    ugly_xml = domdoc.toprettyxml(indent='  ')
    # Join lines with text elements with their tag lines
    pattern = re.compile(r'>\n\s+([^<>\s].*?)\n\s+</', re.DOTALL)
    pretty_xml = pattern.sub(r'>\g<1></', ugly_xml)

    # Work around http://bugs.python.org/issue5752
    pretty_xml = re.sub(
           '"[^"]*"',
           lambda m: m.group(0).replace("\n", "&#10;"),
           pretty_xml)

    # remove empty lines
    pretty_xml = os.linesep.join( \
                    [s for s in pretty_xml.splitlines() if s.strip()])
    return pretty_xml


def dec_to_hex_colour(dec):
    hexString = hex(int(255*dec))[2:]
    if len(hexString) == 1:
        hexString = "0" + hexString

    return hexString

def xml_get_text(dom_node, tag_name):
    """ extract text from a dom node """
    nodelist = dom_node.getElementsByTagName(tag_name)
    if not nodelist:
        return None
    rc = []
    for node in nodelist[0].childNodes:
        if node.nodeType == node.TEXT_NODE:
            rc.append(node.data)
    return ''.join(rc).strip()

def matmult(m, v):
    """ Matrix-vector multiplication """
    nrows = len(m)
    w = [None] * nrows
    for row in range(nrows):
        w[row] = reduce(lambda x,y: x+y, list(map(lambda x,y: x*y, m[row], v)))
    return w

def hexstring_to_float(hexString):
    return float(int(hexString, 16))

def hexcolor_to_rgba(color):
    """
    convert '#rrggbb' or '#rrggbbaa' to (r, g, b, a)

    Doctests:
    >>> def test(color):
    ...     rgba = hexcolor_to_rgba(color)
    ...     if rgba is None:
    ...         print(repr(rgba))
    ...     else:
    ...         print(repr([round(c, 2) for c in rgba]))

    >>> test("#1a2b3c")
    [0.1, 0.17, 0.24, 1.0]

    >>> test("#1a2b3c4d")
    [0.1, 0.17, 0.24, 0.3]

    >>> test("")
    None

    >>> test("1a2b3c")
    None

    >>> test("1a2b3c4d")
    None

    >>> test("#1a2b3c4dx")
    None

    >>> test("#1a2b3cx")
    None

    >>> test("#1a2bx")
    None

    >>> test("#1aXb3c4d")
    None
    """
    rgba = None
    n = len(color)
    if n == 7 or n == 9:
        try:
            rgba = [hexstring_to_float(color[1:3])/255,
                    hexstring_to_float(color[3:5])/255,
                    hexstring_to_float(color[5:7])/255]
            if n == 9:
                rgba.append(hexstring_to_float(color[7:9])/255)
            else:
                rgba.append(1.0)
        except ValueError:
            rgba = None
    return rgba

class dictproperty(object):
    """ Property implementation for dictionaries """

    class _proxy(object):

        def __init__(self, obj, fget, fset, fdel):
            self._obj = obj
            self._fget = fget
            self._fset = fset
            self._fdel = fdel

        def __getitem__(self, key):
            if self._fget is None:
                raise TypeError("can't read item")
            return self._fget(self._obj, key)

        def __setitem__(self, key, value):
            if self._fset is None:
                raise TypeError("can't set item")
            self._fset(self._obj, key, value)

        def __delitem__(self, key):
            if self._fdel is None:
                raise TypeError("can't delete item")
            self._fdel(self._obj, key)

    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        self._fget = fget
        self._fset = fset
        self._fdel = fdel
        self.__doc__ = doc

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return self._proxy(obj, self._fget, self._fset, self._fdel)

def unpack_name_value_list(_list, num_values=2, key_type = str):
    r"""
    Converts a list of strings into a dict of tuples.
    Sample list: ['LWIN:label:super', ...]
    ":" in a value must be escaped as "\:"
    "\" in a value must be escaped as "\\"
    """
    result = {}

    # Awkward fixed regexes; todo: Allow arbirary number of values
    if num_values == 1:
        pattern = re.compile(r"""([^\s:]+)             # name
                                 : ((?:\\.|[^\\:])*)   # first value
                             """, re.VERBOSE)
    elif num_values == 2:
        pattern = re.compile(r"""([^\s:]+)             # name
                                 : ((?:\\.|[^\\:])*)   # first value
                                 : ((?:\\.|[^\\:])*)   # second value
                             """, re.VERBOSE)
    else:
        assert(False)  # unsupported number of values

    for text in _list:
        tuples = pattern.findall(text)
        if tuples:
            a = []
            for t in tuples[0]:
                t = t.replace("\\\\", "\\")   # unescape backslash
                t = t.replace("\\:", ":")     # unescape separator
                a.append(t)

            if key_type == str:
                item = {a[0] : (a[1:])}
            elif key_type == int:
                item = {int(a[0]) : (a[1:])}
            else:
                assert(False)
            result.update(item)

    return result

def pack_name_value_list(tuples, field_sep=":", name_sep=":"):
    """
    Converts a dict of tuples to a string array. It creates one string
    per dict key, with the key-string separated by <name_sep> and
    individual tuple elements separated by <field_sep>.
    """
    result = []
    for t in list(tuples.items()):
        text = str(t[0])
        sep = name_sep
        for value in t[1]:
            value = value.replace("\\", "\\\\")   # escape backslash
            value = value.replace(sep, "\\"+sep)  # escape separator
            text += sep + '%s' % value
            sep = field_sep
        result.append(text)
    return result

def merge_tuple_strings(text1, text2):
    """
    Existing entries in text1 will be kept or overwritten by text2.
    """
    tuples1 = unpack_name_value_tuples(text1)
    tuples2 = unpack_name_value_tuples(text2)
    for key,values in list(tuples2.items()):
        tuples1[key] = values
    return pack_name_value_tuples(tuples1)


class Rect:
    """
    Simple rectangle class.
    Left and top are included, right and bottom excluded.
    Attributes can be accessed by name or by index, e.g. rect.x or rect[0].
    """

    attributes = ("x", "y", "w", "h")

    def __init__(self, x = 0, y = 0, w = 0, h = 0):
        self.x = x
        self.y = y
        self.w = w
        self.h = h

    def __len__(self):
        return 4

    def __getitem__(self, index):
        """ Collection interface for rvalues, unpacking with '*' operator """
        return getattr(self, self.attributes[index])

    def __setitem__(self, index, value):
        """ Collection interface for lvalues """
        return setattr(self, self.attributes[index], value)

    def __str__(self):
        return "Rect(" + \
            " ".join("{}={:.1f}".format(a, getattr(self, a)) \
                     for a in self.attributes) + \
            ")"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        return self.x == other.x and \
               self.y == other.y and \
               self.w == other.w and \
               self.h == other.h

    def __ne__(self, other):
        return self.x != other.x or \
               self.y != other.y or \
               self.w != other.w or \
               self.h != other.h

    @staticmethod
    def from_extents(x0, y0, x1, y1):
        """
        New Rect from two points.
        x0 and y0 are considered inside, x1 and y1 are just outside the Rect.
        """
        return Rect(x0, y0, x1 - x0, y1 - y0)

    @staticmethod
    def from_position_size(position, size):
        """
        New Rect from two tuples.
        """
        return Rect(position[0], position[1], size[0], size[1])

    @staticmethod
    def from_points(p0, p1):
        """
        New Rect from two points, left-top and right-botton.
        The former lies inside, while the latter is considered to be
        just outside the rect.
        """
        return Rect(p0[0], p0[1], p1[0] - p0[0], p1[1] - p0[1])

    def to_extents(self):
        return self.x, self.y , self.x + self.w, self.y + self.h

    def to_position_size(self):
        return (self.x, self.y), (self.w, self.h)

    def copy(self):
        return Rect(self.x, self.y, self.w, self.h)

    def is_empty(self):
        return self.w <= 0 or self.h <= 0

    def get_position(self):
        return (self.x, self.y)

    def get_size(self):
        return (self.w, self.h)

    def get_center(self):
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def get_center_x(self):
        return self.x + self.w / 2.0

    def get_center_y(self):
        return self.y + self.h / 2.0

    def top(self):
        return self.y

    def left(self):
        return self.x

    def right(self):
        return self.x + self.w

    def bottom(self):
        return self.y + self.h

    def left_top(self):
        return self.x, self.y

    def is_point_within(self, point):
        """ True, if the given point lies inside the rectangle """
        if self.x <= point[0] and \
           self.x + self.w > point[0] and \
           self.y <= point[1] and \
           self.y + self.h > point[1]:
            return True
        return False

    def round(self):
        return Rect(round(self.x), round(self.y), round(self.w), round(self.h))

    def int(self):
        return Rect(int(self.x), int(self.y), int(self.w), int(self.h))

    def scale(self, kx, ky = None):
        if ky == None:
            ky = kx
        return Rect(self.x * kx, self.y * ky, self.w * kx, self.h * ky)

    def offset(self, dx, dy):
        """
        Returns a new Rect displaced by dx and dy.
        """
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def inflate(self, dx, dy = None):
        """
        Returns a new Rect which is larger by dx and dy on all sides.
        """
        if dy is None:
            dy = dx
        return Rect(self.x-dx, self.y-dy, self.w+2*dx, self.h+2*dy)

    def apply_border(self, left, top, right, bottom):
        """
        Returns a new Rect which is larger by l, t, r, b on all sides.
        """
        return Rect(self.x-left, self.y-top,
                    self.w+left+right, self.h+top+bottom)

    def deflate(self, dx, dy = None):
        """
        Returns a new Rect which is smaller by dx and dy on all sides.
        """
        if dy is None:
            dy = dx
        return Rect(self.x+dx, self.y+dy, self.w-2*dx, self.h-2*dy)

    def grow(self, kx, ky = None):
        """
        Returns a new Rect with its size multiplied by kx, ky.
        """
        if ky is None:
            ky = kx
        w = self.w * kx
        h = self.h * ky
        return Rect(self.x + (self.w - w) / 2.0,
                    self.y + (self.h - h) / 2.0,
                    w, h)

    def intersects(self, rect):
        """
        Doctests:
        >>> Rect(0, 0, 1, 1).intersects(Rect(0, 0, 1, 1))
        True
        >>> Rect(0, 0, 1, 1).intersects(Rect(1, 0, 1, 1))
        False
        >>> Rect(1, 0, 1, 1).intersects(Rect(0, 0, 1, 1))
        False
        >>> Rect(0, 0, 1, 1).intersects(Rect(0, 1, 1, 1))
        False
        >>> Rect(0, 1, 1, 1).intersects(Rect(0, 0, 1, 1))
        False
        """
        # return not self.intersection(rect).is_empty()
        return not (self.x >= rect.x + rect.w or
                    self.x + self.w <= rect.x or
                    self.y >= rect.y + rect.h or
                    self.y + self.h <= rect.y)

    def intersection(self, rect):
        x0 = max(self.x, rect.x)
        y0 = max(self.y, rect.y)
        x1 = min(self.x + self.w, rect.x + rect.w)
        y1 = min(self.y + self.h, rect.y + rect.h)
        if x0 > x1 or y0 > y1:
            return Rect()
        else:
            return Rect(x0, y0, x1 - x0, y1 - y0)

    def union(self, rect):
        x0 = min(self.x, rect.x)
        y0 = min(self.y, rect.y)
        x1 = max(self.x + self.w, rect.x + rect.w)
        y1 = max(self.y + self.h, rect.y + rect.h)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def inscribe_with_aspect(self, rect, x_align=0.5, y_align=0.5):
        """ Returns a new Rect with the aspect ratio of self
            that fits inside the given rectangle.
        """
        if self.is_empty() or rect.is_empty():
            return Rect()

        src_aspect = self.w / float(self.h)
        dst_aspect = rect.w / float(rect.h)

        result = rect.copy()
        if dst_aspect > src_aspect:
            result.w = rect.h * src_aspect
            result.x += x_align * (rect.w - result.w)
        else:
            result.h = rect.w / src_aspect
            result.y += y_align * (rect.h - result.h)
        return result

    def resize_to_aspect(self, aspect_rect):
        """
        Resize self to the aspect ratio of aspect_rect.
        """
        if self.is_empty() or aspect_rect.is_empty():
            return Rect()

        src_aspect = aspect_rect.w / float(aspect_rect.h)
        dst_aspect = self.w / float(self.h)

        result = self.copy()
        if dst_aspect > src_aspect:
            result.w = self.h * src_aspect
        else:
            result.h = self.w / src_aspect
        return result

    def resize_to_aspect_range(self, aspect_rect, aspect_change_range):
        """
        Resize self to get the aspect ratio of aspect_rect, but limited
        to the given aspect range.
        """
        if self.is_empty() or aspect_rect.is_empty():
            return Rect()

        r = aspect_rect
        if r.h:
            a0 = r.w / float(r.h)
            a0_max = a0 * aspect_change_range[1]
            a1 = self.w / float(self.h)
            a = min(a1, a0_max)

            r = Rect(0, 0, a, 1.0)
            r = Rect(0, 0, a, 1.0)
        return self.resize_to_aspect(r)

    def align_rect(self, rect, x_align = 0.5, y_align = 0.5):
        """
        Aligns the given rect inside of self.
        x/y_align = 0.5 centers rect.
        """
        x = self.x + (self.w - rect.w) * x_align
        y = self.y + (self.h - rect.h) * y_align
        return Rect(x, y, rect.w, rect.h)

    def align_at_point(self, x, y, x_align = 0.5, y_align = 0.5):
        """
        Aligns the given rect to a point.
        x/y_align = 0.5 centers rect.
        """
        x = x - self.w * x_align
        y = y - self.h * y_align
        return Rect(x, y, self.w, self.h)

    def subdivide(self, columns, rows, x_spacing = None, y_spacing = None):
        """ Divide self into columns x rows sub-rectangles """
        if y_spacing is None:
            y_spacing = x_spacing
        if x_spacing is None:
            x_spacing = 0

        x, y, w, h = self
        ws = (self.w - (columns - 1) * x_spacing) / float(columns)
        hs = (self.h - (rows - 1)    * y_spacing) / float(rows)

        rects = []
        y = self.y
        for row in range(rows):
            x = self.x
            for column in range(columns):
                rects.append(Rect(x, y, ws, hs))
                x += ws + x_spacing
            y += hs + y_spacing

        return rects

    def flow_layout(self, item_rect, num_items,
                    x_spacing=None, y_spacing=None,
                    flow_horizontally=True,
                    grow_horizontally=True):
        """
        Layout num_items sub-rectangles of size item_rect in grow_horizontal or
        columns-first order.
        """

        if y_spacing is None:
            y_spacing = x_spacing
        if x_spacing is None:
            x_spacing = 0

        rects = []
        bounds = self.copy()

        if grow_horizontally:
            # self determines height and minimum width.
            # Items may overflow the minimum width.
            nrows = int((self.h + y_spacing) / (item_rect.h + y_spacing))
            ncols = int(ceil(num_items / nrows))
            bounds.w = 0
        else:
            # self determines width and minimum height.
            # Items may overflow the minimum height.
            ncols = int((self.w + x_spacing) / (item_rect.w + x_spacing))
            nrows = int(ceil(num_items / ncols))
            bounds.h = 0

        if flow_horizontally:
            for row in range(nrows):
                for col in range(ncols):
                    x = self.x + item_rect.w * col + \
                        x_spacing * max((col - 1), 0)
                    y = self.y + item_rect.h * row + \
                        y_spacing * max((row - 1), 0)
                    r = Rect(x, y, item_rect.w, item_rect.h)
                    bounds = bounds.union(r)
                    rects.append(r)

                    if len(rects) >= num_items:
                        break

                if len(rects) >= num_items:
                    break
        else:
            for col in range(ncols):
                for row in range(nrows):
                    x = self.x + item_rect.w * col + \
                        x_spacing * max((col - 1), 0)
                    y = self.y + item_rect.h * row + \
                        y_spacing * max((row - 1), 0)
                    r = Rect(x, y, item_rect.w, item_rect.h)
                    bounds = bounds.union(r)
                    rects.append(r)

                    if len(rects) >= num_items:
                        break

                if len(rects) >= num_items:
                    break

        return rects, bounds


def brighten(amount, r, g, b, a=0.0):
    """ Make the given color brighter by amount a [-1.0...1.0] """
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l += amount
    if l > 1.0:
        l = 1.0
    if l < 0.0:
        l = 0.0
    return list(colorsys.hls_to_rgb(h, l, s)) + [a]

def linint_rgba(k, rgba1, rgba2):
    """ interpolate between two colors """
    linint = lambda k, a, b: a + (b - a) * k
    return [linint(k, rgba1[0], rgba12[0]),
            linint(k, rgba1[1], rgba12[1]),
            linint(k, rgba1[2], rgba12[2]),
            linint(k, rgba1[3], rgba12[3])]

def roundrect_arc(context, rect, r = 15):
    x0,y0 = rect.x, rect.y
    x1,y1 = x0 + rect.w, y0 + rect.h

    # top left
    context.move_to(x0+r, y0)

    # top right
    context.line_to(x1-r,y0)
    context.arc(x1-r, y0+r, r, -pi/2, 0)

    # bottom right
    context.line_to(x1, y1-r)
    context.arc(x1-r, y1-r, r, 0, pi/2)

    # bottom left
    context.line_to(x0+r, y1)
    context.arc(x0+r, y1-r, r, pi/2, pi)

    # top left
    context.line_to(x0, y0+r)
    context.arc(x0+r, y0+r, r, pi, pi*1.5)

    context.close_path ()

def roundrect_curve(context, rect, r_pct=100):
    """
    Uses B-splines for less even looks than with arcs, but
    still allows for approximate circles at r_pct = 100.
    """
    x0 = rect.x
    y0 = rect.y
    w  = rect.w
    h  = rect.h
    x1 = x0 + w
    y1 = y0 + h

    r = min(w, h) * min(r_pct/100.0, 0.5) # full range at 50%
    k = (r-1) * r_pct/200.0 # position of control points for circular curves

    line_to = context.line_to
    curve_to = context.curve_to

    # top left
    context.move_to(x0+r, y0)

    # top right
    line_to(x1-r,y0)
    curve_to(x1-k, y0, x1, y0+k, x1, y0+r)

    # bottom right
    line_to(x1, y1-r)
    curve_to(x1, y1-k, x1-k, y1, x1-r, y1)

    # bottom left
    line_to(x0+r, y1)
    curve_to(x0+k, y1, x0, y1-k, x0, y1-r)

    # top left
    line_to(x0, y0+r)
    curve_to(x0, y0+k, x0+k, y0, x0+r, y0)

    context.close_path ()


def roundrect_curve_custom(context, rect, r_pct=100,
                           corner_mask=0b1111):
    """
    Uses B-splines for less even looks than with arcs, but
    still allows for approximate circles at r_pct = 100.
    """
    x0 = rect.x
    y0 = rect.y
    w  = rect.w
    h  = rect.h
    x1 = x0 + w
    y1 = y0 + h

    # full range at 50%
    r = min(w, h) * min(r_pct / 100.0, 0.5)

    # position of control points for circular curves
    k = (r - 1) * r_pct / 200.0

    line_to = context.line_to
    curve_to = context.curve_to

    # top left
    if corner_mask & 0b0001:
        context.move_to(x0 + r, y0)
    else:
        context.move_to(x0, y0)

    # top right
    if corner_mask & 0b0010:
        line_to(x1 - r, y0)
        curve_to(x1 - k, y0, x1, y0 + k, x1, y0 + r)
    else:
        line_to(x1, y0)

    # bottom right
    if corner_mask & 0b0100:
        line_to(x1, y1 - r)
        curve_to(x1, y1 - k, x1 - k, y1, x1 - r, y1)
    else:
        line_to(x1, y1)

    # bottom left
    if corner_mask & 0b1000:
        line_to(x0 + r, y1)
        curve_to(x0 + k, y1, x0, y1 - k, x0, y1 - r)
    else:
        line_to(x0, y1)

    # top left
    if corner_mask & 0b0001:
        line_to(x0, y0 + r)
        curve_to(x0, y0 + k, x0 + k, y0, x0 + r, y0)
    else:
        line_to(x0, y0)

    context.close_path()


def rounded_polygon(cr, coords, r_pct, chamfer_size):
    path = polygon_to_rounded_path(coords, r_pct, chamfer_size)
    rounded_polygon_path_to_cairo_path(cr, path)

def polygon_to_rounded_path(coords, r_pct, chamfer_size):
    """
    Doctests:
    # simple rectangle, chamfer radius 0.
    >>> coords = [0, 0, 10, 0, 10, 10, 0, 10]
    >>> polygon_to_rounded_path(coords, 0, 0) # doctest: +NORMALIZE_WHITESPACE
    [(0.0, 0.0), (10.0, 0.0), (10.0, 0.0, 10.0, 0.0, 10.0, 0.0),
     (10.0, 10.0), (10.0, 10.0, 10.0, 10.0, 10.0, 10.0),
     (0.0, 10.0), (0.0, 10.0, 0.0, 10.0, 0.0, 10.0),
     (0.0, 0.0), (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]
    """
    path = []
    r = chamfer_size * 2.0 * min(r_pct/100.0, 0.5) # full range at 50%

    n = len(coords)
    for i in range(0, n, 2):
        i0 = i
        i1 = i + 2
        if i1 >= n:
            i1 -= n
        i2 = i + 4
        if i2 >= n:
            i2 -= n
        x0 = coords[i0]
        y0 = coords[i0+1]
        x1 = coords[i1]
        y1 = coords[i1+1]
        x2 = coords[i2]
        y2 = coords[i2+1]

        vax = x1 - x0
        vay = y1 - y0
        la = sqrt(vax*vax + vay*vay)
        uax = vax / la
        uay = vay / la

        vbx = x2 - x1
        vby = y2 - y1
        lb = sqrt(vbx*vbx + vby*vby)
        ubx = vbx / lb
        uby = vby / lb

        ra = min(r, la * 0.5)     # offset of curve begin and end
        rb = min(r, lb * 0.5)
        ka = (ra-1) * r_pct/200.0 # offset of control points
        kb = (rb-1) * r_pct/200.0

        if i == 0:
            x = x0 + ra*uax
            y = y0 + ra*uay
            path.append((x, y))

        x = x1 - ra*uax
        y = y1 - ra*uay
        path.append((x, y))

        x = x1 + rb*ubx
        y = y1 + rb*uby
        c0x = x1 - ka*uax
        c0y = y1 - ka*uay
        c1x = x1 + kb*ubx
        c1y = y1 + kb*uby
        path.append((x, y, c0x, c0y, c1x, c1y))

    return path

def rounded_polygon_path_to_cairo_path(cr, path):
    if path:
        cr.move_to(*path[0])
        for i in range(1, len(path), 2):
            p = path[i]
            cr.line_to(p[0], p[1])
            p = path[i+1]
            cr.curve_to(p[2], p[3], p[4], p[5], p[0], p[1])
        cr.close_path()

def rounded_path(cr, path, r_pct, chamfer_size):
    for polygon in path.iter_polygons():
        rounded_polygon(cr, polygon, r_pct, chamfer_size)

def round_corners(cr, r, x, y, w, h):
    """
    Paint 4 round corners.
    Currently x, y are ignored and assumed to be 0.
    """
    # top-left
    cr.curve_to (0, r, 0, 0, r, 0)
    cr.line_to (0, 0)
    cr.close_path()
    cr.fill()
    # top-right
    cr.curve_to (w, r, w, 0, w - r, 0)
    cr.line_to (w, 0)
    cr.close_path()
    cr.fill()
    # bottom-left
    cr.curve_to (r, h, 0, h, 0, h - r)
    cr.line_to (0, h)
    cr.close_path()
    cr.fill()
    # bottom-right
    cr.curve_to (w, h - r, w, h, w - r, h)
    cr.line_to (w, h)
    cr.close_path()
    cr.fill()

def gradient_line(rect, alpha):
    # Find rotated gradient start and end points.
    # Line end points follow the largest extent of the rotated rectangle.
    # The gradient reaches across the entire rectangle.
    x0, y0, w, h = rect.x, rect.y, rect.w, rect.h
    a = w / 2.0
    b = h / 2.0
    coords = [(-a, -b), (a, -b), (a, b), (-a, b)]
    vx = [c[0]*cos(alpha)-c[1]*sin(alpha) for c in coords]
    dx = max(vx) - min(vx)
    r = dx / 2.0
    return (r * cos(alpha) + x0 + a,
            r * sin(alpha) + y0 + b,
           -r * cos(alpha) + x0 + a,
           -r * sin(alpha) + y0 + b)

def drop_shadow(cr, pattern, bounds, blur_radius = 4.0, offset = (0, 0),
                                  alpha=0.06, steps=4):
    """
    Mostly works, but has issues with clipping artefacts for
    damage rects smaller than the full window rect.
    """
    origin = bounds.get_center()
    cr.set_source_rgba(0.0, 0.0, 0.0, alpha)
    for i in range(steps):

        x = (i if i else 0.5) / float(steps)
        k = sqrt(abs(log(1-x))) * 0.7 * blur_radius # gaussian
        #k = i / float(steps) * blur_radius         # linear

        x_scale = (bounds.w + k) / bounds.w
        y_scale = (bounds.h + k) / bounds.h
        cr.save()
        cr.translate(*origin)
        cr.scale(x_scale, y_scale)
        cr.translate(-origin[0] + offset[0], -origin[1] + offset[1])

        cr.mask(pattern)
        cr.restore()

@contextmanager
def timeit(s, out=sys.stdout):
    import time, gc

    if out:
        gc.collect()
        gc.collect()
        gc.collect()

        t = time.time()
        text = s if s else "timeit"
        out.write("%-15s " % text)
        out.flush()
        yield None
        out.write("%10.3fms\n" % ((time.time() - t)*1000))
    else:
        yield None

class Fade:
    """ Helper for opacity fading """
    @staticmethod
    def sin_fade(start_time, duration, start_value, target_value):
        elapsed = time.time() - start_time
        if duration:
            lin_progress = min(1.0, elapsed / duration)
        else:
            lin_progress = 1.0
        return(Fade.sin_int(lin_progress, start_value, target_value),
               lin_progress >= 1.0)

    @staticmethod
    def sin_int(lin_progress, start_value, target_value):
        sin_progress = (sin(lin_progress * pi - pi / 2.0) + 1.0) / 2.0
        return sin_progress * (target_value - start_value) + start_value


class TreeItem(object):
    """
    Abstract base class of tree nodes.
    Base class of nodes in  layout- and color scheme tree.
    """

    # id string of the item
    id = None

    # parent item in the tree
    parent = None

    # child items
    items = ()

    def get_id(self):
        return self.id

    def set_items(self, items):
        self.items = items
        for item in items:
            item.parent = self

    def append_item(self, item):
        if self.items:
            self.items.append(item)
        else:
            self.items = [item]

        item.parent = self

    def append_items(self, items):
        if self.items:
            self.items += items
        else:
            self.items = items

        for item in items:
            item.parent = self

    def get_parent(self):
        return self.parent

    def find_id(self, id):
        """ find the first item with matching id """
        for item in self.iter_items():
            if item.id == id:
                return item
        return None

    def find_ids(self, ids):
        """ find all items with matching id """
        for item in self.iter_items():
            if item.id in ids:
                yield item

    def find_classes(self, item_classes):
        """ find all items with matching id """
        for item in self.iter_items():
            if isinstance(item, item_classes):
                yield item

    def iter_items(self):
        """
        Iterates through all items of the tree.
        """
        yield self

        for item in self.items:
            for child in item.iter_items():
                yield child

    def iter_depth_first(self):
        """
        Iterates depth first through the tree.
        """
        for item in self.items:
            for child in item.iter_depth_first():
                yield child

        yield self

    def iter_to_root(self):
        item = self
        while item:
            yield item
            item = item.parent


class Version(object):
    """ Simple class to encapsulate a version number """
    major = 0
    minor = 0

    def __init__(self, major, minor = 0):
        self.major = major
        self.minor = minor

    def __str__(self):
        return self.to_string()

    @staticmethod
    def from_string(version):
        components = version.split(".")

        major = 0
        minor = 0
        try:
            if len(components) >= 1:
                major = int(components[0])
            if len(components) >= 2:
                minor = int(components[1])
        except ValueError:
            pass

        return Version(major, minor)

    def to_string(self):
        return "{major}.{minor}".format(major=self.major, minor=self.minor)

    def __eq__(self, other): return self._cmp(other) == 0
    def __ne__(self, other): return self._cmp(other) != 0
    def __lt__(self, other): return self._cmp(other) < 0
    def __le__(self, other): return self._cmp(other) <= 0
    def __gt__(self, other): return self._cmp(other) > 0
    def __ge__(self, other): return self._cmp(other) >= 0

    def _cmp(self, other):
        if self.major < other.major:
            return -1
        if self.major > other.major:
            return 1
        if self.minor < other.minor:
            return -1
        if self.minor > other.minor:
            return 1
        return 0


class Process:
    """ Process utilities """

    @staticmethod
    def get_cmdline(pid):
        """ Returns the command line for process id pid """
        cmdline = ""
        with open("/proc/%s/cmdline" % pid) as f:
            cmdline = f.read()
        return cmdline.split("\0")

    @staticmethod
    def get_process_name(pid):
        cmdline = Process.get_cmdline(pid)
        if cmdline:
            return os.path.basename(cmdline[0])
        return ""

    @staticmethod
    def get_launch_process_cmdline():
        """ Checks if this process was launched by <process_name> """
        ppid = os.getppid()
        if ppid:
            cmdline = Process.get_cmdline(ppid)
            return cmdline
        return []

    @staticmethod
    def was_launched_by(process_name):
        """ Checks if this process was launched by <process_name> """
        cmdline = " ".join(Process.get_launch_process_cmdline())
        return process_name in cmdline


def exists_in_path(basename):
    """
    Does a file with this basename exist anywhere in PATH's directories?
    """
    for path in os.environ["PATH"].split(os.pathsep):
        filename = os.path.join(path, basename)
        if os.path.isfile(filename):
            return True
    return False

def chmodtree(path, mode = 0o777, only_dirs = False):
    """
    Change permissions of all files of the given directory tree.
    Raises OSError.
    """
    os.chmod(path, mode)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chmod(os.path.join(root, d), mode)
        if not only_dirs:
            for f in files:
                os.chmod(os.path.join(root, f), mode)

def unicode_str(obj, encoding = "utf-8"):
    """
    Safe str() function that always returns an unicode string.
    Do nothing if the string was already unicode.
    """
    if sys.version_info.major >= 3:  # python 3?
        return str(obj)

    if type(obj) == unicode:         # unicode string?
        return obj

    if hasattr(obj, "__unicode__"):  # Exception object?
        return unicode(obj)

    return str(obj).decode("utf-8")  # strings, numbers, ...

def open_utf8(filename, mode = "r"):
    """
    Python 2 compatible replacement for builtin open().
    Python 3 added the encoding parameter.
    """
    if sys.version_info.major == 2:
        return open(filename, mode)
    else:
        return open(filename, mode=mode, encoding="UTF-8")

def permute_mask(mask):
    """
    Return all permutations of the bits in mask.

    Doctests:
    >>> permute_mask(1)
    [0, 1]
    >>> permute_mask(5)
    [0, 1, 4, 5]
    >>> permute_mask(14)
    [0, 2, 4, 6, 8, 10, 12, 14]
    """
    bit_masks = [bit_mask for bit_mask in (1<<bit for bit in range(8)) \
                 if mask & bit_mask]
    n = len(bit_masks)
    perms = []
    for i in range(2**n):
        m = 0
        for bit in range(n):
            if i & 1<<bit:
                m |= bit_masks[bit]
        perms.append(m)
    return perms


class Translation:
    """
    Translations occasionally contain errors in format fields that
    prevent onboard from starting up. This class aims to catch these
    errors gracefully, report and ignore bad translations and then
    just go on.

    Common errors have been:
        - bad field names, e.g. "{filename}" was translated "{path}"
        - bad anonymous fields, e.g. "{}" was translated "{ }"
    """
    @staticmethod
    def install(domain):
        """ setup gettext, install _() function for all modules """

        try:
            import builtins
        except ImportError:
            builtins = sys.modules["__builtin__"]  # python 2.x

        t = Translation()
        t.translation = gettext.translation(domain, fallback=True)

        builtins.__dict__['_'] = t.ugettext
        builtins.__dict__['_format'] = t.format

    def ugettext(self, msgid):
        if not msgid:
            return unicode_str("")
        if sys.version_info.major < 3:  # python 2?
            return self.translation.ugettext(msgid)
        return self.translation.gettext(msgid)

    def format(self, msgid, *args, **kwargs):
        """ Safe replacement for str.format() """
        msgstr = self.ugettext(msgid)
        try:
            result = msgstr.format(*args, **kwargs)
        except (KeyError, IndexError, UnicodeDecodeError) as e:
            result = msgid.format(*args, **kwargs)

            _logger.warning("_format: Skipping bad translation "
                            "msgid='{}' msgstr='{}' {}: {}" \
                            .format(msgid, msgstr,
                                    e.__class__.__name__,
                                    unicode_str(e)))
        return result


class EventSource(object):
    """ Simple event handling based on python callbacks """
    _event_queue = None  # for optional async delivery

    def __init__(self, event_names):
        self._callbacks = dict((e,[]) for e in event_names)

    def cleanup(self):
        self.flush_events()

    def connect(self, event_name, callback):
        callbacks = self._callbacks[event_name]
        if not callback in callbacks:
            callbacks.append(callback)

    def disconnect(self, event_name, callback):
        callbacks = self._callbacks[event_name]
        if callback in callbacks:
            callbacks.remove(callback)

    def has_listeners(self, event_names = None):
        """
        Are there callbacks registered for the given event_names or any event?
        """
        if event_names:
            return any(bool(self._callbacks[name]) for name in event_names)
        return any(bool(value) for value in self._callbacks.values())

    def emit(self, event_name, *args, **kwargs):
        """
        Send event, call all listener's callbacks.
        """
        #print("emit", event_name, list(args), kwargs)
        for callback in self._callbacks[event_name]:
            callback(*args, **kwargs)

    def emit_async(self, event_name, *args, **kwargs):
        """
        Queue up asynchronous event.
        """
        #print("emit_async", event_name, list(args), kwargs)
        event = (event_name, args, kwargs)
        if self._event_queue is None:
            from gi.repository import GLib
            self._event_queue = [event]
            GLib.idle_add(self.flush_events)
        else:
            self._event_queue.append(event)

    def flush_events(self):
        """
        Send pending asynchronous events.
        """
        if not self._event_queue is None:
            for event_name, args, kwargs in self._event_queue:
                self.emit(event_name, *args, **kwargs)
            self.clear_events()

    def clear_events(self):
        """
        Cancel pending asynchronous events.
        """
        self._event_queue = None


class XDGDirs:
    """
    Build paths compliant with XDG Base Directory Specification.
    http://standards.freedesktop.org/basedir-spec/basedir-spec-latest.html

    Doctests:

    >>> old_env = os.environ.copy()
    >>> os.environ["HOME"] = "/home/test_user"

    # XDG_CONFIG_HOME unavailable
    >>> os.environ["XDG_CONFIG_HOME"] = ""
    >>> XDGDirs.get_config_home("onboard/test.dat")
    '/home/test_user/.config/onboard/test.dat'

    # XDG_CONFIG_HOME available
    >>> os.environ["XDG_CONFIG_HOME"] = "/home/test_user/.config_home"
    >>> XDGDirs.get_config_home("onboard/test.dat")
    '/home/test_user/.config_home/onboard/test.dat'

    # XDG_DATA_HOME unavailable
    >>> os.environ["XDG_DATA_HOME"] = ""
    >>> XDGDirs.get_data_home("onboard/test.dat")
    '/home/test_user/.local/share/onboard/test.dat'

    # XDG_DATA_HOME available
    >>> os.environ["XDG_DATA_HOME"] = "/home/test_user/.data_home"
    >>> XDGDirs.get_data_home("onboard/test.dat")
    '/home/test_user/.data_home/onboard/test.dat'

    # XDG_CONFIG_DIRS unvailable
    >>> os.environ["XDG_CONFIG_HOME"] = ""
    >>> os.environ["XDG_CONFIG_DIRS"] = ""
    >>> XDGDirs.get_all_config_dirs("onboard/test.dat")
    ['/home/test_user/.config/onboard/test.dat', '/etc/xdg/onboard/test.dat']

    # XDG_CONFIG_DIRS available
    >>> os.environ["XDG_CONFIG_HOME"] = ""
    >>> os.environ["XDG_CONFIG_DIRS"] = "/etc/xdg/xdg-ubuntu:/etc/xdg"
    >>> XDGDirs.get_all_config_dirs("onboard/test.dat")
    ['/home/test_user/.config/onboard/test.dat', \
'/etc/xdg/xdg-ubuntu/onboard/test.dat', \
'/etc/xdg/onboard/test.dat']

    # XDG_DATA_DIRS unvailable
    >>> os.environ["XDG_DATA_HOME"] = ""
    >>> os.environ["XDG_DATA_DIRS"] = ""
    >>> XDGDirs.get_all_data_dirs("onboard/test.dat")
    ['/home/test_user/.local/share/onboard/test.dat', \
'/usr/local/share/onboard/test.dat', \
'/usr/share/onboard/test.dat']

    # XDG_DATA_DIRS available
    >>> os.environ["XDG_DATA_HOME"] = ""
    >>> os.environ["XDG_DATA_DIRS"] = "/usr/share/gnome:/usr/local/share/:/usr/local/share/:/usr/share/"
    >>> XDGDirs.get_all_data_dirs("onboard/test.dat")
    ['/home/test_user/.local/share/onboard/test.dat', \
'/usr/share/gnome/onboard/test.dat', \
'/usr/local/share/onboard/test.dat', \
'/usr/share/onboard/test.dat']

    >>> os.environ = old_env
    """

    @staticmethod
    def get_config_home(file = None):
        """
        User specific config directory.
        """
        path = os.environ.get("XDG_CONFIG_HOME")
        if path and not os.path.isabs(path):
            _logger.warning("XDG_CONFIG_HOME doesn't contain an absolute path,"
                            "ignoring.")
            path = None
        if not path:
            path = os.path.join(os.path.expanduser("~"), ".config")

        if file:
            path = os.path.join(path, file)

        return path

    @staticmethod
    def get_config_dirs():
        """
        Config directories ordered by preference.
        """
        paths = []

        value = os.environ.get("XDG_CONFIG_DIRS")
        if not value:
            value = "/etc/xdg"

        paths = value.split(":")
        paths = [p for p in paths if os.path.isabs(p)]

        return paths

    @staticmethod
    def get_all_config_dirs(file = None):
        paths = [XDGDirs.get_config_home()] + XDGDirs.get_config_dirs()

        if file:
            paths = [os.path.join(p, file) for p in paths]

        return paths

    @staticmethod
    def find_config_files(file):
        """ Find file in all config directories, highest priority first. """
        paths = XDGDirs.get_all_config_dirs(file)
        return [p for p in paths if os.path.isfile(path) and \
                                    os.access(filename, os.R_OK)]

    @staticmethod
    def find_config_file(file):
        """ Find file of highest priority """
        paths = XDGDirs.find_config_files(file)
        if paths:
            return paths[0]
        return None

    @staticmethod
    def get_data_home(file = None):
        """
        User specific data directory.
        """
        path = os.environ.get("XDG_DATA_HOME")
        if path and not os.path.isabs(path):
            _logger.warning("XDG_DATA_HOME doesn't contain an absolute path,"
                            "ignoring.")
            path = None
        if not path:
            path = os.path.join(os.path.expanduser("~"), ".local", "share")

        if file:
            path = os.path.join(path, file)

        return path

    @staticmethod
    def get_data_dirs():
        """
        Data directories ordered by preference.
        """
        paths = []

        value = os.environ.get("XDG_DATA_DIRS")
        if not value:
            value = "/usr/local/share/:/usr/share/"

        paths = value.split(":")
        paths = [p for p in paths if os.path.isabs(p)]

        return paths

    @staticmethod
    def get_all_data_dirs(file = None):
        paths = [XDGDirs.get_data_home()] + XDGDirs.get_data_dirs()

        if file:
            paths = [os.path.join(p, file) for p in paths]

        return paths

    @staticmethod
    def find_data_files(file):
        """ Find file in all data directories, highest priority first. """
        paths = XDGDirs.get_all_data_dirs(file)
        return [p for p in paths if os.path.isfile(path) and \
                                    os.access(filename, os.R_OK)]

    @staticmethod
    def find_data_file(file):
        """ Find file of highest priority """
        paths = XDGDirs.find_data_files(file)
        if paths:
            return paths[0]
        return None

    def assure_user_dir_exists(path):
        """
        If necessary create user XDG directory.
        Raises OSError.
        """
        exists = os.path.exists(path)
        if not exists:
            try:
                os.makedirs(path, mode = 0o700)
                exists = True
            except OSError as ex:
                _logger.error(_format("failed to create directory '{}': {}",
                                       path, unicode_str(ex)))
                raise ex
        return exists


_tag_pattern = re.compile(
    r"""(?:
            <[\w\-_]+                         # tag
            (?:\s+[\w\-_]+=["'][^"']*["'])*  # attributes
            /?>
        ) |
        (?:
            </?[\w\-_]+>
        )
    """, re.UNICODE|re.DOTALL|re.VERBOSE)

def _iter_markup(markup):
    """
    Iterate over tag and non-tag sections of a markup string.

    Doctests:
    # Never yield for empty string
    >>> list(_iter_markup(""))
    []

    # must return tag- as well as non-tag sections
    >>> list(_iter_markup("<tt>test</tt>test2"))
    [('<tt>', True), ('test', False), ('</tt>', True), ('test2', False)]

    # should recognize tags with attributes
    >>> list(_iter_markup('<tag attr="value">'))
    [('<tag attr="value">', True)]
    >>> list(_iter_markup('<tag attr="v alue" attr2="234">'))
    [('<tag attr="v alue" attr2="234">', True)]

    # should recognize tags with end shortcut
    >>> list(_iter_markup('<tag/> <tag2 attr="value"/>'))
    [('<tag/>', True), (' ', False), ('<tag2 attr="value"/>', True)]

    # must not modify input, i.e. concatenated result must equal input text
    >>> markup = "asd <tt>t est\\n ds</tt> te st2 "
    >>> "".join([text for text, tag in _iter_markup(markup)]) == markup
    True
    """
    pos = 0
    matches = _tag_pattern.finditer(markup)
    for m in matches:
        text = markup[pos:m.start()]
        if text:
            yield text, False
        yield m.group(), True
        pos = m.end()

    text = markup[pos:]
    if text:
        yield text, False


def escape_markup(markup, preserve_tags = False):
    """
    Escape strings of uncertain content for use in Gtk markup.
    If requested, markup tags are skipped and won't be escaped.

    Doctests:
    >>> escape_markup("&<>")
    '&amp;&lt;&gt;'

    # tags must be escaped when preserve_tags is False
    >>> escape_markup("<big>&<></big>")
    '&lt;big&gt;&amp;&lt;&gt;&lt;/big&gt;'

    # tags must not be escaped when preserve_tags is True
    >>> escape_markup('<big>&</big>><tag attr="value"><</tag>1<3', True)
    '<big>&amp;</big>&gt;<tag attr="value">&lt;</tag>1&lt;3'

    # whitespace must be preserved
    >>> escape_markup("test <big> test2& </big> test3", True)
    'test <big> test2&amp; </big> test3'
    """
    from gi.repository import GLib
    result = ""
    for text, is_tag in _iter_markup(markup):
        if is_tag and preserve_tags:
            result += text
        else:
            try:
                result += GLib.markup_escape_text(text)
            except Exception as ex: # private exception gi._glib.GError
                _logger.error("markup_escape_text failed for "
                                "'{}': {}" \
                            .format(text, unicode_str(ex)))
    return result


class TermColors(object):
    """ Singleton class providing ANSI terminal color codes """

    # sequence ids
    BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, \
    BOLD, RESET = range(10)

    # sequence cache
    sequences = {}

    def __new__(cls, *args, **kwargs):
        """ Singleton magic.  """
        if not hasattr(cls, "self"):
            cls.self = object.__new__(cls, *args, **kwargs)
            cls.self.construct()
        return cls.self

    def __init__(self):
        """ Called multiple times, do not use.  """
        pass

    def construct(self):
        """ Singleton constructor, runs only once.  """

    def get(self, seq_id):
        """
        Return ANSI character sequence for the given sequence id,
        e.g. color index.
        """
        seq = self.sequences.get(seq_id)
        if seq is None:
            seq = ""
            if not seq_id is None:
                if seq_id >= self.BLACK and seq_id <= self.WHITE:
                    seq = self._tput("setaf " + str(seq_id))
                elif seq_id == self.BOLD:
                    seq = self._tput("bold")
                elif seq_id == self.RESET:
                    seq = self._tput("sgr0")
            self.sequences[seq_id] = seq
        return seq

    @staticmethod
    def _tput(params):
        try:
            s = subprocess.check_output(("tput " + params).split())
            return s.decode("ASCII")
        except subprocess.CalledProcessError:
            return ""



