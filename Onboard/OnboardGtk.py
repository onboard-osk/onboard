# -*- coding: UTF-8 -*-

# Copyright © 2007 Martin Böhme <martin.bohm@kubuntu.org>
# Copyright © 2007-2010 Chris Jones <tortoise@tortuga>
# Copyright © 2008-2010 Francesco Fumanti <francesco.fumanti@gmx.net>
# Copyright © 2012-2013 Gerd Kohlberger <lowfi@chello.at>
# Copyright © 2010-2017 marmuta <marmvta@gmail.com>
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
_logger = logging.getLogger("OnboardGtk")

import sys
import time
import signal
import os.path

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GLib, Gdk, Gtk

try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from Onboard.DBusUtils import ServiceBase, dbus_property
    has_dbus = True
except ImportError:
    has_dbus = False

from Onboard.KbdWindow       import KbdWindow, KbdPlugWindow
from Onboard.Keyboard        import Keyboard
from Onboard.KeyboardWidget  import KeyboardWidget
from Onboard.Indicator       import Indicator
from Onboard.LayoutLoaderSVG import LayoutLoaderSVG
from Onboard.Appearance      import ColorScheme
from Onboard.IconPalette     import IconPalette
from Onboard.Exceptions      import LayoutFileError
from Onboard.utils           import unicode_str
from Onboard.Timer           import CallOnce, Timer
from Onboard.WindowUtils     import show_confirmation_dialog
import Onboard.osk as osk

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

from Onboard.KeyCommon import KeyCommon

app = "onboard"
DEFAULT_FONTSIZE = 10


class OnboardGtk(object):
    """
    Main controller class for Onboard using GTK+
    """

    keyboard = None

    def __init__(self):

        # Make sure windows get "onboard", "Onboard" as name and class
        # For some reason they aren't correctly set when onboard is started
        # from outside the source directory (Precise).
        GLib.set_prgname(str(app))
        Gdk.set_program_class(app[0].upper() + app[1:])

        # no python3-dbus on Fedora17
        bus = None
        err_msg = ""
        if not has_dbus:
            err_msg = "D-Bus bindings unavailable"
        else:
            # Use D-bus main loop by default
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

            # Don't fail to start in Xenial's lightdm when
            # D-Bus isn't available.
            try:
                bus = dbus.SessionBus()
            except dbus.exceptions.DBusException:
                err_msg = "D-Bus session bus unavailable"
                bus = None

        if not bus:
            _logger.warning(err_msg + "  " +
                            "Onboard will start with reduced functionality. "
                            "Single-instance check, "
                            "D-Bus service and "
                            "hover click are disabled.")

        # Yield to GNOME Shell's keyboard before any other D-Bus activity
        # to reduce the chance for D-Bus timeouts when enabling a11y keboard.
        if not self._can_show_in_current_desktop(bus):
            sys.exit(0)

        if bus:
            # Check if there is already an Onboard instance running
            has_remote_instance = \
                bus.name_has_owner(ServiceOnboardKeyboard.NAME)

            # Onboard in Ubuntu on first start silently embeds itself into
            # gnome-screensaver and stays like this until embedding is manually
            # turned off.
            # If gnome's "Typing Assistent" is disabled, only show onboard in
            # gss when there is already a non-embedded instance running in
            # the user session (LP: 938302).
            if config.xid_mode and \
               config.launched_by == config.LAUNCHER_GSS and \
               not (config.gnome_a11y and
                    config.gnome_a11y.screen_keyboard_enabled) and \
               not has_remote_instance:
                sys.exit(0)

            # Embedded instances can't become primary instances
            if not config.xid_mode:
                if has_remote_instance and \
                   not config.options.allow_multiple_instances:
                    # Present remote instance
                    remote = bus.get_object(ServiceOnboardKeyboard.NAME,
                                            ServiceOnboardKeyboard.PATH)
                    remote.Show(dbus_interface=ServiceOnboardKeyboard.IFACE)
                    _logger.info("Exiting: Not the primary instance.")
                    sys.exit(0)

                # Register our dbus name
                self._bus_name = \
                    dbus.service.BusName(ServiceOnboardKeyboard.NAME, bus)

        self.init()

        _logger.info("Entering mainloop of onboard")
        Gtk.main()

        # Shut up error messages on SIGTERM in lightdm:
        # "sys.excepthook is missing, lost sys.stderr"
        # See http://bugs.python.org/issue11380 for more.
        # Python 2.7, Precise
        try:
            sys.stdout.close()
        except:
            pass
        try:
            sys.stderr.close()
        except:
            pass

    def init(self):
        self.keyboard_state = None
        self._last_mod_mask = 0
        self.vk_timer = None
        self.reset_vk()
        self._connections = []
        self._window = None
        self.status_icon = None
        self.service_keyboard = None
        self._reload_layout_timer = Timer()

        # finish config initialization
        config.init()

        # Optionally wait a little before proceeding.
        # When the system starts up, the docking strut can be unreliable
        # on Compiz (Yakkety) and the keyboard may end up at unexpected initial
        # positions. Delaying the startup when launched by
        # onboard-autostart.desktop helps to prevent this.
        delay = config.startup_delay
        if delay:
            Timer(delay, self._init_delayed)
        else:
            self._init_delayed()

    def _init_delayed(self):

        # Release pressed keys when onboard is killed.
        # Don't keep enter key stuck when being killed by lightdm.
        try:
            self._osk_util = osk.Util()
        except AttributeError:
            self._osk_util = None
        if self._osk_util is not None:
            self._osk_util.set_unix_signal_handler(signal.SIGTERM, self.on_sigterm)
            self._osk_util.set_unix_signal_handler(signal.SIGINT, self.on_sigint)

        sys.path.append(os.path.join(config.install_dir, 'scripts'))

        # Create the central keyboard model
        self.keyboard = Keyboard(self)

        # Create the initial keyboard widget
        # Care for toolkit independence only once there is another
        # supported one besides GTK.
        self.keyboard_widget = KeyboardWidget(self.keyboard)

        # create the main window
        if config.xid_mode:    # XEmbed mode for gnome-screensaver?
            # no icp, don't flash the icon palette in lightdm

            self._window = KbdPlugWindow(self.keyboard_widget)

            # write xid to stdout
            sys.stdout.write('%d\n' % self._window.get_id())
            sys.stdout.flush()
        else:
            icp = IconPalette(self.keyboard)
            icp.set_layout_view(self.keyboard_widget)
            icp.connect("activated", self._on_icon_palette_acticated)
            self.do_connect(icp.get_menu(), "quit-onboard",
                        lambda x: self.do_quit_onboard())

            self._window = KbdWindow(self.keyboard_widget, icp)
            self.do_connect(self._window, "quit-onboard",
                            lambda x: self.do_quit_onboard())

        # config.xid_mode = True
        self._window.application = self
        # need this to access screen properties
        config.main_window = self._window

        # load the initial layout
        _logger.info("Loading initial layout")
        self.reload_layout()

        # Handle command line options x, y, size after window creation
        # because the rotation code needs the window's screen.
        if not config.xid_mode:
            rect = self._window.get_rect().copy()
            options = config.options
            if options.size:
                size = options.size.split("x")
                rect.w = int(size[0])
                rect.h = int(size[1])
            if options.x is not None:
                rect.x = options.x
            if options.y is not None:
                rect.y = options.y

            # Make sure the keyboard fits on screen
            rect = self._window.limit_size(rect)

            if not rect.is_empty() and \
               rect != self._window.get_rect():
                _logger.debug("limiting window size: {} to {}"
                            .format(self._window.get_rect(), rect))
                orientation = self._window.get_screen_orientation()
                self._window.write_window_rect(orientation, rect)
                self._window.restore_window_rect()  # move/resize early
            else:
                _logger.debug("not limiting window size: {} to {}"
                            .format(self._window.get_rect(), rect))


        # export dbus service
        if not config.xid_mode and \
           has_dbus:
            self.service_keyboard = ServiceOnboardKeyboard(self)

        # show/hide the window
        self.keyboard_widget.set_startup_visibility()

        # keep keyboard window and icon palette on top of dash
        self._keep_windows_on_top()

        # connect notifications for keyboard map and group changes
        self.keymap = Gdk.Keymap.get_default()
        # map changes
        self.do_connect(self.keymap, "keys-changed", self.cb_keys_changed)
        self.do_connect(self.keymap, "state-changed", self.cb_state_changed)
        # group changes
        Gdk.event_handler_set(cb_any_event, self)

        # connect config notifications here to keep config from holding
        # references to keyboard objects.
        once = CallOnce(50).enqueue  # delay callbacks by 50ms
        reload_layout       = lambda x: once(self.reload_layout_and_present)
        update_ui           = lambda x: once(self._update_ui)
        update_ui_no_resize = lambda x: once(self._update_ui_no_resize)
        update_transparency = \
            lambda x: once(self.keyboard_widget.update_transparency)
        update_inactive_transparency = \
            lambda x: once(self.keyboard_widget.update_inactive_transparency)

        # general

        # keyboard
        config.keyboard.key_synth_notify_add(reload_layout)
        config.keyboard.input_event_source_notify_add(lambda x:
                                    self.keyboard.update_input_event_source())
        config.keyboard.touch_input_notify_add(lambda x:
                                    self.keyboard.update_touch_input_mode())
        config.keyboard.show_secondary_labels_notify_add(update_ui)

        # window
        config.window.window_state_sticky_notify_add(
            lambda x: self._window.update_sticky_state())
        config.window.window_decoration_notify_add(
            self._on_window_options_changed)
        config.window.force_to_top_notify_add(self._on_window_options_changed)
        config.window.keep_aspect_ratio_notify_add(update_ui)

        config.window.transparency_notify_add(update_transparency)
        config.window.background_transparency_notify_add(update_transparency)
        config.window.transparent_background_notify_add(update_ui)
        config.window.enable_inactive_transparency_notify_add(update_transparency)
        config.window.inactive_transparency_notify_add(update_inactive_transparency)
        config.window.docking_notify_add(self._update_docking)
        config.window.docking_aspect_change_range_notify_add(
            lambda x: self.keyboard_widget
            .update_docking_aspect_change_range())

        # layout
        config.layout_filename_notify_add(reload_layout)

        # theme
        # config.gdi.gtk_theme_notify_add(self.on_gtk_theme_changed)
        config.theme_notify_add(self.on_theme_changed)
        config.key_label_font_notify_add(reload_layout)
        config.key_label_overrides_notify_add(reload_layout)
        config.theme_settings.color_scheme_filename_notify_add(reload_layout)
        config.theme_settings.key_label_font_notify_add(reload_layout)
        config.theme_settings.key_label_overrides_notify_add(reload_layout)
        config.theme_settings.theme_attributes_notify_add(update_ui)

        # snippets
        config.snippets_notify_add(reload_layout)

        # auto-show
        config.auto_show.enabled_notify_add(
            lambda x: self.keyboard.update_auto_show())
        config.auto_show.hide_on_key_press_notify_add(
            lambda x: self.keyboard.update_auto_hide())
        config.auto_show.tablet_mode_detection_notify_add(
            lambda x: self.keyboard.update_tablet_mode_detection())
        config.auto_show.keyboard_device_detection_enabled_notify_add(
            lambda x: self.keyboard.update_keyboard_device_detection())

        # word suggestions
        config.word_suggestions.show_context_line_notify_add(update_ui)
        config.word_suggestions.enabled_notify_add(lambda x:
                                 self.keyboard.on_word_suggestions_enabled(x))
        config.word_suggestions.auto_learn_notify_add(
                                 update_ui_no_resize)
        config.typing_assistance.active_language_notify_add(lambda x: \
                                 self.keyboard.on_active_lang_id_changed())
        config.typing_assistance.spell_check_backend_notify_add(lambda x: \
                                 self.keyboard.on_spell_checker_changed())
        config.typing_assistance.auto_capitalization_notify_add(lambda x: \
                                 self.keyboard.on_word_suggestions_enabled(x))
        config.word_suggestions.spelling_suggestions_enabled_notify_add(lambda x: \
                                 self.keyboard.on_spell_checker_changed())
        config.word_suggestions.delayed_word_separators_enabled_notify_add(lambda x: \
                                 self.keyboard.on_punctuator_changed())
        config.word_suggestions.wordlist_buttons_notify_add(
                                 update_ui_no_resize)

        # universal access
        config.scanner.enabled_notify_add(self.keyboard._on_scanner_enabled)
        config.window.window_handles_notify_add(self._on_window_handles_changed)

        # misc
        config.keyboard.show_click_buttons_notify_add(update_ui)
        config.lockdown.lockdown_notify_add(update_ui)
        if config.mousetweaks:
            config.mousetweaks.state_notify_add(update_ui_no_resize)

        # create status icon
        self.status_icon = Indicator()
        self.status_icon.set_keyboard(self.keyboard)
        self.do_connect(self.status_icon.get_menu(), "quit-onboard",
                        lambda x: self.do_quit_onboard())

        # Callbacks to use when icp or status icon is toggled
        config.show_status_icon_notify_add(self.show_hide_status_icon)
        config.icp.in_use_notify_add(self.cb_icp_in_use_toggled)

        self.show_hide_status_icon(config.show_status_icon)


        # Minimize to IconPalette if running under GDM
        if 'RUNNING_UNDER_GDM' in os.environ:
            _logger.info("RUNNING_UNDER_GDM set, turning on icon palette")
            config.icp.in_use = True
            _logger.info("RUNNING_UNDER_GDM set, turning off indicator")
            config.show_status_icon = False

            # For some reason the new values don't arrive in gsettings when
            # running the unit test "test_running_in_live_cd_environment".
            # -> Force gsettings to apply them, that seems to do the trick.
            config.icp.apply()
            config.apply()

        # unity-2d needs the skip-task-bar hint set before the first mapping.
        self.show_hide_taskbar()


        # Check gnome-screen-saver integration
        # onboard_xembed_enabled                False True     True      True
        # config.gss.embedded_keyboard_enabled  any   False    any       False
        # config.gss.embedded_keyboard_command  any   empty    !=onboard ==onboard
        # Action:                               nop   enable   Question1 Question2
        #                                             silently
        if not config.xid_mode and \
           config.onboard_xembed_enabled:

            # If it appears, that nothing has touched the gss keys before,
            # silently enable gss integration with onboard.
            if not config.gss.embedded_keyboard_enabled and \
               not config.gss.embedded_keyboard_command:
                config.enable_gss_embedding(True)

            # If onboard is configured to be embedded into the unlock screen
            # dialog, and the embedding command is different from onboard, ask
            # the user what to do
            elif not config.is_onboard_in_xembed_command_string():
                question = _("Onboard is configured to appear with the dialog to "
                             "unlock the screen; for example to dismiss the "
                             "password-protected screensaver.\n\n"
                             "However the system is not configured anymore to use "
                             "Onboard to unlock the screen. A possible reason can "
                             "be that another application configured the system to "
                             "use something else.\n\n"
                             "Would you like to reconfigure the system to show "
                             "Onboard when unlocking the screen?")
                _logger.warning("showing dialog: '{}'".format(question))
                reply = show_confirmation_dialog(question,
                                                 self._window,
                                                 config.is_force_to_top())
                if reply == True:
                    config.enable_gss_embedding(True)
                else:
                    config.onboard_xembed_enabled = False
            else:
                if not config.gss.embedded_keyboard_enabled:
                    question = _("Onboard is configured to appear with the dialog "
                                 "to unlock the screen; for example to dismiss "
                                 "the password-protected screensaver.\n\n"
                                 "However this function is disabled in the system.\n\n"
                                 "Would you like to activate it?")
                    _logger.warning("showing dialog: '{}'".format(question))
                    reply = show_confirmation_dialog(question,
                                                     self._window,
                                                     config.is_force_to_top())
                    if reply == True:
                        config.enable_gss_embedding(True)
                    else:
                        config.onboard_xembed_enabled = False

        # check if gnome accessibility is enabled for auto-show
        if (config.is_auto_show_enabled() or \
            config.are_word_suggestions_enabled()) and \
            not config.check_gnome_accessibility(self._window):
            config.auto_show.enabled = False

    def on_sigterm(self):
        """
        Exit onboard on kill.
        """
        _logger.debug("SIGTERM received")
        self.do_quit_onboard()

    def on_sigint(self):
        """
        Exit onboard on Ctrl+C press.
        """
        _logger.debug("SIGINT received")
        self.do_quit_onboard()

    def do_connect(self, instance, signal, handler):
        handler_id = instance.connect(signal, handler)
        self._connections.append((instance, handler_id))

    # Method concerning the taskbar
    def show_hide_taskbar(self):
        """
        This method shows or hides the taskbard depending on whether there
        is an alternative way to unminimize the Onboard window.
        This method should be called every time such an alternative way
        is activated or deactivated.
        """
        if self._window:
            self._window.update_taskbar_hint()

    # Method concerning the icon palette
    def _on_icon_palette_acticated(self, widget):
        self.keyboard.toggle_visible()

    def cb_icp_in_use_toggled(self, icp_in_use):
        """
        This is the callback that gets executed when the user toggles
        the gsettings key named in_use of the icon_palette. It also
        handles the showing/hiding of the taskar.
        """
        _logger.debug("Entered in on_icp_in_use_toggled")
        self.show_hide_icp()
        _logger.debug("Leaving on_icp_in_use_toggled")

    def show_hide_icp(self):
        if self._window.icp:
            show = config.is_icon_palette_in_use()
            if show:
                # Show icon palette if appropriate and handle visibility of taskbar.
                if not self._window.is_visible():
                    self._window.icp.show()
                self.show_hide_taskbar()
            else:
                # Show icon palette if appropriate and handle visibility of taskbar.
                if not self._window.is_visible():
                    self._window.icp.hide()
                self.show_hide_taskbar()

    # Methods concerning the status icon
    def show_hide_status_icon(self, show_status_icon):
        """
        Callback called when gsettings detects that the gsettings key specifying
        whether the status icon should be shown or not is changed. It also
        handles the showing/hiding of the taskar.
        """
        if show_status_icon:
            self.status_icon.set_visible(True)
        else:
            self.status_icon.set_visible(False)
        self.show_hide_icp()
        self.show_hide_taskbar()

    def cb_status_icon_clicked(self,widget):
        """
        Callback called when status icon clicked.
        Toggles whether Onboard window visibile or not.

        TODO would be nice if appeared to iconify to taskbar
        """
        self.keyboard.toggle_visible()

    def cb_group_changed(self):
        """ keyboard group change """
        self.reload_layout_delayed()

    def cb_keys_changed(self, keymap):
        """ keyboard map change """
        self.reload_layout_delayed()

    def cb_state_changed(self, keymap):
        """ keyboard modifier state change """
        mod_mask = keymap.get_modifier_state()
        _logger.debug("keyboard state changed to 0x{:x}" \
                      .format(mod_mask))
        if self._last_mod_mask == mod_mask:
            # Must be something other than changed modifiers,
            # group change on wayland, for example.
            self.reload_layout_delayed()
        else:
            self.keyboard.set_modifiers(mod_mask)

        self._last_mod_mask = mod_mask


    def cb_vk_timer(self):
        """
        Timer callback for polling until virtkey becomes valid.
        """
        if self.get_vk():
            self.reload_layout(force_update=True)
            GLib.source_remove(self.vk_timer)
            self.vk_timer = None
            return False
        return True

    def _update_ui(self):
        if self.keyboard:
            self.keyboard.invalidate_ui()
            self.keyboard.commit_ui_updates()

    def _update_ui_no_resize(self):
        if self.keyboard:
            self.keyboard.invalidate_ui_no_resize()
            self.keyboard.commit_ui_updates()

    def _on_window_handles_changed(self, value = None):
        self.keyboard_widget.update_window_handles()
        self._update_ui()

    def _on_window_options_changed(self, value = None):
        self._update_window_options()
        self.keyboard.commit_ui_updates()

    def _update_window_options(self, value = None):
        window = self._window
        if window:
            window.update_window_options()
            if window.icp:
                window.icp.update_window_options()
            self.keyboard.invalidate_ui()

    def _update_docking(self, value = None):
        self._update_window_options()

        # give WM time to settle, else move to the strut position may fail
        GLib.idle_add(self._update_docking_delayed)

    def _update_docking_delayed(self):
        self._window.on_docking_notify()
        self.keyboard.invalidate_ui()  # show/hide the move button
#        self.keyboard.commit_ui_updates() # redundant

    def on_gdk_setting_changed(self, name):
        if name == "gtk-theme-name":
            # In Zesty this has to be delayed too.
            GLib.timeout_add_seconds(1, self.on_gtk_theme_changed)

        elif name in ["gtk-xft-dpi",
                      "gtk-xft-antialias"
                      "gtk-xft-hinting",
                      "gtk-xft-hintstyle"]:
            # For some reason the font sizes are still off when running
            # this immediately. Delay it a little.
            GLib.idle_add(self.on_gtk_font_dpi_changed)

    def on_gtk_theme_changed(self, gtk_theme = None):
        """
        Switch onboard themes in sync with gtk-theme changes.
        """
        config.load_theme()

    def on_gtk_font_dpi_changed(self):
        """
        Refresh the key's pango layout objects so that they can adapt
        to the new system dpi setting.
        """
        self.keyboard_widget.refresh_pango_layouts()
        self._update_ui()

        return False

    def on_theme_changed(self, theme):
        config.apply_theme()
        self.reload_layout()

    def _keep_windows_on_top(self, enable=True):
        if not config.xid_mode: # be defensive, not necessary when embedding
            if enable:
                windows = [self._window, self._window.icp]
            else:
                windows = []
            _logger.debug("keep_windows_on_top {}".format(windows))
            if getattr(self, "_osk_util", None) is not None:
                self._osk_util.keep_windows_on_top(windows)

    def on_focusable_gui_opening(self):
        self._keep_windows_on_top(False)

    def on_focusable_gui_closed(self):
        self._keep_windows_on_top(True)

    def reload_layout_and_present(self):
        """
        Reload the layout and briefly show the window
        with active transparency
        """
        self.reload_layout(force_update = True)
        self.keyboard_widget.update_transparency()

    def reload_layout_delayed(self):
        """
        Delay reloading the layout on keyboard map or group changes
        This is mainly for LP #1313176 when Caps-lock is set up as
        an accelerator to switch between keyboard "input sources" (layouts)
        in unity-control-center->Text Entry (aka region panel).
        Without waiting until after the shortcut turns off numlock,
        the next "input source" (layout) is skipped and a second one
        is selected.
        """
        self._reload_layout_timer.start(.5, self.reload_layout)

    def reload_layout(self, force_update=False):
        """
        Checks if the X keyboard layout has changed and
        (re)loads Onboards layout accordingly.
        """
        keyboard_state = (None, None)

        vk = self.get_vk()
        if vk:
            try:
                vk.reload() # reload keyboard names
                keyboard_state = (vk.get_layout_as_string(),
                                  vk.get_current_group_name())
            except osk.error:
                self.reset_vk()
                force_update = True
                _logger.warning("Keyboard layout changed, but retrieving "
                                "keyboard information failed")

        if self.keyboard_state != keyboard_state or force_update:
            self.keyboard_state = keyboard_state

            layout_filename = config.layout_filename
            color_scheme_filename = config.theme_settings.color_scheme_filename

            try:
                self.load_layout(layout_filename, color_scheme_filename)
            except LayoutFileError as ex:
                _logger.error("Layout error: " + unicode_str(ex) + ". Falling back to default layout.")

                # last ditch effort to load the default layout
                self.load_layout(config.get_fallback_layout_filename(),
                                 color_scheme_filename)

        # if there is no X keyboard, poll until it appears (if ever)
        if not vk and not self.vk_timer:
            self.vk_timer = GLib.timeout_add_seconds(1, self.cb_vk_timer)

    def load_layout(self, layout_filename, color_scheme_filename):
        _logger.info("Loading keyboard layout " + layout_filename)
        if (color_scheme_filename):
            _logger.info("Loading color scheme " + color_scheme_filename)

        vk = self.get_vk()

        color_scheme = ColorScheme.load(color_scheme_filename) \
                       if color_scheme_filename else None
        layout = LayoutLoaderSVG().load(vk, layout_filename, color_scheme)

        self.keyboard.set_layout(layout, color_scheme, vk)

        if self._window and self._window.icp:
            self._window.icp.queue_draw()

    def get_vk(self):
        if not self._vk:
            try:
                # may fail if there is no X keyboard (LP: 526791)
                self._vk = osk.Virtkey()

            except osk.error as e:
                t = time.time()
                if t > self._vk_error_time + .2: # rate limit to once per 200ms
                    _logger.warning("vk: " + unicode_str(e))
                    self._vk_error_time = t

        return self._vk

    def reset_vk(self):
        self._vk = None
        self._vk_error_time = 0


    # Methods concerning the application
    def emit_quit_onboard(self):
        self._window.emit_quit_onboard()

    def do_quit_onboard(self):
        _logger.debug("Entered do_quit_onboard")
        self.final_cleanup()
        self.cleanup()

    def cleanup(self):
        self._reload_layout_timer.stop()

        config.cleanup()

        # Make an effort to disconnect all handlers.
        # Used to be used for safe restarting.
        for instance, handler_id in self._connections:
            instance.disconnect(handler_id)

        if self.keyboard:
            if self.keyboard.scanner:
                self.keyboard.scanner.finalize()
                self.keyboard.scanner = None
            self.keyboard.cleanup()

        self.status_icon.cleanup()
        self.status_icon = None

        self._window.cleanup()
        self._window.destroy()
        self._window = None
        Gtk.main_quit()


    def final_cleanup(self):
        config.final_cleanup()

    @staticmethod
    def _can_show_in_current_desktop(bus):
        """
        When GNOME's "Typing Assistent" is enabled in GNOME Shell, Onboard
        starts simultaneously with the Shell's built-in screen keyboard.
        With GNOME Shell 3.5.4-0ubuntu2 there is no known way to choose
        one over the other (LP: 879942).

        Adding NotShowIn=GNOME; to onboard-autostart.desktop prevents it
        from running not only in GNOME Shell, but also in the GMOME Fallback
        session, which is undesirable. Both share the same xdg-desktop name.

        -> Do it ourselves: optionally check for GNOME Shell and yield to the
        built-in keyboard.
        """
        result = True

        # Content of XDG_CURRENT_DESKTOP:
        # Before Vivid: GNOME Shell:   GNOME
        #               GNOME Classic: GNOME
        # Since Vivid:  GNOME Shell:   GNOME
        #               GNOME Classic: GNOME-Classic:GNOME

        if config.options.not_show_in:
            current = os.environ.get("XDG_CURRENT_DESKTOP", "")
            names = config.options.not_show_in.split(",")
            for name in names:
                if name == current:
                    # Before Vivid GNOME Shell and GNOME Classic had the same
                    # desktop name "GNOME". Use the D-BUS name to decide if we
                    # are in the Shell.
                    if name == "GNOME":
                        if bus and bus.name_has_owner("org.gnome.Shell"):
                            result = False
                    else:
                        result  = False

            if not result:
                _logger.info("Command line option not-show-in={} forbids running in "
                             "current desktop environment '{}'; exiting." \
                             .format(names, current))
        return result


if has_dbus:
    class ServiceOnboardKeyboard(ServiceBase):
        """
        Onboard's main D-Bus service.
        """

        NAME = "org.onboard.Onboard"
        PATH = "/org/onboard/Onboard/Keyboard"
        IFACE = "org.onboard.Onboard.Keyboard"

        LOCK_REASON = "D-Bus"

        def __init__(self, app):
            ServiceBase.__init__(self, dbus.SessionBus(), self.NAME, self.PATH)
            self._keyboard = app.keyboard
            self._keyboard_widget = app.keyboard_widget

        @dbus.service.method(dbus_interface=IFACE)
        def Show(self):  # noqa: flake8
            self._keyboard.request_visibility(True)

        @dbus.service.method(dbus_interface=IFACE)
        def Hide(self):  # noqa: flake8
            self._keyboard.request_visibility(False)

        @dbus.service.method(dbus_interface=IFACE)
        def ToggleVisible(self):  # noqa: flake8
            self._keyboard.request_visibility_toggle()

        # private method, for unit-testing only
        if config.is_running_from_source:
            @dbus.service.method(dbus_interface=IFACE,
                                 out_signature='a(sada{is}a{sb})')  # noqa: flake8
            def GetKeyState(self):
                result = []
                for key in self._keyboard.iter_keys():
                    r = self._keyboard_widget.get_key_screen_rect(key)
                    if not r.is_empty() and key.is_path_visible():
                        labels = {m : l for m, l in key.labels.items() if l}
                        result.append([key.get_id(),
                                       list(r),
                                       labels,
                                       key.get_state()])
                return result

        # Property Visible, read-only
        @dbus_property(dbus_interface=IFACE, signature='b')
        def Visible(self):  # noqa: flake8
            return self._keyboard.is_visible()

        # Property AutoShowPaused, read-write
        @dbus_property(dbus_interface=IFACE, signature='b')
        def AutoShowPaused(self):  # noqa: flake8
            return self._keyboard.is_auto_show_locked(self.LOCK_REASON)

        @AutoShowPaused.setter
        def AutoShowPaused(self, value):  # noqa: flake8
            if value:
                self._keyboard.auto_show_lock_and_hide(self.LOCK_REASON)
            else:
                self._keyboard.auto_show_unlock(self.LOCK_REASON)


def cb_any_event(event, onboard):

    # Hide bug in Oneiric's GTK3
    # Suppress ValueError: invalid enum value: 4294967295
    try:
        type = event.type
    except ValueError:
        type = None

    if 0:  # debug
        a = [event, event.type]
        if type == Gdk.EventType.VISIBILITY_NOTIFY:
            a += [event.state]
        if type == Gdk.EventType.CONFIGURE:
            a += [event.x, event.y, event.width, event.height]
        if type == Gdk.EventType.WINDOW_STATE:
            a += [event.window_state]
        if type == Gdk.EventType.UNMAP:
            a += [event.window, "0x{:x}".format(event.window.get_xid())]
        print(*a)

    # Update layout on keyboard group changes
    # XkbStateNotify maps to Gdk.EventType.NOTHING
    # https://bugzilla.gnome.org/show_bug.cgi?id=156948
    if type == Gdk.EventType.NOTHING:
        onboard.cb_group_changed()

    # Update the cached pango layout object here or Onboard
    # doesn't get those settings, i.e. label fonts sizes are off
    # when font dpi changes.
    elif type == Gdk.EventType.SETTING:
        onboard.on_gdk_setting_changed(event.setting.name)

    Gtk.main_do_event(event)

