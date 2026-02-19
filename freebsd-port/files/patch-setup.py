--- setup.py.orig	2025-01-01 00:00:00 UTC
+++ setup.py
@@ -26,6 +26,7 @@

 import os
 import sys
+import platform


 import re
@@ -263,7 +264,7 @@
                                "-Wsign-compare",
                                "-Wdeclaration-after-statement",
                                "-Werror=declaration-after-statement",
-                               "-Wlogical-op"],
+                           ] + (["-Wlogical-op"] if platform.system() == 'Linux' else []),

                            **pkgconfig('gdk-3.0', 'x11', 'xi', 'xtst', 'xkbfile',
                                        'dconf', 'libcanberra', 'hunspell',
@@ -312,7 +313,7 @@
                            define_macros=[('NDEBUG', '1')],
                            extra_compile_args=[
                                "-Wsign-compare",
-                               "-Wlogical-op"],
+                           ] + (["-Wlogical-op"] if platform.system() == 'Linux' else []),
                           )

 extension_lm = Extension_lm("Onboard", "Onboard")