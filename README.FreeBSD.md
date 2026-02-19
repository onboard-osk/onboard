# Onboard on FreeBSD

Tested on FreeBSD 15.0 with X11/scfb.

## Installing via the FreeBSD Port

```sh
sudo mkdir -p /usr/ports/x11/onboard
sudo cp -R freebsd-port/* /usr/ports/x11/onboard/
cd /usr/ports/x11/onboard
sudo make install clean
```

The port handles all dependencies, patches, and shebang rewriting
automatically. The Python version is determined by `DEFAULT_VERSIONS`
in `/etc/make.conf` or the ports framework default.

## What the Port Does

### Patches (applied from `freebsd-port/files/`)

- **patch-setup.py** — Skips `-Wlogical-op` (GCC-only, rejected by clang).
- **patch-Onboard_LanguageSupport.py** — Uses `/usr/local/share/xml/iso-codes`
  instead of `/usr/share/xml/iso-codes`.

### Shebang Rewriting

Uses `USES=shebangfix` to rewrite shebangs in `onboard` and
`onboard-settings` to the versioned Python interpreter selected by the port.

### Upstream C Source Changes

These `#ifdef __FreeBSD__` guards are in the source tree:

- **osk_uinput.c** — Uses `dev/evdev/input.h` and `dev/evdev/uinput.h`.
- **osk_util.c** — Defines `_NSIG` as `NSIG`.
- **lm.cpp**, **lm_dynamic.cpp** — Portable `error()` replacement
  for glibc's `error.h`.

## FreeBSD-Specific Notes

- The `libudev-devd` package provides a FreeBSD-compatible `libudev` shim.
- The `bash` package is required for certain build scripts.
- The `uinput` device (`/dev/uinput`) must be accessible for key injection:

  ```sh
  kldload uinput
  echo 'uinput_load="YES"' >> /boot/loader.conf
  ```
  