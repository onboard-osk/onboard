--- Onboard/LanguageSupport.py.orig	2025-01-01 00:00:00 UTC
+++ Onboard/LanguageSupport.py
@@ -19,6 +19,7 @@

 from __future__ import division, print_function, unicode_literals

+import sys
 import subprocess
 import gettext
 from xml.dom import minidom
@@ -227,8 +228,12 @@
         self._read_languages()
         self._read_countries()

+    _ISO_CODES_PREFIX = "/usr/local/share/xml/iso-codes" \
+        if sys.platform.startswith('freebsd') \
+        else "/usr/share/xml/iso-codes"
+
     def _read_languages(self):
-        with open_utf8("/usr/share/xml/iso-codes/iso_639.xml") as f:
+        with open_utf8(self._ISO_CODES_PREFIX + "/iso_639.xml") as f:
             dom = minidom.parse(f).documentElement
             for node in dom.getElementsByTagName("iso_639_entry"):

@@ -242,7 +247,7 @@
                     self._languages[lang_code] = lang_name

     def _read_countries(self):
-        with open_utf8("/usr/share/xml/iso-codes/iso_3166.xml") as f:
+        with open_utf8(self._ISO_CODES_PREFIX + "/iso_3166.xml") as f:
             dom = minidom.parse(f).documentElement
             for node in dom.getElementsByTagName("iso_3166_entry"):