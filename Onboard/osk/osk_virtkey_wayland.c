
/*
 * Copyright © 2016 marmuta <marmvta@gmail.com>
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

#include <sys/mman.h>

#include <glib.h>
#include <glib/gprintf.h>

#include <gdk/gdk.h>

#ifdef GDK_WINDOWING_WAYLAND
#include <gdk/gdkwayland.h>
#include <gdk/gdkkeysyms.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include <linux/input-event-codes.h>


#include "osk_virtkey_wayland.h"
#include "osk_uinput.h"

#define N_MOD_INDICES (Mod5MapIndex + 1)

typedef struct VirtkeyWayland VirtkeyWayland;

struct VirtkeyWayland {
    VirtkeyBase base;

    struct wl_display *wl_display;
    struct wl_registry *wlregistry;
    struct wl_seat *wlseat;
    struct wl_keyboard *wl_keyboard;

    struct xkb_keymap *xkb_keymap;
    struct xkb_state *xkb_state;
};

struct _GdkWaylandKeymap
{
  GObject     parent_instance;
  GdkDisplay *display;

  struct xkb_keymap *xkb_keymap;
  struct xkb_state *xkb_state;

  PangoDirection *direction;
  gboolean bidi;
};

static GdkKeymap*
get_gdk_keymap (VirtkeyBase *base)
{
//    return gdk_keymap_get_default();
GdkDisplay *display = gdk_display_get_default ();
return gdk_keymap_get_for_display(display);
}

static struct xkb_keymap*
get_gdk_xkb_keymap (VirtkeyBase *base)
{
    GdkKeymap* gdk_keymap = get_gdk_keymap(base);
    struct xkb_keymap* xkb_keymap =
        ((struct _GdkWaylandKeymap*)gdk_keymap)->xkb_keymap;
    return xkb_keymap;
}

static struct xkb_state*
get_gdk_xkb_state (VirtkeyBase *base)
{
    GdkKeymap* gdk_keymap = get_gdk_keymap(base);
    //struct xkb_state* state = GDK_WAYLAND_KEYMAP (keymap)->xkb_state;
    struct xkb_state* xkb_state =
        ((struct _GdkWaylandKeymap*)gdk_keymap)->xkb_state;
    return xkb_state;
}

static struct xkb_keymap*
get_xkb_keymap (VirtkeyBase *base)
{
    VirtkeyWayland* this = (VirtkeyWayland*) base;
    return this->xkb_keymap;
}

static struct xkb_state*
get_xkb_state (VirtkeyBase *base)
{
    VirtkeyWayland* this = (VirtkeyWayland*) base;
    return this->xkb_state;
}

static int
virtkey_wayland_get_current_group (VirtkeyBase *base)
{
    // Gdk's xkb_state doesn't know the currently active layout (group)
    // (Xenial). Use our own xkp_keymap instead.
    struct xkb_keymap* xkb_keymap = get_xkb_keymap(base);
    struct xkb_state* xkb_state = get_xkb_state(base);
    if (xkb_state)
    {
        unsigned int i;
        for (i = 0; i < xkb_keymap_num_layouts (xkb_keymap); i++)
        {
            if (xkb_state_layout_index_is_active(xkb_state, i, 
                                                 XKB_STATE_LAYOUT_EFFECTIVE))
                return i;
        }
    }
    return 0;
}

static char*
virtkey_wayland_get_current_group_name (VirtkeyBase* base)
{
    struct xkb_keymap* xkb_keymap = get_xkb_keymap(base);
    int group = virtkey_wayland_get_current_group(base);
    const char* name = "";
    if (xkb_keymap)
        name = xkb_keymap_layout_get_name(xkb_keymap, group);
    //g_debug("virtkey_wayland_get_current_group_name %d '%s'\n", group, name);
    return strdup(name);
}

static bool
virtkey_wayland_get_auto_repeat_rate (VirtkeyBase *base,
                                      unsigned int *delay, 
                                      unsigned int *interval)
{
    *delay = 500;
    *interval = 30;
    return true;
}

static int
virtkey_wayland_get_keycode_from_keysym (VirtkeyBase* base, int target_keysym,
                                         int group,
                                         unsigned int *mod_mask_out)
{
    /* Modifier masks we will probe, smallest first. Anything more exotic
     * (e.g. Level5/ISO_Level5_Shift) is rare on typical desktop layouts and
     * left to a future extension. */
    static const GdkModifierType trial_masks[] = {
        0,
        GDK_SHIFT_MASK,
        GDK_MOD5_MASK,                       /* AltGr */
        GDK_SHIFT_MASK | GDK_MOD5_MASK,      /* shift + AltGr */
    };
    int keycode = 0;
    unsigned int mod_mask = 0;
    GdkKeymap* gdk_keymap = get_gdk_keymap(base);
    GdkKeymapKey* keys;
    gint n_keys;

    *mod_mask_out = 0;

    g_debug("virtkey_wayland_get_keycode_from_keysym: keysym %d, group %d\n",
            target_keysym, group);

    if (!gdk_keymap_get_entries_for_keyval(gdk_keymap, target_keysym,
                                           &keys, &n_keys))
        return 0;

    {
        gint i;
        for (i = 0; i < n_keys; i++)
        {
            GdkKeymapKey* key = &keys[i];
            g_debug("    candidate keycode %d, group %d, level %d\n",
                    key->keycode, key->group, key->level);
        }
    }

    /* Pass 1: prefer an entry in the active group. For each candidate
     * (keycode, mask) pair, ask the compositor's keymap what keysym it
     * would actually produce. Accept the smallest mask that yields the
     * target keysym. */
    {
        size_t m;
        for (m = 0; m < G_N_ELEMENTS(trial_masks) && !keycode; m++)
        {
            gint i;
            for (i = 0; i < n_keys; i++)
            {
                GdkKeymapKey* key = &keys[i];
                guint produced_keysym;
                gint effective_group, level;
                GdkModifierType consumed;

                if (key->group != group)
                    continue;
                if (!gdk_keymap_translate_keyboard_state(
                            gdk_keymap, key->keycode, trial_masks[m], group,
                            &produced_keysym, &effective_group, &level,
                            &consumed))
                    continue;
                if ((int) produced_keysym == target_keysym)
                {
                    keycode  = key->keycode;
                    mod_mask = trial_masks[m];
                    g_debug("    selected  keycode %d, group %d, level %d, "
                            "mod_mask 0x%x (consumed 0x%x)\n",
                            key->keycode, key->group, level,
                            mod_mask, (unsigned int) consumed);
                    break;
                }
            }
        }
    }

    /* Pass 2: nothing matched in the active group. This happens for
     * keysyms only defined in the base layout and not redefined per
     * language (F1-F12, arrows, Tab, Backspace, Insert, Home, etc.).
     * X11's XkbOutOfRangeGroupAction handles this via WrapIntoRange;
     * GDK doesn't expose that, so we pick the entry with the lowest
     * group number, which is the typical wrap target. Such keys have
     * single-level types (FUNCTION/ONE_LEVEL), so accepting level 0
     * unconditionally is safe. */
    if (!keycode)
    {
        int best_group = -1;
        gint i;
        for (i = 0; i < n_keys; i++)
        {
            GdkKeymapKey* key = &keys[i];
            if (key->level != 0)
                continue;
            if (best_group < 0 || key->group < best_group)
            {
                keycode = key->keycode;
                best_group = key->group;
            }
        }
        if (keycode)
            g_debug("    fallback  keycode %d (lowest group %d, "
                    "current group %d had no entry)\n",
                    keycode, best_group, group);
    }

    g_free(keys);

    g_debug("    final     keycode %d, mod_mask 0x%x\n", keycode, mod_mask);

    *mod_mask_out = mod_mask;

    return keycode;
}

static int
virtkey_wayland_get_keysym_from_keycode(VirtkeyBase* base,
                                  int keycode, int modmask, int group)
{
    GdkKeymap* gdk_keymap = get_gdk_keymap(base);
    guint keysym = 0;
    gint effective_group;
    gint level;
    GdkModifierType consumed_modifiers;

    gdk_keymap_translate_keyboard_state(gdk_keymap, keycode, modmask, group,
                                        &keysym, &effective_group, &level,
                                        &consumed_modifiers);
    //g_debug("virtkey_wayland_get_keysym_from_keycode: keycode %d, modmask %d, group %d, keysym %d\n", keycode, modmask, group, keysym);

    return keysym;
}

static void
virtkey_wayland_get_label_from_keycode(VirtkeyBase* base,
    int keycode, int modmask, int group,
    char* label, int max_label_size)
{
    int keysym = virtkey_wayland_get_keysym_from_keycode(
                        base, keycode, modmask, group);
    strncpy(label, virtkey_get_label_from_keysym(keysym), max_label_size);
    label[max_label_size] = '\0';

    //g_debug("virtkey_wayland_get_label_from_keycode: keycode: %d, modmask %d, group %d, label '%s'\n",
    //        keycode, modmask, group, label);
 }

/**
 * Read the contents of the root window property _XKB_RULES_NAMES.
 */
static char**
virtkey_wayland_get_rules_names(VirtkeyBase* base, int* numentries)
{
    char** results;
    const int n = 5;

    results = malloc(sizeof(char*) * n);
    if (!results)
        return NULL;

    *numentries = n;

    results[0] = strdup("");
    results[1] = strdup("");
    results[2] = strdup("");
    results[3] = strdup("");
    results[4] = strdup("");

    return results;
}

/*
 * Return a string representative of the whole layout including all groups.
 * Caller takes ownership, call free() on the result.
 */
static char*
virtkey_wayland_get_layout_as_string (VirtkeyBase* base)
{
    char* result = NULL;
    struct xkb_keymap* xkb_keymap = get_xkb_keymap(base);
    if (xkb_keymap)
    {
        result = xkb_keymap_get_as_string(xkb_keymap,
                                          XKB_KEYMAP_USE_ORIGINAL_FORMAT);
    }
    return result;
}

void
virtkey_wayland_set_group (VirtkeyBase* base, int group, bool lock)
{
}

/*
 * X11/GDK modifier bit -> evdev keycode (Linux value, NOT the X11 +8 offset).
 * GDK_*_MASK and X11 *Mask share the same bit pattern, so this also matches
 * the bits Onboard's utils.Modifiers enum uses.
 *
 * LockMask/Mod2Mask/Mod3Mask are deliberately omitted: CapsLock/NumLock
 * are stateful at the compositor and toggling them per-keystroke would
 * have side effects far beyond the intended modifier; Mod3 isn't used by
 * Onboard.
 */
struct wayland_mod_map { unsigned int x11_bit; int evdev_keycode; };
static const struct wayland_mod_map WAYLAND_MOD_MAP[] = {
    { GDK_SHIFT_MASK,   KEY_LEFTSHIFT },  /* 0x01 -> 42  */
    { GDK_CONTROL_MASK, KEY_LEFTCTRL  },  /* 0x04 -> 29  */
    { GDK_MOD1_MASK,    KEY_LEFTALT   },  /* 0x08 -> 56  */
    { GDK_MOD4_MASK,    KEY_LEFTMETA  },  /* 0x40 -> 125 */
    { GDK_MOD5_MASK,    KEY_RIGHTALT  },  /* 0x80 -> 100 (AltGr) */
};

void
virtkey_wayland_set_modifiers (VirtkeyBase* base,
                         unsigned int mod_mask, bool lock, bool press)
{
    (void) base;
    (void) lock;  /* uinput is stateless; the caller pairs press with release. */

    /* Only meaningful when the uinput backend is currently open. The ATSPI
     * fallback never opens uinput, so this stays a no-op there. */
    if (!uinput_is_open())
        return;

    g_debug("virtkey_wayland_set_modifiers: mod_mask 0x%x, lock %d, press %d\n",
            mod_mask, lock, press);

    {
        size_t i;
        for (i = 0; i < G_N_ELEMENTS(WAYLAND_MOD_MAP); i++)
        {
            if (mod_mask & WAYLAND_MOD_MAP[i].x11_bit)
            {
                /* osk_uinput_send_key_event_to() subtracts 8 from the
                 * keycode before writing, so we pass the X11 keycode
                 * space value (evdev + 8). */
                uinput_send_key_event(
                    WAYLAND_MOD_MAP[i].evdev_keycode + 8, press);
                g_debug("    %s evdev keycode %d (bit 0x%x)\n",
                        press ? "press  " : "release",
                        WAYLAND_MOD_MAP[i].evdev_keycode,
                        WAYLAND_MOD_MAP[i].x11_bit);
            }
        }
    }
}

static void
keyboard_handle_keymap(void *data, struct wl_keyboard *keyboard,
                       uint32_t format, int fd, uint32_t size)
{
    VirtkeyWayland* this = (VirtkeyWayland*) data;
    struct xkb_context *context;
    struct xkb_keymap *xkb_keymap;
    char *map_str;

    g_debug("keyboard_handle_keymap: format %d, fd %d, size %d\n", format, fd, size);

    context = xkb_context_new (XKB_CONTEXT_NO_FLAGS);

    map_str = mmap (NULL, size, PROT_READ, MAP_SHARED, fd, 0);
    if (map_str == MAP_FAILED)
    {
        close(fd);
        return;
    }

    xkb_keymap = xkb_keymap_new_from_string (context, map_str, format, 0);
    munmap (map_str, size);
    close (fd);

    if (!xkb_keymap)
    {
        g_warning ("Got invalid keymap from compositor, keeping previous/default one");
        xkb_context_unref (context);
        return;
    }

    xkb_keymap_unref (this->xkb_keymap);
    this->xkb_keymap = xkb_keymap;

    xkb_state_unref (this->xkb_state);
    this->xkb_state = xkb_state_new (this->xkb_keymap);

    xkb_context_unref (context);

    {
        unsigned int i;
        for (i = 0; i < xkb_keymap_num_layouts (this->xkb_keymap); i++)
        {
            g_debug("   layout index %d, active %d, \n", i,
                xkb_state_layout_index_is_active (this->xkb_state, i, XKB_STATE_LAYOUT_EFFECTIVE));
        }
    }
}

static void
keyboard_handle_enter(void *data, struct wl_keyboard *keyboard,
                      uint32_t serial, struct wl_surface *surface,
                      struct wl_array *keys)
{
    g_debug("keyboard_handle_enter\n");
}

static void
keyboard_handle_leave(void *data, struct wl_keyboard *keyboard,
                      uint32_t serial, struct wl_surface *surface)
{
    g_debug("keyboard_handle_leave\n");
}

static void
keyboard_handle_key(void *data, struct wl_keyboard *keyboard,
                    uint32_t serial, uint32_t time, uint32_t key,
                    uint32_t state)
{
    g_debug("keyboard_handle_key: key %d, state %d\n", key, state);
}

static void
keyboard_handle_modifiers(void *data, struct wl_keyboard *keyboard,
                          uint32_t serial, uint32_t mods_depressed,
                          uint32_t mods_latched, uint32_t mods_locked,
                          uint32_t group)
{
    VirtkeyWayland* this = data;

    g_debug("keyboard_handle_modifiers: depressed %d, latched %d, locked %d, group %d\n",
            mods_depressed, mods_latched, mods_locked, group);

    xkb_state_update_mask (this->xkb_state, mods_depressed, mods_latched, mods_locked, group, 0, 0);
    {
        VirtkeyBase* base = data;
        struct xkb_keymap* xkb_keymap = get_gdk_xkb_keymap(base);
        struct xkb_state* xkb_state = get_gdk_xkb_state(base);
        unsigned int i;
        for (i = 0; i < xkb_keymap_num_layouts (xkb_keymap); i++)
        {
            g_debug("   gdk layout index %d, active %d, name %s\n", i,
                xkb_state_layout_index_is_active (xkb_state, i, XKB_STATE_LAYOUT_EFFECTIVE),
                xkb_keymap_layout_get_name(xkb_keymap, i) );
        }
    }
    {
        VirtkeyBase* base = data;
        struct xkb_keymap* xkb_keymap = get_xkb_keymap(base);
        struct xkb_state* xkb_state = get_xkb_state(base);
        unsigned int i;
        for (i = 0; i < xkb_keymap_num_layouts (xkb_keymap); i++)
        {
            g_debug("   wl layout index %d, active %d, name %s\n", i,
                xkb_state_layout_index_is_active (xkb_state, i, XKB_STATE_LAYOUT_EFFECTIVE),
                xkb_keymap_layout_get_name(xkb_keymap, i) );
        }
        g_debug("   current group %d\n", virtkey_wayland_get_current_group(base));
    }
}

static void
keyboard_handle_repeat_info (void               *data,
                             struct wl_keyboard *keyboard,
                             int32_t             rate,
                             int32_t             delay)
{
    g_debug("keyboard_handle_repeat_info: rate %d, delay %d\n", rate, delay);
}

static const struct wl_keyboard_listener keyboard_listener = {
    keyboard_handle_keymap,
    keyboard_handle_enter,
    keyboard_handle_leave,
    keyboard_handle_key,
    keyboard_handle_modifiers,
    keyboard_handle_repeat_info,
};

static void
seat_handle_capabilities(void *data, struct wl_seat *seat,
                         enum wl_seat_capability caps)
{
    VirtkeyWayland* this = (VirtkeyWayland*) data;

    g_debug("seat_handle_capabilities %d\n", caps);

    if (caps & WL_SEAT_CAPABILITY_POINTER) {
        g_debug("Display has a pointer\n");
    }

    if (caps & WL_SEAT_CAPABILITY_KEYBOARD)
    {
        g_debug("Display has a keyboard\n");
        this->wl_keyboard = wl_seat_get_keyboard(seat);
        wl_keyboard_set_user_data(this->wl_keyboard, this);
        wl_keyboard_add_listener(this->wl_keyboard, &keyboard_listener, this);
    }
    else
    if (!(caps & WL_SEAT_CAPABILITY_KEYBOARD))
    {
        wl_keyboard_destroy(this->wl_keyboard);
        this->wl_keyboard = NULL;
    }


    if (caps & WL_SEAT_CAPABILITY_TOUCH) {
        g_debug("Display has a touch screen\n");
    }
}

static const struct wl_seat_listener seat_listener =
{
    seat_handle_capabilities,
};

static void
global_registry_handler(void *data, struct wl_registry *registry, uint32_t id,
                        const char *interface, uint32_t version)
{
    VirtkeyWayland* this = (VirtkeyWayland*) data;

    g_debug("registry event for %s id, %d data %p\n", interface, id, data);
    if (strcmp(interface, "wl_seat") == 0)
    {
        this->wlseat = wl_registry_bind(registry, id, &wl_seat_interface, 1);
        wl_seat_add_listener(this->wlseat, &seat_listener, this);
    }
}

static void
global_registry_remover(void *data, struct wl_registry *registry, uint32_t id)
{
    g_debug("registry lost for %d\n", id);
}

static const struct wl_registry_listener registry_listener = {
    global_registry_handler,
    global_registry_remover
};

void custom_log_handler (const gchar *log_domain, GLogLevelFlags log_level, 
                         const gchar *message, gpointer user_data)
{
    if (!log_domain)
        g_printf ("%s", message);
}

static int
virtkey_wayland_init (VirtkeyBase *base)
{
    VirtkeyWayland* this = (VirtkeyWayland*) base;
    struct wl_registry;
    GdkDisplay* display = gdk_display_get_default ();

#if 0
    this->wl_keyboard = gdk_wayland_device_get_wl_keyboard ();
    wl_keyboard_set_user_data(this->wl_keyboard, this);
    wl_keyboard_add_listener(this->wl_keyboard, &keyboard_listener, this);
    return 0;
#endif

    #if 0
    g_log_set_handler(G_LOG_DOMAIN, G_LOG_LEVEL_DEBUG,
                      custom_log_handler, NULL);
    #endif

    this->wl_display = gdk_wayland_display_get_wl_display (display);
    if (this->wl_display == NULL) {
        PyErr_SetString (OSK_EXCEPTION, "wl_display_connect failed.");
        return -1;
    }

    this->wlregistry = wl_display_get_registry(this->wl_display);
    wl_registry_add_listener(this->wlregistry, &registry_listener, this);
    wl_display_dispatch(this->wl_display);
    wl_display_roundtrip(this->wl_display);

    return 0;
}

static int
virtkey_wayland_reload (VirtkeyBase* base)
{
    return 0;
}

static void
virtkey_wayland_destruct (VirtkeyBase *base)
{
    VirtkeyWayland* this = (VirtkeyWayland*) base;
    if (this->wl_display)
    {
        wl_display_disconnect(this->wl_display);
        this->wl_display = NULL;
    }
}

VirtkeyBase*
virtkey_wayland_new(void)
{
   VirtkeyBase* this = (VirtkeyBase*) zalloc(sizeof(VirtkeyWayland));

   this->init = virtkey_wayland_init;
   this->destruct = virtkey_wayland_destruct;
   this->reload = virtkey_wayland_reload;
   this->get_current_group = virtkey_wayland_get_current_group;
   this->get_current_group_name = virtkey_wayland_get_current_group_name;
   this->get_auto_repeat_rate = virtkey_wayland_get_auto_repeat_rate;
   this->get_label_from_keycode = virtkey_wayland_get_label_from_keycode;
   this->get_keysym_from_keycode = virtkey_wayland_get_keysym_from_keycode;
   this->get_keycode_from_keysym = virtkey_wayland_get_keycode_from_keysym;
   this->get_rules_names = virtkey_wayland_get_rules_names;
   this->get_layout_as_string = virtkey_wayland_get_layout_as_string;
   this->set_group = virtkey_wayland_set_group;
   this->set_modifiers = virtkey_wayland_set_modifiers;
   return this;
}

#endif  /* GDK_WINDOWING_WAYLAND */

