# -*- coding: utf-8 -*-

# Copyright © 2012-2016 marmuta <marmvta@gmail.com>
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

import subprocess
import gettext
from xml.dom import minidom

from Onboard.utils import open_utf8

### Logging ###
import logging
_logger = logging.getLogger("LanguageSupport")
###############


class LanguageDB:
    """
    Keeps track of languages selectable in the language menu.
    """
    _main_languages = {"aa" : "ET",
                       "ar" : "EG",
                       "bn" : "BD",
                       "ca" : "ES",
                       "cs" : "CZ",
                       "da" : "DK",  # Danish
                       "de" : "DE",
                       "el" : "GR",
                       "en" : "US",
                       "eo" : "XX",  # Esperanto
                       "es" : "ES",
                       "eu" : "ES",
                       "fr" : "FR",
                       "fy" : "BL",
                       "ga" : "IE",  # Irish
                       "gd" : "GB",  # Scottish Gaelic
                       "it" : "IT",
                       "li" : "NL",
                       "nl" : "NL",
                       "om" : "ET",
                       "pl" : "PL",  # Polish
                       "pa" : "PK",
                       "pt" : "PT",
                       "ro" : "RO",  # Romanian
                       "ru" : "RU",
                       "so" : "SO",
                       "sr" : "RS",
                       "sv" : "SE",
                       "ti" : "ER",
                       "tr" : "TR",  # Turkish
                      }

    def __init__(self, wp = None):
        self._wp = wp
        self._locale_ids = []
        self._iso_codes = ISOCodes()

    @staticmethod
    def get_main_languages():
        return list(LanguageDB._main_languages.keys())

    def get_language_full_name_or_id(self, lang_id):
        full_name = self.get_language_full_name(lang_id)
        if full_name:
            return full_name
        return lang_id

    def get_language_full_name(self, lang_id):
        lang_code, country_code = self.split_lang_id(lang_id)
        name = self._iso_codes.get_translated_language_name(lang_code)
        if country_code:
            country = self._iso_codes.get_translated_country_name(country_code)
            if not country:
                country = country_code
            if country and \
               country != "XX":   # Esperanto has no native country
                name += " (" + country + ")"
        return name

    def get_language_name(self, lang_id):
        lang_code, country_code = self.split_lang_id(lang_id)
        name = self._iso_codes.get_translated_language_name(lang_code)
        return name

    def get_language_code(self, lang_id):
        lang_code, country_code = self.split_lang_id(lang_id)
        return lang_code

    def find_system_model_language_id(self, lang_id):
        """
        Return lang_id if there is an exact match, else return the
        default lang_id for its lanugage code.

        Doctests:
        >>> l = LanguageDB()
        >>> l.get_system_model_language_ids = lambda : ["en_US", "en_GB", "de_DE"]
        >>> l.find_system_model_language_id("en")
        'en_US'
        >>> l.find_system_model_language_id("en_XY")
        'en_US'
        >>> l.find_system_model_language_id("en_US")
        'en_US'
        >>> l.find_system_model_language_id("en_GB")
        'en_GB'
        """
        known_ids = self.get_system_model_language_ids()
        if not lang_id in known_ids:
            lang_code, country_code = self.split_lang_id(lang_id)
            lang_id = self.get_main_lang_id(lang_code)
        return lang_id

    def get_system_model_language_ids(self):
        """
        List of language_ids of the available system language models.
        """
        return self._wp.get_system_model_names()

    def get_language_ids(self):
        """
        List of language_ids (ll_CC) that can be selected by the user.
        """
        return self._wp.get_merged_model_names()

    def get_main_lang_id(self, lang_code):
        """
        Complete given language code to the language id of the main language.
        """
        lang_id = ""
        country_code = self._main_languages.get(lang_code, "")
        if country_code:
            lang_id = lang_code + "_" + country_code
        return lang_id

    def _get_language_ids_in_locale(self):
        lang_ids = []
        for locale_id in self._get_locale_ids():
            lang_code, country_code, encoding = self._split_locale_id(locale_id)
            if lang_code and not lang_code in ["POSIX", "C"]:
                lang_id = lang_code
                if country_code:
                    lang_id += "_" + country_code
                lang_ids.append(lang_id)
        return lang_ids

    def _get_locale_ids(self):
        if not self._locale_ids:
            self._locale_ids = self._find_locale_ids()
        return self._locale_ids

    def _find_locale_ids(self):
        locale_ids = []
        try:
            locale_ids = subprocess.check_output(["locale", "-a"]) \
                                   .decode("UTF-8").split("\n")
        except OSError as e:
            _logger.error(_format("Failed to execute '{}', {}", \
                            " ".join(self.args), e))
        return [id for id in locale_ids if id]

    @staticmethod
    def _split_locale_id(locale_id):
        tokens = locale_id.split(".")
        lang_id = tokens[0] if len(tokens) >= 1 else ""
        encoding = tokens[1] if len(tokens) >= 2 else ""
        lang_code, country_code = LanguageDB.split_lang_id(lang_id)
        return lang_code, country_code, encoding

    @staticmethod
    def split_lang_id(lang_id):
        tokens = lang_id.split("_")
        lang_code    = tokens[0] if len(tokens) >= 1 else ""
        country_code = tokens[1] if len(tokens) >= 2 else ""
        return lang_code, country_code


class ISOCodes:
    """
    Load and translate ISO language and country codes.

    Doctests:
    >>> ic = ISOCodes()
    >>> ic.get_language_name("en")
    'English'
    >>> ic.get_country_name("DE")
    'Germany'
    """

    def __init__(self):
        self._languages = {}  # lowercase lang_ids
        self._countries = {}  # uppercase country_ids

        self._read_all()

    def get_language_name(self, lang_code):
        return self._languages.get(lang_code, "")

    def get_country_name(self, country_code):
        return self._countries.get(country_code, "")

    def get_translated_language_name(self, lang_code):
        return gettext.dgettext("iso_639", self.get_language_name(lang_code))

    def get_translated_country_name(self, country_code):
        country_name = self.get_country_name(country_code)
        if not country_name:
            country_name = country_code
        return gettext.dgettext("iso_3166", country_name)

    def _read_all(self):
        self._read_languages()
        self._read_countries()

    def _read_languages(self):
        with open_utf8("/usr/share/xml/iso-codes/iso_639.xml") as f:
            dom = minidom.parse(f).documentElement
            for node in dom.getElementsByTagName("iso_639_entry"):

                lang_code = self._get_attr(node, "iso_639_1_code")
                if not lang_code:
                    lang_code = self._get_attr(node, "iso_639_2T_code")

                lang_name = self._get_attr(node, "name", "")

                if lang_code and lang_name:
                    self._languages[lang_code] = lang_name

    def _read_countries(self):
        with open_utf8("/usr/share/xml/iso-codes/iso_3166.xml") as f:
            dom = minidom.parse(f).documentElement
            for node in dom.getElementsByTagName("iso_3166_entry"):

                country_code = self._get_attr(node, "alpha_2_code")
                country_name = self._get_attr(node, "name")

                if country_code and country_name:
                    self._countries[country_code.upper()] = country_name

    @staticmethod
    def _get_attr(node, name, default = ""):
        attr = node.attributes.get(name)
        if attr:
            return attr.value
        return ""

