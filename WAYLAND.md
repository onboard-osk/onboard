# Running Onboard on Wayland

Onboard's primary target has historically been X11/Xorg. Initial
phase of Wayland support adds a usable experience on KDE Plasma
(Wayland), on compositors that implement the **wlr-layer-shell**
protocol (sway, Hyprland, river, Phosh), and on GNOME Mutter via
a bundled GNOME Shell extension (recommended) or an automatic
XWayland fallback.

## What works

| Capability                              | KDE (KWin rule) | GNOME native (Shell ext.) | GNOME (auto-XWayland) | wlroots (sway/Hypr/...) |
|-----------------------------------------|---|---|---|---|
| Window stays above other windows        | KWin `above=true` | `Meta.Window.make_above()` | `_NET_WM_STATE_ABOVE` | `gtk-layer-shell Layer.TOP` |
| Window never steals keyboard focus      | KWin `acceptfocus=false` | revert-on-focus extension hook | `WM_HINTS.input=false` | layer-shell `keyboard-mode=NONE` |
| Drag + resize keyboard window           | ✓ regular toplevel | ✓ regular toplevel | ✓ via XWayland | ✗ layer-shell limitation |
| Anchored / docked window                | ✓ via KWin | ✗ (Mutter limit) | `_NET_WM_STRUT_PARTIAL` | layer-shell `set_anchor` |
| Workarea shrink (struts replacement)    | ✓ via KWin | ✗ (Mutter limit) | `_NET_WM_STRUT_PARTIAL` | layer-shell `set_exclusive_zone()` |
| Auto-show on focus into a text field    | AT-SPI | AT-SPI | AT-SPI | AT-SPI |
| Key injection                           | `uinput` | `uinput` | `uinput` | `uinput` |
| Key labels refresh on layout switch     | ✓ `org.kde.KeyboardLayouts.layoutChanged` D-Bus | ✗ (not yet) | ✗ (not yet) | ✗ (not yet) |

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

### Non-KDE Wayland path (wlroots-style compositors)

On sway, Hyprland, river, Phosh, and other compositors that implement
`zwlr_layer_shell_v1`, Onboard falls back to `gtk-layer-shell`. The
keyboard becomes a layer surface anchored to the bottom of the screen,
full width. Drag and resize through the compositor are unavailable on
this path — layer-shell surfaces aren't toplevel windows. The future
plan is to add Onboard's own margin-based drag for these compositors.

### GNOME Mutter path (native Wayland via bundled Shell extension)

GNOME Mutter implements neither `zwlr_layer_shell_v1` nor a
per-window focus rule, so neither the KWin nor the wlroots path
applies. Onboard ships a tiny GNOME Shell extension
(`onboard@onboard.local`) that does for Mutter what the KWin rule
does for KWin: it watches for windows with `wm_class="Onboard"`,
calls `Meta.Window.make_above()` on them, and reverts keyboard
focus to the previously focused window when Onboard accidentally
receives it.

The extension is **auto-installed on first launch** into
`~/.local/share/gnome-shell/extensions/onboard@onboard.local/` and
enabled via `gnome-extensions enable`. Onboard injects the running
gnome-shell major version into `metadata.json` so the extension
doesn't auto-disable after each GNOME bump.

Once an extension is installed, the install function will **not**
re-enable it on subsequent launches if you have explicitly disabled
it (via `gnome-extensions disable` or the GNOME Extensions app).

After installation you should see, in `onboard --debug=info`:

```
INFO: Display server: Wayland (GNOME)
INFO: gtk-layer-shell available: False
INFO: Onboard GNOME Shell extension 'onboard@onboard.local' installed and enabled at ~/.local/share/gnome-shell/extensions/onboard@onboard.local
INFO: Using GNOME Shell extension for keyboard window (drag + resize stay available)
INFO: Using key-synth 'KeySynthEnum.UINPUT'
```

The keyboard is a regular GTK toplevel on this path, so drag + resize
work normally. **Docking is unavailable on the native path** — Mutter
has no mechanism for a regular toplevel to reserve screen space
without layer-shell. If you need docking on GNOME, opt out of the
extension and the auto-XWayland fallback (below) will provide
`_NET_WM_STRUT_PARTIAL`-based docking.

To **opt out** of the extension entirely:

```sh
gnome-extensions disable onboard@onboard.local
killall onboard
onboard
```

Onboard will then auto-route through XWayland on subsequent launches.

### GNOME Mutter path (auto-XWayland fallback)

When the bundled GNOME Shell extension is not installed or is
disabled, Onboard's launcher (`./onboard`) probes
`GtkLayerShell.is_supported()` and — when the running compositor
turns out to lack the protocol — sets `GDK_BACKEND=x11` before any
GTK code loads. Onboard then comes up as an XWayland client and uses
the X11 hints Mutter honours:

- `_NET_WM_STATE_ABOVE` (via `set_keep_above`) → keyboard stays above
  other windows.
- `WM_HINTS.input = false` (via `set_accept_focus(False)`) → keyboard
  never receives keyboard focus.
- `_NET_WM_STRUT_PARTIAL` → docking shrinks the workarea of maximized
  apps.
- `uinput` for key injection (kernel-level, backend-agnostic).

You will see the routing decision in `onboard --debug=info` output:

```
INFO: Routed through XWayland: compositor lacks both wlr-layer-shell
       and a KWin-rule equivalent. X11 hints (above, no-focus-steal,
       strut) work fine.
INFO: Display server: X11
```

To override the auto-selection (e.g. force native Wayland for
testing), export `GDK_BACKEND` yourself before launch:

```sh
GDK_BACKEND=wayland onboard
```

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

Onboard auto-selects the right backend. On compositors without
layer-shell (notably GNOME Mutter) it sets `GDK_BACKEND=x11` itself;
you don't need to set it manually. To force a backend (e.g. for
testing), export `GDK_BACKEND` before launch.

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

On a wlroots-based compositor (sway, Hyprland, ...):

```
INFO: Display server: Wayland (sway)
INFO: gtk-layer-shell available: True
INFO: Using gtk-layer-shell for keyboard window
INFO: Using key-synth 'KeySynthEnum.UINPUT'
```

On stock GNOME Mutter (auto-XWayland fallback):

```
INFO: Routed through XWayland: compositor lacks both wlr-layer-shell and a KWin-rule equivalent. X11 hints (above, no-focus-steal, strut) work fine.
INFO: Display server: X11
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

### "Keys go to the keyboard, not to my application" (GNOME)

The bundled Onboard GNOME Shell extension may have auto-disabled —
this happens silently on some GNOME major bumps. Check:

```sh
gnome-extensions info onboard@onboard.local | grep -E 'State|Version'
```

If `State: DISABLED`, re-enable with:

```sh
gnome-extensions enable onboard@onboard.local
```

If that fails citing an unsupported shell-version, edit
`~/.local/share/gnome-shell/extensions/onboard@onboard.local/metadata.json`
and add your gnome-shell major version to the `shell-version` array,
then restart gnome-shell (Wayland: log out and back in; Xorg:
`Alt+F2` → `r`).

If you'd rather not use the extension at all, disable it and Onboard
will auto-route through XWayland next launch:

```sh
gnome-extensions disable onboard@onboard.local
```

### "Docking doesn't shrink maximized apps" (sway / Hyprland)

Some wlroots-based compositors honour the layer-shell `exclusive_zone`
for floating windows but not for maximized ones — that's a compositor
limitation, not Onboard's. Switch to KDE/Mutter (via the auto-XWayland
path, which uses `_NET_WM_STRUT_PARTIAL`), or use floating + auto-show
without docking.
