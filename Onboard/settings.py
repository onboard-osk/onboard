#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Copyright © 2006-2007, 2009 Chris Jones <tortoise@tortuga>
# Copyright © 2008-2010 Francesco Fumanti <francesco.fumanti@gmx.net>
# Copyright © 2011-2012 Gerd Kohlberger <lowfi@chello.at>
# Copyright © 2011-2017 marmuta <marmvta@gmail.com>
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

""" Onboard preferences utility """

from __future__ import division, print_function, unicode_literals

import os
import sys
import copy
from subprocess import Popen
from xml.parsers.expat import ExpatError
from xml.dom import minidom
try:
    import dbus.mainloop.glib
except ImportError:
    pass

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GObject, Pango, Gdk, Gtk

from Onboard.LayoutLoaderSVG import LayoutLoaderSVG
from Onboard.SnippetView     import SnippetView
from Onboard.Appearance      import Theme, ColorScheme
from Onboard.Scanner         import ScanMode, ScanDevice
from Onboard.XInput          import XIDeviceManager, XIEventType
from Onboard.utils           import unicode_str, open_utf8, escape_markup, \
                                    XDGDirs
from Onboard.WindowUtils     import show_ask_string_dialog, \
                                    show_confirmation_dialog
from Onboard.UDevTracker     import UDevTracker
from Onboard.definitions     import StatusIconProviderEnum


app = "onboard"

### Logging ###
import logging
_logger = logging.getLogger("Settings")
###############

### Config Singleton ###
from Onboard.Config import Config, NumResizeHandles
config = Config()
########################


def LoadUI(filebase):
    builder = Gtk.Builder()
    builder.set_translation_domain(app)
    builder.add_from_file(os.path.join(config.install_dir, filebase+".ui"))
    return builder

def format_list_item(text, issystem):
    if issystem:
        return "<i>{0}</i>".format(text)
    return text


class DialogBuilder(object):
    """
    Utility class for simplified widget setup.
    Has helpers for connecting widgets to ConfigObject properties, i.e.
    indirectly to gsettings keys.
    Mostly borrowed from Gerd Kohlberger's ScannerDialog.
    """

    def __init__(self, builder):
        self._builder = builder

    def wid(self, name):
        widget = self._builder.get_object(name)
        if not widget:
            _logger.error("widget '{}' not found, aborting".format(name))
            assert(widget)
        return widget

    # push button
    def bind_button(self, name, callback):
        w = self.wid(name)
        w.connect("clicked", callback)

    # text entry
    def bind_entry(self, name, config_object, key,
                   config_get_callback=None, config_set_callback=None):
        w = self.wid(name)
        if config_get_callback:
            value = config_get_callback(config_object, key)
        else:
            value = getattr(config_object, key)
        w.set_text(value)
        w.connect("changed", self._bind_entry_callback,
                  config_object, key, config_set_callback)
        getattr(config_object, key + '_notify_add')(
            lambda x: self._notify_entry_callback(w, config_object, key,
                                                  config_get_callback))

    def _notify_entry_callback(self, widget, config_object,
                               key, config_get_callback):
        if config_get_callback:
            value = config_get_callback(config_object, key)
        else:
            value = getattr(config_object, key)
        widget.set_text(value)

    def _bind_entry_callback(self, widget, config_object,
                             key, config_set_callback):
        if config_set_callback:
            config_set_callback(config_object, key, widget.get_text())
        else:
            setattr(config_object, key, widget.get_text())

    # spin button
    def bind_spin(self, name, config_object, key,
                  config_get_callback=None, config_set_callback=None):
        w = self.wid(name)
        if config_get_callback:
            value = config_get_callback(config_object, key)
        else:
            value = getattr(config_object, key)
        w.set_value(value)
        w.connect("value-changed", self._bind_spin_callback,
                  config_object, key, config_set_callback)
        getattr(config_object, key + '_notify_add')( \
             lambda x: self._notify_spin_callback(w, config_object, key,
                                                     config_get_callback))

    def _notify_spin_callback(self, widget, config_object,
                              key, config_get_callback):
        if config_get_callback:
            value = config_get_callback(config_object, key)
        else:
            value = getattr(config_object, key)
        widget.set_value(value)

    def _bind_spin_callback(self, widget, config_object,
                            key, config_set_callback):
        if config_set_callback:
            config_set_callback(config_object, key, widget.get_value())
        else:
            setattr(config_object, key, widget.get_value())

    # scale
    def bind_scale(self, name, config_object, key,
                   config_get_callback=None, config_set_callback=None):
        w = self.wid(name)
        if config_get_callback:
            value = config_get_callback(config_object, key)
        else:
            value = getattr(config_object, key)
        w.set_value(value)
        w.connect("value-changed", self._bind_scale_callback,
                  config_object, key, config_set_callback)
        getattr(config_object, key + '_notify_add')(w.set_value)
        getattr(config_object, key + '_notify_add')(
            lambda x: self._notify_scale_callback(w, config_object, key,
                                                  config_get_callback))

    def _notify_scale_callback(self, widget, config_object,
                               key, config_get_callback):
        if config_get_callback:
            value = config_get_callback(config_object, key)
        else:
            value = getattr(config_object, key)
        widget.set_value(value)

    def _bind_scale_callback(self, widget, config_object,
                             key, config_set_callback):
        value = widget.get_value()
        if config_set_callback:
            config_set_callback(config_object, key, value)
        else:
            setattr(config_object, key, value)

    # checkbox
    def bind_check(self, name, config_object, key,
                   config_get_callback=None, config_set_callback=None):
        w = self.wid(name)
        if config_get_callback:
            active = config_get_callback(config_object, key)
        else:
            active = getattr(config_object, key)
        w.set_active(active)
        w.connect("toggled", self._bind_check_callback,
                  config_object, key, config_set_callback)
        getattr(config_object, key + '_notify_add')( \
             lambda x: self._notify_check_callback(w, config_object, key,
                                                   config_get_callback))

    def _notify_check_callback(self, widget, config_object,
                              key, config_get_callback):
        if config_get_callback:
            active = config_get_callback(config_object, key)
        else:
            active = getattr(config_object, key)
        widget.set_active(active)

    def _bind_check_callback(self, widget, config_object,
                             key, config_set_callback):
        if config_set_callback:
            config_set_callback(config_object, key, widget.get_active())
        else:
            setattr(config_object, key, widget.get_active())

    # combobox with id column
    def bind_combobox_id(self, name, config_object, key,
                         config_get_callback=None, config_set_callback=None):
        w = self.wid(name)

        if config_get_callback:
            id = config_get_callback(config_object, key)
        else:
            id = str(getattr(config_object, key))

        if not config_set_callback:
            config_set_callback = self.bind_combobox_config_set

        w.set_active_id(id)
        w.connect("changed", self._bind_combobox_callback,
                  config_object, key, config_set_callback)
        getattr(config_object, key + '_notify_add')( \
             lambda x: self._notify_combobox_callback(w, config_object, key,
                                                     config_get_callback))
    def _notify_combobox_callback(self, widget,
                                 config_object, key, config_get_callback):
        if config_get_callback:
            id = config_get_callback(config_object, key)
        else:
            id = str(getattr(config_object, key))
        widget.set_active_id(id)

    def _bind_combobox_callback(self, widget,
                               config_object, key, config_set_callback):
        id = widget.get_active_id()
        config_set_callback(config_object, key, id)

    def bind_combobox_config_set(self, config_object, key, id):
        assert(not id is None)  # make sure ID-Column is 0
        if not id is None:
            setattr(config_object, key, int(id))

    def get_tree_view_selection(self, view, column):
        sel = view.get_selection()
        if sel:
            it = sel.get_selected()[1]
            if it:
                return view.get_model().get_value(it, column)
        return None

    def select_tree_view_row(self, view, column, value, _it=None):
        model = view.get_model()
        if _it is None:
            _it = model.get_iter_first()
        while _it:
            child_it = model.iter_children(_it)
            if child_it:
                self.select_tree_view_row(view, column, value, child_it)

            fn = model.get_value(_it, column)
            if fn == value:
                sel = view.get_selection()
                if sel:
                    sel.select_iter(_it)

            _it = model.iter_next(_it)


class Settings(DialogBuilder):

    # column ids of the layout view
    LAYOUT_COL_NAME = 0
    LAYOUT_COL_SUMMARY = 1
    LAYOUT_COL_FILENAME = 2
    LAYOUT_COL_HAS_ABOUT_INFO = 3
    LAYOUT_COL_IS_ROW_SENSITIVE = 4


    def __init__(self, mainwin):
        self.themes = {}       # cache of theme objects

        # Use D-bus main loop by default
        if "dbus" in globals():
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        else:
            _logger.warning("D-Bus bindings unavailable.")

        # finish config initialization
        config.init()

        # init dialog builder
        builder = LoadUI("settings")
        DialogBuilder.__init__(self, builder)

        self.window = builder.get_object("settings_window")
        Gtk.Window.set_default_icon_name("onboard")
        self.window.set_title(_("Onboard Preferences"))

        # General tab
        self.status_icon_toggle = builder.get_object("status_icon_toggle")
        self.status_icon_toggle.set_active(config.show_status_icon)
        config.show_status_icon_notify_add(self.status_icon_toggle.set_active)

        self.start_minimized_toggle = builder.get_object(
            "start_minimized_toggle")
        self.start_minimized_toggle.set_active(config.start_minimized)
        config.start_minimized_notify_add(
            self.start_minimized_toggle.set_active)

        self.icon_palette_toggle = builder.get_object("icon_palette_toggle")
        self.icon_palette_toggle.set_active(config.icp.in_use)
        config.icp.in_use_notify_add(self.icon_palette_toggle.set_active)

        self.onboard_xembed_toggle = \
            builder.get_object("onboard_xembed_toggle")
        self.onboard_xembed_toggle.set_active(config.onboard_xembed_enabled)
        config.onboard_xembed_enabled_notify_add(
            self.onboard_xembed_toggle.set_active)

        self.show_tooltips_toggle = builder.get_object("show_tooltips_toggle")
        self.show_tooltips_toggle.set_active(config.show_tooltips)
        config.show_tooltips_notify_add(self.show_tooltips_toggle.set_active)

        self.bind_combobox_id("status_icon_provider_combobox",
                              config, "status_icon_provider")
        config.status_icon_provider_notify_add(
            lambda x: self.update_window_widgets())
        self.bind_combobox_id("status_icon_left_click_action_combobox",
                              config, "status_icon_left_click_action")
        # window tab
        self.window_decoration_toggle = \
            builder.get_object("window_decoration_toggle")
        self.window_decoration_toggle.set_active(
            config.window.window_decoration)
        config.window.window_decoration_notify_add(
            lambda x: [self.window_decoration_toggle.set_active(x),
                       self.update_window_widgets()])

        self.window_state_sticky_toggle = \
            builder.get_object("window_state_sticky_toggle")
        self.window_state_sticky_toggle.set_active(
            config.window.window_state_sticky)
        config.window.window_state_sticky_notify_add(
            self.window_state_sticky_toggle.set_active)

        self.force_to_top_toggle = builder.get_object("force_to_top_toggle")
        self.force_to_top_toggle.set_active(config.is_force_to_top())
        config.window.force_to_top_notify_add(lambda x: \
                                       [self.force_to_top_toggle.set_active(x),
                                        self.update_window_widgets()])

        self.keep_aspect_ratio_toggle = builder.get_object(
            "keep_aspect_ratio_toggle")
        self.keep_aspect_ratio_toggle.set_active(config.window.keep_aspect_ratio)
        config.window.keep_aspect_ratio_notify_add(
            self.keep_aspect_ratio_toggle.set_active)

        self.transparent_background_toggle = \
                         builder.get_object("transparent_background_toggle")
        self.transparent_background_toggle.set_active(config.window.transparent_background)
        config.window.transparent_background_notify_add(lambda x:
                            [self.transparent_background_toggle.set_active(x),
                             self.update_window_widgets()])

        self.transparency_spinbutton = builder.get_object("transparency_spinbutton")
        self.transparency_spinbutton.set_value(config.window.transparency)
        config.window.transparency_notify_add(self.transparency_spinbutton.set_value)

        self.background_transparency_spinbutton = \
                           builder.get_object("background_transparency_spinbutton")
        self.background_transparency_spinbutton.set_value(config.window.background_transparency)
        config.window.background_transparency_notify_add(self.background_transparency_spinbutton.set_value)

        self.inactivity_frame = builder.get_object("inactive_behavior_frame")

        self.enable_inactive_transparency_toggle = \
                    builder.get_object("enable_inactive_transparency_toggle")
        self.enable_inactive_transparency_toggle.set_active( \
                                        config.window.enable_inactive_transparency)
        config.window.enable_inactive_transparency_notify_add(lambda x: \
                            [self.enable_inactive_transparency_toggle.set_active(x),
                             self.update_window_widgets()])

        self.inactive_transparency_spinbutton = \
                             builder.get_object("inactive_transparency_spinbutton")
        self.inactive_transparency_spinbutton.set_value(config.window.inactive_transparency)
        config.window.inactive_transparency_notify_add(self.inactive_transparency_spinbutton.set_value)

        self.inactive_transparency_delay_spinbutton = \
                             builder.get_object("inactive_transparency_delay_spinbutton")
        self.inactive_transparency_delay_spinbutton.set_value(config.window.inactive_transparency_delay)
        config.window.inactive_transparency_delay_notify_add(self.inactive_transparency_delay_spinbutton.set_value)

        def _get(config_object, key):
            handles = getattr(config_object, key)
            return str(config.window_handles_to_num_handles(handles))

        def _set(config_object, key, value):
            handles = config.num_handles_to_window_handles(int(value))
            setattr(config_object, key, handles[:])

        self.bind_combobox_id("num_window_handles_combobox",
                              config.window, "window_handles",
                              _get, _set)

        def _get(config_object, key):
            handles = getattr(config_object, key)
            return str(config.window_handles_to_num_handles(handles))

        def _set(config_object, key, value):
            handles = config.num_handles_to_icon_palette_handles(int(value))
            setattr(config_object, key, handles[:])

        self.bind_combobox_id("num_icon_palette_handles_combobox",
                              config.icp, "window_handles",
                              _get, _set)

        # Keyboard - first page
        self.bind_check("touch_feedback_enabled_toggle",
                        config.keyboard, "touch_feedback_enabled")

        def _set(config_object, key, value):
            if value < 20:
                value = 0
            setattr(config_object, key, value)
            self._update_touch_feedback_size_label()

        self.bind_scale("touch_feedback_size_scale",
                        config.keyboard, "touch_feedback_size",
                        None, _set)
        self.bind_check("audio_feedback_enabled_toggle",
                        config.keyboard, "audio_feedback_enabled")
        self.bind_check("audio_feedback_place_in_space_toggle",
                        config.keyboard, "audio_feedback_place_in_space")
        self.bind_check("show_secondary_labels_toggle",
                        config.keyboard, "show_secondary_labels")
        self.bind_check("upper_case_on_right_click_toggle",
                        config.keyboard, "key_press_modifiers",
                        config_get_callback=lambda co, key:
                            co.can_upper_case_on_button(3),
                        config_set_callback=lambda co, key, value:
                            co.set_upper_case_on_button(3, value))

        # Keyboard - Advanced page
        self.bind_combobox_id("default_key_action_combobox",
                        config.keyboard, "default_key_action")
        self.bind_combobox_id("key_synth_combobox",
                        config.keyboard, "key_synth")

        def get_sticky_key_behavior(config_object, key):
            behaviors = getattr(config_object, key)
            return behaviors.get("all", "")
        def set_sticky_key_behavior(config_object, key, value):
            behaviors = getattr(config_object, key).copy()
            behaviors["all"] = value
            setattr(config_object, key, behaviors)
        self.bind_combobox_id("sticky_key_behavior_combobox",
                        config.keyboard, "sticky_key_behavior",
                        get_sticky_key_behavior, set_sticky_key_behavior)

        self.bind_spin("sticky_key_release_delay_spinbutton",
                            config.keyboard, "sticky_key_release_delay")

        self.bind_spin("sticky_key_release_on_hide_delay_spinbutton",
                       config.keyboard, "sticky_key_release_on_hide_delay")

        self.bind_combobox_id("touch_input_combobox",
                              config.keyboard, "touch_input")

        def on_input_event_source_set(config_object, key, value):
            self.bind_combobox_config_set(config_object, key, value)
            self.update_window_widgets()

        self.bind_combobox_id("input_event_source_combobox",
                              config.keyboard, "input_event_source",
                              config_set_callback=on_input_event_source_set)

        def get_inter_key_stroke_delay(config_object, key):
            return getattr(config_object, key) * 1000.0

        def set_inter_key_stroke_delay(config_object, key, value):
            setattr(config_object, key, value / 1000.0)

        self.bind_spin("inter_key_stroke_delay_spinbutton",
                       config.keyboard, "inter_key_stroke_delay",
                       get_inter_key_stroke_delay, set_inter_key_stroke_delay)

        def get_popup_duration(config_object, key):
            return getattr(config_object, key)

        def set_popup_duration(config_object, key, value):
            setattr(config_object, key, value)

        self.bind_spin("popup_duration_spinbutton",
                       config.keyboard, "popup_duration",
                       get_popup_duration, set_popup_duration)

        # Auto-show
        self._page_auto_show = PageAutoShow(self, builder)

        # word suggestions
        self._page_word_suggestions = PageWordSuggestions(self, builder)

        # window, docking
        self.docking_enabled_toggle = \
                             builder.get_object("docking_enabled_toggle")
        self.docking_box = builder.get_object("docking_box")

        def on_docking_enabled_config_set(config_object, key, value):
            setattr(config_object, key, value)
            self.update_window_widgets()
        self.bind_check("docking_enabled_toggle",
                        config.window, "docking_enabled",
                        config_set_callback = on_docking_enabled_config_set)
        self.bind_button("docking_settings_button",
                         lambda widget: DockingDialog().run(self.window))

        # layout view
        self.layout_view = builder.get_object("layout_view")
        self.user_layout_root = config.get_user_layout_dir()
        config.layout_notify_add(lambda x: self.update_layout_view_selection())
        self.update_layout_view()
        self.update_layout_widgets()

        # theme view
        self.theme_view = builder.get_object("theme_view")
        self.theme_view.append_column(Gtk.TreeViewColumn(None,
                                                         Gtk.CellRendererText(),
                                                         markup=0))
        self.delete_theme_button = builder.get_object("delete_theme_button")
        self.delete_theme_button
        self.customize_theme_button = \
                                   builder.get_object("customize_theme_button")

        self.update_themeList()
        config.theme_notify_add(self.on_theme_changed)

        self.system_theme_tracking_enabled_toggle = \
                    builder.get_object("system_theme_tracking_enabled_toggle")
        self.system_theme_tracking_enabled_toggle.set_active( \
                                        config.system_theme_tracking_enabled)
        config.system_theme_tracking_enabled_notify_add( \
                                        self.on_system_theme_tracking_changed)

        # Snippets
        self.snippet_view = SnippetView()
        builder.get_object("snippet_scrolled_window").add(self.snippet_view)

        # Universal Access
        scanner_enabled = builder.get_object("scanner_enabled")
        scanner_enabled.set_active(config.scanner.enabled)
        config.scanner.enabled_notify_add(scanner_enabled.set_active)

        self.hide_click_type_window_toggle = \
                builder.get_object("hide_click_type_window_toggle")
        self.hide_click_type_window_toggle.set_active( \
                      config.universal_access.hide_click_type_window)
        config.universal_access.hide_click_type_window_notify_add( \
                      self.hide_click_type_window_toggle.set_active)

        self.enable_click_type_window_on_exit_toggle = \
                builder.get_object("enable_click_type_window_on_exit_toggle")
        self.enable_click_type_window_on_exit_toggle.set_active( \
                      config.universal_access.enable_click_type_window_on_exit)
        config.universal_access.enable_click_type_window_on_exit_notify_add( \
                      self.enable_click_type_window_on_exit_toggle.set_active)

        if config.mousetweaks:
            self.bind_spin("hover_click_delay_spinbutton",
                            config.mousetweaks, "dwell_time")
            self.bind_spin("hover_click_motion_threshold_spinbutton",
                            config.mousetweaks, "dwell_threshold")

        # select last active page
        page = config.current_settings_page
        self.settings_notebook = builder.get_object("settings_notebook")
        self.settings_notebook.set_current_page(page)

        self.pages_view = builder.get_object("pages_view")
        sel = self.pages_view.get_selection()
        if sel:
            sel.select_path(Gtk.TreePath(page))

        # On startup with Gtk 3.14: "Gtk-Message: GtkDialog mapped
        # without a transient parent. This is discouraged."
        # Preferences is a top level dialog, we don't have a parent.
        # No idea how to appease Gtk here.
        #self.window.set_transient_for(None)

        self.window.show_all()

        # update after show_all to apply widget visibility
        self.update_window_widgets()

        # disable hover click controls if mousetweaks isn't installed
        frame = builder.get_object("hover_click_frame")
        frame.set_sensitive(bool(config.mousetweaks))

        self.window.set_keep_above(not mainwin)

        self.window.connect("destroy", Gtk.main_quit)
        builder.connect_signals(self)

        _logger.info("Entering mainloop of Onboard-settings")
        Gtk.main()

    def on_pages_view_cursor_changed(self, widget):
        sel = widget.get_selection()
        if sel:
            paths = sel.get_selected_rows()[1]
            if paths:
                page_num = paths[0].get_indices()[0]
                config.current_settings_page = page_num
                self.settings_notebook.set_current_page(page_num)

    def on_settings_notebook_switch_page(self, widget, gpage, page_num):
        config.current_settings_page = page_num

    def on_snippet_add_button_clicked(self, event):
        _logger.info("Snippet add button clicked")
        self.snippet_view.append("","")

    def on_snippet_remove_button_clicked(self, event):
        _logger.info("Snippet remove button clicked")
        self.snippet_view.remove_selected()

    def on_status_icon_toggled(self,widget):
        config.show_status_icon = widget.get_active()
        self.update_window_widgets()

    def on_start_minimized_toggled(self,widget):
        config.start_minimized = widget.get_active()

    def on_icon_palette_toggled(self, widget):
        if not config.is_icon_palette_last_unhide_option():
            config.icp.in_use = widget.get_active()
        self.update_window_widgets()

    def on_modeless_gksu_toggled(self, widget):
        config.modeless_gksu = widget.get_active()

    def on_xembed_onboard_toggled(self, widget):
        config.enable_gss_embedding(widget.get_active())

    def on_show_tooltips_toggled(self, widget):
        config.show_tooltips = widget.get_active()

    def on_window_decoration_toggled(self, widget):
        if not config.is_force_to_top():
            config.window.window_decoration = widget.get_active()
        self.update_window_widgets()

    def on_window_state_sticky_toggled(self, widget):
        if not config.is_force_to_top():
            config.window.window_state_sticky = widget.get_active()

    def on_force_to_top_toggled(self, widget):
        if not config.is_docking_enabled():
            config.window.force_to_top = widget.get_active()
        self.update_window_widgets()

    def on_keep_aspect_ratio_toggled(self, widget):
        config.window.keep_aspect_ratio = widget.get_active()

    def _update_touch_feedback_size_label(self, value=0):
        value = config.keyboard.touch_feedback_size

        # touch_feedback_size_label: 0=auto, i.e. let Onboard guess
        # the size of the label popup.
        s = str(round(value)) if value else _("auto")
        self.wid("touch_feedback_size_label").set_text(s)

    def update_window_widgets(self):
        force_to_top = config.is_force_to_top()

        # general
        w = self.wid("status_icon_provider_box")
        w.set_sensitive(config.show_status_icon)

        w = self.wid("status_icon_left_click_action_box")
        w.set_sensitive(config.show_status_icon)

        w = self.wid("status_icon_left_click_action_warning")
        w.set_visible(config.get_effective_statusicon_provider() ==
                      StatusIconProviderEnum.AppIndicator)

        self.icon_palette_toggle.set_sensitive(
            not config.is_icon_palette_last_unhide_option())
        active = config.is_icon_palette_in_use()
        if self.icon_palette_toggle.get_active() != active:
            self.icon_palette_toggle.set_active(active)

        # window
        self.window_decoration_toggle.set_sensitive(not force_to_top)
        active = config.has_window_decoration()
        if self.window_decoration_toggle.get_active() != active:
            self.window_decoration_toggle.set_active(active)

        self.window_state_sticky_toggle.set_sensitive( not force_to_top)
        active = config.get_sticky_state()
        if self.window_state_sticky_toggle.get_active() != active:
            self.window_state_sticky_toggle.set_active(active)

        self.force_to_top_toggle.set_sensitive(not config.is_docking_enabled())
        active = force_to_top
        if self.force_to_top_toggle.get_active() != active:
            self.force_to_top_toggle.set_active(active)

        self.background_transparency_spinbutton.set_sensitive( \
                                        not config.has_window_decoration())
        self.start_minimized_toggle.set_sensitive(\
                                        not config.auto_show.enabled)

        self.inactivity_frame.set_sensitive(not config.scanner.enabled)
        active = config.is_inactive_transparency_enabled()
        if self.enable_inactive_transparency_toggle.get_active() != active:
            self.enable_inactive_transparency_toggle.set_active(active)

        # keyboard
        self._update_touch_feedback_size_label()

        # auto-show
        self._page_auto_show.update_ui()

    def update_all_widgets(self):
        pass

    def on_transparent_background_toggled(self, widget):
        config.window.transparent_background = widget.get_active()
        self.update_window_widgets()

    def on_transparency_changed(self, widget):
        config.window.transparency = widget.get_value()

    def on_background_transparency_spinbutton_changed(self, widget):
        config.window.background_transparency = widget.get_value()

    def on_enable_inactive_transparency_toggled(self, widget):
        if not config.scanner.enabled:
            config.window.enable_inactive_transparency = widget.get_active()

    def on_inactive_transparency_changed(self, widget):
        config.window.inactive_transparency = widget.get_value()

    def on_inactive_transparency_delay_changed(self, widget):
        config.window.inactive_transparency_delay = widget.get_value()

    def on_layout_new_button_clicked(self, widget):
        name = self.get_selected_layout_value(self.LAYOUT_COL_NAME)
        filename = self.get_selected_layout_filename()
        if filename:
            new_layout_name = show_ask_string_dialog(
                _format("Copy layout '{}' to this new name:", name), self.window)
            if new_layout_name:
                new_filename = \
                    os.path.join(self.user_layout_root, new_layout_name) + \
                    config.LAYOUT_FILE_EXTENSION
                LayoutLoaderSVG.copy_layout(filename, new_filename)
                self.update_layout_view()
                self.open_user_layout_dir()

    def on_layout_remove_button_clicked(self, event):
        name = self.get_selected_layout_value(self.LAYOUT_COL_NAME)
        filename = self.get_selected_layout_filename()
        if filename:
            question = _format("Delete layout '{}'?", name)
            if show_confirmation_dialog(question, self.window):
                LayoutLoaderSVG.remove_layout(filename)

                config.layout_filename = self.layout_view_model[0][1] \
                                        if len(self.layout_view_model) else ""
                self.update_layout_view()

    def open_user_layout_dir(self):
        user_layout_dir = self.user_layout_root
        XDGDirs.assure_user_dir_exists(user_layout_dir)
        cmd = ["xdg-open", user_layout_dir]
        try:
            Popen(cmd)
        except OSError as e:
            _logger.warning("Failed to run '{}': {}" \
                            .format(" ".join(cmd), unicode_str(e)))

    def on_layout_folder_button_clicked(self, widget):
        self.open_user_layout_dir()

    def update_layout_widgets(self):
        filename = self.get_selected_layout_filename()
        self.wid("layout_new_button").set_sensitive(not filename is None)
        self.wid("layout_remove_button").set_sensitive(not filename is None and \
                                         os.access(filename, os.W_OK))
        has_about_info = bool( \
            self.get_selected_layout_value(self.LAYOUT_COL_HAS_ABOUT_INFO))
        self.wid("layout_about_button").set_sensitive(has_about_info)

    def on_scanner_enabled_toggled(self, widget):
        config.scanner.enabled = widget.get_active()
        self.update_window_widgets()

    def on_scanner_settings_clicked(self, widget):
        ScannerDialog().run(self.window)

    def on_hide_click_type_window_toggled(self, widget):
        config.universal_access.hide_click_type_window = widget.get_active()

    def on_enable_click_type_window_on_exit_toggle(self, widget):
        config.universal_access.enable_click_type_window_on_exit = widget.get_active()

    def on_hover_click_settings_clicked(self, widget):
        filename = "gnome-control-center"
        try:
            Popen([filename, "universal-access"])
        except OSError as e:
            _logger.warning(_format("System settings not found ({}): {}",
                                    filename, unicode_str(e)))

    def on_close_button_clicked(self, widget):
        self.window.destroy()
        Gtk.main_quit()

    def update_layout_view(self):
        model = self.layout_view.get_model()
        self.layout_view_model = model
        model.clear()

        sort_order = ["Small", "Compact", "Full Keyboard", "Phone", "Grid"]
        layout_infos = self._read_layouts(
            os.path.join(config.install_dir, "layouts"), sort_order)

        system = [li for li in layout_infos
                    if li.layout_section == "system"]
        contributed = [li for li in layout_infos
                    if li.layout_section == "contributions"]

        user = self._read_layouts(self.user_layout_root)

        self._add_layout_section(model, system, _("Core layouts"))
        self._add_layout_section(model, contributed, _("Contributions"))
        self._add_layout_section(model, user, _("My layouts"))

        self.layout_view.expand_all()
        self.update_layout_view_selection()

    def update_layout_view_selection(self):
        self.select_tree_view_row(self.layout_view, self.LAYOUT_COL_FILENAME,
                                  config.layout_filename)

    def _add_layout_section(self, model, lis, section_name):
        if lis:
            parent_iter = model.append(None)
            model.set(parent_iter,
                    self.LAYOUT_COL_NAME, "<b>{}</b>" \
                      .format(escape_markup(section_name)))
            for li in lis:
                child_iter = model.append(parent_iter)
                model.set(child_iter,
                        self.LAYOUT_COL_NAME, li.id_string,
                        self.LAYOUT_COL_SUMMARY, li.summary,
                        self.LAYOUT_COL_FILENAME, li.filename,
                        self.LAYOUT_COL_HAS_ABOUT_INFO, li.has_about_info,
                        self.LAYOUT_COL_IS_ROW_SENSITIVE, bool(li.filename))

    def on_layout_about_button_clicked(self, event = None):
        fn = self.get_selected_layout_filename()
        li = self._read_layout(fn)
        if li is None:
            return

        markup = "<big>{}</big>\n".format(escape_markup(li.id))
        body = li.description or li.summary
        if body:
            markup += "\n{}\n".format(escape_markup(body))
        if li.author:
            markup += ("\n" + _("Author: {}") + "\n") \
                .format(escape_markup(li.author))
        markup += "\n <tt>{}</tt>\n".format(escape_markup(li.filename))

        dialog = Gtk.MessageDialog(title=_("About Layout"),
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK)
        dialog.set_markup(markup)
        dialog.set_transient_for(self.window)
        dialog.run()
        dialog.destroy()

    def _read_layouts(self, path, sort_order=()):
        filenames = self._find_layouts(path)

        layout_infos = []
        for filename in filenames:
            li = self._read_layout(filename, sort_order)
            if li:
                layout_infos.append(li)

        return sorted(layout_infos, key=lambda x: x.sort_key)

    def _read_layout(self, filename, sort_order=()):
        li = None

        if filename and os.path.exists(filename):
            file_object = open_utf8(filename)
            try:
                dom_node = minidom.parse(file_object).documentElement

                class LayoutInfo: pass
                li = LayoutInfo()
                id = dom_node.attributes["id"].value
                sort_priority = sort_order.index(id) \
                                if id in sort_order else 1000
                li.id = id
                li.filename = filename
                li.layout_section = self._get_dom_string(dom_node, "section")
                li.summary = _(self._get_dom_string(dom_node, "summary"))
                li.description = \
                    _(self._get_dom_string(dom_node, "description"))
                li.author = self._get_dom_string(dom_node, "author")
                li.sort_key = (li.layout_section.lower(), sort_priority, id.lower())
                li.has_about_info = True
                li.id_string = id

            except ExpatError as ex:
                _logger.error("XML in %s %s" % (filename, unicode_str(ex)))
                li = None
            except KeyError as ex:
                _logger.error("key %s required in %s" % (unicode_str(ex), filename))
                li = None

            file_object.close()

        return li

    @staticmethod
    def _get_dom_string(dom_node, attribute, default = ""):
        if dom_node.hasAttribute(attribute):
            return dom_node.attributes[attribute].value
        return default

    def _find_layouts(self, path):
        layouts = []
        try:
            files = os.listdir(path)
        except OSError:
            files = []
        for filename in files:
            if filename.endswith(".sok") or \
               filename.endswith(config.LAYOUT_FILE_EXTENSION):
                layouts.append(os.path.join(path, filename))
        return layouts

    def on_layout_view_select_cursor_row(self, treeview, *args):
        return False

    def on_layout_view_row_activated(self, treeview, path, view_column):
        #self.on_layout_about_button_clicked() # too annoying
        pass

    def on_layout_view_cursor_changed(self, widget):
        filename = self.get_selected_layout_filename()
        if filename:
            config.layout_filename = filename
        self.update_layout_widgets()

    def get_selected_layout_filename(self):
        sel = self.layout_view.get_selection()
        if sel:
            it = sel.get_selected()[1]
            if it:
                return self.layout_view_model.get_value(it,
                                                    self.LAYOUT_COL_FILENAME)
        return None

    def get_selected_layout_value(self, col_index):
        sel = self.layout_view.get_selection()
        if sel:
            it = sel.get_selected()[1]
            if it:
                return self.layout_view_model.get_value(it, col_index)
        return None

    def on_new_theme_button_clicked(self, widget):
        while True:
            new_name = show_ask_string_dialog(
                _("Enter a name for the new theme:"), self.window)
            if not new_name:
                return

            new_filename = Theme.build_user_filename(new_name)
            if not os.path.exists(new_filename):
                break

            question = _format("This theme file already exists.\n'{filename}'"
                               "\n\nOverwrite it?",
                               filename=new_filename)
            if show_confirmation_dialog(question, self.window):
                break

        theme = self.get_selected_theme()
        if not theme:
            theme = Theme()
        theme.save_as(new_name, new_name)
        config.theme_filename = theme.filename
        self.update_themeList()

    def on_delete_theme_button_clicked(self, widget):
        theme = self.get_selected_theme()
        if theme and not theme.is_system:
            if self.get_hidden_theme(theme):
                question = _("Reset selected theme to Onboard defaults?")
            else:
                question = _("Delete selected theme?")
            reply = show_confirmation_dialog(question, self.window)
            if reply == True:
                # be sure the file hasn't been deleted from outside already
                if os.path.exists(theme.filename):
                    os.remove(theme.filename)

                # Is there a system theme behind the deleted one?
                hidden_theme = self.get_hidden_theme(theme)
                if hidden_theme:
                    config.theme_filename = hidden_theme.filename

                else: # row will disappear
                    # find a neighboring theme to select after deletion
                    near_theme = self.find_neighbor_theme(theme)
                    config.theme_filename = near_theme.filename \
                                            if near_theme else ""

                self.update_themeList()

                # notify gsettings clients
                theme = self.get_selected_theme()
                if theme:
                    theme.apply()

    def find_neighbor_theme(self, theme):
        themes = self.get_sorted_themes()
        for i, tpl in enumerate(themes):
            if theme.basename == tpl[0].basename:
                if i < len(themes)-1:
                    return themes[i+1][0]
                else:
                    return themes[i-1][0]
        return None

    def on_system_theme_tracking_enabled_toggled(self, widget):
        config.system_theme_tracking_enabled = widget.get_active()

    def on_customize_theme_button_clicked(self, widget):
        self.customize_theme()

    def on_theme_view_row_activated(self, treeview, path, view_column):
        self.customize_theme()

    def on_theme_view_cursor_changed(self, widget):
        theme = self.get_selected_theme()
        if theme:
            theme.apply()
            config.theme_filename = theme.filename
        self.update_theme_buttons()

    def get_sorted_themes(self):
        #return sorted(self.themes.values(), key=lambda x: x[0].name)
        is_system = [x for x in list(self.themes.values()) if x[0].is_system or x[1]]
        user = [x for x in list(self.themes.values()) if not (x[0].is_system or x[1])]
        return sorted(is_system, key=lambda x: x[0].name.lower()) + \
               sorted(user, key=lambda x: x[0].name.lower())

    def find_theme_index(self, theme):
        themes = self.get_sorted_themes()
        for i,tpl in enumerate(themes):
            if theme.basename == tpl[0].basename:
                return i
        return -1

    def customize_theme(self):
        theme = self.get_selected_theme()
        if theme:
            system_theme = self.themes[theme.basename][1]

            dialog = ThemeDialog(self, theme)
            modified_theme = dialog.run()

            if modified_theme == system_theme:
                # same as the system theme, so delete the user theme
                _logger.info("Deleting theme '%s'" % theme.filename)
                if os.path.exists(theme.filename):
                    os.remove(theme.filename)

            elif not modified_theme == theme:
                # save as user theme
                modified_theme.save_as(theme.basename, theme.name)
                config.theme_filename = modified_theme.filename
                _logger.info("Saved theme '%s'" % theme.filename)

        self.update_themeList()

    def on_system_theme_tracking_changed(self, x):
        self.system_theme_tracking_enabled_toggle.set_active( \
                                        config.system_theme_tracking_enabled)
        config.load_theme()
        self.on_theme_changed()

    def on_theme_changed(self, theme_filename = None):
        selected = self.get_selected_theme_filename()
        if selected != config.theme_filename:
            self.update_themeList()

    def update_themeList(self):
        self.themeList = Gtk.ListStore(str, str)
        self.theme_view.set_model(self.themeList)

        self.themes = Theme.load_merged_themes()

        theme_basename = \
               os.path.splitext(os.path.basename(config.theme_filename))[0]
        it_selection = None
        for theme,hidden_theme in self.get_sorted_themes():
            it = self.themeList.append((
                         format_list_item(theme.name, theme.is_system),
                         theme.filename))
            if theme.basename == theme_basename:
                sel = self.theme_view.get_selection()
                if sel:
                    sel.select_iter(it)
                it_selection = it

        # scroll to selection
        if it_selection:
            path = self.themeList.get_path(it_selection)
            self.theme_view.scroll_to_cell(path)

        self.update_theme_buttons()

    def update_theme_buttons(self):
        theme = self.get_selected_theme()

        if theme and (self.get_hidden_theme(theme) or theme.is_system):
            # Translators: reset button of Preferences->Theme.
            self.delete_theme_button.set_label(_("_Reset"))
        else:
            # Translators: delete button of Preferences->Theme. It used to be
            # stock item STOCK_DELETE until Gtk 3.10 deprecated those.
            self.delete_theme_button.set_label(_("_Delete"))

        self.delete_theme_button.set_sensitive(bool(theme) and not theme.is_system)
        self.customize_theme_button.set_sensitive(bool(theme))

    def get_hidden_theme(self, theme):
        if theme:
            return self.themes[theme.basename][1]
        return None

    def get_selected_theme(self):
        filename = self.get_selected_theme_filename()
        if filename:
            basename = os.path.splitext(os.path.basename(filename))[0]
            if basename in self.themes:
                return self.themes[basename][0]
        return None

    def get_selected_theme_filename(self):
        sel = self.theme_view.get_selection()
        if sel:
            it = sel.get_selected()[1]
            if it:
                return self.themeList.get_value(it, 1)
        return None


class PageAutoShow(DialogBuilder):
    """ Word Suggestions """

    """ Keyboard devices view columns """
    COL_ID      = 0
    COL_INCLUDE = 1
    COL_TEXT    = 2

    def __init__(self, settings, builder):
        DialogBuilder.__init__(self, builder)
        self._settings = settings

        # General page
        def _set(co, key, value):
            if value and \
               not config.check_gnome_accessibility(self._settings.window):
                value = False
            setattr(co, key, value)
            self._settings.update_window_widgets()  # will call _update_ui()

        self.bind_check("auto_show_toggle1",   # toggle on general page
                        config.auto_show, "enabled",
                        config_set_callback=_set)
        self.bind_check("auto_show_toggle2",   # same thing on auto-show page
                        config.auto_show, "enabled",
                        config_set_callback=_set)

        def _set(config_object, key, value):
            self.bind_combobox_config_set(config_object, key, value)
            self._settings.update_window_widgets()

        self.bind_combobox_id("reposition_method_floating_combobox",
                              config.auto_show, "reposition_method_floating",
                              config_set_callback=_set)

        self.bind_combobox_id("reposition_method_docked_combobox",
                              config.auto_show, "reposition_method_docked",
                              config_set_callback=_set)

        def _set(config_object, key, value):
            setattr(config_object, key, value)
            self._settings.update_window_widgets()

        self.bind_check("hide_on_key_press_toggle",
                        config.auto_show, "hide_on_key_press",
                        config_set_callback=_set)

        def _get(config_object, key):
            duration = getattr(config_object, key)
            return str(int(round(duration)))

        def _set(config_object, key, value):
            duration = float(value)
            setattr(config_object, key, duration)

        self.bind_combobox_id("hide_on_key_press_pause_combobox",
                              config.auto_show, "hide_on_key_press_pause",
                              _get, _set)

        # Convertible Devices page
        self.bind_check("tablet_mode_detection_enabled_toggle",
                        config.auto_show, "tablet_mode_detection_enabled")

        def _get(co, key):
            return str(getattr(co, key))

        def _set(co, key, value):
            try:
                value = int(value)
            except ValueError:
                value = 0
            setattr(co, key, value)

        self.bind_entry("tablet_mode_enter_key_entry",
                        config.auto_show, "tablet_mode_enter_key",
                        config_get_callback=_get,
                        config_set_callback=_set)
        self.bind_entry("tablet_mode_leave_key_entry",
                        config.auto_show, "tablet_mode_leave_key",
                        config_get_callback=_get,
                        config_set_callback=_set)

        # External Keyboards page
        def _set(co, key, value):
            setattr(co, key, value)
            self._settings.update_window_widgets()  # will call _update_ui()

        self.bind_check("keyboard_device_detection_enabled_toggle",
                        config.auto_show, "keyboard_device_detection_enabled",
                        config_set_callback=_set)

        self._udev_tracker = UDevTracker()
        self._udev_tracker.connect("keyboard-detection-changed",
                                   self._on_keyboard_device_detection_changed)

        self._update_keyboard_devices_view()

        config.auto_show.keyboard_device_detection_exceptions_notify_add(
            lambda x: self._update_keyboard_devices_view())

    def _update_keyboard_devices_view(self):
        view = self.wid("keyboard_detection_devices_view")
        if not view.get_model():
            model = Gtk.ListStore(str, bool, str)
            view.set_model(model)

            column_id = Gtk.TreeViewColumn()

            # Translators: header of a tree view column with toggles to ignore
            # keyboard devices (Preferences->Auto-show->External Keyboards).
            column_toggle = Gtk.TreeViewColumn(_("Ignore"))

            # Translators: header of a tree view column with device names
            # (Preferences->Auto-show->External Keyboards).
            column_text = Gtk.TreeViewColumn(_("Device"))

            view.append_column(column_id)
            view.append_column(column_toggle)
            view.append_column(column_text)

            cellrenderer_toggle = Gtk.CellRendererToggle()
            column_toggle.pack_start(cellrenderer_toggle, False)
            column_toggle.add_attribute(
                cellrenderer_toggle, "active", self.COL_INCLUDE)

            cellrenderer_text = Gtk.CellRendererText()
            column_text.pack_start(cellrenderer_text, True)
            column_text.add_attribute(cellrenderer_text, "text", self.COL_TEXT)
            cellrenderer_toggle.connect(
                "toggled", self._on_keyboard_device_toggled, model)

        model = view.get_model()
        selected_id = self.get_tree_view_selection(view, self.COL_ID)

        model.clear()

        devices = self._udev_tracker.get_keyboard_devices()
        for device in devices:
            ignore = device.id in \
                config.auto_show.keyboard_device_detection_exceptions
            t = (device.id, ignore, device.name)
            model.append(t)

        self.select_tree_view_row(view, self.COL_ID, selected_id)

    def _on_keyboard_device_detection_changed(self, detected):
        self._update_keyboard_devices_view()

    def _on_keyboard_device_toggled(self, widget, path, model):
        device_id = model[path][self.COL_ID]
        ignore = not model[path][self.COL_INCLUDE]

        # Update all rows with the same device id. There might still be
        # duplicate device entries.
        for row in model:
            if row[self.COL_ID] == device_id:
                row[self.COL_INCLUDE] = ignore

        self._ignore_keyboard_device(device_id, ignore)

    def _ignore_keyboard_device(self, device_id, ignore):
        exceptions = config.auto_show.keyboard_device_detection_exceptions[:]

        if ignore:
            if device_id not in exceptions:
                exceptions.append(device_id)
        else:
            try:
                exceptions.remove(device_id)
            except ValueError:
                pass

        config.auto_show.keyboard_device_detection_exceptions = exceptions

    def update_ui(self):
        # Two toggles that do the exact same thing: one on the general page,
        # the other on the auto-show page. Keep the one on the general page as
        # it's active state influences the visibility options there.
        # Otherwise it becomes even less clear why some of these options are
        # enabled/disabled.
        self.wid("auto_show_toggle1").set_active(config.auto_show.enabled)
        self.wid("auto_show_toggle2").set_active(config.auto_show.enabled)

        docked = config.is_docking_enabled()
        self.wid("reposition_method_floating_box").set_visible(not docked)
        self.wid("reposition_method_docked_box").set_visible(docked)

        self.wid("hide_on_key_press_toggle") \
            .set_sensitive(config.can_set_auto_hide())
        self.wid("hide_on_key_press_box") \
            .set_sensitive(config.is_auto_hide_enabled())
        auto_show_enabled = config.is_auto_show_enabled()
        self.wid("auto_show_general_box").set_sensitive(auto_show_enabled)
        self.wid("auto_show_convertibles_box").set_sensitive(auto_show_enabled)
        self.wid("auto_show_external_keyboards_box") \
            .set_sensitive(auto_show_enabled)

        self.wid("keyboard_device_detection_box") \
            .set_sensitive(config.is_keyboard_device_detection_enabled())


class PageWordSuggestions(DialogBuilder):
    """ Word Suggestions """

    def __init__(self, settings, builder):
        DialogBuilder.__init__(self, builder)
        self._settings = settings

        def _set_word_suggestions_enabled(co, key, value):
            if value and \
               not config.check_gnome_accessibility(self._settings.window):
                value = False
                self.wid("enable_word_suggestions_toggle") \
                        .set_active(value)
            setattr(co, key, value)

        self.wid("enable_word_suggestions_toggle") \
                .connect_after("toggled", lambda x: self._update_ui())
        self.bind_check("enable_word_suggestions_toggle",
                        config.word_suggestions, "enabled",
                        config_set_callback = _set_word_suggestions_enabled)

        self.wid("auto_learn_toggle") \
                .connect_after("toggled", lambda x: self._update_ui())
        self.bind_check("auto_learn_toggle",
                            config.wp, "auto_learn")

        self.bind_check("punctuation_assistance_toggle",
                            config.wp, "punctuation_assistance")
        self.bind_check("auto_capitalization_toggle",
                            config.typing_assistance, "auto_capitalization")
        self.bind_check("auto_correction_toggle",
                            config.typing_assistance, "auto_correction")
        self.bind_check("enable_spell_check_toggle",
                            config.word_suggestions, "spelling_suggestions_enabled")

        self.bind_check("language_button_toggle",
            config.word_suggestions, "wordlist_buttons",
            config_get_callback = lambda co, key: \
                    co.can_show_language_button(),
            config_set_callback = lambda co, key, value: \
                    co.show_wordlist_button(co.KEY_ID_LANGUAGE, value))

        self.bind_check("pause_learning_button_toggle",
            config.word_suggestions, "wordlist_buttons",
            config_get_callback = lambda co, key: \
                    co.can_show_pause_learning_button(),
            config_set_callback = lambda co, key, value: \
                    co.show_wordlist_button(co.KEY_ID_PAUSE_LEARNING, value))

        def _get(co, key):
            return co.can_show_more_predictions_button()

        def _set(co, key, value):
            co.show_wordlist_button(co.KEY_ID_NEXT_PREDICTIONS, value)
            co.show_wordlist_button(co.KEY_ID_PREVIOUS_PREDICTIONS, value)

        self.bind_check("more_predictions_button_toggle",
                        config.word_suggestions, "wordlist_buttons",
                        config_get_callback=_get,
                        config_set_callback=_set)

        self.bind_combobox_id("learning_behavior_paused_combobox",
                        config.word_suggestions, "learning_behavior_paused")

        #self.bind_check("show_context_line_toggle",
        #                config.word_suggestions, "show_context_line")
        self._init_spell_checker_backend_combo()

        self._update_ui()

    def _init_spell_checker_backend_combo(self):
        combo = self.wid("spell_check_backend_combobox")
        combo.set_active(config.typing_assistance.spell_check_backend)
        combo.connect("changed", self.on_spell_check_backend_changed)
        config.typing_assistance.spell_check_backend_notify_add(self._backend_notify)

    def on_spell_check_backend_changed(self, widget):
        config.typing_assistance.spell_check_backend = widget.get_active()

    def _backend_notify(self, mode):
        self.wid("spell_check_backend_combobox").set_active(mode)

    def _update_ui(self):
        self.wid("word_suggestions_general_box1") \
                .set_sensitive(config.are_word_suggestions_enabled())
        self.wid("pause_learning_button_toggle") \
                .set_sensitive(config.word_suggestions.auto_learn)


class DockingDialog(DialogBuilder):
    """ Dialog "Docking Settings" """

    def __init__(self):

        builder = LoadUI("settings_docking_dialog")

        DialogBuilder.__init__(self, builder)

        self.bind_check("docking_shrink_workarea_toggle",
                        config.window, "docking_shrink_workarea")
        self.bind_check("landscape_dock_expand_toggle",
                        config.window.landscape, "dock_expand")
        self.bind_check("portrait_dock_expand_toggle",
                        config.window.portrait, "dock_expand")
        self.bind_combobox_id("docking_edge_combobox",
                        config.window, "docking_edge")
        self.bind_combobox_id("docking_monitor_combobox",
                        config.window, "docking_monitor")

        self.update_ui()

    def run(self, parent):
        dialog = self.wid("dialog")
        dialog.set_transient_for(parent)
        dialog.run()
        dialog.destroy()

    def update_ui(self):
        pass


class ThemeDialog(DialogBuilder):
    """ Customize theme dialog """

    current_page = 0

    def __init__(self, settings, theme):

        self.original_theme = theme
        self.theme = copy.deepcopy(theme)

        builder = LoadUI("settings_theme_dialog")
        DialogBuilder.__init__(self, builder)

        self.dialog = builder.get_object("customize_theme_dialog")

        self.theme_notebook = builder.get_object("theme_notebook")

        self.key_style_combobox = builder.get_object("key_style_combobox")
        self.color_scheme_combobox = builder.get_object("color_scheme_combobox")
        self.color_grid = builder.get_object("color_grid")
        self.font_combobox = builder.get_object("font_combobox")
        self.font_attributes_view = builder.get_object("font_attributes_view")
        self.background_gradient_scale = builder.get_object(
                                               "background_gradient_scale")
        self.key_roundness_scale = builder.get_object(
                                               "key_roundness_scale")
        self.key_size_scale = builder.get_object(
                                               "key_size_scale")
        self.gradients_box = builder.get_object("gradients_box")
        self.key_fill_gradient_scale = builder.get_object(
                                               "key_fill_gradient_scale")
        self.key_stroke_gradient_scale = builder.get_object(
                                               "key_stroke_gradient_scale")
        self.key_gradient_direction_scale = builder.get_object(
                                               "key_gradient_direction_scale")
        self.key_shadow_strength_scale = builder.get_object(
                                               "key_shadow_strength_scale")
        self.key_shadow_size_scale = builder.get_object(
                                               "key_shadow_size_scale")
        self.revert_button = builder.get_object("revert_button")
        self.superkey_label_combobox = builder.get_object(
                                               "superkey_label_combobox")
        self.superkey_label_size_checkbutton = builder.get_object(
                                            "superkey_label_size_checkbutton")
        self.superkey_label_model = builder.get_object("superkey_label_model")

        self.color_states = [
            ("normal",  _("Normal"),  {}),
            ("hover",   _("Hover"),   {"hover": True}),
            ("pressed", _("Pressed"), {"pressed": True}),
            ("active",  _("Active"),  {"active": True}),
            ("locked",  _("Locked"),  {"active": True, "locked": True}),
            ("scanned", _("Scanned"), {"scanned": True}),
        ]
        self.color_buttons = {}
        self._color_scheme = None
        self._color_scheme_backups = {}
        self._created_color_scheme_filename = None
        self._custom_color_scheme_filename = None
        self._create_color_buttons()

        def _set(config_object, key, value):
            setattr(config_object, key, value)
            self.update_sensivity()

        self.bind_scale("key_stroke_width_scale",
                        config.theme_settings, "key_stroke_width",
                        None, _set)

        self.update_ui()

        self.dialog.set_transient_for(settings.window)
        self.theme_notebook.set_current_page(ThemeDialog.current_page)

        builder.connect_signals(self)

    def run(self):
        # do response processing ourselves to stop the
        # revert button from closing the dialog
        self.dialog.set_modal(True)
        self.dialog.show()
        Gtk.main()
        self.dialog.destroy()
        return self.theme

    def on_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.DELETE_EVENT:
            pass
        if response_id == \
            self.dialog.get_response_for_widget(self.revert_button):

            # revert changes and keep the dialog open
            self.theme = copy.deepcopy(self.original_theme)
            for color_scheme in self._color_scheme_backups.values():
                color_scheme.save()
            if self._created_color_scheme_filename and \
               os.path.exists(self._created_color_scheme_filename):
                os.remove(self._created_color_scheme_filename)
            self._color_scheme_backups = {}
            self._created_color_scheme_filename = None
            self._custom_color_scheme_filename = None
            self.theme.apply()
            self._reload_color_scheme(self.theme.get_color_scheme_filename())
            self.update_ui()
            return

        Gtk.main_quit()

    def update_ui(self):
        self.in_update = True

        self.update_key_styleList()
        self.update_color_schemeList()
        self._load_color_scheme()
        self._update_color_buttons()
        self.update_fontList()
        self.update_font_attributesList()
        self.background_gradient_scale.set_value(self.theme.background_gradient)
        self.key_roundness_scale.set_value(self.theme.roundrect_radius)
        self.key_size_scale.set_value(self.theme.key_size)
        self.key_fill_gradient_scale.set_value(self.theme.key_fill_gradient)
        self.key_stroke_gradient_scale. \
                set_value(self.theme.key_stroke_gradient)
        self.key_gradient_direction_scale. \
                set_value(self.theme.key_gradient_direction)
        self.key_shadow_strength_scale. \
                set_value(self.theme.key_shadow_strength)
        self.key_shadow_size_scale. \
                set_value(self.theme.key_shadow_size)
        self.update_superkey_labelList()
        self.superkey_label_size_checkbutton. \
                set_active(bool(self.theme.get_superkey_size_group()))

        self.update_sensivity()

        self.in_update = False

    def update_sensivity(self):
        self.revert_button.set_sensitive(not self.theme == self.original_theme)

        has_gradient = self.theme.key_style != "flat"
        self.gradients_box.set_sensitive(has_gradient)
        self.superkey_label_size_checkbutton.\
                      set_sensitive(bool(self.theme.get_superkey_label()))

    def update_key_styleList(self):
        self.key_style_list = Gtk.ListStore(str,str)
        self.key_style_combobox.set_model(self.key_style_list)
        cell = Gtk.CellRendererText()
        self.key_style_combobox.clear()
        self.key_style_combobox.pack_start(cell, True)
        self.key_style_combobox.add_attribute(cell, 'markup', 0)

        self.key_styles = [
                           # Key style with flat fill- and border colors
                           [_("Flat"), "flat"],
                           # Key style with simple gradients
                           [_("Gradient"), "gradient"],
                           # Key style for dish-like key caps
                           [_("Dish"), "dish"]
                           ]
        for name, id in self.key_styles:
            it = self.key_style_list.append((name, id))
            if id == self.theme.key_style:
                self.key_style_combobox.set_active_iter(it)

    def update_color_schemeList(self):
        self.color_scheme_list = Gtk.ListStore(str, str, bool)
        self.color_scheme_combobox.set_model(self.color_scheme_list)
        cell = Gtk.CellRendererText()
        self.color_scheme_combobox.clear()
        self.color_scheme_combobox.pack_start(cell, True)
        self.color_scheme_combobox.set_cell_data_func(
            cell, self._set_color_scheme_cell_data)

        self.color_schemes = ColorScheme.get_merged_color_schemes()
        color_scheme_filename = self.theme.get_color_scheme_filename()
        for header, color_schemes in self._get_color_scheme_groups(
                self.color_schemes.values()):
            self.color_scheme_list.append(("<b>{}</b>".format(header), "", True))
            for color_scheme in color_schemes:
                it = self.color_scheme_list.append((color_scheme.name,
                                                    color_scheme.filename,
                                                    False))
                if color_scheme.filename == color_scheme_filename:
                    self.color_scheme_combobox.set_active_iter(it)

    @staticmethod
    def _get_color_scheme_groups(color_schemes):
        built_in = []
        custom = []
        for color_scheme in sorted(color_schemes, key=lambda x: x.name):
            (built_in if color_scheme.is_system else custom).append(color_scheme)

        groups = []
        if built_in:
            groups.append((_("Built-in Color Schemes"), built_in))
        if custom:
            groups.append((_("Custom Color Schemes"), custom))
        return groups

    @staticmethod
    def _set_color_scheme_cell_data(column, cell, model, treeiter, data=None):
        cell.set_property("markup", model.get_value(treeiter, 0))
        cell.set_property("sensitive", not model.get_value(treeiter, 2))

    def _create_color_buttons(self):
        grid = self.color_grid
        headers = [_("Fill"), _("Border"), _("Label")]
        for column, text in enumerate(headers, 1):
            label = Gtk.Label(label=text)
            label.set_halign(Gtk.Align.CENTER)
            grid.attach(label, column, 0, 1, 1)

        for row, (state_id, state_name, state) in enumerate(
                self.color_states, 1):
            label = Gtk.Label(label=state_name)
            label.set_halign(Gtk.Align.START)
            grid.attach(label, 0, row, 1, 1)
            for column, element in enumerate(["fill", "stroke", "label"], 1):
                button = Gtk.ColorButton()
                button.set_use_alpha(True)
                button.set_title(_("{} {} color").format(state_name,
                                                          headers[column - 1]))
                button.connect("color-set", self.on_key_color_set,
                               element, state_id)
                grid.attach(button, column, row, 1, 1)
                self.color_buttons[(element, state_id)] = button

        row = len(self.color_states) + 1
        label = Gtk.Label(label=_("Background"))
        label.set_halign(Gtk.Align.START)
        grid.attach(label, 0, row, 1, 1)
        button = Gtk.ColorButton()
        button.set_use_alpha(True)
        button.set_title(_("Keyboard background color"))
        button.connect("color-set", self.on_background_color_set)
        grid.attach(button, 1, row, 1, 1)
        self.color_buttons[("background", "normal")] = button
        grid.show_all()

    def _load_color_scheme(self):
        filename = self.theme.get_color_scheme_filename()
        self._color_scheme = ColorScheme.load(filename) if filename else None
        self._custom_color_scheme_filename = filename \
            if self._is_theme_custom_color_scheme(self._color_scheme) else None

    def _get_color_state(self, state_id):
        state = {"prelight": False, "pressed": False, "active": False,
                 "locked": False, "scanned": False, "hover": False,
                 "insensitive": False}
        for name, _label, value in self.color_states:
            if name == state_id:
                state.update(value)
                break
        return state

    def _set_color_button_rgba(self, button, rgba):
        color = Gdk.RGBA()
        color.red, color.green, color.blue, color.alpha = rgba
        button.set_rgba(color)

    def _update_color_buttons(self):
        if not self._color_scheme:
            return

        for state_id, _label, _state in self.color_states:
            state = self._get_color_state(state_id)
            for element in ["fill", "stroke", "label"]:
                rgba = self._color_scheme.get_default_key_rgba(element, state)
                self._set_color_button_rgba(
                    self.color_buttons[(element, state_id)], rgba)

        rgba = self._color_scheme.get_layer_fill_rgba(0)
        self._set_color_button_rgba(
            self.color_buttons[("background", "normal")], rgba)

    def _get_custom_color_scheme(self):
        if self._is_theme_custom_color_scheme(self._color_scheme):
            filename = self._color_scheme.filename
            if filename not in self._color_scheme_backups and \
               filename != self._created_color_scheme_filename:
                self._color_scheme_backups[filename] = \
                    copy.deepcopy(self._color_scheme)
            self._custom_color_scheme_filename = filename
        else:
            self._color_scheme = copy.deepcopy(self._color_scheme)
            basename = self._build_custom_color_scheme_basename()
            filename = ColorScheme.build_user_filename(basename)
            self._created_color_scheme_filename = filename
            self._color_scheme.save_as(
                basename, _("{} Custom Colors").format(self.theme.name))
            self.theme.set_color_scheme_filename(self._color_scheme.filename)
            self._custom_color_scheme_filename = filename
            self.in_update = True
            self.update_color_schemeList()
            self.in_update = False
        return self._color_scheme

    def _is_theme_custom_color_scheme(self, color_scheme):
        if not color_scheme or color_scheme.is_system:
            return False
        basename = self.theme.basename + "-custom"
        return color_scheme.basename == basename or \
               color_scheme.basename.startswith(basename + "-")

    def _build_custom_color_scheme_basename(self):
        basename = self.theme.basename + "-custom"
        candidate = basename
        index = 2
        while os.path.exists(ColorScheme.build_user_filename(candidate)):
            candidate = "{}-{}".format(basename, index)
            index += 1
        return candidate

    def _apply_color_scheme(self):
        self._reload_color_scheme(self._color_scheme.filename)
        self.update_sensivity()

    def _reload_color_scheme(self, filename):
        color_schemes = ColorScheme.get_merged_color_schemes()
        reload_filename = next((scheme.filename
                                for scheme in color_schemes.values()
                                if scheme.filename != filename), None)
        if reload_filename:
            config.theme_settings.set_color_scheme_filename(reload_filename,
                                                            save=False)
        config.theme_settings.color_scheme_filename = filename

    def update_fontList(self):
        self.font_list = Gtk.ListStore(str,str)
        self.font_combobox.set_model(self.font_list)
        cell = Gtk.CellRendererText()
        self.font_combobox.clear()
        self.font_combobox.pack_start(cell, True)
        self.font_combobox.add_attribute(cell, 'markup', 0)
        self.font_combobox.set_row_separator_func(
                                    self.font_combobox_row_separator_func,
                                    None)

        # work around https://bugzilla.gnome.org/show_bug.cgi?id=654957
        # "SIGSEGV when trying to call Pango.Context.list_families twice"
        global font_families
        if not "font_families" in globals():
            widget = Gtk.DrawingArea()
            context = widget.create_pango_context()
            font_families = context.list_families()
            widget.destroy()

        families = [(font.get_name(), font.get_name()) \
                    for font in font_families]

        families.sort(key=lambda x: x[0])
        families = [(_("Default"), ""),
                    ("-", "-")] + families
        fd = Pango.FontDescription(self.theme.key_label_font)
        family = fd.get_family()
        for f in families:
            it = self.font_list.append(f)
            if  f[1] == family or \
               (f[1] == "" and not family):
                self.font_combobox.set_active_iter(it)

    def font_combobox_row_separator_func(self, model, iter, data):
        return unicode_str(model.get_value(iter, 0)) == "-"

    def update_font_attributesList(self):
        treeview = self.font_attributes_view

        if not treeview.get_columns():
            liststore = Gtk.ListStore(bool, str, str)
            self.font_attributes_list = liststore
            treeview.set_model(liststore)

            column_toggle = Gtk.TreeViewColumn("Toggle")
            column_text = Gtk.TreeViewColumn("Text")
            treeview.append_column(column_toggle)
            treeview.append_column(column_text)

            cellrenderer_toggle = Gtk.CellRendererToggle()
            column_toggle.pack_start(cellrenderer_toggle, False)
            column_toggle.add_attribute(cellrenderer_toggle, "active", 0)

            cellrenderer_text = Gtk.CellRendererText()
            column_text.pack_start(cellrenderer_text, True)
            column_text.add_attribute(cellrenderer_text, "text", 1)
            cellrenderer_toggle.connect("toggled",
                             self.on_font_attributesList_toggle, liststore)

        liststore = treeview.get_model()
        liststore.clear()

        fd = Pango.FontDescription(self.theme.key_label_font)
        items = [[fd.get_weight() == Pango.Weight.BOLD,
                  _("Bold"), "bold"],
                 [fd.get_style() == Pango.Style.ITALIC,
                  _("Italic"), "italic"],
                 [fd.get_stretch() == Pango.Stretch.CONDENSED,
                  _("Condensed"), "condensed"],
                ]
        for checked, name, id in items:
            it = liststore.append((checked, name, id))
            if id == "":
                treeview.set_active_iter(it)

    def update_superkey_labelList(self):
        # block premature signals when calling model.clear()
        self.superkey_label_combobox.set_model(None)

        self.superkey_label_model.clear()
        self.superkey_labels = [["",      _("Default")],
                                [_(""), _("Ubuntu Logo")]
                               ]

        for label, descr in self.superkey_labels:
            self.superkey_label_model.append((label, descr))

        label = self.theme.get_superkey_label()
        self.superkey_label_combobox.get_child().set_text(label \
                                                          if label else "")

        self.superkey_label_combobox.set_model(self.superkey_label_model)

    def on_background_gradient_value_changed(self, widget):
        value = float(widget.get_value())
        config.theme_settings.background_gradient = value
        self.theme.background_gradient = value
        self.update_sensivity()

    def on_key_style_combobox_changed(self, widget):
        value = self.key_style_list.get_value( \
                            self.key_style_combobox.get_active_iter(),1)
        self.theme.key_style = value
        config.theme_settings.key_style = value
        self.update_sensivity()

    def on_key_roundness_value_changed(self, widget):
        radius = int(widget.get_value())
        config.theme_settings.roundrect_radius = radius
        self.theme.roundrect_radius = radius
        self.update_sensivity()

    def on_key_size_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_size = value
        self.theme.key_size = value
        self.update_sensivity()

    def on_color_scheme_combobox_changed(self, widget):
        if self.in_update:
            return
        treeiter = self.color_scheme_combobox.get_active_iter()
        if self.color_scheme_list.get_value(treeiter, 2):
            self.in_update = True
            self.update_color_schemeList()
            self.in_update = False
            return
        filename = self.color_scheme_list.get_value(treeiter, 1)
        self.theme.set_color_scheme_filename(filename)
        config.theme_settings.color_scheme_filename = filename
        self._load_color_scheme()
        self._update_color_buttons()
        self.update_sensivity()

    def on_key_color_set(self, button, element, state_id):
        if not self._color_scheme:
            return
        color = button.get_rgba()
        rgba = [color.red, color.green, color.blue, color.alpha]
        scheme = self._get_custom_color_scheme()
        state = next(value for name, _label, value in self.color_states
                     if name == state_id)
        scheme.set_default_key_rgba(element, state, rgba)
        scheme.save()
        self._apply_color_scheme()

    def on_background_color_set(self, button):
        if not self._color_scheme:
            return
        color = button.get_rgba()
        rgba = [color.red, color.green, color.blue, color.alpha]
        scheme = self._get_custom_color_scheme()
        scheme.set_layer_fill_rgba(0, rgba)
        scheme.save()
        self._apply_color_scheme()

    def on_key_fill_gradient_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_fill_gradient = value
        self.theme.key_fill_gradient = value
        self.update_sensivity()

    def on_key_stroke_gradient_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_stroke_gradient = value
        self.theme.key_stroke_gradient = value
        self.update_sensivity()

    def on_key_gradient_direction_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_gradient_direction = value
        self.theme.key_gradient_direction = value
        self.update_sensivity()

    def on_key_shadow_strength_value_changed(self, widget):
        value = float(widget.get_value())
        config.theme_settings.key_shadow_strength = value
        self.theme.key_shadow_strength = value
        self.update_sensivity()

    def on_key_shadow_size_value_changed(self, widget):
        value = float(widget.get_value())
        config.theme_settings.key_shadow_size = value
        self.theme.key_shadow_size = value
        self.update_sensivity()

    def on_font_combobox_changed(self, widget):
        if not self.in_update:
            self.store_key_label_font()
            self.update_sensivity()

    def on_font_attributesList_toggle(self, widget, path, model):
        model[path][0] = not model[path][0]
        self.store_key_label_font()
        self.update_sensivity()

    def store_key_label_font(self):
        font = self.font_list.get_value(self.font_combobox.get_active_iter(),1)
        for row in self.font_attributes_list:
            if row[0]:
                font += " " + row[2]

        self.theme.key_label_font = font
        config.theme_settings.key_label_font = font

    def on_superkey_label_combobox_changed(self, widget):
        self.store_superkey_label_override()
        self.update_sensivity()

    def on_superkey_label_size_checkbutton_toggled(self, widget):
        self.store_superkey_label_override()
        self.update_sensivity()

    def store_superkey_label_override(self):
        label = self.superkey_label_combobox.get_child().get_text()
        if sys.version_info.major == 2:
            label = label.decode("utf8")
        if not label:
            label = None   # removes the override
        checked = self.superkey_label_size_checkbutton.get_active()
        size_group = config.SUPERKEY_SIZE_GROUP if checked else ""
        self.theme.set_superkey_label(label, size_group)
        config.theme_settings.key_label_overrides = \
                                          dict(self.theme.key_label_overrides)

    def on_theme_notebook_switch_page(self, widget, gpage, page_num):
        ThemeDialog.current_page = page_num


class ScannerDialog(DialogBuilder):
    """ Scanner settings dialog """

    """ Input device columns """
    COL_ICON_NAME   = 0
    COL_DEVICE_NAME = 1
    COL_DEVICE      = 2

    """ Device mapping columns """
    COL_NAME    = 0
    COL_ACTION  = 1
    COL_BUTTON  = 2
    COL_KEY     = 3
    COL_VISIBLE = 4
    COL_WEIGHT  = 5

    """ UI strings for scan actions """
    action_names = { ScanMode.ACTION_STEP     : _("Step"),
                     ScanMode.ACTION_LEFT     : _("Left"),
                     ScanMode.ACTION_RIGHT    : _("Right"),
                     ScanMode.ACTION_UP       : _("Up"),
                     ScanMode.ACTION_DOWN     : _("Down"),
                     ScanMode.ACTION_ACTIVATE : _("Activate") }

    """ List of actions a profile supports """
    supported_actions = [ [ScanMode.ACTION_STEP],
                          [ScanMode.ACTION_STEP],
                          [ScanMode.ACTION_STEP,
                           ScanMode.ACTION_ACTIVATE],
                          [ScanMode.ACTION_LEFT,
                           ScanMode.ACTION_RIGHT,
                           ScanMode.ACTION_UP,
                           ScanMode.ACTION_DOWN,
                           ScanMode.ACTION_ACTIVATE] ]

    def __init__(self):

        builder = LoadUI("settings_scanner_dialog")
        DialogBuilder.__init__(self, builder)

        self.device_manager = XIDeviceManager()
        self.device_manager.connect("device-event", self._on_device_event)
        self.pointer_selected = None
        self.mapping_renderer = None

        # order of execution is important
        self.init_input_devices()
        self.init_scan_modes()
        self.init_device_mapping()

        scanner = config.scanner
        self.bind_spin("cycles", scanner, "cycles")
        self.bind_spin("cycles_overscan", scanner, "cycles")
        self.bind_spin("cycles_stepscan", scanner, "cycles")
        self.bind_spin("step_interval", scanner, "interval")
        self.bind_spin("backtrack_interval", scanner, "interval")
        self.bind_spin("forward_interval", scanner, "interval_fast")
        self.bind_spin("backtrack_steps", scanner, "backtrack")
        self.bind_check("user_scan", scanner, "user_scan")
        self.bind_check("alternate", scanner, "alternate")
        self.bind_check("device_detach", scanner, "device_detach")

    def __del__(self):
        _logger.debug("ScannerDialog.__del__()")

    def run(self, parent):
        dialog = self.wid("dialog")
        dialog.set_transient_for(parent)
        dialog.run()
        dialog.destroy()

        config.scanner.disconnect_notifications()
        self.device_manager = None

    def init_scan_modes(self):
        combo = self.wid("scan_mode_combo")
        combo.set_active(config.scanner.mode)
        combo.connect("changed", self.on_scan_mode_changed)
        config.scanner.mode_notify_add(self._scan_mode_notify)
        self.wid("scan_mode_notebook").set_current_page(config.scanner.mode)

    def on_scan_mode_changed(self, widget):
        config.scanner.mode = widget.get_active()

    def _scan_mode_notify(self, mode):
        self.wid("scan_mode_combo").set_active(mode)
        self.wid("scan_mode_notebook").set_current_page(mode)
        self.update_device_mapping()

    def init_input_devices(self):
        combo = self.wid("input_device_combo")
        combo.set_model(Gtk.ListStore(str, str, GObject.TYPE_PYOBJECT))
        combo.add_attribute(self.wid("input_device_icon_renderer"),
                            "icon-name", self.COL_ICON_NAME)
        combo.add_attribute(self.wid("input_device_text_renderer"),
                            "text", self.COL_DEVICE_NAME)

        self.update_input_devices()

        combo.connect("changed", self.on_input_device_changed)
        config.scanner.device_name_notify_add(self._device_name_notify)

    def update_input_devices(self):
        devices = self.list_devices()
        model = self.wid("input_device_combo").get_model()
        model.clear()

        model.append(["input-mouse", ScanDevice.DEFAULT_NAME, None])

        for dev in devices:
            if dev.is_pointer():
                model.append(["input-mouse", dev.name, dev])

        for dev in devices:
            if not dev.is_pointer():
                model.append(["input-keyboard", dev.name, dev])

        self.select_current_device(config.scanner.device_name)

    def select_current_device(self, name):
        combo = self.wid("input_device_combo")
        model = combo.get_model()
        it = model.get_iter_first()
        if it is None:
            return

        if name == ScanDevice.DEFAULT_NAME:
            self.pointer_selected = True
            self.wid("device_detach").set_sensitive(False)
            combo.set_active_iter(it)
        else:
            while it:
                device = model.get_value(it, self.COL_DEVICE)
                if device and name == device.get_config_string():
                    self.pointer_selected = device.is_pointer()
                    self.wid("device_detach").set_sensitive(True)
                    combo.set_active_iter(it)
                    break
                it = model.iter_next(it)

        if self.mapping_renderer:
            self.mapping_renderer.set_property("pointer-mode",
                                               self.pointer_selected)

    def on_input_device_changed(self, combo):
        model = combo.get_model()
        it = combo.get_active_iter()
        if it is None:
            return

        config.scanner.device_detach = False
        device = model.get_value(it, self.COL_DEVICE)

        if device:
            config.scanner.device_name = device.get_config_string()
            self.wid("device_detach").set_sensitive(True)
            self.pointer_selected = device.is_pointer()
        else:
            config.scanner.device_name = ScanDevice.DEFAULT_NAME
            self.wid("device_detach").set_sensitive(False)
            self.pointer_selected = True

        if self.mapping_renderer:
            self.mapping_renderer.set_property("pointer-mode",
                                               self.pointer_selected)

    def _device_name_notify(self, name):
        self.select_current_device(name)
        self.update_device_mapping()

    def init_device_mapping(self):
        self.update_device_mapping()

        self.mapping_renderer = CellRendererMapping()
        self.mapping_renderer.set_property("pointer-mode", self.pointer_selected)
        self.mapping_renderer.connect("mapping-edited", self.on_mapping_edited)
        self.mapping_renderer.connect("mapping-cleared", self.on_mapping_cleared)

        column = self.wid("column_mapping")
        column.pack_start(self.mapping_renderer, False)
        column.add_attribute(self.mapping_renderer, "button", self.COL_BUTTON)
        column.add_attribute(self.mapping_renderer, "key", self.COL_KEY)
        column.add_attribute(self.mapping_renderer, "visible", self.COL_VISIBLE)

    def update_device_mapping(self):
        view = self.wid("device_mapping")
        model = view.get_model()
        model.clear()

        parent_iter = model.append(None)
        model.set(parent_iter,
                  self.COL_NAME, _("Action:"),
                  self.COL_WEIGHT, Pango.Weight.BOLD)

        for action in self.supported_actions[config.scanner.mode]:
            child_iter = model.append(parent_iter)
            model.set(child_iter,
                      self.COL_NAME, self.action_names[action],
                      self.COL_ACTION, action,
                      self.COL_VISIBLE, True,
                      self.COL_WEIGHT, Pango.Weight.NORMAL)

            if self.pointer_selected:
                button = self.get_value_for_action \
                    (action, config.scanner.device_button_map)
                if button:
                    model.set(child_iter, self.COL_BUTTON, button)
            else:
                key = self.get_value_for_action \
                    (action, config.scanner.device_key_map)
                if key:
                    model.set(child_iter, self.COL_KEY, key)

        view.expand_all()

    def on_mapping_edited(self, cell, path, value, pointer_mode):
        model = self.wid("device_mapping_model")
        it = model.get_iter_from_string(path)
        if it is None:
            return

        if pointer_mode:
            col = self.COL_BUTTON
            dev_map = config.scanner.device_button_map.copy()
        else:
            col = self.COL_KEY
            dev_map = config.scanner.device_key_map.copy()

        dup_it = model.get_iter_from_string("0:0")
        dup_val = None
        while dup_it:
            if value == model.get_value(dup_it, col):
                dup_val = model.get_value(dup_it, col)
                model.set(dup_it, col, 0)
                break
            dup_it = model.iter_next(dup_it)

        model.set(it, col, value)

        if dup_val in dev_map:
            del dev_map[dup_val]

        action = model.get_value(it, self.COL_ACTION)
        dev_map[value] = action

        for k, v in dev_map.items():
            if k != value and v == action:
                del dev_map[k]
                break

        if pointer_mode:
            config.scanner.device_button_map = dev_map
        else:
            config.scanner.device_key_map = dev_map

    def on_mapping_cleared(self, cell, path, pointer_mode):
        model = self.wid("device_mapping_model")
        it = model.get_iter_from_string(path)
        if it is None:
            return

        if pointer_mode:
            old_value = model.get_value(it, self.COL_BUTTON)
            model.set(it, self.COL_BUTTON, 0)
            if old_value in config.scanner.device_button_map:
                copy = config.scanner.device_button_map.copy()
                del copy[old_value]
                config.scanner.device_button_map = copy
        else:
            old_value = model.get_value(it, self.COL_KEY)
            model.set(it, self.COL_KEY, 0)
            if old_value in config.scanner.device_key_map:
                copy = config.scanner.device_key_map.copy()
                del copy[old_value]
                config.scanner.device_key_map = copy

    def list_devices(self):
        return [d for d in self.device_manager.get_devices() \
                if ScanDevice.is_useable(d) ]

    def _on_device_event(self, event):
        if event.xi_type in [XIEventType.DeviceAdded,
                             XIEventType.DeviceRemoved]:
            self.update_input_devices()

    def get_value_for_action(self, action, dev_map):
        for k, v in dev_map.items():
            if v == action:
                return k


MAX_GINT32 = (1 << 31) - 1

class CellRendererMapping(Gtk.CellRendererText):
    """
    Custom cell renderer that displays device buttons as labels.
    """

    __gproperties__ = { str('button')      : (GObject.TYPE_INT,
                                             '', '', 0, MAX_GINT32, 0,
                                             GObject.PARAM_READWRITE),
                        str('key')         : (GObject.TYPE_INT,
                                             '', '', 0, MAX_GINT32, 0,
                                             GObject.PARAM_READWRITE),
                        str('pointer-mode') : (bool, '', '', True,
                                             GObject.PARAM_READWRITE) }

    __gsignals__ = { str('mapping-edited') : (GObject.SignalFlags.RUN_LAST,
                                             None, (str, int, bool)),
                     str('mapping-cleared'): (GObject.SignalFlags.RUN_LAST,
                                             None, (str, bool)) }

    def __init__(self):
        super(CellRendererMapping, self).__init__(editable=True)

        self.key = 0
        self.button = 0
        self.pointer_mode = True

        self._edit_widget = None
        self._grab_widget = None
        self._grab_pointer = None
        self._grab_keyboard = None
        self._path = None
        self._bp_id = 0
        self._kp_id = 0
        self._se_id = 0
        self._sizing_label = None

        self._update_text_props()

    def do_get_property(self, prop):
        if prop.name == 'button':
            return self.button
        elif prop.name == 'key':
            return self.key
        elif prop.name == 'pointer-mode':
            return self.pointer_mode

    def do_set_property(self, prop, value):
        if prop.name == 'button':
            self.button = value
        elif prop.name == 'key':
            self.key = value
        elif prop.name == 'pointer-mode':
            self.pointer_mode = value

        self._update_text_props()

    def _update_text_props(self):

        if (self.pointer_mode and self.button == 0) or \
           (not self.pointer_mode and self.key == 0):
            self.set_property("style", Pango.Style.ITALIC)
            self.set_property("foreground-rgba", Gdk.RGBA(0.6, 0.6, 0.6, 1.0))
            text = _("Disabled")
        else:
            self.set_property("style", Pango.Style.NORMAL)
            self.set_property("foreground-set", False)

            if self.pointer_mode:
                text = "{} {!s}".format(_("Button"), self.button)
            else:
                text = Gdk.keyval_name(self.key)

        self.set_property("text", text)

    def _on_edit_widget_unrealize(self, widget):
        Gtk.device_grab_remove(self._grab_widget, self._grab_pointer)

        time = Gtk.get_current_event_time()
        self._grab_pointer.ungrab(time)
        self._grab_keyboard.ungrab(time)

    def _editing_done(self):
        self._grab_widget.handler_disconnect(self._bp_id)
        self._grab_widget.handler_disconnect(self._kp_id)
        self._grab_widget.handler_disconnect(self._se_id)
        self._edit_widget.editing_done()
        self._edit_widget.remove_widget()

    def _on_button_press(self, widget, event):
        self._editing_done()

        if self.pointer_mode:
            self.emit("mapping-edited",
                      self._path, event.button, self.pointer_mode)
        return True

    def _on_key_press(self, widget, event):
        self._editing_done()

        value = Gdk.keyval_to_lower(event.keyval)

        if value == Gdk.KEY_BackSpace:
            self.emit("mapping-cleared", self._path, self.pointer_mode)
        elif value == Gdk.KEY_Escape:
            pass
        else:
            if not self.pointer_mode:
                self.emit("mapping-edited",
                          self._path, value, self.pointer_mode)
        return True

    def _on_scroll_event(self, widget, event):
        self._editing_done()

        if self.pointer_mode:
            # mouse buttons 4 - 7 are delivered as scroll-events
            button = 4 + event.direction
            self.emit("mapping-edited",
                      self._path, button, self.pointer_mode)
        return True

    def do_get_preferred_width(self, widget):
        if self._sizing_label is None:
            self._sizing_label = Gtk.Label(label=_("Press a button..."))

        return self._sizing_label.get_preferred_width()

    def do_start_editing(self, event, widget, path, bg_area, cell_area, state):
        if not event: # else SEGFAULT when pressing a keyboard key twice
            return

        time = event.get_time()
        device = event.get_device()
        if device.get_source() == Gdk.InputSource.KEYBOARD:
            keyboard = device
            pointer = device.get_associated_device()
        else:
            pointer = device
            keyboard = device.get_associated_device()

        if keyboard.grab(widget.get_window(),
                         Gdk.GrabOwnership.WINDOW, False,
                         Gdk.EventMask.KEY_PRESS_MASK,
                         None, time) != Gdk.GrabStatus.SUCCESS:
            return

        if pointer.grab(widget.get_window(),
                        Gdk.GrabOwnership.WINDOW, False,
                        Gdk.EventMask.BUTTON_PRESS_MASK,
                        None, time) != Gdk.GrabStatus.SUCCESS:
            keyboard.ungrab(time)
            return

        Gtk.device_grab_add(widget, pointer, True)

        self._path = path
        self._grab_pointer = pointer
        self._grab_keyboard = keyboard
        self._grab_widget = widget
        self._bp_id = widget.connect("button-press-event", self._on_button_press)
        self._kp_id = widget.connect("key-press-event", self._on_key_press)
        self._se_id = widget.connect("scroll-event", self._on_scroll_event)

        style = widget.get_style_context()
        bg = style.get_background_color(Gtk.StateFlags.SELECTED)
        fg = style.get_color(Gtk.StateFlags.SELECTED)

        if self.pointer_mode:
            text = _("Press a button...")
        else:
            text = _("Press a key...")

        label = Gtk.Label(label=text,
                          halign=Gtk.Align.START,
                          valign=Gtk.Align.CENTER)
        label.override_color(Gtk.StateFlags.NORMAL, fg)

        self._edit_widget = EditableBox(label)
        self._edit_widget.override_background_color(Gtk.StateFlags.NORMAL, bg)
        self._edit_widget.connect("unrealize", self._on_edit_widget_unrealize)
        self._edit_widget.show_all()

        return self._edit_widget


class EditableBox(Gtk.EventBox, Gtk.CellEditable):
    """
    Container that implements the Gtk.CellEditable interface.
    """

    __gproperties__ = { str('editing-canceled'): (bool, '', '', False,
                                                  GObject.PARAM_READWRITE) }

    def __init__(self, child=None):
        super(EditableBox, self).__init__()

        self.editing_canceled = False

        if child:
            self.add(child)

    def do_get_property(self, prop):
        if prop.name == 'editing-canceled':
            return self.editing_canceled

    def do_set_property(self, prop, value):
        if prop.name == 'editing-canceled':
            self.editing_canceled = value

    def do_start_editing(self, event):
        pass


if __name__ == '__main__':
    s = Settings(True)
