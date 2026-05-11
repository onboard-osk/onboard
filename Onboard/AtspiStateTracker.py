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

""" GTK specific keyboard class """

from __future__ import division, print_function, unicode_literals

import os
import time
import logging
_logger = logging.getLogger(__name__)

from Onboard.Version   import require_gi_versions
require_gi_versions()
try:
    from gi.repository import Atspi
except ImportError as e:
    _logger.warning("Atspi typelib missing, auto-show unavailable")

from Onboard.utils     import Rect, EventSource, Process, unicode_str
from Onboard.Timer     import Timer

# Config Singleton
from Onboard.Config import Config
config = Config()


class CachedAccessible:
    def __init__(self, accessible):
        self._accessible = accessible
        self._state = {}       # cache of various accessible properties

    # Use "==" for object identity tests instead of "is".
    def __eq__(self, other):
        return other is not None and self._accessible is other._accessible

    def __ne__(self, other):
        return other is None or self._accessible is not other._accessible

    def get_state(self):
        """ All cached state of the accessible """
        return self._state

    def get_all_state(self):
        """
        Return _state filled with all kinds of properties, for easy printint
        as debug output in TextContext.
        """
        self.get_role()
        self.get_role_name()
        self.get_name()
        self.get_state_set()
        self.get_id()
        self.get_attributes()
        self.get_interfaces()
        self.get_description()
        self.get_pid()
        self.get_process_name()
        self.get_toolkit_name()
        self.get_toolkit_version()
        self.get_editable_text_iface()
        self.get_app_name()
        self.get_app_description()
        self.get_app_toolkit_name()
        self.get_extents()
        self.get_frame()
        self.get_frame_extents()
        self.is_urlbar()
        self.is_byobu()
        return self._state

    # ### Cached, exception-safe accessor functions ###

    def get_role(self):
        return self._get_value("role",
                               self._accessible.get_role)

    def get_role_name(self):
        return self._get_value("role-name",
                               self._accessible.get_role_name)

    def get_name(self):
        return self._get_value("name",
                               self._accessible.get_name)

    def invalidate_state_set(self):
        self.invalidate("state-set")

    def get_state_set(self):
        return self._get_value("state-set",
                               self._accessible.get_state_set)

    def get_id(self):
        return self._get_value("id",
                               self._accessible.get_id)

    def get_attributes(self):
        return self._get_value("attributes",
                               self._accessible.get_attributes, {})

    def get_interfaces(self):
        return self._get_value("interfaces",
                               self._accessible.get_interfaces, [])

    def get_description(self):
        return self._get_value("description",
                               self._accessible.get_description)

    def get_pid(self):
        return self._get_value("pid",
                               self._accessible.get_process_id)

    def get_process_name(self):
        pid = self.get_pid()
        if pid != -1:
            return self._get_value_noex(
                "process-name",
                lambda : Process.get_process_name(pid))
        return None

    def get_toolkit_name(self):
        return self._get_value("toolkit-name",
                               self._accessible.get_toolkit_name)

    def get_toolkit_version(self):
        return self._get_value("toolkit-version",
                               self._accessible.get_toolkit_version)

    def get_editable_text_iface(self):
        try:
            func = self._accessible.get_editable_text_iface
        except AttributeError:  # function does not exist in Precise
            return None

        return self._get_value("editable-text-iface", func)

    def get_app_name(self):
        def func():
            app = self.get_application()
            return app.get_name() if app else ""

        return self._get_value("app-name", func, "")

    def get_app_description(self):
        def func():
            app = self.get_application()
            return app.get_description() if app else ""

        return self._get_value("app-description", func, "")

    def get_app_toolkit_name(self):
        """
        Toolkit name reported by the *application* accessible.

        AT-SPI canonically reports the toolkit on the application root
        rather than on each widget; for Qt apps (Kate, KWrite, Konsole,
        Dolphin, etc.) the widget's own get_toolkit_name() returns
        None while the application's returns "Qt". GTK apps mirror
        their toolkit on widget attributes; Qt apps don't, hence this
        separate accessor.
        """
        def func():
            app = self.get_application()
            try:
                return app.get_toolkit_name() if app else ""
            except Exception:
                return ""
        return self._get_value("app-toolkit-name", func, "")

    def get_application(self):
        return self._get_value("application",
                               self._accessible.get_application)

    def invalidate_extents(self):
        self.invalidate("extents")

    def get_extents(self):
        """
        Screen rect after scaling.
        """
        scale = config.window_scaling_factor
        if scale != 1.0:
            # Only Gtk-3 widgets return scaled coordinates, all others,
            # including Gtk-2 apps like firefox, clawsmail and Qt-apps,
            # apparently don't.
            if self.is_toolkit_gtk3():
                scale = 1.0
            else:
                scale = 1.0 / config.window_scaling_factor

        def func():
            ext = self._accessible.get_extents(Atspi.CoordType.SCREEN)
            return Rect(ext.x * scale, ext.y * scale,
                        ext.width * scale, ext.height * scale)

        return self._get_value("extents", func, Rect())

    def get_frame(self):
        def func():
            frame = self._get_accessible_frame(self._accessible)
            if frame:
                return CachedAccessible(frame)
            return None

        return self._get_value_noex("frame", func)

    def get_frame_extents(self):
        def func():
            frame = self.get_frame()
            if frame:
                return frame.get_extents()
            return Rect()

        return self._get_value_noex("frame_extents", func)

    @staticmethod
    def _get_accessible_frame(accessible):
        """ Accessible of the top level window to which accessible belongs. """
        frame = None
        _logger.atspi("_get_accessible_frame(): searching for top level:")
        try:
            parent = accessible
            while True:
                parent = parent.get_parent()
                if not parent:
                    break
                role = parent.get_role()
                _logger.atspi("parent: {}".format(role))
                if role == Atspi.Role.FRAME or \
                   role == Atspi.Role.DIALOG or \
                   role == Atspi.Role.WINDOW or \
                   role == Atspi.Role.NOTIFICATION:
                    frame = parent
                    break
        # private exception gi._glib.GError when
        # right clicking onboards unity2d launcher (Precise)
        except Exception as ex:
            _logger.atspi("Invalid accessible,"
                          " failed to get top level accessible: " +
                          unicode_str(ex))
        return frame

    def is_urlbar(self):
        """ Is this a (most likely firefox') URL bar? """
        def func():
            attributes = self.get_attributes()
            return bool(attributes and "urlbar" in attributes.get("class", ""))

        return self._get_value_noex("is_urlbar", func)

    def is_byobu(self):
        """ Is this possibly byobu running in a terminal? """
        def func():
            description = self.get_description()
            return bool(description and "byobu" in description.lower())

        return self._get_value_noex("is_byobu", func)

    def _get_value(self, name, func, default=None):
        """ Return cached return value of func(). """
        value = self._state.get(name)
        if value is None:
            try:
                value = func()
            except Exception as ex:  # private exception gi._glib.GError
                _logger.info("CachedAccessible._get_value({}): "
                             "invalid accessible, failed to read state: "
                             .format(name) + unicode_str(ex))
                value = default

            self._state[name] = value

        return value

    def _get_value_noex(self, name, func):
        """ Return cached return value of func(). """
        value = self._state.get(name)
        if value is None:
            value = func()
            self._state[name] = value
        return value

    def invalidate(self, name):
        """
        Force re-reading property from the accessible.
        May cause a D-Bus round-trip on the next read-attempt.
        """
        try:
            del self._state[name]
        except KeyError:
            pass

    # ### uncached, but still exception safe functions ###

    def get_selection(self, selection_num=0):
        selection = None
        try:
            sel = self._accessible.get_selection(selection_num)
            # Gtk-2 applications return 0,0 when there is no selection.
            # Gtk-3 applications return caret positions in that case.
            # LibreOffice Writer in Vivid initially returns -1,-1 when there
            # is no selection, later the caret position.
            start = sel.start_offset
            end = sel.end_offset
            if start > 0 and \
               end > 0 and \
               start <= end:
                selection = (sel.start_offset, sel.end_offset)
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.get_selection(): " +
                         unicode_str(ex))
        return selection

    def set_caret_offset(self, offset):
        try:
            self._accessible.set_caret_offset(offset)
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.set_caret_offset(): " +
                         unicode_str(ex))

    def insert_text(self, position, text):
        # AT-SPI's atspi_editable_text_insert_text() takes the number
        # of *characters* to insert, not bytes. Passing -1 works for
        # the GTK bridge (it computes strlen on its side) but Qt's
        # accessibility bridge truncates to length=0 on -1 and the
        # insert silently no-ops. Pass the actual character count.
        try:
            ok = self._accessible.insert_text(position, text, len(text))
            _logger.atspi(
                "CachedAccessible.insert_text(pos={}, text={!r}, "
                "len={}) -> {}".format(position, text, len(text), ok))
            return ok
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.warning("CachedAccessible.insert_text() failed: " +
                            unicode_str(ex))
        return False

    def delete_text(self, start_pos, end_pos):
        try:
            return self._accessible.delete_text(start_pos, end_pos)
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.delete_text(): " +
                         unicode_str(ex))
        return False

    # ### uncached, raising exceptions ###

    def get_caret_offset(self):
        try:
            offset = self._accessible.get_caret_offset()
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.get_caret_offset(): " +
                         unicode_str(ex))
            raise ex
        return offset

    def get_character_count(self):
        try:
            count = self._accessible.get_character_count()
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.get_character_count(): " +
                         unicode_str(ex))
            raise ex
        return count

    def get_text_at_offset(self, offset, boundary_type):
        try:
            text = self._accessible.get_text_at_offset(offset, boundary_type)
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.get_text_at_offset(): " +
                         unicode_str(ex))
            raise ex
        return text

    def get_text_before_offset(self, offset, boundary_type):
        try:
            text = self._accessible.get_text_before_offset(offset,
                                                           boundary_type)
        except Exception as ex:  # Private exception gi._glib.GErro
            _logger.info("CachedAccessible.get_text_before_offset(): " +
                         unicode_str(ex))
            raise ex
        return text

    def get_text(self, begin, end):
        """ Text of the given accessible, no caching """
        try:
            text = Atspi.Text.get_text(self._accessible, begin, end)
        # private exception gi._glib.GError: timeout from dbind
        # with web search in firefox.
        except Exception as ex:
            _logger.atspi("CachedAccessible.get_text(): " +
                          unicode_str(ex))
            raise ex
        return text

    # ### Higher level functions ###

    def is_focused(self, invalidate=False):
        if invalidate:  # re-read properties?
            self.invalidate_state_set()

        state_set = self.get_state_set()
        if state_set is not None:
            return state_set.contains(Atspi.StateType.FOCUSED)
        return False

    def is_editable(self):
        """ Is this an accessible onboard should be shown for? """
        state_set = self.get_state_set()
        if state_set and state_set.contains(Atspi.StateType.EDITABLE):
            return True
        role      = self.get_role()
        return role in [
                    Atspi.Role.TERMINAL,
                    ]

    def is_not_focus_stealing(self):
        """
        Is this accessible unlikely to steal the focus from
        a previously focused editable accessible?
        """
        role      = self.get_role()
        state_set = self.get_state_set()
        if state_set is not None:

            # Mainly firefox elements after the workaround
            # for firefox 50.
            if role in [Atspi.Role.DOCUMENT_FRAME,
                        Atspi.Role.LINK,
                        ] \
               and state_set is not None and \
               not state_set.contains(Atspi.StateType.EDITABLE):
                    return True
        return False

    def is_single_line(self):
        """ Is accessible a single line text entry? """
        state_set = self.get_state_set()
        return state_set and state_set.contains(Atspi.StateType.SINGLE_LINE)

    def is_toolkit_gtk3(self):
        """ Are the accessible attributes from a gtk3 widget? """
        attributes = self.get_attributes()
        return attributes and \
            "toolkit" in attributes and attributes["toolkit"] == "gtk"

    def is_toolkit_qt(self):
        """
        Are the accessible attributes from a Qt widget?

        Two sources are consulted in order:

        1) The widget-level "toolkit" attribute. GTK widgets expose
           toolkit info this way; Qt widgets on KDE Plasma typically
           don't.

        2) The application accessible's get_toolkit_name(). AT-SPI's
           canonical location for toolkit metadata is the application
           root, not individual widgets. Qt apps (Kate / KWrite /
           Konsole / Dolphin / etc.) report "Qt" here while their
           widgets' own get_toolkit_name() returns None.

        Required on Wayland: the uinput keysynth path injects raw
        keycodes that the compositor interprets through the currently-
        active xkb layout group, so cross-group keysyms (e.g. typing
        '>' or '<' or Latin snippets while a non-Latin group is
        active) come out wrong. Routing through AT-SPI direct
        insertion bypasses that entirely. Qt 5.15+ / Qt 6 on modern
        Linux implement EditableText.insert correctly.
        """
        # (1) widget-level "toolkit" attribute (where GTK reports "gtk")
        attributes = self.get_attributes()
        if attributes:
            tk = attributes.get("toolkit", "") or ""
            if tk.lower().startswith("qt"):
                return True

        # (2) application-level toolkit name (where Qt reports "Qt")
        app_tk = self.get_app_toolkit_name() or ""
        if app_tk.lower().startswith("qt"):
            return True

        return False

    def get_character_extents(self, accessible, offset):
        """ Screen rect of the character at offset """
        try:
            rect = self._get_character_extents(offset)
        except Exception as ex:  # private exception gi._glib.GError when
                # right clicking onboards unity2d launcher (Precise)
            _logger.atspi("Invalid accessible,"
                          " failed to get character extents: " +
                          unicode_str(ex))
            rect = Rect()
        return rect

    def _get_character_extents(self, offset):
        """
        Screen rect of the character at offset of the accessible, little
        caching and exception handling.
        """
        scale = config.window_scaling_factor
        if scale != 1.0:
            # Only Gtk-3 widgets return scaled coordinates, all others,
            # including Gtk-2 apps like firefox, clawsmail and Qt-apps,
            # apparently don't.
            if self.is_toolkit_gtk3():
                scale = 1.0
            else:
                scale = 1.0 / config.window_scaling_factor

        ext = self._accessible.get_character_extents(offset,
                                                     Atspi.CoordType.SCREEN)
        # x, y = ext.x + ext.width / 2, ext.y + ext.height / 2
        # offset_control = self._accessible.get_offset_at_point(x, y,
        #                                                Atspi.CoordType.SCREEN)
        # print(offset, offset_control)
        return Rect(ext.x * scale, ext.y * scale,
                    ext.width * scale, ext.height * scale)


class AsyncEvent:
    """
    Decouple AT-SPI events from D-Bus calls to reduce the risk for deadlocks.
    """
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self._kwargs = kwargs

    def __repr__(self):
        return type(self).__name__ + "(" + \
            ", ".join(str(key) + "=" + repr(val)
                      for key, val in self._kwargs.items()) \
            + ")"


class AtspiStateTracker(EventSource):
    """
    Keeps track of the currently active accessible by listening
    to AT-SPI focus events.
    """

    _focus_event_names      = ("text-entry-activated",)
    _text_event_names       = ("text-changed", "text-caret-moved")
    _key_stroke_event_names = ("key-pressed",)
    _async_event_names      = ("async-focus-changed",
                               "async-text-changed",
                               "async-text-caret-moved")
    _event_names = (_async_event_names +
                    _focus_event_names +
                    _text_event_names +
                    _key_stroke_event_names)

    _focus_listeners_registered = False
    _keystroke_listeners_registered = False
    _text_listeners_registered = False

    _keystroke_listener = None

    # asynchronously accessible members
    _focused_accessible = None   # last focused editable accessible
    _focused_pid = None          # pid of last focused editable accessible
    _active_accessible = None    # currently active editable accessible
    _active_accessible_activation_time = 0.0  # time since focus received
    _last_active_accessible = None

    _poll_unity_timer = Timer()

    def __new__(cls, *args, **kwargs):
        """
        Singleton magic.
        """
        if not hasattr(cls, "self"):
            cls.self = object.__new__(cls, *args, **kwargs)
            cls.self.construct()
        return cls.self

    def __init__(self):
        """
        Called multiple times, don't use this.
        """
        pass

    def construct(self):
        """
        Singleton constructor, runs only once.
        """
        EventSource.__init__(self, self._event_names)

        self._frozen = False

    def cleanup(self):
        EventSource.cleanup(self)
        self._register_atspi_listeners(False)

    def connect(self, event_name, callback):
        EventSource.connect(self, event_name, callback)
        self._update_listeners()

    def disconnect(self, event_name, callback):
        had_listeners = self.has_listeners(self._event_names)

        EventSource.disconnect(self, event_name, callback)
        self._update_listeners()

        # help debugging disconnecting events on exit
        if had_listeners and not self.has_listeners(self._event_names):
            _logger.info("all listeners disconnected")

    def _update_listeners(self):
        register = self.has_listeners(self._focus_event_names)
        self._register_atspi_focus_listeners(register)

        register = self.has_listeners(self._text_event_names)
        self._register_atspi_text_listeners(register)

        register = self.has_listeners(self._key_stroke_event_names)
        self._register_atspi_keystroke_listeners(register)

    def _register_atspi_listeners(self, register):
        self._register_atspi_focus_listeners(register)
        self._register_atspi_text_listeners(register)
        self._register_atspi_keystroke_listeners(register)

    def _register_atspi_focus_listeners(self, register):
        if "Atspi" not in globals():
            return

        if self._focus_listeners_registered != register:

            if register:
                self.atspi_connect("_listener_focus",
                                   "focus",
                                   self._on_atspi_global_focus)
                self.atspi_connect("_listener_object_focus",
                                   "object:state-changed:focused",
                                   self._on_atspi_object_focus)

                # private asynchronous events
                for name in self._async_event_names:
                    handler = "_on_" + name.replace("-", "_")
                    EventSource.connect(self, name, getattr(self, handler))
            else:
                self._poll_unity_timer.stop()

                self.atspi_disconnect("_listener_focus",
                                      "focus")
                self.atspi_disconnect("_listener_object_focus",
                                      "object:state-changed:focused")

                for name in self._async_event_names:
                    handler = "_on_" + name.replace("-", "_")
                    EventSource.disconnect(self, name, getattr(self, handler))

            self._focus_listeners_registered = register

    def _register_atspi_text_listeners(self, register):
        if "Atspi" not in globals():
            return

        if self._text_listeners_registered != register:
            if register:
                self.atspi_connect("_listener_text_changed",
                                   "object:text-changed",
                                   self._on_atspi_text_changed)
                self.atspi_connect("_listener_text_caret_moved",
                                   "object:text-caret-moved",
                                   self._on_atspi_text_caret_moved)

            else:
                self.atspi_disconnect("_listener_text_changed",
                                      "object:text-changed")
                self.atspi_disconnect("_listener_text_caret_moved",
                                      "object:text-caret-moved")

        self._text_listeners_registered = register

    def _register_atspi_keystroke_listeners(self, register):
        if "Atspi" not in globals():
            return

        if self._keystroke_listeners_registered != register:
            modifier_masks = range(16)

            if register:
                if not self._keystroke_listener:
                    self._keystroke_listener = \
                        Atspi.DeviceListener.new(self._on_atspi_keystroke,
                                                 None)

                for modifier_mask in modifier_masks:
                    Atspi.register_keystroke_listener(
                        self._keystroke_listener,
                        None,        # key set, None=all
                        modifier_mask,
                        Atspi.KeyEventType.PRESSED,
                        Atspi.KeyListenerSyncType.SYNCHRONOUS)
            else:
                # Apparently any single deregister call will turn off
                # all the other registered modifier_masks too. Since
                # deregistering takes extremely long (~2.5s for 16 calls)
                # seize the opportunity and just pick a single arbitrary
                # mask (Quantal).
                modifier_masks = [2]

                for modifier_mask in modifier_masks:
                    Atspi.deregister_keystroke_listener(
                        self._keystroke_listener,
                        None,  # key set, None=all
                        modifier_mask,
                        Atspi.KeyEventType.PRESSED)

        self._keystroke_listeners_registered = register

    def atspi_connect(self, attribute, event, callback):
        """
        Start listening to an AT-SPI event.
        Creates a new event listener for each event, since this seems
        to be the only way to allow reliable deregistering of events.
        """
        if hasattr(self, attribute):
            listener = getattr(self, attribute)
        else:
            listener = None

        if listener is None:
            listener = Atspi.EventListener.new(callback, None)
            setattr(self, attribute, listener)
        listener.register(event)

    def atspi_disconnect(self, attribute, event):
        """
        Stop listening to AT-SPI event.
        """
        listener = getattr(self, attribute)
        listener.deregister(event)

    def freeze(self):
        """
        Freeze AT-SPI message processing, e.g. while displaying
        a dialog or popoup menu.
        """
        self._register_atspi_listeners(False)
        self._frozen = True

    def thaw(self):
        """
        Resume AT-SPI message processing.
        """
        self._update_listeners()
        self._frozen = False

    def emit_async(self, event_name, *args, **kwargs):
        if not self._frozen:
            EventSource.emit_async(self, event_name, *args, **kwargs)

    def _get_cached_accessible(self, accessible):
        return CachedAccessible(accessible) \
            if accessible else None

    # ######### synchronous handlers ######### #

    def _on_atspi_global_focus(self, event, user_data):
        self._on_atspi_focus(event, True)

    def _on_atspi_object_focus(self, event, user_data):
        self._on_atspi_focus(event)

    def _on_atspi_focus(self, event, focus_received=False):
        focused = (bool(focus_received) or
                   bool(event.detail1))  # received focus?
        ae = AsyncEvent(accessible=self._get_cached_accessible(event.source),
                        focused=focused)
        self.emit_async("async-focus-changed", ae)

    def _on_atspi_text_changed(self, event, user_data):
        # print("_on_atspi_text_changed", event.detail1, event.detail2,
        #       event.source, event.type, event.type.endswith("delete"))
        ae = AsyncEvent(accessible=self._get_cached_accessible(event.source),
                        type=event.type,
                        pos=event.detail1,
                        length=event.detail2)
        self.emit_async("async-text-changed", ae)
        return False

    def _on_atspi_text_caret_moved(self, event, user_data):
        # print("_on_atspi_text_caret_moved", event.detail1, event.detail2,
        #       event.source, event.type, event.source.get_name(),
        #       event.source.get_role())
        ae = AsyncEvent(accessible=self._get_cached_accessible(event.source),
                        caret=event.detail1)
        self.emit_async("async-text-caret-moved", ae)
        return False

    def _on_atspi_keystroke(self, event, user_data):
        if event.type == Atspi.EventType.KEY_PRESSED_EVENT:
            _logger.atspi("key-stroke {} {} {} {}"
                          .format(event.modifiers,
                                  event.hw_code, event.id, event.is_text))
            # keysym = event.id # What is this? Not an XK_ keysym apparently.
            ae = AsyncEvent(hw_code=event.hw_code,
                            modifiers=event.modifiers)
            self.emit_async("key-pressed", ae)

        return False  # don't consume event

    # ######### asynchronous handlers ######### #
    def _on_async_focus_changed(self, event):
        accessible = event.accessible
        focused = event.focused

        # Don't access the accessible while frozen. This leads to deadlocks
        # while displaying Onboard's own dialogs/popup menu's.
        if self._frozen:
            return

        self._log_accessible(accessible, focused)

        if not accessible:
            return

        # Ignore AT-SPI focus events that originate from Onboard's own
        # accessibility tree. When the user clicks a key, the Onboard
        # window emits focus events for its own buttons / window; if
        # processed, _handle_focus_changed_apps drops the target app's
        # editable accessible (PID mismatch heuristic), and the next
        # press_unicode falls back to the broken keysynth path
        # instead of using the AT-SPI direct-insert that was correctly
        # armed when the target was focused.
        try:
            if accessible.get_pid() == os.getpid():
                _logger.atspi(
                    "ignoring AT-SPI focus event from our own process "
                    "(pid={})".format(os.getpid()))
                return
        except Exception:
            pass

        app_name = accessible.get_app_name().lower()
        if app_name == "unity":
            self._handle_focus_changed_unity(event)
        else:
            self._handle_focus_changed_apps(event)

    def _handle_focus_changed_apps(self, event):
        """ Focus change in regular applications """
        accessible = event.accessible
        focused = event.focused

        # Since Trusty, focus events no longer come reliably in a
        # predictable order. -> Store the last editable accessible
        # so we can pick it over later focused non-editable ones.
        # Helps to keep the keyboard open in presence of popup selections
        # e.g. in GNOME's file dialog and in Unity Dash.
        if self._focused_accessible == accessible:
            if not focused:
                self._focused_accessible = None
        else:
            pid = accessible.get_pid()

            if focused:
                self._poll_unity_timer.stop()

                if accessible.is_editable():
                    self._focused_accessible = accessible
                    self._focused_pid = pid

                # Static accessible, i.e. something that cannot
                # accidentally steal the focus from an editable
                # accessible. e.g. firefox ATSPI_ROLE_DOCUMENT_FRAME?
                elif accessible.is_not_focus_stealing():
                    self._focused_accessible = None
                    self._focused_pid = None

                else:
                    # Wily: attempt to hide when unity dash closes
                    # (there's no focus lost event).
                    # Also check duration since last activation to
                    # skip out of order focus events (firefox
                    # ATSPI_ROLE_DOCUMENT_FRAME) for a short while
                    # after opening dash.
                    now = time.time()
                    if focused and \
                       now - self._active_accessible_activation_time > .5:
                        if self._focused_pid != pid:
                            self._focused_accessible = None
                            _logger.atspi("Dropping accessible due to "
                                          "pid change: {} != {} "
                                          .format(self._focused_pid, pid))

        # Has the previously focused accessible lost the focus?
        active_accessible = self._focused_accessible
        if active_accessible and \
           not active_accessible.is_focused(True):

            # Zesty: Firefox 50+ loses focus of the URL entry after
            # typing just a few letters and focuses a completion
            # menu item instead. Let's pretend the accessible is
            # still focused in that case.
            is_firefox_completion = \
                self._focused_accessible.is_urlbar() and \
                accessible.get_role() == Atspi.Role.MENU_ITEM

            if not is_firefox_completion:
                active_accessible = None

        self._set_active_accessible(active_accessible)

    def _handle_focus_changed_unity(self, event):
        """ Focus change in Unity Dash """
        accessible = event.accessible
        focused = event.focused

        # Wily: prevent random icons, buttons and toolbars
        # in unity dash from hiding Onboard. Somehow hovering
        # over those buttons silently drops the focus from the
        # text entry. Let's pretend the buttons don't exist
        # and keep the previously saved text entry active.

        # Zesty: Don't fight lost focus events anymore, only
        # react to focus events when the text entry gains focus.
        if focused and \
           accessible.is_editable():
            self._focused_accessible = accessible
            self._set_active_accessible(accessible)

            # For hiding we poll Dash's toplevel accessible
            def _poll_unity_dash():
                frame = accessible.get_frame()
                state_set = frame.get_state_set()

                _logger.debug(
                    "polling unity dash state_set: {}"
                    .format(AtspiStateType.to_strings(state_set)))

                if not state_set or \
                   not state_set.contains(Atspi.StateType.ACTIVE):
                    self._focused_accessible = None
                    self._set_active_accessible(None)
                    return False

                return True

            # Only ever start polling if Dash is "ACTIVE".
            # The state_set might change in the future and the
            # keyboard better fail to auto-hide than to never show.
            frame = accessible.get_frame()
            state_set = frame.get_state_set()

            _logger.debug(
                "dash focused, state_set: {}"
                .format(AtspiStateType.to_strings(state_set)))

            if state_set and \
               state_set.contains(Atspi.StateType.ACTIVE):
                self._poll_unity_timer.start(0.5, _poll_unity_dash)

    def _set_active_accessible(self, accessible):
        if self._active_accessible != accessible:
            self._active_accessible = accessible

            if self._active_accessible or \
               self._last_active_accessible:

                # notify listeners
                self.emit("text-entry-activated", self._active_accessible)

                self._last_active_accessible = self._active_accessible
                self._active_accessible_activation_time = time.time()

    def _on_async_text_changed(self, event):
        if event.accessible == self._active_accessible:

            type = event.type
            insert = type.endswith(("insert", "insert:system"))
            delete = type.endswith(("delete", "delete:system"))
            # print(event.accessible.get_id(), type, insert)

            _logger.atspi("_on_async_text_changed: "
                          "type={} insert={} delete={}"
                          .format(type, insert, delete))

            if insert or delete:
                event.insert = insert
                self.emit("text-changed", event)
            else:
                _logger.warning("_on_async_text_changed: "
                                "unknown event type '{}'"
                                .format(event.type))

    def _on_async_text_caret_moved(self, event):
        if event.accessible == self._active_accessible:
            self.emit("text-caret-moved", event)

    def _log_accessible(self, accessible, focused):
        if _logger.isEnabledFor(_logger.LEVEL_ATSPI):
            msg = "AT-SPI focus event: focused={}, ".format(focused)
            msg += "accessible={}, ".format(accessible)

            if accessible:
                name = accessible.get_name()
                role = accessible.get_role()
                role_name = accessible.get_role_name()
                state_set = accessible.get_state_set()
                states = state_set.states
                editable = state_set.contains(Atspi.StateType.EDITABLE) \
                    if state_set else None
                extents = accessible.get_extents()

                msg += "name={name}, role={role}({role_name}), " \
                       "editable={editable}, states={states}, " \
                       "extents={extents}]" \
                       .format(accessible=accessible, name=repr(name),
                               role=role.value_name if role else role,
                               role_name=repr(role_name),
                               editable=editable,
                               states=states,
                               extents=extents
                               )
            _logger.atspi(msg)


class AtspiStateType:
    states = ['ACTIVE',
              'ANIMATED',
              'ARMED',
              'BUSY',
              'CHECKED',
              'COLLAPSED',
              'DEFUNCT',
              'EDITABLE',
              'ENABLED',
              'EXPANDABLE',
              'EXPANDED',
              'FOCUSABLE',
              'FOCUSED',
              'HAS_TOOLTIP',
              'HORIZONTAL',
              'ICONIFIED',
              'INDETERMINATE',
              'INVALID',
              'INVALID_ENTRY',
              'IS_DEFAULT',
              'LAST_DEFINED',
              'MANAGES_DESCENDANTS',
              'MODAL',
              'MULTISELECTABLE',
              'MULTI_LINE',
              'OPAQUE',
              'PRESSED',
              'REQUIRED',
              'RESIZABLE',
              'SELECTABLE',
              'SELECTABLE_TEXT',
              'SELECTED',
              'SENSITIVE',
              'SHOWING',
              'SINGLE_LINE',
              'STALE',
              'SUPPORTS_AUTOCOMPLETION',
              'TRANSIENT',
              'TRUNCATED',
              'VERTICAL',
              'VISIBLE',
              'VISITED',
              ]

    @staticmethod
    def to_strings(state_set):
        result = []
        if state_set is not None:
            for s in AtspiStateType.states:
                if state_set.contains(getattr(Atspi.StateType, s)):
                    result.append(s)
        return result


