# Action Plan: Ubuntu Onboard Package Update

**Created:** December 17, 2025  
**Status:** In Progress

---

## Background

The `onboard` package shipped with Ubuntu 24.04.3 LTS (version 1.4.1-5ubuntu6) has a critical segfault bug that affects accessibility users. The bug is fixed in upstream version 1.4.3.post9. This action plan tracks the steps to get Ubuntu to update their package.

---

## Completed Tasks

### Phase 1: Investigation & Verification ✅

- [x] Documented the segfault behavior on Ubuntu 24.04.3
- [x] Created detailed crash report (`onboard-crash-report.txt`)
- [x] Identified crash location: AutoShow visibility management
- [x] Found configuration workaround (disable AutoShow)
- [x] Submitted PR to upstream with documentation and hardening changes

### Phase 2: Upstream Verification ✅

- [x] Received feedback from upstream maintainer (only support ≥v1.4.2)
- [x] Built and tested upstream v1.4.3.post9 from source
- [x] Confirmed segfault does NOT occur in v1.4.3.post9
- [x] Built .deb packages using `build_debs.sh`
- [x] Installed v1.4.3-9 locally — system now stable

### Phase 3: Documentation ✅

- [x] Created `ubuntu-bug-tracking/` folder
- [x] Drafted maintainer response (`01-maintainer-response.md`)
- [x] Drafted Launchpad bug report (`02-launchpad-bug-draft.md`)
- [x] Created this action plan (`03-action-plan.md`)

---

## Pending Tasks

### Phase 4: Respond to Upstream PR

- [ ] Post response to maintainer on GitHub PR
- [ ] Offer to close or trim down the PR based on their preference
- [ ] Thank them for confirming the version support policy

### Phase 5: File Ubuntu Launchpad Bug

1. **Go to:** https://bugs.launchpad.net/ubuntu/+source/onboard/+filebug
2. **Log in** with Ubuntu One account (create one if needed)
3. **Fill in bug details** from `02-launchpad-bug-draft.md`
4. **Attach:**
   - `onboard-crash-report.txt`
   - Debug log excerpt
   - Link to diagnostic fork
5. **Add tags:** `segfault`, `accessibility`, `a11y`, `crash`
6. **Subscribe** to the bug for updates
7. **Record bug number** in this file

**Launchpad Bug URL:** _(fill in after filing)_

### Phase 6: Follow Up

- [ ] Monitor Launchpad bug for responses
- [ ] Provide additional information if requested by Ubuntu developers
- [ ] Test any proposed fixes if/when available
- [ ] Update this action plan with progress

---

## Local Installation Notes

To prevent Ubuntu from overwriting the manually installed v1.4.3-9:

```bash
sudo apt-mark hold onboard onboard-common onboard-data
```

To allow updates again later:

```bash
sudo apt-mark unhold onboard onboard-common onboard-data
```

Built .deb files are stored at:
```
/home/owen/Documents/dev/onboard-upstream/build/debs/
```

---

## Key Links

| Resource | URL |
|----------|-----|
| Upstream repo | https://github.com/onboard-osk/onboard |
| My diagnostic fork | https://github.com/owenpkent/onboard |
| Launchpad (onboard) | https://bugs.launchpad.net/ubuntu/+source/onboard |
| Ubuntu package info | https://packages.ubuntu.com/noble/onboard |

---

## Timeline

| Date | Action |
|------|--------|
| Dec 17, 2025 | Initial investigation, crash report, PR submitted |
| Dec 17, 2025 | Maintainer feedback received |
| Dec 17, 2025 | Verified fix in v1.4.3.post9, installed locally |
| Dec 17, 2025 | Created tracking folder and documentation |
| TBD | File Launchpad bug |
| TBD | Respond to upstream PR |
