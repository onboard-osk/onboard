#!/usr/bin/python3

# Copyright © 2014 marmuta <marmvta@gmail.com>
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

import os
import tempfile
import unittest

import Onboard.LayoutLoaderSVG
from Onboard.LayoutLoaderSVG import LayoutLoaderSVG
import Onboard.osk as osk
from Onboard.utils import Translation

class TestLayoutLoaderSVG(unittest.TestCase):

    class Config_mockup:
        class ThemeSettings:
            key_label_overrides = {}
        theme_settings = ThemeSettings()

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="test_onboard_")
        self._dir = self._tmp_dir.name
        self._user_dir = os.path.join(self._dir, "onboard")
        self._model_dir = os.path.join(self._user_dir, "models")

        # Setup translation, else tests fail in label translation deep in
        # LayoutLoaderSVG.
        Translation.install("onboard")

    def test_system_keyboard_layout_alternatives1(self):
        """
        Without layout tag, multiple keys with the same id must be allowed.
        Don't let the keymap alternatives check swallow them.
        """
        layout = self._load_test_layout(
                 """
                    <key id="layer0"/>
                    <key id="layer0"/>
                 """)
        items = list(layout.find_ids(["layer0"]))
        self.assertEqual(2, len(items))

    def test_system_keyboard_layout_alternatives2(self):
        """
        With layout tag, only the first matching key must be loaded.
        """
        layout = self._load_test_layout(
                 """
                    <key id="layer0" label="key1" layout="us"/>
                    <key id="layer0" label="key2"/>
                 """)
        items = list(layout.find_ids(["layer0"]))
        self.assertEqual(1, len(items))
        self.assertEqual("key1", items[0].labels[0])

    def test_system_keyboard_layout_alternatives3(self):
        """
        With layout tag, if there is no match, fall back to the key without layout tag.
        """
        layout = self._load_test_layout(
                 """
                    <key id="layer0" label="key1" layout="us"/>
                    <key id="layer0" label="key2"/>
                 """, "de")
        items = list(layout.find_ids(["layer0"]))
        self.assertEqual(1, len(items))
        self.assertEqual("key2", items[0].labels[0])

    def test_system_keyboard_layout_alternatives4(self):
        """
        Match rules must only affect siblings. Keys elswhere must be loaded.
        """
        layout = self._load_test_layout(
                 """
                    <key id="layer0" label="key1" layout="us"/>
                    <panel>
                        <key id="layer0" label="key2"/>
                    </panel>
                 """)
        items = list(layout.find_ids(["layer0"]))
        self.assertEqual(2, len(items))

    def _load_test_layout(self, key_definitions,
                          system_keyboard_layout="us",
                          system_keyboard_variant = ""):
        layout_contents = """<?xml version="1.0" ?>
        <keyboard id="Test" format="3.1">
            <panel filename="test.svg">
            {key_definitions}
            </panel>
        </keyboard>
        """.format(key_definitions=key_definitions)

        svg_contents = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
        <svg height="115" width="330" version="1.0">
            <rect id="layer0" x="0" y="0" width="20" height="20"/>
            <rect id="layer0" x="20" y="0" width="20" height="20"/>
        </svg>
        """

        layout_fn = os.path.join(self._dir, "test.onboard")
        svg_fn = os.path.join(self._dir, "test.svg")
        self._write_to_file(layout_fn, layout_contents, )
        self._write_to_file(svg_fn, svg_contents,)

        vk = osk.Virtkey()
        ll = LayoutLoaderSVG()
        if system_keyboard_layout:
            ll._get_system_keyboard_layout = \
                lambda vk: (system_keyboard_layout, system_keyboard_variant, "pc105")
        Onboard.LayoutLoaderSVG.config = self.Config_mockup()
        layout = ll.load(vk, layout_fn, None)
        return layout

    @staticmethod
    def _write_to_file(fn, contents):
        with open(fn, mode="w", encoding="UTF-8") as f:
            f.write(contents)

