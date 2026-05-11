# -*- coding: utf-8 -*-

# Copyright © 2009 Chris Jones <tortoise@tortuga>
# Copyright © 2012 Gerd Kohlberger <lowfi@chello.at>
# Copyright © 2009, 2011-2017 marmuta <marmvta@gmail.com>
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

""" GTK keyboard widget """

from __future__ import division, print_function, unicode_literals

import sys
import time
from math import sin, pi
from gi.repository          import GLib, Gdk, Gtk

from Onboard.Version import require_gi_versions
require_gi_versions()

from Onboard.TouchInput     import TouchInput, InputSequence
from Onboard.Keyboard       import EventType
from Onboard.KeyboardPopups import LayoutPopup, KeyboardPopup, \
                                   LayoutBuilderAlternatives, \
                                   LayoutBuilder
from Onboard.KeyGtk         import Key
from Onboard.KeyCommon      import LOD
from Onboard.TouchHandles   import TouchHandles
from Onboard.LayoutView     import LayoutView
from Onboard.utils          import Rect, escape_markup
from Onboard.Timer          import Timer, FadeTimer
from Onboard.definitions    import Handle, HandleFunction
from Onboard.WindowUtils    import WindowManipulator, \
                                   canvas_to_root_window_rect, \
                                   canvas_to_root_window_point, \
                                   get_monitor_dimensions

### Logging ###
import logging
_logger = logging.getLogger("KeyboardWidget")
###############

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

# prepare mask for faster access
BUTTON123_MASK = Gdk.ModifierType.BUTTON1_MASK | \
                 Gdk.ModifierType.BUTTON2_MASK | \
                 Gdk.ModifierType.BUTTON3_MASK

class AutoReleaseTimer(Timer):
    """
    Releases latched and locked modifiers after a period of inactivity.
    Inactivity here means no keys are pressed.
    """
    _keyboard = None

    def __init__(self, keyboard):
        self._keyboard = keyboard

    def start(self, visibility_change = None):
        self.stop()
        delay = config.keyboard.sticky_key_release_delay
        if visibility_change == False:
            hide_delay = config.keyboard.sticky_key_release_on_hide_delay
            if hide_delay:
                if delay:
                    delay = min(delay, hide_delay)
                else:
                    delay = hide_delay
        if delay:
            Timer.start(self, delay)

    def on_timer(self):
        # When sticky_key_release_delay is set, release NumLock too.
        # We then assume Onboard is used in a kiosk setting, and
        # everything has to be reset for the next customer.
        release_all_keys = bool(config.keyboard.sticky_key_release_delay)
        if release_all_keys:
            config.word_suggestions.set_pause_learning(0)
        self._keyboard.release_latched_sticky_keys()
        self._keyboard.release_locked_sticky_keys(release_all_keys)
        self._keyboard.set_active_layer_id(None)
        self._keyboard.invalidate_ui_no_resize()
        self._keyboard.commit_ui_updates()
        return False


class InactivityTimer(Timer):
    """
    Waits for the inactivity delay and transitions between
    active and inactive state.
    Inactivity here means, the pointer has left the keyboard window.
    """
    _keyboard = None
    _active = False

    def __init__(self, keyboard):
        self._keyboard = keyboard

    def is_enabled(self):
        window = self._keyboard.get_kbd_window()
        if not window:
            return False
        screen = window.get_screen()
        return screen and  screen.is_composited() and \
               config.is_inactive_transparency_enabled() and \
               config.window.enable_inactive_transparency and \
               not config.xid_mode

    def is_active(self):
        return self._active

    def begin_transition(self, active):
        self._active = active
        if active:
            Timer.stop(self)
            if self._keyboard.transition_active_to(True):
                self._keyboard.commit_transition()
        else:
            if not config.xid_mode:
                Timer.start(self, config.window.inactive_transparency_delay)

    def on_timer(self):
        self._keyboard.transition_active_to(False)
        self._keyboard.commit_transition()
        return False


class HideInputLineTimer(Timer):
    """
    Temporarily hides the input line when the pointer touches it.
    """
    def __init__(self, keyboard):
        self._keyboard = keyboard

    def handle_motion(self, sequence):
        """
        Handle pointer motion.
        """
        point = sequence.point

        # Hide inputline when the pointer touches it.
        # Show it again when leaving the area.
        for key in self._keyboard.get_text_displays():
            rect = key.get_canvas_border_rect()
            if rect.is_point_within(point):
                if not self.is_running():
                    self.start(0.3)
            else:
                self.stop()
                self._keyboard.hide_input_line(False)

    def on_timer(self):
        """ Hide the input line after delay """
        self._keyboard.hide_input_line(True)
        return False


class TransitionVariable:
    """ A variable taking part in opacity transitions """

    value        = 0.0
    start_value  = 0.0
    target_value = 0.0
    start_time   = 0.0
    duration     = 0.0
    done         = False

    def start_transition(self, target, duration):
        """ Begin transition """
        self.start_value = self.value
        self.target_value = target
        self.start_time = time.time()
        self.duration = duration
        self.done = False

    def update(self):
        """
        Update self.value based on the elapsed time since start_transition.
        """
        range = self.target_value - self.start_value
        if range and self.duration:
            elapsed  = time.time() - self.start_time
            lin_progress = min(1.0, elapsed / self.duration)
        else:
            lin_progress = 1.0
        sin_progress = (sin(lin_progress * pi - pi / 2.0) + 1.0) / 2.0
        self.value = self.start_value + sin_progress * range
        self.done = lin_progress >= 1.0


class TransitionState:
    """ Set of all state variables involved in opacity transitions. """

    def __init__(self):
        self.visible = TransitionVariable()
        self.active  = TransitionVariable()
        self.x       = TransitionVariable()
        self.y       = TransitionVariable()
        self._vars = [self.visible, self.active, self.x, self.y]

        self.target_visibility = False

    def update(self):
        for var in self._vars:
            var.update()

    def is_done(self):
        return all(var.done for var in self._vars)

    def get_max_duration(self):
        return max(x.duration for x in self._vars)


class WindowManipulatorAspectRatio(WindowManipulator):
    """ Adds support for handles with function ASPECT_RATIO. """

    def __init__(self):
        WindowManipulator.__init__(self)
        self._docking_aspect_change_range = \
            config.window.docking_aspect_change_range

    def update_docking_aspect_change_range(self):
        """ GSettings key changed """
        value = config.window.docking_aspect_change_range
        if self._docking_aspect_change_range != value:
            self._docking_aspect_change_range = value
            self.keyboard.invalidate_ui()
            self.keyboard.commit_ui_updates()

    def get_docking_aspect_change_range(self):
        return self._docking_aspect_change_range

    def on_drag_done(self):
        config.window.docking_aspect_change_range = \
            self._docking_aspect_change_range

    def on_handle_aspect_ratio_pressed(self):
        self._drag_start_keyboard_frame_rect = self.get_keyboard_frame_rect()

    def on_handle_aspect_ratio_motion(self, dx, dy):
        keyboard_frame_rect = self._drag_start_keyboard_frame_rect

        base_aspect_rect = self.get_base_aspect_rect()
        base_aspect = base_aspect_rect.w / base_aspect_rect.h
        start_frame_width = self._drag_start_keyboard_frame_rect.w
        new_frame_width = start_frame_width + dx * 2

        # snap to screen sides
        if new_frame_width >= self.canvas_rect.w * (1.0 - 0.05):
            new_aspect_change = 100.0
        else:
            new_aspect_change = \
                new_frame_width / (keyboard_frame_rect.h * base_aspect)

        # limit to minimum combined aspect
        min_aspect = 0.75
        new_aspect = base_aspect * new_aspect_change
        if new_aspect < min_aspect:
            new_aspect_change = min_aspect / base_aspect

        self._docking_aspect_change_range = \
            (self._docking_aspect_change_range[0], new_aspect_change)

        self.update_layout()
        self.update_touch_handles_positions()
        self.invalidate_for_resize(self._lod)
        self.redraw()


class KeyboardWidget(Gtk.DrawingArea, WindowManipulatorAspectRatio,
                     LayoutView, TouchInput):

    TRANSITION_DURATION_MOVE = 0.25
    TRANSITION_DURATION_SLIDE = 0.25
    TRANSITION_DURATION_OPACITY_HIDE = 0.3

    def __init__(self, keyboard):
        Gtk.DrawingArea.__init__(self)
        WindowManipulatorAspectRatio.__init__(self)
        LayoutView.__init__(self, keyboard)
        TouchInput.__init__(self)

        self.set_app_paintable(True)

        self.canvas_rect = Rect()
        self._opacity = 1.0

        self._last_click_time = 0
        self._last_click_key = None

        self._outside_click_timer = Timer()
        self._outside_click_detected = False
        self._outside_click_num = 0
        self._outside_click_button_mask = 0
        self._outside_click_start_time = None

        self._long_press_timer = Timer()
        self._auto_release_timer = AutoReleaseTimer(keyboard)
        self._key_popup = None

        self.dwell_timer = None
        self.dwell_key = None
        self.last_dwelled_key = None

        self.inactivity_timer = InactivityTimer(self)

        self.touch_handles = TouchHandles()
        self.touch_handles_hide_timer = Timer()
        self.touch_handles_fade = FadeTimer()
        self.touch_handles_auto_hide = True

        self._window_aspect_ratio = None

        self._hide_input_line_timer = HideInputLineTimer(keyboard)

        self._transition_timer = Timer()
        self._transition_state = TransitionState()
        self._transition_state.visible.value = 0.0
        self._transition_state.active.value = 1.0
        self._transition_state.x.value = 0.0
        self._transition_state.y.value = 0.0

        self._configure_timer = Timer()

        self._language_menu = LanguageMenu(self)
        self._suggestion_menu = SuggestionMenu(self)
        self._symbol_search_popup = None

        #self.set_double_buffered(False)
        self.set_app_paintable(True)

        # no tooltips when embedding, gnome-screen-saver flickers (Oneiric)
        if not config.xid_mode:
            self.set_has_tooltip(True) # works only at window creation -> always on

        self.connect("parent-set",           self._on_parent_set)
        self.connect("draw",                 self._on_draw)
        self.connect("query-tooltip",        self._on_query_tooltip)
        self.connect("configure-event",      self._on_configure_event)

        self._update_double_click_time()

        self._hovered_key = False
        # Pointer recovery state
        self._pointer_poll_id = None
        self._last_pointer_inside = None
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", self.on_mouse_leave)
        self.set_events(Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK |
                        self.get_events())
        
        self.show()
        
    def on_mouse_leave(self, widget, event):
        if self._hovered_key:
            self._hovered_key.hover = False
            self._hovered_key.invalidate_key()
            self._hovered_key = None
        self.queue_draw()

    def on_motion(self, widget, event):
        top_key = self.get_key_at_location((int(event.x), int(event.y)))

        if self._hovered_key:
            self._hovered_key.hover = False
            self._hovered_key.invalidate_key()
        if top_key:
            top_key.hover = True
            top_key.invalidate_key()
        else:
            self._hovered_key = False

        self._hovered_key = top_key
        self.queue_draw()

    def cleanup(self):

        # Enter-notify isn't called when resizing without crossing into
        # the window again. Do it here on exit, at the latest, to make sure
        # the home_rect is updated before is is saved later.
        self.stop_system_drag()

        # stop timer callbacks for unused, but not yet destructed keyboards
        self.touch_handles_fade.stop()
        self.touch_handles_hide_timer.stop()
        self._transition_timer.stop()
        self.inactivity_timer.stop()
        self._long_press_timer.stop()
        self._auto_release_timer.stop()
        self.stop_click_polling()
        self._configure_timer.stop()
        self.close_key_popup()

        # free xserver memory
        self.invalidate_keys()
        self.invalidate_shadows()

        LayoutView.cleanup(self)
        TouchInput.cleanup(self)

    def on_layout_loaded(self):
        """ called when the layout has been loaded """
        LayoutView.on_layout_loaded(self)

    def _on_parent_set(self, widget, old_parent):
        win = self.get_kbd_window()
        if win:
            self.touch_handles.set_window(win)
            self.update_window_handles()

    def set_opacity(self, opacity):
        """ Override deprecated Gtk function of the same name """
        if self._opacity != opacity:
            self._opacity = opacity
            self.redraw()

    def get_opacity(self):
        """ Override deprecated Gtk function of the same name """
        return self._opacity

    def set_startup_visibility(self):
        win = self.get_kbd_window()
        assert(win)

        # Show the keyboard when turning off auto-show.
        # Hide the keyboard when turning on auto-show.
        #   (Fix this when we know how to get the active accessible)
        # Hide the keyboard on start when start-minimized is set.
        # Start with active transparency if the inactivity_timer is enabled.
        #
        # start_minimized            False True  False True
        # auto_show                  False False True  True
        # --------------------------------------------------
        # window visible on start    True  False False False

        visible = config.is_visible_on_start()

        # Start with low opacity to stop opacity flashing
        # when inactive transparency is enabled.
        screen = self.get_screen()
        if screen and screen.is_composited() and \
           self.inactivity_timer.is_enabled():
            self.set_opacity(0.05)  # keep it slightly visible just in case

        # transition to initial opacity
        self.transition_visible_to(visible, 0.0, 0.4)
        self.transition_active_to(True, 0.0)
        self.commit_transition()

        # kick off inactivity timer, i.e. inactivate on timeout
        if self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(False)

        # Be sure to initially show/hide window and icon palette
        win.set_visible(visible)

    def pre_render_keys(self, window, w, h):
        if self.is_new_layout_size(w, h):
            self.update_layout(Rect(0, 0, w, h))

            self.invalidate_for_resize()

            win = window.get_window()
            if win:
                context = win.cairo_create()
                self.render(context)

    def is_new_layout_size(self, w, h):
        return self.canvas_rect.w != w or \
               self.canvas_rect.h != h

    def get_canvas_content_rect(self):
        """ Canvas rect excluding resize frame """
        return self.canvas_rect.deflate(self.get_frame_width())

    def get_base_aspect_rect(self):
        """ Rect with aspect ratio of the layout as defined in the SVG file """
        layout = self.get_layout()
        if not layout:
            return Rect(0, 0, 1.0, 1.0)
        return layout.context.log_rect

    def update_layout(self, canvas_rect=None):
        layout = self.get_layout()
        if not layout:
            return

        # recalculate item rectangles
        if canvas_rect is None:
            self.canvas_rect = Rect(0, 0,
                                    self.get_allocated_width(),
                                    self.get_allocated_height())
        else:
            self.canvas_rect = canvas_rect

        rect = self.get_canvas_content_rect()

        layout.update_log_rects()  # update logical tree to base aspect ratio
        rect = self._get_aspect_corrected_layout_rect(
            rect, self.get_base_aspect_rect())
        layout.do_fit_inside_canvas(rect)  # update contexts to final aspect

        # update the aspect ratio of the main window
        self.on_layout_updated()

    def _get_aspect_corrected_layout_rect(self, rect, base_aspect_rect):
        """
        Aspect correction specifically targets xembedding in unity-greeter
        and gnome-screen-saver. Else we would potentially disrupt embedding
        in existing kiosk applications.
        """
        orientation_co = self.get_kbd_window().get_orientation_config_object()
        keep_aspect = config.is_keep_frame_aspect_ratio_enabled(orientation_co)
        xembedding = config.xid_mode
        unity_greeter = config.launched_by == config.LAUNCHER_UNITY_GREETER

        x_align = 0.5
        aspect_change_range = (0, 100)

        if keep_aspect:
            if xembedding:
                aspect_change_range = config.get_xembed_aspect_change_range()
            elif (config.is_docking_enabled() and
                  config.is_dock_expanded(orientation_co)):
                aspect_change_range = self.get_docking_aspect_change_range()

            ra = rect.resize_to_aspect_range(base_aspect_rect,
                                             aspect_change_range)
            if xembedding and \
               unity_greeter:
                padding = rect.w - ra.w
                offset = config.get_xembed_unity_greeter_offset_x()
                # Attempt to left align to unity-greeters password box,
                # but use the whole width on small screens.
                if offset is not None \
                   and padding > 2 * offset:
                    rect.x += offset
                    rect.w -= offset
                    x_align = 0.0

            rect = rect.align_rect(ra, x_align)

        return rect

    def update_window_handles(self):
        """ Tell WindowManipulator about the active resize handles """
        docking = config.is_docking_enabled()

        # frame handles
        WindowManipulator.set_drag_handles(self, self._get_active_drag_handles())
        WindowManipulator.lock_x_axis(self, docking)

        # touch handles
        self.touch_handles.set_active_handles(self._get_active_drag_handles(True))
        self.touch_handles.lock_x_axis(docking)

    def update_transparency(self):
        """
        Updates transparencies in response to user action.
        Temporarily presents the window with active transparency when
        inactive transparency is enabled.
        """
        self._start_pointer_polling()
        self.transition_active_to(True)
        self.commit_transition()
        if self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(False)
        else:
            self.inactivity_timer.stop()
        self.redraw() # for background transparency

    def touch_inactivity_timer(self):
        """ extend active transparency, kick of inactivity_timer """
        if self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(True)
            self.inactivity_timer.begin_transition(False)

    def update_inactive_transparency(self):
        if self.inactivity_timer.is_enabled():
            self.transition_active_to(False)
            self.commit_transition()

    def _update_double_click_time(self):
        """ Scraping the bottom of the barrel to speed up key presses """
        self._double_click_time = Gtk.Settings.get_default() \
                        .get_property("gtk-double-click-time")

    def transition_visible_to(self, visible, opacity_duration = None,
                                             slide_duration = None):
        result = False
        state = self._transition_state
        win = self.get_kbd_window()

        # hide popup
        if not visible:
            self.close_key_popup()

        # bail in xembed mode
        if config.xid_mode:
            return False

        # stop reposition updates when we're hiding anyway
        if win and not visible:
            win.stop_auto_positioning()

        if config.is_docking_enabled():
            if slide_duration is None:
                slide_duration = self.TRANSITION_DURATION_SLIDE
            opacity_duration = 0.0
            opacity_visible = True

            if win:
                visible_before = win.is_visible()
                visible_later  = visible
                hideout_old_mon = win.get_docking_hideout_rect()
                mon_changed = win.update_docking_monitor()
                hideout_new_mon = win.get_docking_hideout_rect() \
                                if mon_changed else hideout_old_mon

                # Only position here if visibility or the active monitor
                # changed. Leave it to auto_position to move the keyboard
                # while it is visible, i.e. not being hidden or shown.
                if visible_before != visible_later or \
                   mon_changed:
                    if visible:
                        begin_rect = hideout_new_mon
                        end_rect = win.get_visible_rect()
                    else:
                        begin_rect = win.get_rect()
                        end_rect = hideout_old_mon

                    state.y.value = begin_rect.y
                    y             = end_rect.y
                    state.x.value = begin_rect.x
                    x             = end_rect.x

                    result |= self._init_transition(state.x, x, slide_duration)
                    result |= self._init_transition(state.y, y, slide_duration)
        else:
            opacity_visible  = visible

        if opacity_duration is None:
            if opacity_visible:
                # No duration when showing. Don't fight with compiz in unity.
                opacity_duration = 0.0
            else:
                opacity_duration = self.TRANSITION_DURATION_OPACITY_HIDE

        result |= self._init_opacity_transition(state.visible, opacity_visible,
                                                opacity_duration)
        state.target_visibility = visible

        return result

    def transition_active_to(self, active, duration = None):
        """
        Transition active state for inactivity timer.
        This ramps up/down the window opacity.
        """
        # not in xembed mode
        if config.xid_mode:
            return False

        if duration is None:
            if active:
                duration = 0.15
            else:
                duration = 0.3
        return self._init_opacity_transition(self._transition_state.active,
                                             active, duration)

    def transition_position_to(self, x, y):
        result = False
        state = self._transition_state
        duration = self.TRANSITION_DURATION_MOVE

        # not in xembed mode
        if config.xid_mode:
            return False

        win = self.get_kbd_window()
        if win:
            begin_rect = win.get_rect()
            state.y.value = begin_rect.y
            state.x.value = begin_rect.x

        result |= self._init_transition(state.x, x, duration)
        result |= self._init_transition(state.y, y, duration)

        return result

    def sync_transition_position(self, rect):
        """
        Update transition variables with the actual window position.
        Necessary on user positioning.
        """
        state = self._transition_state
        state.y.value        = rect.y
        state.x.value        = rect.x
        state.y.target_value = rect.y
        state.x.target_value = rect.x

    def _init_opacity_transition(self, var, target_value, duration):

        # No fade delay for screens that can't fade (unity-2d)
        screen = self.get_screen()
        if screen and not screen.is_composited():
            duration = 0.0

        target_value = 1.0 if target_value else 0.0

        return self._init_transition(var, target_value, duration)

    def _init_transition(self, var, target_value, duration):
        # Transition not yet in progress?
        if var.target_value != target_value:
            var.start_transition(target_value, duration)
            return True
        return False

    def commit_transition(self):
        # not in xembed mode
        if config.xid_mode:
            return

        duration = self._transition_state.get_max_duration()
        if duration == 0.0:
            self._on_transition_step()
        else:
            self._transition_timer.start(0.02, self._on_transition_step)

    def _on_transition_step(self):
        state = self._transition_state
        state.update()

        done              = state.is_done()

        active_opacity    = config.window.get_active_opacity()
        inactive_opacity  = config.window.get_inactive_opacity()
        invisible_opacity = 0.0

        opacity  = inactive_opacity + state.active.value * \
                   (active_opacity - inactive_opacity)
        opacity *= state.visible.value

        window = self.get_kbd_window()
        if window:
            self.set_opacity(opacity)
        # Pointer recovery activation if transparent
        if opacity < 0.99:
            self._start_pointer_polling()
        else:
            self._stop_pointer_polling()

        visible_before = window.is_visible()
        visible_later  = state.target_visibility

        # move
        x = int(state.x.value)
        y = int(state.y.value)
        wx, wy = window.get_position()
        if x != wx or y != wy:
            window.reposition(x, y)

        # show/hide
        visible = (visible_before or visible_later) and not done or \
                  visible_later and done
        if window.is_visible() != visible:
                window.set_visible(visible)

                # on_leave_notify does not start the inactivity timer
                # while the pointer remains inside of the window. Do it
                # here when hiding the window.
                if not visible and \
                   self.inactivity_timer.is_enabled():
                    self.inactivity_timer.begin_transition(False)

                # start/stop on-hide-release timer
                self._auto_release_timer.start(visible)

        if done:
            window.on_transition_done(visible_before, visible_later)

        return not done

    def is_visible(self):
        """ is the keyboard window currently visible? """
        window = self.get_kbd_window()
        return window.is_visible() if window else False

    def set_visible(self, visible):
        """ main method to show/hide onboard manually """
        self.transition_visible_to(visible, 0.0)

        # briefly present the window
        if visible and self.inactivity_timer.is_enabled():
            self.transition_active_to(True, 0.0)
            self.inactivity_timer.begin_transition(False)

        self.commit_transition()

    def raise_to_top(self):
        """ Raise the toplevel parent to top of the z-order. """
        window = self.get_kbd_window()
        if window:
            window.raise_to_top()

    def auto_position(self):
        """ auto-show, start repositioning """
        window = self.get_kbd_window()
        if window:
            window.auto_position()

    def stop_auto_positioning(self):
        """ auto-show, stop all further repositioning attempts """
        window = self.get_kbd_window()
        if window:
            window.stop_auto_positioning()

    def start_click_polling(self):
        if self.keyboard.has_latched_sticky_keys() or \
           self._key_popup or \
           config.are_word_suggestions_enabled():
            self._outside_click_timer.start(0.01, self._on_click_timer)
            self._outside_click_detected = False
            self._outside_click_start_time = time.time()
            self._outside_click_num = 0

    def stop_click_polling(self):
        self._outside_click_timer.stop()

    def _on_click_timer(self):
        """ poll for mouse click outside of onboards window """
        rootwin = Gdk.get_default_root_window()
        dunno, x, y, mask = rootwin.get_pointer()
        if mask & BUTTON123_MASK:
            self._outside_click_detected = True
            self._outside_click_button_mask = mask
        elif self._outside_click_detected:
            self._outside_click_detected = False
            # A button was released anywhere outside of Onboard's control.
            _logger.debug("click polling: outside click")

            self.close_key_popup()

            button = \
                self._get_button_from_mask(self._outside_click_button_mask)

            # When clicking left, don't stop polling right away. This allows
            # the user to select some text and paste it with middle click,
            # while the pending separator is still inserted.
            self._outside_click_num += 1
            max_clicks = 4

            if button != 1:  # middle and right click stop polling immediately
                self.stop_click_polling()
                self.keyboard.on_outside_click(button)

            elif button == 1 and self._outside_click_num == 1:
                if not config.wp.delayed_word_separators_enabled:
                    self.stop_click_polling()
                self.keyboard.on_outside_click(button)

            # allow a couple of left clicks with delayed separators
            elif self._outside_click_num >= max_clicks:
                self.stop_click_polling()
                self.keyboard.on_cancel_outside_click()

            return True

        # stop polling after 30 seconds
        if time.time() - self._outside_click_start_time > 30.0:
            self.stop_click_polling()
            self.keyboard.on_cancel_outside_click()
            return False

        return True

    @staticmethod
    def _get_button_from_mask(mask):
        for i, bit in enumerate((Gdk.ModifierType.BUTTON1_MASK,
                                 Gdk.ModifierType.BUTTON2_MASK,
                                 Gdk.ModifierType.BUTTON3_MASK,)):
            if mask & bit:
                return i + 1
        return 0

    def get_drag_window(self):
        """ Overload for WindowManipulator """
        return self.get_kbd_window()

    def get_drag_threshold(self):
        """ Overload for WindowManipulator """
        return config.get_drag_threshold()

    def on_drag_initiated(self):
        """ Overload for WindowManipulator """
        window = self.get_drag_window()
        if window:
            window.on_user_positioning_begin()
        self.grab_xi_pointer(True)

    def on_drag_activated(self):
        if self.is_resizing():
            self._lod = LOD.MINIMAL
        self.keyboard.hide_touch_feedback()

    def on_drag_done(self):
        """ Overload for WindowManipulator """
        self.grab_xi_pointer(False)
        WindowManipulatorAspectRatio.on_drag_done(self)
        window = self.get_drag_window()
        if window:
            window.on_user_positioning_done()

        self.reset_lod()

    def get_always_visible_rect(self):
        """
        Returns the bounding rectangle of all move buttons
        in canvas coordinates.
        Overload for WindowManipulator
        """
        bounds = None
        if config.is_docking_enabled():
            pass
        else:
            keys = self.keyboard.find_items_from_ids(["move"])
            keys = [k for k in keys if k.is_path_visible()]
            if not keys:   # no visible move key (Small, Phone layout)?
                keys = self.keyboard.find_items_from_ids(["RTRN"])
                keys = [k for k in keys if k.is_path_visible()]
            for key in keys:
                r = key.get_canvas_border_rect()
                if not bounds:
                    bounds = r
                else:
                    bounds = bounds.union(r)

        if bounds is None:
            bounds = self.canvas_rect

        return bounds

    def hit_test_move_resize(self, point):
        """ Overload for WindowManipulator """
        hit = self.touch_handles.hit_test(point)
        if hit is None:
            hit = WindowManipulator.hit_test_move_resize(self, point)
        return hit

    def _on_configure_event(self, widget, user_data):
        if self.is_new_layout_size(self.get_allocated_width(),
                                   self.get_allocated_height()):
            self.update_layout()
            self.update_touch_handles_positions()
            self.invalidate_for_resize(self._lod)

    def on_enter_notify(self, widget, event):
        self.keyboard.on_activity_detected()

        self._update_double_click_time()

        # ignore event if a mouse button is held down
        # we get the event once the button is released
        if event.state & BUTTON123_MASK:
            return

        # ignore unreliable touch enter event for inactivity timer
        # -> smooths startup, only one transition in set_startup_visibility()
        source_device = event.get_source_device()
        source = source_device.get_source()
        if source != Gdk.InputSource.TOUCHSCREEN:

            # stop inactivity timer
            if self.inactivity_timer.is_enabled():
                self.inactivity_timer.begin_transition(True)

        # stop click polling
        self.stop_click_polling()

        # Force into view for WindowManipulator's system drag mode.
        #if not config.xid_mode and \
        #   not config.window.window_decoration and \
        #   not config.is_force_to_top():
        #    GLib.idle_add(self.force_into_view)

    def on_leave_notify(self, widget, event):
        # ignore event if a mouse button is held down
        # we get the event once the button is released
        if event.state & BUTTON123_MASK:
            return

        # Ignore leave events when the cursor hasn't acually left
        # our window. Fixes window becoming idle-transparent while
        # typing into firefox awesomebar.
        # Can't use event.mode as that appears to be broken and
        # never seems to become GDK_CROSSING_GRAB (Precise).
        if self.canvas_rect.is_point_within((event.x, event.y)):
            return

        self.stop_dwelling()
        self.reset_touch_handles()

        # start a timer to detect clicks outside of onboard
        self.start_click_polling()

        # Start inactivity timer, but ignore the unreliable
        # leave event for touch input.
        source_device = event.get_source_device()
        source = source_device.get_source()
        if source != Gdk.InputSource.TOUCHSCREEN:
            if self.inactivity_timer.is_enabled():
                self.inactivity_timer.begin_transition(False)

        # Reset the cursor, so enabling the scanner doesn't get the last
        # selected one stuck forever.
        self.reset_drag_cursor()

    def do_set_cursor_at(self, point, hit_key = None):
        """ Set/reset the cursor for frame resize handles """
        if not config.xid_mode:
            allow_drag_cursors = not hit_key and \
                                 not config.has_window_decoration()
            self.set_drag_cursor_at(point, allow_drag_cursors)

    def on_input_sequence_begin(self, sequence):
        """ Button press/touch begin """
        self.keyboard.on_activity_detected()

        self.stop_click_polling()
        self.stop_dwelling()
        self.close_key_popup()

        # There's no reliable enter/leave for touch input
        # -> turn up inactive transparency on touch begin
        if sequence.is_touch() and \
           self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(True)

        # hit-test touch handles first
        hit_handle = None
        if self.touch_handles.active:
            hit_handle = self.touch_handles.hit_test(sequence.point)
            self.touch_handles.set_pressed(hit_handle)
            if hit_handle is not None:
                # handle clicked -> stop auto-hide until button release
                self.stop_touch_handles_auto_hide()
            else:
                # no handle clicked -> hide them now
                self.show_touch_handles(False)

        # ask layout tree
        if hit_handle is None:
            layout = self.get_layout()
            if layout:
                layout.dispatch_input_sequence_begin(sequence)

        if sequence.active_item is None:
            self.on_input_sequence_begin_delayed(sequence, hit_handle)
        else:
            sequence.active_item.sequence_begin_retry_func = \
                self.on_input_sequence_begin_delayed

    def on_input_sequence_begin_delayed(self, sequence, hit_handle=None):
        point = sequence.point
        key = None

        # hit-test keys
        if hit_handle is None and \
           sequence.active_item is None:
            key = self.get_key_at_location(point)

        # enable/disable the drag threshold
        if hit_handle is not None:
            self.enable_drag_protection(False)
        elif key and key.id == "move":
            # Move key needs to support long press;
            # always use the drag threshold.
            self.enable_drag_protection(True)
            self.reset_drag_protection()
        else:
            self.enable_drag_protection(config.drag_protection)

        # handle resizing
        if key is None and \
           not config.has_window_decoration() and \
           not config.xid_mode:
            if WindowManipulator.handle_press(self, sequence):
                return True

        # bail if we are in scanning mode
        if config.scanner.enabled:
            return True

        # press the key
        sequence.active_key = key
        sequence.initial_active_key = key
        if key:
            # single click?
            if self._last_click_key != key or \
               sequence.time - self._last_click_time > self._double_click_time:

                # handle key press
                sequence.event_type = EventType.CLICK
                self.key_down(sequence)

                # start long press detection
                delay = config.keyboard.long_press_delay
                if key.id == "move":  # don't show touch handles too easily
                    delay += 0.3
                self._long_press_timer.start(delay,
                                             self._on_long_press, sequence)

            # double click
            else:
                sequence.event_type = EventType.DOUBLE_CLICK
                self.key_down(sequence)

            self._last_click_key = key
            self._last_click_time = sequence.time

        return True

    def on_input_sequence_update(self, sequence):
        """ Pointer motion/touch update """
        if not sequence.primary:  # only drag with the very first sequence
            return

        # Redirect to long press popup for drag selection.
        popup = self._key_popup
        if popup:
            popup.redirect_sequence_update(sequence,
                                           popup.on_input_sequence_update)
            return

        point = sequence.point
        hit_key = None

        # hit-test touch handles first
        hit_handle = None
        if self.touch_handles.active:
            hit_handle = self.touch_handles.hit_test(point)
            self.touch_handles.set_prelight(hit_handle)

        # ask layout tree
        if hit_handle is None:
            layout = self.get_layout()
            if layout:
                layout.dispatch_input_sequence_update(sequence)

            if sequence.active_item is None:
                # hit-test keys
                hit_key = self.get_key_at_location(point)
            else:
                # hack that hides popups when ScrolledLayoutPanel
                # starts scrolling
                self.keyboard.hide_touch_feedback()

        if sequence.state & BUTTON123_MASK:

            # move/resize
            # fallback=False for faster system resizing (LP: #959035)
            fallback = True  # self.is_moving() or config.is_force_to_top()

            # move/resize
            WindowManipulator.handle_motion(self, sequence, fallback=fallback)

            # stop long press when drag threshold has been overcome
            if self.is_drag_active():
                self.stop_long_press()

            # drag-select new active key
            active_key = sequence.active_key
            if not self.is_drag_initiated() and \
               active_key != hit_key:
                self.stop_long_press()

                if self._overcome_initial_key_resistance(sequence) and \
                   (not active_key or not active_key.activated) and \
                   not self._key_popup:
                    sequence.active_key = hit_key
                    self.key_down_update(sequence, active_key)

        else:
            if hit_handle is not None:
                # handle hovered over: extend the time
                # touch handles are visible
                self.start_touch_handles_auto_hide()

            # Show/hide the input line
            self._hide_input_line_timer.handle_motion(sequence)

            # start dwelling if we have entered a dwell-enabled key
            if hit_key and \
               hit_key.sensitive:
                controller = self.keyboard.get_button_controller(hit_key)
                if controller and controller.can_dwell() and \
                   not self.is_dwelling() and \
                   not self.already_dwelled(hit_key) and \
                   not config.scanner.enabled and \
                   not config.lockdown.disable_dwell_activation:
                    self.start_dwelling(hit_key)

            self.do_set_cursor_at(point, hit_key)

        # cancel dwelling when the hit key changes
        if self.dwell_key and self.dwell_key != hit_key or \
           self.last_dwelled_key and self.last_dwelled_key != hit_key:
            self.cancel_dwelling()

    def on_input_sequence_end(self, sequence):
        """ Button release/touch end """

        # Redirect to long press popup for end of drag-selection.
        popup = self._key_popup
        if popup and \
           popup.got_motion():  # keep popup open if it wasn't entered
            popup.redirect_sequence_end(sequence,
                                        popup.on_input_sequence_end)

        # layout tree
        layout = self.get_layout()
        if layout:
            layout.dispatch_input_sequence_end(sequence)

        # key up
        active_key = sequence.active_key
        if active_key and \
           not config.scanner.enabled:
            self.key_up(sequence)

        self.stop_drag()
        self.stop_long_press()

        # reset cursor when there was no cursor motion
        point = sequence.point
        hit_key = self.get_key_at_location(point)
        self.do_set_cursor_at(point, hit_key)

        # reset touch handles
        self.reset_touch_handles()
        self.start_touch_handles_auto_hide()

        # There's no reliable enter/leave for touch input
        # -> start inactivity timer on touch end
        if sequence.is_touch() and \
           self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(False)

    def on_drag_gesture_begin(self, num_touches):
        """ Called only for num_touches >= 2  """
        self.stop_long_press()

        if Handle.MOVE in self.get_drag_handles() and \
           num_touches and \
           not self.is_drag_initiated():
            self.show_touch_handles()
            self.start_move_window()
        return True

    def on_drag_gesture_end(self, num_touches):
        self.stop_move_window()
        return True

    def on_tap_gesture(self, num_touches):
        if num_touches == 3:
            self.show_touch_handles()
            return True
        return False

    def _on_long_press(self, sequence):
        long_pressed = self.keyboard.key_long_press(sequence.active_key,
                                                    self, sequence.button)
        sequence.cancel_key_action = long_pressed # cancel generating key-stroke

    def stop_long_press(self):
        self._long_press_timer.stop()

    def key_down(self, sequence):
        self.keyboard.key_down(sequence.active_key, self, sequence)
        self._auto_release_timer.start()

    def key_down_update(self, sequence, old_key):
        assert(not old_key or not old_key.activated) # old_key must be undoable
        self.keyboard.key_up(old_key, self, sequence, False)
        self.keyboard.key_down(sequence.active_key, self, sequence, False)

    def key_up(self, sequence):
        self.keyboard.key_up(sequence.active_key, self, sequence,
                             not sequence.cancel_key_action)

    def is_dwelling(self):
        return not self.dwell_key is None

    def already_dwelled(self, key):
        return self.last_dwelled_key is key

    def start_dwelling(self, key):
        self.cancel_dwelling()
        self.dwell_key = key
        self.last_dwelled_key = key
        key.start_dwelling()
        self.dwell_timer = GLib.timeout_add(50, self._on_dwell_timer)

    def cancel_dwelling(self):
        self.stop_dwelling()
        self.last_dwelled_key = None

    def stop_dwelling(self):
        if self.dwell_timer:
            GLib.source_remove(self.dwell_timer)
            self.dwell_timer = None
            self.redraw([self.dwell_key])
            self.dwell_key.stop_dwelling()
            self.dwell_key = None

    def _on_dwell_timer(self):
        if self.dwell_key:
            self.redraw([self.dwell_key])

            if self.dwell_key.is_done():
                key = self.dwell_key
                self.stop_dwelling()

                sequence = InputSequence()
                sequence.button = 0
                sequence.event_type = EventType.DWELL
                sequence.active_key = key
                sequence.point = key.get_canvas_rect().get_center()
                sequence.root_point = \
                        canvas_to_root_window_point(self, sequence.point)

                self.key_down(sequence)
                self.key_up(sequence)

                return False
        return True

    def _on_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        if config.show_tooltips and \
           not self.is_drag_initiated() and \
           not self.last_event_was_touch():
            key = self.get_key_at_location((x, y))
            if key and key.tooltip:
                r = Gdk.Rectangle()
                r.x, r.y, r.width, r.height = key.get_canvas_rect()
                tooltip.set_tip_area(r)   # no effect on Oneiric?
                tooltip.set_text(_(key.tooltip))
                return True
        return False

    def show_touch_handles(self, show = True, auto_hide = True):
        """
        Show/hide the enlarged resize/move handels.
        Initiates an opacity fade.
        """
        if show and config.lockdown.disable_touch_handles:
            return

        if show:
            self.touch_handles.set_prelight(None)
            self.touch_handles.set_pressed(None)
            self.touch_handles.active = True
            self.touch_handles_auto_hide = auto_hide

            size, size_mm = get_monitor_dimensions(self)
            self.touch_handles.set_monitor_dimensions(size, size_mm)
            self.update_touch_handles_positions()

            if auto_hide:
                self.start_touch_handles_auto_hide()

            start, end = 0.0, 1.0
        else:
            self.stop_touch_handles_auto_hide()
            start, end = 1.0, 0.0

        if self.touch_handles_fade.target_value != end:
            self.touch_handles_fade.time_step = 0.025
            self.touch_handles_fade.fade_to(start, end, 0.2,
                                      self._on_touch_handles_opacity)

    def reset_touch_handles(self):
        if self.touch_handles.active:
            self.touch_handles.set_prelight(None)
            self.touch_handles.set_pressed(None)

    def start_touch_handles_auto_hide(self):
        """ (re-) starts the timer to hide touch handles """
        if self.touch_handles.active and self.touch_handles_auto_hide:
            self.touch_handles_hide_timer.start(4,
                                                self.show_touch_handles, False)

    def stop_touch_handles_auto_hide(self):
        """ stops the timer to hide touch handles """
        self.touch_handles_hide_timer.stop()

    def _on_touch_handles_opacity(self, opacity, done):
        if done and opacity < 0.1:
            self.touch_handles.active = False

        self.touch_handles.opacity = opacity

        # Convoluted workaround for a weird cairo glitch (Precise).
        # When queuing all handles for drawing, the background under
        # the move handle is clipped erroneously and remains transparent.
        # -> Divide handles up into two groups, draw only one
        #    group at a time and fade with twice the frequency.
        if 0:
            self.touch_handles.redraw()
        else:
            for handle in self.touch_handles.handles:
                if bool(self.touch_handles_fade.iteration & 1) != \
                   (handle.id in [Handle.MOVE, Handle.NORTH, Handle.SOUTH]):
                    handle.redraw()

            if done:
                # draw the missing final step
                GLib.idle_add(self._on_touch_handles_opacity, 1.0, False)

    def update_touch_handles_positions(self):
        self.touch_handles.update_positions(self.get_keyboard_frame_rect())

    def _on_draw(self, widget, context):
        context.push_group()

        decorated = LayoutView.draw(self, widget, context)

        # draw touch handles (enlarged move and resize handles)
        if self.touch_handles.active:
            corner_radius = config.CORNER_RADIUS if decorated else 0
            self.touch_handles.set_corner_radius(corner_radius)
            self.touch_handles.draw(context)

        context.pop_group_to_source()
        context.paint_with_alpha(self._opacity)

    def _overcome_initial_key_resistance(self, sequence):
        """
        Drag-select: Increase the hit area of the initial key
        to make it harder to leave the the key the button was
        pressed down on.
        """
        DRAG_SELECT_INITIAL_KEY_ENLARGEMENT = 0.4

        active_key = sequence.active_key
        if active_key and active_key is sequence.initial_active_key:
            rect = active_key.get_canvas_border_rect()
            k = min(rect.w, rect.h) * DRAG_SELECT_INITIAL_KEY_ENLARGEMENT
            rect = rect.inflate(k)
            if rect.is_point_within(sequence.point):
                return False
        return True

    # --- POINTER RECOVERY FIX ---
    def _start_pointer_polling(self):
        if getattr(self, "_pointer_poll_id", None) is None:
            self._pointer_poll_id = GLib.timeout_add(
                120, self._pointer_poll_cb
            )

    def _stop_pointer_polling(self):
        if getattr(self, "_pointer_poll_id", None) is not None:
            GLib.source_remove(self._pointer_poll_id)
            self._pointer_poll_id = None

    def _pointer_poll_cb(self):
        try:
            window = self.get_window()
            if not window:
                return True

            display = Gdk.Display.get_default()
            seat = display.get_default_seat()
            device = seat.get_pointer()
            screen, x, y = device.get_position()
            wx, wy = window.get_root_coords(0, 0)
            alloc = self.get_allocation()

            inside = (
                wx <= x <= wx + alloc.width and
                wy <= y <= wy + alloc.height
            )

            last = getattr(self, "_last_pointer_inside", None)

            if last is None:
                self._last_pointer_inside = inside
                return True

            # correct lost events
            if inside and not last:
                self.keyboard.on_activity_detected()
                if self.inactivity_timer.is_enabled():
                    self.inactivity_timer.begin_transition(True)

            elif not inside and last:
                if self.inactivity_timer.is_enabled():
                    self.inactivity_timer.begin_transition(False)

            self._last_pointer_inside = inside

        except Exception:
            pass

        return True
        
    def get_kbd_window(self):
        return self.get_parent()

    def can_draw_frame(self):
        """ Overload for LayoutView """
        co = self.get_kbd_window().get_orientation_config_object()
        return not config.is_dock_expanded(co)

    def can_draw_sidebars(self):
        """ Overload for LayoutView """
        co = self.get_kbd_window().get_orientation_config_object()
        return config.is_keep_docking_frame_aspect_ratio_enabled(co)

    def get_frame_width(self):
        """ Width of the frame around the keyboard; canvas coordinates. """
        if config.xid_mode:
            return config.UNDECORATED_FRAME_WIDTH
        if config.has_window_decoration():
            return 0.0
        co = self.get_kbd_window().get_orientation_config_object()
        if config.is_dock_expanded(co):
            return 2.0
        if config.window.transparent_background:
            return 3.0
        return config.UNDECORATED_FRAME_WIDTH

    def get_hit_frame_width(self):
        return 10

    def _get_active_drag_handles(self, all_handles = False):
        if config.xid_mode:  # none when xembedding
            handles = ()
        else:
            if config.is_docking_enabled():
                expand = self.get_kbd_window().get_dock_expand()
                if expand:
                    handles = (Handle.NORTH, Handle.SOUTH,
                               Handle.WEST, Handle.EAST,
                               Handle.MOVE)
                else:
                    handles = Handle.RESIZE_MOVE
            else:
                handles = Handle.RESIZE_MOVE

            if not all_handles:
                # filter through handles enabled in config
                config_handles = config.window.window_handles
                handles = tuple(set(handles).intersection(set(config_handles)))

        return handles

    def get_handle_function(self, handle):
        if handle in (Handle.WEST, Handle.EAST) and \
           config.is_docking_enabled() and \
           self.get_kbd_window().get_dock_expand():
               return HandleFunction.ASPECT_RATIO

        return HandleFunction.NORMAL

    def get_click_type_button_rects(self):
        """
        Returns bounding rectangles of all click type buttons
        in root window coordinates.
        """
        keys = self.keyboard.find_items_from_ids(["singleclick",
                                                  "secondaryclick",
                                                  "middleclick",
                                                  "doubleclick",
                                                  "dragclick"])
        rects = []
        for key in keys:
            r = key.get_canvas_border_rect()
            r = canvas_to_root_window_rect(self, r)

            # scale coordinates in response to changes to
            # org.gnome.desktop.interface scaling-factor
            scale = config.window_scaling_factor
            if scale and scale != 1.0:
                r = r.scale(scale)

            rects.append(r)

        return rects

    def get_key_screen_rect(self, key):
        """
        Returns bounding rectangles of key in in root window coordinates.
        """
        r = key.get_canvas_border_rect()
        x0, y0 = self.get_window().get_root_coords(r.x, r.y)
        x1, y1 = self.get_window().get_root_coords(r.x + r.w,
                                                   r.y + r.h)
        return Rect(x0, y0, x1 - x0, y1 -y0)

    def on_layout_updated(self):
        # experimental support for keeping window aspect ratio
        # Currently, in Oneiric, neither lightdm, nor gnome-screen-saver
        # appear to honor these hints.
        layout = self.get_layout()

        aspect_ratio = None
        co = self.get_kbd_window().get_orientation_config_object()
        if config.is_keep_window_aspect_ratio_enabled(co):
            log_rect = layout.get_border_rect()
            aspect_ratio = log_rect.w / float(log_rect.h)
            aspect_ratio = layout.get_log_aspect_ratio()

        if self._window_aspect_ratio != aspect_ratio:
            window = self.get_kbd_window()
            if window:
                geom = Gdk.Geometry()
                if aspect_ratio is None:
                    window.set_geometry_hints(self, geom, 0)
                else:
                    geom.min_aspect = geom.max_aspect = aspect_ratio
                    window.set_geometry_hints(self, geom, Gdk.WindowHints.ASPECT)

                self._window_aspect_ratio = aspect_ratio

    def refresh_pango_layouts(self):
        """
        When the systems font dpi setting changes, our pango layout object
        still caches the old setting, leading to wrong font scaling.
        Refresh the pango layout object.
        """
        _logger.info("Refreshing pango layout, new font dpi setting is '{}'" \
                .format(Gtk.Settings.get_default().get_property("gtk-xft-dpi")))

        Key.reset_pango_layout()
        self.invalidate_label_extents()
        self.keyboard.invalidate_ui()
        self.keyboard.commit_ui_updates()

    def show_popup_alternative_chars(self, key, alternatives):
        """
        Popup with alternative chars.
        """
        popup = self._create_key_popup(self.get_kbd_window())
        result = LayoutBuilderAlternatives \
                    .build(key, self.get_color_scheme(), alternatives)
        popup.set_layout(*result)

        self._show_key_popup(popup, key)
        self._key_popup = popup

        self.keyboard.hide_touch_feedback()

    def show_popup_layout(self, key, layout):
        """
        Popup with predefined layout items.
        """
        popup = self._create_key_popup(self.get_kbd_window())
        result = LayoutBuilder \
                    .build(key, self.get_color_scheme(), layout)
        popup.set_layout(*result)
        self._show_key_popup(popup, key)
        self._key_popup = popup

        self.keyboard.hide_touch_feedback()

    def close_key_popup(self):
        if self._key_popup:
            self._key_popup.destroy()
            self._key_popup = None

    def _create_key_popup(self, parent):
        popup = LayoutPopup(self.keyboard, self.close_key_popup)
        popup.supports_alpha = self.supports_alpha
        popup.set_transient_for(parent)
        popup.set_opacity(self.get_opacity())
        return popup

    def _show_key_popup(self, popup, key):
        r = key.get_canvas_border_rect()
        root_rect = canvas_to_root_window_rect(self, r)
        popup.position_at(root_rect.x + root_rect.w * 0.5,
                         root_rect.y, 0.5, 1.0)
        popup.show_all()
        return popup

    def show_snippets_dialog(self, snippet_id):
        """ Show dialog for creating a new snippet """

        label, text = config.snippets.get(snippet_id, (None, None))
        if snippet_id in config.snippets:
            # Title of the snippets dialog for existing snippets
            title = _format("Edit snippet #{}", snippet_id)
            message = ""
        else:
            # Title of the snippets dialog for new snippets
            title = _("New snippet")
            # Message in the snippets dialog for new snippets
            message = _format("Enter a new snippet for button #{}:", snippet_id)

        # turn off AT-SPI listeners to prevent D-BUS deadlocks (Quantal).
        self.keyboard.on_focusable_gui_opening()

        dialog = Gtk.Dialog(title=title,
                           transient_for=self.get_toplevel(),
                           flags=0)
        # Translators: cancel button of the snippets dialog. It used to
        # be stock item STOCK_CANCEL until Gtk 3.10 deprecated those.
        dialog.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("_Save snippet"), Gtk.ResponseType.OK)

        # Don't hide dialog behind the keyboard in force-to-top mode.
        if config.is_force_to_top():
            dialog.set_position(Gtk.WindowPosition.CENTER)

        dialog.set_default_response(Gtk.ResponseType.OK)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=12, border_width=5)
        dialog.get_content_area().add(box)

        if message:
            msg_label = Gtk.Label(label=message, xalign=0.0)
            box.add(msg_label)

        label_entry = Gtk.Entry(hexpand=True)
        text_entry  = Gtk.Entry(hexpand=True,
                                activates_default = True,
                                width_chars=35)
        label_label = Gtk.Label(label=_("_Button label:"),
                                xalign=0.0,
                                use_underline=True,
                                mnemonic_widget=label_entry)
        text_label  = Gtk.Label(label=_("S_nippet:"),
                                xalign=0.0,
                                use_underline=True,
                                mnemonic_widget=text_entry)

        grid = Gtk.Grid(row_spacing=6, column_spacing=3)
        grid.attach(label_label, 0, 0, 1, 1)
        grid.attach(text_label, 0, 1, 1, 1)
        grid.attach(label_entry, 1, 0, 1, 1)
        grid.attach(text_entry, 1, 1, 1, 1)
        box.add(grid)

        # Init entries, mainly the label for the case when text is empty.
        label, text = config.snippets.get(snippet_id, (None, None))
        if label:
            label_entry.set_text(label)
        if text:
            text_entry.set_text(text)

        if label and not text:
            text_entry.grab_focus()
        else:
            label_entry.grab_focus()

        dialog.connect("response", self._on_snippet_dialog_response, \
                       snippet_id, label_entry, text_entry)
        dialog.show_all()

    def _on_snippet_dialog_response(self, dialog, response, snippet_id, \
                                    label_entry, text_entry):
        if response == Gtk.ResponseType.OK:
            label = label_entry.get_text()
            text = text_entry.get_text()

            if sys.version_info.major == 2:
                label = label.decode("utf-8")
                text = text.decode("utf-8")

            config.set_snippet(snippet_id, (label, text))
        dialog.destroy()

        self.keyboard.on_snippets_dialog_closed()

        # Reenable AT-SPI keystroke listeners.
        # Delay this until the dialog is really gone.
        GLib.idle_add(self.keyboard.on_focusable_gui_closed)

    def show_language_menu(self, key, button, closure=None):
        self._language_menu.popup(key, button, closure)

    def is_language_menu_showing(self):
        return self._language_menu.is_showing()

    def show_prediction_menu(self, key, button, closure=None):
        self._suggestion_menu.popup(key, button, closure)

    def show_symbol_search(self, content_type):
        return

        layout = self.get_layout()
        if not layout:
            return

        wordlist = layout.find_id("wordlist")
        if not wordlist:
            return

        popup = self._symbol_search_popup
        if popup is None:
            popup = SymbolSearchPopup()
            popup.set_transient_for(self.get_toplevel())
            popup.realize()

            entry = Gtk.SearchEntry(hexpand=True, width_chars=35)
            popup.add(entry)

            self._symbol_search_popup = popup

        r = wordlist.get_canvas_rect()
        root_rect = canvas_to_root_window_rect(self, r)
        x, y, w, h = root_rect
        y -= h
        print(x, y, w, h)

        popup.show_all()

        popup.set_default_size(w, h)
        popup.resize(w, h)
        popup.move(x, y)

    def hide_symbol_search(self):
        if not self._symbol_search_popup:
            return
        self._symbol_search_popup.hide()


class SymbolSearchPopup(KeyboardPopup):

    def __init__(self):
        super(SymbolSearchPopup, self).__init__(accept_focus=False)

    def on_draw(self, widget, cr):
        cr.set_source_rgba(1, 0, 0, 0.5)
        cr.paint()


class KeyMenu:
    """ Popup menu for keys """

    def __init__(self, keyboard_widget):
        self._keyboard_widget = keyboard_widget
        self._keyboard = self._keyboard_widget.keyboard
        self._menu = None
        self._closure = None
        self._x_align = 0.0  # horizontal alignment of the menu position

    def is_showing(self):
        return not self._menu is None

    def popup(self, key, button, closure = None):
        self._closure = closure
        self._keyboard.on_focusable_gui_opening()

        menu = self.create_menu(key, button)
        self._menu = menu

        menu.connect("unmap", self._on_menu_unmap)
        menu.show_all()
        menu.popup(None, None, self._menu_positioning_func,
                   key, button, Gtk.get_current_event_time())

    def create_menu(self, key, button):
        """ Overload this in derived class """
        raise NotImplementedError()

    def _on_menu_unmap(self, menu):
        Timer(0.5, self._keyboard.on_focusable_gui_closed)
        self._menu = None
        if self._closure:
            self._closure()

    def _menu_positioning_func(self, *params):
        # work around change in number of paramters in Wily with Gtk 3.16
        if len(params) == 4:
            menu, x, y, key = params  # new in Wily
        else:
            menu, key = params

        r = self._keyboard_widget.get_key_screen_rect(key)
        menu_size  = (menu.get_allocated_width(),
                      menu.get_allocated_width())
        x, y = self.get_menu_position(r, menu_size)
        return x, y, False

    def get_menu_position(self, rkey, menu_size):
        return rkey.left() + (rkey.w - menu_size[0]) * self._x_align, \
               rkey.bottom()


class LanguageMenu(KeyMenu):
    """ Popup menu for the language button """

    def create_menu(self, key, button):
        keyboard = self._keyboard
        languagedb = keyboard._languagedb

        active_lang_id = keyboard.get_active_lang_id()
        system_lang_id = config.get_system_default_lang_id()

        lang_ids = set(languagedb.get_language_ids())
        if system_lang_id in lang_ids:
            lang_ids.remove(system_lang_id)

        max_mru_languages = config.typing_assistance.max_recent_languages
        all_mru_lang_ids = config.typing_assistance.recent_languages
        mru_lang_ids    = [id for id in all_mru_lang_ids if id in lang_ids] \
                          [:max_mru_languages]
        other_lang_ids  = set(lang_ids).difference(mru_lang_ids)
        other_langs = []
        for lang_id in other_lang_ids:
            name = languagedb.get_language_full_name(lang_id)
            if name:
                other_langs.append((name, lang_id))

        # language sub menu
        lang_menu = Gtk.Menu()
        for name, lang_id in sorted(other_langs):
            item = Gtk.MenuItem.new_with_label(name)
            item.connect("activate", self._on_other_language_activated, lang_id)
            lang_menu.append(item)

        # popup menu
        menu = Gtk.Menu()

        active_lang_id = keyboard.get_active_lang_id()
        name = languagedb.get_language_full_name(system_lang_id)
        item = Gtk.CheckMenuItem.new_with_mnemonic(name)
        item.set_draw_as_radio(True)
        item.set_active(not active_lang_id)
        item.connect("activate", self._on_language_activated, "")
        menu.append(item)

        item = Gtk.SeparatorMenuItem.new()
        menu.append(item)

        for lang_id in mru_lang_ids:
            name = languagedb.get_language_full_name(lang_id)
            if name:
                item = Gtk.CheckMenuItem.new_with_label(name)
                item.set_draw_as_radio(True)
                item.set_active(lang_id == active_lang_id)
                item.connect("activate", self._on_language_activated, lang_id)
                menu.append(item)

        if mru_lang_ids:
            item = Gtk.SeparatorMenuItem.new()
            menu.append(item)

        if other_langs:
            item = Gtk.MenuItem.new_with_mnemonic(_("Other _Languages"))
            item.set_submenu(lang_menu)
            menu.append(item)

        return menu

    def _on_language_activated(self, menu, lang_id):
        system_lang_id = config.get_system_default_lang_id()
        if lang_id == system_lang_id:
            lang_id = ""
        self._set_active_lang_id(lang_id)

    def _on_other_language_activated(self, menu, lang_id):
        if lang_id:  # empty string = system default
            self._set_mru_lang_id(lang_id)
        self._set_active_lang_id(lang_id)

    def _set_active_lang_id(self, lang_id):
        self._keyboard.set_active_lang_id(lang_id)

    def _set_mru_lang_id(self, lang_id):
        max_recent_languages = config.typing_assistance.max_recent_languages
        recent_languages = config.typing_assistance.recent_languages[:]
        if lang_id in recent_languages:
            recent_languages.remove(lang_id)
        recent_languages.insert(0, lang_id)
        recent_languages = recent_languages[:max_recent_languages]
        config.typing_assistance.recent_languages = recent_languages


class SuggestionMenu(KeyMenu):
    """ Popup menu for word suggestion buttons """

    def __init__(self, keyboard_widget):
        KeyMenu.__init__(self, keyboard_widget)
        self._x_align = 0.5

    def create_menu(self, key, button):

        self._choice_index = key.code

        # popup menu
        menu = Gtk.Menu()

        item = Gtk.MenuItem.new_with_mnemonic(_("_Remove suggestion…"))
        item.connect("activate", self._on_remove_suggestion, key)
        menu.append(item)

        return menu

    def _on_remove_suggestion(self, menu_item, key):
        keyboard = self._keyboard
        wordlist = key.get_parent()
        suggestion, history = \
            keyboard.get_prediction_choice_and_history(wordlist,
                                                       self._choice_index)
        history = history[-1:]  # only single word history supported
        dialog = RemoveSuggestionConfirmationDialog(
                    self._keyboard_widget.get_kbd_window(),
                    keyboard, suggestion, history)
        context_length = dialog.run()
        if context_length:
            context = [suggestion]
            if context_length == 2:
                context = history[-1:] + context
            keyboard.remove_prediction_context(context)

            # Refresh word suggestions explicitely, the dialog
            # disabled AT-SPI events.
            keyboard.invalidate_context_ui()
            keyboard.commit_ui_updates()

    def get_menu_position(self, rkey, menu_size):
        if menu_size[0] > rkey.w:
            return rkey.left(), rkey.bottom()
        else:
            return super(SuggestionMenu, self) \
                .get_menu_position(rkey, menu_size)


class RemoveSuggestionConfirmationDialog(Gtk.MessageDialog):
    """ Confirm removal of a word suggestion """

    def __init__(self, parent, keyboard, suggestion, history):
        self._keyboard = keyboard
        self._radio1 = None
        self._radio2 = None

        Gtk.MessageDialog.__init__(self,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL,
                                   title=_("Onboard"))

        if parent:
            self.set_transient_for(parent)

        # Don't hide dialog behind the keyboard in force-to-top mode.
        if config.is_force_to_top():
            self.set_position(Gtk.WindowPosition.CENTER)

        markup = "<big>" + _("Remove word suggestion") + "</big>"
        markup = escape_markup(markup, preserve_tags=True)
        self.set_markup(markup)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        label = self._get_remove_context_length_2_label(suggestion, history)
        self._radio2 = Gtk.RadioButton.new_with_label(None, label)
        box.add(self._radio2)

        self._radio1 = Gtk.RadioButton.new_with_label_from_widget(self._radio2,
                       _format("Remove '{}' everywhere.", suggestion))
        box.add(self._radio1)

        if not history:
            # This should rarly happen, if ever. Edited text usually
            # start with the begin of text marker, so there exists a history
            # even if the text appears to be empty.
            self._radio2.set_sensitive(False)
            self._radio1.set_active(True)

        label = Gtk.Label(label=_("This will only affect learned suggestions."),
                                   xalign=0.0)
        box.add(label)

        self.get_message_area().add(box)
        self.show_all()

    @staticmethod
    def _get_remove_context_length_2_label(suggestion, history):
        """
        Label of radio button for remove context length 2.

        Doctests:
        >>> from Onboard.KeyboardWidget import RemoveSuggestionConfirmationDialog
        >>> test = RemoveSuggestionConfirmationDialog._get_remove_context_length_2_label
        >>> def _format(msgstr, *args, **kwargs):
        ...     return msgstr.format(*args, **kwargs)
        >>> import builtins
        >>> builtins.__dict__['_format'] = _format

        >>> test("word", [])
        "Remove 'word' only where it occurs at text begin."

        >>> test("word", ["word2"])
        "Remove 'word' only where it occurs after 'word2'."

        >>> test("word", ["word2", "word3"])
        "Remove 'word' only where it occurs after 'word2 word3'."

        >>> test("word", ["<unk>"])
        "Remove 'word' only where it occurs after '<unk>'."

        >>> test("word", ["<s>"])
        "Remove 'word' only where it occurs at sentence begin."

        >>> test("word", ["<num>"])
        "Remove 'word' only where it occurs after numbers."
        """
        hist0 = history[-1] if history else None
        if not hist0 or hist0.startswith("<bot:"):
            label = _format("Remove '{}' only where it occurs at text begin.",
                            suggestion)
        elif hist0 == "<s>":
            label = _format("Remove '{}' only where it occurs at sentence begin.",
                            suggestion)
        elif hist0 == "<num>":
            label = _format("Remove '{}' only where it occurs after numbers.",
                            suggestion)
        else:
            label = _format("Remove '{}' only where it occurs after '{}'.",
                            suggestion, " ".join(history))
        return label

    def run(self):
        keyboard = self._keyboard

        if keyboard:
            keyboard.on_focusable_gui_opening()

        response = Gtk.Dialog.run(self)
        self.destroy()

        if keyboard:
            keyboard.on_focusable_gui_closed()

        # return length of the remove context
        if response == Gtk.ResponseType.OK:
            if self._radio2.get_active():
                return 2
            if self._radio1.get_active():
                return 1
        return 0


    class AuxcharSelection:
        """ Select emoji or other special characters """

        def __init__(self, keyboard, parent):
            self._keyboard = keyboard
            self._parent = parent

        def show(self):

            # turn off AT-SPI listeners to prevent D-BUS deadlocks (Quantal).
            self._keyboard.on_focusable_gui_opening()

            dialog = Gtk.Dialog(title=title,
                            transient_for=self._parent.get_toplevel(),
                            flags=0)
            dialog.add_button(_("_Close"), Gtk.ResponseType.OK)

            # Don't hide dialog behind the keyboard in force-to-top mode.
            if config.is_force_to_top():
                dialog.set_position(Gtk.WindowPosition.CENTER)

            dialog.set_default_response(Gtk.ResponseType.OK)

            # outer vertical box
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=12, border_width=5)

            # search entry
            search_entry = Gtk.SearchEntry()
            box.add(search_entry)

            # glyph tree
            self._glyph_tree_view = Gtl.TreeView()
            init_glyph_tree_view()
            box.add(self._glyph_tree_view)

            dialog.get_content_area().add(box)

            dialog.connect("response", self._on_dialog_response)
            dialog.show_all()

        def _on_dialog_response(self, dialog, response, snippet_id, \
                                        label_entry, text_entry):
            dialog.destroy()

            # Reenable AT-SPI keystroke listeners.
            # Delay this until the dialog is really gone.
            GLib.idle_add(self._keyboard.on_focusable_gui_closed)

        def init_glyph_tree_view(self):
            (
                self.GLYPH_COL_GLYPH,
                self.GLYPH_COL_DESCRIPTION
            ) = range(2)

            self._glyph_model = Gtk.TreeStore(str, int)
            self._glyph_tree_view.set_model(self._glyph_model)
            #model.clear()

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


