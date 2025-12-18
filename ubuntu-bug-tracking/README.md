# Ubuntu Onboard Bug Tracking

This folder tracks the effort to get Ubuntu to update the `onboard` package from 1.4.1 (which segfaults) to 1.4.3+ (which is stable).

## Contents

| File | Description |
|------|-------------|
| `01-maintainer-response.md` | Response to upstream maintainer's PR feedback |
| `02-launchpad-bug-draft.md` | Draft bug report for Ubuntu Launchpad |
| `03-action-plan.md` | Overall action plan and task tracking |

## Quick Summary

- **Problem:** `onboard 1.4.1-5ubuntu6` on Ubuntu 24.04.3 segfaults with AutoShow enabled
- **Root cause:** Bug in AutoShow/visibility management code
- **Solution:** Upstream v1.4.3.post9 fixes the issue
- **Goal:** Get Ubuntu to update their package

## Status

See `03-action-plan.md` for current status and next steps.
