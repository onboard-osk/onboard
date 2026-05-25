# -*- coding: utf-8 -*-

# Copyright © 2016-2017 marmuta <marmvta@gmail.com>
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

import os
import socket
import select
import threading

from Onboard.utils import EventSource
from Onboard.GlobalKeyListener  import GlobalKeyListener

import logging
_logger = logging.getLogger("HardwareSensorTracker")

from Onboard.Config import Config
config = Config()


class HardwareSensorTracker(EventSource):
    """ Singleton class that keeps track of hardware sensors. """

    _tablet_mode_event_names = ("tablet-mode-changed",)
    _event_names = (("power-button-pressed",) +
                    _tablet_mode_event_names)

    # Filenames and search patterns to determine convertible tablet-mode.
    # Only some of the drivers that send SW_TABLET_MODE evdev events
    # also provide sysfs attributes to read the current tablet-mode state.
    _tablet_mode_state_files = (
        # classmate-laptop.c
        # nothing

        # fujitsu-tablet.c
        # nothing

        # hp-wmi.c
        ("/sys/devices/platform/hp-wmi/tablet",
         "1"),

        # ideapad-laptop.c, only debugfs which requires root
        # ("/sys/kernel/debug/ideapad/status",
        # re.compile("Touchpad status:Off(0)")),

        # thinkpad_acpi.c
        ("/sys/devices/platform/thinkpad_acpi/hotkey_tablet_mode",
         "1"),

        # xo15-ebook.c
        # nothing
    )

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
        self._acpid_listener = None
        self._tablet_mode = None
        self._key_listener = None

    def cleanup(self):
        EventSource.cleanup(self)
        self._register_listeners(False)

    def connect(self, event_name, callback):
        EventSource.connect(self, event_name, callback)
        self.update_sensor_sources()

    def disconnect(self, event_name, callback):
        had_listeners = self.has_listeners(self._event_names)

        EventSource.disconnect(self, event_name, callback)
        self.update_sensor_sources()

        # help debugging disconnecting events on exit
        if had_listeners and not self.has_listeners(self._event_names):
            _logger.info("all listeners disconnected")

    def update_sensor_sources(self):
        register = self.has_listeners()
        self._register_acpid_listeners(register)

        register = self.has_listeners(self._tablet_mode_event_names)
        self._register_hotkey_listeners(register)

    def _register_listeners(self, register):
        self._register_acpid_listeners(register)
        self._register_hotkey_listeners(register)

    def _register_acpid_listeners(self, register):
        if bool(self._acpid_listener) != register:
            if register:
                self._acpid_listener = AcpidListener(self)
            else:
                self._acpid_listener.stop()
                self._acpid_listener = None

    def _register_hotkey_listeners(self, register):
        enter_key = config.auto_show.tablet_mode_enter_key
        leave_key = config.auto_show.tablet_mode_leave_key
        if not enter_key and not leave_key:
            register = False

        if register:
            if not self._key_listener:
                self._key_listener = GlobalKeyListener()
                self._key_listener.connect("key-press", self._on_key_press)
        else:
            if self._key_listener:
                self._key_listener.disconnect("key-press", self._on_key_press)
            self._key_listener = None

    def set_tablet_mode(self, activ):
        self._tablet_mode = activ
        self.emit_async("tablet-mode-changed", activ)

    def get_tablet_mode(self):
        """
        Return value:
            True = convertible is in tablet-mode
            False = convertible is not in tablet-mode
            None = mode unknown
        """
        state = self._get_tablet_mode_state()
        if state is None:
            return self._tablet_mode
        return state

    def _get_tablet_mode_state(self):
        """
        Read the state from known system files, if available.
        Else return None.
        "sysfs" files are read from kernel memory, shouldn't be
        too expensive to do repeatedly.
        """
        custom_state_file = config.auto_show.tablet_mode_state_file
        custom_pattern = config.auto_show.tablet_mode_state_file_pattern
        if custom_state_file:
            candidates = ((custom_state_file, custom_pattern),)
        else:
            candidates = self._tablet_mode_state_files

        for fn, pattern in candidates:
            try:
                with open(fn, "r", encoding="UTF-8") as f:
                    content = f.read(4096)
            except IOError as ex:
                _logger.debug("Opening '{}' failed: {}".format(fn, ex))
                content = ""
            if content:
                if isinstance(pattern, str):
                    active = bool(pattern) and pattern in content
                else:
                    active = bool(pattern.search(content))
                _logger.info("read tablet_mode={} from '{}' with pattern '{}'"
                             .format(active, fn, pattern))
                return active

        return None

    def _on_key_press(self, event):
        """ Global hotkey press received """
        enter_keycode = config.auto_show.tablet_mode_enter_key
        leave_keycode = config.auto_show.tablet_mode_leave_key

        if _logger.isEnabledFor(logging.INFO):
            s = self._key_listener.get_key_event_string(event)
            s += ", enter_keycode={}, leave_keycode={}".format(enter_keycode,
                                                               leave_keycode)
            _logger.info("_on_key_press(): {}".format(s))

        if enter_keycode and event.keycode == enter_keycode:
            _logger.info("hotkey tablet_mode_enter_key {} received"
                         .format(enter_keycode))
            self.set_tablet_mode(True)

        if leave_keycode and event.keycode == leave_keycode:
            _logger.info("hotkey tablet_mode_leave_key {} received"
                         .format(leave_keycode))
            self.set_tablet_mode(False)


class AcpidListener:
    """ Listen to events aggregated by acpid. """

    def __init__(self, sensor_tracker):
        super(AcpidListener, self).__init__()
        self._sensor_tracker = sensor_tracker
        self._exit_r = self._exit_w = None

        self.start()

    def start(self):
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        fn = "/var/run/acpid.socket"
        try:
            self._socket.connect(fn)
        except Exception as ex:
            _logger.warning("Failed to connect to acpid, "
                            "SW_TABLET_MODE detection disabled. "
                            "('{}': {}) "

                            .format(fn, str(ex)))
            return

        self._socket.setblocking(False)
        self._exit_r, self._exit_w = os.pipe()

        self._thread = threading.Thread(name=self.__class__.__name__,
                                        target=self._run)
        self._thread.start()

    def stop(self):
        if self._exit_w:
            os.write(self._exit_w, "x".encode())
            self._thread.join(2)
            _logger.info("AcpidListener: thread stopped, is_alive={}"
                         .format(self._thread.is_alive()))

    def _run(self):
        _logger.info("AcpidListener: thread start")

        while True:
            rl, wl, xl = select.select([self._exit_r, self._socket],
                                       [], [self._socket])
            if self._socket in rl:
                data = self._socket.recv(4096)
            elif self._exit_r in rl:
                break

            for event in data.decode("UTF-8", errors='replace').splitlines():

                _logger.info("AcpidListener: ACPI event: '{}'"
                             .format(event))

                if event == "button/power PBTN 00000080 00000000":
                    _logger.info("AcpidListener: power button")
                    self._sensor_tracker.emit_async("power-button-pressed")

                elif event == "video/tabletmode TBLT 0000008A 00000001":
                    _logger.info("AcpidListener: tablet_mode True")
                    self._sensor_tracker.set_tablet_mode(True)

                elif event == "video/tabletmode TBLT 0000008A 00000000":
                    _logger.info("AcpidListener: tablet_mode False")
                    self._sensor_tracker.set_tablet_mode(False)

        self._socket.close()
        self._socket.close()
        os.close(self._exit_r)
        os.close(self._exit_w)

        _logger.info("AcpidListener: thread exit")


