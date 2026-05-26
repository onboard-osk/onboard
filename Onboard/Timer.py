# -*- coding: utf-8 -*-

# Copyright © 2016 marmuta <marmvta@gmail.com>
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

import time
import logging
import traceback
import subprocess

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import GLib

from Onboard.utils import Fade

_logger = logging.getLogger("utils_timers.py")


def idle_call(func, *args):
    GLib.idle_add(func, *args)


class Timer(object):
    """
    Simple wrapper around GLib's timer API
    Overload on_timer in derived classes.
    For one-shot timers return False there.
    """
    _timer = None
    _callback = None
    _callback_args = None

    def __init__(self, delay=None, callback=None, *callback_args):
        self._callback = callback
        self._callback_args = callback_args

        if delay is not None:
            self.start(delay)

    def start(self, delay, callback=None, *callback_args):
        """
        Delay in seconds.
        Uses second granularity if delay is of type int.
        Uses medium resolution timer if delay is of type float.
        """
        if callback:
            self._callback = callback
            self._callback_args = callback_args

        self.stop()

        if type(delay) == int:
            self._timer = GLib.timeout_add_seconds(delay, self._cb_timer)
        else:
            ms = int(delay * 1000.0)
            self._timer = GLib.timeout_add(ms, self._cb_timer)

    def finish(self):
        """
        Run callback one last time and stop.
        """
        if self.is_running():
            self.stop()
            self.on_timer()

    def stop(self):
        if self.is_running():
            # GLib auto-removes the source if _cb_timer raised, in which
            # case source_remove() warns "Source ID NNNN was not found".
            # Swallow that; the source is already gone.
            try:
                GLib.source_remove(self._timer)
            except Exception:
                pass
            self._timer = None

    def is_running(self):
        return self._timer is not None

    def _cb_timer(self):
        try:
            keep = self.on_timer()
        except Exception:
            # PyGObject prints the traceback and the GLib source is
            # auto-removed after an unhandled exception. Invalidate the
            # cached ID so a later stop() doesn't trigger a spurious
            # "Source ID NNNN was not found" warning.
            self._timer = None
            raise
        if not keep:
            self.stop()
            return False
        return True

    def on_timer(self):
        """
        Overload this.
        For one-shot timers return False.
        """
        if self._callback:
            return self._callback(*self._callback_args)
        return True


class TimerOnce(Timer):
    def on_timer(self):
        """
        Overload this.
        """
        if self._callback:
            return self._callback(*self._callback_args)
        return False


class ProgressiveDelayTimer(Timer):
    """
    Timer that increases the delay for each iteration
    until max_duration is reached.
    """
    growth = 2.0
    max_duration = 3.0
    max_delay = 1.0

    _start_time = 0
    _current_delay = 0

    def start(self, delay, callback=None, *callback_args):
        self._start_time = time.time()
        self._current_delay = delay
        Timer.start(self, delay, callback, *callback_args)

    def on_timer(self):
        if not Timer.on_timer(self):
            return False

        # start another timer for progressively longer intervals
        self._current_delay = min(self._current_delay * self.growth,
                                  self.max_delay)
        if time.time() + self._current_delay < \
           self._start_time + self.max_duration:
            Timer.start(self, self._current_delay,
                        self._callback, *self._callback_args)
            return True
        else:
            return False


class DelayedLauncher(Timer):
    """
    Launches a process after a certain delay.
    Used for launching mousetweaks.
    """
    args = None

    def launch_delayed(self, args, delay):
        self.args = args
        self.start(delay)

    def on_timer(self):
        _logger.debug("launching '{}'".format(" ".join(self.args)))
        try:
            subprocess.Popen(self.args)
        except OSError as e:
            _logger.warning(_format("Failed to execute '{}', {}", \
                            " ".join(self.args), e))
        return False


class FadeTimer(Timer):
    """
    Sine-interpolated fade between two values, e.g. opacities.
    """
    value = None
    start_value = None
    target_value = None
    iteration = 0   # just a counter of on_timer calls since start
    time_step = 0.05

    def fade_to(self, start_value, target_value, duration,
                callback = None, *callback_args):
        """
        Start value fade.
        duration: fade time in seconds, 0 for immediate value change
        """
        self.value = start_value
        self.start_value = start_value
        self._start_time = time.time()
        self._duration = duration
        self._callback = callback
        self._callback_args = callback_args

        self.start(self.time_step)

        self.target_value = target_value

    def start(self, delay):
        self.iteration = 0
        Timer.start(self, delay)

    def stop(self):
        self.target_value = None
        Timer.stop(self)

    def on_timer(self):
        self.value, done = Fade.sin_fade(self._start_time, self._duration,
                                         self.start_value, self.target_value)

        if self._callback:
            self._callback(self.value, done, *self._callback_args)

        self.iteration += 1
        return not done


class CallOnce(object):
    """
    Call each <callback> during <delay> only once
    Useful to reduce a storm of config notifications
    to just a single (or a few) update(s) of onboards state.
    """

    def __init__(self, delay=20, delay_forever=False):
        self.callbacks = {}
        self.timer = None
        self.delay = delay
        self.delay_forever = delay_forever

    def is_running(self):
        return self.timer is not None

    def enqueue(self, callback, *args):
        if callback not in self.callbacks:
            self.callbacks[callback] = args
        else:
            # print "CallOnce: ignored ", callback, args
            pass

        if self.delay_forever and self.timer:
            GLib.source_remove(self.timer)
            self.timer = None

        if not self.timer and self.callbacks:
            self.timer = GLib.timeout_add(self.delay, self.cb_timer)

    def stop(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        self.callbacks.clear()

    def cb_timer(self):
        for callback, args in list(self.callbacks.items()):
            try:
                callback(*args)
            except:
                traceback.print_exc()

        self.stop()
        return False


