# Onboard 1.4.3-9

![onb](https://github.com/onboard-osk/onboard/blob/main/onboard.png)

![onb](https://github.com/onboard-osk/onboard/blob/main/Onboard.gif)

## Description

Onboard is an onscreen keyboard useful for everybody that cannot use a
hardware keyboard; for example Tablet-PC users or mobility impaired users.
It has been designed with simplicity in mind and can be used right away
without the need of any configuration, as it can read the keyboard layout
from the X server. Onboard is currently not working with wayland - a correct
X11/Xorg setup is required.

## Building from Source
Find below short instructions on how to build Onboard straight from this
github repository. If you have improvements to share, get errors or run
into other problems, please let us know. Build instructions for
new distributions are always welcome too.

### !!! First uninstall ALL onboard and mousetweaks packages !!!

## Ubuntu and Debian:
        # Uninstall
        sudo apt purge onboard onboard-common onboard-data
        sudo apt purge mousetweaks

        # Note: It is recommended to build and install Debian packages see below.

        # Install dependencies
        sudo apt install git build-essential python3-packaging python3-dev
        sudo apt install dh-python python3-distutils-extra devscripts pkg-config
        sudo apt install libgtk-3-dev libxtst-dev libxkbfile-dev libdconf-dev libcanberra-dev
        sudo apt install libhunspell-dev libudev-dev
        
        Next step is "Build and Install from Source"

## Arch Linux:
        # Uninstall
        sudo pacman -S mousetweaks
        sudo pacman -S onboard
        
        # Install dependencies
        pacman -S base-devel git python-packaging python-distutils-extra dconf gtk3 \
        libcanberra hunspell python-gobject gsettings-desktop-schemas \
        iso-codes python-cairo librsvg python-dbus dbus-glib

        Next step is "Build and Install from Source"

## Mageia:
        # Install dependencies
        urpmi git gcc-c++ lib64zlib-devel python3-distutils-extra
        urpmi libgtk+3.0-devel libxtst-devel libxkbfile-devel libdconf-devel
        urpmi libhunspell-devel libcanberra-devel libpython3-devel intltool
        # more or less optional, but recommended for full functionality
        urpmi lib64atspi-gir2.0 at-spi2-core-qt python3-dbus qtatspi-plugin

        Next step is "Build and Install from Source"

## Fedora Xfce:
        # Install dependencies
        sudo dnf install python3-distutils-extra dconf-devel intltool
        sudo dnf install libcanberra-devel libxkbfile-devel libXtst-devel
        sudo dnf install hunspell-devel python3-devel intltool gcc-c++ gcc
        sudo dnf install 'pkgconfig(udev)' 'pkgconfig(libudev)'

        Next step is "Build and Install from Source"


## openSUSE Xfce:
        # Install dependencies
        sudo zypper install python3-distutils-extra dconf-devel intltool
        sudo zypper install libcanberra-devel libxkbfile-devel libXtst-devel
        sudo zypper install hunspell-devel python3-devel intltool gcc-c++ gcc
        sudo zypper install 'pkgconfig(udev)' 'pkgconfig(libudev)'

        Next step is "Build and Install from Source"

## Build and Install from Source
        git clone https://github.com/onboard-osk/onboard
        cd onboard
        python3 setup.py clean
        python3 setup.py build
        
        # System-wide installation (requires root access):
        sudo python3 setup.py install

## Uninstall if installed from Source
        # System-wide uninstall (requires root access):
        sudo python3 setup.py uninstall
        
## Build and Install Debian Packages

To build Debian packages from the source, two scripts are available:
- `build_debs.sh`: Creates the `.deb` packages and related metadata.
- `apt_install_debs.sh`: Sets up a local repository and installs the packages on a target system.

---

### Notes
- Both scripts automatically use `sudo` to install dependencies or packages.
- Ensure you have `sudo` privileges and be ready to enter your password when prompted during execution.

---

### Build Debian Packages

The `build_debs.sh` script automates building `.deb` packages and associated metadata in **./build/debs**

#### Steps:
   - Execute:
     ```bash
     /bin/sh ./build_debs.sh
     ```

The Debian packages will be saved in the directory: `/path/to/onboard_sources/build/debs` 

---

### Install the Debian Packages

The `apt_install_debs.sh` script simplifies installing the generated `.deb` packages using a local repository.

#### Steps:
   - If the target system is the build system:
      - Execute:
     ```bash
     /bin/sh ./apt_install_debs.sh
     ```
   - If the target system is not the build system copy this files to a directory on the target:
     - All `build/debs/*.deb` files.
     - The `build/debs/Packages` file.
     - The `apt_install_debs.sh` script.
     - Execute in the directory on the target system:
     ```bash
     /bin/sh ./apt_install_debs.sh
     ```
        
### Uninstall the Debian Packages
   - Execute:
     ```bash
     /bin/sh ./apt_install_debs.sh "remove"
     ```
## Manuals

        # Terminal
        man onboard
        onboard -h
        
        # Interactive
        yelp "help:onboard"
        xdg-open "help:onboard"

        # Onboard
        # Right click on icon in systray -> Help 

        # Change keyboard language layout
        # setxkbmap -layout de
        # or [us|in|ru|...]

## D-Bus interface

The Onboard D-Bus interface allows communication between Onboard and other processes running concurrently on the Linux desktop.

Here the Interface description:
[DBUS.md](https://github.com/onboard-osk/onboard/blob/main/DBUS.md)

## Mousetweaks

This optional package provides mouse accessibility enhancements for the GNOME desktop.
It offers a way to perform clicks without using any physical mouse buttons (Hover Click).
The package is also available in various package managers. However, it is often
not working anymore with onboard. In this case a manual installation from https://github.com/onboard-osk/mousetweaks should help.

## Homepage
https://github.com/onboard-osk/onboard

## Reporting Bugs
https://github.com/onboard-osk/onboard/issues

## License
This program is released under the terms of the GNU General Public License. Please see the file COPYING for details.
