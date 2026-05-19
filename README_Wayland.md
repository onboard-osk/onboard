# Onboard on Wayland

Native Wayland key injection was added in version 1.4.4-2 via the
`zwp_virtual_keyboard_unstable_v1` protocol. This document covers
setup, compositor compatibility, known limitations, and build details.

## Status

| Feature | Status |
|---|---|
| Key injection | Working |
| Modifier keys (Shift, Ctrl, Alt, ...) | Working |
| Layout group switching | Working |
| XKB keymap upload | Working |
| Focus-stealing prevention (layer-shell) | Not yet implemented |
| Auto-show on text field focus (AT-SPI) | Partially working |

## Compositor Support

| Compositor | Protocol available | Tested |
|---|---|---|
| sway | yes, built-in | yes |
| labwc | yes, built-in | no |
| Hyprland | yes, built-in | no |
| KDE Plasma >= 5.25 | yes, built-in | no |
| GNOME / Mutter >= 45 | yes, built-in | no |
| weston | partial | no |
| Mir | not supported | no |

To verify your compositor exposes the protocol:

        sudo apt install wayland-utils
        wayland-info | grep virtual_keyboard

You should see a line like:

        interface: 'zwp_virtual_keyboard_manager_v1', version: 1, name: 44

If nothing appears, key injection will be disabled and Onboard will fall
back to display-only mode.

## Running Onboard on Wayland

        GDK_BACKEND=wayland ONBOARD_ALLOW_WAYLAND=1 onboard

`GDK_BACKEND=wayland` forces GDK to use the Wayland backend even when
`XDG_SESSION_TYPE` is set to `x11` (e.g. inside a nested compositor).
`ONBOARD_ALLOW_WAYLAND=1` bypasses the Wayland session warning.

### Sway example

Add to `~/.config/sway/config`:

        exec GDK_BACKEND=wayland ONBOARD_ALLOW_WAYLAND=1 onboard

To prevent Onboard from stealing focus add:

        for_window [app_id="onboard"] floating enable, border none
        no_focus [app_id="onboard"]

### GNOME / KDE

Log into a Wayland session from the login screen, then run:

        ONBOARD_ALLOW_WAYLAND=1 onboard

GDK should auto-detect the Wayland display without needing
`GDK_BACKEND=wayland`.

## Known Limitations

### Focus stealing

On Wayland, clicking an Onboard key causes the focused window to lose
keyboard focus because Onboard does not yet implement the
`zwlr_layer_shell_v1` protocol. This means key injection fires but the
text may not land in the expected target.

Implementing `zwlr_layer_shell_v1` is the next planned step to fix this
properly.

### XInput / click simulators

XInput-based click simulation (`CSFloatingSlave`) is X11-only and
unavailable on Wayland. Onboard automatically falls back to
`CSButtonMapper`, which works via the virtual keyboard protocol.
The following warnings at startup are expected and harmless:

        WARNING Onboard.XInput: Failed to create osk.Devices: not an X display
        WARNING Onboard.Keyboard: XInput click simulator CSFloatingSlave unavailable,
                falling back to CSButtonMapper.

## Build Dependencies

The following additional packages are required for Wayland support:

### Ubuntu / Debian

        sudo apt install libwayland-dev libwayland-bin wayland-protocols libxkbcommon-dev

### Arch Linux

        pacman -S wayland wayland-protocols libxkbcommon

### Fedora

        sudo dnf install wayland-devel wayland-protocols-devel libxkbcommon-devel

### openSUSE

        sudo zypper install wayland-devel wayland-protocols-devel libxkbcommon-devel

## Protocol Files

The Wayland virtual keyboard protocol stubs are pre-generated and
included in the source tree:

        Onboard/osk/virtual-keyboard-unstable-v1-client-protocol.h
        Onboard/osk/virtual-keyboard-unstable-v1-protocol.c
        Onboard/osk/virtual-keyboard-unstable-v1.xml

To regenerate them from the XML (requires `wayland-scanner`):

        wayland-scanner client-header \
            Onboard/osk/virtual-keyboard-unstable-v1.xml \
            Onboard/osk/virtual-keyboard-unstable-v1-client-protocol.h

        wayland-scanner private-code \
            Onboard/osk/virtual-keyboard-unstable-v1.xml \
            Onboard/osk/virtual-keyboard-unstable-v1-protocol.c

## How It Works

When Onboard starts on a Wayland display it:

1. Connects to the Wayland registry and discovers
   `zwp_virtual_keyboard_manager_v1`.
2. Gets the `wl_seat` from GDK, sharing GDK's existing connection.
3. Creates a `zwp_virtual_keyboard_v1` object.
4. Uploads the XKB keymap from GDK to the compositor via shared memory.
5. For each key press/release, sends a `zwp_virtual_keyboard_v1::key`
   event with the correct evdev keycode (X11 keycode minus 8).
6. For modifier changes, sends a `zwp_virtual_keyboard_v1::modifiers`
   event with the updated bitmask.

The backend degrades gracefully: if the compositor does not expose
`zwp_virtual_keyboard_manager_v1`, Onboard still starts and displays
the keyboard, but key injection is disabled.

## Debugging

Enable verbose output:

        G_MESSAGES_DEBUG=all GDK_BACKEND=wayland ONBOARD_ALLOW_WAYLAND=1 onboard 2>&1

Relevant log lines to look for:

        zwp_virtual_keyboard_manager_v1 found   -> protocol discovered
        virtual keyboard ready                   -> key injection active
        zwp_virtual_keyboard_manager_v1 not available -> compositor unsupported

## Related Issues

- https://github.com/onboard-osk/onboard/issues/3
