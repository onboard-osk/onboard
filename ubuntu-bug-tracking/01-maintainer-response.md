# Response to Maintainer (PR Feedback)

**Date:** December 17, 2025  
**Context:** Maintainer asked if the segfault issue occurs with v1.4.3-9, noting they only support versions >=v1.4.2

---

## Response

Hi, thanks for pointing that out!

I just tested with **v1.4.3.post9** (built from upstream/main) on the same system (Ubuntu 24.04.3 LTS / X11 / GNOME).

**Good news:** The segfault does **not** reproduce with this version—Onboard runs stably even with AutoShow enabled.

### Testing Summary

| Version | AutoShow Disabled | AutoShow Enabled |
|---------|-------------------|------------------|
| **1.4.1-5ubuntu6** (Ubuntu package) | Unstable, segfaults | Segfaults within seconds |
| **1.4.3.post9** (upstream) | ✅ Stable | ✅ Stable |

### What I Did

1. **Checked out upstream/main** (v1.4.3.post9) via git worktree
2. **Built from source** using `python3 setup.py build`
3. **Tested with AutoShow disabled** — ran for 2+ minutes, keyboard fully functional
4. **Tested with AutoShow enabled** — no segfault, `AutoShow lock/unlock` operations work correctly
5. **Built .deb packages** using the project's `build_debs.sh` script
6. **Installed v1.4.3-9** — replaced the Ubuntu-packaged version
7. **Verified installation** — system Onboard now works stably with AutoShow enabled

### Conclusion

The issue I documented appears to be specific to the older Ubuntu-packaged version (**1.4.1-5ubuntu6**), which is what ships by default with Ubuntu 24.04.3 LTS. Since you only support ≥v1.4.2, this is already fixed upstream.

### Regarding This PR

Given that the crash is fixed in the supported version, I can:

1. **Close this PR** if the documentation/hardening changes aren't useful for the project, or
2. **Trim it down** to just the defensive `osk`/`pypredict` import hardening (which helps when running from source with incomplete native builds)

Let me know which you'd prefer.

### Next Steps (On My End)

I'm planning to file a bug on **Ubuntu Launchpad** requesting that the `onboard` package be updated from 1.4.1 to a supported upstream version (≥1.4.2). This would help Ubuntu users who depend on Onboard for accessibility.

Thanks for your time and for maintaining Onboard!

---

## Environment Details (For Reference)

- **Distro:** Ubuntu 24.04.3 LTS (Noble)
- **Kernel:** `6.14.0-36-generic`
- **Session:** X11 (`XDG_SESSION_TYPE=x11`)
- **Desktop:** GNOME
- **Original packages:**
  - `onboard 1.4.1-5ubuntu6`
  - `onboard-common 1.4.1-5ubuntu6`
  - `onboard-data 1.4.1-5ubuntu6`
- **Upgraded packages:**
  - `onboard 1.4.3-9`
  - `onboard-common 1.4.3-9`
  - `onboard-data 1.4.3-9`
