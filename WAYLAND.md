# Running Onboard on Wayland

Onboard's primary target has historically been X11/Xorg. Initial phase of Wayland
support adds a usable experience on KDE Plasma (Wayland) and on compositors
that implement the **wlr-layer-shell** protocol — sway, Hyprland, river,
phosh, and (with caveats) GNOME Mutter.

## What works

| Capability                                      | How it's implemented |
|-------------------------------------------------|---|
| Window stays above other windows                | KDE: KWin rule `above=true,aboverule=2`  · other: `gtk-layer-shell Layer.TOP` |
| Window never steals keyboard focus              | KDE: KWin rule `acceptfocus=false,acceptfocusrule=2`  · other: layer-shell `keyboard-mode=NONE` |
| Drag + resize keyboard window                   | KDE: ✓ (regular toplevel)  · other: ✗ (layer-shell limitation) |
| Anchored / docked window                        | layer-shell `set_anchor(BOTTOM/TOP)` |
| Workarea shrink (struts replacement)            | layer-shell `set_exclusive_zone()` |
| Auto-show on focus into a text field            | AT-SPI (`gir1.2-atspi-2.0`) |
| Key injection                                   | `uinput` (Linux kernel) |
| Key labels refresh on layout switch             | KDE: ✓ (`org.kde.KeyboardLayouts.layoutChanged` D-Bus signal)  · other: ✗ (not yet) |

### KDE Plasma path (preferred)

On detection of `XDG_CURRENT_DESKTOP=KDE` (or `KDE_FULL_SESSION=true`),
Onboard installs a window rule into `~/.config/kwinrulesrc` keyed on the
Wayland `app_id="onboard"` and asks KWin to reload (via
`org.kde.KWin.reconfigure`). The rule sets `acceptfocus=false` so KWin
never gives the keyboard window keyboard focus, even on click. The window
itself remains a regular GTK toplevel — so dragging it by its frame works,
resizing works, and the position is remembered by KWin across runs. To
remove the rule, delete the `[onboard]` section from
`~/.config/kwinrulesrc` and the `onboard,` entry from the
`[General] rules` list.

### Non-KDE Wayland path

On sway, Hyprland, GNOME Mutter, river, Phosh, etc. (no per-window-rule
mechanism), Onboard falls back to `gtk-layer-shell`. The keyboard becomes
a layer surface anchored to the bottom of the screen, full width. Drag
and resize through the compositor are unavailable on this path —
layer-shell surfaces aren't toplevel windows. The future plan is to add Onboard's
own margin-based drag for these compositors.

## Limitations / not yet implemented

- **Native `virtual-keyboard-v1` Wayland backend** — would remove the
  `/dev/uinput` permission requirement entirely.
- **XTest "convert primary click"** — XTest is X11-only. Disabled on Wayland.
- **XInput-based touch handling** — falls back to GTK events.
- **Drag/resize on non-KDE Wayland** — see "Non-KDE Wayland path" above.
- **Tablet-mode hotkey detection that relied on XInput device events.**
- **Layout-switch refresh on non-KDE Wayland.** On KDE we subscribe to
  `org.kde.KeyboardLayouts.layoutChanged` so the key labels refresh as soon as
  the user switches input source from the tray.
  GNOME Mutter exposes the equivalent via GSettings
  (`org.gnome.desktop.input-sources current`); sway / Hyprland / river
  have no equivalent channel and would need either
  `zwp_input_method_v2` integration or low-frequency polling. Until
  that lands on non-KDE compositors, the keyboard's labels will only
  catch up on the next pointer-enter event after a layout switch.

## One-time setup

### 1. Install runtime dependencies

Debian/Ubuntu/Kubuntu:

```sh
sudo apt install gir1.2-gtklayershell-0.1 gir1.2-atspi-2.0
```

Arch / KDE neon nightly:

```sh
sudo pacman -S gtk-layer-shell at-spi2-core
```

Fedora:

```sh
sudo dnf install gtk-layer-shell at-spi2-core
```

(`gtk-layer-shell` is only used on non-KDE compositors but is still
recommended, e.g. as a fallback if it ever runs outside KDE.)

### 2. Enable `/dev/uinput` for your user

Onboard ships a udev rule at `data/72-onboard-uinput.rules`. The Debian
package installs it to `/lib/udev/rules.d/` automatically. From source:

```sh
sudo cp data/72-onboard-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger /dev/uinput
```

That's it. **No `input` group membership and no re-login are required.**
The rule:

- grants the active user a POSIX ACL on `/dev/uinput` via the
  systemd-logind `uaccess` mechanism;
- when Onboard creates its virtual keyboard, sets `ID_SEAT=seat0` and
  `TAG+=uaccess` on the resulting `/dev/input/eventN` so the Wayland
  compositor (running as you) can read events back from it.

The `72-` filename prefix is intentional: it makes our rule run
*just before* `73-seat-late.rules`, which then adds the `seat0` tag
based on our `ID_SEAT` and fires the `uaccess` builtin that writes
the actual ACL. We don't have to invoke the builtin ourselves and
we don't have to add the seat tag explicitly — `73-seat-late.rules`
does both for us as long as we run earlier.

systemd-logind only EVIOCGRABs input devices that advertise
`KEY_POWER`/`KEY_SLEEP`/`KEY_WAKEUP`/`KEY_SUSPEND`/`KEY_KBDILLUM*`,
so Onboard's uinput backend explicitly omits those capability bits
when registering the virtual device (`Onboard/osk/osk_uinput.c`).
That means the `power-switch` tag systemd's `70-power-switch.rules`
puts on every keyboard is harmless on us — no separate `TAG-=` is
needed.

You can verify the ACL is applied while Onboard is running:

```sh
getfacl /dev/uinput | grep "user:$USER"     # should show 'rw-'
# Once Onboard is started:
getfacl $(ls -t /dev/input/event* | head -1) | grep "user:$USER"
```

Both should report `rw-` for your user.

### 3. Run

As usual,

```sh
onboard
```

Make sure to remove `GDK_BACKEND=x11` if you were earlier experimenting with it. 

If you run

```sh
onboard --debug=info
```

The startup log will say something like (on KDE):

```
INFO: Display server: Wayland (KDE)
INFO: KWin window rule 'onboard' installed at ~/.config/kwinrulesrc
INFO: Using KWin window rule for keyboard window (drag + resize stay available)
INFO: Using key-synth 'KeySynthEnum.UINPUT'
```

Or on a non-KDE Wayland compositor:

```
INFO: Display server: Wayland (sway)
INFO: Using gtk-layer-shell for keyboard window
INFO: Using key-synth 'KeySynthEnum.UINPUT'
```

If the key-synth line says `ATSPI` instead of `UINPUT`, uinput failed —
the udev rule isn't installed (or hasn't been reloaded). See step 2.

## Troubleshooting

### "Onboard appears but nothing is typed"

Almost always a uinput / udev rule issue. Quick diagnostic:

```sh
onboard --debug=DEBUG 2>&1 | \
    grep -iE 'wayland:|key-synth|uinput|kwin'
```

If you see `Wayland: Found /dev/input/eventN but it is not readable`, the
udev rule isn't applied. Re-run step 2 of the setup.

If you see `Using key-synth 'KeySynthEnum.ATSPI'` and no Wayland warning,
your kernel was built without uinput. Check with `modinfo uinput` and
`grep CONFIG_INPUT_UINPUT /boot/config-$(uname -r)`.

### "Keys go to the keyboard, not to my application" (KDE)

The KWin rule may not have been picked up. Verify:

```sh
grep -A 2 '\[onboard\]' ~/.config/kwinrulesrc
```

The section must include `acceptfocus=false` and `acceptfocusrule=2`.
If it does, ask KWin to reload the rules:

```sh
qdbus6 org.kde.KWin /KWin reconfigure
```

### "Docking doesn't shrink maximized apps" (non-KDE)

GNOME Mutter honours the layer-shell `exclusive_zone` for floating
windows but not for maximized ones — that's a Mutter limitation. Switch
to KDE/sway/Hyprland for full docking, or use floating + auto-show
without docking.

