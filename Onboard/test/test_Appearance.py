#!/usr/bin/python3

import os
import copy
import tempfile
import unittest

from Onboard.Appearance import ColorScheme
from Onboard.settings import ThemeDialog


class TestColorScheme(unittest.TestCase):

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="test_onboard_")
        self._old_user_path = ColorScheme.user_path
        ColorScheme.user_path = staticmethod(lambda: self._tmp_dir.name)
        self._source_filename = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "themes", "Typist.colors")

    def tearDown(self):
        ColorScheme.user_path = staticmethod(self._old_user_path)
        self._tmp_dir.cleanup()

    def test_saved_state_colors_override_generic_colors(self):
        scheme = ColorScheme.load(self._source_filename, True)
        scheme.set_default_key_rgba("label", {"active": True},
                                    [0.1, 0.2, 0.3, 0.4])
        scheme.set_default_key_rgba("label",
                                    {"active": True, "locked": True},
                                    [0.4, 0.3, 0.2, 0.1])
        scheme.set_layer_fill_rgba(0, [0.2, 0.3, 0.4, 0.5])
        scheme.save_as("test", "Test")

        scheme = ColorScheme.load(
            os.path.join(self._tmp_dir.name, "test.colors"))
        state = {"prelight": False, "pressed": False, "active": True,
                 "locked": True, "scanned": False, "hover": False,
                 "insensitive": False}
        self.assertEqual(
            [0.4, 0.2980392156862745, 0.2, 0.1],
            scheme.get_default_key_rgba("label", state))
        self.assertEqual(
            [0.2, 0.2980392156862745, 0.4, 0.5],
            scheme.get_layer_fill_rgba(0))

    def test_saved_scheme_can_be_restored(self):
        scheme = ColorScheme.load(self._source_filename, True)
        scheme.save_as("test", "Test")
        original_scheme = copy.deepcopy(scheme)
        scheme.set_default_key_rgba("fill", {"pressed": True},
                                    [0.1, 0.2, 0.3, 0.4])
        scheme.save()

        original_scheme.save()
        scheme = ColorScheme.load(
            os.path.join(self._tmp_dir.name, "test.colors"))
        state = {"prelight": False, "pressed": True, "active": False,
                 "locked": False, "scanned": False, "hover": False,
                 "insensitive": False}
        self.assertNotEqual(
            [0.1, 0.2, 0.3, 0.4],
            scheme.get_default_key_rgba("fill", state))

    def test_custom_scheme_basename_avoids_existing_schemes(self):
        dialog = ThemeDialog.__new__(ThemeDialog)
        dialog.theme = type("Theme", (), {"basename": "Test"})()
        existing = ColorScheme.build_user_filename("Test-custom")
        with open(existing, "w"):
            pass

        self.assertEqual("Test-custom-2",
                         dialog._build_custom_color_scheme_basename())

    def test_color_scheme_groups_omit_empty_custom_section(self):
        scheme = type("ColorScheme", (), {"name": "Built-in",
                                             "is_system": True})()
        groups = ThemeDialog._get_color_scheme_groups([scheme])
        self.assertEqual(["Built-in Color Schemes"],
                         [header for header, _schemes in groups])

        scheme = type("ColorScheme", (), {"name": "Custom",
                                             "is_system": False})()
        groups = ThemeDialog._get_color_scheme_groups([scheme])
        self.assertEqual(["Custom Color Schemes"],
                         [header for header, _schemes in groups])
