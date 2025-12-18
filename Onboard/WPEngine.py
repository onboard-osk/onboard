# -*- coding: utf-8 -*-

# Copyright © 2013-2017 marmuta <marmvta@gmail.com>
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
import logging

from Onboard.utils import unicode_str, XDGDirs
from Onboard.Timer import Timer
from Onboard.Config import Config

try:
    import Onboard.pypredict as pypredict
except Exception as e:
    pypredict = None

config = Config()

_logger = logging.getLogger(__name__)


class WPLocalEngine(object):
    """
    Singleton class for low-level word prediction, local in-process engine.
    """

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
        Called multiple times, do not use.
        """
        pass

    def construct(self):
        """
        Singleton constructor, runs only once.
        """
        self._model_cache = ModelCache()
        self._auto_save_timer = AutoSaveTimer(self._model_cache)
        self.models = []
        self.persistent_models = []
        self.auto_learn_models = []
        self.scratch_models = []

    def cleanup(self):
        self._auto_save_timer.stop()
        self._model_cache.save_models()

    def set_models(self, persistent_models, auto_learn_models, scratch_models):
        """ Fixme: rename to "set_model_ids" """
        self.models = persistent_models + scratch_models
        self.persistent_models = persistent_models
        self.auto_learn_models = auto_learn_models
        self.auto_learn_models = auto_learn_models
        self.scratch_models = scratch_models

    def load_models(self):
        """
        Pre-load models set with set_models. If this isn't called,
        language models are lazy-loaded on demand.
        """
        self._model_cache.get_models(self.models)

    def postpone_autosave(self):
        self._auto_save_timer.postpone()

    def pause_autosave(self):
        # Pause for a minute max, because resume_autosave isn't
        # reliable called, e.g. when dragging and leaving the window.
        self._auto_save_timer.pause(60)

    def resume_autosave(self):
        self._auto_save_timer.resume()

    def predict(self, context_line, limit=20,
                case_insensitive=False,
                case_insensitive_smart=False,
                accent_insensitive=False,
                accent_insensitive_smart=False,
                ignore_capitalized=False,
                ignore_non_capitalized=False):
        """ Find completion/prediction choices. """
        LanguageModel = pypredict.LanguageModel
        options = 0
        if case_insensitive:
            options |= LanguageModel.CASE_INSENSITIVE
        if case_insensitive_smart:
            options |= LanguageModel.CASE_INSENSITIVE_SMART
        if accent_insensitive:
            options |= LanguageModel.ACCENT_INSENSITIVE
        if accent_insensitive_smart:
            options |= LanguageModel.ACCENT_INSENSITIVE_SMART
        if ignore_capitalized:
            options |= LanguageModel.IGNORE_CAPITALIZED
        if ignore_non_capitalized:
            options |= LanguageModel.IGNORE_NON_CAPITALIZED

        context, spans = pypredict.tokenize_context(context_line)
        choices = self._get_prediction(self.models, context, limit, options)
        _logger.debug("context=" + repr(context))
        _logger.debug("choices=" + repr(choices[:5]))
        return [x[0] for x in choices]

    def learn_text(self, text, allow_new_words):
        """ Count n-grams and add words to the auto-learn models. """

        _logger.debug("learn_text(text={}, allow_new_words={}): "
                      "auto_learn_models={}"
                      .format(repr(text), allow_new_words,
                              self.auto_learn_models))

        if self.auto_learn_models:
            tokens, spans = pypredict.tokenize_text(text)

            # There are too many false positives with trailing
            # single quotes, remove them.
            # Do this here, because we still want "it's", etc. to
            # incrementally provide completions.
            for i, token in enumerate(tokens):
                if token.endswith("'"):
                    token = token[:-1]
                    if not token:  # shouldn't happen
                        token = "<unk>"
                    tokens[i] = token

            # if requested, drop unknown words
            if allow_new_words:
                token_sections = [tokens]
            else:
                token_sections = self._drop_new_words(tokens, spans,
                                                      self.persistent_models)
            models = self._model_cache.get_models(self.auto_learn_models)
            for model in models:
                for tokens in token_sections:
                    model.learn_tokens(tokens)

            _logger.info("learn_text: tokens=" + repr(token_sections))

            # debug: save all learned text for later parameter optimization
            if config.log_learn:
                fn = os.path.join(config.user_dir, "learned_text.txt")
                with open(fn, "a") as f:
                    f.write(text + "\n")

    def _drop_new_words(self, tokens, spans, lmids):
        """ Remove tokens that don't already exist in any active model.  """

        tokspans, counts = self.lookup_tokens(tokens, spans, lmids)
        split_indices = [i for i, model_counts in enumerate(counts)
                         if all(n != 1 for n in model_counts)]
        return pypredict.split_tokens_at(tokens, split_indices)

    def learn_scratch_text(self, text):
        """ Count n-grams and add words to the scratch models. """
        tokens, spans = pypredict.tokenize_text(text)
        models = self._model_cache.get_models(self.scratch_models)
        for model in models:
            # print("scratch learn", model, tokens)
            model.learn_tokens(tokens, True)

    def clear_scratch_models(self):
        models = self._model_cache.get_models(self.scratch_models)
        for model in models:
            model.clear()

    def lookup_text(self, text, lmids):
        """
        Split <text> into tokens and lookup the individual tokens in each
        of the given language models. See lookup_tokens() for more information.
        """
        tokens, spans = pypredict.tokenize_sentence(text)
        return self.lookup_tokens(tokens, spans, lmids)

    def lookup_tokens(self, tokens, spans, lmids):
        """
        Lookup the individual tokens in each of the given language models.
        This method is meant to be a basis for highlighting (partially)
        unknown words in a display for recently typed text.

        The return value is a tuple of two arrays. First an array of tuples
        (start, end, token), one per token, with start and end index pointing
        into <text> and second a two dimensional array of lookup results.
        There is one lookup result per token and language model. Each lookup
        result is either 0 for no match, 1 for an exact match or -n for
        count n partial (prefix) matches.
        """
        tokspans  = [(spans[i][0], spans[i][1], t)
                     for i, t in enumerate(tokens)]
        counts = [[0 for lmid in lmids] for t in tokspans]
        for i, lmid in enumerate(lmids):
            model = self._model_cache.get_model(lmid)
            if model:
                for j, t in enumerate(tokspans):
                    counts[j][i] = model.lookup_word(t[2])

        _logger.debug("lookup_tokens: tokens=%s counts=%s" %
                     (repr(tokens), repr(counts)))

        # Counts are 0 for no match, 1 for exact match or
        # -n for partial matches
        return tokens, counts

    def word_exists(self, word):
        """
        Does word exist in any of the non-scratch models?
        """
        exists = False
        lmids = self.persistent_models
        for i, lmid in enumerate(lmids):
            model = self._model_cache.get_model(lmid)
            if model:
                count = model.lookup_word(word)
                if count > 0:
                    exists = True
                    break
        return exists

    def tokenize_text(self, text):
        """
        Let the service find the words in text.
        """
        tokens, spans = pypredict.tokenize_text(text)
        return tokens, spans

    def tokenize_text_pythonic(self, text):
        """
        Let the service find the words in text.
        Return python types instead of dbus.Array/String/... .

        Doctests:
        # whitspace have to be respected in spans
        >>> p = WPLocalEngine()
        >>> p.tokenize_text_pythonic("abc  def")
        (['abc', 'def'], [[0, 3], [5, 8]])
        """
        return self.tokenize_text(text)

    def tokenize_context(self, text):
        """ let the service find the words in text """
        return pypredict.tokenize_context(text)

    def get_model_names(self, _class):
        """ Return the names of the available models. """
        names = self._model_cache.find_available_model_names(_class)
        return names

    def get_last_context_fragment(self, text):
        """
        Return the very last (partial) word in text.
        """
        text = text[-1024:]
        tokens, spans = self.tokenize_context(text)
        if len(spans):
            # Don't return the token itself as it won't include
            # trailing dashes. Catch the text until its very end.
            begin = spans[-1][0]
            return text[begin:]
        else:
            return ""

    def _get_prediction(self, lmdesc, context, limit, options):
        lmids, weights = self._model_cache.parse_lmdesc(lmdesc)
        models = self._model_cache.get_models(lmids)

        for m in models:
            # Kneser-ney perfomes best in entropy and ksr measures, but
            # failed in practice for anything but natural language, e.g.
            # shell commands.
            # -> use the second best available: absolute discounting
            # m.smoothing = "kneser-ney"
            m.smoothing = "abs-disc"

            # setup recency caching
            if hasattr(m, "recency_ratio"):
                # Values found with
                # $ pypredict/optimize caching models/en.lm learned_text.txt
                # based on multilingual text actually typed (--log-learning)
                # with onboard over ~3 months.
                # How valid those settings are under different conditions
                # remains to be seen, but for now this is the best I have.
                m.recency_ratio = 0.811
                m.recency_halflife = 96
                m.recency_smoothing = "jelinek-mercer"
                m.recency_lambdas = [0.404, 0.831, 0.444]

        model = pypredict.overlay(models)
        # model = pypredict.linint(models, weights)
        # model = pypredict.loglinint(models, weights)

        choices = model.predictp(context, limit, options=options)

        return choices

    def remove_context(self, context):
        """
        Remove the last word of context in the given context.
        If len(context) == 1 then all occurences of the word will be removed.
        """
        lmids, weights = self._model_cache.parse_lmdesc(self.auto_learn_models)
        models = self._model_cache.get_models(lmids)
        for i, m in enumerate(models):
            changes = m.remove_context(context)

            # debug output
            _logger.debug("removing {} from '{}': {} n-grams affected"
                          .format(context, lmids[i], len(changes)))
            if _logger.isEnabledFor(logging.DEBUG):
                changes = sorted(sorted(changes.items()),
                                 key=lambda x: -len(x[0]))
                for ng in changes:
                    _logger.debug("    remove: {}, count {}"
                                  .format(ng[0], ng[1]))


class ModelCache:
    """ Loads and caches language models """

    def __init__(self):
        self._language_models = {}

    def clear(self):
        self._language_models = {}

    def get_models(self, lmids):
        models = []
        for lmid in lmids:
            model = self.get_model(lmid)
            if model:
                models.append(model)
        return models

    def get_model(self, lmid):
        """ get language model from cache or load it from disk"""
        lmid = self.canonicalize_lmid(lmid)
        if lmid in self._language_models:
            model = self._language_models[lmid]
        else:
            model = self.load_model(lmid)
            if model:
                self._language_models[lmid] = model
        return model

    def find_available_model_names(self, _class):
        names = []
        models = self._find_models(_class)
        for model in models:
            name = os.path.basename(model)
            name, ext = os.path.splitext(name)
            names.append(name)
        return names

    @staticmethod
    def _find_models(_class):
        models = []

        if _class == "system":
            path = config.get_system_model_dir()
        else:
            path = config.get_user_model_dir()

        try:
            files = os.listdir(path)
            extension = "lm"
            for filename in files:
                if filename.endswith("." + extension):
                    models.append(os.path.join(path, filename))
        except OSError as e:
            _logger.warning("Failed to find language models in '{}': {} ({})"
                            .format(path, os.strerror(e.errno), e.errno))
        return models

    @staticmethod
    def parse_lmdesc(lmdesc):
        """
        Extract language model ids and interpolation weights from
        the language model description.
        """
        lmids = []
        weights = []

        for entry in lmdesc:
            fields = entry.split(",")

            lmids.append(fields[0])

            weight = 1.0
            if len(fields) >= 2:  # weight is optional
                try:
                    weight = float(fields[1])
                except:
                    pass
            weights.append(weight)

        return lmids, weights

    @staticmethod
    def canonicalize_lmid(lmid):
        """
        Fully qualifies and unifies language model ids.
        Fills in missing fields with default values.
        The result is of the format "type:class:name".
        """
        # default values
        result = ["lm", "system", "en"]
        for i, field in enumerate(lmid.split(":")[:3]):
            result[i] = field
        return ":".join(result)

    @staticmethod
    def split_lmid(lmid):
        lmid = ModelCache.canonicalize_lmid(lmid)
        return lmid.split(":")

    @staticmethod
    def is_user_lmid(lmid):
        type_, class_, name = ModelCache.split_lmid(lmid)
        return class_ == "user"

    def load_model(self, lmid):
        type_, class_, name  = lmid.split(":")

        filename = self.get_filename(lmid)

        if type_ == "lm":
            if class_ == "system":
                if pypredict.read_order(filename) == 1:
                    model = pypredict.UnigramModel()
                else:
                    model = pypredict.DynamicModel()
            elif class_ == "user":
                model = pypredict.CachedDynamicModel()
            elif class_ == "mem":
                model = pypredict.DynamicModel()
            else:
                _logger.error("Unknown class component '{}' in lmid '{}'"
                              .format(class_, lmid))
                return None
        else:
            _logger.error("Unknown type component '{}' in lmid '{}'"
                          .format(type_, lmid))
            return None

        if filename:
            self.do_load_model(model, filename, class_)

        return model

    @staticmethod
    def do_load_model(model, filename, class_):
        _logger.info("Loading language model '{}'.".format(filename))

        if not os.path.exists(filename):
            if class_ == "system":
                _logger.warning("System language model '{}' "
                                "doesn't exist, skipping."
                                .format(filename))
        else:
            try:
                model.load(filename)
            except IOError as ex:
                if ex.errno is not None:  # not n-gram count mismatch
                    errno = ex.errno
                    errstr = os.strerror(errno)
                    msg = _format(
                            "Failed to load language model '{}': {} ({})",
                            filename, errstr, errno)
                else:
                    msg = unicode_str(ex)
                _logger.error(msg)
                model.load_error_msg = msg

                if class_ == "user":
                    _logger.error("Saving word suggestions disabled "
                                  "to prevent further data loss.")

    def save_models(self):
        for lmid, model in list(self._language_models.items()):
            if self.can_save(lmid):
                self.save_model(model, lmid)

    @staticmethod
    def can_save(lmid):
        type_, class_, name  = lmid.split(":")
        return class_ == "user"

    def save_model(self, model, lmid):
        type_, class_, name  = lmid.split(":")
        filename = self.get_filename(lmid)

        backup_filename = self.get_backup_filename(filename)

        if filename and \
           model.modified:

            if model.load_error:
                _logger.warning("Not saving modified language model '{}' "
                                "due to previous error on load."
                                .format(filename))
            else:
                _logger.info("Saving language model '{}'".format(filename))
                try:
                    # create the path
                    path = os.path.dirname(filename)
                    XDGDirs.assure_user_dir_exists(path)

                    if 1:
                        # save to temp file
                        basename, ext = os.path.splitext(filename)
                        tempfile = basename + ".tmp"
                        model.save(tempfile)

                        # rename to final file
                        if os.path.exists(filename):
                            os.rename(filename, backup_filename)
                        os.rename(tempfile, filename)

                    model.modified = False
                except (IOError, OSError) as e:
                    _logger.warning(
                        "Failed to save language model '{}': {} ({})"
                        .format(filename, os.strerror(e.errno), e.errno))

    @staticmethod
    def get_filename(lmid):
        type_, class_, name  = lmid.split(":")
        if class_ == "mem":
            filename = ""
        else:
            if class_ == "system":
                path = config.get_system_model_dir()
            else:  # if class_ == "user":
                path = config.get_user_model_dir()
            ext = type_
            filename = os.path.join(path, name + "." + ext)

        return filename

    @staticmethod
    def get_backup_filename(filename):
        return filename + ".bak"

    @staticmethod
    def get_broken_filename(filename):
        """
        Filename broken files are renamed to.

        Doctests:
        >>> import tempfile
        >>> import subprocess
        >>> from os.path import basename
        >>> td = tempfile.TemporaryDirectory(prefix="test_onboard_")
        >>> dir = td.name
        >>> fn = os.path.join(dir, "en_US.lm")
        >>>
        >>> def test(fn):
        ...     bfn = ModelCache.get_broken_filename(fn)
        ...     print(repr(basename(bfn)))
        ...     _ignore = subprocess.call(["touch", bfn])

        >>> test(fn)   # doctest: +ELLIPSIS
        'en_US.lm.broken-..._001'

        >>> test(fn)   # doctest: +ELLIPSIS
        'en_US.lm.broken-..._002'

        >>> test(fn)   # doctest: +ELLIPSIS
        'en_US.lm.broken-..._003'
        """
        count = 1
        while True:
            fn = "{}.broken-{}_{:03}".format(filename,
                                             time.strftime("%Y-%m-%d"),
                                             count)
            if not os.path.exists(fn):
                break
            count += 1
        return fn


class AutoSaveTimer(Timer):
    """ Auto-save modified language models periodically """

    def __init__(self, mode_cache,
                 interval_min=10 * 60,
                 interval_max=30 * 60,
                 postpone_delay=10):
        self._model_cache = mode_cache
        self._interval_min = interval_min  # in seconds
        self._interval_max = interval_max  # in seconds
        self._postpone_delay = postpone_delay
        self._interval = self._interval_min  # in seconds
        self._last_save_time = time.time()
        self._pause = 0
        self._timer_interval = 5
        self.start(self._timer_interval, self._on_timer)

    def pause(self, duration=None):
        """
        No auto-saving while paused, e.g. during key-press.
        """
        self._pause = duration

    def resume(self):
        """
        Allow auto-saving again.
        """
        self._pause = 0

    def postpone(self):
        """
        Postpone saving a little while the user is still typing.
        Helps to mask the delay when saving large models, during which
        Onboard briefly becomes unresponsive.
        """
        elapsed = time.time() - self._last_save_time
        if self._interval < elapsed + self._postpone_delay:
            self._interval = elapsed + self._postpone_delay
            if self._interval > self._interval_max:
                self._interval = self._interval_max
        _logger.debug("postponing autosave: current interval {}, "
                      "elapsed since last save {}"
                      .format(self._interval, elapsed))

    def _on_timer(self):
        now = time.time()
        elapsed = now - self._last_save_time
        if self._interval < elapsed and \
           self._pause == 0:
            self._last_save_time = now
            self._interval = self._interval_min
            _logger.debug("auto-saving language models; "
                          "interval {}, elapsed time {}"
                          .format(self._interval, elapsed))
            self._model_cache.save_models()

        if self._pause:
            self._pause = max(0, self._pause - self._timer_interval)

        return True  # run again


