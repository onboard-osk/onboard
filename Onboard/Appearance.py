# -*- coding: utf-8 -*-

# Copyright © 2011-2014, 2016 marmuta <marmvta@gmail.com>
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
Module for theme related classes.
"""

from __future__ import division, print_function, unicode_literals


### Logging ###
import logging
_logger = logging.getLogger("Appearance")
###############

import xml
from xml.dom import minidom
import sys
import os
import re
import colorsys
from math import log

from Onboard             import Exceptions
from Onboard.utils       import hexstring_to_float, brighten, toprettyxml, \
                                TreeItem, Version, unicode_str, open_utf8, \
                                XDGDirs

import Onboard.utils as utils

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

class Theme:
    """
    Theme controls the visual appearance of Onboards keyboard window.
    """
    # onboard 0.95
    THEME_FORMAT_INITIAL = Version(1, 0)

    # onboard 0.97, added key_size, switch most int values to float,
    # changed range of key_gradient_direction
    THEME_FORMAT_1_1 = Version(1, 1)

    # onboard 0.98, added shadow keys
    THEME_FORMAT_1_2 = Version(1, 2)

    # onboard 0.99, added key_stroke_width
    THEME_FORMAT_1_3 = Version(1, 3)

    THEME_FORMAT = THEME_FORMAT_1_3

    # core theme members
    # name, type, default
    attributes = [
            ["color_scheme_basename", "s", ""],
            ["background_gradient", "d", 0.0],
            ["key_style", "s", "flat"],
            ["roundrect_radius", "d", 0.0],
            ["key_size", "d", 100.0],
            ["key_stroke_width", "d", 100.0],
            ["key_fill_gradient", "d", 0.0],
            ["key_stroke_gradient", "d", 0.0],
            ["key_gradient_direction", "d", 0.0],
            ["key_label_font", "s", ""],
            ["key_label_overrides", "a{s[ss]}", {}],   # dict {name:(key:group)}
            ["key_shadow_strength", "d", 0.0],
            ["key_shadow_size", "d", 0.0],
            ]

    def __init__(self):
        self._modified = False

        self._filename = ""
        self._is_system = False       # True if this a system theme
        self._system_exists = False   # True if there exists a system
                                     #  theme with the same basename
        self._name = ""

        # create attributes
        for name, _type, default in self.attributes:
            setattr(self, name, default)

    @property
    def basename(self):
        """ Returns the file base name of the theme. """
        return os.path.splitext(os.path.basename(self._filename))[0]

    @property
    def filename(self):
        """ Returns the filename of the theme. """
        return self._filename

    def __eq__(self, other):
        if not other:
            return False
        for name, _type, _default in self.attributes:
            if getattr(self, name) != getattr(other, name):
                return False
        return True

    def __str__(self):
        return "name=%s, colors=%s, font=%s, radius=%d" % (self._name,
                                                self.color_scheme_basename,
                                                self.key_label_font,
                                                self.roundrect_radius)

    def apply(self, save=True):
        """ Applies the theme to config properties/gsettings. """
        filename = self.get_color_scheme_filename()
        if not filename:
            _logger.error(_format("Color scheme for theme '{filename}' not found", \
                                  filename=self._filename))
            return False

        config.theme_settings.set_color_scheme_filename(filename, save)
        for name, _type, _default in self.attributes:
            if name != "color_scheme_basename":
                getattr(config.theme_settings, "set_" + name) \
                                 (getattr(self, name), save)

        return True

    def get_color_scheme_filename(self):
        """ Returns the filename of the themes color scheme."""
        filename = os.path.join(Theme.user_path(),
                                self.color_scheme_basename) + \
                                "." + ColorScheme.extension()
        if not os.path.isfile(filename):
            filename = os.path.join(Theme.system_path(),
                                    self.color_scheme_basename) + \
                                    "." + ColorScheme.extension()
        if not os.path.isfile(filename):
            return None
        return filename

    def set_color_scheme_filename(self, filename):
        """ Set the filename of the color_scheme. """
        self.color_scheme_basename = \
                             os.path.splitext(os.path.basename(filename ))[0]

    def get_superkey_label(self):
        """ Returns the (potentially overridden) label of the super keys. """
        override = self.key_label_overrides.get("LWIN")
        if override:
            return override[0] # assumes RWIN=LWIN
        return None

    def get_superkey_size_group(self):
        """
        Returns the (potentially overridden) size group of the super keys.
        """
        override = self.key_label_overrides.get("LWIN")
        if override:
            return override[1] # assumes RWIN=LWIN
        return None

    def set_superkey_label(self, label, size_group):
        """ Sets or clears the override for left and right super key labels. """
        tuples = self.key_label_overrides
        if label is None:
            if "LWIN" in tuples:
                del tuples["LWIN"]
            if "RWIN" in tuples:
                del tuples["RWIN"]
        else:
            tuples["LWIN"] = (label, size_group)
            tuples["RWIN"] = (label, size_group)
        self.key_label_overrides = tuples

    @staticmethod
    def system_to_user_filename(filename):
        """ Returns the user filename for the given system filename. """
        basename = os.path.splitext(os.path.basename(filename ))[0]
        return os.path.join(Theme.user_path(),
                                basename) + "." + Theme.extension()

    @staticmethod
    def build_user_filename(basename):
        """
        Returns a fully qualified filename pointing into the user directory
        """
        return os.path.join(Theme.user_path(),
                                basename) + "." + Theme.extension()

    @staticmethod
    def build_system_filename(basename):
        """
        Returns a fully qualified filename pointing into the system directory
        """
        return os.path.join(Theme.system_path(),
                                basename) + "." + Theme.extension()

    @staticmethod
    def user_path():
        """ Returns the path of the user directory for themes. """
        return os.path.join(config.user_dir, "themes")

    @staticmethod
    def system_path():
        """ Returns the path of the system directory for themes. """
        return os.path.join(config.install_dir, "themes")

    @staticmethod
    def extension():
        """ Returns the file extension of theme files """
        return "theme"

    @staticmethod
    def load_merged_themes():
        """
        Merge system and user themes.
        User themes take precedence and hide system themes.
        """
        system_themes = Theme.load_themes(True)
        user_themes = Theme.load_themes(False)
        themes = dict((t.basename, (t, None)) for t in system_themes)
        for theme in user_themes:
            # system theme hidden behind user theme?
            if theme.basename in themes:
                # keep the system theme behind the user theme
                themes[theme.basename] = (theme, themes[theme.basename][0])
            else:
                themes[theme.basename] = (theme, None)
        return themes

    @staticmethod
    def load_themes(is_system=False):
        """ Load all themes from either the user or the system directory. """
        themes = []

        if is_system:
            path = Theme.system_path()
        else:
            path = Theme.user_path()

        filenames = Theme.find_themes(path)
        for filename in filenames:
            theme = Theme.load(filename, is_system)
            if theme:
                themes.append(theme)
        return themes

    @staticmethod
    def find_themes(path):
        """
        Returns the full path names of all themes found in the given path.
        """
        themes = []

        try:
            files = os.listdir(path)
        except OSError:
            files = []

        for filename in files:
            if filename.endswith(Theme.extension()):
                themes.append(os.path.join(path, filename))
        return themes

    @staticmethod
    def load(filename, is_system=False):
        """ Load a theme and return a new theme object. """

        result = None

        _file = open_utf8(filename)
        try:
            domdoc = minidom.parse(_file).documentElement
            try:
                theme = Theme()

                node = domdoc.attributes.get("format")
                format = Version.from_string(node.value) \
                         if node else Theme.THEME_FORMAT_INITIAL

                theme.name = domdoc.attributes["name"].value

                # "color_scheme" is the base file name of the color scheme
                text = utils.xml_get_text(domdoc, "color_scheme")
                if not text is None:
                    theme.color_scheme_basename = text

                # get key label overrides
                nodes = domdoc.getElementsByTagName("key_label_overrides")
                if nodes:
                    overrides = nodes[0]
                    tuples = {}
                    for override in overrides.getElementsByTagName("key"):
                        key_id = override.attributes["id"].value
                        node = override.attributes.get("label")
                        label = node.value if node else ""
                        node = override.attributes.get("group")
                        group = node.value if node else ""
                        tuples[key_id] = (label, group)
                    theme.key_label_overrides = tuples

                # read all other members
                for name, _type, _default in Theme.attributes:
                    if not name in ["color_scheme_basename",
                                    "key_label_overrides"]:
                        value = utils.xml_get_text(domdoc, name)
                        if not value is None:

                            if _type == "i":
                                value = int(value)
                            if _type == "d":
                                value = float(value)
                            if _type == "ad":
                                value = [float(s) for s in value.split(",")]

                            # upgrade to current file format
                            if format < Theme.THEME_FORMAT_1_1:
                                # direction was    0..360, ccw
                                #        is now -180..180, cw
                                if name == "key_gradient_direction":
                                    value = -(value % 360)
                                    if value <= -180:
                                        value += 360

                            setattr(theme, name, value)

                theme._filename = filename
                theme.is_system = is_system
                theme.system_exists = is_system
                result = theme
            finally:
                domdoc.unlink()

        except (Exceptions.ThemeFileError,
                xml.parsers.expat.ExpatError) as ex:
            _logger.error(_format("Error loading theme '{filename}'. "
                                  "{exception}: {cause}",
                                  filename = filename,
                                  exception = type(ex).__name__,
                                  cause = unicode_str(ex)))
            result = None
        finally:
            _file.close()

        return result

    def save_as(self, basename, name):
        """ Save this theme under a new name. """
        self._filename = self.build_user_filename(basename)
        self._name = name
        self.save()

    def save(self):
        """ Save this theme. """

        domdoc = minidom.Document()
        try:
            theme_element = domdoc.createElement("theme")
            theme_element.setAttribute("name", self._name)
            theme_element.setAttribute("format", str(self.THEME_FORMAT))
            domdoc.appendChild(theme_element)

            for name, _type, _default in self.attributes:
                if name == "color_scheme_basename":
                    element = domdoc.createElement("color_scheme")
                    text = domdoc.createTextNode(self.color_scheme_basename)
                    element.appendChild(text)
                    theme_element.appendChild(element)
                elif name == "key_label_overrides":
                    overrides_element = \
                            domdoc.createElement("key_label_overrides")
                    theme_element.appendChild(overrides_element)
                    tuples = self.key_label_overrides
                    for key_id, values in list(tuples.items()):
                        element = domdoc.createElement("key")
                        element.setAttribute("id", key_id)
                        element.setAttribute("label", values[0])
                        element.setAttribute("group", values[1])
                        overrides_element.appendChild(element)
                else:
                    value = getattr(self, name)
                    if _type == "s":
                        pass
                    elif _type == "i":
                        value = str(value)
                    elif _type == "d":
                        value = str(round(float(value), 2))
                    elif _type == "ad":
                        value = ", ".join(str(d) for d in value)
                    else:
                        assert(False) # attribute of unknown type

                    element = domdoc.createElement(name)
                    text = domdoc.createTextNode(value)
                    element.appendChild(text)
                    theme_element.appendChild(element)

            pretty_xml = toprettyxml(domdoc)

            XDGDirs.assure_user_dir_exists(self.user_path())

            with open_utf8(self._filename, "w") as _file:
                if sys.version_info.major >= 3:
                    _file.write(pretty_xml)
                else:
                    _file.write(pretty_xml.encode("UTF-8"))

        except Exception as xxx_todo_changeme2:
            (ex) = xxx_todo_changeme2
            raise Exceptions.ThemeFileError(_("Error saving ")
                + self._filename, chained_exception = ex)
        finally:
            domdoc.unlink()


class ColorScheme(object):
    """
    ColorScheme defines the colors of onboards keyboard.
    Each key or groups of keys may have their own individual colors.
    Any color definition may be omitted. Undefined colors fall back
    to color scheme defaults first, then to hard coded default colors.
    """

    # onboard 0.95
    COLOR_SCHEME_FORMAT_LEGACY = Version(1, 0)

    # onboard 0.97, tree format, rule-based color matching
    COLOR_SCHEME_FORMAT_TREE   = Version(2, 0)

    # onboard 0.99, added window colors
    COLOR_SCHEME_WINDOW_COLORS   = Version(2, 1)

    COLOR_SCHEME_FORMAT = COLOR_SCHEME_WINDOW_COLORS

    def __init__(self):
        self._filename = ""
        self._is_system = False
        self._root = None       # tree root

    @property
    def basename(self):
        """ Returns the file base name of the color scheme. """
        return os.path.splitext(os.path.basename(self._filename))[0]

    @property
    def filename(self):
        """ Returns the filename of the color scheme. """
        return self._filename

    def is_key_in_scheme(self, key):
        for id in [key.theme_id, key.id]:
            if self._root.find_key_id(id):
                return True
        return False

    def get_key_rgba(self, key, element, state=None):
        """
        Get the color for the given key element and optionally key state.
        If <state> is None the key state is retrieved from <key>.
        """

        if state is None:
            state = key.get_state()
            if "insensitive" in state:
                state["insensitive"] = not key.sensitive
                del state["sensitive"]

        rgb = None
        opacity = None
        root_rgb = None
        root_opacity = None
        key_group = None

        # First try to find the theme_id then fall back to the generic id
        ids = [key.theme_id, key.id]

        # Let numbered keys fall back to their base id, e.g. instead
        # of prediction0, prediction1,... have only "prediction" in
        # the color scheme.
        if key.is_key():  # can be a DrawingItem too
            if key.id == "correctionsbg":
                ids.append("wordlist")
            elif key.id == "predictionsbg":
                ids.append("wordlist")
            elif key.is_prediction_key():
                ids.append("prediction")
            elif key.is_correction_key():
                ids.append("correction")
            elif key.is_layer_button():
                ids.append(key.get_similar_theme_id("layer"))
                ids.append("layer")

        # look for a matching key_group and color in the color scheme
        for id in ids:
            key_group = self._root.find_key_id(id)
            if key_group:
                rgb, opacity = key_group.find_element_color(element, state)
                break

        # Get root colors as fallback for the case when key id
        # wasn't mentioned anywhere in the color scheme.
        root_key_group = self._root.get_default_key_group()
        if root_key_group:
            root_rgb, root_opacity = \
                    root_key_group.find_element_color(element, state)

        # Special case for layer buttons:
        # don't take fill color from the root group,
        # we want the layer fill color instead (via get_key_default_rgba()).
        if key.is_key() and \
           (element == "fill" and key.is_layer_button() or \
            element == "label" and key.is_correction_key()):
            # Don't pick layer fill opacity when there is
            # an rgb color defined in the color scheme.
            if not rgb is None and \
               opacity is None:
                opacity = root_opacity
                if opacity is None:
                    opacity = 1.0
        elif key_group is None:
            # All other colors fall back to the root group's colors
            rgb = root_rgb
            opacity = root_opacity

        if rgb is None:
            rgb = self.get_key_default_rgba(key, element, state)[:3]

        if opacity is None:
            opacity = self.get_key_default_rgba(key, element, state)[3]

        rgba = rgb + [opacity]
        return rgba

    def get_default_key_rgba(self, element, state):
        """Return a default key color for a state without requiring a key."""
        key_group = self._root.get_default_key_group()
        rgb = opacity = None
        if key_group:
            rgb, opacity = key_group.find_element_color(element, state)

        defaults = {
            "fill":   [0.9, 0.85, 0.7, 1.0],
            "stroke": [0.0, 0.0, 0.0, 1.0],
            "label":  [0.0, 0.0, 0.0, 1.0],
        }
        fallback = defaults[element]
        return (rgb if rgb is not None else fallback[:3]) + \
               [opacity if opacity is not None else fallback[3]]

    def set_default_key_rgba(self, element, state, rgba):
        """Set a default key color, placing state-specific rules first."""
        key_group = self._root.get_default_key_group()
        if key_group is None:
            key_group = KeyGroup()
            key_group.set_items([])
            self._root.append_item(key_group)

        state = dict(state)
        if state:
            # Label and border colors otherwise treat unspecified states as
            # wildcards. Make the editor's state rules mutually exclusive.
            for name in ["pressed", "active", "locked", "scanned", "hover"]:
                state.setdefault(name, False)
        color = next((item for item in key_group.items
                      if item.is_color() and item.element == element and
                      item.state == state), None)
        if color is None:
            color = KeyColor()
            color.element = element
            color.state = state
            if state:
                key_group.items.insert(0, color)
                color.parent = key_group
            else:
                key_group.append_item(color)
        elif state:
            key_group.items.remove(color)
            key_group.items.insert(0, color)

        color.rgb = list(rgba[:3])
        color.opacity = rgba[3]

    def set_layer_fill_rgba(self, layer_index, rgba):
        """Set the background color for one keyboard layer."""
        layers = self._root.get_layers()
        while len(layers) <= layer_index:
            layer = Layer()
            layer.set_items([])
            self._root.append_item(layer)
            layers.append(layer)

        layer = layers[layer_index]
        color = next((item for item in layer.items
                      if item.is_color() and item.element == "background"),
                     None)
        if color is None:
            color = KeyColor()
            color.element = "background"
            color.state = {}
            layer.append_item(color)

        color.rgb = list(rgba[:3])
        color.opacity = rgba[3]

    def get_key_default_rgba(self, key, element, state):
        colors = {
                    "fill":                     [0.9,  0.85, 0.7, 1.0],
                    "prelight":                 [0.0,  0.0,  0.0, 1.0],
                    "pressed":                  [0.6,  0.6,  0.6, 1.0],
                    "active":                   [0.5,  0.5,  0.5, 1.0],
                    "locked":                   [1.0,  0.0,  0.0, 1.0],
                    "scanned":                  [0.45, 0.45, 0.7, 1.0],
                    "stroke":                   [0.0,  0.0,  0.0, 1.0],
                    "label":                    [0.0,  0.0,  0.0, 1.0],
                    "secondary-label":          [0.5,  0.5,  0.5, 1.0],
                    "dwell-progress":           [0.82, 0.19, 0.25, 1.0],
                    "correction-label":         [1.0,  0.5,  0.5, 1.0],
                    "hover":                     [0.9,  0.85, 0.7, 1.0],
                    }

        rgba = [0.0, 0.0, 0.0, 1.0]

        if element == "fill":
            if key.is_key() and \
               key.is_layer_button() and \
               not any(state.values()):
                # Special case for base fill color of layer buttons:
                # default color is layer fill color (as in onboard <=0.95).
                layer_index = key.get_layer_index()
                rgba = self.get_layer_fill_rgba(layer_index)

            elif state.get("pressed"):
                new_state = dict(list(state.items()))
                new_state["pressed"] = False
                rgba = self.get_key_rgba(key, element, new_state)

                # Make the default pressed color a slightly darker
                # or brighter variation of the unpressed color.
                h, l, s = colorsys.rgb_to_hls(*rgba[:3])

                # boost lightness changes for very dark and very bright colors
                # Ad-hoc formula, purly for aesthetics
                amount = -(log((l+.001)*(1-(l-.001))))*0.05 + 0.08

                if l < .5:  # dark color?
                    rgba = brighten(+amount, *rgba) # brigther
                else:
                    rgba = brighten(-amount, *rgba) # darker

            elif state.get("scanned"):
                rgba = colors["scanned"]
                # Make scanned active modifier keys stick out by blending
                # scanned color with non-scanned color.
                if state.get("active"): # includes locked
                    # inactive scanned color
                    new_state = dict(list(state.items()))
                    new_state["active"] = False
                    new_state["locked"] = False
                    scanned = self.get_key_rgba(key, element, new_state)

                    # unscanned fill color
                    new_state = dict(list(state.items()))
                    new_state["scanned"] = False
                    fill = self.get_key_rgba(key, element, new_state)

                    # blend inactive scanned color with unscanned fill color
                    for i in range(4):
                        rgba[i] = (scanned[i] + fill[i]) / 2.0

            elif state.get("prelight"):
                rgba = colors["prelight"]
            elif state.get("locked"):
                rgba = colors["locked"]
            elif state.get("active"):
                rgba = colors["active"]
            elif state.get("hover"):
                rgba = colors["hover"]
            else:
                rgba = colors["fill"]

        elif element == "stroke":
            rgba == colors["stroke"]
            if state.get("hover"):
                rgba = colors["hover"]
        elif element == "label":

            if key.is_correction_key():
                rgba = colors["correction-label"]
            else:
                rgba = colors["label"]

            if state.get("hover"):
                rgba = colors["hover"]
                
            # dim label color for insensitive keys
            if state.get("insensitive"):
                rgba = self._get_insensitive_color(key, state, element)

        elif element == "secondary-label":

            rgba = colors["secondary-label"]

            # dim label color for insensitive keys
            if state.get("insensitive"):
                rgba = self._get_insensitive_color(key, state, element)

        elif element == "dwell-progress":
            rgba = colors["dwell-progress"]

        else:
            assert(False)   # unknown element

        return rgba

    def _get_insensitive_color(self, key, state, element):
        new_state = state.copy()
        new_state["insensitive"] = False
        fill = self.get_key_rgba(key, "fill", new_state)
        rgba = self.get_key_rgba(key, element, new_state)

        h, lf, s = colorsys.rgb_to_hls(*fill[:3])
        h, ll, s = colorsys.rgb_to_hls(*rgba[:3])

        # Leave only one third of the lightness difference
        # between label and fill color.
        amount = (ll - lf) * 2.0 / 3.0
        return brighten(-amount, *rgba)

    def get_window_rgba(self, window_type, element):
        """
        Returns window colors.
        window_type may be "keyboard" or "key-popup".
        element may be "border"
        """
        rgb = None
        opacity = None
        windows = self._root.get_windows()

        window = None
        for item in windows:
            if item.type == window_type:
                window = item
                break

        if window:
            for item in window.items:
                if item.is_color() and \
                   item.element == element:
                    rgb = item.rgb
                    opacity = item.opacity
                    break

        if rgb is None:
            rgb = [1.0, 1.0, 1.0]
        if opacity is None:
            opacity = 1.0
        rgba = rgb + [opacity]

        return rgba

    def get_layer_fill_rgba(self, layer_index):
        """
        Returns the background fill color of the layer with the given index.
        """

        rgb = None
        opacity = None
        layers = self._root.get_layers()

        # If there is no layer definition for this index,
        # repeat the last defined layer color.
        layer_index = min(layer_index, len(layers) - 1)

        if layer_index >= 0 and layer_index < len(layers):
            for item in layers[layer_index].items:
                if item.is_color() and \
                   item.element == "background":
                    rgb = item.rgb
                    opacity = item.opacity
                    break

        if rgb is None:
            rgb = [0.5, 0.5, 0.5]
        if opacity is None:
            opacity = 1.0
        rgba = rgb + [opacity]

        return rgba

    def get_icon_rgba(self, element):
        """
        Returns the color for the given element of the icon.
        """
        rgb = None
        opacity = None
        icons = self._root.get_icons()
        for icon in icons:
            for item in icon.items:
                if item.is_color() and \
                   item.element == element:
                    rgb = item.rgb
                    opacity = item.opacity
                    break

        # default icon background is layer0 background
        if element == "background":
            # hard-coded default is the most common color
            rgba_default = [0.88, 0.88, 0.88, 1.0]
        else:
            assert(False)

        if rgb is None:
            rgb = rgba_default[:3]
        if opacity is None:
            opacity = rgba_default[3]

        if rgb is None:
            rgb = [0.5, 0.5, 0.5]
        if opacity is None:
            opacity = 1.0

        rgba = rgb + [opacity]

        return rgba

    @staticmethod
    def user_path():
        """ Returns the path of the user directory for color schemes. """
        return os.path.join(config.user_dir, "themes")

    @staticmethod
    def system_path():
        """ Returns the path of the system directory for color schemes. """
        return os.path.join(config.install_dir, "themes")

    @staticmethod
    def extension():
        """ Returns the file extension of color scheme files """
        return "colors"

    @staticmethod
    def build_user_filename(basename):
        """Return the user color-scheme filename for a basename."""
        return os.path.join(ColorScheme.user_path(), basename) + \
               "." + ColorScheme.extension()

    def save_as(self, basename, name):
        """Save this color scheme as a user-owned scheme."""
        self._filename = self.build_user_filename(basename)
        self._is_system = False
        self.is_system = False
        self.name = name
        self.save()

    def save(self):
        """Save the color-scheme tree in the current XML format."""
        domdoc = minidom.Document()
        try:
            root = domdoc.createElement("color_scheme")
            root.setAttribute("name", self.name)
            root.setAttribute("format", str(self.COLOR_SCHEME_FORMAT))
            domdoc.appendChild(root)
            for item in self._root.items:
                self._append_dom_item(domdoc, root, item)

            XDGDirs.assure_user_dir_exists(self.user_path())
            with open_utf8(self._filename, "w") as _file:
                pretty_xml = toprettyxml(domdoc)
                if sys.version_info.major >= 3:
                    _file.write(pretty_xml)
                else:
                    _file.write(pretty_xml.encode("UTF-8"))
        except Exception as ex:
            raise Exceptions.ColorSchemeFileError(
                _("Error saving ") + self._filename, chained_exception=ex)
        finally:
            domdoc.unlink()

    @staticmethod
    def _append_dom_item(domdoc, parent, item):
        if item.is_window():
            node = domdoc.createElement("window")
            node.setAttribute("type", item.type)
        elif item.is_layer():
            node = domdoc.createElement("layer")
        elif item.is_icon():
            node = domdoc.createElement("icon")
        elif item.is_key_group():
            node = domdoc.createElement("key_group")
            if item.key_ids:
                node.appendChild(domdoc.createTextNode(
                    ", ".join(item.key_ids)))
        elif item.is_color():
            node = domdoc.createElement("color")
            node.setAttribute("element", item.element)
            if item.rgb is not None:
                rgb = [max(0, min(255, int(round(value * 255))))
                       for value in item.rgb]
                node.setAttribute("rgb", "#{:02x}{:02x}{:02x}".format(*rgb))
            if item.opacity is not None:
                node.setAttribute("opacity", str(item.opacity))
            for name, value in item.state.items():
                node.setAttribute(name, str(value).lower())
        else:
            return

        if item.id:
            node.setAttribute("id", item.id)
        parent.appendChild(node)
        for child in item.items:
            ColorScheme._append_dom_item(domdoc, node, child)

    @staticmethod
    def get_merged_color_schemes():
        """
        Merge system and user color schemes.
        User color schemes take precedence and hide system color schemes.
        """
        system_color_schemes = ColorScheme.load_color_schemes(True)
        user_color_schemes = ColorScheme.load_color_schemes(False)
        color_schemes = dict((t.basename, t) for t in system_color_schemes)
        for scheme in user_color_schemes:
            color_schemes[scheme.basename] = scheme
        return color_schemes

    @staticmethod
    def load_color_schemes(is_system=False):
        """
        Load all color schemes from either the user or the system directory.
        """
        color_schemes = []

        if is_system:
            path = ColorScheme.system_path()
        else:
            path = ColorScheme.user_path()

        filenames = ColorScheme.find_color_schemes(path)
        for filename in filenames:
            color_scheme = ColorScheme.load(filename, is_system)
            if color_scheme:
                color_schemes.append(color_scheme)
        return color_schemes

    @staticmethod
    def find_color_schemes(path):
        """
        Returns the full path names of all color schemes found in the given path.
        """
        color_schemes = []

        try:
            files = os.listdir(path)
        except OSError:
            files = []

        for filename in files:
            if filename.endswith(ColorScheme.extension()):
                color_schemes.append(os.path.join(path, filename))
        return color_schemes

    @staticmethod
    def load(filename, is_system=False):
        """ Load a color scheme and return it as a new instance. """

        color_scheme = None

        f = open_utf8(filename)
        try:
            dom = minidom.parse(f).documentElement
            name = dom.attributes["name"].value

            # check layout format
            format = ColorScheme.COLOR_SCHEME_FORMAT_LEGACY
            if dom.hasAttribute("format"):
               format = Version.from_string(dom.attributes["format"].value)

            if format >= ColorScheme.COLOR_SCHEME_FORMAT_TREE:   # tree format?
                items = ColorScheme._parse_dom_node(dom, None, {})
            else:
                _logger.warning(_format( \
                    "Loading legacy color scheme format '{old_format}', "
                    "please consider upgrading to current format "
                    "'{new_format}': '{filename}'",
                    old_format = format,
                    new_format = ColorScheme.COLOR_SCHEME_FORMAT,
                    filename = filename))

                items = ColorScheme._parse_legacy_color_scheme(dom)

            if  not items is None:
                root = Root()
                root.set_items(items)

                color_scheme = ColorScheme()
                color_scheme.name = name
                color_scheme._filename = filename
                color_scheme.is_system = is_system
                color_scheme._root = root
                #print(root.dumps())
        except xml.parsers.expat.ExpatError as ex:
            _logger.error(_format("Error loading color scheme '{filename}'. "
                                  "{exception}: {cause}",
                                  filename = filename,
                                  exception = type(ex).__name__,
                                  cause = unicode_str(ex)))
        finally:
            f.close()

        return color_scheme

    @staticmethod
    def _parse_dom_node(dom_node, parent_item, used_keys):
        """ Recursive function to parse all dom nodes of the layout tree """
        items = []
        for child in dom_node.childNodes:
            if child.nodeType == minidom.Node.ELEMENT_NODE:
                if child.tagName == "window":
                    item = ColorScheme._parse_window(child)
                elif child.tagName == "layer":
                    item = ColorScheme._parse_layer(child)
                elif child.tagName == "icon":
                    item = ColorScheme._parse_icon(child)
                elif child.tagName == "key_group":
                    item = ColorScheme._parse_key_group(child, used_keys)
                elif child.tagName == "color":
                    item = ColorScheme._parse_color(child)
                else:
                    item = None

                if item:
                    item.parent = parent_item
                    item.items = ColorScheme._parse_dom_node(child, item, used_keys)
                    items.append(item)

        return items

    @staticmethod
    def _parse_dom_node_item(node, item):
        """ Parses common properties of all items """
        if node.hasAttribute("id"):
            item.id = node.attributes["id"].value

    @staticmethod
    def _parse_window(node):
        item = Window()
        if node.hasAttribute("type"):
            item.type = node.attributes["type"].value
        ColorScheme._parse_dom_node_item(node, item)
        return item

    @staticmethod
    def _parse_layer(node):
        item = Layer()
        ColorScheme._parse_dom_node_item(node, item)
        return item

    @staticmethod
    def _parse_icon(node):
        item = Icon()
        ColorScheme._parse_dom_node_item(node, item)
        return item

    _key_ids_pattern = re.compile(r'[\w-]+(?:[.][\w-]+)?', re.UNICODE)

    @staticmethod
    def _parse_key_group(node, used_keys):
        item = KeyGroup()
        ColorScheme._parse_dom_node_item(node, item)

        # read key ids
        text = "".join([n.data for n in node.childNodes \
                        if n.nodeType == n.TEXT_NODE])
        ids = [id for id in ColorScheme._key_ids_pattern.findall(text) if id]

        # check for duplicate key definitions
        for key_id in ids:
            if key_id in used_keys:
                raise ValueError(_format("Duplicate key_id '{}' found "
                                         "in color scheme file. "
                                         "Key_ids must occur only once.",
                                         key_id))

        used_keys.update(list(zip(ids, ids)))

        item.key_ids = ids

        return item

    @staticmethod
    def _parse_color(node):
        item = KeyColor()
        ColorScheme._parse_dom_node_item(node, item)

        if node.hasAttribute("element"):
            item.element = node.attributes["element"].value
        if node.hasAttribute("rgb"):
            value = node.attributes["rgb"].value
            item.rgb = [hexstring_to_float(value[1:3])/255,
                        hexstring_to_float(value[3:5])/255,
                        hexstring_to_float(value[5:7])/255]
        if node.hasAttribute("opacity"):
            item.opacity = float(node.attributes["opacity"].value)

        state = {}
        ColorScheme._parse_state_attibute(node, "prelight", state)
        ColorScheme._parse_state_attibute(node, "pressed", state)
        ColorScheme._parse_state_attibute(node, "active", state)
        ColorScheme._parse_state_attibute(node, "locked", state)
        ColorScheme._parse_state_attibute(node, "insensitive", state)
        ColorScheme._parse_state_attibute(node, "scanned", state)
        ColorScheme._parse_state_attibute(node, "hover", state)
        item.state = state

        return item

    @staticmethod
    def _parse_state_attibute(node, name, state):
        if node.hasAttribute(name):
            value = node.attributes[name].value == "true"
            state[name] = value

            if name == "locked" and value:
                state["active"] = True  # locked implies active


    ###########################################################################
    @staticmethod
    def _parse_legacy_color_scheme(dom_node):
        """ Load a color scheme and return it as a new object. """

        color_defaults = {
                    "fill":                   [0.0,  0.0,  0.0, 1.0],
                    "hovered":                [0.0,  0.0,  0.0, 1.0],
                    "pressed":                [0.6,  0.6,  0.6, 1.0],
                    "pressed-latched":        [0.6,  0.6,  0.6, 1.0],
                    "pressed-locked":         [0.6,  0.6,  0.6, 1.0],
                    "latched":                [0.5,  0.5,  0.5, 1.0],
                    "locked":                 [1.0,  0.0,  0.0, 1.0],
                    "scanned":                [0.45, 0.45, 0.7, 1.0],

                    "stroke":                 [0.0,  0.0,  0.0, 1.0],
                    "stroke-hovered":         [0.0,  0.0,  0.0, 1.0],
                    "stroke-pressed":         [0.0,  0.0,  0.0, 1.0],
                    "stroke-pressed-latched": [0.0,  0.0,  0.0, 1.0],
                    "stroke-pressed-locked":  [0.0,  0.0,  0.0, 1.0],
                    "stroke-latched":         [0.0,  0.0,  0.0, 1.0],
                    "stroke-locked":          [0.0,  0.0,  0.0, 1.0],
                    "stroke-scanned":         [0.0,  0.0,  0.0, 1.0],

                    "label":                  [0.0,  0.0,  0.0, 1.0],
                    "label-hovered":          [0.0,  0.0,  0.0, 1.0],
                    "label-pressed":          [0.0,  0.0,  0.0, 1.0],
                    "label-pressed-latched":  [0.0,  0.0,  0.0, 1.0],
                    "label-pressed-locked":   [0.0,  0.0,  0.0, 1.0],
                    "label-latched":          [0.0,  0.0,  0.0, 1.0],
                    "label-locked":           [0.0,  0.0,  0.0, 1.0],
                    "label-scanned":          [0.0,  0.0,  0.0, 1.0],

                    "dwell-progress":         [0.82, 0.19, 0.25, 1.0],
                    }

        items = []

        # layer colors
        layers = dom_node.getElementsByTagName("layer")
        if not layers:
            # Still accept "pane" for backwards compatibility
            layers = dom_node.getElementsByTagName("pane")
        for i, layer in enumerate(layers):
            attrib = "fill"
            rgb = None
            opacity = None

            color = KeyColor()
            if layer.hasAttribute(attrib):
                value = layer.attributes[attrib].value
                color.rgb = [hexstring_to_float(value[1:3])/255,
                hexstring_to_float(value[3:5])/255,
                hexstring_to_float(value[5:7])/255]


            oattrib = attrib + "-opacity"
            if layer.hasAttribute(oattrib):
                color.opacity = float(layer.attributes[oattrib].value)

            color.element = "background"
            layer = Layer()
            layer.set_items([color])
            items.append(layer)

        # key groups
        used_keys = {}
        root_key_group = None
        key_groups = []
        for group in dom_node.getElementsByTagName("key_group"):

            # Check for default flag.
            # Default colors are applied to all keys
            # not found in the color scheme.
            default_group = False
            if group.hasAttribute("default"):
                default_group = bool(group.attributes["default"].value)

            # read key ids
            text = "".join([n.data for n in group.childNodes])
            key_ids = [x for x in re.findall(r'\w+(?:[.][\w-]+)?', text) if x]

            # check for duplicate key definitions
            for key_id in key_ids:
                if key_id in used_keys:
                    raise ValueError(_format("Duplicate key_id '{}' found "
                                             "in color scheme file. "
                                             "Key_ids must occur only once.",
                                             key_id))
            used_keys.update(list(zip(key_ids, key_ids)))

            colors = []

            for attrib in list(color_defaults.keys()):

                rgb = None
                opacity = None

                # read color attribute
                if group.hasAttribute(attrib):
                    value = group.attributes[attrib].value
                    rgb = [hexstring_to_float(value[1:3])/255,
                                 hexstring_to_float(value[3:5])/255,
                                 hexstring_to_float(value[5:7])/255]

                # read opacity attribute
                oattrib = attrib + "-opacity"
                if group.hasAttribute(oattrib):
                    opacity = float(group.attributes[oattrib].value)

                if not rgb is None or not opacity is None:
                    elements = ["fill", "stroke", "label", "dwell-progress"]
                    for element in elements:
                        if attrib.startswith(element):
                            break
                    else:
                        element = "fill"

                    if attrib.startswith(element):
                        state_attrib = attrib[len(element):]
                        if state_attrib.startswith("-"):
                            state_attrib = state_attrib[1:]
                    else:
                        state_attrib = attrib

                    color = KeyColor()
                    color.rgb = rgb
                    color.opacity = opacity
                    color.element = element
                    if state_attrib:
                        color.state = {state_attrib : True}
                    else:
                        color.state = {}

                    colors.append(color)

            key_group = KeyGroup()
            key_group.set_items(colors)
            key_group.key_ids = key_ids
            if default_group:
                root_key_group = key_group
            else:
                key_groups.append(key_group)

        if root_key_group:
            root_key_group.append_items(key_groups)
            items.append(root_key_group)

        return items


class ColorSchemeItem(TreeItem):
    """ Base class of color scheme items """

    def dumps(self):
        """
        Recursively dumps the (sub-) tree starting from self.
        Returns a multi-line string.
        """
        global _level
        if not "_level" in globals():
            _level = -1
        _level += 1
        s = "   "*_level + repr(self) + "\n" + \
               "".join(item.dumps() for item in self.items)
        _level -= 1
        return s

    def is_window(self):
        return False
    def is_layer(self):
        return False
    def is_icon(self):
        return False
    def is_key_group(self):
        return False
    def is_color(self):
        return False

    def find_key_id(self, key_id):
        """ Find the key group that has key_id """
        if self.is_key_group():
           if key_id in self.key_ids:
               return self

        for child in self.items:
            item = child.find_key_id(key_id)
            if item:
                return item

        return None


class Root(ColorSchemeItem):
    """ Container for a layers colors """

    def get_windows(self):
        """
        Get list of window in order of appearance
        in the color scheme file.
        """
        windows = []
        for item in self.items:
            if item.is_window():
                windows.append(item)
        return windows

    def get_layers(self):
        """
        Get list of layer items in order of appearance
        in the color scheme file.
        """
        layers = []
        for item in self.items:
            if item.is_layer():
                layers.append(item)
        return layers

    def get_icons(self):
        """
        Get list of the icon items in order of appearance
        in the color scheme file.
        """
        icons = []
        for item in self.items:
            if item.is_icon():
                icons.append(item)
        return icons

    def get_default_key_group(self):
        """ Default key group for keys that aren't part of any key group """
        for child in self.items:
            if child.is_key_group():
                return child
        return None


class Window(ColorSchemeItem):
    """ Container for a window's colors """

    type = ""   # keyboard, key-popup

    def is_window(self):
        return True


class Layer(ColorSchemeItem):
    """ Container for a layer's colors """

    def is_layer(self):
        return True


class Icon(ColorSchemeItem):
    """ Container for a Icon's' colors """

    def is_icon(self):
        return True


class Color(ColorSchemeItem):
    """ A single color, rgb + opacity """
    element = None
    rgb = None
    opacity = None

    def __repr__(self):
        return "{} element={} rgb={} opacity={}".format( \
                                    ColorSchemeItem.__repr__(self),
                                    repr(self.element),
                                    repr(self.rgb),
                                    repr(self.opacity))
    def is_color(self):
        return True

    def matches(self, element, *args):
        """
        Returns true if self matches the given parameters.
        """
        return self.element == element


class KeyColor(Color):
    """
    A single key (or layer) color.
    """
    state = None   # dict whith "pressed"=True, "active"=False, etc.

    def __repr__(self):
        return "{} element={} rgb={} opacity={} state={}".format( \
                                    ColorSchemeItem.__repr__(self),
                                    repr(self.element),
                                    repr(self.rgb),
                                    repr(self.opacity),
                                    repr(self.state))

    def matches(self, element, state):
        """
        Returns true if self matches the given parameters.
        state attributes match if they are equal or None, i.e. an
        empty state dict always matches.
        """
        result = True

        if not self.element == element:
            return False

        for attr, value in list(state.items()):
            # Special case for fill color
            # By default the fill color is only applied to the single
            # state where nothing is pressed, active, hover, locked, etc.
            # All other elements apply to all state permutations if
            # not asked to do otherwise.
            # Allows for hard coded default fill colors to take over without
            # doing anything special in the color scheme files.
            default = value  # "don't care", always match unspecified states

            if element == "fill" and \
               attr in ["active", "locked", "pressed", "scanned", "hover"] and \
               not attr in self.state:
                default = False   # consider unspecified states to be False

            if (element == "label" or element == "secondary-label") and \
               attr in [ "insensitive", "pressed", "hover"] and \
               not attr in self.state:
                default = False   # consider unspecified states to be False
                
            if (element == "stroke") and \
               attr in [ "hover", "pressed" ] and \
               not attr in self.state:
                default = False   # consider unspecified states to be False

            if  self.state.get(attr, default) != value:
                result = False

        return result


class KeyGroup(ColorSchemeItem):
    """ A group of key ids and their colors """
    key_ids = ()

    def __repr__(self):
        return "{} key_ids={}".format(ColorSchemeItem.__repr__(self),
                                    repr(self.key_ids))

    def is_key_group(self):
        return True

    def find_element_color(self, element, state):
        rgb = None
        opacity = None

        # walk key groups from self down to the root
        for key_group in self.iter_to_root():
            if key_group.is_key_group():

                # run through all colors of the key group, top to bottom
                for child in key_group.items:
                    if child.is_color():
                        for color in child.iter_depth_first():

                            # matching color found?
                            if color.matches(element, state):
                                if rgb is None:
                                    rgb = color.rgb
                                if opacity is None:
                                    opacity = color.opacity
                                if not rgb is None and not opacity is None:
                                    return rgb, opacity # break early

        return rgb, opacity
