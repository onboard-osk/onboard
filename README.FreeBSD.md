# Onboard on FreeBSD — Porting Guide & README Section


### C Source Changes

**Onboard/osk/osk_uinput.c** — replace the Linux-specific includes:

```c
// Replace:
#include <linux/input.h>
#include <linux/uinput.h>

// With:
#ifdef __FreeBSD__
#include <dev/evdev/input.h>
#include <dev/evdev/uinput.h>
#else
#include <linux/input.h>
#include <linux/uinput.h>
#endif
```

**Onboard/osk/osk_util.c** — fix `_NSIG` (around line 40):

```c
// Add before the array declaration:
#ifdef __FreeBSD__
#ifndef _NSIG
#define _NSIG NSIG
#endif
#endif

    PyObject* signal_callbacks[_NSIG];
```

**Onboard/pypredict/lm/lm.cpp** and **lm_dynamic.cpp** — replace glibc `error.h`:

```cpp
// Replace:
#include <error.h>

// With:
#ifdef __FreeBSD__
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

static inline void error(int status, int errnum, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    if (errnum)
        fprintf(stderr, ": %s", strerror(errnum));
    fprintf(stderr, "\n");
    if (status)
        exit(status);
}
#else
#include <error.h>
#endif
```

To avoid duplicating the `error()` shim, you could put it in a shared header
like `Onboard/pypredict/lm/compat.h` and include that — but since only two
files use it, inline is fine.

### setup.py Changes

```python
import platform

# In Extension_osk.__init__, remove -Wlogical-op (GCC-only, clang rejects it):
extra_compile_args = [
    "-Wsign-compare",
    "-Wdeclaration-after-statement",
    "-Werror=declaration-after-statement",
]
if platform.system() == 'Linux':
    extra_compile_args.append("-Wlogical-op")
```

### Python Source Changes

**Onboard/LanguageSupport.py** — use platform-correct paths:

```python
import sys

if sys.platform.startswith('freebsd'):
    _ISO_CODES_PREFIX = "/usr/local/share/xml/iso-codes"
else:
    _ISO_CODES_PREFIX = "/usr/share/xml/iso-codes"
```

**onboard** and **onboard-settings** scripts — portable shebang:

```python
#!/usr/bin/env python3
```

---

## Building on FreeBSD

Onboard builds and runs on FreeBSD (tested on FreeBSD 15.0 with X11/scfb).
Wayland is not currently supported on FreeBSD.

### Prerequisites

Install required packages:

```sh
pkg install python311 py311-setuptools py311-pygobject py311-python-distutils-extra \
    py311-dbus py311-cairo gtk3 libXtst libxkbfile dconf hunspell libcanberra \
    intltool at-spi2-core libudev-devd gsettings-desktop-schemas iso-codes \
    gettext-tools bash
```

### Building

```sh
git clone https://github.com/alipang/onboard.git
cd onboard
python3.11 setup.py build 2>&1 | tee build.log
```

### Installing

```sh
sudo python3.11 setup.py install
```

Verify:

```sh
onboard --help
```

### Running

Onboard requires a running X11 session:

```sh
onboard &
```

### LightDM Greeter Integration

To use Onboard as the on-screen keyboard in lightdm-gtk-greeter, edit
`/usr/local/etc/lightdm/lightdm-gtk-greeter.conf`:

```ini
[greeter]
keyboard=onboard
keyboard-position=50%,100%;50% 25%
```

### Defaults Configuration

Create `/usr/local/share/onboard/onboard-defaults.conf`:

```ini
[main]
layout=/usr/local/share/onboard/layouts/Compact.onboard
theme=/usr/local/share/onboard/themes/Nightshade.theme

[window]
force-to-top=True
dock-expand=True

[window.landscape]
dock-height=25
```

### FreeBSD-Specific Notes

- FreeBSD uses `dev/evdev/input.h` and `dev/evdev/uinput.h` instead of
  Linux's `linux/input.h` and `linux/uinput.h`. The C sources handle this
  via `#ifdef __FreeBSD__`.
- The `libudev-devd` package provides a FreeBSD-compatible `libudev` shim.
- Signal constant `NSIG` is used instead of Linux's `_NSIG`.
- GNU `error.h` is not available on FreeBSD; a portable replacement is
  compiled in for the `pypredict` language model module.
- iso-codes XML files are located under `/usr/local/share/xml/iso-codes/`
  rather than `/usr/share/xml/iso-codes/`.
- The `bash` package is required for certain build scripts (`/bin/bash`
  must be available, either via the package or a symlink from
  `/usr/local/bin/bash`).

### Known Limitations on FreeBSD

- Wayland support is not available (X11 only).
- Auto-show (accessibility) depends on `at-spi2-core` D-Bus accessibility
  being enabled in the desktop environment.
- The `uinput` device (`/dev/uinput`) must be accessible for key injection.
  You may need to load the `uinput` kernel module and adjust permissions:

  ```sh
  kldload uinput
  echo 'uinput_load="YES"' >> /boot/loader.conf
  ```