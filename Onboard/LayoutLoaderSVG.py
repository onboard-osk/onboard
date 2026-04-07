# -*- coding: utf-8 -*-

# Copyright © 2008-2009 Chris Jones <tortoise@tortuga>
# Copyright © 2010 Francesco Fumanti <francesco.fumanti@gmx.net>
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

import logging
_logger = logging.getLogger("LayoutLoaderSVG")

import os
import re
import sys
import shutil
from xml.dom import minidom

from Onboard                 import Exceptions
from Onboard                 import KeyCommon
from Onboard.KeyCommon       import (StickyBehavior, ImageSlot,
                                     KeyPath, KeyGeometry)
from Onboard.Layout          import LayoutRoot, LayoutBox, LayoutPanel
from Onboard.utils           import (modifiers, Rect,
                                     toprettyxml, Version, open_utf8,
                                     permute_mask, LABEL_MODIFIERS,
                                     unicode_str, XDGDirs)

# Layout items that can be created dynamically via the 'class' XML attribute.
from Onboard.WordSuggestions import WordListPanel  # noqa: flake8
from Onboard.KeyGtk          import (RectKey, WordlistKey, BarKey, # noqa: flake8
                                     WordKey, InputlineKey)
from Onboard.CharacterPalette import (EmojiPalettePanel,    # noqa: flake8
                                      SymbolPalettePanel,
                                      CharacterPaletteKey)

from Onboard.Config import Config
config = Config()


class LayoutLoaderSVG:
    """
    Keyboard layout loaded from an SVG file.
    """
    # onboard <= 0.95
    LAYOUT_FORMAT_LEGACY      = Version(1, 0)

    # onboard 0.96, initial layout-tree
    LAYOUT_FORMAT_LAYOUT_TREE = Version(2, 0)

    # onboard 0.97, scanner overhaul, no more scan columns,
    # new attributes scannable, scan_priority
    LAYOUT_FORMAT_SCANNER     = Version(2, 1)

    # onboard 0.99, prerelease on Nexus 7,
    # new attributes key.action, key.sticky_behavior.
    # allow (i.e. have by default) keycodes for modifiers.
    LAYOUT_FORMAT_2_2         = Version(2, 2)

    # onboard 0.99, key_templates in key_def.xml and include tags.
    LAYOUT_FORMAT_3_0         = Version(3, 0)

    # sub-layouts for popups, various new key attributes,
    # label_margin, theme_id, popup_id
    LAYOUT_FORMAT_3_1         = Version(3, 1)

    # new key attributes show_active
    LAYOUT_FORMAT_3_2         = Version(3, 2)

    # current format
    LAYOUT_FORMAT             = LAYOUT_FORMAT_3_2

    # precalc mask permutations
    _label_modifier_masks = permute_mask(LABEL_MODIFIERS)

    # XKB model -> physical layout family
    _model_to_physical_layout = {
        "pc104": "ansi",
        "pc101": "ansi",
        "pc105": "iso",
    }

    # Per-physical-layout key overrides
    _physical_layout_overrides = {
        "ansi": {
            "svg_id_remap": {
                "RTRN": "RTRN_ansi",
                "BKSL": "BKSL_ansi",
                "LFSH": "LFSH_ansi",
            },
            "hidden_keys": {"LSGT"},
        },
    }

    def __init__(self):
        self._vk = None
        self._svg_cache = {}
        self._format = None   # format of the currently loading layout
        self._layout_filename = ""
        self._color_scheme = None
        self._root_layout_dir = ""  # path to svg files
        self._layout_regex = re.compile(r"([^\(]+) (?: \( ([^\)]*) \) )?",
                                        re.VERBOSE)

    def load(self, vk, layout_filename, color_scheme):
        """ Load layout root file. """
        self._system_layout, self._system_variant, self._system_model = \
            self._get_system_keyboard_layout(vk)
        _logger.info("current system keyboard layout(variant): '{}'"
                     .format(self._get_system_layout_string()))

        layout = self._load(vk, layout_filename, color_scheme,
                            os.path.dirname(layout_filename))
        if layout:
            # purge attributes only used during loading
            for item in layout.iter_items():
                if item.templates is not None:
                    item.templates = None
                if item.keysym_rules is not None:
                    item.keysym_rules = None

            # enable caching
            layout = LayoutRoot(layout)

            # Fill in missing target_layer_id attributes
            self._fill_in_target_layer_id(layout)

        return layout

    def _load(self, vk, layout_filename, color_scheme,
              root_layout_dir, parent_item=None):
        """ Load or include layout file at any depth level. """
        self._vk = vk
        self._layout_filename = layout_filename
        self._color_scheme = color_scheme
        self._root_layout_dir = root_layout_dir
        return self._load_layout(layout_filename, parent_item)

    def _load_layout(self, layout_filename, parent_item=None):
        self._svg_cache = {}
        layout = None

        try:
            f = open_utf8(layout_filename)
        except FileNotFoundError as ex:
            _logger.warning("Failed to open '{}': {}"
                            .format(layout_filename, unicode_str(ex)))
            return None

        # make sure unlink is called
        with minidom.parse(f).documentElement as dom:

            # check layout format, no format version means legacy layout
            format = self.LAYOUT_FORMAT_LEGACY
            if dom.hasAttribute("format"):
                format = Version.from_string(dom.attributes["format"].value)
            self._format = format

            root = LayoutPanel()  # root, representing the 'keyboard' tag
            root.set_id("__root__")  # id for debug prints

            # Init included root with the parent item's svg filename.
            # -> Allows to skip specifying svg filenames in includes.
            if parent_item:
                root.filename = parent_item.filename

            if format >= self.LAYOUT_FORMAT_LAYOUT_TREE:
                self._parse_dom_node(dom, root)
                layout = root
            else:
                _logger.warning(
                    _format("Loading legacy layout, format '{}'. "
                            "Please consider upgrading to current format '{}'",
                            format, self.LAYOUT_FORMAT))
                items = self._parse_legacy_layout(dom)
                if items:
                    root.set_items(items)
                    layout = root

        f.close()

        self._svg_cache = {}  # Free the memory
        return layout

    @staticmethod
    def _fill_in_target_layer_id(layout):
        """
        Make sure the target_layer_id attribute is set for all layer buttons.
        """
        layer_ids = layout.get_layer_ids()
        if layer_ids:
            for item in layout.iter_items():
                if item.is_key() and \
                   item.is_layer_button() and \
                   not item.target_layer_id:
                    item.target_layer_id = layer_ids[item.get_layer_index()]

    def _parse_dom_node(self, dom_node, parent_item):
        """ Recursively traverse the dom nodes of the layout tree. """
        loaded_ids = set()

        for child in dom_node.childNodes:
            if child.nodeType == minidom.Node.ELEMENT_NODE:

                # Skip over items with non-matching keyboard layout string.
                # Items with the same id are processed from top to bottom,
                # the first match wins. If no item matches we fall back to
                # the item without layout string.
                # This is used to select between alternative key definitions
                # depending on the current system layout.
                can_load = False
                if not child.hasAttribute("id"):
                    can_load = True
                else:
                    id = child.attributes["id"].value
                    if id not in loaded_ids:
                        if child.hasAttribute("layout"):
                            layout = child.attributes["layout"].value
                            can_load = self._has_matching_layout(layout)

                            # don't look at items with this id again
                            if can_load:
                                loaded_ids.add(id)
                        else:
                            can_load = True

                if can_load:
                    tag = child.tagName

                    # rule and control tags
                    if tag == "include":
                        self._parse_include(child, parent_item)
                    elif tag == "key_template":
                        self._parse_key_template(child, parent_item)
                    elif tag == "keysym_rule":
                        self._parse_keysym_rule(child, parent_item)
                    elif tag == "layout":
                        item = self._parse_sublayout(child, parent_item)
                        parent_item.append_sublayout(item)
                        self._parse_dom_node(child, item)
                    else:
                        # actual items that make up the layout tree
                        if tag == "box":
                            item = self._parse_box(child)
                        elif tag == "panel":
                            item = self._parse_panel(child)
                        elif tag == "key":
                            item = self._parse_key(child, parent_item)
                        else:
                            item = None

                        if item:
                            parent_item.append_item(item)
                            self._parse_dom_node(child, item)

    def _parse_include(self, node, parent):
        if node.hasAttribute("file"):
            filename = node.attributes["file"].value
            filepath = config.find_layout_filename(filename, "layout include")
    
            loader = LayoutLoaderSVG()
            loader._system_layout = self._system_layout
            loader._system_variant = self._system_variant
            loader._system_model = self._system_model
            _logger.info("Including layout '{}'".format(filename))

            incl_root = loader._load(self._vk,
                                    filepath,
                                    self._color_scheme,
                                    self._root_layout_dir,
                                    parent)

            if incl_root:
                parent.append_items(incl_root.items)
                parent.update_keysym_rules(incl_root.keysym_rules)
                parent.update_templates(incl_root.templates)
                # cleanup temporary data from included layout
                incl_root.items = None
                incl_root.keysym_rules = None
                incl_root.templates = None

    def _parse_key_template(self, node, parent):
        """
        Templates are partially define layout items. Later non-template
        items inherit attributes of templates with matching id.
        """
        attributes = dict(list(node.attributes.items()))
        id = attributes.get("id")
        if not id:
            raise Exceptions.LayoutFileError(
                "'id' attribute required for template '{}' "
                "in layout '{}'"
                .format(str(list(attributes.values())),
                        self._layout_filename))

        parent.update_templates({(id, RectKey) : attributes})

    def _parse_keysym_rule(self, node, parent):
        """
        Keysym rules link attributes like "label", "image"
        to certain keysyms.
        """
        attributes = dict(list(node.attributes.items()))
        keysym = attributes.get("keysym")
        if keysym:
            del attributes["keysym"]
            if keysym.startswith("0x"):
                keysym = int(keysym, 16)
            else:
                # translate symbolic keysym name
                keysym = 0

            if keysym:
                parent.update_keysym_rules({keysym : attributes})

    def _init_item(self, attributes, item_class):
        """ Parses attributes common to all LayoutItems """

        id = attributes.get("id")

        # allow to override the item's default class
        if "class" in attributes:
            class_name = attributes["class"]
            try:
                item_class = globals()[class_name]
            except KeyError as ex:
                _logger.warning("key {}: class {} not found - "
                                .format(repr(id), repr(class_name)) +
                                unicode_str(ex))

        # create the item
        item = item_class()

        if id is not None:
            item.id = id

        value = attributes.get("group")
        if value is not None:
            item.group = value

        value = attributes.get("layer")
        if value is not None:
            item.layer_id = value

        value = attributes.get("filename")
        if value is not None:
            item.filename = value

        value = attributes.get("visible")
        if value is not None:
            item.visible = value == "true"

        value = attributes.get("sensitive")
        if value is not None:
            item.sensitive = value == "true"

        value = attributes.get("border")
        if value is not None:
            item.border = float(value)

        value = attributes.get("expand")
        if value is not None:
            item.expand = value == "true"

        value = attributes.get("unlatch_layer")
        if value is not None:
            item.unlatch_layer = value == "true"

        value = attributes.get("scannable")
        if value and value.lower() == 'false':
            item.scannable = False

        value = attributes.get("scan_priority")
        if value is not None:
            item.scan_priority = int(value)

        return item

    def _parse_sublayout(self, node, parent):
        attributes = dict(node.attributes.items())
        item = self._init_item(attributes, LayoutPanel)

        # make templates accessible in the sublayout
        item.sublayout_parent = parent
        return item

    def _parse_box(self, node):
        attributes = dict(node.attributes.items())
        item = self._init_item(attributes, LayoutBox)
        if node.hasAttribute("orientation"):
            item.horizontal = \
                node.attributes["orientation"].value.lower() == "horizontal"
        if node.hasAttribute("spacing"):
            item.spacing = float(node.attributes["spacing"].value)
        if node.hasAttribute("compact"):
            item.compact = node.attributes["compact"].value == "true"
        return item

    def _parse_panel(self, node):
        attributes = dict(node.attributes.items())
        item = self._init_item(attributes, LayoutPanel)
        if node.hasAttribute("compact"):
            item.compact = node.attributes["compact"].value == "true"
        return item

    def _parse_key(self, node, parent):
        result = None

        id = node.attributes["id"].value
        if id == "inputline":
            item_class = InputlineKey
        else:
            item_class = RectKey

        # find template attributes
        attributes = {}
        if node.hasAttribute("id"):
            theme_id, id = RectKey.parse_id(node.attributes["id"].value)
            attributes.update(self.find_template(parent, RectKey, [id]))

        # let current node override any preceding templates
        attributes.update(dict(node.attributes.items()))

        # handle common layout-item attributes
        key = self._init_item(attributes, item_class)
        key.parent = parent  # assign early to have get_filename() work

        # handle key-specific attributes
        self._init_key(key, attributes)

        # get key geometry from the closest svg file
        filename = key.get_filename()
        if not filename:
            if not attributes.get("group") == "wsbutton":
                _logger.warning(_format("Ignoring key '{}'."
                                        " No svg filename defined.",
                                        key.id))
        else:
            svg_nodes = self._get_svg_keys(filename)
            if svg_nodes:
                # try svg_id first, if there is one
                if key.svg_id != key.id:
                    svg_node = svg_nodes.get(key.svg_id)
                else:
                    # then the regular id
                    svg_node = svg_nodes.get(key.id)

                if svg_node:
                    r, geometry = svg_node.extract_key_params()
                    key.set_initial_border_rect(r.copy())
                    key.set_border_rect(r.copy())
                    key.geometry = geometry
                    result = key

                    overrides = self._get_layout_overrides()
                    if overrides and key.id in overrides.get("hidden_keys", ()):
                        key.set_visible(False)
                else:
                    _logger.warning("Ignoring key '{}'."
                                    " No svg object found for '{}' in '{}'."
                                    .format(key.id, key.svg_id, filename))

        return result  # ignore keys not found in an svg file

    def _init_key(self, key, attributes):
        # Re-parse the id to distinguish between the short key_id
        # and the optional longer theme_id.
        full_id = attributes["id"]
        theme_id = attributes.get("theme_id")
        svg_id = attributes.get("svg_id")
        key.set_id(full_id, theme_id, svg_id)

        # Remap SVG IDs for non-default physical layouts
        overrides = self._get_layout_overrides()
        if overrides:
            remap = overrides.get("svg_id_remap", {})
            if key.id in remap:
                key.svg_id = remap[key.id]

        if "_" in key.get_id():
            _logger.warning("underscore in key id '{}', please use dashes"
                            .format(key.get_id()))

        value = attributes.get("modifier")
        if value:
            try:
                key.modifier = modifiers[value]
            except KeyError as ex:
                (strerror) = ex
                raise Exceptions.LayoutFileError(
                    "Unrecognized modifier {} in"
                    "definition of {}".format(strerror, full_id))

        value = attributes.get("action")
        if value:
            try:
                key.action = KeyCommon.actions[value]
            except KeyError as ex:
                (strerror) = ex
                raise Exceptions.LayoutFileError(
                    "Unrecognized key action {} in"
                    "definition of {}".format(strerror, full_id))

        if "char" in attributes:
            key.code = attributes["char"]
            key.type = KeyCommon.CHAR_TYPE
        elif "keysym" in attributes:
            value = attributes["keysym"]
            key.type = KeyCommon.KEYSYM_TYPE
            if value[1] == "x":  # for hex keysym
                key.code = int(value, 16)
            else:
                key.code = int(value, 10)
        elif "keypress_name" in attributes:
            key.code = attributes["keypress_name"]
            key.type = KeyCommon.KEYPRESS_NAME_TYPE
        elif "macro" in attributes:
            key.code = attributes["macro"]
            key.type = KeyCommon.MACRO_TYPE
        elif "script" in attributes:
            key.code = attributes["script"]
            key.type = KeyCommon.SCRIPT_TYPE
        elif "keycode" in attributes:
            key.code = int(attributes["keycode"])
            key.type = KeyCommon.KEYCODE_TYPE
        elif "target_layer" in attributes:
            key.code = key.id[:]
            key.type = KeyCommon.BUTTON_TYPE
            key.target_layer_id = attributes["target_layer"]
        elif "button" in attributes:
            key.code = key.id[:]
            key.type = KeyCommon.BUTTON_TYPE
        elif key.modifier:
            key.code = None
            key.type = KeyCommon.LEGACY_MODIFIER_TYPE
        else:
            # key without action: just draw it, do nothing on click
            key.action = None
            key.action_type = None

        # get the size group of the key
        if "group" in attributes:
            group_name = attributes["group"]
        else:
            group_name = "_default"

        # get the optional image filename
        if "image" in attributes:
            if not key.image_filenames:
                key.image_filenames = {}
            key.image_filenames[ImageSlot.NORMAL] = \
                attributes["image"].split(";")[0]
        if "image_active" in attributes:
            if not key.image_filenames:
                key.image_filenames = {}
            key.image_filenames[ImageSlot.ACTIVE] = attributes["image_active"]

        # get labels
        labels = self._parse_key_labels(attributes, key)

        # Replace label and size group with overrides from
        # theme and/or system defaults.
        label_overrides = config.theme_settings.key_label_overrides
        override = label_overrides.get(key.id)
        if override:
            olabel, ogroup = override
            if olabel:
                labels = {0 : olabel[:]}
                if ogroup:
                    group_name = ogroup[:]

        key.labels = labels
        key.group = group_name

        # optionally  override the theme's default key_style
        if "key_style" in attributes:
            key.style = attributes["key_style"]

        # select what gets drawn, different from "visible" flag as this
        # doesn't affect the layout.
        if "show" in attributes:
            if attributes["show"].lower() == 'false':
                key.show_face = False
                key.show_border = False
        if "show_face" in attributes:
            if attributes["show_face"].lower() == 'false':
                key.show_face = False
        if "show_border" in attributes:
            if attributes["show_border"].lower() == 'false':
                key.show_border = False

        # show_active: allow to display key in latched or locked state
        # legacy show_active behavior was hard-coded for layer0
        if self._format < LayoutLoaderSVG.LAYOUT_FORMAT_3_2:
            if key.id == "layer0":
                key.show_active = False
        if "show_active" in attributes:
            if attributes["show_active"].lower() == 'false':
                key.show_active = False

        if "label_x_align" in attributes:
            key.label_x_align = float(attributes["label_x_align"])
        if "label_y_align" in attributes:
            key.label_y_align = float(attributes["label_y_align"])

        if "label_margin" in attributes:
            values = attributes["label_margin"].replace(" ", "").split(",")
            margin = [float(x) if x else key.label_margin[i]
                      for i, x in enumerate(values[:2])]
            margin += margin[:1] * (2 - len(margin))
            if margin:
                key.label_margin = margin

        if "sticky" in attributes:
            sticky = attributes["sticky"].lower()
            if sticky == "true":
                key.sticky = True
            elif sticky == "false":
                key.sticky = False
            else:
                raise Exceptions.LayoutFileError(
                    "Invalid value '{}' for 'sticky' attribute of key '{}'"
                    .format(sticky, key.id))
        else:
            key.sticky = False

        # legacy sticky key behavior was hard-coded for CAPS
        if self._format < LayoutLoaderSVG.LAYOUT_FORMAT_2_2:
            if key.id == "CAPS":
                key.sticky_behavior = StickyBehavior.LOCK_ONLY

        value = attributes.get("sticky_behavior")
        if value:
            try:
                key.sticky_behavior = StickyBehavior.from_string(value)
            except KeyError as ex:
                (strerror) = ex
                raise Exceptions.LayoutFileError(
                    "Unrecognized sticky behavior {} in"
                    "definition of {}".format(strerror, full_id))

        if "tooltip" in attributes:
            key.tooltip = attributes["tooltip"]

        if "popup_id" in attributes:
            key.popup_id = attributes["popup_id"]

        if "chamfer_size" in attributes:
            key.chamfer_size = float(attributes["chamfer_size"])

        key.color_scheme = self._color_scheme

    def _parse_key_labels(self, attributes, key):
        labels = {}   # {modifier_mask : label, ...}

        # Get labels from keyboard mapping first.
        if key.type == KeyCommon.KEYCODE_TYPE and \
           key.id not in ["BKSP"]:
            if self._vk:  # xkb keyboard found?
                vkmodmasks = self._label_modifier_masks
                if sys.version_info.major == 2:
                    vkmodmasks = [int(m) for m in vkmodmasks]
                vklabels = self._vk.labels_from_keycode(key.code, vkmodmasks)
                if sys.version_info.major == 2:
                    vklabels = [x.decode("UTF-8") for x in vklabels]
                labels = {m : l for m, l in zip(vkmodmasks, vklabels)}
            else:
                if key.id.upper() == "SPCE":
                    labels[0] = "No X keyboard found, retrying..."
                else:
                    labels[0] = "?"

        # If key is a macro (snippet) generate label from its number.
        elif key.type == KeyCommon.MACRO_TYPE:
            label, text = config.snippets.get(int(key.code), (None, None))
            tooltip = _format("Snippet {}", key.code)
            if not label:
                labels[0] = "     --     "
                # i18n: full string is "Snippet n, unassigned"
                tooltip += _(", unassigned")
            else:
                labels[0] = label.replace("\\n", "\n")
            key.tooltip = tooltip

        # get labels from the key/template definition in the layout
        layout_labels = self._parse_layout_labels(attributes)
        if layout_labels:
            labels = layout_labels

        # override with per-keysym labels
        keysym_rules = self._get_keysym_rules(key)
        if key.type == KeyCommon.KEYCODE_TYPE:
            if self._vk:  # xkb keyboard found?
                vkmodmasks = self._label_modifier_masks
                try:
                    if sys.version_info.major == 2:
                        vkmodmasks = [int(m) for m in vkmodmasks]
                    vkkeysyms  = self._vk.keysyms_from_keycode(key.code,
                                                               vkmodmasks)
                except AttributeError:
                    # virtkey until 0.61.0 didn't have that method.
                    vkkeysyms = []

                # replace all labels whith keysyms matching a keysym rule
                for i, keysym in enumerate(vkkeysyms):
                    attributes = keysym_rules.get(keysym)
                    if attributes:
                        label = attributes.get("label")
                        if label is not None:
                            mask = vkmodmasks[i]
                            labels[mask] = label

        # Translate labels - Gettext behaves oddly when translating
        # empty strings
        return {mask : lab and _(lab) or None
                for mask, lab in labels.items()}

    def _parse_layout_labels(self, attributes):
        """ Deprecated label definitions up to v0.98.x """
        labels = {}
        # modifier masks were hard-coded in python-virtkey
        if "label" in attributes:
            labels[0] = attributes["label"]
            if "cap_label" in attributes:
                labels[1] = attributes["cap_label"]
            if "shift_label" in attributes:
                labels[2] = attributes["shift_label"]
            if "altgr_label" in attributes:
                labels[128] = attributes["altgr_label"]
            if "altgrNshift_label" in attributes:
                labels[129] = attributes["altgrNshift_label"]
            if "_label" in attributes:
                labels[129] = attributes["altgrNshift_label"]
        return labels

    def _get_svg_keys(self, filename):
        svg_nodes = self._svg_cache.get(filename)
        if svg_nodes is None:
            svg_nodes = self._load_svg_keys(filename)
            self._svg_cache[filename] = svg_nodes

        return svg_nodes

    def _load_svg_keys(self, filename):
        filename = os.path.join(self._root_layout_dir, filename)
        try:
            with open_utf8(filename) as svg_file:
                svg_dom = minidom.parse(svg_file).documentElement
                svg_nodes = self._parse_svg(svg_dom)
                svg_nodes = {node.id : node for node in svg_nodes}
        except Exceptions.LayoutFileError as ex:
            raise Exceptions.LayoutFileError(
                "error loading '{}'".format(filename),
                chained_exception=(ex))
        return svg_nodes

    def _parse_svg(self, node):
        svg_nodes = []
        for child in node.childNodes:
            if child.nodeType == minidom.Node.ELEMENT_NODE:
                tag = child.tagName
                if tag in ("rect", "path", "g"):
                    svg_node = SVGNode()
                    id = child.attributes["id"].value
                    svg_node.id = id

                    if tag == "rect":
                        svg_node.bounds = \
                            Rect(float(child.attributes['x'].value),
                                 float(child.attributes['y'].value),
                                 float(child.attributes['width'].value),
                                 float(child.attributes['height'].value))

                    elif tag == "path":
                        data = child.attributes['d'].value

                        try:
                            svg_node.path = KeyPath.from_svg_path(data)
                        except ValueError as ex:
                            raise Exceptions.LayoutFileError(
                                "while reading geometry with id '{}'"
                                .format(id),
                                chained_exception=(ex))

                        svg_node.bounds = svg_node.path.get_bounds()
                        if svg_node.bounds.is_empty():
                            raise Exceptions.LayoutFileError(
                                "empty bounding box of svg path "
                                "while reading geometry with id '{}': '{}'"
                                .format(id, data))

                    elif tag == "g":  # group
                        svg_node.children = self._parse_svg(child)

                    svg_nodes.append(svg_node)

                svg_nodes.extend(self._parse_svg(child))

        return svg_nodes

    def find_template(self, scope_item, classinfo, ids):
        """
        Look for a template definition upwards from item until the root.
        """
        for item in scope_item.iter_to_global_root():
            templates = item.templates
            if templates:
                for id in ids:
                    match = templates.get((id, classinfo))
                    if match is not None:
                        return match
        return {}

    def _get_keysym_rules(self, scope_item):
        """
        Collect and merge keysym_rule from the root to item.
        Rules in nested items overwrite their parents'.
        """
        keysym_rules = {}
        for item in reversed(list(scope_item.iter_to_root())):
            if item.keysym_rules is not None:
                keysym_rules.update(item.keysym_rules)

        return keysym_rules

    def _get_system_keyboard_layout(self, vk):
        """ get names of the currently active layout group and variant """

        if vk:  # xkb keyboard found?
            group = vk.get_current_group()
            names = vk.get_rules_names()
        else:
            group = 0
            names = ""

        if not names:
            names = ("base", "pc105", "us", "", "")
        model = names[1]
        layouts  = names[2].split(",")
        variants = names[3].split(",")

        if group >= 0 and group < len(layouts):
            layout = layouts[group]
        else:
            layout = ""
        if group >= 0 and group < len(variants):
            variant = variants[group]
        else:
            variant = ""

        return layout, variant, model

    def _get_physical_layout(self):
        """ Return the physical layout family for the current model. """
        return self._model_to_physical_layout.get(self._system_model)

    def _get_layout_overrides(self):
        """ Return key overrides for the current physical layout, or None. """
        physical = self._get_physical_layout()
        if physical:
            return self._physical_layout_overrides.get(physical)
        return None

    def _get_system_layout_string(self):
        s = self._system_layout
        if self._system_variant:
            s += "(" + self._system_variant + ")"
        return s

    def _has_matching_layout(self, layout_str):
        """
        Check if one ot the given layout strings matches
        system keyboard layout and variant.

        Doctests:
        >>> l = LayoutLoaderSVG()
        >>> l._system_layout = "ch"
        >>> l._system_variant = "fr"
        >>> l._has_matching_layout("ch(x), us, de")
        False
        >>> l._has_matching_layout("abc, ch(fr)")
        True
        >>> l._system_variant = ""
        >>> l._has_matching_layout("ch(x), us, de")
        False
        >>> l._has_matching_layout("ch, us, de")
        True
        """
        layouts = layout_str.split(",")  # comma separated layout specifiers
        sys_layout = self._system_layout
        sys_variant = self._system_variant
        for value in layouts:
            layout, variant = self._layout_regex.search(value.strip()).groups()
            if layout == sys_layout and \
               (not variant or sys_variant.startswith(variant)):
                return True
        return False

    # --------------------------------------------------------------------------
    # Legacy pane layout support
    # --------------------------------------------------------------------------
    def _parse_legacy_layout(self, dom_node):

        # parse panes
        panes = []
        is_scan = False
        for i, pane_node in enumerate(dom_node.getElementsByTagName("pane")):
            item = LayoutPanel()
            item.layer_id = "layer {}".format(i)

            item.id       = pane_node.attributes["id"].value
            item.filename = pane_node.attributes["filename"].value

            # parse keys
            keys = []
            for node in pane_node.getElementsByTagName("key"):
                key = self._parse_key(node, item)
                if key:
                    # some keys have changed since Onboard 0.95
                    if key.id == "middleClick":
                        key.set_id("middleclick")
                        key.type = KeyCommon.BUTTON_TYPE
                    if key.id == "secondaryClick":
                        key.set_id("secondaryclick")
                        key.type = KeyCommon.BUTTON_TYPE

                    keys.append(key)

            item.set_items(keys)

            # check for scan columns
            if pane_node.getElementsByTagName("column"):
                is_scan = True

            panes.append(item)

        layer_area = LayoutPanel()
        layer_area.id = "layer_area"
        layer_area.set_items(panes)

        # find the most frequent key width
        histogram = {}
        for key in layer_area.iter_keys():
            w = key.get_border_rect().w
            histogram[w] = histogram.get(w, 0) + 1
        most_frequent_width = max(list(zip(list(histogram.values()),
                                           list(histogram.keys()))))[1] \
            if histogram else 18

        # Legacy onboard had automatic tab-keys for pane switching.
        # Simulate this by generating layer buttons from scratch.
        keys = []
        group = "__layer_buttons__"
        widen = 1.4 if not is_scan else 1.0
        rect = Rect(0, 0, most_frequent_width * widen, 20)

        key = RectKey()
        attributes = {}
        attributes["id"]     = "hide"
        attributes["group"]  = group
        attributes["image"]  = "close.svg"
        attributes["button"] = "true"
        attributes["scannable"] = "false"
        self._init_key(key, attributes)
        key.set_border_rect(rect.copy())
        keys.append(key)

        key = RectKey()
        attributes = {}
        attributes["id"]     = "move"
        attributes["group"]  = group
        attributes["image"]  = "move.svg"
        attributes["button"] = "true"
        attributes["scannable"] = "false"
        self._init_key(key, attributes)
        key.set_border_rect(rect.copy())
        keys.append(key)

        if len(panes) > 1:
            for i, pane in enumerate(panes):
                key = RectKey()
                attributes = {}
                attributes["id"]     = "layer{}".format(i)
                attributes["group"]  = group
                attributes["label"]  = pane.id
                attributes["button"] = "true"
                self._init_key(key, attributes)
                key.set_border_rect(rect.copy())
                keys.append(key)

        layer_switch_column = LayoutBox()
        layer_switch_column.horizontal = False
        layer_switch_column.set_items(keys)

        layout = LayoutBox()
        layout.border = 1
        layout.spacing = 2
        layout.set_items([layer_area, layer_switch_column])

        return [layout]

    @staticmethod
    def copy_layout(src_filename, dst_filename):
        src_dir = os.path.dirname(src_filename)
        dst_dir, name_ext = os.path.split(dst_filename)
        dst_basename, ext = os.path.splitext(name_ext)
        _logger.info(_format("copying layout '{}' to '{}'",
                             src_filename, dst_filename))

        domdoc = None
        svg_filenames = {}
        fallback_layers = {}

        try:
            with open_utf8(src_filename) as f:
                domdoc = minidom.parse(f)
                keyboard_node = domdoc.documentElement

                # check layout format
                format = LayoutLoaderSVG.LAYOUT_FORMAT_LEGACY
                if keyboard_node.hasAttribute("format"):
                    format = Version.from_string(
                        keyboard_node.attributes["format"].value)
                keyboard_node.attributes["id"] = dst_basename

                if format < LayoutLoaderSVG.LAYOUT_FORMAT_LAYOUT_TREE:
                    raise Exceptions.LayoutFileError(
                        _format("copy_layouts failed, "
                                "unsupported layout format '{}'.",
                                format))
                else:
                    # replace the basename of all svg filenames
                    for node in LayoutLoaderSVG._iter_dom_nodes(keyboard_node):
                        if LayoutLoaderSVG.is_layout_node(node):
                            if node.hasAttribute("filename"):
                                filename = node.attributes["filename"].value

                                # Create a replacement layer name for the
                                # unlikely case  that the svg-filename doesn't
                                # contain a layer section
                                # (as in path/basename-layer.ext).
                                fallback_layer_name = fallback_layers.get(
                                    filename,
                                    "Layer" + str(len(fallback_layers)))
                                fallback_layers[filename] = fallback_layer_name

                                # replace the basename of this filename
                                new_filename = \
                                    LayoutLoaderSVG._replace_basename(
                                        filename, dst_basename,
                                        fallback_layer_name)

                                node.attributes["filename"].value = \
                                    new_filename
                                svg_filenames[filename] = new_filename

            if domdoc:
                XDGDirs.assure_user_dir_exists(config.get_user_layout_dir())

                # write the new layout file
                with open_utf8(dst_filename, "w") as f:
                    xml = toprettyxml(domdoc)
                    if sys.version_info.major == 2:  # python 2?
                        xml = xml.encode("UTF-8")
                    f.write(xml)

                    # copy the svg files
                    for src, dst in list(svg_filenames.items()):

                        dir, name = os.path.split(src)
                        if not dir:
                            src = os.path.join(src_dir, name)
                        dir, name = os.path.split(dst)
                        if not dir:
                            dst = os.path.join(dst_dir, name)

                        _logger.info(_format("copying svg file '{}' to '{}'",
                                     src, dst))
                        shutil.copyfile(src, dst)
        except OSError as ex:
            _logger.error("copy_layout failed: " +
                          unicode_str(ex))
        except Exceptions.LayoutFileError as ex:
            _logger.error(unicode_str(ex))

    @staticmethod
    def remove_layout(filename):
        for fn in LayoutLoaderSVG.get_layout_svg_filenames(filename):
            os.remove(fn)
        os.remove(filename)

    @staticmethod
    def get_layout_svg_filenames(filename):
        results = []
        domdoc = None
        with open_utf8(filename) as f:
            domdoc = minidom.parse(f).documentElement

        if domdoc:
            filenames = {}
            for node in LayoutLoaderSVG._iter_dom_nodes(domdoc):
                if LayoutLoaderSVG.is_layout_node(node):
                    if node.hasAttribute("filename"):
                        fn = node.attributes["filename"].value
                        filenames[fn] = fn

            layout_dir, name = os.path.split(filename)
            results = []
            for fn in list(filenames.keys()):
                dir, name = os.path.split(fn)
                results.append(os.path.join(layout_dir, name))

        return results

    @staticmethod
    def _replace_basename(filename, new_basename, fallback_layer_name):
        """
        Doctests:
        # Basename has to be replaced with new_basename.
        >>> test = LayoutLoaderSVG._replace_basename
        >>> test("/home/usr/.local/share/onboard/Base-Alpha.svg",
        ... "NewBase","Fallback")
        'NewBase-Alpha.svg'

        # Dashes in front are allowed, but the layer name must not have any.
        >>> test("/home/usr/.local/share/onboard/a-b-c-Alpha.svg",
        ... "d-e-f","g-h")
        'd-e-f-Alpha.svg'
        """
        dir, name_ext = os.path.split(filename)
        name, ext = os.path.splitext(name_ext)
        if name:
            index = name.rfind("-")
            if index >= 0:
                layer = name[index + 1:]
            else:
                layer = fallback_layer_name
            return "{}-{}{}".format(new_basename, layer, ext)
        return ""

    @staticmethod
    def is_layout_node(dom_node):
        return dom_node.tagName in ["include", "key_template", "keysym_rule",
                                    "box", "panel", "key", "layout"]

    @staticmethod
    def _iter_dom_nodes(dom_node):
        """ Recursive generator function to traverse aa dom tree """
        yield dom_node

        for child in dom_node.childNodes:
            if child.nodeType == minidom.Node.ELEMENT_NODE:
                for node in LayoutLoaderSVG._iter_dom_nodes(child):
                    yield node


class SVGNode:
    """
    Cache of SVG provided key attributes.
    """
    id = None             # svg_id
    bounds = None         # logical bounding rect, aka border rect
    path = None           # optional path for arbitrary shapes

    def __init__(self):
        self.children = []

    def extract_key_params(self):
        if self.children:
            nodes = self.children[:2]
        else:
            nodes = [self]
        bounds = nodes[0].bounds
        paths = [node.path for node in nodes if node.path]
        if paths:
            geometry = KeyGeometry.from_paths(paths)
        else:
            geometry = None
        return bounds, geometry


