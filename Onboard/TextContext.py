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

from __future__ import division, print_function, unicode_literals

import unicodedata
import time

import logging
_logger = logging.getLogger(__name__)

from Onboard.Version import require_gi_versions
require_gi_versions()
try:
    from gi.repository import Atspi
except ImportError as e:
    pass

from Onboard.AtspiStateTracker import AtspiStateTracker, AtspiStateType
from Onboard.TextDomain        import TextDomains
from Onboard.TextChanges       import TextChanges, TextSpan
from Onboard.utils             import KeyCode, unicode_str
from Onboard.Timer             import Timer
from Onboard                   import KeyCommon

### Config Singleton ###
from Onboard.Config import Config
config = Config()


class TextContext:
    """
    Keep track of the current text context and intecept typed key events.
    """

    def cleanup(self):
        pass

    def reset(self):
        pass

    def can_insert_text(self):
        return NotImplementedError()

    def insert_text(self, offset, text):
        return NotImplementedError()

    def insert_text_at_caret(self, text):
        return NotImplementedError()

    def delete_text(self, offset, length=1):
        return NotImplementedError()

    def delete_text_before_caret(self, length=1):
        return NotImplementedError()

    def get_context(self):
        raise NotImplementedError()

    def get_line(self):
        raise NotImplementedError()

    def get_line_caret_pos(self):
        raise NotImplementedError()

    def get_changes(self):
        raise NotImplementedError()

    def clear_changes(self):
        raise NotImplementedError()


class AtspiTextContext(TextContext):
    """
    Keep track of the current text context with AT-SPI
    """

    _state_tracker = AtspiStateTracker()

    def __init__(self, wp):
        self._wp = wp
        self._accessible = None
        self._can_insert_text = False

        self._text_domains = TextDomains()
        self._text_domain = self._text_domains.get_nop_domain()

        self._changes = TextChanges()
        self._entering_text = False
        self._text_changed = False

        self._context = ""
        self._line = ""
        self._line_caret = 0
        self._selection_span = TextSpan()
        self._begin_of_text = False        # context starts at begin of text?
        self._begin_of_text_offset = None  # offset of text begin

        self._pending_separator_span = None
        self._last_text_change_time = 0
        self._last_caret_move_time = 0
        self._last_caret_move_position = 0

        self._last_context = None
        self._last_line = None

        self._update_context_timer = Timer()
        self._update_context_delay_normal = 0.01
        self._update_context_delay = self._update_context_delay_normal

    def cleanup(self):
        self._register_atspi_listeners(False)

    def enable(self, enable):
        self._register_atspi_listeners(enable)

    def get_text_domain(self):
        return self._text_domain

    def set_pending_separator(self, separator_span=None):
        """ Remember this separator span for later insertion. """
        if self._pending_separator_span is not separator_span:
            self._pending_separator_span = separator_span

    def get_pending_separator(self):
        """ Return current pending separator span or None """
        return self._pending_separator_span

    def get_context(self):
        """
        Returns the predictions context, i.e. some range of
        text before the caret position.
        """
        if self._accessible is None:
            return ""

        # Don't update suggestions in scrolling terminals
        if self._entering_text or \
           not self._text_changed or \
           self.can_suggest_before_typing():
            return self._context

        return ""

    def get_bot_context(self):
        """
        Returns the predictions context with
        begin of text marker (at text begin).
        """
        context = ""
        if self._accessible:
            context = self.get_context()

            # prepend domain specific begin-of-text marker
            if self._begin_of_text:
                marker = self.get_text_begin_marker()
                if marker:
                    context = marker + " " + context

        return context

    def get_pending_bot_context(self):
        """
        Context including bot marker and pending separator.
        """
        context = self.get_bot_context()
        if self._pending_separator_span is not None:
            context += self._pending_separator_span.get_span_text()
        return context

    def get_line(self):
        return self._line \
               if self._accessible else ""

    def get_line_caret_pos(self):
        return self._line_caret \
               if self._accessible else 0

    def get_line_past_caret(self):
        return self._line[self._line_caret:] \
               if self._accessible else ""

    def get_selection_span(self):
        return self._selection_span \
               if self._accessible else None

    def get_span_at_caret(self):
        if not self._accessible:
            return None

        span = self._selection_span.copy()
        span.length = 0
        return span

    def get_caret(self):
        return self._selection_span.begin() \
            if self._accessible else 0

    def get_character_extents(self, offset):
        accessible = self._accessible
        if accessible:
            return accessible.get_character_extents(offset)
        else:
            return None

    def get_text_begin_marker(self):
        domain = self.get_text_domain()
        if domain:
            return domain.get_text_begin_marker()
        return ""

    def can_record_insertion(self, accessible, pos, length):
        domain = self.get_text_domain()
        if domain:
            return domain.can_record_insertion(accessible, pos, length)
        return True

    def can_suggest_before_typing(self):
        domain = self.get_text_domain()
        if domain:
            return domain.can_suggest_before_typing()
        return True

    def can_auto_punctuate(self):
        domain = self.get_text_domain()
        if domain:
            return domain.can_auto_punctuate(self._begin_of_text)
        return False

    def get_begin_of_text_offset(self):
        return self._begin_of_text_offset \
               if self._accessible else None

    def get_changes(self):
        return self._changes

    def has_changes(self):
        """ Are there any changes to learn? """
        return not self._changes.is_empty()

    def clear_changes(self):
        self._changes.clear()

    def can_insert_text(self):
        """
        Can delete or insert text into the accessible?
        """
        # support for inserting is spotty: not in firefox, terminal
        return bool(self._accessible) and self._can_insert_text

    def delete_text(self, offset, length=1):
        """ Delete directly, without going through faking key presses. """
        self._accessible.delete_text(offset, offset + length)

    def delete_text_before_caret(self, length=1):
        """ Delete directly, without going through faking key presses. """
        try:
            caret_offset = self._accessible.get_caret_offset()
        except Exception as ex:  # Private exception gi._glib.GError when
            _logger.info("TextContext.delete_text_before_caret(): " +
                         unicode_str(ex))
            return

        self.delete_text(caret_offset - length, length)

    def insert_text(self, offset, text):
        """
        Insert directly, without going through faking key presses.
        """
        self._accessible.insert_text(offset, text)

        # Move the caret after insertion if the accessible itself
        # hasn't done so already. This assumes the insertion begins at
        # the current caret position, which always happens to be the case
        # currently.
        # Only the nautilus rename text entry appears to need this.
        offset_before = offset
        try:
            offset_after = self._accessible.get_caret_offset()
        except Exception as ex:  # Private exception gi._glib.GError when
            _logger.info("TextContext.insert_text(): " +
                         unicode_str(ex))
            return

        if text and offset_before == offset_after:
            self._accessible.set_caret_offset(offset_before + len(text))

    def insert_text_at_caret(self, text):
        """
        Insert directly, without going through faking key presses.
        Fails for terminal and firefox, unfortunately.
        """
        try:
            caret_offset = self._accessible.get_caret_offset()
        except Exception as ex:  # Private exception gi._glib.GError when
            _logger.info("TextContext.insert_text_at_caret(): " +
                         unicode_str(ex))
            return

        self.insert_text(caret_offset, text)

    def _register_atspi_listeners(self, register=True):
        st = self._state_tracker
        if register:
            st.connect("text-entry-activated", self._on_text_entry_activated)
            st.connect("text-changed", self._on_text_changed)
            st.connect("text-caret-moved", self._on_text_caret_moved)
            # st.connect("key-pressed", self._on_atspi_key_pressed)
        else:
            st.disconnect("text-entry-activated", self._on_text_entry_activated)
            st.disconnect("text-changed", self._on_text_changed)
            st.disconnect("text-caret-moved", self._on_text_caret_moved)
            # st.disconnect("key-pressed", self._on_atspi_key_pressed)

    def get_accessible_capabilities(self, accessible):
        can_insert_text = False

        if accessible:

            # Can insert text via Atspi?
            # Advantages:
            # - faster, no individual key presses
            # - trouble-free insertion of all unicode characters
            # - on Wayland, sidesteps xkb-group interpretation of the
            #   injected keycodes (some keys and snippet text
            #   must always be inserted as Latin regardless of
            #   the active layout)
            if "EditableText" in accessible.get_interfaces():
                # Support for atspi text insertion is spotty.
                # Firefox, LibreOffice Writer, gnome-terminal don't support it,
                # even if they claim to implement the EditableText interface.

                # Allow direct text insertion for gtk widgets.
                if accessible.is_toolkit_gtk3():
                    can_insert_text = True

                # Allow it for Qt widgets too -- Qt 5.15+/6 on
                # modern Linux implement EditableText.insert correctly.
                # Required on Wayland (KDE Plasma) to type some keys and
                # snippets while a non-Latin xkb group is active, since
                # the uinput keysynth path cannot select a layout
                # group on a per-keystroke basis.
                elif accessible.is_toolkit_qt():
                    can_insert_text = True

        return can_insert_text

    def _on_text_entry_activated(self, accessible):
        # old text_domain still valid here
        self._wp.on_text_entry_deactivated()

        # keep track of the active accessible asynchronously
        self._accessible = accessible
        self._entering_text = False
        self._text_changed = False

        # make sure state is filled with essential entries
        if accessible:
            accessible.get_role()
            accessible.get_attributes()
            accessible.get_interfaces()
            accessible.is_urlbar()
            state = accessible.get_state()
        else:
            state = {}

        # select text domain matching this accessible
        self._text_domain = self._text_domains.find_match(**state)
        self._text_domain.init_domain()

        # determine capabilities of this accessible
        self._can_insert_text = \
            self.get_accessible_capabilities(accessible)

        # log accessible info
        if _logger.isEnabledFor(_logger.LEVEL_ATSPI):
            log = _logger.atspi
            log("-" * 70)
            log("Accessible focused: ")
            indent = " " * 4
            if accessible:
                state = accessible.get_all_state()
                for key, value in sorted(state.items()):
                    msg = str(key) + "="
                    if key == "state-set":
                        msg += repr(AtspiStateType.to_strings(value))
                    elif hasattr(value, "value_name"):  # e.g. role
                        msg += value.value_name
                    else:
                        msg += repr(value)
                    log(indent + msg)
                log(indent + "text_domain: {}"
                    .format(self._text_domain and
                            type(self._text_domain).__name__))
                log(indent + "can_insert_text: {}"
                    .format(self._can_insert_text))
            else:
                log(indent + "None")

        self._update_context()

        self._wp.on_text_entry_activated()

    def _on_text_changed(self, event):
        _logger.atspi("_on_text_changed: pos={}, length={}, insert={}"
                      .format(event.pos, event.length, event.insert))

        insertion_span = self._record_text_change(event.pos,
                                                  event.length,
                                                  event.insert)
        # synchronously notify of text insertion
        if insertion_span:
            try:
                caret_offset = self._accessible.get_caret_offset()
            except Exception as ex:  # Private exception gi._glib.GError when
                _logger.info("TextContext._on_text_changed(): " +
                            unicode_str(ex))
            else:
                self._wp.on_text_inserted(insertion_span, caret_offset)

        self._last_text_change_time = time.time()
        self._update_context()

    def _on_text_caret_moved(self, event):
        self._last_caret_move_time = time.time()
        self._last_caret_move_position = event.caret
        self._update_context()
        self._wp.on_text_caret_moved()

    def _on_atspi_key_pressed(self, event):
        """ disabled, Francesco didn't receive any AT-SPI key-strokes. """
        # keycode = event.hw_code # uh oh, only keycodes...
        #                         # hopefully "c" doesn't move around a lot.
        # modifiers = event.modifiers
        # self._handle_key_press(keycode, modifiers)

    def on_onboard_typing(self, key, mod_mask):
        if key.is_text_changing():
            keycode = 0
            if key.is_return():
                keycode = KeyCode.KP_Enter
            else:
                label = key.get_label()
                if label == "C" or label == "c":
                    keycode = KeyCode.C

            self._handle_key_press(keycode, mod_mask)

    def _handle_key_press(self, keycode, modifiers):
        if self._accessible:
            domain = self.get_text_domain()
            if domain:
                self._entering_text, end_of_editing = \
                    domain.handle_key_press(keycode, modifiers)

                if end_of_editing is True:
                    self._wp.commit_changes()
                elif end_of_editing is False:
                    self._wp.discard_changes()

    def _record_text_change(self, pos, length, insert):
        accessible = self._accessible

        insertion_span = None
        char_count = None
        if accessible:
            try:
                char_count = accessible.get_character_count()
            except:     # gi._glib.GError: The application no longer exists
                        # when closing a tab in gnome-terminal.
                char_count = None

        if _logger.isEnabledFor(_logger.LEVEL_ATSPI):
            _logger.atspi("_record_text_change1(pos={}, length={}, "
                          "insert={}): "
                          "accessible={} char_count={}"
                          .format(pos, length, insert,
                                  accessible, char_count))

        if char_count is not None:
            # record the change
            spans_to_update = []

            if insert:
                # None = remember nothing, just update existing spans.
                include_length = None

                if self._entering_text:
                    can_record_insertion = \
                        self.can_record_insertion(accessible, pos, length)

                    if _logger.isEnabledFor(_logger.LEVEL_ATSPI):
                        _logger.atspi("_record_text_change2(): "
                                      "can_record_insertion={} "
                                      "is_typing={} "
                                      .format(can_record_insertion,
                                              self._wp.is_typing()))

                    if can_record_insertion:
                        if self._wp.is_typing() or length < 30:
                            # Remember all of the insertion, might have been
                            # a pressed snippet or wordlist button.
                            include_length = -1
                        else:
                            # Remember only the first few characters.
                            # Large inserts can be paste, reload or scroll
                            # operations. Only learn the first word of these.
                            include_length = 2

                        # simple span for current insertion
                        begin = max(pos - 100, 0)
                        end = min(pos + length + 100, char_count)
                        try:
                            text = accessible.get_text(begin, end)
                        except Exception as ex:
                            _logger.info("_record_text_change() exception 1: "
                                         + unicode_str(ex))
                        else:
                            insertion_span = TextSpan(pos, length, text, begin)

                spans_to_update = self._changes.insert(pos, length,
                                                       include_length)
            else:
                spans_to_update = self._changes.delete(pos, length,
                                                       self._entering_text)

            # update text of all modified spans
            for span in spans_to_update:
                # Get some more text around the span to hopefully
                # include whole words at beginning and end.
                begin = max(span.begin() - 100, 0)
                end = min(span.end() + 100, char_count)
                try:
                    span.text = accessible.get_text(begin, end)
                except Exception as ex:
                    _logger.info("_record_text_change() exception 2: " +
                                 unicode_str(ex))
                    span.text = ""
                span.text_pos = begin

        self._text_changed = True

        if _logger.isEnabledFor(_logger.LEVEL_ATSPI):
            _logger.atspi("_record_text_change3(): "
                          "insertion_span={} spans_to_update={} "
                          "changes={} "
                          .format(insertion_span, spans_to_update,
                                  self._changes))

        return insertion_span

    def set_update_context_delay(self, delay):
        self._update_context_delay = delay

    def reset_update_context_delay(self):
        self._update_context_delay = self._update_context_delay_normal

    def _update_context(self):
        self._update_context_timer.start(self._update_context_delay,
                                         self.on_text_context_changed)

    def on_text_context_changed(self):
        # Clear pending separator when the user clicked to move
        # the cursor away from the separator position.
        if self._pending_separator_span:
            # Lone caret movement, no recent text change?
            if self._last_caret_move_time - self._last_text_change_time > 1.0:
                # Away from the separator?
                if self._last_caret_move_position != \
                   self._pending_separator_span.begin():
                    self.set_pending_separator(None)

        result = self._text_domain.read_context(self._wp, self._accessible)
        if result is not None:
            (self._context,
             self._line,
             self._line_caret,
             self._selection_span,
             self._begin_of_text,
             self._begin_of_text_offset) = result

            # make sure to include bot-markers and pending separator
            context = self.get_pending_bot_context()
            change_detected = (self._last_context != context or
                               self._last_line != self._line)
            if change_detected:
                self._last_context = context
                self._last_line    = self._line

            self._wp.on_text_context_changed(change_detected)

        return False


class InputLine(TextContext):
    """
    Track key presses ourselves.
    Advantage: Doesn't require AT-SPI
    Problems:  Misses key repeats,
               Doesn't know about keymap translations before events are
               delivered to their destination, i.e records wrong key
               strokes when changing keymaps.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.line = ""
        self.caret = 0
        self.valid = True

        self.word_infos = {}

    def is_valid(self):
        return self.valid

    def is_empty(self):
        return len(self.line) == 0

    def insert(self, s):
        self.line   = self.line[:self.caret] + s + self.line[self.caret:]
        self.move_caret(len(s))

    def delete_left(self, n=1):     # backspace
        self.line = self.line[:self.caret - n] + self.line[self.caret:]
        self.move_caret(-n)

    def delete_right(self, n=1):    # delete
        self.line = self.line[:self.caret] + self.line[self.caret + n:]

    def move_caret(self, n):
        self.caret += n

        # moving into unknown territory -> suggest reset
        if self.caret < 0:
            self.caret = 0
            self.valid = False
        if self.caret > len(self.line):
            self.caret = len(self.line)
            self.valid = False

    def get_context(self):
        return self.line[:self.caret]

    def get_line(self):
        return self.line

    def get_line_caret_pos(self):
        return self.caret

    @staticmethod
    def is_printable(char):
        """
        True for printable keys including whitespace as defined for isprint().
        """
        if char == "\t":
            return True
        return not unicodedata.category(char) in ('Cc', 'Cf', 'Cs', 'Co',
                                                  'Cn', 'Zl', 'Zp')

    def track_sent_key(self, key, mods):
        """
        Sync input_line with single key presses.
        WORD_ACTION and MACRO_ACTION do this in press_key_string.
        """
        end_editing = False

        if config.wp.stealth_mode:
            return True

        id = key.id.upper()
        char = key.get_label()
        if char is None or len(char) > 1:
            char = ""

        if key.action_type == KeyCommon.WORD_ACTION:
            pass  # don't reset input on word insertion

        elif key.action_type == KeyCommon.MODIFIER_ACTION:
            pass  # simply pressing a modifier shouldn't stop the word

        elif key.action_type == KeyCommon.BUTTON_ACTION:
            pass

        elif key.action_type == KeyCommon.KEYSYM_ACTION:
            if id == 'ESC':
                self.reset()
            end_editing = True

        elif key.action_type == KeyCommon.KEYPRESS_NAME_ACTION:
            if id == 'DELE':
                self.delete_right()
            elif id == 'LEFT':
                self.move_caret(-1)
            elif id == 'RGHT':
                self.move_caret(1)
            else:
                end_editing = True

        elif key.action_type == KeyCommon.KEYCODE_ACTION:
            if id == 'RTRN':
                char = "\n"
            elif id == 'SPCE':
                char = " "
            elif id == 'TAB':
                char = "\t"

            if id == 'BKSP':
                self.delete_left()
            elif self.is_printable(char):
                if mods[4]:  # ctrl+key press?
                    end_editing = True
                else:
                    self.insert(char)
            else:
                end_editing = True
        else:
            end_editing = True

        if not self.is_valid():  # caret moved outside known range?
            end_editing = True

        # print end_editing,"'%s' " % self.line, self.caret
        return end_editing

