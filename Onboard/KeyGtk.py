# -*- coding: UTF-8 -*-

# Copyright © 2007 Martin Böhme <martin.bohm@kubuntu.org>
# Copyright © 2009 Chris Jones <tortoise@tortuga>
# Copyright © 2010 Francesco Fumanti <francesco.fumanti@gmx.net>
# Copyright © 2011 Alan Bell <alanbell@ubuntu.com>
# Copyright © 2012 Gerd Kohlberger <lowfi@chello.at>
# Copyright © 2009-2014, 2016 marmuta <marmvta@gmail.com>
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

from math import pi, sin, cos, sqrt

import cairo
from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GLib, Gdk, Pango, PangoCairo, GdkPixbuf

from Onboard.KeyCommon   import (KeyCommon, RectKeyCommon,
                                 LOD, InputlineKeyCommon, ImageStyle,
                                 ImageSlot)
from Onboard.KeyCommon   import *
from Onboard.WindowUtils import DwellProgress
from Onboard.utils       import (brighten, unicode_str,
                                 gradient_line, drop_shadow,
                                 roundrect_curve, roundrect_curve_custom,
                                 rounded_path,
                                 rounded_polygon_path_to_cairo_path)

import logging
_logger = logging.getLogger("KeyGTK")

from Onboard.Config import Config
config = Config()

PangoUnscale = 1.0 / Pango.SCALE


class Key(KeyCommon):
    _pango_layouts = None
    _label_extents = None  # resolution independent size {mod_mask: (w, h)}
    _popup_indicator = ""  # font dependent popup indicator (ellipsis)

    _shadow_steps  = 0
    _shadow_alpha  = 0
    _shadow_presets = ((1, 0.015), (4, 0.005)) # quality presets (steps, alpha)

    def __init__(self):
        KeyCommon.__init__(self)
        self._label_extents = {}

    def get_best_font_size(self):
        """
        Get the maximum font possible that would not cause the label to
        overflow the boundaries of the key.
        """

        raise NotImplementedError()

    @staticmethod
    def reset_pango_layout():
        Key._pango_layouts = None

    @staticmethod
    def get_pango_layout(text, font_size, slot = 0):
        # work around memory leak (gnome #599730)
        if Key._pango_layouts is None:
            # use PangoCairo.create_layout once it works with gi (pango >= 1.29.1)
            #Key._pango_layouts = PangoCairo.create_layout(context)
            Key._pango_layouts = (
                Pango.Layout(context = Gdk.pango_context_get()),
                Pango.Layout(context = Gdk.pango_context_get()),
                Pango.Layout(context = Gdk.pango_context_get()))

        layout = Key._pango_layouts[slot]
        Key.prepare_pango_layout(layout, text, font_size)
        return layout

    @staticmethod
    def prepare_pango_layout(layout, text, font_size):
        if text is None:
            text = ""
        layout.set_text(text, -1)
        layout.set_width(-1) # no wrapping, ellipsization
        font_description = Pango.FontDescription(config.theme_settings.key_label_font)
        font_description.set_size(max(1, font_size))
        layout.set_font_description(font_description)

    @classmethod
    def set_shadow_quality(_class, quality):
        if quality is None:
            quality = 1
        _class._shadow_steps, _class._shadow_alpha = \
                                    _class._shadow_presets[quality]


class RectKey(Key, RectKeyCommon, DwellProgress):

    _image_pixbuf = None
    _requested_image_size = None
    _shadow_surface = None

    can_draw_cached = True

    def __init__(self, id="", border_rect=None):
        Key.__init__(self)
        RectKeyCommon.__init__(self, id, border_rect)

        self._key_surfaces = {}

    def is_key(self):
        """ Is this a key item? """
        return True

    def invalidate_caches(self):
        """
        Clear buffered patterns, e.g. after resizing, change of settings...
        """
        self.invalidate_key()
        self.invalidate_shadow()

    def invalidate_key(self):
        self._key_surfaces = {}
        # Also drop resolved per-state theme colors (see KeyCommon.get_color).
        # These are cached separately from the rendered surfaces above and
        # were previously left stale after a live theme/color-scheme edit,
        # so states that hadn't been drawn yet since startup (e.g. Pressed,
        # Active, Locked, Scanned) kept showing old colors until restart.
        self.colors = {}

    def invalidate_image(self):
        """
        Images only have to be expicitely cleared when the
        window_scaling_factor changes.
        """
        self._image_pixbuf = {}
        self._requested_image_size = {}

    def invalidate_shadow(self):
        self._shadow_surface = None

    def set_border_rect(self, rect):
        """
        The expand-corrections button moves around a lot.
        Be sure to keep its image surfaces updated.
        """
        if rect != self.get_border_rect():
            super(RectKey, self).set_border_rect(rect)
            self.invalidate_caches()

    def draw_cached(self, cr):
        key = (self.label, self.font_size >> 8)
        entry = self._key_surfaces.get(key)
        if entry is None:
            if self.font_size:
                entry = self._create_key_surface(cr)
                self._key_surfaces[key] = entry

        if entry:
            surface, rect = entry
            cr.set_source_surface(surface, rect.x, rect.y)
            cr.paint()

    def _create_key_surface(self, base_context):
        rect = self.get_canvas_rect()
        clip_rect = rect.inflate(*self.get_extra_render_size()).int()

        # create caching surface
        target = base_context.get_target()
        surface = target.create_similar(cairo.CONTENT_COLOR_ALPHA,
                                        clip_rect.w, clip_rect.h)
        cr = cairo.Context(surface)

        cr.save()
        cr.translate(-clip_rect.x, -clip_rect.y)
        self.draw(cr)
        cr.restore()

        Gdk.flush()  # else artefacts in labels and images on Nexus 7, Raring

        return surface, clip_rect

    def draw_item(self, context):
        if context.draw_cached and self.can_draw_cached:
            self.draw_cached(context.cr)
        else:
            self.draw(context.cr, context.lod)

    def draw(self, cr, lod=LOD.FULL):
        self.draw_geometry(cr, lod)
        self.draw_image(cr, lod)
        self.draw_label(cr, lod)

    def draw_geometry(self, cr, lod):
        if not self.show_face and not self.show_border:
            return

        if lod == LOD.FULL and self.show_border:
            scale = self.get_stroke_width()
            if scale:
                root = self.get_layout_root()
                t    = root.context.scale_log_to_canvas((1.0, 1.0))
                line_width = (t[0] + t[1]) / 2.4
                line_width = min(line_width, 3.0) * scale
                line_width = max(line_width, 1.0)
            else:
                line_width = 0
        else:
            line_width = 0

        fill = self.get_fill_color()

        key_style = self.get_style()
        if key_style == "flat":
            self.draw_flat_key(cr, fill, line_width)

        elif key_style == "gradient":
            self.draw_gradient_key(cr, fill, line_width, lod)

        elif key_style == "dish":
            self.draw_dish_key(cr, fill, line_width, lod)

    def draw_flat_key(self, cr, fill, line_width):
        self._build_canvas_path(cr)

        if self.show_face:
            cr.set_source_rgba(*fill)
            if line_width:
                cr.fill_preserve()
            else:
                cr.fill()

        if line_width:
            cr.set_source_rgba(*self.get_stroke_color())
            cr.set_line_width(line_width)
            cr.stroke()

    def draw_gradient_key(self, cr, fill, line_width, lod):
        # simple gradients for fill and stroke
        fill_gradient   = config.theme_settings.key_fill_gradient / 100.0
        stroke_gradient = self.get_stroke_gradient()
        alpha = self.get_gradient_angle()

        rect = self.get_canvas_rect()
        self._build_canvas_path(cr, rect)
        gline = gradient_line(rect, alpha)

        # fill
        if self.show_face:
            if fill_gradient and lod:
                pat = cairo.LinearGradient (*gline)
                rgba = brighten(+fill_gradient*.5, *fill)
                pat.add_color_stop_rgba(0, *rgba)
                rgba = brighten(-fill_gradient*.5, *fill)
                pat.add_color_stop_rgba(1, *rgba)
                cr.set_source (pat)
            else: # take gradient from color scheme (not implemented)
                cr.set_source_rgba(*fill)

            if self.show_border:
                cr.fill_preserve()
            else:
                cr.fill()

        # stroke
        if self.show_border:
            if stroke_gradient:
                if lod:
                    stroke = fill
                    pat = cairo.LinearGradient(*gline)

                    if self.pressed:
                        stops = [-stroke_gradient * 0.5, +stroke_gradient * 0.5]
                    else:
                        stops = [+stroke_gradient * 0.5, -stroke_gradient * 0.5]

                    for i, stop in enumerate(stops):
                        rgba = brighten(stop, *stroke)
                        pat.add_color_stop_rgba(i, *rgba)

                    cr.set_source(pat)
                else:
                    cr.set_source_rgba(*fill)
            else:
                cr.set_source_rgba(*self.get_stroke_color())

            cr.set_line_width(line_width)
        cr.stroke()

    def draw_dish_key(self, cr, fill, line_width, lod):
        canvas_rect = self.get_canvas_rect()
        if self.geometry:
            geometry = self.geometry
        else:
            geometry = KeyGeometry.from_rect(self.get_border_rect())
        size_scale_x, size_scale_y = geometry.scale_log_to_size((1.0, 1.0))

        # compensate for smaller size due to missing stroke
        canvas_rect = canvas_rect.inflate(1.0)

        # parameters for the base path
        base_rgba = brighten(-0.200, *fill)
        stroke_gradient = self.get_stroke_gradient()
        light_dir = self.get_light_direction() - pi * 0.5  # 0 = light from top
        lightx = cos(light_dir)
        lighty = sin(light_dir)

        key_offset_x, key_offset_y, key_size_x, key_size_y = \
                                            self.get_key_offset_size(geometry)
        radius_pct = max(config.theme_settings.roundrect_radius, 2)
        radius_pct = max(radius_pct, 2) # too much +-1 fudging for square corners
        chamfer_size = self.get_chamfer_size()
        chamfer_size = (self.context.scale_log_to_canvas_x(chamfer_size) +
                        self.context.scale_log_to_canvas_y(chamfer_size)) * 0.5

        # parameters for the top path, key face
        stroke_width  = self.get_stroke_width()
        key_offset_top_y = key_offset_y - \
            config.DISH_KEY_Y_OFFSET * stroke_width
        border = config.DISH_KEY_BORDER
        scale_top_x = 1.0 - (border[0] * stroke_width * size_scale_x * 2.0)
        scale_top_y = 1.0 - (border[1] * stroke_width * size_scale_y * 2.0)
        key_size_top_x = key_size_x * scale_top_x
        key_size_top_y = key_size_y * scale_top_y
        chamfer_size_top = chamfer_size * (scale_top_x + scale_top_y) * 0.5

        # realize all paths we're going to use
        polygons, polygon_paths = \
            self.get_canvas_polygons(geometry,
                                   key_offset_x, key_offset_y,
                                   key_size_x, key_size_y,
                                   radius_pct, chamfer_size)
        polygons_top, polygon_paths_top = \
            self.get_canvas_polygons(geometry,
                                   key_offset_x, key_offset_top_y,
                                   key_size_top_x - size_scale_x,
                                   key_size_top_y - size_scale_y,
                                   radius_pct, chamfer_size_top)
        polygons_top1, polygon_paths_top1 = \
            self.get_canvas_polygons(geometry,
                                   key_offset_x, key_offset_top_y,
                                   key_size_top_x, key_size_top_y,
                                   radius_pct, chamfer_size_top)

        # draw key border
        if self.show_border:
            if not lod:
                cr.set_source_rgba(*base_rgba)
                for path in polygon_paths:
                    rounded_polygon_path_to_cairo_path(cr, path)
                    cr.fill()
            else:
                for ipg, polygon in enumerate(polygons):
                    polygon_top = polygons_top[ipg]
                    path = polygon_paths[ipg]
                    path_top = polygon_paths_top[ipg]

                    self._draw_dish_key_border(cr, path, path_top,
                                               polygon, polygon_top,
                                               base_rgba, stroke_gradient,
                                               lightx, lighty)

        # Draw the key face, the smaller top rectangle.
        if self.show_face:
            if not lod:
                cr.set_source_rgba(*fill)
            else:
                # Simulate the concave key dish with a gradient that has
                # a sligthly brighter middle section.
                if self.id == "SPCE":
                    angle = pi / 2.0  # space has a convex top
                else:
                    angle = 0.0       # all others are concave
                fill_gradient   = config.theme_settings.key_fill_gradient / 100.0
                dark_rgba = brighten(-fill_gradient*.5, *fill)
                bright_rgba = brighten(+fill_gradient*.5, *fill)
                gline = gradient_line(canvas_rect, angle)

                pat = cairo.LinearGradient (*gline)
                pat.add_color_stop_rgba(0.0, *dark_rgba)
                pat.add_color_stop_rgba(0.5, *bright_rgba)
                pat.add_color_stop_rgba(1.0, *dark_rgba)
                cr.set_source (pat)

            for path in polygon_paths_top1:
                rounded_polygon_path_to_cairo_path(cr, path)
                cr.fill()

    def _draw_dish_key_border(self, cr, path, path_top,
                              polygon, polygon_top,
                              base_rgba, stroke_gradient, lightx, lighty):
        n = len(polygon)
        m = len(path)

        # Lambert lighting
        edge_colors = []
        for i in range(0, n, 2):
            x0 = polygon[i]
            y0 = polygon[i+1]
            if i < n-2:
                x1 = polygon[i+2]
                y1 = polygon[i+3]
            else:
                x1 = polygon[0]
                y1 = polygon[1]

            nx = y1 - y0
            ny = -(x1 - x0)
            ln = sqrt(nx*nx + ny*ny)
            I = (nx * lightx + ny * lighty) / ln \
                * stroke_gradient * 0.8 \
                if ln else 0.0
            edge_colors.append(brighten(I, *base_rgba))

        # draw border sections
        edge = 0
        for i in range(0, m-2, 2):
            # get path points
            i1 = i + 1
            i2 = i + 2
            if i2 >= m:
                i2 -= m
            i3 = i + 3
            if i3 >= m:
                i3 = 1

            p0 = path[i]
            p0x = p0[0]
            p0y = p0[1]

            p1 = path[i1]
            p1x = p1[0]
            p1y = p1[1]

            p2 = path[i2]
            p2x = p2[0]
            p2y = p2[1]

            p3 = path[i3]
            p3x = p3[0]
            p3y = p3[1]

            p = path_top[i]
            ptop0x = p[0]
            ptop0y = p[1]

            p = path_top[i1]
            ptop1x = p[0]
            ptop1y = p[1]

            p = path_top[i2]
            ptop2x = p[0]
            ptop2y = p[1]

            # get polygon points, only to
            # fill in gaps at concave corners.
            j0 = edge*2
            j1 = j0 + 2
            if j1 >= n:
                j1 -= n
            j2 = j0 + 4
            if j2 >= n:
                j2 -= n

            ptopax = polygon_top[j0]
            ptopay = polygon_top[j0 + 1]
            ptopbx = polygon_top[j1]
            ptopby = polygon_top[j1 + 1]
            ptopcx = polygon_top[j2]
            ptopcy = polygon_top[j2 + 1]
            vax = ptopbx - ptopax
            vay = ptopby - ptopay
            nbx = ptopcy - ptopby
            nby = -(ptopcx - ptopbx)
            concave = vax*nbx + vay*nby < 0.0

            # Fake Gouraud shading: draw a gradient between mid points
            # of the lines connecting the base with the top path.
            pat = cairo.LinearGradient((p1x + ptop1x) * 0.5,
                                        (p1y + ptop1y) * 0.5,
                                        (p2x + ptop2x) * 0.5,
                                        (p2y + ptop2y) * 0.5)
            edge1 = (edge + 1) % len(edge_colors)
            pat.add_color_stop_rgba(0.0, *edge_colors[edge])
            pat.add_color_stop_rgba(1.0, *edge_colors[edge1])
            cr.set_source (pat)

            # Draw corners and edges with enough overlap to avoid
            # artefacts at touching line boundaries.
            cr.move_to(p0x, p0y)
            cr.line_to(p1x, p1y)
            cr.curve_to(p2[2], p2[3], p2[4], p2[5], p2[0], p2[1])
            cr.line_to(p3x, p3y)
            cr.line_to(ptop2x, ptop2y)
            if concave:
                cr.line_to(ptopbx, ptopby)
            cr.line_to(ptop1x, ptop1y)
            cr.line_to(ptop0x, ptop0y)
            cr.close_path()
            cr.fill()

            edge += 1

    def get_label_runs(self):
        runs = []
        log_rect = self.get_label_rect()
        canvas_rect = self.context.log_to_canvas_rect(log_rect)

        # secondary label
        label = self.get_secondary_label()
        if label and \
           len(label) == 1 and \
           config.keyboard.show_secondary_labels:
            font_size = self.font_size * 0.5
            layout = self.get_pango_layout(label, font_size, 1)
            src_size = layout.get_size()
            src_size = (src_size[0] * PangoUnscale, src_size[1] * PangoUnscale)
            xalign, yalign = self.align_secondary_label(src_size,
                                                (canvas_rect.w, canvas_rect.h))
            x = int(canvas_rect.x + xalign)
            y = int(canvas_rect.y + yalign)
            rgba = self.get_secondary_label_color()

            runs.append((layout, x, y, rgba))

        # popup indicator
        if not self.popup_id is None and \
           not config.xid_mode:

            label = self._get_popup_indicator()
            font_size = self.font_size
            layout = self.get_pango_layout(label, font_size, 2)

            src_size = layout.get_size()
            src_size = (src_size[0] * PangoUnscale, src_size[1] * PangoUnscale)
            xalign, yalign = self.align_popup_indicator(src_size,
                                                 (canvas_rect.w, canvas_rect.h))
            x = int(canvas_rect.x + xalign)
            y = int(canvas_rect.y + yalign)
            rgba = self.get_secondary_label_color()

            runs.append((layout, x, y, rgba))

        # main label
        label = self.get_label()
        if label:
            font_size = self.font_size
            layout = self.get_pango_layout(label, font_size, 0)
            src_size = layout.get_size()
            src_size = (src_size[0] * PangoUnscale, src_size[1] * PangoUnscale)
            xalign, yalign = self.align_label(src_size,
                                                (canvas_rect.w, canvas_rect.h))
            x = int(canvas_rect.x + xalign)
            y = int(canvas_rect.y + yalign)
            rgba = self.get_label_color()

            runs.append((layout, x, y, rgba))

        return runs

    def _get_popup_indicator(self):
        """
        Find the shortest ellipsis possible with the current font.
        The font is assumed to never change during the livetime of the key.
        """
        result = self._popup_indicator
        if not result:
            labels = ("…", "...")  # label candidates

            BASE_FONTDESCRIPTION_SIZE = 10000000
            wmin = None
            result = ""
            for label in labels:
                layout = self.get_pango_layout(label, BASE_FONTDESCRIPTION_SIZE, 2)
                w = layout.get_size()[0]
                if wmin is None or w < wmin:
                    wmin = w
                    result = label
            self._popup_indicator = result

        return result

    def draw_label(self, context, lod):
        # Skip cairo errors when drawing labels with font size 0
        # This may happen for hidden keys and keys with bad size groups.
        if self.font_size == 0 or not self.show_label:
            return

        runs = self.get_label_runs()
        if not runs:
            return

        fill = self.get_fill_color()

        for dx, dy, lum, last in self._label_iterations(lod):
            # draw dwell progress after fake emboss, before final image
            if last and self.is_dwelling():
                DwellProgress.draw(self, context,
                                   self.get_dwell_progress_canvas_rect(),
                                   self.get_dwell_progress_color())
            for layout, x, y, rgba in runs:
                if lum:
                    rgba = brighten(lum, *fill) # darker
                context.move_to(x + dx, y + dy)
                context.set_source_rgba(*rgba)
                PangoCairo.show_layout(context, layout)

    def draw_image(self, context, lod):
        """
        Draws the key's optional image.
        Fixme: merge with draw_label, can't do this for 0.99 because
        the Gdk.flush() workaround on the nexus 7 might fail.
        """
        if not self.image_filenames or not self.show_image:
            return

        log_rect = self.get_label_rect()
        rect = self.context.log_to_canvas_rect(log_rect)
        if rect.w < 1 or rect.h < 1:
            return

        pixbuf = self.get_image(rect.w, rect.h)
        if not pixbuf:
            return

        src_size = (pixbuf.get_width(), pixbuf.get_height())
        xalign, yalign = self.align_label(src_size, (rect.w, rect.h))

        image_rgba = self.get_image_color()
        fill = self.get_fill_color()

        for dx, dy, lum, last in self._label_iterations(lod):
            # draw dwell progress after fake emboss, before final image
            if last and self.is_dwelling():
                DwellProgress.draw(self, context,
                                   self.get_dwell_progress_canvas_rect(),
                                   self.get_dwell_progress_color())

            r = rect.offset(xalign + dx, yalign + dy)

            if image_rgba is None:
                pixbuf.draw(context, r, None, self.image_style)
            else:
                if lum:
                    rgba = brighten(lum, *fill)  # darker
                else:
                    rgba = image_rgba

                pixbuf.draw(context, r, rgba, self.image_style)

    def draw_shadow_cached(self, context):
        entry = self._shadow_surface
        if entry is None:
            if config.theme_settings.key_shadow_strength:
                entry = self.create_shadow_surface(context,
                                              self._shadow_steps,
                                              self._shadow_alpha)
                self._shadow_surface = entry

        if entry:
            surface, rect = entry
            context.set_source_rgba(0.0, 0.0, 0.0, 1.0)
            context.mask_surface(surface, rect.x, rect.y)

    def create_shadow_surface(self, base_context, shadow_steps, shadow_alpha):
        """
        Draw shadow and shaded halo.
        Somewhat slow, make sure to cache the result.
        Glitchy, if the clip-rect covers only a single button (Precise),
        therefore, draw only with unrestricted clipping rect.
        """
        rect = self.get_canvas_rect()
        root = self.get_layout_root()

        if rect.is_empty():
            return None

        extent = min(root.context.scale_log_to_canvas((1.0, 1.0)))
        alpha = pi / 2 + self.get_light_direction()

        shadow_opacity = config.theme_settings.key_shadow_strength * \
                         shadow_alpha
        shadow_scale   = config.theme_settings.key_shadow_size / 20.0
        shadow_radius  = max(extent * shadow_scale, 1.0)
        shadow_displacement = max(extent * shadow_scale * 0.26, 1.0)
        shadow_offset  = (shadow_displacement * cos(alpha),
                          shadow_displacement * sin(alpha))

        has_halo = shadow_steps > 1 and not config.window.transparent_background
        halo_opacity   = shadow_opacity * 0.11
        halo_radius    = max(extent * 8.0, 1.0)

        clip_rect = rect.offset(shadow_offset[0]+1, shadow_offset[1]+1)
        if has_halo:
            clip_rect = clip_rect.inflate(halo_radius * 1.5)
        else:
            clip_rect = clip_rect.inflate(shadow_radius * 1.3)
        clip_rect = clip_rect.int()

        # create caching surface
        target = base_context.get_target()
        surface = target.create_similar(cairo.CONTENT_ALPHA,
                                        clip_rect.w, clip_rect.h)
        context = cairo.Context(surface)

        # paint the surface
        context.save()
        context.translate(-clip_rect.x, -clip_rect.y)

        context.rectangle(*clip_rect)
        context.clip()

        context.push_group_with_content(cairo.CONTENT_ALPHA)
        self._build_canvas_path(context, rect)
        context.set_source_rgba(0.0, 0.0, 0.0, 1.0)
        context.fill()
        shape = context.pop_group()

        # shadow
        drop_shadow(context, shape, rect,
                    shadow_radius, shadow_offset, shadow_opacity, shadow_steps)
        # halo
        if has_halo:
            drop_shadow(context, shape, rect,
                        halo_radius, shadow_offset, halo_opacity, shadow_steps)

        # cut out the key area, the key may be transparent
        context.set_operator(cairo.OPERATOR_CLEAR)
        context.set_source_rgba(0.0, 0.0, 0.0, 1.0)
        self._build_canvas_path(context, rect)
        context.fill()

        context.restore()

        return surface, clip_rect

    def _build_canvas_path(self, cr, rect = None, path = None):
        """ Build cairo path of the key geometry. """
        if self.geometry:
            if not path:
                path = self.get_canvas_path()
            self._build_complex_path(cr, path)
        else:
            if not rect:
                rect = self.get_canvas_rect()
            self.build_rect_path(cr, rect)

    def _build_complex_path(self, cr, path):
        roundness = config.theme_settings.roundrect_radius
        chamfer_size = self.get_chamfer_size()
        chamfer_size = self.context.scale_log_to_canvas_y(chamfer_size)
        rounded_path(cr, path, roundness, chamfer_size)

    def build_rect_path(self, context, rect):
        roundness = config.theme_settings.roundrect_radius
        if roundness:
            roundrect_curve(context, rect, roundness)
        else:
            context.rectangle(*rect)

    def build_rect_path_custom(self, context, rect, corner_mask):
        roundness = config.theme_settings.roundrect_radius
        if roundness:
            roundrect_curve_custom(context, rect, roundness, corner_mask)
        else:
            context.rectangle(*rect)

    def get_gradient_angle(self):
        return -pi/2.0 + self.get_light_direction()

    def get_best_font_size(self, mod_mask):
        """
        Get the maximum font size that would not cause the label to
        overflow the boundaries of the key.
        """
        # Base this on the unpressed rect, so fake physical key action
        # doesn't influence the font_size and doesn't cause surface cache
        # misses for that minor wiggle.
        rect = self.get_label_rect(self.get_unpressed_rect())
        label_width, label_height = \
                      self.get_label_base_extents(mod_mask)

        size_for_maximum_width  = self.context.scale_log_to_canvas_x(
                                      (rect.w - self.label_margin[0]*2)) \
                                  / label_width

        size_for_maximum_height = self.context.scale_log_to_canvas_y(
                                     (rect.h - self.label_margin[1]*2)) \
                                  / label_height

        if size_for_maximum_width < size_for_maximum_height:
            return int(size_for_maximum_width)
        else:
            return int(size_for_maximum_height)

    def get_label_base_extents(self, mod_mask):
        """
        Update resolution independent extents of the label layout.
        """
        extents = self._label_extents.get(mod_mask)
        if not extents:
            extents = self.calc_label_base_extents(self.get_label())
            self._label_extents[mod_mask] = extents

        return extents

    def calc_label_base_extents(self, label):
        """ Calculate font-size independent extents. """
        cr = Gdk.pango_context_get()
        layout = Pango.Layout(cr)
        BASE_FONTDESCRIPTION_SIZE = 10000000
        self.prepare_pango_layout(layout, label, BASE_FONTDESCRIPTION_SIZE)
        w, h = layout.get_size()   # In Pango units
        w = w or 1.0
        h = h or 1.0
        return w / (Pango.SCALE * BASE_FONTDESCRIPTION_SIZE), \
               h / (Pango.SCALE * BASE_FONTDESCRIPTION_SIZE)

    def invalidate_label_extents(self):
        """
        Cached label extents are resolution independent. Calling this
        is only necessary when the system font dpi change.
        """
        self._label_extents = {}

    def get_image(self, width, height):
        """
        Get the cached image pixbuf object, load image
        and create it if necessary.
        Width and height in canvas coordinates.
        """
        if not self.image_filenames:
            return None

        if self.active and ImageSlot.ACTIVE in self.image_filenames:
            slot = ImageSlot.ACTIVE
        else:
            slot = ImageSlot.NORMAL
        image_filename = self.image_filenames.get(slot)
        if not image_filename:
            return

        if not self._image_pixbuf:
            self._image_pixbuf = {}
            self._requested_image_size = {}

        pixbuf = self._image_pixbuf.get(slot)
        size = self._requested_image_size.get(slot)

        if not pixbuf or \
           size[0] != int(width) or size[1] != int(height):
            pixbuf = None
            filename = config.get_image_filename(image_filename)
            if filename:
                _logger.debug("loading image '{}'".format(filename))

                try:
                    pixbuf = PixBufScaled. \
                        from_file_and_size(filename, width, height)
                except Exception as ex: # private exception gi._glib.GError when
                                        # librsvg2-common wasn't installed
                    _logger.error("get_image(): " + unicode_str(ex))

                if pixbuf:
                    self._requested_image_size[slot] = (int(width), int(height))

            self._image_pixbuf[slot] = pixbuf

        return pixbuf

    def _label_iterations(self, lod):
        stroke_gradient = self.get_stroke_gradient()
        if lod == LOD.FULL and \
           self.get_style() != "flat" and stroke_gradient:
            root = self.get_layout_root()
            d = 0.4  # fake-emboss distance
            #d = max(src_size[1] * 0.02, 0.0)
            max_offset = 2

            alpha = self.get_gradient_angle()
            xo = root.context.scale_log_to_canvas_x(d * cos(alpha))
            yo = root.context.scale_log_to_canvas_y(d * sin(alpha))
            xo = min(int(round(xo)), max_offset)
            yo = min(int(round(yo)), max_offset)

            luminosity_factor = stroke_gradient * 0.25

            # shadow
            yield xo, yo, -luminosity_factor, False

            # highlight
            yield -xo, -yo, luminosity_factor, False

        # normal
        yield 0, 0, 0, True


class FixedFontMixin:
    """ Font size independent of text length """

    def get_best_font_size(self, mod_mask):
        """
        Get the maximum font size that would not cause the label to
        overflow the height of the key.
        """
        return self.calc_font_size(self.context,
                                   self.get_fullsize_rect().get_size(),
                                   True)

    def calc_font_size(self, context, size, use_width = False):
        """ Calculate font size based on the height of the key """
        # Base this on the unpressed rect, so fake physical key action
        # doesn't influence the font_size and doesn't cause surface cache
        # misses for that minor wiggle.
        label_width, label_height = self.get_label_base_extents(0)

        size_for_maximum_width  = context.scale_log_to_canvas_x(
                                     (size[0] - self.label_margin[0]*2)) \
                                  / label_width

        size_for_maximum_height = context.scale_log_to_canvas_y(
                                     (size[1] - self.label_margin[1]*2)) \
                                 / label_height

        font_size = size_for_maximum_height
        if use_width and size_for_maximum_width < font_size:
            font_size = size_for_maximum_width

        return int(font_size * 0.9)

    def get_label_base_extents(self, mod_mask):
        """
        Update resolution independent extents of the label layout.
        """
        extents = self._label_extents.get(mod_mask)
        if not extents:
            extents = self.calc_label_base_extents("Mg")
            self._label_extents[mod_mask] = extents

        return extents


class WordlistKey(RectKey):

    def get_style(self):
        style = super(WordlistKey, self).get_style()
        if style == "dish":
            style = "gradient"
        return style

    def get_stroke_width(self):
        # Turn down stroke width -> Only subtly bevel the wordlist bar.
        value = super(WordlistKey, self).get_stroke_width()
        return min(value, 0.6)

    def get_stroke_gradient(self):
        # Turn down stroke gradient -> Only subtly bevel the wordlist bar.
        value = super(WordlistKey, self).get_stroke_gradient()
        return min(value, 0.3)

    def get_light_direction(self):
        return -0.3 * pi / 180

    def draw_shadow_cached(self, context):
        # no shadow
        pass


class FullSizeKey(WordlistKey):
    def __init__(self, id = "", border_rect = None):
        super(FullSizeKey, self).__init__(id, border_rect)

    def get_rect(self):
        """ Get bounding box in logical coordinates """
        # Disable key_size, let wordlist creation have complete size control.
        return self.get_fullsize_rect()


class FlatKey(FullSizeKey):
    def __init__(self, id="", border_rect=None):
        super(FlatKey, self).__init__(id, border_rect)

    def draw(self, context, lod=LOD.FULL):
        # draw only when pressed, to blend in with the word list bar
        if self.pressed or self.active or self.scanned:
            self.draw_geometry(context, lod)
        self.draw_image(context, lod)
        self.draw_label(context, lod)

    def get_stroke_width(self):
        # Turn down stroke width -> no annoying banding at
        # what should be flat key edges.
        return 0.0


class BarKey(FlatKey):
    def __init__(self, id="", border_rect=None):
        super(BarKey, self).__init__(id, border_rect)

    def can_show_label_popup(self):
        return False


class WordKey(FixedFontMixin, BarKey):
    def __init__(self, id="", border_rect=None):
        super(WordKey, self).__init__(id, border_rect)


class InputlineKey(FixedFontMixin, RectKey, InputlineKeyCommon):

    cursor = 0

    def __init__(self, id="", border_rect = None):
        RectKey.__init__(self, id, border_rect)
        self.word_infos = []
        self._xscroll = 0.0

    def set_content(self, line, word_infos, cursor):
        self.line = line
        self.word_infos = word_infos
        self.cursor = cursor
        self.invalidate_key()

        # determine text direction
        dir = Pango.find_base_dir(line, -1)
        self.ltr = dir != Pango.Direction.RTL

    def draw_label(self, context, lod):
        layout, rect, cursor_rect, layout_pos = self._calc_layout_params()
        cursor_width = cursor_rect.h * 0.075
        cursor_width = max(cursor_width, 1.0)
        label_rgba = self.get_label_color()

        context.save()
        context.rectangle(*rect)
        context.clip()

        # draw text
        context.set_source_rgba(*label_rgba)
        context.move_to(*layout_pos)
        PangoCairo.show_layout(context, layout)

        context.restore()  # don't clip the caret

        # draw caret
        context.move_to(cursor_rect.x, cursor_rect.y)
        context.rel_line_to(0, cursor_rect.h)
        context.set_source_rgba(*label_rgba)
        context.set_line_width(cursor_width)
        context.stroke()

        # reset attributes; layout is reused by all keys due to memory leak
        layout.set_attributes(Pango.AttrList())

    def get_layout(self):
        text, attrs = self._build_layout_contents()
        layout = self.get_pango_layout(text, self.font_size)
        layout.set_attributes(attrs)
        layout.set_auto_dir(True)
        return layout

    def get_canvas_label_rect(self):
        rect = super(InputlineKey, self).get_canvas_label_rect()
        return rect.int()       # else clipping glitches

    def _build_layout_contents(self):
        # Add one char to avoid having to handle RTL corner cases at line end.
        text =  self.line + " "
        attrs = None

        # prepare colors
        color_ignored       = '#00FFFF'
        color_partial_match = '#00AA00'
        color_no_match      = '#00FF00'
        color_error         = '#FF0000'

        # set text colors, highlight unknown words
        #   AttrForeground/pango_attr_foreground_new are still inaccassible
        #   -> use parse_markup instead.
        # https://bugzilla.gnome.org/show_bug.cgi?id=646788
        markup = ""
        wis = self.word_infos
        for i, wi in enumerate(wis):
            cursor_at_word_end = self.cursor == wi.end

            # select colors
            predict_color = None
            spell_color = None
            if 0:  # no more bold, keep it simple
                if wi.ignored:
                    #color = color_ignored
                    pass
                elif not wi.exact_match:
                    if wi.partial_match and cursor_at_word_end:
                        predict_color = color_partial_match
                    else:
                        predict_color = color_no_match

            if wi.spelling_errors:
                spell_color = color_error

            # highlight the word as needed
            word = text[wi.start : wi.end]
            word = GLib.markup_escape_text(word)
            if predict_color or spell_color:
                span = ""
                if predict_color:
                    span += "<b>"
                if spell_color:
                    span += "<span underline_color='" + spell_color + "' " + \
                                 "underline='error'>"
                span += word

                if spell_color:
                    span += "</span>"
                if predict_color:
                    span += "</b>"

                t = span
            else:
                span = word

            # assemble the escaped pieces
            if i == 0:
                # add text up to the first word
                intro = text[:wi.start]
                markup += GLib.markup_escape_text(intro)
            else:
                # add gap between words
                wiprev = wis[i-1]
                gap = text[wiprev.end : wi.start]
                markup += GLib.markup_escape_text(gap)

            # add the word
            markup += span

            if i == len(wis) - 1:
                # add remaining text after the last word
                remainder = text[wi.end:]
                markup += GLib.markup_escape_text(remainder)

        result = Pango.parse_markup(markup, -1, "\0")
        if len(result) == 4:
            ok, attrs, text, error = result

        return text, attrs

    def _calc_layout_params(self):
        layout = self.get_layout()

        # get label rect and aligned drawing origin
        rect = self.get_canvas_label_rect()
        text_size = layout.get_pixel_size()
        xalign, yalign = self.align_label(text_size, (rect.w, rect.h), self.ltr)

        # get cursor position
        cursor_index = self.cursor_to_layout_index(layout, self.cursor, self.ltr)
        strong_pos, weak_pos = layout.get_cursor_pos(cursor_index)
        pos = strong_pos
        cursor_rect = Rect(pos.x, pos.y, pos.width, pos.height).scale(1.0 / Pango.SCALE)

        # scroll to cursor
        self._update_scroll_position(rect, text_size, cursor_rect, xalign)

        xlayout = rect.x + xalign + self._xscroll
        ylayout = rect.y + yalign
        cursor_rect.x += xlayout
        cursor_rect.y += ylayout

        return layout, rect, cursor_rect, (xlayout, ylayout)

    def _update_scroll_position(self, label_rect, text_size,
                                cursor_rect, xalign):
        xscroll = self._xscroll

        # scroll line into view
        gap_begin = xalign + xscroll
        gap_end   = label_rect.w - (xalign + xscroll + text_size[0])
        if gap_begin > 0 or gap_end > 0:
            xscroll = 0

        # scroll cursor into view
        over_begin = -(xalign + cursor_rect.x)
        over_end   =   xalign + cursor_rect.x - label_rect.w
        if over_begin - xscroll > 0.0:
            xscroll = over_begin
        if over_end + xscroll > 0.0:
            xscroll = -over_end

        self._xscroll = xscroll

    @staticmethod
    def cursor_to_layout_index(layout, cursor, ltr = False):
        """ Translate unicode character position to pango byte index. """
        indexes = []
        i = 0
        iter = layout.get_iter()
        while True:
            indexes.append(iter.get_index())
            if not iter.next_char():
                break

        if ltr:
            if len(indexes) == 0:
                cursor_index = 0
            elif cursor < 0:
                cursor_index = 0
            elif cursor >= len(indexes):
                cursor_index = indexes[-1]
            else:
                cursor_index = indexes[cursor]
        else:
            if len(indexes) == 0:
                cursor_index = 0
            elif cursor < 0:
                cursor_index = indexes[-1]
            elif cursor >= len(indexes):
                cursor_index = 0
            else:
                cursor_index = indexes[-(cursor+1)]

        return cursor_index

zf = None
count = 0

class PixBufScaled:
    """
    Workaround for blurry images when window_scaling_factor >1
    """
    _pixbuf = None
    _width = 0
    _height = 0
    _real_width = 0
    _real_height = 0

    @staticmethod
    def from_file_and_size(filename, width, height):
        pixbuf = PixBufScaled()
        pixbuf._load(filename, width, height)
        return pixbuf

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height

    def _load(self, filename, width, height):
        scale = config.window_scaling_factor
        load_width = width * scale
        load_height = height * scale

        self._pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
            filename, load_width, load_height)

        self._real_width = self._pixbuf.get_width()
        self._real_height = self._pixbuf.get_height()
        self._width = self._real_width / scale
        self._height = self._real_height / scale

    def draw(self, context, rect, rgba, image_style):
        """
        Draw the image in the theme's label color.
        Only the alpha channel of the image is used.
        """
        context.save()

        context.translate(rect.x, rect.y)
        scale = config.window_scaling_factor
        if scale and scale != 1.0:
            context.scale(1.0 / scale, 1.0 / scale)

        # colored?
        if image_style == ImageStyle.MULTI_COLOR:
            Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
            context.paint()

        # grayscale?
        elif image_style == ImageStyle.DESATURATED:
            # gdk_pixbuf_saturate_and_pixelate ()
            if 0:
                # must have non-black background
                Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
                pattern = context.get_source()
                k = 1 / 256.0
                context.set_source_rgb(k, k, k)
                context.mask(pattern)

                # cairo.OPERATOR_HSL_LUMINOSITY doesn't exist in python bindings
                # CAIRO_OPERATOR_HSL_LUMINOSITY = 28
                context.set_operator(28)
                Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
                context.paint()
            elif 0:
                Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
                context.paint()

                import colorsys
                rgb = colorsys.hls_to_rgb(0, 0.5, 0.2)
                Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
                pattern = context.get_source()

                context.set_source_rgb(*rgb)
                context.set_operator(26)
                context.paint()
            else:

                Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
                pattern = context.get_source()

                context.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)

                Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
                context.paint()

                import colorsys
                saturation = 0.3
                rgb = colorsys.hls_to_rgb(0, 0.5, saturation)

                context.set_source_rgb(*rgb)
                context.set_operator(26)
                context.paint()

                context.pop_group_to_source()
                context.mask(pattern)

        # single color
        else:
            Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
            pattern = context.get_source()
            context.set_source_rgba(*rgba)
            context.mask(pattern)

        context.restore()


