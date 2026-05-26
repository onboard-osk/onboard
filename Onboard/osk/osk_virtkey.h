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

#ifndef __OSK_VIRTKEY__
#define __OSK_VIRTKEY__

#include "osk_module.h"  // bool

struct RulesNames
{
    char* rules_file;
    char* model;
    char* layout;
    char* variant;
    char* options;
};

typedef struct VirtkeyBase VirtkeyBase;
struct VirtkeyBase {
    int     (*init)(VirtkeyBase* base);
    void    (*destruct)(VirtkeyBase* base);
    int     (*reload)(VirtkeyBase* base);
    int     (*get_current_group)(VirtkeyBase* base);
    char*   (*get_current_group_name)(VirtkeyBase* base);
    bool    (*get_auto_repeat_rate) (VirtkeyBase *base,
                unsigned int *delay, unsigned int *interval);
    void    (*get_label_from_keycode)(VirtkeyBase* base,
                int keycode, int modmask, int group,
                char* label, int max_label_size);
    int     (*get_keycode_from_keysym)(VirtkeyBase* base,
                int keysym, int group, unsigned int *mod_mask_out);
    int     (*get_keysym_from_keycode)(VirtkeyBase* base,
                int keycode, int modmask, int group);
    char**  (*get_rules_names)(VirtkeyBase* base, int* numentries);
    char*   (*get_layout_as_string)(VirtkeyBase* base);
    void    (*set_group) (VirtkeyBase* base,
            int group, bool lock);
    void    (*set_modifiers) (VirtkeyBase* base,
            unsigned int mod_mask, bool lock, bool press);
};

char* virtkey_get_label_from_keysym (int keyval);

#endif
