# Onboard on FreeBSD

Tested on FreeBSD 15.0 with X11/scfb.

## Installing via the FreeBSD Port

On FreeBSD, third-party software is typically built and installed through
the ports system under `/usr/ports`. This is the standard way to compile
software from source on FreeBSD, comparable to building packages from
AUR on Arch Linux or PPAs on Ubuntu.

```sh
sudo mkdir -p /usr/ports/x11/onboard
sudo cp -R freebsd-port/* /usr/ports/x11/onboard/
cd /usr/ports/x11/onboard
sudo make install clean
```

The port handles all dependencies and shebang rewriting automatically.
The Python version is determined by `DEFAULT_VERSIONS` in `/etc/make.conf`
or the ports framework default.

## FreeBSD-Specific Notes

- The `libudev-devd` package provides a FreeBSD-compatible `libudev` shim.
- The `bash` package is required for certain build scripts.
- The `uinput` device (`/dev/uinput`) must be accessible for key injection:

  ```sh
  kldload uinput
  echo 'uinput_load="YES"' >> /boot/loader.conf
  ```
