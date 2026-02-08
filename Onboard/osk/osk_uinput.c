/*
 * Copyright © 2012, 2016 marmuta <marmvta@gmail.com>
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

#include <fcntl.h>
#include <errno.h>
#ifdef __FreeBSD__
#include <dev/evdev/input.h>
#include <dev/evdev/uinput.h>
#else
#include <linux/input.h>
#include <linux/uinput.h>
#endif

#include "osk_module.h"
#include "osk_uinput.h"

typedef struct {
    int fd;
    struct uinput_user_dev uidev;
} UInput;

static int uinput_open (UInput* uinput, const char* device_name);
static void uinput_close (UInput* uinput);
static void uinput_send_key_event_to(UInput* uinput, int keycode, bool press);

static UInput uinput_singleton = {0};

int
uinput_init (const char* device_name)
{
    UInput* uinput = &uinput_singleton;
    if (!uinput->fd)
        return uinput_open(uinput, device_name);
    return 0;
}

void
uinput_destruct ()
{
    uinput_close(&uinput_singleton);
}

void
uinput_send_key_event(int keycode, bool press)
{
    uinput_send_key_event_to(&uinput_singleton, keycode, press);
}

static int
uinput_open (UInput* uinput, const char* device_name)
{
    int fd;
    int i;
    struct uinput_user_dev* uidev = &uinput->uidev;

    if(!device_name)
    {
        PyErr_SetString (PyExc_ValueError, "device_name must not be None");
        return -1;
    }

    fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if(fd < 0)
    {
        PyErr_SetString (OSK_EXCEPTION, 
                "Failed to open /dev/uinput. "
                "Write permission required.");
        return -1;
    }

    if(ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0)
    {
        PyErr_SetString (OSK_EXCEPTION, "error in ioctl UI_SET_EVBIT");
        return -2;
    }

    for (i=0; i < 256; i++) 
    {
        if(ioctl(fd, UI_SET_KEYBIT, i) < 0)
        {
            PyErr_SetString (OSK_EXCEPTION, "error in ioctl UI_SET_KEYBIT");
            return -3;
        }
    }

    // init uinput device
    memset(uidev, 0, sizeof(*uidev));
    snprintf(uidev->name, UINPUT_MAX_NAME_SIZE, "%s", device_name);
    uidev->id.bustype = BUS_USB;
    uidev->id.vendor  = 0x1;
    uidev->id.product = 0x1;
    uidev->id.version = 1;

    if(write(fd, uidev, sizeof(*uidev)) < 0)
    {
        PyErr_SetString (OSK_EXCEPTION, "error writing uinput device struct");
        return -4;
    }

    if(ioctl(fd, UI_DEV_CREATE) < 0)
    {
        PyErr_SetString (OSK_EXCEPTION, 
                         "error creating uinput device: ioctl UI_DEV_CREATE");
        return -5;
    }

    uinput->fd = fd;
    return 0;
}

static void
uinput_close(UInput* uinput)
{
    if (uinput == NULL)
        uinput = &uinput_singleton;
    if (uinput->fd)
    {
        if(ioctl(uinput->fd, UI_DEV_DESTROY) < 0)
        {
            PyErr_SetString (OSK_EXCEPTION, "ioctl UI_DEV_DESTROY");
        }

        close(uinput->fd);
        uinput->fd = 0;
    }
}

void
uinput_send_key_event_to(UInput* uinput, int keycode, bool press)
{
    int fd = uinput->fd;
    int code = keycode - 8;
    struct input_event ev;

    //printf("send_key_event %d %d \n", keycode, press);
    memset(&ev, 0, sizeof(ev));
    ev.type = EV_KEY;
    ev.code = code;
    //ev.code = KEY_A;
    ev.value = press;
    if(write(fd, &ev, sizeof(ev)) < 0)
    {
        PyErr_SetString (OSK_EXCEPTION, "write key event");
        return;
    }

    ev.type = EV_SYN;
    ev.code = 0;
    ev.value = 0;
    if(write(fd, &ev, sizeof(ev)) < 0)
    {
        PyErr_SetString (OSK_EXCEPTION, "write syn");
        return;
    }
}


/*
 * Python type
 */

typedef struct {
    PyObject_HEAD
} OskUInput;

OSK_REGISTER_TYPE (OskUInput, osk_uinput, "UInput")

static int
osk_uinput_init (OskUInput *self, PyObject *args, PyObject *kwds)
{
    return uinput_init("onboard test device");
}

static void
osk_uinput_dealloc (OskUInput *self)
{
    uinput_destruct();
    OSK_FINISH_DEALLOC (self);
}

static void
send_key_event(OskUInput* self, int keycode, bool press)
{
    uinput_send_key_event(keycode, press);
}

static PyObject *
osk_press_keycode (PyObject *self, PyObject *args)
{
    int     keycode;

    if (!PyArg_ParseTuple (args, "I", &keycode))
        return NULL;

    send_key_event((OskUInput*) self, keycode, 1);

    Py_RETURN_NONE;
}

static PyObject *
osk_release_keycode (PyObject *self, PyObject *args)
{
    int     keycode;

    if (!PyArg_ParseTuple (args, "I", &keycode))
        return NULL;

    send_key_event((OskUInput*) self, keycode, 0);

    Py_RETURN_NONE;
}

static PyMethodDef osk_uinput_methods[] = {
    { "press_keycode", 
        osk_press_keycode, 
        METH_VARARGS, NULL },
    { "release_keycode", 
        osk_release_keycode, 
        METH_VARARGS, NULL },
    { NULL, NULL, 0, NULL }
};

