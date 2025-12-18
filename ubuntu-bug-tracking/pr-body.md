### Summary

This PR is based on a diagnostic fork used to investigate a segmentation
fault on **Ubuntu 24.04.3 (Noble, X11)**. It has two goals:

1. Make Onboard more robust when native helpers or `pypredict` are
   incomplete or missing (especially when running directly from a source
   checkout).
2. Document the Noble/X11 crash behavior and a configuration-based
   mitigation that makes the **packaged** Onboard stable and usable on
   the affected system.

### Environment (where the issue happens)

- Distro: Ubuntu 24.04.3 LTS (Noble)
- Session: X11 (`XDG_SESSION_TYPE=x11`), Wayland disabled
- Packages:
  - `onboard 1.4.1-5ubuntu6`
  - `onboard-common 1.4.1-5ubuntu6`
  - `onboard-data 1.4.1-5ubuntu6`
- Desktop: GNOME / “Unity” variant according to Onboard logs
- GNOME accessibility: enabled

### Original crash behavior

With the packaged Onboard (`/usr/bin/onboard`), typical behavior is:

- Onboard window appears and is briefly usable.
- Shortly afterwards the process exits with:

  ```text
  Segmentation fault (core dumped)
  ```

- With `--debug debug`, the last lines often include:

  ```text
  DEBUG   AutoShow              unlock('lock_visible') []
  Segmentation fault (core dumped)
  ```

A more detailed narrative (with logs and commands) is in
**`README-dev-notes.md`** and **`onboard-crash-report.txt`** in this fork.

### Changes in this PR

#### 1. Harden integration with native `osk` helpers

Files:

- `Onboard/KeyboardPopups.py`
- `Onboard/OnboardGtk.py`
- `Onboard/KbdWindow.py`

Key ideas:

- Treat `Onboard.osk.Util` and `Onboard.osk.Struts` as **optional**:
  - Wrap creations of `osk.Util()` / `osk.Struts()` in `try/except`.
  - Guard later uses (e.g. `keep_windows_on_top`, root property
    notifications) with `if _osk_util is not None`.
- Motivation:
  - On some setups (especially when running from a source tree), the
    Python `Onboard.osk` module does not always expose these attributes,
    which previously caused immediate startup failures.
  - With these changes, Onboard starts with reduced functionality rather
    than crashing if the native helpers are missing.

#### 2. Harden `pypredict` / word suggestions

Files:

- `Onboard/WordSuggestions.py`
- `Onboard/WPEngine.py`

Key ideas:

- Make `Onboard.pypredict` **optional**:
  - Import `Onboard.pypredict` in a `try/except` block.
  - When import fails or symbols like `overlay` are missing, set
    `pypredict = None` and log a warning.
- Guard all uses of `pypredict`:
  - Only construct `WPLocalEngine` / access language models if
    `pypredict` is available.
  - When it is not, **disable predictions** instead of crashing.
- Motivation:
  - On Ubuntu 24.04.3, the packaged `pypredict` installation on this
    system lacked symbols like `overlay` in `pypredict.lm`, causing
    import-time errors.
  - The change favors “no word suggestions” over runtime failures.

#### 3. Diagnostic docs and working configuration

Files:

- `README-dev-notes.md`
- `README.md`

Highlights:

- `README-dev-notes.md`:
  - Explains the environment and observed crash behavior.
  - Describes debug runs (including under `gdb`).
  - Records a **working configuration** that makes the **packaged**
    Onboard stable and usable on this Noble/X11 system.
- `README.md`:
  - Adds a short “diagnostic fork” header at the top.
  - Points maintainers to `README-dev-notes.md` and
    `onboard-crash-report.txt` as primary references.
  - Summarizes the original crash behavior and the configuration
    mitigation (see next section).

#### 4. Local schemas

- `data/gschemas.compiled`:
  - Result of running `glib-compile-schemas data` in this fork.
  - Included here for completeness; it may be better regenerated or
    excluded on your side.

### Current mitigation (Noble/X11)

On the affected system, the following configuration makes the **packaged**
Onboard (from Ubuntu) stable and usable:

- Enable GNOME accessibility (Onboard prompts for this).

- GSettings overrides:

  ```bash
  # Auto-show and related integrations
  gsettings set org.onboard.auto-show enabled false
  gsettings set org.onboard.auto-show hide-on-key-press false
  gsettings set org.onboard.auto-show tablet-mode-detection-enabled false
  gsettings set org.onboard.auto-show keyboard-device-detection-enabled false

  # Input and key synthesis
  gsettings set org.onboard.keyboard input-event-source 'GTK'
  gsettings set org.onboard.keyboard key-synth 'XTest'
  ```

With this configuration:

- `/usr/bin/onboard`:
  - Starts reliably.
  - Accepts clicks on keys.
  - Successfully injects text into other applications.
- There is still sometimes a **segfault on shutdown** (e.g. after SIGINT
  in the terminal), with logs ending roughly around:

  ```text
  OnboardGtk            SIGINT received
  OnboardGtk            Entered do_quit_onboard
  Onboard.AtspiStateTracker all listeners disconnected
  AutoShow              enable_tablet_mode_detection False None
  AutoShow              enable_keyboard_device_detection False None
  Segmentation fault (core dumped)
  ```

so this PR is **not claiming to fully fix** the underlying bug, only to
make Onboard more robust and to document a practical mitigation.

### Notes / questions

- The shutdown-time segfault still looks related to cleanup of
  AutoShow/AT-SPI/X11 interactions, even when AutoShow is disabled at
  runtime.
- I’m happy to run more targeted commands (preferably short/single-line
  ones) if that would help pinpoint the remaining crash.
- If you’d prefer a smaller PR, I can trim this down to just the
  `osk`/`pypredict` hardening changes.
