# -*- coding: utf-8 -*-

# Copyright © 2010 Chris Jones <tortoise@tortuga>
# Copyright © 2010 Francesco Fumanti <francesco.fumanti@gmx.net>
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

from __future__ import division, print_function, unicode_literals

import subprocess

try:
    import dbus
except ImportError:
    pass

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GObject, Gtk

from Onboard.definitions import StatusIconProviderEnum
from Onboard.utils import unicode_str, run_script

import logging
_logger = logging.getLogger("Indicator")

from Onboard.Config import Config
config = Config()


class ContextMenu(GObject.GObject):
    __gsignals__ = {
        str('quit-onboard') : (GObject.SignalFlags.RUN_LAST,
                               GObject.TYPE_NONE, ())
    }

    def __init__(self, keyboard=None):
        GObject.GObject.__init__(self)

        self._keyboard = keyboard

        self._show_label = _("_Show Onboard")
        self._hide_label = _("_Hide Onboard")

        self._menu = self.create_menu()

    def set_keyboard(self, keyboard):
        self._keyboard = keyboard

    def get_gtk_menu(self):
        return self._menu

    def create_menu(self):
        menu = Gtk.Menu()

        # This updates the menu in gnome-shell and gnome-classic,
        # but not in unity or unity2D.
        menu.connect_object("show", ContextMenu.update_items, self)

        show_item = Gtk.MenuItem.new_with_label(self._show_label)
        show_item.set_use_underline(True)
        show_item.connect_object("activate",
                                 ContextMenu.on_show_keyboard_toggle, self)
        menu.append(show_item)

        if not config.lockdown.disable_preferences:
            # Translators: label of a menu item. It used to be stock item
            # STOCK_PREFERENCES until Gtk 3.10 deprecated those.
            settings_item = Gtk.MenuItem.new_with_label(_("_Preferences"))
            settings_item.set_use_underline(True)
            settings_item.connect("activate", self._on_settings_clicked)
            menu.append(settings_item)

        item = Gtk.SeparatorMenuItem.new()
        menu.append(item)

        help_item = Gtk.MenuItem.new_with_label(_("_Help"))
        help_item.set_use_underline(True)
        help_item.connect("activate", self._on_help)
        menu.append(help_item)

        if not config.lockdown.disable_quit:
            item = Gtk.SeparatorMenuItem.new()
            menu.append(item)

            # Translators: label of a menu item. It used to be stock item
            # STOCK_QUIT until Gtk 3.10 deprecated those.
            quit_item = Gtk.MenuItem.new_with_label(_("_Quit"))
            quit_item.set_use_underline(True)
            quit_item.connect("activate", self._on_quit)
            menu.append(quit_item)

        menu.show_all()

        return menu

    def popup(self, button, activate_time,
              data=None, menu_position_func=None):
        """
        Callback called when status icon right clicked.  Produces menu.
        """
        self._menu.popup(None, None,
                         menu_position_func, data,
                         button, activate_time)

    def update_items(self):
        if self._keyboard:
            if self._keyboard.is_visible():
                self._menu.get_children()[0].set_label(self._hide_label)
            else:
                self._menu.get_children()[0].set_label(self._show_label)

    def _on_settings_clicked(self, widget):
        run_script("sokSettings")

    def on_show_keyboard_toggle(self):
        # The menu item might have been activated by keyboard hotkeys. Delay
        # request until keys have been released.
        self._keyboard.request_visibility_toggle()

    def _on_help(self, data=None):
        subprocess.Popen(["/usr/bin/yelp", "help:onboard"])

    def _on_quit(self, data=None):
        _logger.debug("Entered _on_quit")
        self.emit("quit-onboard")


class Indicator():

    "Keyboard window managed by this indicator"
    _keyboard = None

    "Menu attached to backend"
    _menu = None

    def __new__(cls, *args, **kwargs):
        """
        Singleton magic.
        """
        if not hasattr(cls, "self"):
            cls.self = object.__new__(cls, *args, **kwargs)
            cls.self.init()
        return cls.self

    def __init__(self):
        """
        This constructor is still called multiple times.
        Do nothing here and use the singleton constructor "init()" instead.
        """
        pass

    def init(self):

        self._menu = ContextMenu()

        sip = config.status_icon_provider

        if sip == StatusIconProviderEnum.auto:
            # auto-detection
            sip = config.get_preferred_statusicon_provider()

        if sip == StatusIconProviderEnum.GtkStatusIcon:
            backends = [BackendGtkStatusIcon]
        elif sip == StatusIconProviderEnum.AppIndicator:
            backends = [BackendAppIndicator, BackendGtkStatusIcon]
        elif sip is None:
            backends = []
        else:  # sip == StatusIconProviderEnum.auto
            backends = [BackendAppIndicator,
                        BackendGtkStatusIcon]

        self._backend = None
        for backend in backends:
            try:
                self._backend = backend(self._menu)
                break
            except RuntimeError as ex:
                _logger.info("Status icon provider: '{}' unavailable: {}"
                             .format(backend.__name__, unicode_str(ex)))

        _logger.info("Status icon provider: '{}' selected"
                     .format(self._backend and
                             type(self._backend).__name__))

        if self._backend is not None:
            self._backend.set_visible(False)

    def cleanup(self):
        if self._backend:
            self._backend.cleanup()
        self.set_keyboard(None)

    def set_keyboard(self, keyboard):
        self._keyboard = keyboard
        self._menu.set_keyboard(keyboard)

    def get_menu(self):
        return self._menu

    def update_menu_items(self):
        self._menu.update_items()

    def set_visible(self, visible):
        if self._backend is not None:
            self._backend.set_visible(visible)


class BackendBase():

    _menu = None

    category = "ApplicationStatus"
    icon_desc = _("Onboard on-screen keyboard")
    icon_name = "onboard"
    id = "Onboard"
    title = _("Onboard on-screen keyboard")

    def __init__(self, menu):
        self._menu = menu

    def cleanup(self):
        pass

    def get_menu(self):
        return self._menu


class BackendGtkStatusIcon(BackendBase):

    _status_icon = None

    def __init__(self, menu):
        BackendBase.__init__(self, menu)

        self._status_icon = Gtk.StatusIcon(icon_name=self.icon_name)
        self._status_icon.connect("activate",
                                  lambda x:
                                  self._menu.on_show_keyboard_toggle())
        self._status_icon.connect("popup-menu",
                                  self._on_status_icon_popup_menu)

    def set_visible(self, visible):
        self._status_icon.set_visible(visible)

    def _on_status_icon_popup_menu(self, status_icon, button, activate_time):
        """
        Callback called when status icon right clicked.  Produces menu.
        """
        self._menu.popup(button, activate_time,
                         status_icon, self._menu_position_func)

    def _menu_position_func(self, menu, *args):
        gtk_menu = self._menu.get_gtk_menu()

        # Work around gi annotation bug in gtk-3.0:
        # gtk_status_icon_position_menu() doesn't mark 'push_in' as inout
        # which is required for any (*GtkMenuPositionFunc)
        # Precise: args = (status_icon,)
        if len(args) == 1:    # in Precise
            status_icon, = args
            return Gtk.StatusIcon.position_menu(gtk_menu, status_icon)
        elif len(args) == 2:  # in <=Oneiric?
            push_in, status_icon = args
            return Gtk.StatusIcon.position_menu(gtk_menu, status_icon)
        elif len(args) == 3:  # in <=Xenial?
            x, y, status_icon = args
            return Gtk.StatusIcon.position_menu(gtk_menu, x, y, status_icon)


class BackendAppIndicator(BackendBase):

    _indicator = None

    STATUSNOTIFIER_OBJECT = "/org/ayatana/NotificationItem/Onboard"
    STATUSNOTIFIER_IFACE = "org.kde.StatusNotifierItem"
    ACTIVATE_METHOD = "Activate"

    def __init__(self, menu):
        BackendBase.__init__(self, menu)

        try:
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except ImportError as ex:
            raise RuntimeError(ex)

        self._indicator = AppIndicator.Indicator.new(
            self.id,
            self.icon_name,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self._indicator.set_icon_full(self.icon_name,
                                      self.icon_desc)

        self._indicator.set_menu(menu._menu)
        self._indicator.set_secondary_activate_target(
            menu._menu.get_children()[0])

        if "dbus" in globals():
            # Watch left-click Activate() calls on desktops that send them
            # (KDE Plasma). There is still "No such method 'Activate'" in
            # AppIndicator.
            try:
                self._bus = dbus.SessionBus()
            except dbus.exceptions.DBusException as ex:
                _logger.warning("D-Bus session bus unavailable, "
                                "no left-click Activate() for AppIndicator: " +
                                unicode_str(ex))
            else:
                try:
                    self._bus.add_match_string(
                        "type='method_call',"
                        "eavesdrop=true,"
                        "path='{}',"
                        "interface='{}',"
                        "member='{}'"
                        .format(self.STATUSNOTIFIER_OBJECT,
                                self.STATUSNOTIFIER_IFACE,
                                self.ACTIVATE_METHOD))
                    self._bus.add_message_filter(self._on_activate_method)
                except dbus.exceptions.DBusException as ex:
                    _logger.warning("Failed to setup D-Bus match rule, "
                                    "no left-click Activate() for AppIndicator: " +
                                    unicode_str(ex))

    def cleanup(self):
        pass

    def _on_activate_method(self, bus, message):
        if message.get_path() == self.STATUSNOTIFIER_OBJECT and \
           message.get_member() == self.ACTIVATE_METHOD:
            self._menu.on_show_keyboard_toggle()
        return dbus.connection.HANDLER_RESULT_NOT_YET_HANDLED

    def set_visible(self, visible):
        self._set_indicator_active(visible)

    def _set_indicator_active(self, active):
        try:
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except ImportError:
            pass
        else:
            if active:
                self._indicator.set_status(
                    AppIndicator.IndicatorStatus.ACTIVE)
            else:
                self._indicator.set_status(
                    AppIndicator.IndicatorStatus.PASSIVE)


