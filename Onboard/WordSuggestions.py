# -*- coding: utf-8 -*-

# Copyright © 2009-2010, 2012-2017 marmuta <marmvta@gmail.com>
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

import os
import time
import re
import shutil
import weakref

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import Gtk, Pango

import logging
_logger = logging.getLogger(__name__)

try:
    import Onboard.pypredict as pypredict
except Exception as e:
    pypredict = None
    _logger.warning("pypredict unavailable, disabling word suggestions: %s" % e)

from Onboard                   import KeyCommon
from Onboard.TextContext       import AtspiTextContext, InputLine
from Onboard.TextChanges       import TextChanges, TextSpan
from Onboard.SpellChecker      import SpellChecker
from Onboard.LanguageSupport   import LanguageDB
from Onboard.Layout            import LayoutPanel
from Onboard.AtspiStateTracker import AtspiStateTracker
from Onboard.WPEngine          import WPLocalEngine, ModelCache
from Onboard.utils             import Rect, unicode_str, escape_markup
from Onboard.Timer             import CallOnce, Timer, TimerOnce
from Onboard.KeyGtk            import FullSizeKey, WordKey
from Onboard.KeyboardPopups    import PendingSeparatorPopup

from Onboard.Config import Config
config = Config()


class WordSuggestions:
    """ Keyboard mix-in for word prediction """

    def __init__(self):

        self.input_line = InputLine()
        self.atspi_text_context = AtspiTextContext(self)
        self.text_context = self.atspi_text_context  # initialize for doctests
        self._learn_strategy = LearnStrategyLRU(self)
        self._languagedb = LanguageDB(self)
        self._spell_checker = SpellChecker(self._languagedb)
        self._punctuator = PunctuatorImmediateSeparators(self)
        self._pending_separator_popup = PendingSeparatorPopup()
        self._pending_separator_popup_timer = Timer()
        self._load_error_recovery = ModelErrorRecovery(self)
        self._load_errors_reported = False
        self._wpengine  = None

        self._correction_choices = []
        self._correction_span = None
        self._prediction_choices = []
        self.word_infos = []

        self._separator_before_key_press = None

        self._hide_input_line = False
        self._word_list_bars = []
        self._text_displays = []

        self._focusable_count = 0

    def reset(self):
        self.commit_changes()

    def cleanup(self):
        self.reset()
        if self.text_context:
            self.text_context.cleanup()
        if self._wpengine:
            self._wpengine.cleanup()

    def on_layout_loaded(self):
        self._word_list_bars = self.find_items_from_classes((WordListPanel,))
        self._text_displays = self.find_items_from_ids(("inputline",))
        self.enable_word_suggestions(config.are_word_suggestions_enabled())

    def enable_word_suggestions(self, enable):

        # show/hide word-prediction buttons
        for item in self._get_wordlist_bars():
            item.visible = enable
        for item in self.get_text_displays():
            item.visible = enable

        # show/hide other word list dependent layout items
        layout = self.layout
        if layout:
            for item in layout.iter_global_items():
                if item.group == 'wordlist':
                    item.visible = enable
                elif item.group == 'nowordlist':
                    item.visible = not enable

        self._update_wp_engine()
        self._update_spell_checker()
        self._update_punctuator()

    def _update_wp_engine(self):
        enable = config.is_typing_assistance_enabled()

        if enable:
            # only enable if there is a wordlist in the layout
            if self._get_wordlist_bars():
                self._wpengine = WPLocalEngine()
                self.apply_prediction_profile()
        else:
            if self._wpengine:
                self._wpengine.cleanup()
            self._wpengine = None

        # Init text context tracking.
        # Keep track in and write to both contexts in parallel,
        # but read only from the active one.
        self.text_context = self.atspi_text_context
        self.text_context.enable(enable)  # register AT-SPI listerners

    def on_word_suggestions_enabled(self, enabled):
        """ Config callback for word_suggestions.enabled changes. """
        self.enable_word_suggestions(config.are_word_suggestions_enabled())
        self.invalidate_ui()
        self.commit_ui_updates()

    def apply_prediction_profile(self):
        if self._wpengine:
            lang_id = self.get_lang_id()
            system_lang_id = \
                self._languagedb.find_system_model_language_id(lang_id)

            system_models  = ["lm:system:" + system_lang_id]
            user_models    = ["lm:user:" + lang_id]
            scratch_models = ["lm:mem"]

            persistent_models = system_models + user_models
            # models = system_models + user_models + scratch_models
            auto_learn_models = user_models

            _logger.info("selecting language models: "
                         "system={} user={} auto_learn={}"
                         .format(repr(system_models),
                                 repr(user_models),
                                 repr(auto_learn_models)))

            # auto-learn language model must be part of the user models
            for model in auto_learn_models:
                if model not in user_models:
                    auto_learn_models = None
                    _logger.warning("No auto learn model selected. "
                                    "Please setup learning first.")
                    break

            self._wpengine.set_models(persistent_models,
                                      auto_learn_models,
                                      scratch_models)

            # Make sure to load the language models, so there is no
            # delay on first key press. Don't burden the startup
            # with this either, run it a little delayed.
            TimerOnce(1, self._load_models)

    def _load_models(self):
        self._wpengine.load_models()
        if not self._load_errors_reported:
            self._load_errors_reported = True
            self._load_error_recovery.report_errors(self._wpengine)

    def get_system_model_names(self):
        """ Union of all system and user models """
        return self._wpengine.get_model_names("system")

    def get_merged_model_names(self):
        """ Union of all system and user models """
        names = []
        if self._wpengine:
            system_models = set(self._wpengine.get_model_names("system"))
            user_models = set(self._wpengine.get_model_names("user"))
            names = list(system_models.union(user_models))
        return names

    def get_lang_id(self):
        """
        Current language id; never None.
        """
        lang_id = self.get_active_lang_id()
        if not lang_id:
            lang_id = config.get_system_default_lang_id()
        return lang_id

    def get_active_lang_id(self):
        """
        Current language id; None for system default language.
        """
        return config.typing_assistance.active_language

    def on_active_lang_id_changed(self):
        self._update_spell_checker()
        self.apply_prediction_profile()
        self.invalidate_context_ui()
        self.commit_ui_updates()

    def set_active_lang_id(self, lang_id):
        config.typing_assistance.active_language = lang_id
        self.on_active_lang_id_changed()

    def _get_wordlist_bars(self):
        """
        Return all word list bars, so we don't have
        to look for them all the time.
        """
        return self._word_list_bars

    def get_text_displays(self):
        """
        Return all text feedback items, so we don't have
        to look for them all the time.
        """
        return self._text_displays

    def get_spellchecker_dicts(self):
        return self._spell_checker.get_supported_dict_ids()

    def on_any_key_down(self):
        self.on_activity_detected()

        # Don't auto-save while keys are being pressed,
        # or the resulting delay may cause key repeats.
        if self._wpengine:
            self._wpengine.pause_autosave()

    def on_all_keys_up(self):
        if self._wpengine:
            self._wpengine.resume_autosave()

    def on_before_key_press(self, key):
        self._separator_before_key_press = \
            self.text_context.get_pending_separator()
        if not key.is_modifier() and not key.is_button():
            self._punctuator.on_before_press(key)
        self.text_context.on_onboard_typing(key, self.get_mod_mask())

    def on_before_key_release(self, key):
        self._punctuator.on_before_release(key)

    def on_after_key_release(self, key):
        self._punctuator.on_after_release(key)

        # DEL and BKSP don't always change text and generate
        # AT-SPI events. Be sure to update the separator popup here.
        separator_after = self.text_context.get_pending_separator()
        if self._separator_before_key_press != separator_after:
            self.invalidate_context_ui()

        if not key.is_correction_key():
            self.expand_corrections(False)

        if not key.is_button():
            self._goto_first_prediction()

    def send_key_up(self, key, button, event_type):
        key_type = key.type
        if key_type == KeyCommon.CORRECTION_TYPE:
            self._insert_correction_choice(key, key.code)

        elif key_type == KeyCommon.WORD_TYPE:
            # no punctuation assistance on right click
            self._insert_prediction_choice(key, key.code, button != 3)

        if key_type in [KeyCommon.WORD_TYPE,
                        KeyCommon.CORRECTION_TYPE,
                        KeyCommon.MACRO_TYPE]:
            self.text_context.on_onboard_typing(key, self.get_mod_mask())

    def on_outside_click(self, button):
        if button == 2:  # middle button?
            self._insert_pending_separator()
            self.invalidate_context_ui()

    def on_cancel_outside_click(self):
        self.text_context.set_pending_separator(None)
        self.invalidate_context_ui()
        self.commit_ui_updates()

    def on_activity_detected(self):
        """
        User interacted with the keyboard.
        """
        if self._wpengine:
            self._wpengine.postpone_autosave()

    def on_spell_checker_changed(self):
        self._update_spell_checker()
        self.commit_ui_updates()

    def on_punctuator_changed(self):
        """ On delayed_word_separators_enabled changed """
        self._update_punctuator()
        self.invalidate_context_ui()
        self.commit_ui_updates()

    def _insert_pending_separator(self):
        """
        Insert pending separator at the position of the separator.
        This is only useful when text was inserted from outside of
        Onboard, e.g. by middle clicking to insert something from
        clipboard.
        """
        separator_span = self.text_context.get_pending_separator()
        if separator_span:
            self._punctuator.insert_separator_at_distance(separator_span)
            self.text_context.set_pending_separator(None)

    def _update_spell_checker(self):
        # select the backend
        backend = config.typing_assistance.spell_check_backend \
            if config.is_spell_checker_enabled() else None
        self._spell_checker.set_backend(backend)

        if backend is not None:
            # chose dicts
            lang_id = self.get_lang_id()
            dict_ids = [lang_id] if lang_id else []
            self._spell_checker.set_dict_ids(dict_ids)

        self.invalidate_context_ui()

    def invalidate_for_resize(self):
        self._goto_first_prediction()

    def update_suggestions_ui(self):
        self._update_correction_choices()
        self._update_prediction_choices()
        self._update_pending_separator_popup()
        keys_to_redraw = self.update_inputline()
        keys_to_redraw.extend(self.update_wordlists())
        return keys_to_redraw

    def update_wordlists(self):
        keys_to_redraw = []
        items = self._get_wordlist_bars()
        for item in items:
            keys = item.create_keys(self._correction_choices,
                                    self._prediction_choices)
            keys_to_redraw.extend(keys)

        # layout changed, but doesn't know it yet
        # -> invalidate all item caches
        if self.layout:
            self.layout.invalidate_caches()

        for key in keys_to_redraw:
            key.configure_label(0)

        return items + keys_to_redraw

    def update_language_ui(self):
        return []

    def _update_punctuator(self):
        if config.wp.delayed_word_separators_enabled:
            self._punctuator = PunctuatorDelayedSeparators(self)
        else:
            self._punctuator = PunctuatorImmediateSeparators(self)

    def _has_suggestions(self):
        return bool(self._correction_choices or self._prediction_choices)

    def expand_corrections(self, expand):
        # collapse all expanded corrections
        for item in self.find_items_from_classes((WordListPanel)):
            if item.are_corrections_expanded():
                item.expand_corrections(expand)
                self.redraw([item])

    def _goto_first_prediction(self):
        for item in self.find_items_from_classes((WordListPanel)):
            item.goto_first_prediction()

    def get_prediction_choice(self, wordlist, choice_index):
        """
        wordlist:     WordListPanel that choice_index belongs to
        choice_index: index of a visible prediction key
        """
        if wordlist:
            index = choice_index + wordlist.get_first_prediction_index()
            if index >= 0 and index < len(self._prediction_choices):
                return self._prediction_choices[index]
        return ""

    def get_prediction_choice_and_history(self, wordlist, choice_index):
        word = self.get_prediction_choice(wordlist, choice_index)
        context = self.text_context.get_bot_context()
        tokens, spans = self._wpengine.tokenize_context(context)
        history = tokens[:-1]
        return word, history

    def remove_prediction_context(self, context):
        if self._wpengine:
            self._wpengine.remove_context(context)

    def _insert_correction_choice(self, key, choice_index):
        """ spelling correction clicked """
        span = self._correction_span  # span to correct
        with self.suppress_modifiers():
            self._delete_selected_text()
            self.replace_text(span.begin(), span.end(),
                              self.text_context.get_span_at_caret().begin(),
                              self._correction_choices[choice_index])

    def _insert_prediction_choice(self, key, choice_index, allow_separator):
        """ prediction choice clicked """
        choice = self.get_prediction_choice(key.get_parent(), choice_index)
        deletion, insertion = \
            self._get_prediction_choice_changes(choice)

        # Get the auto separator
        separator = ""
        simulated_result = None
        if allow_separator:
            # Simulate the change and determine the final text
            # before the caret.
            caret_span = self.text_context.get_span_at_caret()
            simulated_result = \
                self._simulate_insertion(caret_span, deletion, insertion)

            domain = self.get_text_domain()
            separator = \
                domain.get_auto_separator(simulated_result.get_text())

        # Type remainder/replace word and possibly add separator.
        with self.suppress_modifiers():
            if config.wp.delayed_word_separators_enabled:
                self._replace_text_at_caret(deletion, insertion)
            else:
                # Insert separator now, so we suppress_modifiers only once.
                self._replace_text_at_caret(deletion, insertion, separator)
                if separator:
                    self.set_last_typed_was_separator(True)

        # Separator becomes the new pending separator
        separator_span = TextSpan(simulated_result.begin(),
                                  len(separator), separator,
                                  simulated_result.begin()) \
            if separator else None

        self._punctuator.set_separator(separator_span)

    def _replace_text_at_caret(self, deletion, insertion, auto_separator=""):
        """
        Insert a word/word-remainder and add a separator string as needed.
        """
        delete_existing_separator = False

        # Check if we have to delete an existing separator
        selection_span = self.text_context.get_selection_span()
        if auto_separator:
            remaining_text = selection_span.get_text_after_span()
            remaining_line = self.text_context.get_line_past_caret()

            # Insert separator if the separator does not exist at
            # the caret yet. For space characters also check if the
            # caret is at the end of the line. The end of the line
            # in the terminal (e.g. in vim) may mean lots of spaces
            # until the final new line.
            delete_existing_separator = True
            if not remaining_text.startswith(auto_separator) or \
               auto_separator.isspace() and remaining_line.isspace():
                delete_existing_separator = False

        if insertion:
            self._delete_selected_text()
            self.replace_text(selection_span.begin() - len(deletion),
                              selection_span.begin(),
                              selection_span.begin(),
                              insertion)
        if auto_separator:
            self.get_text_changer().insert_string_at_caret(auto_separator)
            if delete_existing_separator:
                pos = selection_span.begin() - len(deletion) + \
                    len(insertion) + len(auto_separator)
                self.replace_text(pos,
                                  pos + len(auto_separator),
                                  pos,
                                  "")  # delete, replace with nothing

    def _update_pending_separator_popup(self):
        if config.xid_mode:
            return

        show = False
        rect = None

        span = self.text_context.get_pending_separator()
        if span:
            offset = span.begin() - 1
            if offset >= 0:
                rect = self.text_context.get_character_extents(offset)
                if rect:
                    rect.x += rect.w
                    # Set width to a compromise between narrowest, e.g. "i",
                    # and widest, e.g. "M", proportional characters.
                    # This still does OK for fixed Terminal fonts.
                    rect.w = max(min(rect.w, rect.h / 2), rect.h / 3)
                    if not rect.is_empty():
                        show = True

        if show:
            view = self.get_main_view()
            if view:
                self._pending_separator_popup.show_at(view, rect)
        else:
            self.hide_pending_separator_popup()

        return False

    def hide_pending_separator_popup(self):
        if self._pending_separator_popup.is_visible():
            self._pending_separator_popup.hide()

    def _simulate_insertion(self, caret_span, deletion, insertion):
        """
        Return the context up to caret_span with deletion
        and insertion applied.

        Doctests:
        >>> ws = WordSuggestions()
        >>> def test(caret_span, deletion, insertion):
        ...     span = ws._simulate_insertion(caret_span, deletion, insertion)
        ...     return span.begin(), span.text, span.text_begin()
        >>> test(TextSpan(0, 0, ""), "", "")
        (0, '', 0)
        >>> test(TextSpan(0, 0, ""), "", "local")
        (5, 'local', 0)
        >>> test(TextSpan(0, 0, ""), "log", "")
        (0, '', 0)
        >>> test(TextSpan(8, 0, "", 5), "log", "")
        (5, '', 5)
        >>> test(TextSpan(3, 0, ""), "log", "local")
        (5, 'local', 0)
        >>> test(TextSpan(6, 0, "/var/l"), "", "og")
        (8, '/var/log', 0)
        >>> test(TextSpan(8, 0, "/var/log"), "log", "local")
        (10, '/var/local', 0)
        >>> test(TextSpan(8, 0, "/var/log"), "log", "")
        (5, '/var/', 0)
        """
        context = caret_span.get_text_until_span()
        if deletion:
            context = context[:-len(deletion)]
        context += insertion
        text_pos = caret_span.text_begin()
        return TextSpan(text_pos + len(context), 0, context, text_pos)

    def _update_correction_choices(self):
        self._correction_choices = []
        self._correction_span = None

        if self._spell_checker and \
           config.are_spelling_suggestions_enabled():
            caret_span = self.text_context.get_span_at_caret()
            if caret_span:
                word_span = self._get_word_to_spell_check(caret_span,
                                                          self.is_typing())
                if word_span:
                    (self._correction_choices,
                     self._correction_span,
                     auto_capitalization) = \
                        self._find_correction_choices(word_span, False)

    def _find_correction_choices(self, word_span, auto_capitalize):
        """
        Find spelling suggestions for the word at or before the caret.

        Doctests:
        >>> ws = WordSuggestions()
        >>> f = ws._find_correction_choices
        >>> ws._spell_checker.set_backend(0)
        >>> ws._spell_checker.set_dict_ids(["en_US"])
        True

        # auto-capitalization
        >>> f(TextSpan(0, 7, "Jupiter"), True)
        ([], None, None)
        >>> f(TextSpan(0, 7, "jupiter"), True)             # doctest: +ELLIPSIS
        (['Jupiter', ...], TextSpan(0, 7, 'jupiter', 0, None), 'Jupiter')
        >>> f(TextSpan(0, 7, "jupiter-orbit"), True)       # doctest: +ELLIPSIS
        (['Jupiter', ...], TextSpan(0, 7, 'jupiter', 0, None), 'Jupiter')
        >>> f(TextSpan(0, 3, "usa"), True)                 # doctest: +ELLIPSIS
        (['USA', ...], TextSpan(0, 3, 'usa', 0, None), 'USA')
        >>> f(TextSpan(0, 3, "Usa"), True)                 # doctest: +ELLIPSIS
        (['USA', ...], TextSpan(0, 3, 'Usa', 0, None), 'USA')
        >>> f(TextSpan(0, 13, "ubuntu-system"), True)      # doctest: +ELLIPSIS
        ([...], TextSpan(0, 13, 'ubuntu-system', 0, None), 'Ubuntu-system')
        """
        correction_choices = []
        correction_span = None
        auto_capitalization = None

        text_begin = word_span.text_begin()
        word = word_span.get_span_text()
        caret = self.text_context.get_caret()
        offset = caret - text_begin  # caret offset into the word

        span, choices = \
            self._spell_checker.find_corrections(word, offset)
        if choices:
            correction_choices = choices
            correction_span = TextSpan(span[0] + text_begin,
                                       span[1] - span[0],
                                       span[2],
                                       span[0] + text_begin)

            # See if there is a valid upper caps variant for
            # auto-capitalization.
            if auto_capitalize:
                choice = correction_choices[0]
                if word.upper() == choice.upper():
                    auto_capitalization = choice

                elif word and word[0].islower():
                    word_caps = word.capitalize()
                    span, choices = \
                        self._spell_checker.find_corrections(word_caps, offset)
                    if not choices:
                        auto_capitalization = word_caps
                        correction_span = word_span

        return correction_choices, correction_span, auto_capitalization

    def _update_prediction_choices(self):
        """ word prediction: find choices, only once per key press """
        self._prediction_choices = []
        text_context = self.text_context

        if self._wpengine:

            context = text_context.get_context()
            if context:  # don't load models on startup
                bot_marker = text_context.get_text_begin_marker()
                bot_context = text_context.get_pending_bot_context()

                tokens, spans = self._wpengine.tokenize_context(bot_context)

                (case_insensitive_mode, ignore_non_caps,
                 capitalize, drop_capitalized) = \
                    self._get_prediction_options(tokens,
                                                 bool(self.mods[1]),
                                                 bot_marker)

                _choices = self._wpengine.predict(
                    bot_context,
                    config.wp.max_word_choices * 8,
                    case_insensitive=case_insensitive_mode == 1,
                    case_insensitive_smart=case_insensitive_mode == 2,
                    accent_insensitive_smart=config.wp.accent_insensitive,
                    ignore_non_capitalized=ignore_non_caps)

                choices = []
                for choice in _choices:
                    # Filter out begin of text markers that sneak in as
                    # high frequency unigrams.
                    if choice.startswith("<bot:"):
                        continue

                    # Drop upper caps spelling in favor of a lower caps one.
                    # Auto-capitalization may elect to upper caps on insertion.
                    if drop_capitalized:
                        choice_lower = choice.lower()
                        if choice != choice_lower and \
                           self._wpengine.word_exists(choice_lower):
                            continue

                    choices.append(choice)

                # Make all words start upper case
                if capitalize:
                    choices = self._capitalize_choices(choices)
            else:
                choices = []

            self._prediction_choices = choices

            # update word information for the input line display
            # self.word_infos = \
            #    self.get_word_infos(self.text_context.get_line())

    @staticmethod
    def _get_prediction_options(tokens, shift, bot_marker=None):
        """
        Determine prediction options.

        Doctests:
        >>> get_options = WordSuggestions._get_prediction_options

        # mid-sentence, no tokens
        >>> get_options([], False)
        (0, False, False, False)

        # mid-sentence, lowercase
        >>> get_options(["<s>", "Word", "prefix"], False)
        (2, False, False, False)

        # mid-sentence, capitalized
        >>> get_options(["<s>", "Word", "Prefix"], False)
        (0, True, False, False)

        # mid-sentence, capitals in word (Irish, e.g. bPoblacht)
        # Don't drop caps words, smart case-insensitive search already dropped
        # the lower caps variant.
        >>> get_options(["<s>", "Word", "pRefix"], False)
        (2, False, False, False)

        # mid-sentence, empty prefix
        >>> get_options(["<s>", "Word", ""], False)
        (2, False, False, False)

        # mid-sentence, empty prefix, with shift
        >>> get_options(["<s>", "Word", ""], True)
        (0, True, False, False)

        # sentence begin, lowercase
        >>> get_options(["<s>", "prefix"], False)
        (2, False, False, False)

        # sentence begin, capitalized
        >>> get_options(["<s>", "Prefix"], False)
        (1, False, True, False)

        # sentence begin, empty prefix
        >>> get_options(["<s>", ""], False)
        (2, False, False, False)

        # sentence begin, empty prefix, with shift
        >>> get_options(["<s>", ""], True)
        (1, False, True, False)

        # sentence begin at begin of text.
        >>> get_options(["<bot:txt>", "Prefix"], True, "<bot:txt>")
        (1, False, True, False)
        """
        if tokens:
            prefix = tokens[-1]
            sentence_begin = (len(tokens) >= 2 and
                              (tokens[-2] == "<s>" or
                               bool(bot_marker) and tokens[-2] == bot_marker))
            capitalized = bool(prefix) and prefix[0].isupper()
            empty_prefix = not bool(prefix)

            ignore_non_caps  = (not sentence_begin and
                                (capitalized or empty_prefix and shift))

            capitalize       = sentence_begin and (capitalized or shift)

            case_insensitive = sentence_begin or not (capitalized or shift)
            if not case_insensitive:
                case_insensitive_mode = 0      # case sensitive
            else:
                if capitalize:
                    case_insensitive_mode = 1  # simple
                else:
                    case_insensitive_mode = 2  # smart

        else:
            case_insensitive_mode = 0
            ignore_non_caps  = False
            capitalize = False

        drop_capitalized = False

        return (case_insensitive_mode, ignore_non_caps,
                capitalize, drop_capitalized)

    def get_word_infos(self, text):
        wis = []
        if text.rstrip():  # don't load models on startup
            tokspans, counts = self._wpengine.lookup_text(text)
            for i, t in enumerate(tokspans):
                start, end, token = t
                word = text[start:end]
                wi = WordInfo(start, end, word)
                wi.exact_match   = any(count == 1 for count in counts[i])
                wi.partial_match = any(count  < 0 for count in counts[i])
                wi.ignored       = word != token
                if self._spell_checker:
                    wi.spelling_errors = \
                        self._spell_checker.find_incorrect_spans(word)
                wis.append(wi)

        return wis

    @staticmethod
    def _capitalize_choices(choices):
        """
        Set first letters to upper case and remove
        double entries created that way.

        Doctests:
        >>> WordSuggestions._capitalize_choices(
        ...     ["word1", "Word1", "Word2", "word3"])
        ['Word1', 'Word2', 'Word3']
        >>> WordSuggestions._capitalize_choices(
        ...     ["Word", "woRD", "WORD"])
        ['Word', 'WoRD', 'WORD']
        """
        results = []
        seen = set()

        for choice in choices:
            if choice:
                choice = choice[:1].upper() + choice[1:]
                if choice not in seen:
                    results.append(choice)
                    seen.add(choice)
        return results

    def _get_prediction_choice_changes(self, choice):
        """
        Determines the text changes necessary when inserting
        the prediction choice at choice_index.
        """
        deletion = ""
        insertion = ""
        if self._wpengine:
            context = self.text_context.get_context()
            word_prefix = self._wpengine.get_last_context_fragment(context)
            remainder = choice[len(word_prefix):]

            # no case change?
            if word_prefix + remainder == choice:
                insertion = remainder

                # Force update of word suggestions, even if there is
                # no change necessary. Else, with lazy word separators,
                # predictions won't take the pending separator into account.
                if not deletion and \
                   not insertion:
                    deletion = choice[-1]
                    insertion = deletion
            else:
                deletion = word_prefix
                insertion = choice

        return deletion, insertion

    def _get_word_to_spell_check(self, caret_span, is_typing):
        """
        Get the word to be spell-checked.

        Doctests:
        >>> from Onboard.TextDomain import DomainGenericText
        >>> wp = WordSuggestions()
        >>> wp._wpengine = WPLocalEngine()
        >>> d = DomainGenericText()
        >>> wp.get_text_domain = lambda : d

        # not typing and caret at word begin: return word at caret
        >>> span = TextSpan(9, 0, "binomial proportion")
        >>> print(wp._get_word_to_spell_check(span, False))
        TextSpan(9, 10, 'proportion', 9, None)

        # not typing and caret before word end: return word at caret
        >>> span = TextSpan(7, 0, "binomial proportion")
        >>> print(wp._get_word_to_spell_check(span, False))
        TextSpan(0, 8, 'binomial', 0, None)

        # not typing and caret at word end: return word before caret
        >>> span = TextSpan(8, 0, "binomial proportion")
        >>> print(wp._get_word_to_spell_check(span, False))
        TextSpan(0, 8, 'binomial', 0, None)

        # not typing and caret in URLs or filenames: no spelling suggestions
        >>> span = TextSpan(57, 0,
        ...  "abc http://user:pass@www.do-mai_n.nl/path/name.ext/?p=1#anchor ")
        >>> print(wp._get_word_to_spell_check(span, False))
        None
        >>> span = TextSpan(2, 0, "http://www.domain.org")
        >>> print(wp._get_word_to_spell_check(span, False))
        None

        # typing and caret at word end: suppress spelling suggestions
        >>> span = TextSpan(8, 0, "binomial proportion")
        >>> print(wp._get_word_to_spell_check(span, True))
        None
        """
        section_span = self._get_section_before_span(caret_span)
        if section_span and \
           self._can_spell_check(section_span):
            word_span = self._get_word_before_span(caret_span)

            # Don't pop up spelling corrections if we're
            # currently typing the word.
            caret = caret_span.begin()
            if is_typing and \
               word_span and \
               word_span.end() == caret:
                word_span = None

            return word_span
        return None

    def _delete_selected_text(self):
        """
        Delete the current selection.
        """
        if self.text_context.can_insert_text():
            # delete any selected text first
            selection_span = self.text_context.get_selection_span()
            if not selection_span.is_empty():
                self.text_context.delete_text(selection_span.pos,
                                              selection_span.length)
        else:
            # keystrokes delete the selection on their own.
            pass

    def replace_text(self, begin, end, caret, new_text):
        """
        Replace text from <begin> to <end> with <new_text>,
        """
        if self.text_context.can_insert_text():
            self._replace_text_direct(begin, end, caret, new_text)
        else:
            self._replace_text_key_strokes(begin, end, caret, new_text)

    def _replace_text_direct(self, begin, end, caret, new_text):
        """
        Replace text from <begin> to <end> with <new_text>,
        """
        if end > begin:
            self.text_context.delete_text(begin, end - begin)
        self.text_context.insert_text(begin, new_text)

    def _replace_text_key_strokes(self, begin, end, caret, new_text):
        """
        Replace text from <begin> to <end> with <new_text>,
        Fall-back for text entries without support for direct text insertion.
        """

        with self.suppress_modifiers():
            length = end - begin
            offset = caret - end  # offset of caret to word end

            # delete the old word
            if offset >= 0:
                self.text_changer_key_stroke.press_keysyms("left", offset)
                self.text_changer_key_stroke.press_keysyms("backspace", length)
            else:
                self.text_changer_key_stroke.press_keysyms(
                    "delete", abs(offset))
                self.text_changer_key_stroke.press_keysyms(
                    "backspace", length - abs(offset))

            # insert the new word
            self.text_changer_key_stroke.press_key_string(new_text)

            # move caret back
            if offset >= 0:
                self.text_changer_key_stroke.press_keysyms("right", offset)

    def on_text_entry_deactivated(self):
        """ The current accessible lost focus. """
        self.commit_changes()

        # deactivate the pause button
        if config.word_suggestions.get_pause_learning() == 1:  # not locked?
            config.word_suggestions.set_pause_learning(0)

        self.text_context.set_pending_separator(None)

        self.text_changer_direct_insert.stop_auto_repeat()

    def on_text_entry_activated(self):
        """ A different target widget has been focused """
        self._learn_strategy.on_text_entry_activated()

    def on_text_inserted(self, insertion_span, caret_offset):
        """ Synchronous callback for text insertion """
        # auto-correction
        if config.is_auto_capitalization_enabled():
            word_span = self._get_word_to_auto_correct(insertion_span)
            if word_span:
                self._auto_correct_at(word_span, caret_offset)

    def on_text_context_changed(self, change_detected):
        """
        Asynchronous callback for generic context changes.
        The text of the target widget changed or the caret moved.
        Use this for low priority display stuff.
        """
        if change_detected:
            self.expand_corrections(False)
            self._goto_first_prediction()
            self.invalidate_context_ui()
            self.commit_ui_updates()
            self._learn_strategy.on_text_context_changed()

    def on_text_caret_moved(self):
        """ Caret position changed. """
        pass

    def has_changes(self):
        """ Are there any text changes to learn? """
        return (self.text_context and
                self.text_context.has_changes())

    def commit_changes(self):
        """ Learn all accumulated changes and clear them """
        has_changes = self.has_changes()

        _logger.debug("commit_changes(): has_changes={}"
                      .format(has_changes))

        if has_changes:
            self._learn_strategy.commit_changes()

        self._clear_changes()  # clear input line too

    def discard_changes(self):
        """
        Discard all changes that have accumulated, don't learn them.
        """
        _logger.info("discarding changes")
        self._learn_strategy.discard_changes()
        self._clear_changes()

    def _clear_changes(self):
        """
        Reset all contexts and clear all changes.
        """
        self.atspi_text_context.clear_changes()
        self.atspi_text_context.reset()
        self.input_line.reset()

        # Clear the spell checker cache, new words may have
        # been added from somewhere.
        if self._spell_checker:
            self._spell_checker.invalidate_query_cache()

        self.set_last_typed_was_separator(False)

    def update_inputline(self):
        """ Refresh the GUI displaying the current line's content """
        keys_to_redraw = []
        layout = self.layout  # may be None on exit
        if layout and self._wpengine:
            for key in self.get_text_displays():
                if not config.word_suggestions.show_context_line or \
                   self._hide_input_line:
                    if key.visible:
                        layout.set_item_visible(key, False)
                        keys_to_redraw.append(key)
                else:
                    line = self.text_context.get_line()
                    if line:
                        key.raise_to_top()
                        layout.set_item_visible(key, True)
                    else:
                        line = ""
                        layout.set_item_visible(key, False)

                    key.set_content(line, self.word_infos,
                                    self.text_context.get_line_caret_pos())
                    keys_to_redraw.append(key)

        return keys_to_redraw

    def hide_input_line(self, hide=True):
        """
        Temporarily hide the input line to access keys below it.
        """
        if self._hide_input_line != hide:
            self._hide_input_line = hide
            self.redraw(self.update_inputline())

    def show_input_line_on_key_release(self, key):
        if self._hide_input_line and \
           not self._key_intersects_input_line(key):
            self._hide_input_line = False

    def _key_intersects_input_line(self, key):
        """ Check if key rect is intersecting the input line. """
        for item in self.get_text_displays():
            if item.get_border_rect().intersects(key.get_border_rect()):
                return True
        return False

    def get_text_domain(self):
        return self.text_context.get_text_domain()

    def can_give_keypress_feedback(self):
        """ Password entries may block feedback to prevent eavesdropping. """
        domain = self.get_text_domain()
        if domain and not domain.can_give_keypress_feedback():
            return False
        return True

    def _can_spell_check(self, span_to_check):
        """ No spell checking for passwords, URLs, etc. """
        domain = self.get_text_domain()
        if domain and domain.can_spell_check(span_to_check):
            return True
        return False

    def _can_auto_correct(self, section_span):
        """ No auto correct for passwords, URLs, etc. """
        domain = self.get_text_domain()
        if domain and domain.can_auto_correct(section_span):
            return True
        return False

    def _can_auto_punctuate(self):
        """ No auto-punctuation in terminal when prompt was detected. """
        return self.text_context.can_auto_punctuate()

    def on_focusable_gui_opening(self):
        """
        Turn off AT-SPI listeners while there is a dialog open.
        Onboard and occasionally the whole desktop tend to lock up otherwise.
        Call this before dialog/popop menus are opened by Onboard itself.
        """
        if self._focusable_count == 0:
            AtspiStateTracker().freeze()

            # disable keep_windows_on_top
            app = self.get_application()
            if app:
                app.on_focusable_gui_opening()

            self.stop_raise_attempts()

        self._focusable_count += 1

    def on_focusable_gui_closed(self):
        """
        Call this after dialogs/menus have been closed.
        """
        self._focusable_count -= 1
        if self._focusable_count == 0:
            # Re-enable AT-SPI listeners
            AtspiStateTracker().thaw()

            # re-enable keep_windows_on_top
            app = self.get_application()
            if app:
                app.on_focusable_gui_closed()

    def has_focusable_gui(self):
        return self._focusable_count > 0

    def _auto_correct_at(self, word_span, caret_offset):
        """
        Auto-capitalize/correct a word_span.
        """
        correction_span, replacement = \
            self._find_auto_correction(
                word_span, True, config.typing_assistance.auto_correction)
        if replacement:
            with self.suppress_modifiers():
                self.replace_text(correction_span.begin(),
                                  correction_span.end(),
                                  caret_offset,
                                  replacement)

    def _find_auto_correction(self, word_span, auto_capitalize, auto_correct):
        """
        Doctests:
        >>> wp = WordSuggestions()
        >>> wp._spell_checker.set_backend(0)
        >>> wp._spell_checker.set_dict_ids(["en_US"])
        True

        # auto-capitalization
        >>> wp._find_auto_correction(
        ...     TextSpan(0, 7, "jupiter ..."), True, False)
        (TextSpan(0, 7, 'jupiter', 0, None), 'Jupiter')
        >>> wp._find_auto_correction(TextSpan(0, 3, "usa ..."), True, False)
        (TextSpan(0, 3, 'usa', 0, None), 'USA')
        >>> wp._find_auto_correction(TextSpan(2, 3, "  Usa ..."), True, False)
        (TextSpan(0, 3, 'Usa', 0, None), 'USA')
        """

        (correction_choices,
         correction_span,
         auto_capitalization) = \
            self._find_correction_choices(word_span, auto_capitalize)

        replacement = auto_capitalization
        if not replacement and \
           correction_choices and \
           auto_correct:

            min_auto_correct_length = 2
            max_string_distance = 2

            word = word_span.get_span_text()
            if len(word) > min_auto_correct_length:
                choice = correction_choices[0]  # rely on spell checker for now
                distance = self._string_distance(word, choice)
                if distance <= max_string_distance:
                    replacement = choice

        return correction_span, replacement

    def _get_word_to_auto_correct(self, insertion_span):
        """
        Doctests:
        >>> from Onboard.TextDomain import DomainGenericText
        >>> wp = WordSuggestions()
        >>> wp._wpengine = WPLocalEngine()
        >>> d = DomainGenericText()
        >>> wp.get_text_domain = lambda : d

        >>> print(wp._get_word_to_auto_correct(TextSpan(6, 1, "abc def")))
        None

        >>> print(wp._get_word_to_auto_correct(TextSpan(7, 1, "abc def ")))
        TextSpan(4, 3, 'def', 4, None)

        >>> print(wp._get_word_to_auto_correct(TextSpan(7, 1, "abc def.")))
        None

        >>> print(wp._get_word_to_auto_correct(TextSpan(8, 1, "abc def. ")))
        TextSpan(4, 3, 'def', 4, None)

        >>> print(wp._get_word_to_auto_correct(TextSpan(56, 6,
        ...     "abc http://user:pass@www.do-mai_n.nl/path/name.ext/"
        ...     "?p=1#anchor ")))
        None
        """
        char = insertion_span.get_last_char_in_span()
        if char.isspace():
            section_span = self._get_section_before_span(insertion_span)
            if section_span and \
               self._can_auto_correct(section_span):
                word_span = self._get_word_before_span(insertion_span)
                return word_span
        return None

    _section_begin_pattern = re.compile(r"\S*\s*$")
    _section_end_pattern = re.compile(r"\S*(?=\s*)")

    def _get_section_before_span(self, insertion_span):
        """
        Get first whitespace-separated section that
        begins before the given span.

        Doctests:
        >>> wp = WordSuggestions()

        # If input span intersects a word anywhere, return the whole word
        >>> wp._get_section_before_span(TextSpan(6, 1, "abc def"))
        TextSpan(4, 3, 'def', 0, None)

        # If input span lies just past a word, return the whole word
        >>> wp._get_section_before_span(TextSpan(7, 1, "abc def "))
        TextSpan(4, 3, 'def', 0, None)

        # If input span intersects a URL anywhere, return the whole URL
        >>> wp._get_section_before_span(TextSpan(56, 6,
        ...     "abc http://user:pass@www.do-mai_n.nl/path/name.ext/"
        ...     "?p=1#anchor"))
        TextSpan(4, 58, 'http://user:pass@www.do-mai_n.nl/path/name.ext/\
?p=1#anchor', 0, None)

        # If input span intersects a URL anywhere, return the whole URL
        >>> wp._get_section_before_span(TextSpan(6, 2,
        ...     "abc http://www.domain.org"))
        TextSpan(4, 21, 'http://www.domain.org', 0, None)

        # Still return section before span if text_pos != 0
        >>> wp._get_section_before_span(TextSpan(10+32, 1,
        ...     "http://www.domain.org/index.html ", 10))
        TextSpan(10, 32, 'http://www.domain.org/index.html', 10, None)
        """
        text = insertion_span.get_text_until_span()
        match = self._section_begin_pattern.search(text)
        if match:
            begin = match.start()
            text = insertion_span.text[begin:]
            match = self._section_end_pattern.search(text)
            if match:
                span = insertion_span.copy()
                span.length = match.end() - match.start()
                span.pos = span.text_pos + begin + match.start()
                return span
        return None

    def _get_word_before_span(self, span):
        """
        Get the word at or before the span.

        Doctests:
        >>> wp = WordSuggestions()
        >>> wp._wpengine = WPLocalEngine()

        # caret right in the middle of a word
        >>> wp._get_word_before_span(
        ...     TextSpan(15, 0, "binomial proportion"))
        TextSpan(9, 10, 'proportion', 9, None)

        # text at offset
        >>> wp._get_word_before_span(
        ...     TextSpan(25, 0, "binomial proportion", 10))
        TextSpan(19, 10, 'proportion', 19, None)

        # caret after whitespace - get the previous word
        >>> wp._get_word_before_span(
        ...     TextSpan(9, 0, "binomial  proportion"))
        TextSpan(0, 8, 'binomial', 0, None)
        """
        word_span = None
        if span and self._wpengine:
            tokens, spans = self._wpengine.tokenize_text(span.get_text())

            text_begin = span.text_begin()
            local_caret = span.begin() - text_begin

            itoken = None
            for i, s in enumerate(spans):
                if s[0] > local_caret:
                    break
                itoken = i

            if itoken is not None:
                token = unicode_str(tokens[itoken])

                # We're only looking for actual words
                if token not in ["<unk>", "<num>", "<s>"]:
                    b = spans[itoken][0] + text_begin
                    e = spans[itoken][1] + text_begin
                    word_span = TextSpan(b, e - b, token, b)

        return word_span

    @staticmethod
    def _string_distance(s1, s2):
        """ Levenshtein string distance """
        len1 = len(s1)
        len2 = len(s2)
        last_row = None
        row = list(range(1, len2 + 1)) + [0]
        for ri in range(len1):
            last_row = row
            row = [0] * len2 + [ri + 1]
            for ci in range(len2):
                d = int(s1[ri] != s2[ci])
                row[ci] = min(row[ci - 1] + 1,
                              last_row[ci] + 1,
                              last_row[ci - 1] + d)
        return row[len2 - 1]


class LearnStrategy:
    """
    Base class of learn strategies.
    """

    def __init__(self, tokenize=None):
        self._tokenize = tokenize \
            if tokenize else pypredict.tokenize_text  # no D-Bus for tests

    def _learn_spans(self, spans, bot_marker="", bot_offset=None,
                     text_domain=None):

        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("LearnStrategy._learn_spans("
                          "bot_marker={}, bot_offset={}, text_domain={}): "
                          "can_auto_learn={} ({})"
                          .format(bot_marker, bot_offset, text_domain,
                                  config.wp.can_auto_learn(),
                                  config.wp.get_can_auto_learn_debug_string()))

        if config.wp.can_auto_learn():
            texts = self._get_learn_texts(spans, bot_marker, bot_offset,
                                          text_domain)

            # print("learning " + repr(texts))

            engine = self._wp._wpengine
            for text in texts:
                engine.learn_text(
                    text, config.word_suggestions.can_learn_new_words())

    def _learn_scratch_spans(self, spans, text_domain=None):
        if config.wp.can_auto_learn():
            engine = self._wp._wpengine
            engine.clear_scratch_models()
            if spans:
                texts = self._get_learn_texts(spans, None, None, text_domain)

                for text in texts:
                    engine.learn_scratch_text(text)

    def _get_learn_texts(self, spans, bot_marker="", bot_offset=None,
                         text_domain=None):
        token_sets = self._get_learn_tokens(spans, bot_marker, bot_offset,
                                            text_domain)
        return [" ".join(tokens) for tokens in token_sets]

    def _get_learn_tokens(self, text_spans,
                          bot_marker="", bot_offset=None,
                          text_domain=None):
        """
        Get disjoint sets of tokens to learn.
        Tokens of overlapping or adjacent spans are joined.
        Expected overlap is at most one word at begin and end.

        Doctests:
        >>> from Onboard.TextDomain import DomainGenericText
        >>> p = LearnStrategy()
        >>> d = DomainGenericText()

        # single span
        >>> p._get_learn_tokens([TextSpan(14, 2, "word1 word2 word3")])
        [['word3']]

        # multiple distinct spans
        >>> p._get_learn_tokens([TextSpan( 3, 1, "word1 word2 word3"),
        ...                      TextSpan(14, 2, "word1 word2 word3")])
        [['word1'], ['word3']]

        # multiple joined spans
        >>> p._get_learn_tokens([TextSpan( 3, 4, "word1 word2 word3"),
        ...                      TextSpan(10, 1, "word1 word2 word3"),
        ...                      TextSpan(14, 2, "word1 word2 word3")])
        [['word1', 'word2', 'word3']]

        # single span with preceding sentence begin
        >>> p._get_learn_tokens([TextSpan(9, 2, "word1. word2 word3")])
        [['<s>', 'word2']]

        # Multiple joined spans across sentence marker.
        >>> p._get_learn_tokens([TextSpan(2, 2, "word1. word2 word3"),
        ...                      TextSpan(9, 2, "word1. word2 word3")])
        [['word1', '<s>', 'word2']]

        # Begin of text marker, generic text.
        >>> p._get_learn_tokens(
        ...     [TextSpan(2, 2, "word1 word2")], "<bot:txt>", 0)
        [['<bot:txt>', 'word1']]
        >>> p._get_learn_tokens(
        ...     [TextSpan(8, 2, "word1 word2")], "<bot:txt>", 0)
        [['word2']]

        # Begin of text marker, terminal.
        >>> p._get_learn_tokens([TextSpan(9, 2, "prompt$ word1 word2")],
        ...                               "<bot:term>", 8)
        [['<bot:term>', 'word1']]
        >>> p._get_learn_tokens([TextSpan(11, 2, "prompt$   word1 word2")],
        ...                               "<bot:term>", 8)
        [['<bot:term>', 'word1']]
        >>> p._get_learn_tokens([TextSpan(13, 2, "prompt$ 123 word1 word2")],
        ...                               "<bot:term>", 8)
        [['word1']]
        >>> p._get_learn_tokens([TextSpan(15, 2, "prompt$ word1 word2")],
        ...                               "<bot:term>", 8)
        [['word2']]

        # The hierarichcal part of URLs is considered changed as a whole
        >>> p._get_learn_tokens(
        ...     [TextSpan(14, 1, "http://www.domain.org/home/index.html")],
        ...                          None, None, d)
        [['http', 'www', 'domain', 'org', 'home', 'index', 'html']]

        # Recognize URLs even without the scheme part
        >>> p._get_learn_tokens([TextSpan(8, 1, "www.domain.org")],
        ...                          None, None, d)
        [['www', 'domain', 'org']]

        # The query part of an URL isn't automatically included.
        >>> p._get_learn_tokens(
        ...     [TextSpan(14, 1, "http://www.domain.org/?p=1")],
        ...                          None, None, d)
        [['http', 'www', 'domain', 'org']]

        # The fragment part of an URL isn't automatically included.
        >>> p._get_learn_tokens(
        ...     [TextSpan(14, 1, "http://www.domain.org/#anchor")],
        ...                          None, None, d)
        [['http', 'www', 'domain', 'org']]

        # File paths are considered changed as a whole
        >>> p._get_learn_tokens([TextSpan(6, 1, "/usr/bin/onboard")],
        ...                          None, None, d)
        [['usr', 'bin', 'onboard']]
        >>> p._get_learn_tokens([TextSpan(5, 1, "usr/bin/onboard")],
        ...                          None, None, d)
        [['usr', 'bin', 'onboard']]

        # Passwords in URLs must not be learned
        >>> p._get_learn_tokens([TextSpan(0, 11, "user:pass@www.domain.org")],
        ...                          None, None, d)
        [['user', '<unk>', 'www', 'domain', 'org']]
        """
        token_sets = []
        span_sets = []

        if text_domain:
            text_spans = self._grow_spans(text_spans, text_domain)

        text_spans = sorted(text_spans, key=lambda x: (x.begin(), x.end()))

        for text_span in text_spans:
            # Tokenize with one additional token in front so we can
            # spot and join adjacent token sets.
            tokens, spans, span_before = self._tokenize_span(text_span)

            merged = False
            if tokens:
                # Do previous tokens to merge with exist?
                if token_sets:
                    prev_tokens = token_sets[-1]
                    prev_spans  = span_sets[-1]
                    link_span = span_before if span_before else spans[0]
                    for i, prev_span in enumerate(prev_spans):
                        if prev_span == link_span:
                            k = i + 1 if span_before else i
                            token_sets[-1] = prev_tokens[:k] + tokens
                            span_sets[-1]  = prev_spans[:k] + spans
                            merged = True

                # No previous tokens exist, the current ones are the
                # very first tokens at lowest offset.
                else:
                    # prepend begin-of-text marker
                    if bot_marker and \
                       bot_offset is not None:
                        if span_before is None or \
                           span_before[1] < bot_offset <= spans[0][0]:
                            tokens = [bot_marker] + tokens
                            spans = [(-1, -1)] + spans  # dummy span, don't use

            if not merged and \
               len(tokens):
                token_sets.append(tokens)
                span_sets.append(spans)

        return token_sets

    def _tokenize_span(self, text_span, prepend_tokens=0):
        """
        Expand spans to word boundaries and return as tokens.
        Include <prepend_tokens> tokens before the span.

        Doctests:
        >>> import Onboard.TextContext as tc
        >>> p = LearnStrategy()
        >>> p._tokenize_span(tc.TextSpan(0, 1, "word1 word2 word3"))
        (['word1'], [(0, 5)], None)
        >>> p._tokenize_span(tc.TextSpan(16, 1, "word1 word2 word3"))
        (['word3'], [(12, 17)], (6, 11))
        >>> p._tokenize_span(tc.TextSpan(8, 12, "word1 word2 word3"))
        (['word2', 'word3'], [(6, 11), (12, 17)], (0, 5))
        >>> p._tokenize_span(tc.TextSpan(5, 1, "word1 word2 word3"))
        ([], [], None)
        >>> p._tokenize_span(tc.TextSpan(4, 1, "word1 word2 word3"))
        (['word1'], [(0, 5)], None)
        >>> p._tokenize_span(tc.TextSpan(6, 1, "word1 word2 word3"))
        (['word2'], [(6, 11)], (0, 5))

        - text at offset
        >>> p._tokenize_span(tc.TextSpan(108, 1, "word1 word2 word3", 100))
        (['word2'], [(106, 111)], (100, 105))

        - prepend tokens
        >>> p._tokenize_span(tc.TextSpan(13, 1, "word1 word2 word3"), 1)
        (['word2', 'word3'], [(6, 11), (12, 17)], (0, 5))
        >>> p._tokenize_span(tc.TextSpan(1, 1, "word1 word2 word3"), 1)
        (['word1'], [(0, 5)], None)
        """
        offset = text_span.text_begin()
        tokens, spans = self._tokenize(text_span.get_text())

        itokens = self._intersect_span(text_span, tokens, spans)

        if prepend_tokens and itokens:
            first = itokens[0]
            n = min(prepend_tokens, first)
            itokens = list(range(first - n, first)) + itokens
        elif itokens:
            # Always include a preceding sentence marker to link
            # upper case words with it when learning.
            first = itokens[0]
            if first > 0 and \
               tokens[first - 1] == "<s>":
                itokens.insert(0, first - 1)

        # Return an additional span for linking with other token lists:
        # span of the token before the first returned token.
        span_before = None
        if itokens and itokens[0] > 0:
            k = itokens[0] - 1
            span_before = (offset + spans[k][0], offset + spans[k][1])

        return([unicode_str(tokens[i]) for i in itokens],
               [(offset + spans[i][0], offset + spans[i][1]) for i in itokens],
               span_before)

    def _grow_spans(self, spans, text_domain):
        """
        Grow spans to include e.g. whole URLs.
        """
        new_spans = [s.copy() for s in spans]
        modified = False

        for span in new_spans:
            begin, length, _text = text_domain.grow_learning_span(span)
            end = begin + length
            if begin < span.begin() or \
               end > span.end():
                span.pos = begin
                span.length = length
                modified = True

        if modified:
            new_spans, _span = TextChanges.consolidate_spans(new_spans)

        return new_spans

    def _intersect_span(self, text_span, tokens, spans):
        """
        Returns indices of tokens the given text_span touches.
        """
        itokens = []
        offset = text_span.text_begin()
        begin  = text_span.begin() - offset
        end    = text_span.end() - offset
        for i, s in enumerate(spans):
            if begin < s[1] and end > s[0]:  # intersects?
                itokens.append(i)

        return itokens


class LearnStrategyLRU(LearnStrategy):
    """
    Delay learning individual spans of changed text to reduce the
    rate of junk entering the language model.
    """

    LEARN_DELAY  = 60  # seconds from last modification until spans are learned
    POLLING_TIME = 2   # seconds between polling for timed-out spans

    def __init__(self, wp):
        LearnStrategy.__init__(self,
                               lambda text: wp._wpengine.tokenize_text(text))
        self._wp = wp
        self._timer = Timer()

        self._insert_count = 0
        self._delete_count = 0
        self._rate_limiter = CallOnce(500)

    def reset(self):
        self._timer.stop()
        self._rate_limiter.stop()

        self._insert_count = 0
        self._delete_count = 0

    def commit_changes(self):
        """ Learn and remove all changes """
        self.reset()

        text_context = self._wp.text_context
        changes = text_context.get_changes()  # by reference
        spans = changes.get_spans()  # by reference

        _logger.debug("LearnStrategyLRU.commit_changes(): "
                      "changes={} engine={}"
                      .format(changes, self._wp._wpengine))
        if spans:
            engine = self._wp._wpengine
            if engine:
                bot_marker = text_context.get_text_begin_marker()
                bot_offset = text_context.get_begin_of_text_offset()
                domain = self._wp.get_text_domain()
                self._learn_spans(spans, bot_marker, bot_offset, domain)
                engine.clear_scratch_models()  # clear short term memory

        changes.clear()

    def discard_changes(self):
        """ Discard and remove all changes """
        engine = self._wp._wpengine
        if engine:
            engine.clear_scratch_models()  # clear short term memory
        self.reset()

    def commit_expired_changes(self):
        """
        Learn and remove spans that have reached their timeout.
        Keep the most recent span untouched,so it can be
        worked on indefinitely.
        """
        changes = self._wp.text_context.get_changes()
        spans = changes.get_spans()

        # find most recently update span
        most_recent = None
        for span in spans:
            if not most_recent or \
               most_recent.last_modified < span.last_modified:
                most_recent = span

        # learn expired spans
        expired_spans = []
        for span in list(spans):
            if span is not most_recent and \
               time.time() - span.last_modified >= self.LEARN_DELAY:
                expired_spans.append(span)
                changes.remove_span(span)
        self._learn_spans(expired_spans)

        return changes.get_spans()

    def on_text_entry_activated(self):
        pass

    def on_text_context_changed(self):
        changes = self._wp.text_context.get_changes()

        if not changes.is_empty():
            self._handle_timed_learning()
        self._maybe_update_scratch_models()

    def _handle_timed_learning(self):
        changes = self._wp.text_context.get_changes()
        if not changes.is_empty() and \
           not self._timer.is_running():
            # begin polling for text changes to learn every x seconds
            if 0:  # disabled for now
                self._timer.start(self.POLLING_TIME, self._poll_changes)

    def _poll_changes(self):
        remaining_spans = self.commit_expired_changes()
        return len(remaining_spans) != 0

    def _maybe_update_scratch_models(self):
        """
        Update short term memory if the time is right.
        This may be a time consuming task, so we try to limit the
        frequency of updates.
        """
        if self._wp._wpengine:
            text_context = self._wp.text_context
            changes = text_context.get_changes()

            # Whitespace inserted or delete to whitespace?
            inserted = self._insert_count < changes.insert_count
            deleted = self._delete_count < changes.delete_count
            if inserted or deleted:
                # caret inside a word?
                caret_span = text_context.get_span_at_caret()
                if caret_span:
                    char = caret_span.get_char_before_span()
                    in_word = not char.isspace()
                else:
                    in_word = False

                if not in_word:
                    # run now, or guaranteed very soon, but not too often
                    self._rate_limiter.enqueue(self._update_scratch_memory,
                                               deleted)

    def _update_scratch_memory(self, update_ui):
        """
        Update short term memory with changes that haven't been learned yet.
        """
        changes = self._wp.text_context.get_changes()
        spans = changes.get_spans()  # by reference
        self._learn_scratch_spans(spans)
        self._insert_count = changes.insert_count
        self._delete_count = changes.delete_count

        # reflect the model change in the wordlist,
        # e.g. when deleting new words
        if update_ui:
            self._wp.invalidate_context_ui()
            self._wp.commit_ui_updates()


class Punctuator:
    """
    Abstract base class for punctuation assistance.
    """

    punctuation_no_capitalize = [",", ":", ";", ")", "}", "]",
                                 "’",
                                 "”", "»", '›',  # language dependent :/
                                 ]
    punctuation_capitalize    = [".", "?", "!"]
    punctuation = punctuation_capitalize + punctuation_no_capitalize

    def __init__(self, wp):
        self._wp = wp
        self.reset()

    def reset(self):
        pass

    def set_separator(self, separator_span):
        raise NotImplementedError()

    def _handle_key_event(self, key, pending_separator_span, before=True):
        raise NotImplementedError()

    def insert_separator(self, separator_span):
        """ Insert pending separator at the caret. """
        if separator_span:
            string = separator_span.get_span_text()

            # Don't use direct insertion here, because many keys
            # always generate key-strokes and are likely to arrive
            # after the separator.
            self._wp.get_text_changer().insert_string_at_caret(string)
            self.set_last_typed_was_separator(True)

    def insert_separator_at_distance(self, separator_span):
        """
        Insert pending separator even if it is located at some
        distance away from the caret. Caret position will be
        restored afterwards.
        """
        if separator_span:
            string = separator_span.get_span_text()

            caret_span = self._get_span_at_caret()
            if caret_span and \
               (self._wp.text_context.can_insert_text() or
                len(string) < 50):  # abort if too many key-presses necessary
                separator_pos = separator_span.begin()
                self._wp.replace_text(separator_pos,
                                      separator_pos,
                                      caret_span.begin(),
                                      string)

    def _update_text_context(self):
        self._wp.text_context.on_text_context_changed()

    def _delete_at_caret(self):
        self._wp.get_text_changer().delete_at_caret()

    def _get_span_at_caret(self):
        text_context = self._wp.text_context
        return text_context.get_span_at_caret()


class PunctuatorImmediateSeparators(Punctuator):
    """
    Insert word separators immediately (on inserting prediction).
    Mainly adds and removes spaces around punctuation depending on
    the user action immediately after word completion.
    """
    def __init__(self, wp):
        Punctuator.__init__(self, wp)

    def reset(self):
        Punctuator.reset(self)
        self._added_separator_span = None
        self._separator_removed = False

    def set_separator(self, separator_span):
        self._added_separator_span = separator_span

    def on_before_press(self, key):
        """
        Chance to insert separators before key-press.
        """
        caps_mode = False

        # before punctuation remove the separator again
        if self._added_separator_span and \
           key.is_text_changing():
            # Only act if we are still at the caret position
            # where the separator was added.
            caret_span = self._get_span_at_caret()
            if caret_span:
                if caret_span.begin() == \
                   self._added_separator_span.end():
                    char = key.get_label()
                    if char in self.punctuation_no_capitalize:
                        self._delete_at_caret()
                        self._separator_removed = True

                    elif char in self.punctuation_capitalize:
                        self._delete_at_caret()
                        self._separator_removed = True
                        if config.is_auto_capitalization_enabled():
                            caps_mode = True

            self._added_separator_span = None

        if caps_mode and \
           config.is_auto_capitalization_enabled():
            self._wp.request_capitalization(True)

    def on_before_release(self, key):
        """
        Chance to request capitalization before key-release.
        """
        pass

    def on_after_release(self, key):
        """
        Chance to insert separators after key-release.
        """
        # after punctuation re-add the separator (always space)
        if self._separator_removed:
            self._separator_removed = False
            # No direct insertion here. Space must always arrive
            # last, i.e. after the released key was generated.
            self._wp.text_changer_key_stroke.press_keysyms("space")


class PunctuatorDelayedSeparators(Punctuator):
    """
    Insert word separators lazily.
    """

    def __init__(self, wp):
        Punctuator.__init__(self, wp)

    def reset(self):
        Punctuator.reset(self)
        self._key_down_labels = {}

    def set_separator(self, separator_span):
        self.set_pending_separator(separator_span)

    def set_pending_separator(self, separator_span=None):
        """ Remember this separator span for later insertion. """
        self._wp.text_context.set_pending_separator(separator_span)

    def get_pending_separator(self):
        """ Return current pending separator span or None """
        return self._wp.text_context.get_pending_separator()

    def on_before_press(self, key):
        """
        Chance to insert separators before key-press.
        """
        # clear label in case there was no after call
        self._key_down_labels[key] = None

        separator_span = self.get_pending_separator()
        separator_span, insert, caps_mode = \
            self._handle_key_event(key, separator_span, True)
        if insert:
            self.insert_separator(separator_span)
            separator_span = None
        if caps_mode and \
           config.is_auto_capitalization_enabled():
            self._wp.request_capitalization(True)

        self.set_pending_separator(separator_span)

    def on_before_release(self, key):
        """
        Chance to request capitalization before key-release.
        """
        separator_span = self.get_pending_separator()
        separator_span, insert, caps_mode = \
            self._handle_key_event(key, separator_span, False)
        if caps_mode and \
           config.is_auto_capitalization_enabled():
            self._wp.request_capitalization(True)

    def on_after_release(self, key):
        """
        Chance to insert separators after key-release.
        """
        separator_span = self.get_pending_separator()
        separator_span, insert, caps_mode = \
            self._handle_key_event(key, separator_span, False)
        if insert:
            self.insert_separator(separator_span)
            separator_span = None

        self.set_pending_separator(separator_span)
        self._key_down_labels[key] = None

    def _handle_key_event(self, key, pending_separator_span, before=True):
        after = not before
        caps_mode = False

        if pending_separator_span and \
           config.wp.punctuation_assistance:

            insert_now = False

            if key.is_layer_button() or \
               key.is_modifier():
                pass  # do nothing, keep pending separator for the next key

            elif key.is_prediction_key():
                if before:
                    insert_now = True
                elif after:
                    # Separators that aren't space are always inserted
                    # right away. Too confusing otherwise.
                    if pending_separator_span.get_span_text() != " ":
                        insert_now = True

            elif key.is_separator_cancelling():
                pending_separator_span = None

            elif key.is_text_changing():
                caret_span = self._get_span_at_caret()
                if caret_span:
                    char = self._get_key_down_label(key, before)
                    punctuation_no_capitalize = \
                        char in self.punctuation_no_capitalize
                    punctuation_capitalize = \
                        char in self.punctuation_capitalize
                    punctuation = (punctuation_no_capitalize or
                                   punctuation_capitalize)
                    caret_pos = caret_span.begin()
                    separator_pos = pending_separator_span.begin()

                    if punctuation and self._wp._can_auto_punctuate():
                        # Text context often hasn't caught up to recent changes
                        # for delayed stroke keys like "?". Allow
                        # range of caret positions.
                        if after and (caret_pos >= separator_pos and
                                      caret_pos <= separator_pos + 1):
                            # Move span after the punctuation character.
                            # Make copy for fast comparison.
                            pending_separator_span = \
                                pending_separator_span.copy()
                            pending_separator_span.pos += 1
                            pending_separator_span.text_pos += 1

                            if punctuation_capitalize:
                                caps_mode = True
                    else:
                        # Only act if we are still at the caret position
                        # where the separator is to be added.
                        if before and caret_pos == separator_pos:
                            insert_now = True
                        elif after and (caret_pos >= separator_pos and
                                        caret_pos <= separator_pos + 1):
                            insert_now = True

                else:
                    pending_separator_span = None
            else:
                pending_separator_span = None

            if pending_separator_span and \
               insert_now:
                return pending_separator_span, True, caps_mode

        return pending_separator_span, False, caps_mode

    def _get_key_down_label(self, key, before):
        """
        Return the label key had before it was pressed down.
        Keys may change labels when modifiers are activated or released,
        but we need the same label before and after the press here.
        Else ":" turns to "." in Compact and requests capitalization.
        Multiple pressed keys may be in flight, due to multi-touch
        so keep the label per key.
        """
        label = self._key_down_labels.get(key)
        if label is None:
            label = key.get_label()
            self._key_down_labels[key] = label
        return label


class WordInfo:
    """ Word level information about found predictions """

    exact_match = False
    partial_match = False
    ignored = False
    spelling_errors = None

    def __init__(self, start, end, word):
        self.start = start
        self.end   = end
        self.word  = word
        self.spelling_errors = None

    def __str__(self):
        return "'{}' {}-{} unknown={} exact={} partial={} ignored={} " \
               "spelling_errors={}".format(self.word,
                                           self.start, self.end,
                                           self.unknown, self.exact_match,
                                           self.partial_match, self.ignored,
                                           self.spelling_errors)


class WordListPanel(LayoutPanel):
    """ Panel populated with correction and prediction keys at run-time """

    # Place all dynamically created suggestions into their own
    # group to prevent font_size changes forced by unrelated keys.
    SUGGESTIONS_GROUP = "_suggestion_"

    def __init__(self):
        LayoutPanel.__init__(self)
        self._correcions_expanded = False
        self._first_prediction = 0
        self._more_predictions_requested = False
        self._can_goto_previous_predictions = False
        self._can_goto_next_predictions = False
        self._next_predictions_increment = 0
        self._prev_predictions_stack = []

    def get_max_non_expanded_corrections(self):
        return 1

    def expand_corrections(self, expand):
        self._correcions_expanded = expand

    def are_corrections_expanded(self):
        return self._correcions_expanded

    def were_more_predictions_requested(self):
        """ Has the more arrow been clicked at least once? """
        return self._more_predictions_requested

    def get_first_prediction_index(self):
        """ index of first visible prediction """
        return self._first_prediction

    def goto_next_predictions(self):
        """ scroll suggestions to the right """
        self._prev_predictions_stack.append(self._first_prediction)
        self._first_prediction += self._next_predictions_increment
        self._more_predictions_requested = True

    def goto_previous_predictions(self):
        """ scroll suggestions to the left """
        if self._prev_predictions_stack:
            self._first_prediction = self._prev_predictions_stack[-1]
            del self._prev_predictions_stack[-1]

    def goto_first_prediction(self):
        """ reset scroll position """
        self._first_prediction = 0
        self._prev_predictions_stack = []
        self._more_predictions_requested = False

    def can_goto_previous_predictions(self):
        return self._can_goto_previous_predictions

    def can_goto_next_predictions(self):
        return self._can_goto_next_predictions

    def _get_button_width(self, button):
        return button.get_initial_border_rect().w * \
            config.theme_settings.key_size / 100.0

    def _get_button_spacing(self):
        return config.WORDLIST_BUTTON_SPACING[0] * 2 * \
            (2.0 - config.theme_settings.key_size / 100.0)

    def _get_entry_spacing(self):
        return config.WORDLIST_ENTRY_SPACING[0] * 2 * \
            (2.0 - config.theme_settings.key_size / 100.0)

    def create_keys(self, correction_choices, prediction_choices):
        fixed_background = list(self.find_ids(["wordlist",
                                               "prediction",
                                               "correction"]))
        fixed_buttons    = list(self.find_ids(["expand-corrections"]))
        hideable_buttons = list(self.find_ids(["previous-predictions",
                                               "next-predictions",
                                               "pause-learning",
                                               "language",
                                               "hide"]))
        # scroll to current position
        first = self._first_prediction
        max_choices = config.wp.max_word_choices \
            if first == 0 else 100
        # if not self._more_predictions_requested else 100
        first = self._first_prediction
        scrolled_prediction_choices = \
            prediction_choices[first:first + max_choices]

        # hide/show buttons
        visible_button_ids = \
            self._get_visible_buttons(correction_choices, prediction_choices,
                                      scrolled_prediction_choices)
        for button in hideable_buttons:
            button.set_visible(button.get_id() in visible_button_ids)

        # create keys
        correction_keys, prediction_keys = \
            self._create_keys(correction_choices,
                              scrolled_prediction_choices,
                              visible_button_ids)

        # add all keys to the panel
        keys = correction_keys + prediction_keys
        if fixed_buttons:
            color_scheme = fixed_buttons[0].color_scheme
            for key in keys:
                key.color_scheme = color_scheme
        self.set_items(fixed_background + keys +
                       fixed_buttons + hideable_buttons)

        self._can_goto_previous_predictions = \
            self._first_prediction > 0
        self._can_goto_next_predictions = \
            self._first_prediction + len(prediction_keys) < \
            len(prediction_choices)
        self._next_predictions_increment = len(prediction_keys)

        return keys

    def _get_visible_buttons(self, correction_choices,
                             prediction_choices, scrolled_prediction_choices):
        enabled_button_ids = \
            config.word_suggestions.get_shown_wordlist_button_ids()

        previous_predictions_id = "previous-predictions"
        next_predictions_id = "next-predictions"

        previous_predictions_enabled = \
            previous_predictions_id in enabled_button_ids
        next_predictions_enabled = \
            next_predictions_id in enabled_button_ids

        if previous_predictions_enabled or \
           next_predictions_enabled:

            # generate keys without scrolling buttons
            visible_button_ids = enabled_button_ids[:]
            if previous_predictions_enabled:
                visible_button_ids.remove(previous_predictions_id)

            if next_predictions_enabled:
                visible_button_ids.remove(next_predictions_id)

            correction_keys, prediction_keys = \
                self._create_keys(correction_choices,
                                  scrolled_prediction_choices,
                                  visible_button_ids)

            # hide/show buttons as needed
            visible_button_ids = enabled_button_ids[:]
            enabled = len(prediction_keys) != len(prediction_choices) and \
                not self._correcions_expanded

            if previous_predictions_enabled and \
               (not self._more_predictions_requested or
                not enabled):
                visible_button_ids.remove(previous_predictions_id)

            if next_predictions_enabled and \
               not enabled:
                visible_button_ids.remove(next_predictions_id)
        else:
            visible_button_ids = enabled_button_ids

        return visible_button_ids

    def _create_keys(self, correction_choices, prediction_choices,
                     visible_button_ids):
        """
        Dynamically create a variable number of buttons
        for word correction and prediction.
        """
        correction_keys = []
        prediction_keys = []
        wordlist = self._get_child_button("wordlist")
        if wordlist:
            key_context = wordlist.context
            wordlist_rect = wordlist.get_rect()
            rect = wordlist_rect.copy()

            # position visible buttons
            buttons = []
            button_spacing = self._get_button_spacing()
            for button_id in reversed(visible_button_ids):
                button = self._get_child_button(button_id)
                if button and button.visible:
                    button_width = self._get_button_width(button)
                    rect.w -= button_width + button_spacing
                    buttons.append((button, button_width, 1))

            # font size is based on the height of the wordlist background
            font_size = WordKey().calc_font_size(key_context, rect.get_size())

            # hide the wordlist background when corrections create their own
            wordlist.set_visible(not correction_choices)

            # create correction keys
            correction_keys, used_rect = \
                self._create_correction_keys(correction_choices,
                                             rect, wordlist,
                                             key_context, font_size)
            rect.x += used_rect.w
            rect.w -= used_rect.w

            # create prediction keys
            if not self.are_corrections_expanded():
                prediction_keys = \
                    self._create_prediction_keys(prediction_choices,
                                                 rect, key_context,
                                                 font_size)

            # move the buttons to the end of the bar
            if buttons:
                rw = wordlist_rect.copy()
                for button, button_width, align in buttons:
                    r = rw.copy()
                    r.w = button_width
                    if align == -1:   # left align
                        r.x = rw.left() - button_width
                        rw.x += button_width
                        rw.w -= button_width + button_spacing
                    elif align == 1:  # right align
                        r.x = rw.right() - button_width
                        rw.w -= button_width + button_spacing
                    button.set_border_rect(r)

        return correction_keys, prediction_keys

    def _create_correction_keys(self, correction_choices, rect, wordlist,
                                key_context, font_size):
        """
        Create all correction keys.
        """
        choices_rect = rect.copy()
        wordlist_rect = wordlist.get_rect()
        section_spacing = 1
        if not self.are_corrections_expanded():
            section_spacing += wordlist.get_fullsize_rect().w - wordlist_rect.w
            section_spacing = max(section_spacing, wordlist_rect.h * 0.1)

        # get button to expand/close the corrections
        show_button = len(correction_choices) > 1
        button_width = 0
        button = self._get_child_button("expand-corrections")
        if button and show_button:
            button_width = self._get_button_width(button)
            choices_rect.w -= button_width + section_spacing

        # get template key for tooltips
        template = self._get_child_button("correction")

        # partition choices
        n = self.get_max_non_expanded_corrections()
        choices = correction_choices[:n]
        expanded_choices = correction_choices[n:] \
            if self.are_corrections_expanded() else []

        # create unexpanded correction keys
        keys, used_rect = \
            self._create_correction_choices(choices, choices_rect,
                                            key_context, font_size,
                                            0, template)
        exp_keys = []
        bg_keys = []
        if keys:
            if button:
                if show_button:
                    # Move the expand button to the end
                    # of the unexpanded corrections.
                    r = used_rect.copy()
                    r.x = used_rect.right()
                    r.w = button_width
                    button.set_border_rect(r)
                    button.set_visible(True)

                    used_rect.w += r.w
                else:
                    button.set_visible(False)

            # create background keys
            if self.are_corrections_expanded():
                x_split = wordlist_rect.right()
            else:
                x_split = used_rect.right()

            r = wordlist_rect.copy()
            r.w = x_split - r.x
            key = FullSizeKey("correctionsbg", r)
            key.sensitive = False
            key.scannable = False
            bg_keys.append(key)

            r = wordlist_rect.copy()
            r.w = r.right() - x_split - section_spacing
            r.x = x_split + section_spacing
            key = FullSizeKey("predictionsbg", r)
            key.sensitive = False
            key.scannable = False
            bg_keys.append(key)

            used_rect.w += section_spacing

            # create expanded correction keys
            if expanded_choices:
                exp_rect = choices_rect.copy()
                exp_rect.x += used_rect.w
                exp_rect.w -= used_rect.w
                exp_keys, exp_used_rect = \
                    self._create_correction_choices(expanded_choices, exp_rect,
                                                    key_context, font_size,
                                                    len(choices))
                keys += exp_keys
                used_rect.w += exp_used_rect.w
        else:
            if button:
                button.set_visible(False)

        return bg_keys + keys + exp_keys, used_rect

    def _get_child_button(self, id):
        return self.find_id(id)

    def _create_correction_choices(self, choices, rect,
                                   key_context, font_size,
                                   start_index=0, template=None):
        """
        Dynamically create a variable number of buttons for word correction.
        """
        spacing = self._get_entry_spacing()
        button_infos, filled_up, xend = \
            self._fill_rect_with_choices(choices, rect, key_context, font_size)

        # create buttons
        keys = []
        x, y = 0.0, 0.0
        for i, bi in enumerate(button_infos):
            w = bi.w

            # create key
            r = Rect(rect.x + x, rect.y + y, w, rect.h)
            key = WordKey("", r)
            key.id = "correction" + str(i)
            key.group = self.SUGGESTIONS_GROUP
            key.labels = {0 : bi.label[:]}
            key.font_size = font_size
            key.type = KeyCommon.CORRECTION_TYPE
            key.code = start_index + i
            if template:
                key.tooltip = template.tooltip
            keys.append(key)

            x += w + spacing  # move to begin of next button

        # return the actually used rect for all correction keys
        if keys:
            x -= spacing
        used_rect = rect.copy()
        used_rect.w = x

        return keys, used_rect

    def _create_prediction_keys(self, choices, wordlist_rect,
                                key_context, font_size):
        """
        Dynamically create a variable number of buttons for word prediction.
        """
        keys = []
        spacing = self._get_entry_spacing()

        button_infos, filled_up, xend = \
            self._fill_rect_with_choices(choices, wordlist_rect,
                                         key_context, font_size)
        if button_infos:
            all_spacings = (len(button_infos) - 1) * spacing

            if filled_up:
                # Find a stretch factor that fills the remaining space
                # with only expandable items.
                length_nonexpandables = sum(bi.w for bi in button_infos
                                            if not bi.expand)
                length_expandables = sum(bi.w for bi in button_infos
                                         if bi.expand)
                length_target = (wordlist_rect.w -
                                 length_nonexpandables -
                                 all_spacings)
                scale = length_target / length_expandables \
                    if length_expandables else 1.0
            else:
                # Find the stretch factor that fills the available
                # space with all items.
                scale = ((wordlist_rect.w - all_spacings) /
                         float(xend - all_spacings))
            # scale = 1.0  # no stretching, left aligned

            # create buttons
            x, y = 0.0, 0.0
            for i, bi in enumerate(button_infos):
                w = bi.w

                # scale either all buttons or only the expandable ones
                if not filled_up or bi.expand:
                    w *= scale

                # create the word key with the generic id "prediction<n>"
                key = WordKey("prediction" + str(i),
                              Rect(wordlist_rect.x + x,
                              wordlist_rect.y + y,
                              w, wordlist_rect.h))
                key.group = self.SUGGESTIONS_GROUP
                key.labels = {0 : bi.label[:]}
                key.font_size = font_size
                key.type = KeyCommon.WORD_TYPE
                key.code = i
                keys.append(key)

                x += w + spacing  # move to begin of next button

        return keys

    def _fill_rect_with_choices(self, choices, rect, key_context, font_size):
        spacing = self._get_entry_spacing()
        x = 0.0

        # context = Gdk.pango_context_get()
        pango_layout = WordKey.get_pango_layout(None, font_size)
        button_infos = []
        filled_up = False
        margins = config.WORDLIST_LABEL_MARGIN[0] * 2
        scale = key_context.scale_log_to_canvas_x(Pango.SCALE)

        for i, choice in enumerate(choices):
            # text extent in Pango units, button size in logical units
            max_width = (rect.w - margins) * scale
            label, label_width = \
                self._ellipsize(pango_layout, choice, max_width)
            w = label_width / scale + margins

            # Long words are allowed to expand their widths into the
            # remaining space. Short ones stay short.
            expand = w >= rect.h
            if not expand:
                w = rect.h

            # reached the end of the available space?
            if x + w > rect.w:
                filled_up = True
                break

            class ButtonInfo:
                pass
            bi = ButtonInfo()
            bi.label_width = label_width
            bi.x = x
            bi.w = w
            bi.expand = expand  # can stretch into available space?
            bi.label = label[:]

            button_infos.append(bi)

            x += w + spacing  # move to begin of next button

        if button_infos:
            x -= spacing

        return button_infos, filled_up, x

    @staticmethod
    def _ellipsize(pango_layout, text, max_width):
        """ Shorten very long words and add an ellipsis. """
        ellipsized_text = text
        w = WordListPanel._get_text_size(pango_layout, text)
        if w > max_width:
            # Grow one char at a time. Inefficient, but it isn't done often
            # anyway. PangoLayout introspection is too broken to use its
            # built-in ellipsizing capability (Xenial).
            w = 0
            for i, c in enumerate(text, start=0):
                ellipsized_text = text[:i] + "..."
                wt = WordListPanel._get_text_size(pango_layout,
                                                  ellipsized_text)
                if wt > max_width:
                    break
                w = wt
        return ellipsized_text, w

    @staticmethod
    def _get_text_size(pango_layout, text):
        pango_layout.set_text(text, -1)
        label_width, _label_height = pango_layout.get_size()
        return label_width


class ModelErrorRecovery:
    """ Notify of and recover from errors when loading user models. """

    def __init__(self, keyboard=None):
        self._keyboard = weakref.ref(keyboard) if keyboard else None

    def get_keyboard(self):
        if self._keyboard:
            return self._keyboard()

    def get_wpengine(self):
        keyboard = self.get_keyboard()
        if keyboard:
            return keyboard._wpengine
        return None

    def get_model_cache(self):
        wpengine = self.get_wpengine()
        if wpengine:
            return wpengine._model_cache
        return None

    def report_errors(self, wpengine):
        wpengine = self.get_wpengine()
        cache = self.get_model_cache()

        retry = False
        for lmid in wpengine.models:
            if cache.is_user_lmid(lmid):
                model = cache.get_model(lmid)
                filename = cache.get_filename(lmid)
                if model.load_error:
                    retry = retry or \
                        self._report_load_error(filename, model.load_error_msg)

        # clear the model cache and have models reloaded lazily
        if retry:
            cache.clear()

    def _report_load_error(self, filename, msg, _test_mode=False):
        """
        Handle model load failure.

        Doctests:
        >>> import tempfile
        >>> import subprocess
        >>> import glob
        >>> from os.path import basename, join
        >>> td = tempfile.TemporaryDirectory(prefix="test_onboard_")
        >>> tdir = td.name
        >>> fn = os.path.join(tdir, "en_US.lm")
        >>> mc = ModelErrorRecovery()

        >>> def touch(fn):
        ...     with open(fn, mode="w") as f: pass
        >>>
        >>> def test(fn):
        ...    mc._report_load_error(fn, "Failed to load model", True)
        ...    for f in sorted(f for f in glob.glob(join(tdir, "*"))):
        ...        print(repr(basename(f)))
        ...        os.remove(f)

        # If there is no backup, the old model shall be removed and saved
        # under the broken name.
        >>> touch(fn)
        >>> test(fn)    # doctest: +ELLIPSIS
        'en_US.lm.broken-..._001'

        # Test rename failure
        >>> test(fn)    # doctest: +ELLIPSIS
        Failed to rename broken model: ...

        # If there is a backup, the old model shall be saved under the broken
        # name and a copy of the backup becomes the new model.
        >>> touch(fn)
        >>> touch(mc.get_backup_filename(fn))
        >>> test(fn)    # doctest: +ELLIPSIS
        'en_US.lm'
        'en_US.lm.bak'
        'en_US.lm.broken-..._001'

        # Test rename failure
        >>> touch(mc.get_backup_filename(fn))
        >>> test(fn)    # doctest: +ELLIPSIS
        Failed to revert to backup model: ...
        """
        retry = False
        keyboard = self.get_keyboard()
        parent = keyboard.get_application()._window if keyboard else None
        backup_filename = self.get_backup_filename(filename)
        broken_filename = self.get_broken_filename(filename)

        if _test_mode:
            class Logger:
                def error(self, s):
                    print(s)
            _logger = Logger()

        if os.path.exists(backup_filename):
            markup = _format(
                "<big>Onboard failed to open learned "
                "word suggestions.</big>\n\n"
                "The error was:\n<tt>{}</tt>\n\n"
                "Revert word suggestions to the last backup (recommended)?",
                msg)
            markup = escape_markup(markup, preserve_tags=True)
            reply = self._show_dialog(markup, parent, buttons="yes_no") \
                if not _test_mode else True

            if reply:
                try:
                    os.rename(filename, broken_filename)
                    shutil.copy(backup_filename, filename)
                except OSError as ex:
                    _logger.error("Failed to revert to backup model: {}"
                                  .format(unicode_str(ex)))
                retry = True
        else:
            markup = _format(
                "<big>Onboard failed to open learned "
                "word suggestions.</big>\n\n"
                "The error was:\n<tt>{}</tt>\n\n"
                "The suggestions have to be reset, but "
                "the erroneous file will remain at \n'{}'",
                msg, broken_filename)
            markup = escape_markup(markup, preserve_tags=True)
            if not _test_mode:
                self._show_dialog(markup, parent)
            try:
                os.rename(filename, broken_filename)
            except OSError as ex:
                _logger.error("Failed to rename broken model: {}"
                              .format(unicode_str(ex)))
        return retry

    def _show_dialog(self, markup, parent=None,
                     message_type="error", buttons="ok"):
        keyboard = self.get_keyboard()

        if message_type == "question":
            message_type = Gtk.MessageType.QUESTION
        else:
            message_type = Gtk.MessageType.ERROR

        if buttons == "yes_no":
            buttons = Gtk.ButtonsType.YES_NO
        else:
            buttons = Gtk.ButtonsType.OK

        message_type = Gtk.MessageType.QUESTION
        dialog = Gtk.MessageDialog(message_type=message_type,
                                   buttons=buttons)
        dialog.set_markup(markup)
        if parent:
            dialog.set_transient_for(parent)

        # Don't hide dialog behind the keyboard in force-to-top mode.
        if config.is_force_to_top():
            dialog.set_position(Gtk.WindowPosition.CENTER)

        if keyboard:
            keyboard.on_focusable_gui_opening()
        response = dialog.run()
        dialog.destroy()
        if keyboard:
            keyboard.on_focusable_gui_closed()

        return response == Gtk.ResponseType.YES

    def get_backup_filename(self, filename):
        return ModelCache.get_backup_filename(filename)

    def get_broken_filename(self, filename):
        return ModelCache.get_broken_filename(filename)


