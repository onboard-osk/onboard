# Launchpad Bug Report Draft

**Package:** onboard  
**URL:** https://bugs.launchpad.net/ubuntu/+source/onboard/+filebug

---

## Title

Onboard 1.4.1 segfaults on Ubuntu 24.04.3 with AutoShow enabled; fixed in upstream 1.4.3

---

## Description

### Summary

The `onboard` package (version 1.4.1-5ubuntu6) shipped with Ubuntu 24.04.3 LTS crashes with a segmentation fault shortly after launch. This is an **accessibility-critical bug** as Onboard is a primary input method for users who cannot use a hardware keyboard.

The issue is **already fixed in upstream version 1.4.3.post9**. This bug requests updating the Ubuntu package to a supported upstream version (≥1.4.2).

### Steps to Reproduce

1. Fresh install of Ubuntu 24.04.3 LTS
2. Use X11 session (Wayland disabled)
3. Install onboard: `sudo apt install onboard`
4. Enable GNOME accessibility when prompted
5. Launch onboard: `/usr/bin/onboard`
6. Wait a few seconds

### Expected Behavior

Onboard opens and remains stable as an on-screen keyboard.

### Actual Behavior

Onboard window appears briefly, then crashes with:
```
Segmentation fault (core dumped)
```

With debug logging (`onboard --debug debug`), the last lines before crash are:
```
DEBUG   AutoShow              unlock('lock_visible') []
Segmentation fault (core dumped)
```

### Environment

- **Ubuntu version:** 24.04.3 LTS (Noble Numbat)
- **Kernel:** 6.14.0-36-generic
- **Session type:** X11 (XDG_SESSION_TYPE=x11)
- **Desktop:** GNOME
- **Affected packages:**
  - onboard 1.4.1-5ubuntu6
  - onboard-common 1.4.1-5ubuntu6
  - onboard-data 1.4.1-5ubuntu6

### Workaround (Configuration-based)

Disabling AutoShow and related features via GSettings allows the packaged version to run stably:

```bash
gsettings set org.onboard.auto-show enabled false
gsettings set org.onboard.auto-show hide-on-key-press false
gsettings set org.onboard.auto-show tablet-mode-detection-enabled false
gsettings set org.onboard.auto-show keyboard-device-detection-enabled false
gsettings set org.onboard.keyboard input-event-source 'GTK'
gsettings set org.onboard.keyboard key-synth 'XTest'
```

However, this disables useful functionality.

### Solution: Update to Upstream 1.4.3

I built and tested **upstream version 1.4.3.post9** from https://github.com/onboard-osk/onboard on the same system:

- **AutoShow disabled:** Stable, ran for 2+ minutes
- **AutoShow enabled:** Stable, no segfault

The upstream maintainers confirmed they only support versions ≥1.4.2, and the bug appears to be fixed in that version.

### Impact

- **Severity:** High (accessibility-critical)
- **Users affected:** Anyone using Onboard on Ubuntu 24.04.x with default settings
- **Functionality lost:** On-screen keyboard crashes, blocking input for users who depend on it

### Request

Please consider updating the `onboard` package from 1.4.1 to 1.4.3 (or later) to resolve this segfault and align with the upstream-supported version.

### References

- Upstream repository: https://github.com/onboard-osk/onboard
- Upstream version with fix: 1.4.3.post9
- My diagnostic fork with detailed notes: https://github.com/owenpkent/onboard
  - See `README-dev-notes.md` and `onboard-crash-report.txt`

---

## Attachments to Include

1. `onboard-crash-report.txt` from this repo
2. Debug log excerpt showing crash
3. Link to diagnostic fork

---

## Tags to Add

- `segfault`
- `accessibility`
- `a11y`
- `crash`
