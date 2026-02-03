# -*- coding: utf-8 -*-

# Copyright © 2012-2017 marmuta <marmvta@gmail.com>
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

""" Window manipulation and other helpers """

from __future__ import division, print_function, unicode_literals

import time
from math import sqrt, pi

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GLib, Gtk, Gdk

from Onboard.utils import Rect, Version
from Onboard.Timer import Timer
from Onboard.definitions import Handle, HandleFunction

import Onboard.osk as osk

### Logging ###
import logging
_logger = logging.getLogger("WindowUtils")
###############


class WindowManipulator(object):
    """
    Adds resize and move capability to windows.
    Meant for resizing windows without decoration or resize gripper.

    Quirks to remember:

    Keyboard window:
        - Always use threshold when move button was pressed
          in order to support long press to show the touch handles.
        - Never use the threshold for the enlarged touch handles.
          They are only temporarily visible and thus don't need protection.

    IconPalette:
        - Always use threshold when trying to move, otherwise
          clicking to unhide the keyboard window won't work.
    """
    def __init__(self):
        self.hit_frame_width = 10         # size of resize corners and edges
        self.drag_protection = True       # enable protection threshold
        self._temporary_unlock_time = None

        # seconds until protection threshold returns
        # - counts from drag end in fallback mode
        # - counts from drag start in system mode
        #   (unfortunately)
        self.temporary_unlock_delay = 6.0

        self.min_window_size = (50, 50)

        self._drag_start_pointer = None
        self._drag_start_offset  = None
        self._drag_start_rect    = None
        self._drag_handle        = None
        self._drag_handles = Handle.ALL
        self._drag_active        = False  # has window move/resize actually started yet?
        self._drag_threshold     = 8
        self._drag_snap_threshold = 16

        self._lock_x_axis         = False
        self._lock_y_axis         = False

        self._last_drag_handle    = None
        self._monitor_rects       = None  # cache them to save the lookup time

    def set_min_window_size(self, w, h):
        self.min_window_size = (w, h)

    def get_min_window_size(self):
        return self.min_window_size

    def get_hit_frame_width(self):
        return self.hit_frame_width

    def enable_drag_protection(self, enable):
        self.drag_protection = enable

    def reset_drag_protection(self):
        self._temporary_unlock_time = None

    def get_resize_frame_rect(self):
        try:
            return self.get_keyboard_frame_rect()
        except AttributeError:
            return Rect(0, 0,
                        self.get_allocated_width(),
                        self.get_allocated_height())

    def get_drag_start_rect(self):
        return self._drag_start_rect

    def get_drag_window(self):
        return self

    def get_drag_handles(self):
        return self._drag_handles

    def set_drag_handles(self, handles):
        self._drag_handles = handles

    def get_handle_function(self, handle):
        return HandleFunction.NORMAL    

    def get_drag_threshold(self):
        return 8

    def get_always_visible_rect(self):
        """ Rectangle in canvas coordinates that must not leave the screen. """
        return None

    def lock_x_axis(self, lock):
        """ Set to False to constraint movement in x. """
        self._lock_x_axis = lock

    def lock_y_axis(self, lock):
        """ Set to True to constraint movement in y. """
        self._lock_y_axis = lock

    def handle_press(self, sequence, move_on_background = False):
        hit = self.hit_test_move_resize(sequence.point)
        if not hit is None:
            if hit == Handle.MOVE:
                self.start_move_window(sequence.root_point)
            else:
                self.start_resize_window(hit, sequence.root_point)
                
                function = self.get_handle_function(hit)
                if function == HandleFunction.ASPECT_RATIO:
                    self.on_handle_aspect_ratio_pressed()
                    
            return True

        if move_on_background and \
            Handle.MOVE in self.get_drag_handles():
            self.start_move_window(sequence.root_point)
            return True

        return False

    def handle_motion(self, sequence, fallback = False):
        if not self.is_drag_initiated():
            return

        snap_to_cursor = False
        x_root, y_root = sequence.root_point
        dx = x_root - self._drag_start_pointer[0]
        dy = y_root - self._drag_start_pointer[1]

        # distance threshold, protection from accidental drags
        if not self._drag_active:
            d = sqrt(dx*dx + dy*dy)
            drag_active = not self.drag_protection

            if self.drag_protection:
                # snap off for temporary unlocking
                if self._temporary_unlock_time is None and \
                   d >= self._drag_threshold:
                    self._temporary_unlock_time = 1

                    # Snap to cursor position for large drag thresholds
                    # Dragging is smoother without snapping, but for large
                    # thresholds, the cursor ends up far away from the
                    # window and there is a danger of windows going offscreen.
                    if d >= self._drag_snap_threshold:
                        snap_to_cursor = True
                    else:
                        self._drag_start_offset[0] += dx
                        self._drag_start_offset[1] += dy

                if not self._temporary_unlock_time is None:
                    drag_active = True
            else:
                self._temporary_unlock_time = 1 # unlock for touch handles too

            self._drag_active |= drag_active

        # move/resize
        if self._drag_active:
            if fallback:
                self._handle_motion_fallback(dx, dy)
            else:
                self._handle_motion_system(dx, dy, snap_to_cursor, sequence)

            # give keyboard window a chance to react
            self.on_drag_activated()

    def _handle_motion_system(self, dx, dy, snap_to_cursor, sequence):
        """
        Let the window manager do the moving
        This fixes issues like not reaching edges at high move speed
        and not being able to snap off a maximized window.
        Does nothing in force-to-top mode (override redirect or
        type hint "DOCK").
        """
        window = self.get_drag_window()
        if window:
            x, y = sequence.root_point
            if self.is_moving():
                if snap_to_cursor:
                    x = x - dx # snap to cursor
                    y = y - dy
                window.begin_move_drag(1, x, y, sequence.time)
            elif self.is_resizing():

                # Compensate for weird begin_resize_drag behaviour:
                # catch up with the mouse cursor
                if snap_to_cursor:
                    if not self._drag_start_rect.is_point_within((x, y)):
                        x, y = x + dx, y + dy

                window.begin_resize_drag(self._drag_handle, 1,
                                         x, y, sequence.time)

    def stop_system_drag(self):
        """
        Call this when the system drag has ended.
        We need this to kick off the on_drag_done() call for KbdWindow.
        """
        self.stop_drag()

    def _handle_motion_fallback(self, dx, dy):
        """ handle dragging for window move and resize """
        if not self.is_drag_initiated():
            return

        function = self.get_handle_function(self._drag_handle)
        if function == HandleFunction.ASPECT_RATIO:
        
            if self._drag_handle == Handle.WEST:
                dx *= -1

            self.on_handle_aspect_ratio_motion(dx, dy)
        else:
            wx = self._drag_start_pointer[0] + dx - self._drag_start_offset[0]
            wy = self._drag_start_pointer[1] + dy - self._drag_start_offset[1]

            if self._drag_handle == Handle.MOVE:
                # contrain axis movement
                if self._lock_x_axis:
                    wx = self.get_drag_window().get_position()[0]
                if self._lock_y_axis:
                    wx = self.get_drag_window().get_position()[1]

                # move window
                x, y = self.limit_position(wx, wy)
                w, h = None, None
            else:
                # resize window
                wmin, hmin = self.get_min_window_size()
                rect = self._drag_start_rect
                x0, y0, x1, y1 = rect.to_extents()
                w, h = rect.get_size()

                if self._drag_handle in [Handle.NORTH,
                                        Handle.NORTH_WEST,
                                        Handle.NORTH_EAST]:
                    y0 = min(wy, y1 - hmin)
                if self._drag_handle in [Handle.WEST,
                                        Handle.NORTH_WEST,
                                        Handle.SOUTH_WEST]:
                    x0 = min(wx, x1 - wmin)
                if self._drag_handle in [Handle.EAST,
                                        Handle.NORTH_EAST,
                                        Handle.SOUTH_EAST]:
                    x1 = max(wx + w, x0 + wmin)
                if self._drag_handle in [Handle.SOUTH,
                                        Handle.SOUTH_WEST,
                                        Handle.SOUTH_EAST]:
                    y1 = max(wy + h, y0 + hmin)

                x, y, w, h = x0, y0, x1 -x0, y1 - y0

            self._move_resize(x, y, w, h)

    def on_handle_aspect_ratio_pressed(self):
        """
        Overload this to process start of dragging of
        handles with ASPECT_RATIO function.
        """
        pass

    def on_handle_aspect_ratio_motion(self, wx, wy):
        """
        Overload this to process motion changes of 
        handles with ASPECT_RATIO function.
        """
        pass

    def set_drag_cursor_at(self, point, allow_drag_cursors = True):
        """ set the mouse cursor """
        window = self.get_window()
        if not window:
            return

        cursor_type = None
        if allow_drag_cursors or \
           not self._drag_handle is None:    # already dragging a handle?
            cursor_type = self.get_drag_cursor_at(point)

        # set/reset cursor
        if not cursor_type is None:
            cursor = Gdk.Cursor(cursor_type)
            if cursor:
                window.set_cursor(cursor)
        else:
            window.set_cursor(None)

    def reset_drag_cursor(self):
        """ set the mouse cursor """
        window = self.get_window()
        if not window:
            return

        if self._drag_handle is None:    # not dragging a handle?
            window.set_cursor(None)

    def get_drag_cursor_at(self, point):
        hit = self._drag_handle
        if hit is None:
           hit = self.hit_test_move_resize(point)
        if not hit is None and \
           not hit == Handle.MOVE or self.is_drag_active(): # delay it for move
            return Handle.CURSOR_TYPES[hit]
        return None

    def start_move_window(self, point = None):
        self.start_drag(point)
        self._drag_handle = Handle.MOVE
        self._last_drag_handle = self._drag_handle

    def stop_move_window(self):
        self.stop_drag()

    def start_resize_window(self, handle, point = None):
        self.start_drag(point)
        self._drag_handle = handle
        self._last_drag_handle = self._drag_handle

    def start_drag(self, point = None):
        self._monitor_rects = None

        # Find the pointer position for the occasions when we are
        # not being called from an event (move button).
        if not point:
            rootwin = Gdk.get_default_root_window()
            dunno, x_root, y_root, mask = rootwin.get_pointer()
            point = (x_root, y_root)

        # rmember pointer and window positions
        window = self.get_drag_window()
        x, y = window.get_position()
        self._drag_start_pointer = point
        self._drag_start_offset = [point[0] - x, point[1] - y]
        self._drag_start_rect = Rect.from_position_size(window.get_position(),
                                                        window.get_size())
        # not yet actually moving the window
        self._drag_active = False

        # get the threshold
        self._drag_threshold = self.get_drag_threshold()

        # check if the temporary threshold unlocking has expired
        if not self.drag_protection or \
           not self._temporary_unlock_time is None and \
           time.time() - self._temporary_unlock_time > \
                         self.temporary_unlock_delay:
            self._temporary_unlock_time = None

        # give keyboard window a chance to react
        self.on_drag_initiated()

    def stop_drag(self):
        if self.is_drag_initiated():

            if self._temporary_unlock_time is None:
                # snap back to start position
                if self.drag_protection:
                    self._move_resize(*self._drag_start_rect)
            else:
                # restart the temporary unlock period
                self._temporary_unlock_time = time.time()

            self._drag_start_offset = None
            self._drag_handle = None
            self._drag_active = False

            self.move_into_view()

            # give keyboard window a chance to react
            self.on_drag_done()

    def on_drag_initiated(self):
        """
        User controlled drag initiated, but drag hasn't actually begun yet.
        """
        pass

    def on_drag_activated(self):
        """
        Moving/resizing has begun.
        """
        pass

    def on_drag_done(self):
        """
        User controlled drag ended.
        overload this in derived classes.
        """
        pass

    def is_drag_initiated(self):
        """ Button pressed down on a drag handle, not yet actually dragging """
        return bool(self._drag_start_offset)

    def is_drag_active(self):
        """ Are we actually moving/resizing """
        return self.is_drag_initiated() and self._drag_active

    def is_moving(self):
        return self.is_drag_initiated() and self._drag_handle == Handle.MOVE

    def was_moving(self):
        return self._last_drag_handle == Handle.MOVE

    def is_resizing(self):
        return self.is_drag_initiated() and self._drag_handle  != Handle.MOVE

    def move_into_view(self):
        """
        If the window has somehow ended up off-screen,
        move the always-visible-rect back into view.
        """
        window = self.get_drag_window()
        if window:  # don't crash on exit
            x, y = window.get_position()
            _x, _y = self.limit_position(x, y)
            if _x != x or _y != y:
                self._move_resize(_x, _y)

    def force_into_view(self):
        self.move_into_view()
        if False:  # Only for system drag, not needed when using fallback mode
            GLib.idle_add(self._do_force_into_view)

    def _do_force_into_view(self):
        """ Works mostly, but occasionally the window disappears... """
        window = self.get_drag_window()
        x, y = window.get_position()
        _x, _y = self.limit_position(x, y)
        if _x != x or _y != y:
            window.hide()
            self._move_resize(_x, _y)
            window.show()

    def limit_size(self, rect):
        """
        Limit the given window rect to fit on screen.
        """
        screen = self.get_screen()
        limits = Rect(0, 0, screen.get_width(), screen.get_height())
        r = rect.copy()
        if not limits.is_empty():  # LP #1633284
            if r.w > limits.w:
                r.w = limits.w - 40
            if r.h > limits.h:
                r.h = limits.h - 20
        return r

    def limit_position(self, x, y, visible_rect = None, limit_rects = None):
        """
        Limits the given window position to keep the current
        always_visible_rect fully in view.
        """
        # rect to stay always visible, in canvas coordinates
        if visible_rect is None:
            visible_rect = self.get_always_visible_rect()

        if not limit_rects:
            if not self._monitor_rects:
                self._monitor_rects = get_monitor_rects(self.get_screen())
            limit_rects = self._monitor_rects

        x, y = limit_window_position(x, y, visible_rect, limit_rects)
        return x, y

    def hit_test_move_resize(self, point):
        canvas_rect = self.get_resize_frame_rect()
        handles = self.get_drag_handles()
        hit_frame_width = self.get_hit_frame_width()

        w = min(canvas_rect.w / 2, hit_frame_width)
        h = min(canvas_rect.h / 2, hit_frame_width)

        x, y = point
        x0, y0, x1, y1 = canvas_rect.to_extents()

        # try corners first
        for handle in handles:
            if handle == Handle.NORTH_WEST:
                if x >= x0 and x < x0 + w and \
                   y >= y0 and y < y0 + h:
                    return handle

            if handle == Handle.NORTH_EAST:
                if x <= x1 and x > x1 - w and \
                   y >= y0 and y < y0 + h:
                    return handle

            if handle == Handle.SOUTH_EAST:
                if x <= x1 and x > x1 - w and \
                   y <= y1 and y > y1 - h:
                    return handle

            if handle == Handle.SOUTH_WEST:
                if x >= x0 and x < x0 + w and \
                   y <= y1 and y > y1 - h:
                    return handle

        # then check the edges
        for handle in handles:
            if handle == Handle.WEST:
                if x < x0 + w and x >= x0 - 1:
                    return handle
            if handle == Handle.EAST:
                if x > x1 - w and x <= x1 + 1:
                    return handle
            if handle == Handle.NORTH:
                if y < y0 + h:
                    return handle
            if handle == Handle.SOUTH:
                if y > y1 - h:
                    return handle
                    
        return None

    def _move_resize(self, x, y, w = None, h = None):
        #print("_move_resize", x, y, w, h)
        window = self.get_drag_window()
        gdk_win = window.get_window()
        if w is None:
            # Stop inserting edge move for now. In unity, when
            # jamming onboard into the lower left corner the keyboard
            # window disappears (Precise).
            #self._insert_edge_move(window, x, y)
            window.move(x, y)
            #print("_move_resize: move ", x, y, " position ", window.get_position(), " origin ", _win.get_origin(), " root origin ", _win.get_root_origin())
        else:
            if hasattr(window, "move_resize"):
                window.move_resize(x, y, w, h) # keyboard window
            else:
                gdk_win.move_resize(x, y, w, h) # icon palette


    def _insert_edge_move(self, window, x, y):
        """
        Compiz and potentially other window managers silently ignore
        moves outside of some screen edges. When hitting the edge at
        high speed, onboard gets stuck some distance away from it.
        Fix this by inserting an intermediate move right to the edge.
        Does not help with the edge below unity bar.
        """
        limits = self.get_screen_limits()
        one_more_x = x
        one_more_y = y
        pos = window.get_position()
        size = window.get_size()

        if pos[0] > limits.left() and \
           x      < limits.left():
            one_more_x = limits.left()
        if pos[0] + size[0] < limits.right() and \
           x      + size[0] > limits.right():
            one_more_x = limits.right()
        if pos[1] > limits.top() and \
           y      < limits.top():
            one_more_y = limits.top()
        if pos[1] + size[1] < limits.bottom() and \
           y      + size[1] > limits.bottom():
            one_more_x = limits.right()

        if one_more_x != x or one_more_y != y:
            window.move(one_more_x, one_more_y)


class Orientation:
    """ enum for screen orientation """

    class LANDSCAPE: pass
    class PORTRAIT: pass


class WindowRectTracker:
    """
    Keeps track of the window rectangle when moving/resizing.
    Gtk only updates the position and size asynchrounously on
    configure events and hidden windows return invalid values.
    Auto-show et al need valid values from get_position and
    get_size at all times.
    """
    def __init__(self):
        self._window_rect = None
        self._origin = None
        self._client_offset = (0, 0)
        self._override_redirect = False

    def cleanup(self):
        pass

    def update_window_rect(self):
        """
        Call this on configure event, the only time when
        get_position, get_size, etc. can be trusted.
        """
        visible = self.is_visible()
        if visible:
            pos  = Gtk.Window.get_position(self)
            size = Gtk.Window.get_size(self)
            origin = self.get_window().get_origin()
            if len(origin) == 3:   # What is the first parameter for? Gdk bug?
                origin = origin[1:]

            if _logger.isEnabledFor(logging.DEBUG):
                _logger.debug("update_window_rect1: pos {}, size {}, origin {}"
                         .format(pos, size, origin))
            pos = self._apply_window_scaling_factor(pos)

            self._window_rect = Rect.from_position_size(pos, size)
            self._origin = origin
            self._client_offset = (origin[0] - pos[0], origin[1] - pos[1])
            self._screen_orientation = self.get_screen_orientation()

            if _logger.isEnabledFor(logging.DEBUG):
                _logger.debug("update_window_rect2: pos {}, client_offset {}, "
                              "screen_orientation {}"
                              .format(pos,
                                      self._client_offset,
                                      self._screen_orientation))

    def move(self, x, y):
        Gtk.Window.move(self, x, y)

    def resize(self, w, h):
        Gtk.Window.resize(self, w, h)

    def move_resize(self, x, y, w, h):
        win = self.get_window()
        if win:
            win.move_resize(x, y, w, h)

    def get_position(self):
        if self._window_rect is None:
            pos = Gtk.Window.get_position(self)
            pos = self._apply_window_scaling_factor(pos)
        else:
            pos = self._window_rect.get_position()
        return pos

    def get_size(self):
        if self._window_rect is None:
            return Gtk.Window.get_size(self)
        else:
            return self._window_rect.get_size()

    def get_origin(self):
        if self._origin is None:
            win = self.get_window()
            if win:
                origin = win.get_origin()
                if len(origin) == 3:   # What is the first parameter for? Gdk bug?
                    origin = origin[1:]
                return origin
            return 0
        else:
            return self._origin

    def get_client_offset(self):
        return self._client_offset

    def get_rect(self):
        return self._window_rect

    def get_override_redirect(self):
        return self._override_redirect

    def set_override_redirect(self, value):
        self._override_redirect = value
        self.get_window().set_override_redirect(True)

    def get_scale_factor(self):
        gdk_win = self.get_window()
        if gdk_win:
            try:
                return gdk_win.get_scale_factor()
            except AttributeError:  # from Gdk 3.10
                pass
        return None

    def _apply_window_scaling_factor(self, values):
        """
        Gdk doesn't scale position of override redirect windows (Trusty)
        """
        if self._override_redirect:
            scale = self.get_scale_factor()
            if not scale is None:
                scale = 1.0 / scale
                values = (values[0] * scale, values[1] * scale)
        return values

    def get_screen_orientation(self):
        """
        Current orientation of the screen (tablet rotation).
        Only the aspect ratio is taken into account at this time.
        This appears to cover more cases than looking at monitor rotation,
        in particular with multi-monitor screens.
        """
        screen = self.get_screen()
        if screen.get_width() >= screen.get_height():
            return Orientation.LANDSCAPE
        else:
            return Orientation.PORTRAIT


class WindowRectPersist(WindowRectTracker):
    """
    Save and restore window position and size.
    """
    def __init__(self):
        WindowRectTracker.__init__(self)
        self._screen_orientation = None
        self._save_position_timer = Timer()

        # init detection of screen "rotation"
        screen = self.get_screen()
        screen.connect('size-changed', self.on_screen_size_changed)

    def cleanup(self):
        self._save_position_timer.finish()

    def is_visible(self):
        """ This is overloaded in KbdWindow """
        return Gtk.Window.get_visible(self)

    def on_screen_size_changed(self, screen):
        """ detect screen rotation (tablets)"""

        # Give the screen time to settle, the window manager
        # may block the move to previously invalid positions and
        # when docked, the slide animation may be drowned out by all
        # the action in other processes.
        Timer(1.5, self.on_screen_size_changed_delayed, screen)

    def on_screen_size_changed_delayed(self, screen):
        self.restore_window_rect()

    def restore_window_rect(self, startup = False):
        """
        Restore window size and position.
        """
        # Run pending save operations now, so they don't
        # interfere with the window rect after it was restored.
        self._save_position_timer.finish()

        orientation = self.get_screen_orientation()
        rect = self.read_window_rect(orientation)

        self._screen_orientation = orientation
        self._window_rect = rect
        _logger.debug("restore_window_rect {rect}, {orientation}" \
                      .format(rect = rect, orientation = orientation))

        # Give the derived class a chance to modify the rect,
        # for example to correct the position for auto-show.
        rect = self.on_restore_window_rect(rect)
        self._window_rect = rect

        # move/resize the window
        if startup:
            # gnome-shell doesn't take kindly to an initial move_resize().
            # The window ends up at (0, 0) on and goes back there
            # repeatedly when hiding and unhiding.
            self.set_default_size(rect.w, rect.h)
            self.move(rect.x, rect.y)
        else:
            self.move_resize(rect.x, rect.y, rect.w, rect.h)

        # Initialize shadow variables with valid values so they
        # don't get taken from the unreliable window.
        # Fixes bad positioning of the very first auto-show.
        if startup:
            self._window_rect = rect.copy()
            # Ignore frame dimensions; still better than asking the window.
            self._origin      = rect.left_top()
            self._screen_orientation = self.get_screen_orientation()

    def on_restore_window_rect(self, rect):
        return rect

    def save_window_rect(self, orientation=None, rect=None):
        """
        Save window size and position.
        """
        if orientation is None:
            orientation = self._screen_orientation
        if rect is None:
            rect = self._window_rect

        # Give the derived class a chance to modify the rect,
        # for example to override it for auto-show.
        rect = self.on_save_window_rect(rect)

        self.write_window_rect(orientation, rect)

        _logger.debug("save_window_rect {rect}, {orientation}" \
                      .format(rect=rect, orientation=orientation))

    def on_save_window_rect(self, rect):
        return rect

    def read_window_rect(self, orientation, rect):
        """
        Read orientation dependent rect.
        Overload this in derived classes.
        """
        raise NotImplementedError()

    def write_window_rect(self, orientation, rect):
        """
        Write orientation dependent rect.
        Overload this in derived classes.
        """
        raise NotImplementedError()

    def start_save_position_timer(self):
        """
        Trigger saving position and size to gsettings
        Delay this a few seconds to avoid excessive disk writes.

        Remember the current rect and rotation as the screen may have been
        rotated when the saving happens.
        """
        self._save_position_timer.start(5,
                                        self.save_window_rect,
                                        self.get_screen_orientation(),
                                        self.get_rect())

    def stop_save_position_timer(self):
        self._save_position_timer.stop()


def set_unity_property(window):
    """
    Set custom X window property to tell unity 3D this is an on-screen
    keyboard that wants to be raised on top of dash. See LP 739812, 915250.
    Since onboard started detecting dash itself this isn't really needed
    for unity anymore. Leave it anyway, it may come in handy in the future.
    """
    gdk_win = window.get_window()
    if gdk_win:
        if hasattr(gdk_win, "get_xid"):  # not on wayland
            xid = gdk_win.get_xid()
            osk.Util().set_x_property(xid, "ONSCREEN_KEYBOARD", 1)


class DwellProgress(object):

    # dwell time in seconds
    dwell_delay = 4

    # time of dwell start
    dwell_start_time = None

    opacity = 1.0

    def is_dwelling(self):
        return self.dwell_start_time is not None

    def is_done(self):
        return time.time() > self.dwell_start_time + self.dwell_delay

    def start_dwelling(self):
        self.dwell_start_time = time.time()

    def stop_dwelling(self):
        self.dwell_start_time = None

    def draw(self, context, rect, rgba=(1, 0, 0, .75), rgba_bg = None):
        if self.is_dwelling():
            if self.opacity <= 0.0:
                pass
            if self.opacity >= 1.0:
                self._draw_dwell_progress(context, rect, rgba, rgba_bg)
            else:
                context.save()
                context.rectangle(*rect.int())
                context.clip()
                context.push_group()

                self._draw_dwell_progress(context, rect, rgba, rgba_bg)

                context.pop_group_to_source()
                context.paint_with_alpha(self.opacity)
                context.restore()

    def _draw_dwell_progress(self, context, rect, rgba, rgba_bg):
            xc, yc = rect.get_center()

            radius = min(rect.w, rect.h) / 2.0

            alpha0 = -pi / 2.0
            k = (time.time() - self.dwell_start_time) / self.dwell_delay
            k = min(k, 1.0)
            alpha = k * pi * 2.0

            if rgba_bg:
                context.set_source_rgba(*rgba_bg)
                context.move_to(xc, yc)
                context.arc(xc, yc, radius, 0, 2 * pi)
                context.close_path()
                context.fill()

            context.move_to(xc, yc)
            context.arc(xc, yc, radius, alpha0, alpha0 + alpha)
            context.close_path()

            context.set_source_rgba(*rgba)
            context.fill_preserve()

            context.set_source_rgba(0, 0, 0, 1)
            context.set_line_width(0)
            context.stroke()


def limit_window_position(x, y, always_visible_rect, limit_rects = None):
    """
    Limits the given window position to keep the
    always_visible_rect fully in view.
    """
    # rect to stay always visible, in canvas coordinates
    r = always_visible_rect

    if r is not None:
        r = r.int()  # avoid rounding errors

        # transform always visible rect to screen coordinates,
        # take window decoration into account.
        rs = r.copy()
        rs.x += x
        rs.y += y

        dmin = None
        rsmin = None
        for limits in limit_rects:
            # get limited candidate rect
            rsc = rs.copy()
            rsc.x = max(rsc.x, limits.left())
            rsc.x = min(rsc.x, limits.right() - rsc.w)
            rsc.y = max(rsc.y, limits.top())
            rsc.y = min(rsc.y, limits.bottom() - rsc.h)

            # closest candidate rect wins
            cx, cy = rsc.get_center()
            dx, dy = rs.x - rsc.x, rs.y - rsc.y
            d = dx * dx + dy * dy
            if dmin is None or d < dmin:
                dmin = d
                rsmin = rsc

        x = rsmin.x - r.x
        y = rsmin.y - r.y

    return x, y

def get_monitor_rects(screen):
    """
    Screen limits, one rect per monitor. Monitors may have
    different sizes and arbitrary relative positions.
    """
    rects = []
    if screen:
        for i in range(screen.get_n_monitors()):
            r = screen.get_monitor_geometry(i)
            rects.append(Rect(r.x, r.y, r.width, r.height))
    else:
        rootwin = Gdk.get_default_root_window()
        r = Rect.from_position_size(rootwin.get_position(),
                                (rootwin.get_width(), rootwin.get_height()))
        rects.append(r)
    return rects

def canvas_to_root_window_rect(window, rect):
    """
    Convert rect in canvas coordinates to root window coordinates.
    """
    gdk_win = window.get_window()
    if gdk_win:
        x0, y0 = gdk_win.get_root_coords(rect.x, rect.y)
        x1, y1 = gdk_win.get_root_coords(rect.x + rect.w,
                                         rect.y + rect.h)
        rect = Rect.from_extents(x0, y0, x1, y1)
    else:
        rect = Rect()

    return rect

def canvas_to_root_window_point(window, point):
    """
    Convert point in canvas coordinates to root window coordinates.
    """
    gdk_win = window.get_window()
    if gdk_win:
        point = gdk_win.get_root_coords(*point)
    else:
        point(0, 0)
    return point

def get_monitor_dimensions(window):
    """ Geometry and physical size of the monitor at window. """
    gdk_win = window.get_window()
    screen = window.get_screen()
    if gdk_win and screen:
        monitor = screen.get_monitor_at_window(gdk_win)
        r = screen.get_monitor_geometry(monitor)
        size = (r.width, r.height)
        size_mm = (screen.get_monitor_width_mm(monitor),
                   screen.get_monitor_height_mm(monitor))

        # Nexus7 simulation
        device = None       # keep this at None
        # device = 1
        if device == 0:     # dimension unavailable
            size_mm = 0, 0
        elif device == 1:     # Nexus 7, as it should report
            size = 1280, 800
            size_mm = 150, 94

        dpi_x = size[0] * 25.4 / size_mm[0] if size_mm[0] != 0 else 0
        dpi_y = size[1] * 25.4 / size_mm[1] if size_mm[1] != 0 else 0

        if dpi_x < 50 or dpi_x > 350 or dpi_y < 50 or dpi_y > 350:
            size_mm = 0, 0

        return size, size_mm
    else:
        return (0, 0), (0, 0)

def physical_to_monitor_pixel_size(window, size_mm, fallback_size = (0, 0)):
    """
    Convert a physical size in mm to pixels of windows's monitor,
    """
    sz, sz_mm = get_monitor_dimensions(window)
    if sz[0] > 0 and sz[1] > 0 and \
       sz_mm[0] > 0 and sz_mm[1] > 0:
        w = sz[0] * size_mm[0] / sz_mm[0] \
            if sz_mm[0] else fallback_size[0]
        h = sz[1] * size_mm[1] / sz_mm[1] \
            if sz_mm[0] else fallback_size[1]
    else:
        w, h = fallback_size
    return w, h

def show_error_dialog(error_string):
    """ Show an error dialog """

    error_dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR,
                                  message_format=error_string,
                                  buttons=Gtk.ButtonsType.OK)
    error_dlg.run()
    error_dlg.destroy()

def show_ask_string_dialog(question, parent=None):
    question_dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION,
                                        buttons=Gtk.ButtonsType.OK_CANCEL)
    if parent:
        question_dialog.set_transient_for(parent)
    question_dialog.set_markup(question)
    entry = Gtk.Entry()
    entry.connect("activate", lambda event:
        question_dialog.response(Gtk.ResponseType.OK))
    question_dialog.get_message_area().add(entry)
    question_dialog.show_all()
    response = question_dialog.run()
    text = entry.get_text() if response == Gtk.ResponseType.OK else None
    question_dialog.destroy()
    return text

def show_confirmation_dialog(question, parent=None, center=False, title=None):
    """
    Show this dialog to ask confirmation before executing a task.
    """
    if title is None:
        # Default dialog title: name of the application
        title = _("Onboard")
    dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION,
                            text=question,
                            title=title,
                            buttons=Gtk.ButtonsType.YES_NO)
    if parent:
        dlg.set_transient_for(parent)

    if center:
        dlg.set_position(Gtk.WindowPosition.CENTER)

    response = dlg.run()
    dlg.destroy()
    return response == Gtk.ResponseType.YES

def show_new_device_dialog(name, config_string, is_pointer, callback):
    """
    Show a "New Input Device" dialog.
    """
    dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.OTHER,
                               title=_("New Input Device"),
                               text=_("Onboard has detected a new input device"))
    if is_pointer:
        dialog.set_image(Gtk.Image(icon_name="input-mouse",
                                   icon_size=Gtk.IconSize.DIALOG))
    else:
        dialog.set_image(Gtk.Image(icon_name="input-keyboard",
                                   icon_size=Gtk.IconSize.DIALOG))

    secondary  = "<i>{}</i>\n\n".format(name)
    secondary += _("Do you want to use this device for keyboard scanning?")

    dialog.format_secondary_markup(secondary)

    # Translators: cancel button of "New Input Device" dialog. It used to be
    # stock item STOCK_CANCEL until Gtk 3.10 deprecated those.
    dialog.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(_("Use device"), Gtk.ResponseType.ACCEPT).grab_default()
    dialog.connect("response", _show_new_device_dialog_response,
                   callback, config_string)
    dialog.show_all()

def _show_new_device_dialog_response(dialog, response, callback, config_string):
    """ Callback for the "New Input Device" dialog. """
    if response == Gtk.ResponseType.ACCEPT:
        callback(config_string)
    dialog.destroy()

def gtk_has_resize_grip_support():
    """ Gtk from 3.14 removes resize grips. """
    gtk_version = Version(Gtk.MAJOR_VERSION, Gtk.MINOR_VERSION)
    return gtk_version < Version(3, 14)


