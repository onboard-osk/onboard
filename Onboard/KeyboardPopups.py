# -*- coding: utf-8 -*-

# Copyright © 2013-2016 marmuta <marmvta@gmail.com>
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

import threading
import cairo

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository          import Gdk, Gtk, Pango, PangoCairo

from Onboard.utils          import Rect, roundrect_arc, Version
from Onboard.Timer          import Timer
from Onboard.WindowUtils    import limit_window_position, \
                                   get_monitor_rects, \
                                   canvas_to_root_window_rect, \
                                   get_monitor_dimensions, \
                                   WindowRectTracker, \
                                   gtk_has_resize_grip_support
from Onboard.TouchInput     import TouchInput
from Onboard                import KeyCommon
from Onboard.Layout         import LayoutRoot, LayoutPanel
from Onboard.LayoutView     import LayoutView
from Onboard.KeyGtk         import RectKey
from Onboard.KeyCommon      import ImageSlot
import Onboard.osk as osk

### Logging ###
import logging
_logger = logging.getLogger(__name__)
###############

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

# prepare mask for faster access
BUTTON123_MASK = Gdk.ModifierType.BUTTON1_MASK | \
                 Gdk.ModifierType.BUTTON2_MASK | \
                 Gdk.ModifierType.BUTTON3_MASK
popup_fill_color = None
popup_label_color = None
popup_color_scheme_name = None
popup_color_lock = threading.Lock()


class TouchFeedback:
    """ Display magnified labels as touch feedback """

    def __init__(self):
        self._key_feedback_popup_pool = []
        self._visible_key_feedback_popups = {}

    def show(self, key, view):
        if not key in self._visible_key_feedback_popups:  # not already shown?
            r = key.get_canvas_border_rect()
            root_rect = canvas_to_root_window_rect(view, r)
            toplevel = view.get_toplevel()

            popup = self._get_free_key_feedback_popup()
            if popup is None:
                popup = LabelPopup()
                popup.set_transient_for(toplevel)
                self._key_feedback_popup_pool.append(popup)
                popup.realize()

            # Set window size
            w, h = self._get_popup_size(popup)
            popup.set_default_size(w, h)
            popup.resize(w, h)

            popup.set_key(key)
            popup.position_at(root_rect.x + root_rect.w * 0.5,
                              root_rect.y, 0.5, 1)
            popup.supports_alpha = view.supports_alpha
            if popup.supports_alpha:
                popup.set_opacity(toplevel.get_opacity())
            popup.show_all()

            self._visible_key_feedback_popups[key] = popup

    def hide(self, key = None):
        keys = [key] if key else list(self._visible_key_feedback_popups.keys())
        for _key in keys:
            popup = self._visible_key_feedback_popups.get(_key)
            if popup:
                popup.hide()
                popup.set_key(None)
                del self._visible_key_feedback_popups[_key]

    def raise_all(self):
        for key, popup in self._visible_key_feedback_popups.items():
            win = popup.get_window()
            if win:
                win.raise_()

    def _get_free_key_feedback_popup(self):
        """ Get a currently unused one from the pool of popups. """
        for popup in self._key_feedback_popup_pool:
            if not popup.get_key():
                return popup
        return None

    def _get_popup_size(self, window):
        DEFAULT_POPUP_SIZE_MM = 18.0
        MAX_POPUP_SIZE_PX = 120.0  # fall-back if phys. monitor  size unavail.

        gdk_win = window.get_window()

        w = config.keyboard.touch_feedback_size
        if w == 0:
            sz, sz_mm = get_monitor_dimensions(window)
            if sz[0] > 0 and sz_mm[0] > 0:
                default_size_mm = DEFAULT_POPUP_SIZE_MM

                # scale for hires displays
                if gdk_win:
                    try:
                        default_size_mm *= gdk_win.get_scale_factor() # from Gdk 3.10
                    except AttributeError:
                        pass

                w = sz[0] * default_size_mm / sz_mm[0]
            elif sz[0] > 0:
                w = min(sz[0] / 12.0, MAX_POPUP_SIZE_PX)
            else:   # LP #1633284
                w = MAX_POPUP_SIZE_PX

        return w, w * (1.0 + LabelPopup.ARROW_HEIGHT)


class KeyboardPopup(WindowRectTracker, Gtk.Window):
    """ Abstract base class for popups. """

    def __init__(self, **window_args):
        self._opacity = 1.0

        WindowRectTracker.__init__(self)

        args = {
            "skip_taskbar_hint" : True,
            "skip_pager_hint" : True,
            "urgency_hint" : False,
            "decorated" : False,
            "accept_focus" : False,
            "opacity" : 1.0,
            "app_paintable" : True,
        }

        if gtk_has_resize_grip_support():
            args["has_resize_grip"] = False

        args.update(window_args)

        Gtk.Window.__init__(self, **args)

        self.set_keep_above(True)

        # use transparency if available)
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        self.supports_alpha = False
        if visual:
            self.set_visual(visual)

            # Somehow Gtk 3.4 still needs these now deprecated calls
            # for LayoutPopups, even though IconPalette and LabelPopups
            # don't. Otherwise there will be a white background.
            gtk_version = Version(Gtk.MAJOR_VERSION, Gtk.MINOR_VERSION)
            if gtk_version < Version(3, 18):  # Xenial doesn't need them
                self.override_background_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
            self.supports_alpha = True

    def set_opacity(self, opacity):
        """ Override deprecated Gtk function of the same name """
        self._opacity = opacity

    def get_opacity(self):
        """ Override deprecated Gtk function of the same name """
        return self._opacity

    def on_draw(self, widget, context):
        raise NotImplementedError()

    def position_at(self, x, y, x_align, y_align):
        """
        Align the window with the given point.
        x, y in root window coordinates.
        """
        rect = Rect.from_position_size(self.get_position(), self.get_size())
        rect = rect.align_at_point(x, y, x_align, y_align)
        rect = self.limit_to_workarea(rect)
        x, y = rect.get_position()

        self.move(x, y)

    def limit_to_workarea(self, rect):
        visible_rect = Rect(0, 0, rect.w, rect.h)

        x, y = limit_window_position(rect.x, rect.y, visible_rect,
                                     get_monitor_rects(self.get_screen()))
        return Rect(x, y, rect.w, rect.h)


class KeyboardPopupDrawable(KeyboardPopup):
    """ Abstract base class for popups. """

    def __init__(self):
        super(KeyboardPopupDrawable,self).__init__()

        # In Precise, directly drawing on the top level window has no effect.
        # The Cairo target surface is correctly rendered, but somehow it
        # doesn't become visible. Compositing or not doesn't matter.
        # It's most likely an old issue with Gtk/Gdk. Later releases like
        # Trusty, Vivid are unaffected.
        # -> Create a widget we can successfully draw on anywhere.
        self.drawing_area = Gtk.DrawingArea()
        self.add(self.drawing_area)
        self.drawing_area.connect("draw", self.on_draw)

        # use transparency if available
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual:
            self.drawing_area.set_visual(visual)

            # Somehow Gtk 3.4 still needs these now deprecated calls
            # for LayoutPopups, even though IconPalette and LabelPopups
            # don't. Otherwise there will be a white background.
            gtk_version = Version(Gtk.MAJOR_VERSION, Gtk.MINOR_VERSION)
            if gtk_version < Version(3, 18):  # Xenial doesn't need them
                self.drawing_area.override_background_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))

    def on_draw(self, widget, context):
        raise NotImplementedError()


class LabelPopup(KeyboardPopupDrawable):
    """ Ephemeral popup displaying a key label without user interaction. """

    ARROW_HEIGHT = 0.13
    ARROW_WIDTH  = 0.3
    LABEL_MARGIN = 0.1

    _pango_layout = None
    try:
        _osk_util = osk.Util()
    except AttributeError:
        _osk_util = None

    def __init__(self):
        KeyboardPopupDrawable.__init__(self)
        self._key = None
        self.connect("realize", self._on_realize_event)

    def _on_realize_event(self, user_data):
        self.set_override_redirect(True)

        # set minimal input shape for the popup to become click-through
        win = self.get_window()
        if self._osk_util is not None and win is not None:
            self._osk_util.set_input_rect(win, 0, 0, 1, 1)

    def on_draw(self, widget, context):
        global popup_fill_color, popup_label_color
        if not LabelPopup._pango_layout:
            LabelPopup._pango_layout = Pango.Layout(context=Gdk.pango_context_get())

        rect = Rect(0, 0, self.get_allocated_width(),
                          self.get_allocated_height())
        content_rect = Rect(rect.x, rect.y, rect.w,
                            rect.h - rect.h * self.ARROW_HEIGHT)
        arrow_rect   = Rect(rect.x, content_rect.bottom(), rect.w,
                            rect.h * self.ARROW_HEIGHT) \
                       .deflate((rect.w - rect.w * self.ARROW_WIDTH) / 2.0, 0)

        label_rect = content_rect.deflate(rect.w * self.LABEL_MARGIN)
            
        context.save()
        context.set_operator(cairo.OPERATOR_CLEAR)
        context.paint()
        context.restore()

        context.push_group()

        context.set_source_rgba(*popup_fill_color)
        roundrect_arc(context, content_rect, config.CORNER_RADIUS)
        context.fill()

        l, t, r, b = arrow_rect.to_extents()
        t -= 1
        context.move_to(l, t)
        context.line_to(r, t)
        context.line_to((l+r) / 2, b)
        context.fill()

        # draw label/image
        pixbuf = self._key.get_image(label_rect.w, label_rect.h)
        if pixbuf:
            # color = self._key.get_image_color()
            image_style = self._key.image_style
            pixbuf.draw(context, label_rect, popup_label_color, image_style)
        else:
            label = self._key.get_label()
            if label:
                if label == " ":
                    label = "␣"
                self._draw_text(context, label, label_rect, popup_label_color)

        context.pop_group_to_source()
        context.paint_with_alpha(self._opacity)

    def _draw_text(self, context, text, rect, rgba):
        layout = self._pango_layout
        layout.set_text(text, -1)

        # find text extents
        font_description = Pango.FontDescription( \
                                        config.theme_settings.key_label_font)
        base_extents = self._calc_base_layout_extents(layout, font_description)

        # scale label to the available rect
        font_size = self._calc_font_size(rect, base_extents)
        font_description.set_size(max(1, font_size))
        layout.set_font_description(font_description)

        # center
        w, h = layout.get_size()
        w /= Pango.SCALE
        h /= Pango.SCALE
        offset = rect.align_rect(Rect(0, 0, w, h)).get_position()

        # draw
        context.move_to(*offset)
        context.set_source_rgba(*rgba)
        PangoCairo.show_layout(context, layout)

    @staticmethod
    def _calc_font_size(rect, base_extents):
        size_for_maximum_width  = rect.w / base_extents[0]
        size_for_maximum_height = rect.h / base_extents[1]
        if size_for_maximum_width < size_for_maximum_height:
            return int(size_for_maximum_width)
        else:
            return int(size_for_maximum_height)

    @staticmethod
    def _calc_base_layout_extents(layout, font_description):
        BASE_FONTDESCRIPTION_SIZE = 10000000

        font_description.set_size(BASE_FONTDESCRIPTION_SIZE)
        layout.set_font_description(font_description)

        w, h = layout.get_size()   # In Pango units
        w = w or 1.0
        h = h or 1.0
        extents = (w / (Pango.SCALE * BASE_FONTDESCRIPTION_SIZE),
                   h / (Pango.SCALE * BASE_FONTDESCRIPTION_SIZE))
        return extents

    def get_key(self):
        return self._key

    def set_key(self, key):
        global popup_fill_color, popup_label_color, popup_color_scheme_name
        # background
        if key:
            if not popup_color_scheme_name or popup_color_scheme_name != key.color_scheme.name:
                with popup_color_lock:
                    root_key_group = key.color_scheme._root.get_default_key_group()
                    if root_key_group:
                        fill_rgb, fill_opacity = \
                                root_key_group.find_element_color("fill", {})

                        label_rgb, label_opacity = \
                                root_key_group.find_element_color("label", {})
                                
                    if fill_rgb is None: 
                        fill_rgb = key.color_scheme.get_key_default_rgba(key, "fill", {})[:3]

                    if fill_opacity is None:
                        fill_opacity = key.color_scheme.get_key_default_rgba(key, "fill", {})[3]     
                    
                    if label_rgb is None: 
                        label_rgb = key.color_scheme.get_key_default_rgba(key, "label", {})[:3]

                    if label_opacity is None:
                        label_opacity = key.color_scheme.get_key_default_rgba(key, "label", {})[3]     
                            
                    popup_fill_color = fill_rgb + [fill_opacity]
                    popup_label_color = label_rgb + [label_opacity]
                    popup_color_scheme_name = key.color_scheme.name
        self._key = key


class LayoutPopup(KeyboardPopupDrawable, LayoutView, TouchInput):
    """ Popup showing a (sub-)layout tree. """

    IDLE_CLOSE_DELAY = 0  # seconds of inactivity until window closes

    def __init__(self, keyboard, notify_done_callback):
        self._layout = None
        self._notify_done_callback = notify_done_callback
        self._drag_selected = False # grazed by the pointer?

        KeyboardPopupDrawable.__init__(self)
        LayoutView.__init__(self, keyboard)
        TouchInput.__init__(self)

        self.connect("destroy", self._on_destroy_event)

        self._close_timer = Timer()
        self.start_close_timer()

    def cleanup(self):
        self.stop_close_timer()

        # fix label popup staying visible on double click
        self.keyboard.hide_touch_feedback()

        LayoutView.cleanup(self)  # deregister from keyboard

    def get_toplevel(self):
        return self

    def set_layout(self, layout, frame_width):
        self._layout = layout
        self._frame_width = frame_width

        self.update_labels()

        # set window size
        layout_canvas_rect = layout.get_canvas_border_rect()
        canvas_rect = layout_canvas_rect.inflate(frame_width)
        w, h = canvas_rect.get_size()
        self.set_default_size(w + 1, h + 1)

    def get_layout(self):
        return self._layout

    def get_frame_width(self):
        return self._frame_width

    def got_motion(self):
        """ Has the pointer ever entered the popup? """
        return self._drag_selected

    def handle_realize_event(self):
        self.set_override_redirect(True)
        super(LayoutPopup, self).handle_realize_event()

    def _on_destroy_event(self, user_data):
        self.cleanup()

    def on_enter_notify(self, widget, event):
        self.stop_close_timer()

    def on_leave_notify(self, widget, event):
        self.start_close_timer()

    def on_input_sequence_begin(self, sequence):
        self.stop_close_timer()
        key = self.get_key_at_location(sequence.point)
        if key:
            sequence.active_key = key
            self.keyboard.key_down(key, self, sequence)

    def on_input_sequence_update(self, sequence):
        if sequence.state & BUTTON123_MASK:
            key = self.get_key_at_location(sequence.point)

            # drag-select new active key
            active_key = sequence.active_key
            if not active_key is key and \
               (active_key is None or not active_key.activated):
                sequence.active_key = key
                self.keyboard.key_up(active_key, self, sequence, False)
                self.keyboard.key_down(key, self, sequence, False)
                self._drag_selected = True

    def on_input_sequence_end(self, sequence):
        key = sequence.active_key
        if key:
            keyboard = self.keyboard
            keyboard.key_up(key, self, sequence)

        if key and \
           not self._drag_selected:
            Timer(config.UNPRESS_DELAY, self.close_window)
        else:
            self.close_window()

    def on_draw(self, widget, context):
        context.push_group()

        LayoutView.draw(self, widget, context)

        context.pop_group_to_source()
        context.paint_with_alpha(self._opacity)

    def draw_window_frame(self, context, lod):
        corner_radius = config.CORNER_RADIUS
        border_rgba = self.get_popup_window_rgba("border")
        alpha = border_rgba[3]

        colors = [
                  [[0.5, 0.5, 0.5, alpha], 0  , 1],
                  [border_rgba,            1.5, 2.0],
                 ]

        rect = Rect(0, 0, self.get_allocated_width(),
                          self.get_allocated_height())

        for rgba, pos, width in colors:
            r = rect.deflate(width)
            roundrect_arc(context, r, corner_radius)
            context.set_line_width(width)
            context.set_source_rgba(*rgba)
            context.stroke()

    def close_window(self):
        self._notify_done_callback()

    def start_close_timer(self):
        if self.IDLE_CLOSE_DELAY:
            self._close_timer.start(self.IDLE_CLOSE_DELAY, self.close_window)

    def stop_close_timer(self):
        self._close_timer.stop()


class LayoutBuilder:

    @staticmethod
    def build(source_key, color_scheme, layout):
        context = source_key.context

        frame_width = LayoutBuilder._calc_frame_width(context)

        layout = LayoutRoot(layout)
        layout.update_log_rects()
        log_rect = layout.get_border_rect()
        canvas_rect = Rect(frame_width, frame_width,
                           log_rect.w * context.scale_log_to_canvas_x(1.0),
                           log_rect.h * context.scale_log_to_canvas_y(1.0))
        layout.fit_inside_canvas(canvas_rect)

        return layout, frame_width

    @staticmethod
    def _calc_frame_width(context):
        # calculate border around the layout
        canvas_border = context.scale_log_to_canvas((1, 1))
        return config.POPUP_FRAME_WIDTH + min(canvas_border)


class LayoutBuilderKeySequence(LayoutBuilder):

    MAX_KEY_COLUMNS  = 8  # max number of keys in one row

    @staticmethod
    def build(source_key, color_scheme, key_sequence):
        # parse sequence into lines
        lines, ncolumns = \
            LayoutBuilderKeySequence ._layout_sequence(key_sequence)
        return LayoutBuilderKeySequence._create_layout(source_key,
                                                       color_scheme,
                                                       lines, ncolumns)

    @staticmethod
    def _create_layout(source_key, color_scheme, lines, ncolumns):
        context = source_key.context
        frame_width = LayoutBuilderKeySequence._calc_frame_width(context)

        nrows = len(lines)
        spacing = (1, 1)

        # calc canvas size
        rect = source_key.get_canvas_border_rect()
        layout_canvas_rect = Rect(frame_width, frame_width,
                              rect.w * ncolumns + spacing[0] * (ncolumns - 1),
                              rect.h * nrows + spacing[1] * (nrows - 1))

        # subdivide into logical rectangles for the keys
        layout_rect = context.canvas_to_log_rect(layout_canvas_rect)
        key_rects = layout_rect.subdivide(ncolumns, nrows, *spacing)

        # create keys, slots for empty labels are skipped
        keys = []
        for i, line in enumerate(lines):
            for j, item in enumerate(line):
                slot = i * ncolumns + j
                if item:
                    # control item?
                    key = item
                    key.set_border_rect(key_rects[slot])
                    key.group = "alternatives"
                    key.color_scheme = color_scheme
                    keys.append(key)

        item = LayoutPanel()
        item.border  = 0
        item.set_items(keys)
        layout = LayoutRoot(item)

        layout.fit_inside_canvas(layout_canvas_rect)

        return layout, frame_width

    @staticmethod
    def _layout_sequence(sequence):
        """
        Split sequence into lines.
        """
        max_columns = LayoutBuilderAlternatives.MAX_KEY_COLUMNS
        min_columns = max_columns // 2
        add_close = False
        fill_gaps = True

        # find the number of columns with the best packing,
        # i.e. the least number of empty slots.
        n = len(sequence)
        if add_close:
            n += 1    # +1 for close button
        max_mod = 0
        ncolumns = max_columns
        for i in range(max_columns, min_columns, -1):
            m = n % i
            if m == 0:
                max_mod = m
                ncolumns = i
                break
            if max_mod < m:
                max_mod = m
                ncolumns = i

        # limit to len for the single row case
        ncolumns = min(n, ncolumns)

        # cut the input into lines of the newly found optimal length
        lines = []
        line = []
        column = 0
        for item in sequence:
            line.append(item)
            column += 1
            if column >= ncolumns:
                lines.append(line)
                line = []
                column = 0

        # append close button
        if add_close:
            n = len(line)
            line.extend([None]*(ncolumns - (n+1)))

            key = RectKey("_close_")
            key.labels = {}
            key.image_filenames = {ImageSlot.NORMAL : "close.svg"}
            key.type = KeyCommon.BUTTON_TYPE
            line.append(key)

        # fill empty slots with dummy buttons
        if fill_gaps:
            n = len(line)
            if n:
                for i in range(ncolumns - n):
                    key = RectKey("_dummy_")
                    key.sensitive = False
                    line.append(key)

        if line:
            lines.append(line)

        return lines, ncolumns


class LayoutBuilderAlternatives(LayoutBuilderKeySequence):

    @staticmethod
    def build(source_key, color_scheme, alternatives):
        key_sequence = []
        for i, label in enumerate(alternatives):
            key = RectKey("_alternative" + str(i))
            key.type = KeyCommon.CHAR_TYPE
            key.labels = {0: label}
            key.code  = label[0]
            key_sequence.append(key)

        return LayoutBuilderKeySequence.build(source_key, color_scheme,
                                              key_sequence)


class PendingSeparatorPopup(KeyboardPopupDrawable):
    """ Ephemeral popup displaying the pending word separator. """

    try:
        _osk_util = osk.Util()
    except AttributeError:
        _osk_util = None

    def __init__(self):
        KeyboardPopupDrawable.__init__(self)
        self.connect("realize", self._on_realize_event)
        self._visible = False

    def show_at(self, view, character_rect):
        toplevel = view.get_toplevel()

        self.set_transient_for(toplevel)
        self.realize()

        x, y, w, h = character_rect
        self.set_default_size(w, h)
        self.resize(w, h)
        self.move(x, y)

        self.supports_alpha = view.supports_alpha
        if self.supports_alpha:
            self.set_opacity(toplevel.get_opacity())
        self.show_all()
        self._visible = True

    def hide(self):
        KeyboardPopupDrawable.hide(self)
        self._visible = False

    def is_visible(self):
        return self._visible

    def _on_realize_event(self, user_data):
        self.set_override_redirect(True)

        # set minimal input shape for the popup to become click-through
        win = self.get_window()
        self._osk_util.set_input_rect(win, 0, 0, 1, 1)

    def on_draw(self, widget, cr):
        rect = Rect(0, 0,
                    self.get_allocated_width(), self.get_allocated_height())

        fill_rgba = (1.0, 0.5, 0.5, 0.2)
        stroke_rgba = (1.0, 0.5, 0.5, 0.7)

        cr.set_source_rgba(*fill_rgba)
        cr.rectangle(*rect)
        cr.fill()

        r = rect
        top = r.y + r.h * 0.75
        bottom = r.bottom()
        cr.set_source_rgba(*stroke_rgba)
        cr.set_line_width(max(1, r.h / 6))
        cr.move_to(r.left(), top)
        cr.line_to(r.left(), bottom)
        cr.line_to(r.right(), bottom)
        cr.line_to(r.right(), top)
        cr.stroke()

