/*
 * Copyright © 2016 marmuta <marmvta@gmail.com>
 * Copyright © 2024 Onboard contributors
 *
 * This file is part of Onboard.
 *
 * Onboard is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * Onboard is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * Wayland backend for Onboard virtual keyboard.
 *
 * Key injection uses zwp_virtual_keyboard_unstable_v1, supported by
 * sway/wlroots, KDE Plasma >= 5.25, GNOME >= 45, Hyprland, labwc, etc.
 *
 * Generate protocol stubs before building:
 *   wayland-scanner client-header \
 *       virtual-keyboard-unstable-v1.xml \
 *       virtual-keyboard-unstable-v1-client-protocol.h
 *   wayland-scanner private-code \
 *       virtual-keyboard-unstable-v1.xml \
 *       virtual-keyboard-unstable-v1-protocol.c
 */

#include <errno.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#include <glib.h>
#include <glib/gprintf.h>
#include <gdk/gdk.h>

#ifdef GDK_WINDOWING_WAYLAND

#include <gdk/gdkwayland.h>
#include <gdk/gdkkeysyms.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>

#include "virtual-keyboard-unstable-v1-client-protocol.h"
#include "osk_virtkey_wayland.h"

/* -------------------------------------------------------------------------
 * Internal GdkWaylandKeymap mirror
 * ---------------------------------------------------------------------- */
struct _GdkWaylandKeymap
{
    GObject            parent_instance;
    GdkDisplay        *display;
    struct xkb_keymap *xkb_keymap;
    struct xkb_state  *xkb_state;
    PangoDirection    *direction;
    gboolean           bidi;
};

/* -------------------------------------------------------------------------
 * VirtkeyWayland state
 * ---------------------------------------------------------------------- */
typedef struct VirtkeyWayland VirtkeyWayland;

struct VirtkeyWayland {
    VirtkeyBase  base;

    struct wl_display   *wl_display;
    struct wl_registry  *wl_registry;
    struct wl_seat      *wl_seat;
    struct wl_keyboard  *wl_keyboard;

    struct zwp_virtual_keyboard_manager_v1 *vk_manager;
    struct zwp_virtual_keyboard_v1         *vk;

    struct xkb_keymap *xkb_keymap;
    struct xkb_state  *xkb_state;

    gboolean keymap_sent;
};

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */
static uint32_t
get_timestamp_ms (void)
{
    struct timespec ts;
    clock_gettime (CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

static int
create_keymap_fd (const char *keymap_str, size_t len)
{
    int   fd;
    char *map;
    char  path[] = "/tmp/onboard-keymap-XXXXXX";

#ifdef __NR_memfd_create
    fd = (int) syscall (__NR_memfd_create, "onboard-keymap", 0);
#else
    fd = -1;
#endif
    if (fd < 0) {
        fd = mkstemp (path);
        if (fd >= 0)
            unlink (path);
    }
    if (fd < 0) {
        g_warning ("create_keymap_fd: cannot create fd: %s", g_strerror (errno));
        return -1;
    }
    if (ftruncate (fd, (off_t) len) < 0) {
        g_warning ("create_keymap_fd: ftruncate failed: %s", g_strerror (errno));
        close (fd);
        return -1;
    }
    map = mmap (NULL, len, PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        g_warning ("create_keymap_fd: mmap failed: %s", g_strerror (errno));
        close (fd);
        return -1;
    }
    memcpy (map, keymap_str, len);
    munmap (map, len);
    return fd;
}

static void
send_keymap_to_vk (VirtkeyWayland *this)
{
    char   *keymap_str;
    size_t  len;
    int     fd;

    if (!this->vk || !this->xkb_keymap) {
        fprintf(stderr, "KEYMAP: vk=%p xkb_keymap=%p - cannot send\n",
                (void*)this->vk, (void*)this->xkb_keymap);
        return;
    }

    keymap_str = xkb_keymap_get_as_string (this->xkb_keymap,
                                            XKB_KEYMAP_FORMAT_TEXT_V1);
    if (!keymap_str) {
        g_warning ("send_keymap_to_vk: xkb_keymap_get_as_string failed");
        return;
    }
    len = strlen (keymap_str) + 1;
    fd  = create_keymap_fd (keymap_str, len);
    free (keymap_str);

    if (fd < 0)
        return;

    zwp_virtual_keyboard_v1_keymap (this->vk,
                                    WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1,
                                    fd, (uint32_t) len);
    close (fd);
    wl_display_flush (this->wl_display);
    this->keymap_sent = TRUE;
}

/* -------------------------------------------------------------------------
 * wl_keyboard listener
 * ---------------------------------------------------------------------- */
static void
keyboard_handle_keymap (void *data, struct wl_keyboard *keyboard,
                         uint32_t format, int fd, uint32_t size)
{
    VirtkeyWayland     *this = (VirtkeyWayland *) data;
    struct xkb_context *ctx;
    struct xkb_keymap  *keymap;
    char               *map_str;

    g_debug ("keyboard_handle_keymap: format %d, fd %d, size %d\n",
             format, fd, size);

    if (format != WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1) { close (fd); return; }

    ctx     = xkb_context_new (XKB_CONTEXT_NO_FLAGS);
    map_str = mmap (NULL, size, PROT_READ, MAP_SHARED, fd, 0);

    if (map_str == MAP_FAILED) {
        close (fd); xkb_context_unref (ctx); return;
    }

    keymap = xkb_keymap_new_from_string (ctx, map_str,
                                          XKB_KEYMAP_FORMAT_TEXT_V1,
                                          XKB_KEYMAP_COMPILE_NO_FLAGS);
    munmap (map_str, size);
    close (fd);
    xkb_context_unref (ctx);

    if (!keymap) {
        g_warning ("keyboard_handle_keymap: invalid keymap from compositor");
        return;
    }

    xkb_keymap_unref (this->xkb_keymap);
    this->xkb_keymap = keymap;
    xkb_state_unref (this->xkb_state);
    this->xkb_state = xkb_state_new (this->xkb_keymap);
    this->keymap_sent = FALSE;
    send_keymap_to_vk (this);
}

static void
keyboard_handle_enter (void *data, struct wl_keyboard *keyboard,
                        uint32_t serial, struct wl_surface *surface,
                        struct wl_array *keys)
{ g_debug ("keyboard_handle_enter\n"); }

static void
keyboard_handle_leave (void *data, struct wl_keyboard *keyboard,
                        uint32_t serial, struct wl_surface *surface)
{ g_debug ("keyboard_handle_leave\n"); }

static void
keyboard_handle_key (void *data, struct wl_keyboard *keyboard,
                      uint32_t serial, uint32_t time, uint32_t key,
                      uint32_t state)
{ g_debug ("keyboard_handle_key: key %d, state %d\n", key, state); }

static void
keyboard_handle_modifiers (void *data, struct wl_keyboard *keyboard,
                            uint32_t serial, uint32_t mods_depressed,
                            uint32_t mods_latched, uint32_t mods_locked,
                            uint32_t group)
{
    VirtkeyWayland *this = (VirtkeyWayland *) data;
    g_debug ("keyboard_handle_modifiers: dep=%d lat=%d loc=%d grp=%d\n",
             mods_depressed, mods_latched, mods_locked, group);
    if (this->xkb_state)
        xkb_state_update_mask (this->xkb_state,
                                mods_depressed, mods_latched, mods_locked,
                                group, 0, 0);
}

static void
keyboard_handle_repeat_info (void *data, struct wl_keyboard *keyboard,
                              int32_t rate, int32_t delay)
{ g_debug ("keyboard_handle_repeat_info: rate %d, delay %d\n", rate, delay); }

static const struct wl_keyboard_listener keyboard_listener = {
    keyboard_handle_keymap,
    keyboard_handle_enter,
    keyboard_handle_leave,
    keyboard_handle_key,
    keyboard_handle_modifiers,
    keyboard_handle_repeat_info,
};

/* -------------------------------------------------------------------------
 * wl_seat listener
 * ---------------------------------------------------------------------- */
static void
seat_handle_capabilities (void *data, struct wl_seat *seat,
                           enum wl_seat_capability caps)
{
    VirtkeyWayland *this = (VirtkeyWayland *) data;
    if ((caps & WL_SEAT_CAPABILITY_KEYBOARD) && !this->wl_keyboard) {
        this->wl_keyboard = wl_seat_get_keyboard (seat);
        wl_keyboard_set_user_data (this->wl_keyboard, this);
        wl_keyboard_add_listener (this->wl_keyboard, &keyboard_listener, this);
    } else if (!(caps & WL_SEAT_CAPABILITY_KEYBOARD) && this->wl_keyboard) {
        wl_keyboard_destroy (this->wl_keyboard);
        this->wl_keyboard = NULL;
    }
}

static void
seat_handle_name (void *data, struct wl_seat *seat, const char *name)
{ g_debug ("seat name: %s\n", name); }

static const struct wl_seat_listener seat_listener = {
    seat_handle_capabilities,
    seat_handle_name,
};

/* -------------------------------------------------------------------------
 * wl_registry listener
 * ---------------------------------------------------------------------- */
static void
global_registry_handler (void *data, struct wl_registry *registry,
                          uint32_t id, const char *interface, uint32_t version)
{
    VirtkeyWayland *this = (VirtkeyWayland *) data;
    uint32_t ver;

    g_debug ("registry: interface=%s id=%d\n", interface, id);

    if (strcmp (interface, wl_seat_interface.name) == 0) {
        ver = version < 4u ? version : 4u;
        this->wl_seat = wl_registry_bind (registry, id, &wl_seat_interface, ver);
        wl_seat_add_listener (this->wl_seat, &seat_listener, this);
    } else if (strcmp (interface,
                       zwp_virtual_keyboard_manager_v1_interface.name) == 0) {
        this->vk_manager = wl_registry_bind (
            registry, id, &zwp_virtual_keyboard_manager_v1_interface, 1);
        g_debug ("registry: zwp_virtual_keyboard_manager_v1 found\n");
    }
}

static void
global_registry_remover (void *data, struct wl_registry *registry, uint32_t id)
{ g_debug ("registry lost: id=%d\n", id); }

static const struct wl_registry_listener registry_listener = {
    global_registry_handler,
    global_registry_remover,
};

/* -------------------------------------------------------------------------
 * GDK / XKB accessors
 * ---------------------------------------------------------------------- */
static GdkKeymap *
get_gdk_keymap (VirtkeyBase *base)
{
    GdkDisplay *display = gdk_display_get_default ();
    return gdk_keymap_get_for_display (display);
}





static struct xkb_keymap *
get_xkb_keymap (VirtkeyBase *base)
{ return ((VirtkeyWayland *) base)->xkb_keymap; }

static struct xkb_state *
get_xkb_state (VirtkeyBase *base)
{ return ((VirtkeyWayland *) base)->xkb_state; }

/* -------------------------------------------------------------------------
 * Read-only queries
 * ---------------------------------------------------------------------- */
static int
virtkey_wayland_get_current_group (VirtkeyBase *base)
{
    struct xkb_keymap *keymap = get_xkb_keymap (base);
    struct xkb_state  *state  = get_xkb_state (base);
    unsigned int i;
    if (state)
        for (i = 0; i < xkb_keymap_num_layouts (keymap); i++)
            if (xkb_state_layout_index_is_active (state, i,
                                                   XKB_STATE_LAYOUT_EFFECTIVE))
                return (int) i;
    return 0;
}

static char *
virtkey_wayland_get_current_group_name (VirtkeyBase *base)
{
    struct xkb_keymap *keymap = get_xkb_keymap (base);
    int group = virtkey_wayland_get_current_group (base);
    const char *name = keymap ? xkb_keymap_layout_get_name (keymap, group) : "";
    return strdup (name ? name : "");
}

static bool
virtkey_wayland_get_auto_repeat_rate (VirtkeyBase *base,
                                      unsigned int *delay,
                                      unsigned int *interval)
{
    *delay = 500; *interval = 30;
    return true;
}

static int
virtkey_wayland_get_keycode_from_keysym (VirtkeyBase *base, int keysym,
                                          int group, unsigned int *mod_mask_out)
{
    GdkKeymap    *gdk_keymap = get_gdk_keymap (base);
    GdkKeymapKey *keys       = NULL;
    gint          n_keys     = 0;
    int           keycode    = 0;
    int           i;

    if (gdk_keymap_get_entries_for_keyval (gdk_keymap, keysym, &keys, &n_keys)) {
        for (i = 0; i < n_keys; i++) {
            GdkKeymapKey   *key = keys + i;
            guint           ks;
            gint            eg, level;
            GdkModifierType cm;

            if (key->group != group) continue;
            if (!gdk_keymap_translate_keyboard_state (gdk_keymap,
                    key->keycode, 0, group, &ks, &eg, &level, &cm))
                gdk_keymap_translate_keyboard_state (gdk_keymap,
                    key->keycode, GDK_SHIFT_MASK, group, &ks, &eg, &level, &cm);
            if (key->level == level) { keycode = key->keycode; break; }
        }
        g_free (keys);
    }
    *mod_mask_out = 0;
    return keycode;
}

static int
virtkey_wayland_get_keysym_from_keycode (VirtkeyBase *base,
                                          int keycode, int modmask, int group)
{
    GdkKeymap      *gdk_keymap = get_gdk_keymap (base);
    guint           keysym     = 0;
    gint            eg, level;
    GdkModifierType cm;
    gdk_keymap_translate_keyboard_state (gdk_keymap, keycode, modmask, group,
                                         &keysym, &eg, &level, &cm);
    return (int) keysym;
}

static void
virtkey_wayland_get_label_from_keycode (VirtkeyBase *base,
                                         int keycode, int modmask, int group,
                                         char *label, int max_label_size)
{
    int keysym = virtkey_wayland_get_keysym_from_keycode (base, keycode,
                                                            modmask, group);
    strncpy (label, virtkey_get_label_from_keysym (keysym), max_label_size);
    label[max_label_size] = '\0';
}

static char **
virtkey_wayland_get_rules_names (VirtkeyBase *base, int *numentries)
{
    const int  n = 5;
    char     **results = malloc (sizeof (char *) * n);
    int        i;
    if (!results) return NULL;
    *numentries = n;
    for (i = 0; i < n; i++) results[i] = strdup ("");
    return results;
}

static char *
virtkey_wayland_get_layout_as_string (VirtkeyBase *base)
{
    struct xkb_keymap *keymap = get_xkb_keymap (base);
    if (!keymap) return NULL;
    return xkb_keymap_get_as_string (keymap, XKB_KEYMAP_FORMAT_TEXT_V1);
}

/* -------------------------------------------------------------------------
 * Modifier helper
 * ---------------------------------------------------------------------- */
static void
collect_mods (VirtkeyWayland *this,
              uint32_t *dep, uint32_t *lat, uint32_t *loc)
{
    xkb_mod_index_t i;
    *dep = 0; *lat = 0; *loc = 0;
    if (!this->xkb_keymap || !this->xkb_state) return;
    for (i = 0; i < xkb_keymap_num_mods (this->xkb_keymap); i++) {
        uint32_t bit = 1u << i;
        if (xkb_state_mod_index_is_active (this->xkb_state, i, XKB_STATE_MODS_DEPRESSED)) *dep |= bit;
        if (xkb_state_mod_index_is_active (this->xkb_state, i, XKB_STATE_MODS_LATCHED))   *lat |= bit;
        if (xkb_state_mod_index_is_active (this->xkb_state, i, XKB_STATE_MODS_LOCKED))    *loc |= bit;
    }
}

/* -------------------------------------------------------------------------
 * Key injection
 * ---------------------------------------------------------------------- */
void
virtkey_wayland_set_group (VirtkeyBase *base, int group, bool lock)
{
    VirtkeyWayland *this = (VirtkeyWayland *) base;
    uint32_t dep, lat, loc;
    uint32_t grp = (uint32_t) group;

    if (!this->vk) { g_warning ("set_group: no virtual keyboard"); return; }
    if (!this->keymap_sent) send_keymap_to_vk (this);

    collect_mods (this, &dep, &lat, &loc);
    zwp_virtual_keyboard_v1_modifiers (this->vk, dep, lat, loc, grp);
    wl_display_flush (this->wl_display);
    if (this->xkb_state)
        xkb_state_update_mask (this->xkb_state, dep, lat, loc, grp, 0, 0);
}

void
virtkey_wayland_set_modifiers (VirtkeyBase *base,
                                unsigned int mod_mask, bool lock, bool press)
{
    VirtkeyWayland *this = (VirtkeyWayland *) base;
    uint32_t dep, lat, loc;
    uint32_t grp;

    if (!this->vk) { g_warning ("set_modifiers: no virtual keyboard"); return; }
    if (!this->keymap_sent) send_keymap_to_vk (this);

    collect_mods (this, &dep, &lat, &loc);
    grp = (uint32_t) virtkey_wayland_get_current_group (base);

    if (press) {
        if (lock) loc |= mod_mask; else dep |= mod_mask;
    } else {
        dep &= ~mod_mask; lat &= ~mod_mask;
        if (!lock) loc &= ~mod_mask;
    }

    zwp_virtual_keyboard_v1_modifiers (this->vk, dep, lat, loc, grp);
    wl_display_flush (this->wl_display);
    if (this->xkb_state)
        xkb_state_update_mask (this->xkb_state, dep, lat, loc, grp, 0, 0);
}

void
virtkey_wayland_send_key (VirtkeyBase *base, int keycode, bool press)
{
    VirtkeyWayland *this = (VirtkeyWayland *) base;
    uint32_t evdev_keycode;
    uint32_t state;

    if (!this->vk) { g_warning ("send_key: no virtual keyboard"); return; }
    if (!this->keymap_sent) send_keymap_to_vk (this);

    evdev_keycode = (uint32_t)(keycode - 8);
    state = press ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED;

    zwp_virtual_keyboard_v1_key (this->vk, get_timestamp_ms (),
                                  evdev_keycode, state);
    wl_display_flush (this->wl_display);
}

/* -------------------------------------------------------------------------
 * Lifecycle
 * ---------------------------------------------------------------------- */
static int
virtkey_wayland_init (VirtkeyBase *base)
{
    VirtkeyWayland *this = (VirtkeyWayland *) base;
    GdkDisplay     *gdk_display;

    gdk_display = gdk_display_get_default ();
    if (!GDK_IS_WAYLAND_DISPLAY (gdk_display)) {
        PyErr_SetString (OSK_EXCEPTION, "virtkey_wayland_init: not a Wayland display");
        return -1;
    }

    this->wl_display = gdk_wayland_display_get_wl_display (gdk_display);
    if (!this->wl_display) {
        PyErr_SetString (OSK_EXCEPTION, "virtkey_wayland_init: wl_display is NULL");
        return -1;
    }

    this->wl_registry = wl_display_get_registry (this->wl_display);
    wl_registry_add_listener (this->wl_registry, &registry_listener, this);
    wl_display_roundtrip (this->wl_display);
    wl_display_roundtrip (this->wl_display);

    if (!this->vk_manager) {
        g_warning ("virtkey_wayland_init: zwp_virtual_keyboard_manager_v1 not "
                   "available – key injection disabled.");
        return 0;
    }

    /* Get wl_seat from GDK to share the same connection */
    {
        GdkDisplay *gdkdisplay = gdk_display_get_default ();
        GdkSeat    *gdkseat    = gdk_display_get_default_seat (gdkdisplay);
        if (gdkseat)
            this->wl_seat = gdk_wayland_seat_get_wl_seat (gdkseat);
    }

    if (!this->wl_seat) {
        g_warning ("virtkey_wayland_init: no wl_seat found");
        return 0;
    }

    this->vk = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard (
                    this->vk_manager, this->wl_seat);
    if (!this->vk) {
        g_warning ("virtkey_wayland_init: failed to create virtual keyboard");
        return 0;
    }

    /* Get keymap from GDK directly since we may not have a main loop yet */
    if (!this->xkb_keymap) {
        GdkKeymap *gdk_keymap = gdk_keymap_get_for_display (gdk_display_get_default ());
        this->xkb_keymap = ((struct _GdkWaylandKeymap *) gdk_keymap)->xkb_keymap;
        if (this->xkb_keymap)
            xkb_keymap_ref (this->xkb_keymap);
    }
    send_keymap_to_vk (this);
    g_debug ("virtkey_wayland_init: virtual keyboard ready\n");
    return 0;
}

static int
virtkey_wayland_reload (VirtkeyBase *base)
{
    VirtkeyWayland *this = (VirtkeyWayland *) base;
    this->keymap_sent = FALSE;
    send_keymap_to_vk (this);
    return 0;
}

static void
virtkey_wayland_destruct (VirtkeyBase *base)
{
    VirtkeyWayland *this = (VirtkeyWayland *) base;
    if (this->xkb_state)   { xkb_state_unref (this->xkb_state);                      this->xkb_state   = NULL; }
    if (this->xkb_keymap)  { xkb_keymap_unref (this->xkb_keymap);                    this->xkb_keymap  = NULL; }
    if (this->vk)          { zwp_virtual_keyboard_v1_destroy (this->vk);              this->vk          = NULL; }
    if (this->vk_manager)  { zwp_virtual_keyboard_manager_v1_destroy (this->vk_manager); this->vk_manager = NULL; }
    if (this->wl_keyboard) { wl_keyboard_destroy (this->wl_keyboard);                 this->wl_keyboard = NULL; }
    if (this->wl_seat)     { wl_seat_destroy (this->wl_seat);                         this->wl_seat     = NULL; }
    if (this->wl_registry) { wl_registry_destroy (this->wl_registry);                 this->wl_registry = NULL; }
    /* wl_display borrowed from GDK – do NOT disconnect */
}

/* -------------------------------------------------------------------------
 * Constructor
 * ---------------------------------------------------------------------- */
VirtkeyBase *
virtkey_wayland_new (void)
{
    VirtkeyBase *this = (VirtkeyBase *) zalloc (sizeof (VirtkeyWayland));

    this->init                    = virtkey_wayland_init;
    this->destruct                = virtkey_wayland_destruct;
    this->reload                  = virtkey_wayland_reload;
    this->get_current_group       = virtkey_wayland_get_current_group;
    this->get_current_group_name  = virtkey_wayland_get_current_group_name;
    this->get_auto_repeat_rate    = virtkey_wayland_get_auto_repeat_rate;
    this->get_label_from_keycode  = virtkey_wayland_get_label_from_keycode;
    this->get_keysym_from_keycode = virtkey_wayland_get_keysym_from_keycode;
    this->get_keycode_from_keysym = virtkey_wayland_get_keycode_from_keysym;
    this->get_rules_names         = virtkey_wayland_get_rules_names;
    this->get_layout_as_string    = virtkey_wayland_get_layout_as_string;
    this->set_group               = virtkey_wayland_set_group;
    this->set_modifiers           = virtkey_wayland_set_modifiers;
    this->send_key                = virtkey_wayland_send_key;

    return this;
}

#endif /* GDK_WINDOWING_WAYLAND */
