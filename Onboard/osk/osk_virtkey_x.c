
/*
 * Copyright © 2006-2008 Chris Jones <tortoise@tortuga>
 * Copyright © 2010, 2016 marmuta <marmvta@gmail.com>
 * Copyright © 2013 Gerd Kohlberger <lowfi@chello.at>
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

#include <gdk/gdkx.h>
#include <gdk/gdkkeysyms.h>

#include <X11/keysym.h>
#include <X11/XKBlib.h>
#include <X11/extensions/XTest.h>
#include <X11/extensions/XKBrules.h>

#include "osk_virtkey_x.h"

#define N_MOD_INDICES (Mod5MapIndex + 1)

typedef struct VirtkeyX VirtkeyX;

struct VirtkeyX {
    VirtkeyBase base;

    Display   *xdisplay;

    int xkb_base_event;
    XkbDescPtr kbd;
};

static int virtkey_x_reload (VirtkeyBase* base);

static int
virtkey_x_get_current_group (VirtkeyBase *base)
{
    VirtkeyX* this = (VirtkeyX*) base;
    XkbStateRec state;

    if (XkbGetState (this->xdisplay, XkbUseCoreKbd, &state) != Success)
    {
        PyErr_SetString (OSK_EXCEPTION, "XkbGetState failed");
        return -1;
    }
    return state.locked_group;
}

static char*
virtkey_x_get_current_group_name (VirtkeyBase* base)
{
    VirtkeyX* this = (VirtkeyX*) base;
    char* result = NULL;
    int group;

    if (!this->kbd->names)
    {
        PyErr_SetString (OSK_EXCEPTION, "no group names available");
        return NULL;
    }
/* fixed warning comparison will always evaluate as true
    if (!this->kbd->names->groups)
    {
        PyErr_SetString (OSK_EXCEPTION, "no groups available");
        return NULL;
    }
*/
    group = virtkey_x_get_current_group(base);
    if (group < 0)
        return NULL;

    if (this->kbd->names->groups[group] != None)
    {
        char *name = XGetAtomName (this->xdisplay,
                                   this->kbd->names->groups[group]);
        if (name)
        {
            result = strdup(name);
            XFree (name);
        }
    }

    return result;
}

static bool
virtkey_x_get_auto_repeat_rate (VirtkeyBase *base,
                                unsigned int *delay, unsigned int *interval)
{
    VirtkeyX* this = (VirtkeyX*) base;

    if (!XkbGetAutoRepeatRate (this->xdisplay, XkbUseCoreKbd, delay, interval))
    {
        PyErr_SetString (OSK_EXCEPTION, "XkbGetAutoRepeatRate failed");
        return false;
    }
    return true;
}

/*
 * Return group for keycode with out-of-range action applied.
 * */
static int
get_effective_group(XkbDescPtr kbd, KeyCode keycode, int group)
{
    int num_groups = XkbKeyNumGroups(kbd, keycode);
    int key_group = group;

    if (num_groups == 0)
    {
        key_group = -1;
    }
    else if (num_groups == 1)
    {
        key_group = 0;
    }
    else if (key_group >= num_groups)
    {
        unsigned int group_info = XkbKeyGroupInfo(kbd, keycode);

        switch (XkbOutOfRangeGroupAction(group_info))
        {
            case XkbClampIntoRange:
                key_group = num_groups - 1;
                break;

            case XkbRedirectIntoRange:
                key_group = XkbOutOfRangeGroupNumber(group_info);
                if (key_group >= num_groups)
                    key_group = 0;
                break;

            case XkbWrapIntoRange:
            default:
                key_group %= num_groups;
                break;
        }
    }

    return key_group;
}

static KeyCode
keysym_to_keycode(XkbDescPtr kbd, KeySym keysym, int group, unsigned *mod_mask)
{
    KeyCode result = 0;
    KeyCode keycode = 0;
    int key_group;
    int num_levels;
    int level;
    unsigned int new_mask = 0;

    for (keycode = kbd->min_key_code; keycode < kbd->max_key_code; keycode++)
    {
        key_group = get_effective_group(kbd, keycode, group);
        if (key_group >= 0)  // valid key, i.e. not unused?
        {
            num_levels = XkbKeyGroupWidth(kbd, keycode, key_group);
            for (level = 0; level < num_levels; level++)
            {
                KeySym ks = XkbKeySymEntry(kbd, keycode, level, key_group);
                if (ks == keysym)
                {
                    int i;
                    XkbKeyTypePtr key_type = XkbKeyKeyType(kbd, keycode,
                                                           key_group);
                    int map_count = key_type->map_count;

                    #ifdef DEBUG_OUTPUT
                    printf("kc %d level %d ks %ld key_type->num_levels %d, "
                           "key_type->map_count %d, mods m %d r %d v %d\n",
                           keycode, level, ks, key_type->num_levels,
                           key_type->map_count, key_type->mods.mask,
                           key_type->mods.real_mods, key_type->mods.vmods);
                    for (i = 0; i < map_count; i++)
                    {
                        XkbKTMapEntryPtr entry = key_type->map + i;
                        printf("xxx i %d: level %d entry->level %d "
                               "entry->modsmask %d \n",
                               i, level, entry->level, entry->mods.mask);
                    }
                    #endif

                    if (level == 0)
                    {
                        result = keycode;
                        new_mask = 0;
                    }
                    else
                    {
                        for (i = 0; i < map_count; i++)
                        {
                            XkbKTMapEntryPtr entry = key_type->map + i;
                            if (entry->level == level)
                            {
                                result = keycode;
                                new_mask = entry->mods.mask;
                                break;
                            }
                        }
                    }

                    break;
                }
            }
        }

        if (result)
            break;
    }

    if (mod_mask)
        *mod_mask = new_mask;

    return result;
}


#ifdef DEBUG_OUTPUT
static void dump_xkb_state(VirtkeyX* this, int keycode, KeySym keysym, int group)
{
    unsigned int ma;
    int kca;
    int key_group = get_effective_group(this->kbd, keycode, group);

    for (int i=244; i<=this->kbd->max_key_code;i++)
    {
        KeySym* pks = &XkbKeySymEntry(this->kbd, i, 0, key_group);
        unsigned int g = XkbKeyGroupInfo(this->kbd, i);
        printf("%3d pkeysym 0x%p keysym %9ld group_info 0x%x "
               //"NumGroups %d "
               //"OutOfRangeGroupAction %d OutOfRangeGroupNumber "
               //"%d OutOfRangeGroupInfo %d "
               "num_groups %d KeyGroupsWidth %d kt_index [%d,%d,%d,%d] "
               "XkbCMKeySymsOffset %4d [",
               i, pks,
               XkbKeySymEntry(this->kbd, i, 0, key_group),
               g,
               //XkbNumGroups(g),
               //XkbOutOfRangeGroupAction(g),
               //XkbOutOfRangeGroupNumber(g),
               //XkbOutOfRangeGroupInfo(g),
               XkbKeyNumGroups(this->kbd, i),
               XkbKeyGroupsWidth(this->kbd, i),
               XkbCMKeyTypeIndex(this->kbd->map, i, 0),
               XkbCMKeyTypeIndex(this->kbd->map, i, 1),
               XkbCMKeyTypeIndex(this->kbd->map, i, 2),
               XkbCMKeyTypeIndex(this->kbd->map, i, 3),
               XkbCMKeySymsOffset(this->kbd->map, i)
               );
        {
            int n = XkbKeyNumGroups(this->kbd, i) *
                    XkbKeyGroupsWidth(this->kbd, i);
            for (int j=0; j<n; j++)
            {
                printf("%9ld", (XkbKeySymsPtr(this->kbd, i)[j]));
                if (j < n-1)
                    printf(",");
            }
            printf("]\n");
        }
    }

    {
        int start = XkbCMKeySymsOffset(this->kbd->map, 244) / 10 * 10;
        for (int r=0; r<5; r++)
        {
            int offset = start + r*10;
            printf("%4d: ", offset);

            for (int i=0; i<10; i++)
            {
                printf("%9ld ", (this->kbd->map->syms[offset+i]));
            }
            printf("\n");
        }
    }

    kca = keysym_to_keycode(this->kbd, keysym, group, &ma);
    printf("Remapping keysym %ld to keycode %d found keycode %d, "
           "min_key_code %d max_key_code %d XkbKeyGroupsWidth %d\n",
           keysym, keycode, kca, this->kbd->min_key_code, 
           this->kbd->max_key_code, XkbKeyGroupsWidth(this->kbd, keycode));
}
#endif

static KeyCode
map_keysym_xkb(VirtkeyBase* base, KeySym keysym, int group)
{
    VirtkeyX* this = (VirtkeyX*) base;
    static int modified_key = 0;
    KeyCode keycode;
    Status status;
    int key_group;

    // Change one of the last 10 keysyms, remapping the keyboard map
    // on the fly. This assumes the last 10 aren't already used.
    const int n = 10;
    keycode = this->kbd->max_key_code - modified_key - 1;
    modified_key = (modified_key + 1) % n;

    #ifdef DEBUG_OUTPUT
    dump_xkb_state(this, keycode, keysym, group);
    #endif

    // Allocate space for the new symbol and init types.
    {
        int n_groups = 1;
        int new_types[XkbNumKbdGroups] = {XkbOneLevelIndex};
        XkbMapChangesRec changes;  // man XkbSetMap
        memset(&changes, 0, sizeof(changes));

        changes.changed = XkbKeySymsMask;
        changes.first_key_sym = keycode;
        changes.num_key_syms = 1;

        status = XkbChangeTypesOfKey (this->kbd, keycode,
                                      n_groups, XkbGroup1Mask,
                                      new_types, &changes);
        if (status != Success)
        {
            return 0;
        }
    }

    // Patch in our new symbol
    key_group = get_effective_group(this->kbd, keycode, group);
    XkbKeySymEntry(this->kbd, keycode, 0, key_group) = keysym;

    #ifdef DEBUG_OUTPUT
    dump_xkb_state(this, keycode, keysym, group);
    #endif


    // Tell the server
    {
        XkbMapChangesRec changes;  // man XkbSetMap
        changes.changed = XkbKeySymsMask;
        changes.first_key_sym = keycode;
        changes.num_key_syms = 1;

        if (!XkbChangeMap(this->xdisplay, this->kbd, &changes))
            return 0;

        XSync (this->xdisplay, False);
    }

    return keycode;
}

static int
virtkey_x_get_keycode_from_keysym (VirtkeyBase* base, int keysym,
                                   int group,
                                   unsigned int *mod_mask_out)
{
    KeyCode keycode;
    VirtkeyX* this = (VirtkeyX*) base;

    // Look keysym up in current group.
    keycode = keysym_to_keycode(this->kbd, keysym,
                                group, mod_mask_out);
    if (!keycode)
        keycode = map_keysym_xkb(base, keysym, group);

    return keycode;
}

static void
virtkey_x_get_label_from_keycode(VirtkeyBase* base,
    int keycode, int modmask, int group,
    char* label, int max_label_size)
{
    VirtkeyX* this = (VirtkeyX*) base;
    unsigned int mods;
    int label_size;
    KeySym keysym;
    XKeyPressedEvent ev;

    memset(&ev, 0, sizeof(ev));
    ev.type = KeyPress;
    ev.display = this->xdisplay;

    mods = XkbBuildCoreState (modmask, group);

    ev.state = mods;
    ev.keycode = keycode;
    label_size = XLookupString(&ev, label, max_label_size, &keysym, NULL);
    label[label_size] = '\0';

    if (keysym)
    {
        strncpy(label, virtkey_get_label_from_keysym(keysym), max_label_size - 1);
        label[max_label_size - 1] = '\0';
    }
}

static int
virtkey_x_get_keysym_from_keycode(VirtkeyBase* base,
                                  int keycode, int modmask, int group)
{
    KeySym keysym;
    unsigned int mods_rtn;

    VirtkeyX* this = (VirtkeyX*) base;

    XkbTranslateKeyCode (this->kbd,
                         keycode,
                         XkbBuildCoreState (modmask, group),
                         &mods_rtn,
                         &keysym);
    return keysym;
}

/**
 * Reads the contents of the root window property _XKB_RULES_NAMES.
 */
static char**
virtkey_x_get_rules_names(VirtkeyBase* base, int* numentries)
{
    VirtkeyX* this = (VirtkeyX*) base;
    XkbRF_VarDefsRec vd;
    char *tmp = NULL;
    char** results;
    const int n = 5;

    if (!XkbRF_GetNamesProp (this->xdisplay, &tmp, &vd))
        return NULL;

    results = malloc(sizeof(char*) * n);
    if (!results)
        return NULL;

    *numentries = n;

    if (tmp)
    {
        results[0] = strdup(tmp);
        XFree (tmp);
    }
    else
        results[0] = strdup("");

    if (vd.model)
    {
        results[1] = strdup(vd.model);
        XFree (vd.model);
    }
    else
        results[1] = strdup("");

    if (vd.layout)
    {
        results[2] = strdup(vd.layout);
        XFree (vd.layout);
    }
    else
        results[2] = strdup("");

    if (vd.variant)
    {
        results[3] = strdup(vd.variant);
        XFree (vd.variant);
    }
    else
        results[3] = strdup("");

    if (vd.options)
    {
        results[4] = strdup(vd.options);
        XFree (vd.options);
    }
    else
        results[4] = strdup("");

    return results;
}

/*
 * Return a string representative of the whole layout including all groups.
 * Caller takes ownership, call free() on the result.
 */
static char*
virtkey_x_get_layout_as_string (VirtkeyBase* base)
{
    VirtkeyX* this = (VirtkeyX*) base;
    char* result = NULL;
    char* symbols;

    if (!this->kbd->names || !this->kbd->names->symbols)
    {
        PyErr_SetString (OSK_EXCEPTION, "no symbols names available");
        return NULL;
    }

    symbols = XGetAtomName (this->xdisplay, this->kbd->names->symbols);
    if (symbols)
    {
        result = strdup(symbols);
        XFree (symbols);
    }

    return result;
}

void
virtkey_x_set_group (VirtkeyBase* base, int group, bool lock)
{
    VirtkeyX* this = (VirtkeyX*) base;

    if (lock)
        XkbLockGroup (this->xdisplay, XkbUseCoreKbd, group);
    else
        XkbLatchGroup (this->xdisplay, XkbUseCoreKbd, group);

    XSync (this->xdisplay, False);
}

void
virtkey_x_set_modifiers (VirtkeyBase* base,
                         unsigned int mod_mask, bool lock, bool press)
{
    VirtkeyX* this = (VirtkeyX*) base;

    // apply modifier change
    if (lock)
        XkbLockModifiers (this->xdisplay, XkbUseCoreKbd,
                        mod_mask, press ? mod_mask : 0);
    else
        XkbLatchModifiers (this->xdisplay, XkbUseCoreKbd,
                        mod_mask, press ? mod_mask : 0);

    XSync (this->xdisplay, False);
}

static int
virtkey_x_init_keyboard (VirtkeyX *this)
{
    if (this->kbd)
    {
        XkbFreeKeyboard (this->kbd, XkbAllComponentsMask, True);
        this->kbd = NULL;
    }

    this->kbd = XkbGetKeyboard (this->xdisplay,
                              XkbCompatMapMask | XkbNamesMask | XkbGeometryMask,
                              XkbUseCoreKbd);
#ifndef NDEBUG
    /* test missing keyboard (LP:#526791) keyboard on/off every 10 seconds */
    if (getenv ("VIRTKEY_DEBUG"))
    {
        if (this->kbd && time(NULL) % 20 < 10)
        {
            XkbFreeKeyboard (this->kbd, XkbAllComponentsMask, True);
            this->kbd = NULL;
        }
    }
#endif
    if (!this->kbd)
    {
        PyErr_SetString (OSK_EXCEPTION, "XkbGetKeyboard failed.");
        return -1;
    }

    return 0;
}

static int
virtkey_x_init (VirtkeyBase *base)
{
    VirtkeyX* this = (VirtkeyX*) base;
    GdkDisplay* display;
    gint xkb_major = XkbMajorVersion;
    gint xkb_minor = XkbMinorVersion;

    this->kbd = NULL;

    display = gdk_display_get_default ();
    if (!GDK_IS_X11_DISPLAY (display)) // Wayland, MIR?
    {
        PyErr_SetString (OSK_EXCEPTION, "not an X display");
        return -1;
    }
    this->xdisplay = GDK_DISPLAY_XDISPLAY (display);

    // Init Xkb just in case, even though Gdk should have done so already.
    if (!XkbLibraryVersion (&xkb_major, &xkb_minor))
    {
        PyErr_Format (OSK_EXCEPTION,
            "XkbLibraryVersion failed: compiled for v%d.%d but found v%d.%d",
            XkbMajorVersion, XkbMinorVersion, xkb_major, xkb_minor);
        return -1;
    }

    xkb_major = XkbMajorVersion;
    xkb_minor = XkbMinorVersion;

    if (!XkbQueryExtension (this->xdisplay, NULL,
                            &this->xkb_base_event, NULL,
                            &xkb_major, &xkb_minor))
    {
        PyErr_Format (OSK_EXCEPTION,
            "XkbQueryExtension failed: compiled for v%d.%d but found v%d.%d",
            XkbMajorVersion, XkbMinorVersion, xkb_major, xkb_minor);
        return -1;
    }

    if (virtkey_x_init_keyboard (this) < 0)
        return -1;

    return 0;
}

static int
virtkey_x_reload (VirtkeyBase* base)
{
    VirtkeyX* this = (VirtkeyX*) base;

    if (virtkey_x_init_keyboard (this) < 0)
        return -1;

    return 0;
}

static void
virtkey_x_destruct (VirtkeyBase *base)
{
    VirtkeyX* this = (VirtkeyX*) base;

    if (this->kbd)
        XkbFreeKeyboard (this->kbd, XkbAllComponentsMask, True);
}

VirtkeyBase*
virtkey_x_new(void)
{
   VirtkeyBase* this = (VirtkeyBase*) malloc(sizeof(VirtkeyX));
   this->init = virtkey_x_init;
   this->destruct = virtkey_x_destruct;
   this->reload = virtkey_x_reload;
   this->get_current_group = virtkey_x_get_current_group;
   this->get_current_group_name = virtkey_x_get_current_group_name;
   this->get_auto_repeat_rate = virtkey_x_get_auto_repeat_rate;
   this->get_label_from_keycode = virtkey_x_get_label_from_keycode;
   this->get_keysym_from_keycode = virtkey_x_get_keysym_from_keycode;
   this->get_keycode_from_keysym = virtkey_x_get_keycode_from_keysym;
   this->get_rules_names = virtkey_x_get_rules_names;
   this->get_layout_as_string = virtkey_x_get_layout_as_string;
   this->set_group = virtkey_x_set_group;
   this->set_modifiers = virtkey_x_set_modifiers;
   return this;
}

