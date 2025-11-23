# -*- coding: utf-8 -*-

# Copyright © 2008 Chris Jones <tortoise@tortuga>
# Copyright © 2009-2011 Francesco Fumanti <francesco.fumanti@gmx.net>
# Copyright © 2012 Gerd Kohlberger <lowfi@chello.at>
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

import time

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GObject, GLib, GdkX11, Gdk, Gtk

from Onboard.definitions import DockingMonitor
from Onboard.utils       import Rect, Version
from Onboard.Timer       import CallOnce, Timer
from Onboard.WindowUtils import Orientation, WindowRectPersist, \
                                WindowRectTracker, set_unity_property, \
                                gtk_has_resize_grip_support, \
                                get_monitor_dimensions, \
                                physical_to_monitor_pixel_size
import Onboard.osk as osk

### Logging ###
import logging
_logger = logging.getLogger("KbdWindow")
###############

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################



class KbdWindowBase:
    """
    Very messy class holds the keyboard widget. The mess is the docked
    window support which is disable because of numerous metacity bugs.
    """
    def __init__(self, keyboard_widget, icp):
        _logger.debug("Entered in __init__")

        try:
            self._osk_util = osk.Util()
        except AttributeError:
            self._osk_util = None
        try:
            self._osk_struts = osk.Struts()
        except AttributeError:
            self._osk_struts = None

        self.application = None
        self.keyboard_widget = keyboard_widget
        self.icp = icp

        self.supports_alpha = False

        self._visible = False
        self._sticky = False
        self._iconified = False
        self._maximized = False

        self._screen_resizing = False

        self._docking_enabled = False
        self._docking_edge = None
        self._docking_rect = Rect()
        self._docking_monitor = None
        self._shrink_work_area = False
        self._dock_expand = False
        self._current_struts = None

        self._current_docking_monitor = None
        self._monitor_workarea = {}

        if gtk_has_resize_grip_support():
            self._default_resize_grip = self.get_has_resize_grip()
        else:
            self._default_resize_grip = False
        self._type_hint = None

        self._known_window_rects = []
        self._written_window_rects = {}
        self._written_dock_sizes = {}
        self._auto_position_started = False

        self._desktop_switch_count = 0
        self._moved_desktop_switch_count = 0

        self.set_accept_focus(False)
        self.set_app_paintable(True)
        self.set_keep_above(True)
        #Gtk.Settings.get_default().set_property("gtk-touchscreen-mode", True)

        Gtk.Window.set_default_icon_name("onboard")
        self.set_title(_("Onboard"))

        self.connect("window-state-event",      self._cb_window_state_event)
        self.connect("visibility-notify-event", self._cb_visibility_notify)
        self.connect('screen-changed',          self._cb_screen_changed)
        self.connect('composited-changed',      self._cb_composited_changed)
        self.connect("realize",                 self._cb_realize_event)
        self.connect("unrealize",               self._cb_unrealize_event)
        self.connect("configure-event",         self._on_configure_event)

        self.detect_window_manager()
        self.check_alpha_support()
        self.update_unrealize_options()

        self.add(self.keyboard_widget)

        _logger.debug("Leaving __init__")

    def cleanup(self):
        pass

    def _cb_screen_changed(self, widget, old_screen=None):
        self.detect_window_manager()
        self.check_alpha_support()
        self.queue_draw()

    def _cb_composited_changed(self, widget):
        self.detect_window_manager()
        self.check_alpha_support()
        self.queue_draw()

    def emit_quit_onboard(self):
        self.emit("quit-onboard")

    def detect_window_manager(self):
        """
        Detect the WM and select WM specific behavior.

        Doctests:
        >>> osk_util = osk.Util()
        >>> len(osk_util.get_current_wm_name().lower()) > 0
        True
        """
        wm = config.quirks_name
        if not wm and self._osk_util is not None:
            # Returns None on X error BadWindow (LP: 1016980)
            # Keep the same quirks as before in that case.
            wm = self._osk_util.get_current_wm_name()

        if wm:
            config.quirks = None
            for cls in [WMQuirksCompiz, WMQuirksMetacity, WMQuirksMutter]:
                if  wm.lower() in cls.wms:
                    config.quirks = cls()
                    break

        if not config.quirks:
            config.quirks = WMQuirksDefault()

        _logger.debug("window manager: {}".format(wm))
        _logger.debug("quirks selected: {}" \
                                       .format(str(config.quirks.__class__)))
        return True

    def check_alpha_support(self):
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.supports_alpha = \
            visual and \
            (screen.is_composited() or \
             config.launched_by == config.LAUNCHER_UNITY_GREETER)

        self.keyboard_widget.supports_alpha = self.supports_alpha

        _logger.debug("screen changed, supports_alpha={}" \
                       .format(self.supports_alpha))

        # Unity may start onboard early, where there is no compositing
        # enabled yet. If we set the visual later the window never becomes
        # transparent -> do it as soon as there is an rgba visual.
        if visual:
            self.set_visual(visual)
            self.keyboard_widget.set_visual(visual)

            # Somehow Gtk 3.4 still needs these now deprecated calls,
            # even though IconPalette and LabelPopups don't.
            # Otherwise there will be a white background.
            gtk_version = Version(Gtk.MAJOR_VERSION, Gtk.MINOR_VERSION)
            if gtk_version < Version(3, 18):  # Xenial doesn't need them
                self.override_background_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
                self.keyboard_widget.override_background_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
        else:
            _logger.info(_("no window transparency available;"
                           " screen doesn't support alpha channels"))
        return False

    def _show_first_time(self):
        self.update_window_options()
        self.update_window_scaling_factor(True)
        self.pre_render_keys(*self.get_size())
        self.show()

    def move_resize(self, x, y, w, h):
        if not self.keyboard_widget.is_drag_initiated():
            self.pre_render_keys(w, h)
        super(KbdWindowBase, self).move_resize(x, y, w, h)

    def pre_render_keys(self, w, h):
        self.keyboard_widget.pre_render_keys(self, w, h)

    def get_min_window_size(self):
        min_mm = (50, 20)  # just large enough to grab with a 3 finger gesture
        return physical_to_monitor_pixel_size(self, min_mm, (150, 100))

    def _cb_realize_event(self, user_data):
        """ Gdk window created """
        # Disable maximize function (LP #859288)
        # unity:    no effect, but double click on top bar unhides anyway
        # unity-2d: works and avoids the bug
        self.get_window().set_functions(Gdk.WMFunction.RESIZE | \
                                        Gdk.WMFunction.MOVE | \
                                        Gdk.WMFunction.MINIMIZE | \
                                        Gdk.WMFunction.CLOSE)

        set_unity_property(self)

        if not config.xid_mode:   # not when embedding
            ord = config.is_override_redirect()
            if ord:
                self.set_override_redirect(True)

            self.update_taskbar_hint()
            self.restore_window_rect(startup = True)

        # set min window size for unity MT grab handles
        geom = Gdk.Geometry()
        geom.min_width, geom.min_height = self.get_min_window_size()

        if _logger.isEnabledFor(logging.DEBUG):
            screen = self.get_screen()
            _logger.debug("_cb_realize_event, screen size {}x{}"
                          .format(screen.width(), screen.height()))
            size, size_mm = get_monitor_dimensions(self)
            _logger.debug("_cb_realize_event, "
                          "monitor dimensions: {} pixel, {} mm"
                          .format(repr(size), repr(size_mm)))
            _logger.debug("_cb_realize_event, get_min_window_size(): {} {}"
                          .format(geom.min_width, geom.min_height))

        self.set_geometry_hints(self, geom, Gdk.WindowHints.MIN_SIZE)

    def _cb_unrealize_event(self, user_data):
        """ Gdk window destroyed """
        self.update_unrealize_options()

    def update_unrealize_options(self):
        if not config.xid_mode:   # not when embedding
            self.set_decorated(config.window.window_decoration)

            type_hint = config.quirks.get_window_type_hint(self)
            self.set_type_hint(type_hint)
            self._type_hint = type_hint

    def update_window_options(self, startup = False):
        if not config.xid_mode:   # not when embedding

            recreate = False

            # Window decoration toggled?
            decorated = config.window.window_decoration
            if decorated != self.get_decorated():
                recreate = True

            # Override redirect toggled?
            ord = config.is_override_redirect()
            if ord != self.get_override_redirect():
                recreate = True

            # Window type hint changed?
            type_hint = config.quirks.get_window_type_hint(self)
            if type_hint != self._type_hint:
                recreate = True

            # (re-)create the gdk window?
            if recreate:
                self._recreate_window()

            # Show the resize gripper?
            if gtk_has_resize_grip_support():
                if config.has_window_decoration():
                    self.set_has_resize_grip(self._default_resize_grip)
                else:
                    self.set_has_resize_grip(False)

            self.update_sticky_state()

    def _recreate_window(self):
        _logger.info("recreating window - window options changed")

        visible = None
        if self.get_realized(): # not starting up?
            visible = self.is_visible()
            self.hide()
            self.unrealize()

        self.realize()

        if not visible is None:
            Gtk.Window.set_visible(self, visible)

    def update_sticky_state(self):
        if not config.xid_mode:
            # Always on visible workspace?
            sticky = config.get_sticky_state()
            if self._sticky != sticky:
                self._sticky = sticky
                if sticky:
                    self.stick()
                else:
                    self.unstick()

            if self.icp:
                self.icp.update_sticky_state()

    def update_taskbar_hint(self):
        config.quirks.update_taskbar_hint(self)

    def _on_configure_event(self, widget, event):
        self.update_window_rect()
        self.update_window_scaling_factor()

        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(
                "configure-event: event {}, allocated {}, "
                "window {}, scaling factor {}"
                .format(Rect(event.x, event.y, event.width, event.height),
                        (self.get_allocated_width(),
                        self.get_allocated_height()),
                        self.get_rect(),
                        config.window_scaling_factor))

    def update_window_scaling_factor(self, startup = False):
        """ check for changes to the window-scaling-factor """
        scale = self.get_scale_factor()
        _logger.debug("update_window_scaling_factor: scale {}"
                      .format(scale))

        if not scale is None and \
           config.window_scaling_factor != scale:
            config.window_scaling_factor = scale
            _logger.info("new window-scaling-factor '{}'".format(scale))

            keyboard = self.keyboard_widget.keyboard

            # In override redirect mode the window gets stuck with
            # the old scale -> recreate it
            if config.is_override_redirect() and \
               not startup and \
               not config.xid_mode:
                self._recreate_window()

                # Release pressed keys in case the switch was initiated
                # by Onboard, e.g. with the Return key in the terminal.
                keyboard.release_pressed_keys()

            # invalidate all images and key surfaces
            self.keyboard_widget.invalidate_images()
            keyboard.invalidate_ui()
            keyboard.commit_ui_updates()

    def is_visible(self):
        if not self.get_mapped():
            return False
        return self._visible

    def set_visible(self, visible):
        # Lazily show the window for smooth startup,
        # in particular with force-to-top mode enabled.
        if not self.get_realized():
            self._show_first_time()

        # Make sure the move button stays visible
        # Do this on hiding the window, because the window position
        # is unreliable when unhiding.
        if not visible and \
           self.can_move_into_view():
            self.move_home_rect_into_view()

        config.quirks.set_visible(self, visible)
        self.on_visibility_changed(visible)

    def on_visibility_changed(self, visible):

        if not visible:
            keyboard = self.keyboard_widget.keyboard
            keyboard.stop_raise_attempts()

        visible_before = self._visible
        self._visible = visible

        if not self._screen_resizing:
            if visible:
                self.set_icp_visible(False)
                self.update_sticky_state()
            else:
                # show the icon palette
                if config.is_icon_palette_in_use():
                    self.set_icp_visible(True)

        # update indicator menu for unity and unity2d
        # not necessary but doesn't hurt in gnome-shell, gnome classic
        if self.application:
            status_icon = self.application.status_icon
            if status_icon:
                status_icon.update_menu_items()

            service = self.application.service_keyboard
            if service:
                service.PropertiesChanged(service.IFACE,
                                          {'Visible': visible}, ['Visible'])

    def set_opacity(self, opacity):
        self.keyboard_widget.set_opacity(opacity)

    def get_opacity(self):
        return self.keyboard_widget.get_opacity()

    def is_maximized(self):
        return self._maximized

    def is_iconified(self):
        # Force-to-top windows are ignored by the window manager
        # and cannot be in iconified state.
        if config.is_force_to_top():
            return False

        return self._iconified

    def set_icp_visible(self, visible):
        """ Show/hide the icon palette """
        if self.icp:
            if visible:
                self.icp.show()
            else:
                self.icp.hide()

    def _cb_visibility_notify(self, widget, event):
        pass

    def _cb_window_state_event(self, widget, event):
        """
        This is the callback that gets executed when the user hides the
        onscreen keyboard by using the minimize button in the decoration
        of the window.
        Fails to be called when iconifying in gnome-shell (Oneiric).
        Fails to be called when iconifying in unity (Precise).
        Still keep it around for sticky changes.
        """
        _logger.debug("window-state-event: {}, {}"
                      .format(event.changed_mask, event.new_window_state))

        if event.changed_mask & Gdk.WindowState.MAXIMIZED:
            self._maximized = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)

        if event.changed_mask & Gdk.WindowState.ICONIFIED:
            self._iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
            self._on_iconification_state_changed(self._iconified)

        if event.changed_mask & Gdk.WindowState.STICKY:
            self._sticky = bool(event.new_window_state & Gdk.WindowState.STICKY)

    def _on_iconification_state_changed(self, iconified):
            visible = not iconified
            was_visible = self.is_visible()

            self.on_visibility_changed(visible)

            # Cancel visibility transitions still in progress
            self.keyboard_widget.transition_visible_to(visible, 0.0)

            if was_visible != visible:
                if visible:
                    # Hiding may have left the window opacity at 0.
                    # Ramp up the opacity when it was unminimized by
                    # clicking the (unity) launcher.
                    self.keyboard_widget.update_transparency()

                # - Unminimizing from unity-2d launcher is a user
                #   triggered unhide -> lock auto-show visible.
                # - Minimizing while locked visible -> unlock
                keyboard = self.keyboard_widget.keyboard
                keyboard.update_auto_show_on_visibility_change(visible)

            return

    def on_transition_done(self, visible_before, visible_now):
        pass

    def do_set_gravity(self, edgeGravity):
        '''
        This will place the window on the edge corresponding to the edge gravity
        '''
        _logger.debug("Entered in do_set_gravity")
        self.edgeGravity = edgeGravity
        width, height = self.get_size()

        geom = self.get_screen().get_monitor_geometry(0)
        eg = self.edgeGravity

        x = 0
        y = 0
        if eg == Gdk.Gravity.SOUTH:
            y = geom.height - height
            y += 29 #to account for panel.

        self.move(x, y)

        GLib.idle_add(self.do_set_strut)

    def do_set_strut(self):
        _logger.debug("Entered in do_set_strut")
        propvals = [0,0,0,0,0,0,0,0,0,0,0,0]
        """propvals = [0,#left
                0, #right
                0, #top
                300,#bottom
                0,#left_start_y
                0,#left_end_y
                0,#right_start_y
                0,#right_end_y
                0,#top_start_x
                0,#top_end_x
                0,#bottom_start_x
                3000]#bottom_end_x"""

        screen = self.get_screen()
        biggestHeight = 0
        for n in range(screen.get_n_monitors()):
            tempHeight = screen.get_monitor_geometry(n).height
            if biggestHeight < tempHeight:
                biggestHeight = tempHeight

        eg = self.edgeGravity
        x, y = self.window.get_origin()

        width,height = self.get_size()

        if eg == Gdk.Gravity.NORTH:
            propvals[2] = height + y
            propvals[9] = width
        elif eg == Gdk.Gravity.SOUTH and y != 0:
            propvals[3] = biggestHeight - y
            propvals[11] = width - 1

            # tell window manager to not overlap buttons with maximized window
            self.window.property_change("_NET_WM_STRUT_PARTIAL",
                                        "CARDINAL",
                                        32,
                                        Gdk.PropMode.REPLACE,
                                        propvals)
        self.queue_resize_no_redraw()

    def can_move_into_view(self):
        return not config.xid_mode and \
           not config.has_window_decoration() and \
           not config.is_docking_enabled()


    def get_orientation_config_object(self):
        return None


class KbdWindow(KbdWindowBase, WindowRectPersist, Gtk.Window):

    # Minimum window size (for resizing in system mode, see handle_motion())
    MINIMUM_SIZE = 20

    home_rect = None

    def __init__(self, keyboard_widget, icp):
        self._last_ignore_configure_time = None
        self._last_configures = []
        self._was_visible = False
        self._user_positioning_begin_rect = Rect()
        self._realize_docking_time = None

        Gtk.Window.__init__(self,
                            urgency_hint = False,
                            width_request=self.MINIMUM_SIZE,
                            height_request=self.MINIMUM_SIZE)

        KbdWindowBase.__init__(self, keyboard_widget, icp)

        WindowRectPersist.__init__(self)

        GObject.signal_new("quit-onboard", KbdWindow,
                           GObject.SignalFlags.RUN_LAST,
                           GObject.TYPE_BOOLEAN, ())

        self._auto_position_poll_timer = Timer()

        self.restore_window_rect(startup = True)

        self.connect("map",                     self._on_map_event)
        self.connect("unmap",                   self._on_unmap_event)
        self.connect("delete-event", self._on_delete_event)
        # Connect_after seems broken in Quantal, the callback is never called.
        #self.connect_after("configure-event", self._on_configure_event_after)

        if getattr(self, "_osk_util", None) is not None:
            self._osk_util.connect_root_property_notify(["_NET_WORKAREA",
                                                         "_NET_CURRENT_DESKTOP"],
                                                    self._on_root_property_notify)

        once = CallOnce(100).enqueue  # call at most once per 100ms

        rect_changed = lambda x: once(self._on_config_rect_changed)
        config.window.position_notify_add(rect_changed)
        config.window.size_notify_add(rect_changed)

        dock_size_changed = lambda x: once(self._on_config_dock_size_changed)
        config.window.dock_size_notify_add(dock_size_changed)

    def cleanup(self):
        WindowRectPersist.cleanup(self)
        KbdWindowBase.cleanup(self)
        if self.icp:
            self.icp.cleanup()
            self.icp.destroy()
            self.icp = None

    def _on_root_property_notify(self, property):
        """ Fixme: Exceptions get lost in here."""

        if property == "_NET_WORKAREA":

            if config.is_docking_enabled() and \
               not config.xid_mode:

                # Precise: Unity launcher reacts to work area change and
                # Onboard gets trapped in an endles loop of strut resizing
                # including and excluding the launchers width.
                # ignore workarea changes most likely caused by us.
                if self._realize_docking_time is not None and \
                   time.time() - self._realize_docking_time < 1.0:
                    _logger.info("workarea change ignored - "
                                 "it's too soon after setting struts")
                else:
                    monitor = self.get_docking_monitor()
                    new_area = self.get_monitor_workarea(monitor)
                    area = self._monitor_workarea.get(monitor)
                    if area:
                        # Only check for x changes, y is too dangerous
                        # for now, too easy to get the timing wrong and
                        # end up with double docks.
                        if area.x != new_area.x or \
                        area.w != new_area.w:
                            area.x = new_area.x
                            area.w = new_area.w

                            _logger.info("workarea changed to {}, "
                                        "using {} for docking." \
                                        .format(str(new_area), str(area)))
                            self.update_docking()

        elif property == "_NET_CURRENT_DESKTOP":
            # OpenBox: Make sure to move the keyboard to the new desktop
            #          on the next occasion.
            # Unity:   Never reached (Raring), _NET_CURRENT_DESKTOP isn't
            #          set when switching desktops there. However we do get a
            #          configure event, so the transitioning code moves the
            #          window and it is brought to the current desktop anyway.
            self._desktop_switch_count += 1

    def _on_map_event(self, user_data):
        pass

    def _on_unmap_event(self, user_data):
        # Turn off struts in case this unmap is in response to
        # changes in window options, force-to-top in particular.
        if config.is_docking_enabled():
            self.clear_struts()

        # untity starts onboard before the desktops
        # workarea has settled, reset it here on hiding,
        # as we know our struts are gone from this point.
        self.reset_monitor_workarea()

    def on_visibility_changed(self, visible):
        if not self._visible and visible and \
           not config.is_docking_enabled() and \
           not config.xid_mode:
            rect = self.get_current_rect()
            if not rect is None: # shouldn't happen, fix this
                self.move_resize(*rect) # sync position
                self.keyboard_widget.sync_transition_position(rect)

        KbdWindowBase.on_visibility_changed(self, visible)

    def _on_config_rect_changed(self):
        """ Gsettings position or size changed """
        if not config.xid_mode and \
           not config.is_docking_enabled():
            orientation = self.get_screen_orientation()
            rect = self.read_window_rect(orientation)

            # Only apply the new rect if it isn't the one we just wrote to
            # gsettings. Someone has to have manually changed the values
            # in gsettings to allow moving the window.
            rects = list(self._written_window_rects.values())
            if not any(rect == r for r in rects):
                self.restore_window_rect()

    def _on_config_dock_size_changed(self):
        """ Gsettings size changed """
        if not config.xid_mode and \
           config.is_docking_enabled():
            size = self.get_dock_size()

            # Only apply the new rect if it isn't the one we just wrote to
            # gsettings. Someone has to have manually changed the values
            # in gsettings to allow moving the window.
            sizes = list(self._written_dock_sizes.values())
            if not any(size == sz for sz in sizes):
                self.restore_window_rect()

    def on_user_positioning_begin(self):
        keyboard = self.keyboard_widget.keyboard

        self.stop_save_position_timer()
        self.stop_auto_positioning()
        keyboard.stop_raise_attempts()
        keyboard.auto_show_lock("user-positioning")

        self._user_positioning_begin_rect = self.get_rect()

    def on_user_positioning_done(self):
        self.update_window_rect()

        # self.detect_docking()
        if config.is_docking_enabled():
            self.write_docking_size(self.get_screen_orientation(),
                                    self.get_size())
            self.update_docking()
        else:
            # Attempt to filter out accidental resize frame clicks to prevent
            # losing the home position without clear user intention.
            proximity = 30
            rh = self.get_home_rect()
            began_near_home = \
                self._user_positioning_begin_rect.inflate(proximity) \
                .intersection(rh) == rh
            moved = self.keyboard_widget.was_moving()
            update_home_position = began_near_home or moved

            self.update_home_rect(update_home_position)

        # Thaw auto show only after a short delay to stop the window
        # from hiding due to spurios focus events after a system resize.
        keyboard = self.keyboard_widget.keyboard
        keyboard.auto_show_lock("user-positioning", 1.0)

    def detect_docking(self):
        if self.keyboard_widget.was_moving():
            config.window.docking_enabled = False

    def _on_configure_event(self, widget, event):
        KbdWindowBase._on_configure_event(self, widget, event)

        if not config.is_docking_enabled():
            # Connect_after seems broken in Quantal, but we still need to
            # get in after the default configure handler is done. Try to run
            # _on_configure_event_after in an idle handler instead.
            GLib.idle_add(self._on_configure_event_after, widget, event.copy())

    def _on_configure_event_after(self, widget, event):
        """
        Run this after KeyboardWidget's configure handler.
        After resizing Keyboard.update_layout() has to be called before
        limit_position() or the window jumps when it was close
        to the opposite screen edge of the resize handle.
        """
        # Configure event due to user positioning?
        result = self._filter_configure_event(self._window_rect)
        if result == 0:
            self.update_home_rect()

            # While moving with the title bar...
            if config.has_window_decoration():

                # stop auto-show from hiding the keyboard
                keyboard = self.keyboard_widget.keyboard
                keyboard.auto_show_lock("configure-event", 1.0)

                # cut off any leftover auto-show repositioning
                self.stop_auto_positioning()

                # keep the window active (LP: #1461823)
                self.keyboard_widget.touch_inactivity_timer()

    def _filter_configure_event(self, rect):
        """
        Returns 0 for detected user positioning/sizing.
        Multiple defenses against false positives, i.e.
        window movement by autoshow, screen rotation, whathaveyou.
        """

        # There is no user positioning in xembed mode.
        if config.xid_mode:
            return -1

        # There is no system provided way to move/resize in
        # force-to-top mode. Solely rely on on_user_positioning_done().
        if config.is_force_to_top():
            return -2

        # There is no user positioning for invisible windows.
        if not self.is_visible():
            return -3

        # There is no user positioning for iconified windows.
        if self.is_iconified():
            return -4

        # There is no user positioning for maximized windows.
        if self.is_maximized():
            return -5

        # Remember past n configure events.
        now = time.time()
        max_events = 4
        self._last_configures = self._last_configures[-(max_events - 1):]

        # Same rect as before?
        if len(self._last_configures) and \
           self._last_configures[-1][0] == rect:
            return 1

        self._last_configures.append([rect, now])

        # Only just started?
        if len(self._last_configures) < max_events:
            return 2

        # Did we just move the window by auto-show?
        if not self._last_ignore_configure_time is None and \
           time.time() - self._last_ignore_configure_time < 0.5:
            return 3

        # Is the new window rect one of our known ones?
        if self.is_known_rect(self._window_rect):
            return 4

        # Dragging the decorated frame doesn't produce continous
        # configure-events anymore as in Oneriric (Precise).
        # Disable all affected checks based on this.
        # The home rect will probably get lost occasionally.
        if not config.has_window_decoration():

            # Less than n configure events in the last x seconds?
            first = self._last_configures[0]
            intervall = now - first[1]
            if intervall > 1.0:
                return 5

            # Is there a jump > threshold in past positions?
            r0 = self._last_configures[-1][0]
            r1 = self._last_configures[-2][0]
            dx = r1.x - r0.x
            dy = r1.y - r0.y
            d2 = dx * dx + dy * dy
            if d2 > 50**2:
                self._last_configures = [] # restart
                return 6

        return 0

    def ignore_configure_events(self):
        self._last_ignore_configure_time = time.time()

    def remember_rect(self, rect):
        """
        Remember the last 3 rectangles of auto-show repositioning.
        Time and order of configure events is somewhat unpredictable,
        so don't rely only on a single remembered rect.
        """
        self._known_window_rects = self._known_window_rects[-3:]
        self._known_window_rects.append(rect)

        # Remembering the rects doesn't help if respositioning outside
        # of the work area in compiz with force-to-top mode disabled.
        # WM corrects window positions to fit into the viewable area.
        # -> add timing based block
        self.ignore_configure_events()

    def get_known_rects(self):
        """
        Return all rects that may have resulted from internal
        window moves, not from user controlled drag operations.
        """
        rects = list(self._known_window_rects)

        co = config.window.landscape
        rects.append(Rect(co.x, co.y, co.width, co.height))

        co = config.window.portrait
        rects.append(Rect(co.x, co.y, co.width, co.height))

        rects.append(self.home_rect)
        return rects

    def is_known_rect(self, rect):
        """
        The home rect should be updated in response to user positiong/resizing.
        However we are unable to detect the end of window movement/resizing
        when window decoration is enabled. Instead we check if the current
        window rect is different from the ones auto-show knows and assume
        the user has changed it in this case.
        """
        return any(rect == r for r in self.get_known_rects())

    def move_home_rect_into_view(self):
        """
        Make sure the home rect is valid, move it if necessary.
        This function may be called even if the window is invisible.
        """
        rect = self._window_rect.copy()
        x, y = rect.x, rect.y
        _x, _y = self.keyboard_widget.limit_position(x, y)
        if _x != x or _y != y:
            self.update_home_rect()

    def update_home_rect(self, update_home_position=True):
        if config.is_docking_enabled():
            return

        # update home rect
        rect = self._window_rect.copy()
        if not update_home_position:
            rh = self.home_rect
            rect.x = rh.x
            rect.y = rh.y

        # Make sure the move button stays visible
        if self.can_move_into_view():
            rect.x, rect.y = self.keyboard_widget.limit_position(rect.x, rect.y)

        self.home_rect = rect.copy()
        self.start_save_position_timer()

        # Make transitions aware of the new position,
        # undoubtedly reached by user positioning.
        # Else, window snaps back to the last transition position.
        self.keyboard_widget.sync_transition_position(rect)

    def get_home_rect(self):
        """
        Get the un-repositioned rect, the one auto-show falls back to
        when there is nowhere else to move.
        """
        if config.is_docking_enabled():
            rect = self.get_dock_rect()
        else:
            rect = self.home_rect
        return rect

    def get_visible_rect(self):
        """
        Returns the rect of the visible window rect with auto-show
        repositioning taken into account.
        """
        home_rect = self.get_home_rect()  # aware of docking
        rect = home_rect

        if config.is_auto_show_enabled():

            r = self.get_repositioned_window_rect(home_rect)
            if not r is None:
                rect = r

        return rect

    def auto_position(self):
        self._auto_position_started = True

        # Leave the first commit to AutoShow._begin_transition()
        self.update_position(False)

        # With docking enabled, when focusing the search entry of a
        # maximized firefox window, it changes position when the work
        # area shrinks and ends up below Onboard.
        # -> periodically update the window position for a little while,
        #    this way slow systems can catch up too eventually (Nexus 7).
        self._poll_auto_position_start_time = time.time()
        start_delay = 0.1
        self._auto_position_poll_timer.start(start_delay,
                                             self._on_auto_position_poll,
                                             start_delay)

    def _on_auto_position_poll(self, delay):
        self.update_position()

        # start another timer for progressively longer intervals
        delay = min(delay * 2.0, 1.0)
        if time.time() + delay < self._poll_auto_position_start_time + 3.0:
            self._auto_position_poll_timer.start(delay,
                                                 self._on_auto_position_poll,
                                                 delay)
            return True
        else:
            return False

    def stop_auto_positioning(self):
        self._auto_position_poll_timer.stop()

    def update_position(self, commit=True):
        home_rect = self.get_home_rect()
        rect = self.get_repositioned_window_rect(home_rect)
        if rect is None:
            # move back home
            rect = home_rect

        if self.get_position() != rect.get_position():
            self.keyboard_widget.transition_position_to(rect.x, rect.y)
            if commit:
                self.keyboard_widget.commit_transition()

    def get_repositioned_window_rect(self, home_rect):
        if config.can_auto_show_reposition():
            clearance = config.auto_show.widget_clearance
            test_clearance = clearance
            move_clearance = clearance
            limit_rects = None  # None: all monitors

            # No test clearance when docking. Make it harder to jump
            # out of the dock, for example for the bottom search box
            # in maximized firefox.
            if config.is_docking_enabled():
                test_clearance = (clearance[0], 0, clearance[2], 0)

                # limit the horizontal freedom to the docking monitor
                area, geom = self.get_docking_monitor_rects()
                limit_rects = [area]

            horizontal, vertical = self.get_repositioning_constraints()
            keyboard = self.keyboard_widget.keyboard
            rect = keyboard.get_auto_show_repositioned_window_rect( \
                                            self.keyboard_widget,
                                            home_rect, limit_rects,
                                            test_clearance, move_clearance,
                                            horizontal, vertical)
        else:
            rect = None

        return rect

    def reposition(self, x, y):
        """
        Move the window from a transition, not meant for user positioning.
        """
        # remember rects to distinguish from user move/resize
        w, h = self.get_size()
        self.remember_rect(Rect(x, y, w, h))

        # In Trusty, Compiz, floating window with force-to-top enabled,
        # this window's configure-event lies about the window position
        # after un-hiding by auto-show, which leads to jumping when
        # moving or resizing afterwards.
        #
        # Test case:
        # 1) Start onboard with auto-show enabled
        # 2) Focus text entry to show the keyboard
        # 3) Move the keyboard
        # 4) Unfocus all text entries to hide the keyboard
        # 5) Focus text entry to show the keyboard
        # 6) Move the keyboard
        #    -> keyboard jumps to the previous position
        #
        # Workaround: force position change to get new, hopefully
        #             correct configure-events
        if self._auto_position_started:
            self._auto_position_started = False
            if config.quirks.must_fix_configure_event():
                self.move(x-1, y)

        self.move(x, y)

    def get_repositioning_constraints(self):
        """
        Return allowed respositioning directions for auto-show.
        """
        if config.is_docking_enabled() and \
           self.get_dock_expand():
            return False, True
        else:
            return True, True

    def get_hidden_rect(self):
        """
        Returns the rect of the hidden window with auto-show
        repositioning taken into account.
        """
        if config.is_docking_enabled():
            return self.get_docking_hideout_rect()
        return self.get_visible_rect()

    def get_current_rect(self):
        """
        Returns the window rect with auto-show
        repositioning taken into account.
        """
        if self.is_visible():
            rect = self.get_visible_rect()
        else:
            rect = self.get_hidden_rect()
        return rect

    def on_restore_window_rect(self, rect):
        """
        Overload for WindowRectPersist.
        """
        if not config.is_docking_enabled():
            self.home_rect = rect.copy()

        # check for alternative auto-show position
        r = self.get_current_rect()
        if r != rect:
            # remember our rects to distinguish from user move/resize
            self.remember_rect(r)
            rect = r

        self.keyboard_widget.sync_transition_position(rect)
        return rect

    def on_save_window_rect(self, rect):
        """
        Overload for WindowRectPersist.
        """
        # Ignore <rect> (self._window_rect), it may just be a temporary one
        # set by auto-show. Save the user selected home_rect instead.
        return self.home_rect

    def read_window_rect(self, orientation):
        """
        Read orientation dependent rect.
        Overload for WindowRectPersist.
        """
        if orientation == Orientation.LANDSCAPE:
            co = config.window.landscape
        else:
            co = config.window.portrait

        # limit size due to LP #1633284, gsettings values might be negative
        min_width, min_height = self.get_min_window_size()
        rect = Rect(co.x,
                    co.y,
                    max(co.width, min_width),
                    max(co.height, min_height))
        return rect

    def write_window_rect(self, orientation, rect):
        """
        Write orientation dependent rect.
        Overload for WindowRectPersist.
        """
        # There are separate rects for normal and rotated screen (tablets).
        if orientation == Orientation.LANDSCAPE:
            co = config.window.landscape
        else:
            co = config.window.portrait

        # remember that we wrote this rect to gsettings
        self._written_window_rects[orientation] = rect.copy()

        # write to gsettings and trigger notifications
        co.delay()
        co.x, co.y, co.width, co.height = rect
        co.apply()

    def write_docking_size(self, orientation, size):
        co = self.get_orientation_config_object()
        expand = self.get_dock_expand()

        # remember that we wrote this rect to gsettings
        self._written_dock_sizes[orientation] = tuple(size)

        # write to gsettings and trigger notifications
        co.delay()
        if not expand:
            co.dock_width = size[0]
        co.dock_height = size[1]
        co.apply()

    def get_orientation_config_object(self):
        orientation = self.get_screen_orientation()
        if orientation == Orientation.LANDSCAPE:
            co = config.window.landscape
        else:
            co = config.window.portrait
        return co

    def on_transition_done(self, visible_before, visible_now):
        if visible_now:
            self.assure_on_current_desktop()
            self.update_docking()

    def on_screen_size_changed(self, screen):
        """ Screen rotation, etc. """
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("on_screen_size_changed",
                          screen.width(), screen.height())

        if config.is_docking_enabled():
            # Can't correctly position the window while struts are active
            # -> turn them off for a moment
            self.clear_struts()

            # Attempt to hide the keyboard now. This won't work that well
            # as the system doesn't refresh the screen anymore until
            # after the rotation.
            self._was_visible = self.is_visible()
            self._screen_resizing = True
            keyboard_widget = self.keyboard_widget
            if keyboard_widget:
                keyboard_widget.transition_visible_to(False, 0.0)
                keyboard_widget.commit_transition()

        WindowRectPersist.on_screen_size_changed(self, screen)

    def on_screen_size_changed_delayed(self, screen):
        if config.is_docking_enabled():
            self._screen_resizing = False
            self.reset_monitor_workarea()

            # The keyboard size may have changed, draw with the new size now,
            # while it's still in the hideout, so we don't have to watch.
            self.restore_window_rect()
            self.keyboard_widget.process_updates()

            keyboard_widget = self.keyboard_widget
            if keyboard_widget and self._was_visible:
                keyboard_widget.transition_visible_to(True, 0.0, 0.4)
                keyboard_widget.commit_transition()
        else:
            self.restore_window_rect()

    def limit_size(self, rect):
        """
        Limits the given window rect to fit on screen.
        """
        if self.keyboard_widget:
            return self.keyboard_widget.limit_size(rect)
        return rect

    def _on_delete_event(self, event, data=None):
        if config.lockdown.disable_quit:
            if self.keyboard_widget:
                return True
        else:
            self.emit_quit_onboard()

    def on_docking_notify(self):
        self.update_docking()
        self.keyboard_widget.update_window_handles()

    def update_docking(self, force_update = False):
        enable = config.is_docking_enabled()
        if enable:
            rect = self.get_dock_rect()
        else:
            rect = Rect()
        shrink = config.window.docking_shrink_workarea
        expand = self.get_dock_expand()
        edge = config.window.docking_edge
        monitor = self.get_docking_monitor(True)

        if self._docking_enabled != enable or \
           (self._docking_enabled and \
            (self._docking_rect != rect or \
             self._shrink_work_area != shrink or \
             self._dock_expand != expand or \
             bool(self._current_struts) != shrink or
             self._docking_monitor != monitor)
           ):
            self._current_docking_monitor = monitor

            self._realize_docking(enable)

            self._shrink_work_area = shrink
            self._dock_expand = expand
            self._docking_edge = edge
            self._docking_monitor = monitor
            self._docking_enabled = enable
            self._docking_rect = rect

    def _realize_docking(self, enable):
        if enable:
            self._set_docking_struts(config.window.docking_shrink_workarea,
                                     config.window.docking_edge,
                                     self.get_dock_expand())
            self.restore_window_rect() # knows about docking
            self._realize_docking_time = time.time()
        else:
            self.restore_window_rect()
            self.clear_struts()
            self._realize_docking_time = None


    def update_docking_monitor(self):
        mon_before = self._current_docking_monitor
        mon_now = self.get_docking_monitor(True)
        self._current_docking_monitor = mon_now
        return mon_before != mon_now

    def clear_struts(self):
        self._set_docking_struts(False)

    def _set_docking_struts(self, enable, edge = None, expand = True):
        if not self.get_realized():
            # no window, no xid
            return

        win = self.get_window()
        try:
            xid = win.get_xid()  # requires GdkX11 import
        except AttributeError:
            # docking unavailable on Wayland
            return

        if not enable:
            self._apply_struts(xid, None)
            return

        area, geom = self.get_docking_monitor_rects()
        root = self.get_rootwin_rect()

        rect = self.get_dock_rect()
        top_start_x = top_end_x = 0
        bottom_start_x = bottom_end_x = 0

        if edge: # Bottom
            top    = 0
            bottom = geom.h - area.bottom() + rect.h
            #bottom = root.h - area.bottom() + rect.h
            bottom_start_x = rect.left()
            bottom_end_x   = rect.right() - 1
        else:    # Top
            top    = area.top() + rect.h
            bottom = 0
            top_start_x = rect.left()
            top_end_x   = rect.right() - 1


        struts = [0, 0, top, bottom, 0, 0, 0, 0,
                  top_start_x, top_end_x, bottom_start_x, bottom_end_x]

        scale = config.window_scaling_factor
        if scale != 1.0:
            struts = [val * scale for val in struts]

        self._apply_struts(xid, struts)

    def _apply_struts(self, xid, struts = None):
        if self._current_struts != struts:
            if struts is None:
                self._osk_struts.clear(xid)
            else:
                self._osk_struts.set(xid, struts)
            self._current_struts = struts

    def get_dock_size(self):
        co = self.get_orientation_config_object()
        return co.dock_width, co.dock_height

    def get_dock_expand(self):
        co = self.get_orientation_config_object()
        return co.dock_expand

    def get_dock_rect(self):
        area, geom = self.get_docking_monitor_rects()
        edge = config.window.docking_edge

        width, height = self.get_dock_size()
        rect = Rect(area.x, 0, area.w, height)
        if edge: # Bottom
            rect.y = area.y + area.h - height
        else:    # Top
            rect.y = area.y

        expand = self.get_dock_expand()
        if expand:
            rect.w = area.w
            rect.x = area.x
        else:
            rect.w = min(width, area.w)
            rect.x = rect.x + (area.w - rect.w) // 2
        return rect

    def get_docking_hideout_rect(self, reference_rect = None):
        """ Where the keyboard goes to hide when it slides off-screen. """
        area, geom = self.get_docking_monitor_rects()
        rect = self.get_dock_rect()
        hideout = rect

        mcx, mcy = geom.get_center()
        if reference_rect:
            cx, cy = reference_rect.get_center()
        else:
            cx, cy = rect.get_center()
        clearance = 10
        if cy > mcy:
            hideout.y = geom.bottom() + clearance  # below Bottom
        else:
            hideout.y = geom.top() - rect.h - clearance # above Top

        return hideout

    def get_docking_monitor_rects(self):
        screen = self.get_screen()
        monitor = self.get_docking_monitor()

        area = self._monitor_workarea.get(monitor)
        if area is None:
            area = self.update_monitor_workarea(monitor)

        geom = screen.get_monitor_geometry(monitor)
        geom = Rect(geom.x, geom.y, geom.width, geom.height)

        return area, geom

    def get_docking_monitor(self, force_update=False):
        if self._current_docking_monitor is None or force_update:
            screen = self.get_screen()
            docking_monitor = config.window.docking_monitor

            monitor = int(docking_monitor)
            if monitor < 100:
                if monitor < 0 or \
                   monitor >= screen.get_n_monitors():
                    docking_monitor = DockingMonitor.PRIMARY

            if docking_monitor == DockingMonitor.ACTIVE:
                monitor = self._get_monitor_at_active_window(screen)
            elif docking_monitor == DockingMonitor.PRIMARY:
                monitor = screen.get_primary_monitor()
        else:
            monitor = self._current_docking_monitor
        return monitor

    @staticmethod
    def _get_monitor_at_active_window(screen):
        window = screen.get_active_window()
        if window:
            # Sometimes causes X error "BadDrawable" in Vivid for Francesco
            Gdk.error_trap_push()
            monitor = screen.get_monitor_at_window(window)
            if Gdk.error_trap_pop():
                monitor = screen.get_primary_monitor()
        else:
            monitor = screen.get_primary_monitor()
        return monitor

    def reset_monitor_workarea(self):
        self._monitor_workarea = {}

    def update_monitor_workarea(self, monitor):
        """
        Save the workarea, so we don't have to
        check all the time if our strut is already installed.
        """
        area = self.get_monitor_workarea(monitor)
        self._monitor_workarea[monitor] = area
        return area

    def get_monitor_workarea(self, monitor_index):
        display = Gdk.Display.get_default()
        try:
            # From Zesty, new in Gtk 3.22
            monitor = display.get_monitor(monitor_index)
            area = monitor.get_workarea()
        except AttributeError:
            # Around Xenial and below, deprecated and returning unexpected values in Zesty
            screen = self.get_screen()
            area = screen.get_monitor_workarea(monitor_index)

        area = Rect(area.x, area.y, area.width, area.height)
        return area

    @staticmethod
    def get_rootwin_rect():
        rootwin = Gdk.get_default_root_window()
        return Rect.from_position_size(rootwin.get_position(),
                                (rootwin.get_width(), rootwin.get_height()))

    def assure_on_current_desktop(self):
        """
        Make sure the window is visible in the current desktop in OpenBox and
        perhaps other WMs, except Compiz/Unity. Dbus Show() then makes Onboard
        appear after switching desktop (LP: 1092166, Raring).
        """
        if self._moved_desktop_switch_count != self._desktop_switch_count:
            win = self.get_window()
            if win:
                win.move_to_current_desktop()
                self._moved_desktop_switch_count = self._desktop_switch_count

    def raise_to_top(self):
        win = self.get_window()
        if win and self.is_visible():
            win.raise_()


class KbdPlugWindow(KbdWindowBase, WindowRectTracker, Gtk.Plug):
    def __init__(self, keyboard_widget, icp = None):
        Gtk.Plug.__init__(self)
        WindowRectTracker.__init__(self)
        KbdWindowBase.__init__(self, keyboard_widget, icp)

    def _on_configure_event(self, widget, event):
        KbdWindowBase._on_configure_event(self, widget, event)

    def toggle_visible(self):
        pass


class WMQuirksDefault:
    """ Miscellaneous window managers, no special quirks """
    wms = ()

    @staticmethod
    def set_visible(window, visible):
        if window.is_iconified():
            if visible and \
               not config.xid_mode:
                window.deiconify()
                window.present()
        else:
            Gtk.Window.set_visible(window, visible)

    @staticmethod
    def update_taskbar_hint(window):
        window.set_skip_taskbar_hint(True)

    @staticmethod
    def get_window_type_hint(window):
        if config.is_docking_enabled():
            return Gdk.WindowTypeHint.DOCK
        else:
            return Gdk.WindowTypeHint.NORMAL

    @staticmethod
    def can_set_override_redirect(window):
        # struts fail for override redirect windows in metacity and mutter
        return not config.is_docking_enabled()

    @staticmethod
    def must_fix_configure_event():
        """ does configure-event need fixing? """
        return False

class WMQuirksCompiz(WMQuirksDefault):
    """ Unity with Compiz """
    wms = ("compiz")

    @staticmethod
    def get_window_type_hint(window):
        if config.is_docking_enabled():
            # repel unity MT touch handles with DOCK, but we have OR enabled now
            return Gdk.WindowTypeHint.NORMAL
        else:
            if config.is_force_to_top():
                # NORMAL keeps Onboard on top of fullscreen firefox (LP: 1035578)
                return Gdk.WindowTypeHint.NORMAL
            else:
                if config.window.window_decoration:
                    # Keep showing the minimize button
                    return Gdk.WindowTypeHint.NORMAL
                else:
                    # don't get resized by compiz's grid plugin (LP: 893644)
                    return Gdk.WindowTypeHint.UTILITY

    @staticmethod
    def can_set_override_redirect(window):
        # Can't type into unity dash with keys below dash without
        # override redirect. -> turn it on even when docking. Apparently
        # Compiz can handle struts with OR windows.
        return True

    @staticmethod
    def must_fix_configure_event():
        return config.is_force_to_top() and \
            not config.xid_mode # in Trusty


class WMQuirksMutter(WMQuirksDefault):
    """ Gnome-shell """

    wms = ("mutter", "GNOME Shell".lower())

    @staticmethod
    def set_visible(window, visible):
        if window.is_iconified() and visible:
            # When minimized, Mutter doesn't react when asked to
            # remove WM_STATE_HIDDEN. Once the window was minimized
            # by title bar button it cannot be unhidden by auto-show.
            # The only workaround I found is re-mapping it (Precise).
            window.unmap()
            window.map()

        WMQuirksDefault.set_visible(window, visible)


class WMQuirksMetacity(WMQuirksDefault):
    """ Unity-2d, Gnome Classic """

    wms = ("metacity")

    @staticmethod
    def set_visible(window, visible):
        # Metacity is good at iconifying. Take advantage of that
        # and get onboard minimized to the task list when possible.
        if not config.xid_mode and \
           not config.is_force_to_top() and \
           not config.has_unhide_option():
            if visible:
                window.deiconify()
                window.present()
            else:
                window.iconify()
        else:
            WMQuirksDefault.set_visible(window, visible)

    @staticmethod
    def update_taskbar_hint(window):
        window.set_skip_taskbar_hint(config.xid_mode or \
                                     config.is_force_to_top() or \
                                     config.has_unhide_option())

