# -*- coding: utf-8 -*-

# Copyright © 2008-2010 Chris Jones <tortoise@tortuga>
# Copyright © 2008-2011 Francesco Fumanti <francesco.fumanti@gmx.net>
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

"""
File containing Config singleton.
"""

from __future__ import division, print_function, unicode_literals

import os
import sys
import locale
import ctypes.util
import site

from shutil import copytree
from optparse import OptionParser, OptionGroup

from Onboard.Version import require_gi_versions
require_gi_versions()
from gi.repository import Gtk, Gio, GLib

from Onboard.WindowUtils    import show_confirmation_dialog
from Onboard.utils          import Version, \
                                   unicode_str, XDGDirs, chmodtree, \
                                   Process, hexcolor_to_rgba, TermColors
from Onboard.definitions    import DesktopEnvironmentEnum, \
                                   StatusIconProviderEnum, \
                                   InputEventSourceEnum, \
                                   TouchInputEnum, \
                                   LearningBehavior, \
                                   RepositionMethodEnum, \
                                   Handle, DockingEdge, DockingMonitor
from Onboard.ConfigUtils    import ConfigObject
from Onboard.ClickSimulator import CSMousetweaks0, CSMousetweaks1
from Onboard.Exceptions     import SchemaError
from importlib.metadata import PackageNotFoundError, version


### Logging ###
import logging
_logger = logging.getLogger("Config")
###############

# gsettings schemas
SCHEMA_ONBOARD           = "org.onboard"
SCHEMA_KEYBOARD          = "org.onboard.keyboard"
SCHEMA_WINDOW            = "org.onboard.window"
SCHEMA_WINDOW_LANDSCAPE  = "org.onboard.window.landscape"
SCHEMA_WINDOW_PORTRAIT   = "org.onboard.window.portrait"
SCHEMA_ICP               = "org.onboard.icon-palette"
SCHEMA_ICP_LANDSCAPE     = "org.onboard.icon-palette.landscape"
SCHEMA_ICP_PORTRAIT      = "org.onboard.icon-palette.portrait"
SCHEMA_AUTO_SHOW         = "org.onboard.auto-show"
SCHEMA_UNIVERSAL_ACCESS  = "org.onboard.universal-access"
SCHEMA_THEME             = "org.onboard.theme-settings"
SCHEMA_LOCKDOWN          = "org.onboard.lockdown"
SCHEMA_SCANNER           = "org.onboard.scanner"
SCHEMA_TYPING_ASSISTANCE = "org.onboard.typing-assistance"
SCHEMA_WORD_SUGGESTIONS  = "org.onboard.typing-assistance.word-suggestions"

SCHEMA_GSS               = "org.gnome.desktop.screensaver"
SCHEMA_GDI               = "org.gnome.desktop.interface"
SCHEMA_GDA               = "org.gnome.desktop.a11y.applications"
SCHEMA_UNITY_GREETER     = "com.canonical.unity-greeter"

MODELESS_GKSU_KEY = "/apps/gksu/disable-grab"  # old gconf key, unused

# hard coded defaults
DEFAULT_X                  = 100   # Make sure these match the schema defaults,
DEFAULT_Y                  = 50    # else dconf data migration won't happen.
DEFAULT_WIDTH              = 700
DEFAULT_HEIGHT             = 205

# Default rect on Nexus 7
# landscape x=65, y=500, w=1215 h=300
# portrait  x=55, y=343, w=736 h=295

DEFAULT_ICP_X              = 100   # Make sure these match the schema defaults,
DEFAULT_ICP_Y              = 50    # else dconf data migration won't happen.
DEFAULT_ICP_HEIGHT         = 64
DEFAULT_ICP_WIDTH          = 64

DEFAULT_LAYOUT             = "Compact"
DEFAULT_THEME              = "Classic Onboard"
DEFAULT_COLOR_SCHEME       = "Classic Onboard"

START_ONBOARD_XEMBED_COMMAND = "onboard --xid"

INSTALL_DIR                = "/usr/share/onboard"
LOCAL_INSTALL_DIR          = "/usr/local/share/onboard"
USER_DIR                   = "onboard"

SYSTEM_DEFAULTS_FILENAME   = "onboard-defaults.conf"

DEFAULT_WINDOW_HANDLES     = list(Handle.RESIZE_MOVE)

DEFAULT_FREQUENCY_TIME_RATIO = 75  # 0=100% frequency, 100=100% time (last use)

SCHEMA_VERSION_0_97         = Version(1, 0)   # Onboard 0.97
SCHEMA_VERSION_0_98         = Version(2, 0)   # Onboard 0.97.1
SCHEMA_VERSION_0_99         = Version(2, 1)   # Onboard 0.99.0
SCHEMA_VERSION_1_1          = Version(2, 2)
SCHEMA_VERSION_1_2          = Version(2, 3)
SCHEMA_VERSION              = SCHEMA_VERSION_1_2


# enum for simplified number of window_handles
class NumResizeHandles:
    NONE     = 0
    NORESIZE = 1
    SOME     = 2
    ALL      = 3

class Config(ConfigObject):
    """
    Singleton Class to encapsulate the gsettings stuff and check values.
    """

    # launched by ...
    (LAUNCHER_NONE,
     LAUNCHER_GSS,
     LAUNCHER_UNITY_GREETER) = range(3)

    # extension of layout files
    LAYOUT_FILE_EXTENSION = ".onboard"

    # A copy of snippets so that when the list changes in gsettings we can
    # tell which items have changed.
    _last_snippets = None

    # Margin to leave around labels
    LABEL_MARGIN = (1, 1)

    # Horizontal label alignment
    DEFAULT_LABEL_X_ALIGN = 0.5

    # Vertical label alignment
    DEFAULT_LABEL_Y_ALIGN = 0.5

    # layout group for independently sized superkey labels
    SUPERKEY_SIZE_GROUP = "super"

    # width of frame around onboard when window decoration is disabled
    UNDECORATED_FRAME_WIDTH = 6.0

    # width of frame around popup windows
    POPUP_FRAME_WIDTH = 5.0

    # radius of the rounded window corners
    CORNER_RADIUS = 10

    # y displacement of the key face of dish keys
    DISH_KEY_Y_OFFSET = 0.8

    # raised border size of dish keys
    DISH_KEY_BORDER = (2.5, 2.5)

    # minimum time keys are drawn in pressed state
    UNPRESS_DELAY = 0.06
    
    # Margin to leave around wordlist labels; smaller margins leave
    # more room for prediction choices
    WORDLIST_LABEL_MARGIN = (2, 2)

    # Gap between wordlist buttons
    WORDLIST_BUTTON_SPACING = (0.5, 0.5)

    # Gap between wordlist predictions and correctios
    WORDLIST_ENTRY_SPACING = (1.0, 1.0)

    # threshold protect window move/resize
    drag_protection = True

    # Allow to iconify onboard when neither icon-palette nor
    # status-icon are enabled, else hide and show the window.
    # Iconifying is shaky in unity and gnome-shell. After unhiding
    # from launcher once, the WM won't allow to unminimize onboard
    # itself anymore for auto-show. (Precise)
    allow_iconifying = False

    # Gdk window scaling factor
    window_scaling_factor = 1.0

    _xembed_background_rgba = None

    _desktop_environment = None

    _source_path = None

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
        This constructor is still called multiple times.
        Do nothing here and use the singleton constructor construct() instead.
        Don't call base class constructors.
        """
        pass

    def construct(self):
        """
        Singleton constructor, runs only once.
        First intialization stage that runs before the
        single instance check. Only do the bare minimum here.
        """
        try:
            ver = version("onboard")
        except PackageNotFoundError:
            ver = "unknown"
        ver = ver.replace('.post', '-') 
        # parse command line
        parser = OptionParser(version=ver)
        group = OptionGroup(parser, "General Options")

        group.add_option("-l", "--layout", dest="layout",
                help=_format("Layout file ({}) or name",
                             self.LAYOUT_FILE_EXTENSION))
        group.add_option("-t", "--theme", dest="theme",
                help=_("Theme file (.theme) or name"))
        group.add_option("-s", "--size", dest="size",
                help=_("Window size, widthxheight"))
        group.add_option("-x", type="int", dest="x", help=_("Window x position"))
        group.add_option("-y", type="int", dest="y", help=_("Window y position"))
        parser.add_option_group(group)

        group = OptionGroup(parser, "Advanced Options")
        group.add_option("-e", "--xid", action="store_true", dest="xid_mode",
                help=_("Start in XEmbed mode, e.g. for gnome-screensaver"))
        group.add_option("-m", "--allow-multiple-instances",
                action="store_true", dest="allow_multiple_instances",
                help=_("Allow multiple Onboard instances"))
        group.add_option("--not-show-in", dest="not_show_in",
                metavar="DESKTOPS",
                help=_("Silently fail to start in the given desktop "
                       "environments. DESKTOPS is a comma-separated list of "
                       "XDG desktop names, e.g. GNOME for GNOME Shell."
                       ))
        group.add_option("-D", "--startup-delay",
                type="float", dest="startup_delay",
                help=_("Delay showing the initial keyboard window "
                       "by STARTUP_DELAY seconds (default 0.0)."))
        group.add_option("-a", "--keep-aspect", action="store_true",
                dest="keep_aspect_ratio",
                help=_("Keep aspect ratio when resizing the window"))
        group.add_option("-q", "--quirks", dest="quirks_name",
                help=_("Override auto-detection and manually select quirks\n"
                       "QUIRKS={metacity|compiz|mutter}"))
        parser.add_option_group(group)

        group = OptionGroup(parser, "Debug Options")
        group.add_option("-d", "--debug", type="str", dest="debug",
                         metavar="ARG",
            help="Set logging level or range of levels \n"
                 "ARG=MIN_LEVEL[-MAX_LEVEL]\n"
                 "LEVEL={all|event|atspi|debug|info|warning|error|\n"
                 "critical}\n")

        group.add_option("", "--launched-by", type="str", dest="launched_by",
                         metavar="ARG",
            help="Simulate being launched by certain XEmbed sockets. \n"
                 "Use this together with option --xid.               \n"
                 "ARG={unity-greeter|gnome-screen-saver}\n")

        group.add_option("-g", "--log-learning",
                  action="store_true", dest="log_learn", default=False,
                  help="log all learned text; off by default")

        parser.add_option_group(group)


        options = parser.parse_args()[0]
        self.options = options

        self.xid_mode = options.xid_mode
        self.log_learn = options.log_learn
        self.quirks_name = options.quirks_name
        self.quirks = None  # WMQuirks instance, provided by KbdWindow for now
        self.startup_delay = options.startup_delay

        # optionally log to file; everything, including stack traces
        if 0:
            options.debug = "debug"
            logfile = open("/tmp/onboard.log", "w")
            sys.stdout = logfile
            sys.stderr = logfile

        # setup logging
        min_level_name = None
        max_level_name = None
        if options.debug:
            level_range = options.debug.split("-")
            if len(level_range) >= 2:
                min_level_name, max_level_name = level_range[:2]
            else:
                min_level_name = level_range[0]
        self._init_logging(min_level_name, max_level_name)

        # Add basic config children for usage before the single instance check.
        # All the others are added in self._init_keys().
        self.children = []
        self.gnome_a11y = self.add_optional_child(ConfigGDA)
        if self.gnome_a11y:  # schema may not exist on Gentoo (LP: #1402558)
            self.gnome_a11y.init_from_gsettings()

        # detect who launched us
        self.launched_by = self.LAUNCHER_NONE
        if self.xid_mode:
            if options.launched_by:
                if options.launched_by == "gnome-screensaver":
                    self.launched_by = self.LAUNCHER_GSS
                elif options.launched_by == "unity-greeter":
                    self.launched_by = self.LAUNCHER_UNITY_GREETER
            else:
                if Process.was_launched_by("gnome-screensaver"):
                    self.launched_by = self.LAUNCHER_GSS
                elif "UNITY_GREETER_DBUS_NAME" in os.environ:
                    self.launched_by = self.LAUNCHER_UNITY_GREETER

        self.is_running_from_source = self._is_running_from_source()
        if self.is_running_from_source:
            _logger.warning("Starting in project directory, "
                            "importing local packages and extensions.")

    @staticmethod
    def isdesktop(id):

        xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        desktop = os.environ.get("DESKTOP_SESSION", "")

        def istrcmp(s1, s2):
            if sys.version_info < (3,3):
                return s1.lower() == s2.lower()
            return s1.casefold() == s2.casefold()

        return istrcmp(xdg_desktop, id) or istrcmp(desktop, id)

    def init(self):
        """
        Second initialization stage.
        Call this after single instance checking on application start.
        """

        # call base class constructor once logging is available
        try:
            ConfigObject.__init__(self)
        except SchemaError as e:
            _logger.error(unicode_str(e))
            sys.exit()

        # log desktop environment
        _logger.debug("Desktop environment: {}"
                      .format(self.get_desktop_environment().name))

        # log how we were launched
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("command line: " + str(sys.argv))
            #_logger.debug("environment: " + str(os.environ))
            cmdline = Process.get_launch_process_cmdline()
            _logger.debug("lauched by, process: '{}'".format(cmdline))
            _logger.debug("lauched by, detected: {}".format(self.launched_by))

        # init paths
        self.install_dir = self._get_install_dir()
        self.user_dir = self._get_user_dir()
        self._image_search_paths = []

        # migrate old user dir ".sok" or ".onboard" to XDG data home
        if not os.path.exists(self.user_dir):
            old_user_dir = os.path.join(os.path.expanduser("~"), ".onboard")
            if not os.path.exists(old_user_dir):
                old_user_dir = os.path.join(os.path.expanduser("~"), ".sok")
            if os.path.exists(old_user_dir):
                _logger.info(_format("Migrating user directory '{}' to '{}'.", \
                                     old_user_dir, self.user_dir))
                # Copy the data directory
                try:
                    copytree(old_user_dir, self.user_dir)
                    chmodtree(self.user_dir, 0o700, True) # honor XDG spec

                except OSError as ex: # python >2.5
                    _logger.error(_format("failed to migrate user directory. ") + \
                                  unicode_str(ex))

                # Return paths in gsettings to basenames. Custom path
                # information will be lost.
                try:
                    filter = lambda x: os.path.splitext(os.path.basename(x))[0]
                    s = Gio.Settings.new(SCHEMA_ONBOARD)
                    s["layout"] = filter(s["layout"])
                    s["theme"] = filter(s["theme"])
                    s["system-theme-associations"] = \
                        {k : filter(v) \
                         for k, v in s["system-theme-associations"].items()}
                except Exception as ex:
                    _logger.warning("migrating gsettings paths failed: " + \
                                    unicode_str(ex))

        # Migrate old user language model to language specific
        # model for Onboard 1.0.
        from Onboard.WPEngine import ModelCache
        old_fn = ModelCache.get_filename("lm:user:user")
        if os.path.exists(old_fn):
            lang_id = self.get_system_default_lang_id()
            new_fn = ModelCache.get_filename("lm:user:" + lang_id)
            old_bak = ModelCache.get_backup_filename(old_fn)
            new_bak = ModelCache.get_backup_filename(new_fn)

            _logger.info("Starting migration, user.lm has been deprecated.")
            for old, new in [[old_fn, new_fn], [old_bak, new_bak]]:
                if os.path.exists(old):
                    if os.path.exists(new):
                        _logger.info("Migration target already exists, "
                                     "skipping renaming "
                                     "'{}' to '{}'." \
                                     .format(old, new))
                        break # skip backup

                    _logger.info("Migrating user language model "
                                    "'{}' to '{}'." \
                                    .format(old, new))
                    try:
                        os.rename(old, new)
                    except OSError as ex:
                        _logger.error("Failed to migrate "
                                        "user language model. " + \
                                        unicode_str(ex))
                        break

        # Load system defaults (if there are any, not required).
        # Used for distribution specific settings, aka branding.
        paths = XDGDirs.get_all_config_dirs(USER_DIR) + \
                [self.install_dir, "/etc/onboard"]
        paths = [os.path.join(p, SYSTEM_DEFAULTS_FILENAME) for p in paths]
        self.load_system_defaults(paths)

        # initialize all property values
        used_system_defaults = self.init_properties(self.options)

        self._update_xembed_background_rgba()

        # Make sure there is a 'Default' entry when tracking the system theme.
        # 'Default' is the theme used when encountering a so far unknown
        # gtk-theme.
        theme_assocs = self.system_theme_associations.copy()
        if not "Default" in theme_assocs:
            theme_assocs["Default"] = ""
            self.system_theme_associations = theme_assocs

        # Remember command line theme for system theme tracking.
        if self.options.theme:
            self.theme_key.modified = True # force writing on next occasion
            self.remember_theme(self.theme)

        # load theme
        global Theme
        from Onboard.Appearance import Theme
        _logger.info("Theme candidates {}" \
                     .format(self._get_theme_candidates()))
        self.load_theme()

        # misc initializations
        self._last_snippets = dict(self.snippets)  # store a copy

        # init state of mousetweaks' click-type window
        if self.mousetweaks:
            self.mousetweaks.init_click_type_window_visible(
                self.universal_access.hide_click_type_window)

        # remember if we are running under GDM
        self.running_under_gdm = 'RUNNING_UNDER_GDM' in os.environ

        # tell config objects that their properties are valid now
        self.on_properties_initialized()

        # Work around changes in preferences having no effect in Saucy.
        # If there is an unbalanced self.delay() somewhere I haven't found it.
        self.apply()

        _logger.debug("Leaving init")

    def cleanup(self):
        # This used to stop dangling main windows from responding
        # when restarting. Restarts don't happen anymore, keep
        # this for now anyway.
        self.disconnect_notifications()
        if self.mousetweaks:
            self.mousetweaks.cleanup()

    def final_cleanup(self):
        if self.mousetweaks:
            self.mousetweaks.restore_click_type_window_visible(
                self.universal_access.enable_click_type_window_on_exit and \
                not self.xid_mode)

    def _init_logging(self, min_level_name, max_level_name):
        LEVEL_ATSPI = 6
        LEVEL_EVENT = 5

        levels = {
            # builtin levels
            "CRITICAL" : (50,          TermColors.RED),
            "ERROR"    : (40,          TermColors.RED),
            "WARNING"  : (30,          TermColors.YELLOW),
            "INFO"     : (20,          TermColors.GREEN),
            "DEBUG"    : (10,          TermColors.BLUE),

            # custom levels
            "ATSPI"    : (LEVEL_ATSPI, TermColors.CYAN),
            "EVENT"    : (LEVEL_EVENT, TermColors.MAGENTA),
            "ALL"      : ( 1,          None),
            "NOTSET"   : ( 0,          None),
        }
        for name, (level, color) in levels.items():
            if logging.getLevelName(level) != name:
                logging.addLevelName(level, name)

        class CustomLogger(logging.Logger):
            def __init__(self, *args):
                self.LEVEL_ATSPI = LEVEL_ATSPI
                self.LEVEL_EVENT = LEVEL_EVENT
                logging.Logger.__init__(self, *args)

            def atspi(self, msg, *args, **kwargs):
                if self.isEnabledFor(LEVEL_ATSPI):
                    self._log(LEVEL_ATSPI, msg, args, **kwargs)

            def event(self, msg, *args, **kwargs):
                if self.isEnabledFor(LEVEL_EVENT):
                    self._log(LEVEL_EVENT, msg, args, **kwargs)

        class CustomFilter(object):
            def __init__(self, max_level):
                self._max_level = max_level

            def filter(self, logRecord):
                return logRecord.levelno <= self._max_level

        class ColorFormatter(logging.Formatter):
            def __init__(self, msgfmt, log_levels, use_colors):
                self._log_levels = log_levels
                self._use_colors = use_colors
                self._tcs = TermColors() if use_colors else None

                msg = self._substitute_variables(msgfmt, use_colors)
                logging.Formatter.__init__(self, msg,
                                           datefmt="%H:%M:%S",
                                           style="{")

            def _substitute_variables(self, msg, use_colors):
                if use_colors:
                    msg = msg.replace("{RESET}",
                                      self._tcs.get(TermColors.RESET))
                    msg = msg.replace("{BOLD}",
                                      self._tcs.get(TermColors.BOLD))
                else:
                    msg = msg.replace("{RESET}", "")
                    msg = msg.replace("{BOLD}", "")
                return msg

            def formatMessage(self, record):
                level_name = record.levelname
                if self._use_colors and \
                   level_name in self._log_levels:
                    level, color = self._log_levels[level_name]
                    color_seq = self._tcs.get(color)
                else:
                    color_seq = ""
                record.__dict__["levelcolor"] = color_seq
                debug = bool(min_level_name)
                record.__dict__["name_width"] = 21 if debug else 1
                record.__dict__["level_width"] = 7 if debug else 1
                record.__dict__["name_"] = record.name + \
                                           ("" if debug else ":")
                return logging.Formatter.formatMessage(self, record)

        msgfmt = ("{asctime}.{msecs:03.0f} "
                  "{levelcolor}{levelname:{level_width}}{RESET} "
                  "{BOLD}{name_:{name_width}}{RESET} "
                  "{message}")

        handler = logging.StreamHandler()
        try:
            is_tty = handler.stream.isatty() # color only in interact. terminal
        except:
            is_tty = False
        handler.setFormatter(ColorFormatter(msgfmt, levels, is_tty))
        root = logging.getLogger()
        root.addHandler(handler)
        logging.setLoggerClass(CustomLogger)

        if min_level_name:
            root.setLevel(min_level_name.upper())

        if max_level_name:
            max_level_name = max_level_name.upper()
            if max_level_name in levels:
                max_level, color = levels[max_level_name]
                handler.addFilter(CustomFilter(max_level))

    def _init_keys(self):
        """ Create key descriptions """

        self.schema = SCHEMA_ONBOARD
        self.sysdef_section = "main"

        self.add_key("schema-version", "") # is assigned SCHEMA_VERSION on first start
        self.add_key("use-system-defaults", False)
        self.layout_key = \
        self.add_key("layout", DEFAULT_LAYOUT)
        self.theme_key  = \
        self.add_key("theme",  DEFAULT_THEME)
        self.add_key("system-theme-tracking-enabled", True)
        self.add_key("system-theme-associations", {}, 'a{ss}')
        self.add_key("snippets", {}, "as")
        self.add_key("show-status-icon", True)
        self.add_key("status-icon-provider", StatusIconProviderEnum.AppIndicator,
                                             enum={"auto" : 0,
                                                   "GtkStatusIcon" : 1,
                                                   "AppIndicator" : 2,
                                                  })
        self.add_key("start-minimized", False)
        self.add_key("show-tooltips", True)
        self.add_key("key-label-font", "")      # default font for all themes
        self.add_key("key-label-overrides", {}, "as") # default labels for all themes
        self.add_key("current-settings-page", 0)

        self.add_key("xembed-onboard", False, prop="onboard_xembed_enabled")
        self.add_key("xembed-aspect-change-range", [0, 1.6])
        self.add_key("xembed-background-color", "#0000007F")
        self.add_key("xembed-background-image-enabled", True)
        self.add_key("xembed-unity-greeter-offset-x", 85.0)

        self.keyboard          = ConfigKeyboard()
        self.window            = ConfigWindow()
        self.icp               = ConfigICP(self)
        self.auto_show         = ConfigAutoShow()
        self.universal_access  = ConfigUniversalAccess(self)
        self.theme_settings    = ConfigTheme(self)
        self.lockdown          = ConfigLockdown(self)
        self.scanner           = ConfigScanner(self)
        self.typing_assistance = ConfigTypingAssistance(self)
        self.gss               = ConfigGSS(self)
        self.gdi               = ConfigGDI(self)

        self.children += [self.keyboard,
                          self.window,
                          self.icp,
                          self.auto_show,
                          self.universal_access,
                          self.theme_settings,
                          self.lockdown,
                          self.gss,
                          self.gdi,
                          self.scanner,
                          self.typing_assistance]

        # mousetweaks (optional)
        for _class in [CSMousetweaks1, CSMousetweaks0]:
            try:
                self.mousetweaks = _class()
                self.children.append(self.mousetweaks)
                break
            except (SchemaError, ImportError, RuntimeError) as e:
                _logger.info(unicode_str(e))
                self.mousetweaks = None
        if self.mousetweaks is None:
            _logger.warning("mousetweaks GSettings schema not found, "
                            "mousetweaks integration disabled.")

        # unity greeter (very optional)
        self.unity_greeter = None
        if self.launched_by == self.LAUNCHER_UNITY_GREETER:
            try:
                self.unity_greeter = ConfigUnityGreeter(self)
                self.children.append(self.unity_greeter)
            except (SchemaError, ImportError) as e:
                _logger.warning(unicode_str(e))

    def init_from_gsettings(self):
        """
        Overloaded to migrate old dconf data to new gsettings schemas
        """
        ConfigObject.init_from_gsettings(self)

        # --- onboard 0.97 -> 0.98 --------------------------------------------
        format = Version.from_string(self.schema_version)
        if format < SCHEMA_VERSION_0_98:
            _logger.info("Migrating dconf values from before v0.98: "
                         "/apps/onboard -> /org/onboard")
            self.migrate_dconf_tree("apps.", "org.")

            # --- onboard 0.96 -> 0.97 ----------------------------------------
            format = Version.from_string(self.schema_version)
            if format < SCHEMA_VERSION_0_97:
                _logger.info("Migrating dconfs values from before v0.97")
                self._migrate_to_0_97()


        # --- onboard 1.0.0 -> 1.1.0-------------------------------------------
        format = Version.from_string(self.schema_version)
        if format < SCHEMA_VERSION_1_1:
            _logger.info("Migrating dconf values from before v1.1.0 ")
            self._migrate_to_1_1()

        # --- onboard 1.1.0 -> 1.2.0-------------------------------------------
        format = Version.from_string(self.schema_version)
        if format < SCHEMA_VERSION_1_2:
            _logger.info("Migrating dconf values from before v1.2.0 ")
            self._migrate_to_1_2()

        self.schema_version = SCHEMA_VERSION.to_string()

    def _migrate_to_1_2(self):
        """
        reposition-method split into reposition-method-floating
        and reposition-method-docked
        """
        co = self.auto_show
        co.delay()
        co.migrate_dconf_key("/org/onboard/auto-show/reposition-method",
                             "reposition-method-floating")
        co.migrate_dconf_key("/org/onboard/auto-show/reposition-method",
                             "reposition-method-docked")
        co.apply()

    def _migrate_to_1_1(self):
        """ resize_handles renamed to window_handles and movement added """

        def migrate_resize_handles(co, dconf_path, key):
            gskey = co.find_key(key)

            co.delay()
            co.migrate_dconf_value(dconf_path, gskey)

            # add new "MOVE" entry
            handles = getattr(co, gskey.prop)[:]
            if not Handle.MOVE in handles:
                handles += (Handle.MOVE,)
            setattr(co, gskey.prop, handles)

            co.apply()

        migrate_resize_handles(self.window,
                               "/org/onboard/window/resize-handles",
                               "window-handles")
        migrate_resize_handles(self.icp,
                               "/org/onboard/icon-palette/resize-handles",
                               "window-handles")

    def _migrate_to_0_97(self):
        # window rect moves from org.onboard to
        # org.onboard.window.landscape/portrait
        co = self.window.landscape
        if co.gskeys["x"].is_default() and \
           co.gskeys["y"].is_default() and \
           co.gskeys["width"].is_default() and \
           co.gskeys["height"].is_default():

            co.delay()
            co.migrate_dconf_value("/apps/onboard/x", co.gskeys["x"])
            co.migrate_dconf_value("/apps/onboard/y", co.gskeys["y"])
            co.migrate_dconf_value("/apps/onboard/width", co.gskeys["width"])
            co.migrate_dconf_value("/apps/onboard/height", co.gskeys["height"])
            co.apply()

        # icon-palette rect moves from org.onboard.icon-palette to
        # org.onboard.icon-palette.landscape/portrait
        co = self.icp.landscape
        if co.gskeys["x"].is_default() and \
           co.gskeys["y"].is_default() and \
           co.gskeys["width"].is_default() and \
           co.gskeys["height"].is_default():

            co.delay()
            co.migrate_dconf_value("/apps/onboard/icon-palette/x", co.gskeys["x"])
            co.migrate_dconf_value("/apps/onboard/icon-palette/y", co.gskeys["y"])
            co.migrate_dconf_value("/apps/onboard/icon-palette/width", co.gskeys["width"])
            co.migrate_dconf_value("/apps/onboard/icon-palette/height", co.gskeys["height"])
            co.apply()

        # move keys from root to window
        co = self.window
        co.migrate_dconf_key("/apps/onboard/window-decoration", "window-decoration")
        co.migrate_dconf_key("/apps/onboard/force-to-top", "force-to-top")
        co.migrate_dconf_key("/apps/onboard/transparent-background", "transparent-background")
        co.migrate_dconf_key("/apps/onboard/transparency", "transparency")
        co.migrate_dconf_key("/apps/onboard/background-transparency", "background-transparency")
        co.migrate_dconf_key("/apps/onboard/enable-inactive-transparency", "enable-inactive-transparency")
        co.migrate_dconf_key("/apps/onboard/inactive-transparency", "inactive-transparency")
        co.migrate_dconf_key("/apps/onboard/inactive-transparency-delay", "inactive-transparency-delay")

        # accessibility keys move from root to universal-access
        co = self.universal_access
        co.migrate_dconf_key("/apps/onboard/hide-click-type-window", "hide-click-type-window")
        co.migrate_dconf_key("/apps/onboard/enable-click-type-window-on-exit", "enable-click-type-window-on-exit")

        # move keys from root to keyboard
        co = self.keyboard
        co.migrate_dconf_key("/apps/onboard/show-click-buttons", "show-click-buttons")

    ##### handle special keys only valid in system defaults #####
    def _read_sysdef_section(self, parser):
        super(self.__class__, self)._read_sysdef_section(parser)

        # Convert the simplified superkey_label setting into
        # the more general key_label_overrides setting.
        sds = self.system_defaults
        if "superkey_label" in sds:
            overrides = sds.get( "key_label_overrides", {})
            group = self.SUPERKEY_SIZE_GROUP \
                if sds.get("superkey_label_independent_size") else ""
            for key_id in ["LWIN", "RWIN"]:
                overrides[key_id] = (sds["superkey_label"], group)
            sds["key_label_overrides"] = overrides

    def _convert_sysdef_key(self, gskey, sysdef, value):
        # key exclusive to system defaults?
        if sysdef in ["superkey-label",
                      "superkey-label-independent-size"]:
            return value
        else:
            return super(self.__class__, self). \
                         _convert_sysdef_key(gskey, sysdef, value)


    ##### property helpers #####
    def _unpack_key_label_overrides(self, value):
        return self.unpack_string_list(value, "a{s[ss]}")

    def _pack_key_label_overrides(self, value):
        return self.pack_string_list(value)

    def _unpack_snippets(self, value):
        return self.unpack_string_list(value, "a{i[ss]}")

    def _pack_snippets(self, value):
        return self.pack_string_list(value)

    def _post_notify_layout(self):
        self._invalidate_layout_resource_search_paths()

    # Property layout_filename, linked to gsettings key "layout".
    # layout_filename may only get/set a valid filename,
    # whereas layout also allows to get/set only the basename of a layout.
    def layout_filename_notify_add(self, callback):
        self.layout_notify_add(callback)

    def get_layout_filename(self):
        gskey = self.layout_key
        return self.find_layout_filename(gskey.value, gskey.key,
                                     self.LAYOUT_FILE_EXTENSION,
                                     os.path.join(self.install_dir,
                                                  "layouts", DEFAULT_LAYOUT +
                                                  self.LAYOUT_FILE_EXTENSION))

    def set_layout_filename(self, filename):
        if filename and os.path.exists(filename):
            self.layout = filename
        else:
            _logger.warning(_format("layout '{filename}' does not exist", \
                                    filename=filename))

    layout_filename = property(get_layout_filename, set_layout_filename)

    def get_fallback_layout_filename(self):
        """ Layout file to fallback to when the initial layout won't load """
        return self.find_layout_filename(DEFAULT_LAYOUT, "layout",
                                         self.LAYOUT_FILE_EXTENSION)

    def find_layout_filename(self, filename, description,
                                    extension = "", final_fallback = ""):
        """ Find layout file, either the root layout or an include file. """
        return self._get_user_sys_filename(
             filename    = filename,
             description = description,
             user_filename_func   = lambda x: \
                 os.path.join(self.user_dir,    "layouts", x) + extension,
             system_filename_func = lambda x: \
                 os.path.join(self.install_dir, "layouts", x) + extension,
             final_fallback       = final_fallback)

    # Property theme_filename, linked to gsettings key "theme".
    # theme_filename may only get/set a valid filename,
    # whereas theme also allows to get/set only the basename of a theme.
    def theme_filename_notify_add(self, callback):
        self.theme_notify_add(callback)

    def get_theme_filename(self):
        candidates = self._get_theme_candidates()
        for theme in candidates:
            if theme:
                filename = self._expand_theme_filename(theme)
                if filename:
                    return filename
        return ""

    def set_theme_filename(self, filename):
        if filename and os.path.exists(filename):
            self.remember_theme(filename)
        else:
            _logger.warning(_format("theme '{filename}' does not exist", \
                                    filename=filename))

    theme_filename = property(get_theme_filename, set_theme_filename)

    def _expand_theme_filename(self, theme):
        """ expand generic theme name """
        return self._expand_user_sys_filename(theme,
             user_filename_func   = Theme.build_user_filename,
             system_filename_func = Theme.build_system_filename)

    def remember_theme(self, theme_filename):
        if self.system_theme_tracking_enabled and \
           self.gdi:   # be defensive
            gtk_theme = self.get_gtk_theme()
            theme_assocs = self.system_theme_associations.copy()
            theme_assocs[gtk_theme] = theme_filename
            self.system_theme_associations = theme_assocs

        self.set_theme(theme_filename)

    def apply_theme(self, filename = None):
        if not filename:
            filename = self.get_theme_filename()

        _logger.info(_format("Loading theme from '{}'", filename))

        theme = Theme.load(filename)
        if not theme:
            _logger.error(_format("Unable to read theme '{}'", filename))
        else:
            # Save to gsettings
            # Make sure gsettings is in sync with onboard (LP: 877601)
            #self.theme = filename
            theme.apply()

            # Fix theme not saved to gesettings when switching
            # system contrast themes.
            # Possible gsettings bug in Precise (wasn't in Oneiric).
            #self.apply()

        return bool(theme)

    def load_theme(self):
        """
        Figure out which theme to load and load it.
        """
        self.apply_theme()

    def _get_theme_candidates(self):
        """
        Return a list of themes to consider for loading.
        Highest priority first.
        """
        candidates = []

        if self.system_theme_tracking_enabled:
            theme_assocs = self.system_theme_associations
            gtk_theme = self.get_gtk_theme()

            candidates += [theme_assocs.get(gtk_theme, ""),
                           theme_assocs.get("Default", ""),
                           self.theme]
        else:
            candidates += ["",
                           "",
                           self.theme]

        if self.system_defaults:
            theme = self.system_defaults.get("theme", "")
        else:
            theme = ""
        candidates.append(theme)

        candidates.append(DEFAULT_THEME)

        return candidates

    def get_gtk_theme(self):
        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings:   # be defensive, don't know if this can fail
            gtk_theme = gtk_settings.get_property('gtk-theme-name')
            return gtk_theme
        return None

    def get_image_filename(self, image_filename):
        """
        Returns an absolute path for a label image.
        This function isn't linked to any gsettings key.
        """
        if not self._image_search_paths:
            self._image_search_paths = \
                self._get_layout_resource_search_paths("images") + \
                self._get_emojione_image_dirs()

        return self._get_resource_filename(image_filename, "image",
                                           self._image_search_paths)

    def _invalidate_layout_resource_search_paths(self):
        """ Force re-generation of search paths at next opportunity. """
        self._image_search_paths = []

    def _get_layout_resource_search_paths(self, subdir):
        """
        Look for files in directory of current layout, user- and
        system directory, in that order.
        """
        user_dir = os.path.join(self.user_dir, "layouts")
        sys_dir = os.path.join(self.install_dir, "layouts")
        if subdir:
            user_dir = os.path.join(user_dir, subdir)
            sys_dir = os.path.join(sys_dir, subdir)

        paths = [user_dir, sys_dir]

        # In case a full path was given for the layout file on command line,
        # look relative to there first.
        if self._is_valid_filename(self.layout):
            dir_ = os.path.dirname(self.layout)
            if subdir:
                dir_ = os.path.join(dir_, "images")
            try:
                # make sure it isn't one of those we already know
                same = os.path.isdir(user_dir) and \
                       os.path.samefile(dir_, sys_dir) or \
                       os.path.isdir(user_dir) and \
                       os.path.samefile(dir_, user_dir)
            except Exception as ex:
                _logger.error("samefile failed with '{}': {}" \
                              .format(dir_, unicode_str(ex)))
                same = True

            if not same:
                paths = [dir_] + paths

        return paths

    def get_user_model_dir(self):
        return os.path.join(self.user_dir, "models")

    def get_system_model_dir(self):
        return os.path.join(self.install_dir, "models")

    def enable_hover_click(self, enable):
        hide = self.universal_access.hide_click_type_window
        if enable:
            self.mousetweaks.allow_system_click_type_window(False, hide)
            self.mousetweaks.set_active(True)
        else:
            self.mousetweaks.set_active(False)
            self.mousetweaks.allow_system_click_type_window(True, hide)

    def is_hover_click_active(self):
        return bool(self.mousetweaks) and self.mousetweaks.is_active()

    def is_visible_on_start(self):
        return self.xid_mode or \
               not self.start_minimized and \
               not self.auto_show.enabled

    def is_auto_show_enabled(self):
        return not self.xid_mode and \
               self.auto_show.enabled

    def is_auto_hide_enabled(self):
        return self.is_auto_hide_on_keypress_enabled() or \
            self.is_tablet_mode_detection_enabled() or \
            self.is_keyboard_device_detection_enabled()

    def is_auto_hide_on_keypress_enabled(self):
        return self.can_set_auto_hide() and \
            self.auto_show.hide_on_key_press

    def is_tablet_mode_detection_enabled(self):
        return self.can_set_auto_hide() and \
            self.auto_show.tablet_mode_detection_enabled

    def is_keyboard_device_detection_enabled(self):
        return self.can_set_auto_hide() and \
            self.auto_show.keyboard_device_detection_enabled

    def can_auto_show_reposition(self):
        return self.is_auto_show_enabled() and \
            self.get_auto_show_reposition_method() != RepositionMethodEnum.NONE

    def get_auto_show_reposition_method(self):
        if self.is_docking_enabled():
            return self.auto_show.reposition_method_docked
        else:
            return self.auto_show.reposition_method_floating

    def can_set_auto_hide(self):
        """ Allowed to change auto hide? """
        return self.is_auto_show_enabled() and \
               self.is_event_source_xinput()

    def is_force_to_top(self):
        return self.window.force_to_top or self.is_docking_enabled()

    def is_override_redirect(self):
        return self.is_force_to_top() and \
               self.quirks.can_set_override_redirect(self)

    def is_docking_enabled(self):
        # Disable docking when x/y coordinates were explicitly provided
        # via command line to respect user's positioning choice
        if self.options and (self.options.x is not None or self.options.y is not None):
            return False
        return self.window.docking_enabled

    def is_dock_expanded(self, orientation_co):
        return self.window.docking_enabled and orientation_co.dock_expand

    def are_word_suggestions_enabled(self):
        return self.word_suggestions.enabled and not self.xid_mode

    def are_spelling_suggestions_enabled(self):
        return self.are_word_suggestions_enabled() and \
               self.word_suggestions.spelling_suggestions_enabled

    def is_spell_checker_enabled(self):
        return self.are_spelling_suggestions_enabled() or \
               self.typing_assistance.auto_capitalization or \
               self.typing_assistance.auto_correction

    def is_auto_capitalization_enabled(self):
        return self.typing_assistance.auto_capitalization and not self.xid_mode

    def is_typing_assistance_enabled(self):
        return self.are_word_suggestions_enabled() or \
               self.is_auto_capitalization_enabled()

    def is_event_source_gtk(self):
        return self.keyboard.input_event_source == InputEventSourceEnum.GTK

    def is_event_source_xinput(self):
        return self.keyboard.input_event_source == InputEventSourceEnum.XINPUT

    def check_gnome_accessibility(self, parent = None):
        if not (self.isdesktop("GNOME") or \
                self.isdesktop("GNOME-Classic:GNOME") or \
                self.isdesktop("Unity") or \
                self.isdesktop("X-Cinnamon") or \
                self.isdesktop("cinnamon") or \
                ctypes.util.find_library("atspi") \
               ):
            return False
        if not self.xid_mode and \
           not self.gdi.toolkit_accessibility:
            question = _("Enabling auto-show requires Gnome Accessibility.\n\n"
                         "Onboard can turn on accessiblity now, however it is "
                         "recommended that you log out and back in "
                         "for it to reach its full potential.\n\n"
                         "Enable accessibility now?")
            reply = show_confirmation_dialog(question, parent,
                                             self.is_force_to_top())
            if not reply == True:
                return False

            self.gdi.toolkit_accessibility = True

        return True

    def get_drag_threshold(self):
        threshold = self.universal_access.gskeys["drag_threshold"].value
        if threshold == -1:
            # get the systems DND threshold
            threshold = Gtk.Settings.get_default(). \
                                    get_property("gtk-dnd-drag-threshold")
        return threshold

    def is_icon_palette_in_use(self):
        """
        Show icon palette when there is no other means to unhide onboard.
        Unhiding by unity launcher isn't available in force-to-top mode.
        """
        return self.icp.in_use or self.is_icon_palette_last_unhide_option()

    def is_icon_palette_last_unhide_option(self):
        """
        Is the icon palette the last remaining way to unhide onboard?
        Consider single instance check a way to always unhide onboard.
        """
        return False

    def has_unhide_option(self):
        """
        No native ui visible to unhide onboard?
        There might still be the launcher to unminimize it.
        """
        return self.is_icon_palette_in_use() or self.show_status_icon

    def has_window_decoration(self):
        """ Force-to-top mode doesn't support window decoration """
        return self.window.window_decoration and not self.is_force_to_top()

    def get_sticky_state(self):
        return not self.xid_mode and \
               (self.window.window_state_sticky or self.is_force_to_top())

    def is_inactive_transparency_enabled(self):
        return self.window.enable_inactive_transparency and \
               not self.scanner.enabled

    def is_keep_window_aspect_ratio_enabled(self, orientation_co):
        """
        Keep aspect ratio of the whole keyboard window?
        Not recommended, no effect in MATE and elsewhere the
        keyboard tends to shrink with each interaction.
        """
        return ((self.window.keep_aspect_ratio or
                 self.options.keep_aspect_ratio) and
                not self.xid_mode and
                not self.is_docking_enabled())

    def is_keep_frame_aspect_ratio_enabled(self, orientation_co):
        """
        Keep aspect ratio of only the frame (keyboard area)
        inside the keyboard window?
        """
        return \
            self.is_keep_xembed_frame_aspect_ratio_enabled() or \
            self.is_keep_docking_frame_aspect_ratio_enabled(orientation_co)

    def is_keep_xembed_frame_aspect_ratio_enabled(self):
        return self.xid_mode and self.launched_by != self.LAUNCHER_NONE

    def is_keep_docking_frame_aspect_ratio_enabled(self, orientation_co):
        return (not self.xid_mode and
                self.is_docking_enabled() and
                self.is_dock_expanded(orientation_co))

    def is_mousetweaks_active(self):
        return self.mousetweaks and self.mousetweaks.is_active()

    ####### window handles (resize & move handles) #######
    def window_handles_notify_add(self, callback):
        self.window.window_handles_notify_add(callback)
        self.icp.window_handles_notify_add(callback)

    def window_handles_to_num_handles(self, handles):
        """ Translate array of handles to simplified NumResizeHandles enum """
        if len(handles) == 0:
            return NumResizeHandles.NONE
        if len(handles) == 1 and handles[0] == Handle.MOVE:
            return NumResizeHandles.NORESIZE
        if len(handles) == 8 + 1:
            return NumResizeHandles.ALL
        return NumResizeHandles.SOME

    def num_handles_to_window_handles(self, num):
        if num == NumResizeHandles.ALL:
            handles = list(Handle.RESIZE_MOVE)
        elif num == NumResizeHandles.NORESIZE:
            handles = [Handle.MOVE]
        elif num == NumResizeHandles.NONE:
            handles = []
        else:  # NumResizeHandles.SOME
            handles = list(Handle.CORNERS + (Handle.MOVE, ))
        return handles

    def num_handles_to_icon_palette_handles(self, num):
        handles = self.num_handles_to_window_handles(num)
        if num == NumResizeHandles.SOME:
            handles = [Handle.SOUTH_EAST, Handle.MOVE]
        return handles

    @staticmethod
    def _string_to_handles(string):
        """ String of handle ids to array of Handle enums """
        ids = string.split()
        handles = []
        for id in ids:
            handle = Handle.RIDS.get(id)
            if not handle is None:
                handles.append(handle)
        return handles

    @staticmethod
    def _handles_to_string(handles):
        """ Array of handle enums to string of handle ids """
        ids = []
        for handle in handles:
            ids.append(Handle.IDS[handle])
        return " ".join(ids)

    ####### Snippets editing #######
    def set_snippet(self, index, value):
        """
        Set a snippet in the snippet list.  Enlarge the list if not big
        enough.

        @type  index: int
        @param index: index of the snippet to set.
        @type  value: str
        @param value: Contents of the new snippet.
        """
        if value == None:
            raise TypeError("Snippet text must be str")

        label, text = value
        snippets = dict(self.snippets) # copy to enable callbacks
        _logger.info("Setting snippet %d to '%s', '%s'" % (index, label, text))
        snippets[index] = (label, text)
        self.snippets = snippets

    def del_snippet(self, index):
        """
        Delete a snippet.

        @type  index: int
        @param index: index of the snippet to delete.
        """
        _logger.info("Deleting snippet %d" % index)
        snippets = dict(self.snippets) # copy to enable callbacks
        del snippets[index]
        self.snippets = snippets


    ###### gnome-screensaver, xembedding #####
    def enable_gss_embedding(self, enable):
        if enable:
            self.onboard_xembed_enabled = True
            self.gss.embedded_keyboard_enabled = True
            self.set_xembed_command_string_to_onboard()
        else:
            self.onboard_xembed_enabled = False
            self.gss.embedded_keyboard_enabled = False

    def is_onboard_in_xembed_command_string(self):
        """
        Checks whether the gsettings key for the embeded application command
        contains the entry defined by onboard.
        Returns True if it is set to onboard and False otherwise.
        """
        if self.gss.embedded_keyboard_command.startswith(START_ONBOARD_XEMBED_COMMAND):
            return True
        else:
            return False

    def set_xembed_command_string_to_onboard(self):
        """
        Write command to start the embedded onboard into the corresponding
        gsettings key.
        """
        self.gss.embedded_keyboard_command = START_ONBOARD_XEMBED_COMMAND

    def _get_kbd_render_mixin(self):
        __import__(self._kbd_render_mixin_mod)
        return getattr(sys.modules[self._kbd_render_mixin_mod],
                self._kbd_render_mixin_cls)
    kbd_render_mixin = property(_get_kbd_render_mixin)


    # modeless gksu - disabled until gksu moves to gsettings
    def modeless_gksu_notify_add(self, callback):
        pass
    modeless_gksu = property(lambda self: False)


    def get_desktop_background_filename(self):
        fn = ""

        # Starting with Vivid's unity greeter try to get the filename
        # from the greeter's schema.
        if self.launched_by == self.LAUNCHER_UNITY_GREETER:
            fn = self._get_desktop_background_filename_from_schema(
                     "com.canonical.unity-greeter", "background")

        # Elsewhere (old Ubuntu releases, gnome-screen-saver) get it
        # from the gnome key.
        if not fn:
            fn = self._get_desktop_background_filename_from_schema(
                     "org.gnome.desktop.background", "picture-uri")
        return fn

    def _get_desktop_background_filename_from_schema(self, schema, key):
        try:
            s = Gio.Settings.new(schema)
            fn = s.get_string(key)
        except Exception as ex: # private exception gi._glib.GError
            fn = ""
            _logger.error("failed to read desktop background from {} {}: {}" \
                          .format(schema, key, unicode_str(ex)))
        if fn:
            try:
                # Valid file URI?
                # Prevents error 'not an absolute URI using the "file" scheme'
                # when using the unity-greeter schema.
                if GLib.uri_parse_scheme(fn) == "file":
                    try:
                        fn, error = GLib.filename_from_uri(fn)
                    except TypeError: # broken introspection on Precise
                        fn = GLib.filename_from_uri(fn, "")
                        error = ""
                    if error:
                        fn = ""
            except Exception as ex: # private exception gi._glib.GError
                _logger.error("failed to unescape URI for desktop background "
                              "'{}': {}" \
                            .format(fn, unicode_str(ex)))
        return fn

    def get_xembed_unity_greeter_offset_x(self):
        value = self.gskeys["xembed_unity_greeter_offset_x"].value
        if value < 0:
            value = None
        return value

    def get_xembed_background_rgba(self):
        return self._xembed_background_rgba

    def _update_xembed_background_rgba(self):
        value = self.xembed_background_color
        self._xembed_background_rgba = hexcolor_to_rgba(value)

    def _is_running_from_source(self):
        return bool(self._get_source_path())

    def _get_source_path(self):
        if self._source_path is None:

            candidates = [
                os.path.abspath(os.path.curdir),
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ]

            self._source_path = ""
            for path in candidates:
                data_path = os.path.join(path, "data")
                fn = os.path.join(data_path, "org.onboard.gschema.xml")
                if os.path.isfile(fn):
                    self._source_path = path
                    break

        return self._source_path

    def _get_install_dir(self):
        result = None

        # when run from source
        if self._is_running_from_source():
            # Add the data directory to the icon search path
            icon_theme = Gtk.IconTheme.get_default()
            src_path = self._get_source_path()
            src_icon_path = os.path.join(src_path, "icons")
            icon_theme.append_search_path(src_icon_path)
            result = src_path
        # when installed to /usr/local
        elif os.path.exists(os.path.join(LOCAL_INSTALL_DIR, "themes", "Classic Onboard.theme")):
            result = LOCAL_INSTALL_DIR
        # when installed to /usr
        elif os.path.exists(os.path.join(INSTALL_DIR, "themes", "Classic Onboard.theme")):
            result = INSTALL_DIR

        assert(result)  # warn early when the installation dir wasn't found
        return result

    def _get_user_dir(self):
        return XDGDirs.get_data_home(USER_DIR)

    def get_user_layout_dir(self):
        return os.path.join(self.user_dir, "layouts/")

    def _get_emojione_image_dirs(self):
        return [os.path.join(self.install_dir, "emojione", "svg")]

    def get_system_default_lang_id(self):
        lang_id = locale.getdefaultlocale()[0]
        if not lang_id:  # None e.g. with LANG=C
            lang_id = "en_US"
        return lang_id

    def get_desktop_environment(self):
        """
        Return current desktop environment.
        """
        if not self._desktop_environment:
            self._desktop_environment = self._detect_desktop_environment()
        return self._desktop_environment

    @staticmethod
    def _detect_desktop_environment():
        """
        Detect current desktop environment. Extend as needed.
        """
        isdesktop = Config.isdesktop

        if isdesktop("X-Cinnamon") or isdesktop("cinnamon"):
            return DesktopEnvironmentEnum.Cinnamon
        if isdesktop("GNOME"):
            return DesktopEnvironmentEnum.GNOME_Shell
        if isdesktop("GNOME-Classic:GNOME"):
            return DesktopEnvironmentEnum.GNOME_Classic
        if isdesktop("KDE"):
            return DesktopEnvironmentEnum.KDE
        if isdesktop("lxde"):
            return DesktopEnvironmentEnum.LXDE
        if isdesktop("LXQt"):
            return DesktopEnvironmentEnum.LXQT
        if isdesktop("mate"):
            return DesktopEnvironmentEnum.MATE
        if isdesktop("Unity"):
            return DesktopEnvironmentEnum.Unity
        if isdesktop("xfce"):
            return DesktopEnvironmentEnum.XFCE

        return DesktopEnvironmentEnum.Unknown

    def get_preferred_statusicon_provider(self):
        """
        Auto-detect if we should fall back to GtkStatusIcon.
        """
        result = StatusIconProviderEnum.AppIndicator

        de = self.get_desktop_environment()

        # Gnome-shell annoys with sliding in their legacy icon panel.
        # We have our indicator extension now, so turn the status icon off.
        if de in (DesktopEnvironmentEnum.GNOME_Shell, ):
            result = None

        elif de in (
            # AppIndicator is supported in XUbuntu 16.04, but w/o left click
            # activation. GtkStatusIcon works well and allows left click.
            DesktopEnvironmentEnum.Cinnamon,

            # AppIndicator does nothing in 16.04.
            DesktopEnvironmentEnum.MATE,

            # AppIndicator does nothing in 16.04.
            DesktopEnvironmentEnum.XFCE,

            # AppIndicator is supported in Lubuntu 16.04, but w/o left
            # click activation. GtkStatusIcon works well, but is placed
            # into a legacy system tray applet with tiny 16x16 icon.
            # DesktopEnvironmentEnum.LXDE,

            # AppIndicator is supported in LXQt 16.04, including left
            # click activation. GtkStatusIcon works well too.
            # DesktopEnvironmentEnum.LXQT,
        ):
            result = StatusIconProviderEnum.GtkStatusIcon

        return result


class ConfigKeyboard(ConfigObject):
    """Window configuration """
    DEFAULT_KEY_ACTION = 1 # Release-only, supports long press
    DEFAULT_KEY_SYNTH  = 0 # Auto
    DEFAULT_TOUCH_INPUT = TouchInputEnum.MULTI
    DEFAULT_INPUT_EVENT_SOURCE = InputEventSourceEnum.XINPUT

    def _init_keys(self):
        self.schema = SCHEMA_KEYBOARD
        self.sysdef_section = "keyboard"

        self.add_key("show-click-buttons", False)
        self.add_key("sticky-key-release-delay", 0.0)
        self.add_key("sticky-key-release-on-hide-delay", 5.0)
        self.add_key("sticky-key-behavior", {"all" : "cycle"}, 'a{ss}')
        self.add_key("long-press-delay", 0.5)
        self.add_key("default-key-action", self.DEFAULT_KEY_ACTION,
                                           enum={"single-stroke" : 0,
                                                 "delayed-stroke" : 1,
                                                })
        self.add_key("key-synth", self.DEFAULT_KEY_SYNTH,
                                           enum={"auto" : 0,
                                                 "XTest" : 1,
                                                 "uinput" : 2,
                                                 "AT-SPI" : 3,
                                                })
        self.add_key("touch-input", self.DEFAULT_TOUCH_INPUT,
                                           enum={"none" : 0,
                                                 "single" : 1,
                                                 "multi" : 2,
                                                })
        self.add_key("input-event-source", self.DEFAULT_INPUT_EVENT_SOURCE,
                                           enum={"GTK" : 0,
                                                 "XInput" : 1,
                                                })
        self.add_key("touch-feedback-enabled", False)
        self.add_key("touch-feedback-size", 0)
        self.add_key("audio-feedback-enabled", False)
        self.add_key("audio-feedback-place-in-space", False)
        self.add_key("show-secondary-labels", False)

        self.add_key("inter-key-stroke-delay", 0.0)
        self.add_key("modifier-update-delay", 1.0)

        self.add_key("popup-duration", 0.2)

        self.add_key("key-press-modifiers", {"button3" : "SHIFT"}, 'a{ss}')

    def can_upper_case_on_button(self, button):
        kpms = self.key_press_modifiers
        if not kpms:  # speed up the empty case
            return False
        key = "button" + str(button)
        return kpms.get(key) == "SHIFT"

    def set_upper_case_on_button(self, button, on):
        key = "button" + str(button)
        values = dict(self.key_press_modifiers)  # must be a copy
        if on:
            values[key] = "SHIFT"
        else:
            if key in values:
                del values[key]
        self.key_press_modifiers = values


class ConfigWindow(ConfigObject):
    """Window configuration """
    DEFAULT_DOCKING_EDGE = DockingEdge.BOTTOM
    DEFAULT_DOCKING_MONITOR = DockingMonitor.ACTIVE

    def _init_keys(self):
        self.schema = SCHEMA_WINDOW
        self.sysdef_section = "window"

        self.add_key("window-state-sticky", True)
        self.add_key("window-decoration", False)
        self.add_key("force-to-top", False)
        self.add_key("keep-aspect-ratio", False)
        self.add_key("transparent-background", False)
        self.add_key("transparency", 0.0)
        self.add_key("background-transparency", 10.0)
        self.add_key("enable-inactive-transparency", False)
        self.add_key("inactive-transparency", 50.0)
        self.add_key("inactive-transparency-delay", 1.0)
        self.add_key("window-handles", DEFAULT_WINDOW_HANDLES)
        self.add_key("docking-enabled", False)
        self.add_key("docking-edge", self.DEFAULT_DOCKING_EDGE,
                                     enum={"top"    : DockingEdge.TOP,
                                           "bottom" : DockingEdge.BOTTOM,
                                          })
        self.add_key("docking-monitor", self.DEFAULT_DOCKING_MONITOR,
                                     enum={"active" : DockingMonitor.ACTIVE,
                                           "primary" : DockingMonitor.PRIMARY,
                                           "monitor0" : DockingMonitor.MONITOR0,
                                           "monitor1" : DockingMonitor.MONITOR1,
                                           "monitor2" : DockingMonitor.MONITOR2,
                                           "monitor3" : DockingMonitor.MONITOR3,
                                           "monitor4" : DockingMonitor.MONITOR4,
                                           "monitor5" : DockingMonitor.MONITOR5,
                                           "monitor6" : DockingMonitor.MONITOR6,
                                           "monitor7" : DockingMonitor.MONITOR7,
                                           "monitor8" : DockingMonitor.MONITOR8,
                                          })
        self.add_key("docking-shrink-workarea", True)
        self.add_key("docking-aspect-change-range", [0, 1.6], "ad")

        self.landscape = ConfigWindow.Landscape(self)
        self.portrait = ConfigWindow.Portrait(self)

        self.children = [self.landscape, self.portrait]

    ##### property helpers #####
    def _convert_sysdef_key(self, gskey, sysdef, value):
        if sysdef == "resize-handles" or \
           sysdef == "window-handles":
            return Config._string_to_handles(value)
        else:
            return ConfigObject._convert_sysdef_key(self, gskey, sysdef, value)

    def _unpack_window_handles(self, value):
        return Config._string_to_handles(value)

    def _pack_window_handles(self, value):
        return Config._handles_to_string(value)

    def position_notify_add(self, callback):
        self.landscape.x_notify_add(callback)
        self.landscape.y_notify_add(callback)
        self.portrait.x_notify_add(callback)
        self.portrait.y_notify_add(callback)

    def size_notify_add(self, callback):
        self.landscape.width_notify_add(callback)
        self.landscape.height_notify_add(callback)
        self.portrait.width_notify_add(callback)
        self.portrait.height_notify_add(callback)

    def dock_size_notify_add(self, callback):
        self.landscape.dock_width_notify_add(callback)
        self.landscape.dock_height_notify_add(callback)
        self.portrait.dock_width_notify_add(callback)
        self.portrait.dock_height_notify_add(callback)

    def docking_notify_add(self, callback):
        self.docking_enabled_notify_add(callback)
        self.docking_edge_notify_add(callback)
        self.docking_monitor_notify_add(callback)
        self.docking_shrink_workarea_notify_add(callback)

        self.landscape.dock_expand_notify_add(callback)
        self.portrait.dock_expand_notify_add(callback)

    def get_active_opacity(self):
        return 1.0 - self.transparency / 100.0

    def get_inactive_opacity(self):
        return 1.0 - self.inactive_transparency / 100.0

    def get_minimal_opacity(self):
        # Return the lowest opacity the window can have when visible.
        return min(self.get_active_opacity(), self.get_inactive_opacity())

    def get_background_opacity(self):
        return 1.0 - self.background_transparency / 100.0

    class Landscape(ConfigObject):
        def _init_keys(self):
            self.schema = SCHEMA_WINDOW_LANDSCAPE
            self.sysdef_section = "window.landscape"

            self.add_key("x", DEFAULT_X)
            self.add_key("y", DEFAULT_Y)
            self.add_key("width", DEFAULT_WIDTH)
            self.add_key("height", DEFAULT_HEIGHT)
            self.add_key("dock-width", DEFAULT_WIDTH)
            self.add_key("dock-height", DEFAULT_HEIGHT)
            self.add_key("dock-expand", True)

    class Portrait(ConfigObject):
        def _init_keys(self):
            self.schema = SCHEMA_WINDOW_PORTRAIT
            self.sysdef_section = "window.portrait"

            self.add_key("x", DEFAULT_X)
            self.add_key("y", DEFAULT_Y)
            self.add_key("width", DEFAULT_WIDTH)
            self.add_key("height", DEFAULT_HEIGHT)
            self.add_key("dock-width", DEFAULT_WIDTH)
            self.add_key("dock-height", DEFAULT_HEIGHT)
            self.add_key("dock-expand", True)


class ConfigICP(ConfigObject):
    """ Icon palette configuration """

    def _init_keys(self):
        self.schema = SCHEMA_ICP
        self.sysdef_section = "icon-palette"

        self.add_key("in-use", False)
        self.add_key("window-handles", DEFAULT_WINDOW_HANDLES)

        self.landscape = ConfigICP.Landscape(self)
        self.portrait = ConfigICP.Portrait(self)

        self.children = [self.landscape, self.portrait]

    ##### property helpers #####
    def _convert_sysdef_key(self, gskey, sysdef, value):
        if sysdef == "resize-handles" or \
           sysdef == "window-handles":
            return Config._string_to_handles(value)
        else:
            return ConfigObject._convert_sysdef_key(self, gskey, sysdef, value)

    def _unpack_window_handles(self, value):
        return Config._string_to_handles(value)

    def _pack_window_handles(self, value):
        return Config._handles_to_string(value)

    def position_notify_add(self, callback):
        self.landscape.x_notify_add(callback)
        self.landscape.y_notify_add(callback)
        self.portrait.x_notify_add(callback)
        self.portrait.y_notify_add(callback)

    def size_notify_add(self, callback):
        self.landscape.width_notify_add(callback)
        self.landscape.height_notify_add(callback)
        self.portrait.width_notify_add(callback)
        self.portrait.height_notify_add(callback)

    class Landscape(ConfigObject):
        def _init_keys(self):
            self.schema = SCHEMA_ICP_LANDSCAPE
            self.sysdef_section = "icon-palette.landscape"

            self.add_key("x", DEFAULT_ICP_X)
            self.add_key("y", DEFAULT_ICP_Y)
            self.add_key("width", DEFAULT_ICP_WIDTH)
            self.add_key("height", DEFAULT_ICP_HEIGHT)

    class Portrait(ConfigObject):
        def _init_keys(self):
            self.schema = SCHEMA_ICP_PORTRAIT
            self.sysdef_section = "icon-palette.portrait"

            self.add_key("x", DEFAULT_ICP_X)
            self.add_key("y", DEFAULT_ICP_Y)
            self.add_key("width", DEFAULT_ICP_WIDTH)
            self.add_key("height", DEFAULT_ICP_HEIGHT)


class ConfigAutoShow(ConfigObject):
    """ auto_show configuration """

    DEFAULT_REPOSITION_METHOD = RepositionMethodEnum.PREVENT_OCCLUSION

    def _init_keys(self):
        self.schema = SCHEMA_AUTO_SHOW
        self.sysdef_section = "auto-show"

        self.add_key("enabled", False)

        enum = {
            "none" : 0,
            "prevent-occlusion" : RepositionMethodEnum.PREVENT_OCCLUSION,
            "reduce-travel"   : RepositionMethodEnum.REDUCE_POINTER_TRAVEL,
        }
        self.add_key("reposition-method-floating", self.DEFAULT_REPOSITION_METHOD,
                      enum=enum)
        self.add_key("reposition-method-docked", self.DEFAULT_REPOSITION_METHOD,
                      enum=enum)
        self.add_key("widget-clearance", (25.0, 55.0, 25.0, 40.0), '(dddd)')

        self.add_key("hide-on-key-press", True)
        self.add_key("hide-on-key-press-pause", 1800.0)

        self.add_key("tablet-mode-detection-enabled", True)
        self.add_key("tablet-mode-enter-key", 0)
        self.add_key("tablet-mode-leave-key", 0)
        self.add_key("tablet-mode-state-file", "")
        self.add_key("tablet-mode-state-file-pattern", "1")

        self.add_key("keyboard-device-detection-enabled", False)
        self.add_key("keyboard-device-detection-exceptions", [])

    def tablet_mode_detection_notify_add(self, callback):
        self.tablet_mode_detection_enabled_notify_add(callback)
        self.tablet_mode_enter_key_notify_add(callback)
        self.tablet_mode_leave_key_notify_add(callback)


class ConfigUniversalAccess(ConfigObject):
    """ universal_access configuration """

    def _init_keys(self):
        self.schema = SCHEMA_UNIVERSAL_ACCESS
        self.sysdef_section = "universal-access"

        self.add_key("drag-threshold", -1)
        self.add_key("hide-click-type-window", True)
        self.add_key("enable-click-type-window-on-exit", True)

    def _post_notify_hide_click_type_window(self):
        """ called when changed in gsettings (preferences window) """
        mousetweaks = self.parent.mousetweaks
        if mousetweaks:
            mousetweaks.on_hide_click_type_window_changed(
                self.hide_click_type_window)


class ConfigTheme(ConfigObject):
    """ Theme configuration """

    def _init_keys(self):
        self.schema = SCHEMA_THEME
        self.sysdef_section = "theme-settings"

        self.add_key("color-scheme", DEFAULT_COLOR_SCHEME,
                     prop="color_scheme_filename")
        self.add_key("background-gradient", 0.0)
        self.add_key("key-style", "flat")
        self.add_key("roundrect-radius", 0.0)
        self.add_key("key-size", 100.0)
        self.add_key("key-stroke-width", 100.0)
        self.add_key("key-fill-gradient", 0.0)
        self.add_key("key-stroke-gradient", 0.0)
        self.add_key("key-gradient-direction", 0.0)
        self.key_label_font_key = \
        self.add_key("key-label-font", "")      # font for current theme
        self.key_label_overrides_key = \
        self.add_key("key-label-overrides", {}, "as") # labels for current theme
        self.add_key("key-shadow-strength", 20.0)
        self.add_key("key-shadow-size", 5.0)

    ##### property helpers #####
    def theme_attributes_notify_add(self, callback):
        self.background_gradient_notify_add(callback)
        self.key_style_notify_add(callback)
        self.roundrect_radius_notify_add(callback)
        self.key_size_notify_add(callback)
        self.key_stroke_width_notify_add(callback)
        self.key_fill_gradient_notify_add(callback)
        self.key_stroke_gradient_notify_add(callback)
        self.key_gradient_direction_notify_add(callback)
        self.key_label_font_notify_add(callback)
        self.key_label_overrides_notify_add(callback)
        self.key_style_notify_add(callback)
        self.key_shadow_strength_notify_add(callback)
        self.key_shadow_size_notify_add(callback)

    def _can_set_color_scheme_filename(self, filename):
        if not os.path.exists(filename):
            _logger.warning(_format("color scheme '{filename}' does not exist", \
                                    filename=filename))
            return False
        return True

    def _unpack_key_label_overrides(self, value):
        return self.unpack_string_list(value, "a{s[ss]}")

    def _pack_key_label_overrides(self, value):
        return self.pack_string_list(value)

    def get_key_label_overrides(self):
        gskey = self.key_label_overrides_key

        # merge with default value from onboard base config
        value = dict(self.parent.key_label_overrides)
        value.update(gskey.value)

        return value

    _font_attributes = ("bold", "italic", "condensed")
    _cached_key_label_font = None

    def _post_notify_key_label_font(self):
        self._cached_key_label_font = None

    def get_key_label_font(self):
        if self._cached_key_label_font is None:
            gskey = self.key_label_font_key

            value = gskey.value
            if value:
                items = value.split()
                if items:
                    # If no font family was provided merge in system font family
                    if items[0] in self._font_attributes:
                        system_items = self.parent.key_label_font.split()
                        if system_items and \
                        system_items[0] not in self._font_attributes:
                            items.insert(0, system_items[0])
                            value = " ".join(items)
            else:
                # get default value from onboard base config instead
                value = self.parent.key_label_font

            self._cached_key_label_font = value

        return self._cached_key_label_font


class ConfigLockdown(ConfigObject):
    """ Lockdown/Kiosk mode configuration """

    def _init_keys(self):
        self.schema = SCHEMA_LOCKDOWN
        self.sysdef_section = "lockdown"

        self.add_key("disable-click-buttons", False)
        self.add_key("disable-hover-click", False)
        self.add_key("disable-dwell-activation", False)
        self.add_key("disable-preferences", False)
        self.add_key("disable-quit", False)
        self.add_key("disable-touch-handles", False)
        self.add_key("disable-keys", [["CTRL", "LALT", "F[0-9]+"]], 'aas')

    def lockdown_notify_add(self, callback):
        self.disable_click_buttons_notify_add(callback)
        self.disable_hover_click_notify_add(callback)
        self.disable_preferences_notify_add(callback)
        self.disable_quit_notify_add(callback)


class ConfigGSS(ConfigObject):
    """ gnome-screen-saver configuration keys"""

    def _init_keys(self):
        self.schema = SCHEMA_GSS
        self.sysdef_section = "gnome-screen-saver"

        self.add_key("embedded-keyboard-enabled", True)
        self.add_key("embedded-keyboard-command", "")


class ConfigGDI(ConfigObject):
    """ Key to enable Gnome Accessibility"""

    def _init_keys(self):
        self.schema = SCHEMA_GDI
        self.sysdef_section = "gnome-desktop-interface"

        self.add_key("toolkit-accessibility", False)
        self.add_key("gtk-theme", "", writable=False)  # read-only for safety


class ConfigGDA(ConfigObject):
    """ Key to check if a11y keyboard is enabled """

    def _init_keys(self):
        self.schema = SCHEMA_GDA
        self.sysdef_section = "gnome-desktop-a11y-applications"

        # read-only for safety
        self.add_key("screen-keyboard-enabled", False, writable=False)


class ConfigUnityGreeter(ConfigObject):
    """ Key to hide onboard when embedded into unity-greeter """

    def _init_keys(self):
        self.schema = SCHEMA_UNITY_GREETER
        self.sysdef_section = "unity-greeter"

        self.add_key("onscreen-keyboard", False)


class ConfigScanner(ConfigObject):
    """ Scanner configuration """

    DEFAULT_INTERVAL          = 1.20
    DEFAULT_INTERVAL_FAST     = 0.05
    DEFAULT_MODE              = 0 # AutoScan
    DEFAULT_CYCLES            = 2
    DEFAULT_BACKTRACK         = 5
    DEFAULT_ALTERNATE         = False
    DEFAULT_USER_SCAN         = False
    DEFAULT_DEVICE_NAME       = "Default"
    DEFAULT_DEVICE_KEY_MAP    = {}
    DEFAULT_DEVICE_BUTTON_MAP = { 1: 0, 3: 5 } # Button 1: Step, Button 3: Activate
    DEFAULT_FEEDBACK_FLASH    = True

    def _init_keys(self):
        self.schema = SCHEMA_SCANNER
        self.sysdef_section = "scanner"

        self.add_key("enabled", False)
        self.add_key("mode", self.DEFAULT_MODE, enum={"Autoscan" : 0,
                                                      "Overscan" : 1,
                                                      "Stepscan" : 2,
                                                      "Directed" : 3})
        self.add_key("interval", self.DEFAULT_INTERVAL)
        self.add_key("interval-fast", self.DEFAULT_INTERVAL_FAST)
        self.add_key("cycles", self.DEFAULT_CYCLES)
        self.add_key("backtrack", self.DEFAULT_BACKTRACK)
        self.add_key("alternate", self.DEFAULT_ALTERNATE)
        self.add_key("user-scan", self.DEFAULT_USER_SCAN)
        self.add_key("device-name", self.DEFAULT_DEVICE_NAME)
        self.add_key("device-detach", False)
        self.add_key("device-key-map", self.DEFAULT_DEVICE_KEY_MAP, 'a{ii}')
        self.add_key("device-button-map", self.DEFAULT_DEVICE_BUTTON_MAP, 'a{ii}')
        self.add_key("feedback-flash", self.DEFAULT_FEEDBACK_FLASH)


class ConfigTypingAssistance(ConfigObject):
    """ typing-assistance configuration keys"""

    DEFAULT_BACKEND = 0

    def _init_keys(self):
        self.schema = SCHEMA_TYPING_ASSISTANCE
        self.sysdef_section = "typing-assistance"

        self.add_key("active-language", "")
        self.add_key("recent-languages", [], 'as')
        self.add_key("max-recent-languages", 5)
        self.add_key("spell-check-backend", self.DEFAULT_BACKEND,
                                                     enum={"hunspell" : 0,
                                                           "aspell"   : 1})
        self.add_key("auto-capitalization", False)
        self.add_key("auto-correction", False)

        self.word_suggestions = ConfigWordSuggestions(self)
        self.children = [self.word_suggestions]

        # shortcuts in the root for convenient access
        self.get_root().wp = self.word_suggestions
        self.get_root().word_suggestions = self.word_suggestions


class ConfigWordSuggestions(ConfigObject):
    """ word-suggestions configuration keys"""

    # wordlist_buttons
    KEY_ID_PREVIOUS_PREDICTIONS = "previous-predictions"
    KEY_ID_NEXT_PREDICTIONS = "next-predictions"
    KEY_ID_LANGUAGE = "language"
    KEY_ID_PAUSE_LEARNING = "pause-learning"
    KEY_ID_HIDE = "hide"
    KEY_ORDER = [KEY_ID_PREVIOUS_PREDICTIONS,
                 KEY_ID_NEXT_PREDICTIONS,
                 KEY_ID_PAUSE_LEARNING,
                 KEY_ID_LANGUAGE,
                 KEY_ID_HIDE]

    def _init_keys(self):
        self.schema = SCHEMA_WORD_SUGGESTIONS
        self.sysdef_section = "word-suggestions"

        self.add_key("enabled", False)
        self.add_key("auto-learn", True)
        self.add_key("punctuation-assistance", True)
        self.add_key("delayed-word-separators-enabled", False)
        self.add_key("accent-insensitive", True)
        self.add_key("max-word-choices", 5)
        self.add_key("spelling-suggestions-enabled", True)
        self.add_key("wordlist-buttons",
                     [self.KEY_ID_PREVIOUS_PREDICTIONS,
                      self.KEY_ID_NEXT_PREDICTIONS,
                      self.KEY_ID_LANGUAGE,
                      self.KEY_ID_HIDE])
        self.add_key("pause-learning-locked", False)
        self.add_key("learning-behavior-paused", LearningBehavior.NOTHING,
                     enum={"nothing" : LearningBehavior.NOTHING,
                           "known-only" : LearningBehavior.KNOWN_ONLY})

        # 0=off, 1=latched, 2=locked; not in gsettings
        self._pause_learning = 0

        # deprecated
        self.add_key("stealth-mode", False)
        self.add_key("show-context-line", False)

    def word_prediction_notify_add(self, callback):
        self.auto_learn_notify_add(callback)
        self.punctuation_assistance_notify_add(callback)
        self.stealth_mode_notify_add(callback)

    def get_can_auto_learn_debug_string(self):
        return ("enabled={} auto_learn={} "
                "is_learning_paused={} learning_behavior_paused={} "
                "pause_learning_locked={} _pause_learning={} "
                "stealth_mode={}"
                .format(self.enabled,
                        self.auto_learn,
                        self.is_learning_paused(),
                        self.learning_behavior_paused,
                        self.pause_learning_locked,
                        self._pause_learning,
                        self.stealth_mode
                        ))

    def can_auto_learn(self):
        return (self.enabled and
                self.auto_learn and
                (not self.is_learning_paused() or
                 self.learning_behavior_paused != LearningBehavior.NOTHING) and
                not self.stealth_mode)

    def is_learning_paused(self):
        return self.get_pause_learning() > 0

    def get_pause_learning(self):
        if self.pause_learning_locked:
            return 2
        else:
            return self._pause_learning

    def set_pause_learning(self, value):
        self._pause_learning = value
        self.pause_learning_locked = value == 2

    def _post_notify_pause_learning_locked(self):
        self._pause_learning = 2 if self.pause_learning_locked else 0

    def get_shown_wordlist_button_ids(self):
        result = []
        for button_id in self.wordlist_buttons:
            if button_id != self.KEY_ID_PAUSE_LEARNING or \
               self.can_show_pause_learning_button():
                result.append(button_id)
        return result

    def show_wordlist_button(self, key_id, show):
        """
        Doctests:
        >>> co = ConfigWordSuggestions()
        >>> ConfigWordSuggestions.wordlist_buttons = []

        >>> co.wordlist_buttons = []
        >>> co.show_wordlist_button(co.KEY_ID_HIDE, True)
        >>> print(co.wordlist_buttons)
        ['hide']

        >>> co.wordlist_buttons = []
        >>> co.show_wordlist_button(co.KEY_ID_LANGUAGE, True)
        >>> print(co.wordlist_buttons)
        ['language']

        >>> co.wordlist_buttons = []
        >>> co.show_wordlist_button("notabutton", True)
        >>> print(co.wordlist_buttons)
        []

        >>> co.wordlist_buttons = [co.KEY_ID_PAUSE_LEARNING, co.KEY_ID_HIDE]
        >>> co.show_wordlist_button(co.KEY_ID_LANGUAGE, True)
        >>> print(co.wordlist_buttons)
        ['pause-learning', 'language', 'hide']

        >>> co.wordlist_buttons = [co.KEY_ID_LANGUAGE, co.KEY_ID_HIDE]
        >>> co.show_wordlist_button(co.KEY_ID_PAUSE_LEARNING, True)
        >>> print(co.wordlist_buttons)
        ['pause-learning', 'language', 'hide']

        """
        buttons = self.wordlist_buttons[:]
        if show:
            if key_id not in buttons:
                priority = self._get_button_priority(key_id)
                if priority >= 0:
                    for i, button in enumerate(buttons):
                        p = self._get_button_priority(button)
                        if priority < p:
                            buttons.insert(i, key_id)
                            break
                    else:
                        buttons.append(key_id)

                self.wordlist_buttons = buttons
        else:
            if key_id in buttons:
                buttons.remove(key_id)
                self.wordlist_buttons = buttons

    @staticmethod
    def _get_button_priority(key_id):
        try:
            return ConfigWordSuggestions.KEY_ORDER.index(key_id)
        except ValueError:
            return -1

    def can_show_language_button(self):
        return self.KEY_ID_LANGUAGE in self.wordlist_buttons

    def can_show_pause_learning_button(self):
        return self.auto_learn and \
            self.KEY_ID_PAUSE_LEARNING in self.wordlist_buttons

    def can_show_more_predictions_button(self):
        return self.KEY_ID_NEXT_PREDICTIONS in self.wordlist_buttons

    def can_learn_new_words(self):
        return not self.is_learning_paused()

