# Developer Notes: Onboard Segmentation Fault on Ubuntu 24.04.3

This repository is a fork of `onboard-osk/onboard` dedicated to investigating and documenting a **segmentation fault** affecting Onboard on **Ubuntu 24.04.3 LTS**.

The issue is serious from an accessibility perspective: **Onboard is crashing a few seconds after opening**, which makes it unreliable as an on-screen keyboard for users who depend on it to interact with their system. This fork and the accompanying documentation are intended to provide maintainers and contributors with a clear, reproducible description of the problem.

## Why this matters

- **Accessibility-critical**: Onboard is not just a convenience; it is a primary input method for users who cannot rely on a hardware keyboard.
- **Current behavior**: On Ubuntu 24.04.3 (X11 session, Wayland explicitly disabled), Onboard opens, shows the keyboard window, then **segfaults after a short time**.
- **Impact**: Users may lose the ability to type safely, authenticate, or perform basic tasks, especially on a fresh system where alternatives are not yet configured.

Ensuring that Onboard is stable on supported Ubuntu releases is therefore a high-priority accessibility and usability concern, not a minor cosmetic bug.

## Environment summary

A more detailed description is in `onboard-crash-report.txt`. Key points:

- **Distribution**: Ubuntu 24.04.3 LTS (Noble)
- **Kernel**: `Linux owen-GR9 6.14.0-36-generic #36~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC Wed Oct 15 15:45:17 UTC 2 x86_64 x86_64 x86_64 GNU/Linux`
- **Session**: X11 (`XDG_SESSION_TYPE=x11`, `DISPLAY=:0`)
- **Onboard packages**:
  - `onboard 1.4.1-5ubuntu6`
  - `onboard-common 1.4.1-5ubuntu6`
  - `onboard-data 1.4.1-5ubuntu6`
- **Wayland**: Explicitly disabled (for TeamViewer compatibility and to avoid earlier "Not running under X11" errors).

## Crash behavior (high level)

- Onboard is started via `/usr/bin/onboard` or `onboard`.
- The on-screen keyboard window appears and seems to work briefly.
- Within a few seconds, the window closes and the process exits with:

  ```
  Segmentation fault (core dumped)
  ```

- When run with debug logging (`/usr/bin/onboard --debug debug`), the last messages show repeated window configuration events followed by:

  ```
  DEBUG   AutoShow              unlock('lock_visible') []
  Segmentation fault (core dumped)
  ```

This strongly suggests that the crash is happening in or around the **AutoShow / visibility management** logic, likely within native GTK/X11 or related code paths.

## Files in this fork related to the crash

- `onboard-crash-report.txt`
  - Detailed narrative of the environment, steps taken, debug output excerpts, and reasoning.
  - Intended to be attached to or referenced from an upstream bug report.

As additional diagnostics are collected (e.g., full `gdb bt full` backtraces, logs, or patches), they should be added to this repository alongside these notes.

## Potential fix ideas (to investigate)

The following are **ideas for investigation**, not confirmed fixes. They are listed here to give maintainers and contributors some starting points:

- **1. Make AutoShow logic more defensive**  
  - The crash happens immediately after `AutoShow unlock('lock_visible')` is logged.  
  - Investigate whether AutoShow is assuming:
    - A valid, realized GTK window, and/or
    - A still-alive X11/AT-SPI/DBus connection.  
  - Add defensive checks (e.g., null/None checks, window existence checks, guards against re-entrancy) before calling into native code that manipulates window visibility or geometry.

- **2. Isolate integrations that may be unstable (mousetweaks, AT-SPI, etc.)**  
  - Earlier runs on Wayland showed `mousetweaks` errors ("Not running under X11"). Even on X11, similar integrations may still be involved.  
  - Consider a configuration or build-time option to **disable optional integrations** (e.g., mousetweaks/auto-click helpers, advanced accessibility hooks) and see whether the segfault disappears.  
  - If disabling a specific integration stops the crash, that would narrow the bug to a particular code path.

- **3. Review interactions with window managers / compositors on Ubuntu 24.04**  
  - Ubuntu 24.04 ships a newer GNOME / Mutter stack.  
  - Onboard’s window management code (for docking, auto-hide, avoiding overlaps, etc.) may be relying on behavior that changed across GNOME releases.  
  - Testing Onboard’s AutoShow behavior on different window managers (or with reduced window hints) might help isolate a problematic call.

- **4. Add temporary debug logging and guards around native calls**  
  - Insert targeted logs and assertions around the code that bridges from Python into native libraries (GTK, X11, accessibility APIs) when AutoShow changes visibility.  
  - If possible, short-circuit potentially unsafe operations when invariants are not met (e.g., window not yet realized, missing display, lost connection), to fail gracefully instead of segfaulting.

- **5. Provide a user-facing option to disable AutoShow / auto-hide**  
  - As a mitigation, it could help to offer a setting that turns off AutoShow/auto-hide entirely.  
  - If disabling AutoShow prevents the crash, users who depend on Onboard can at least run it in a more static mode until a full fix is implemented.

## Next steps

- Use this fork to:
  - Attach crash reports when filing bugs against Onboard/Ubuntu.
  - Experiment with possible fixes or workarounds to the AutoShow behavior and related native integrations.
- Any confirmed fix or mitigation should be proposed upstream so that users relying on Onboard for accessibility can benefit through their normal distribution packages.

## Recent debugging attempts (this fork and system Onboard)

- **Local schemas and fork startup fixes**  
  - Compiled and used the `data/org.onboard.gschema.xml` schemas from this fork (`glib-compile-schemas data`, `GSETTINGS_SCHEMA_DIR=./data ./onboard --debug debug`) to avoid schema/key mismatches such as `popup-duration`.  
  - Made the native `Onboard.osk` helpers (`Util`, `Struts`, etc.) *optional* in the fork (`KeyboardPopups`, `OnboardGtk`, `KbdWindow`) so that missing C symbols no longer prevent the source checkout from starting, at the cost of reduced functionality.

- **Prediction / pypredict disabled when incomplete**  
  - The Ubuntu Noble `pypredict` installation lacks symbols like `overlay` in `pypredict.lm`.  
  - The fork now treats `Onboard.pypredict` as optional in `WordSuggestions` and `WPEngine`: when imports fail, word predictions are disabled instead of crashing.

- **Disabling AutoShow via GSettings (system Onboard)**  
  - Tested a configuration-level mitigation on the **packaged** Onboard:  
    - `gsettings set org.onboard.auto-show enabled false`  
  - Despite AutoShow being disabled at the GSettings level, `/usr/bin/onboard` still fails to provide a stable, usable keyboard window on Ubuntu 24.04.3 in this environment.

- **Running system Onboard under gdb**  
  - Ran the packaged `/usr/bin/onboard` under `gdb` by invoking `python3` as the debug target:  
    - `gdb --args python3 /usr/bin/onboard --debug debug` (scripted via `-ex` commands to run and collect `bt full`).  
  - Under `gdb`, the Onboard window **appears and remains visible** and **does not segfault**, but it becomes effectively **unresponsive**: key presses and button clicks have no visible effect.  
  - When the gdb session ends, the window disappears. This suggests that timing/interaction with native components (GTK/X11/AT-SPI, etc.) is fragile: the debugger changes behavior from "briefly-usable then segfault" to "stable but frozen".

### Current working configuration (system Onboard)

As of the latest tests, the following combination yields a **stable and usable** Onboard instance on this system:

- **GNOME accessibility (a11y) enabled** when prompted by Onboard.
- `org.onboard.auto-show`:
  - `enabled = false` (AutoShow fully disabled).
  - `hide-on-key-press = false`.
  - `tablet-mode-detection-enabled = false`.
  - `keyboard-device-detection-enabled = false`.
- `org.onboard.keyboard`:
  - `input-event-source = 'GTK'`.
  - `key-synth = 'XTest'`.

With this configuration, launching the packaged `onboard` brings up a keyboard window that accepts clicks and successfully injects text into other applications, and it remains stable under normal use.

These observations indicate that the bug likely lies in the interaction between Onboard's AutoShow/window-management logic and the surrounding desktop stack on Ubuntu 24.04.x, rather than in pure Python exceptions. The forked code here includes small defensive changes and startup workarounds, but the primary goal remains to provide maintainers with a clear description and environment for reproducing and fixing the underlying native crash.
