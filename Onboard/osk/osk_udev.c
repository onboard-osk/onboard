/*
 * Copyright © 2017 marmuta <marmvta@gmail.com>
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

#include "osk_module.h"

#include <libudev.h>
#include <glib.h>


typedef struct {
    PyObject_HEAD

    struct udev *udev;
    struct udev_monitor *input_monitor;
    GSource* watch_source;
    PyObject* event_handler;
} OskUDev;

OSK_REGISTER_TYPE (OskUDev, osk_udev, "UDev")

static gboolean
on_udev_event(GIOChannel *source, GIOCondition condition, gpointer data);
static void
disconnect_monitor (OskUDev *this);


static int
osk_udev_init (OskUDev *this, PyObject *args, PyObject *kwds)
{
    this->udev = udev_new();
    if (this->udev == NULL)
    {
        PyErr_SetString(PyExc_ValueError, "failed to create UDev object");
        return -1;
    }

    return 0;
}

static void
osk_udev_dealloc (OskUDev *this)
{
    disconnect_monitor(this);

    if (this->udev)
    {
        udev_unref(this->udev);
        this->udev = NULL;
    }

    OSK_FINISH_DEALLOC (this);
}

static const char* not_null(const char* c)
{
    return c ? c : "";
}

/*
 * Enumerate UDev keyboard devices.
 * Returns list of dicts with device properties.
 *
 * python3 -c "import Onboard.osk as osk; import pprint; pprint.pprint(osk.UDev().get_keyboard_devices())"
 */
static PyObject *
osk_udev_get_keyboard_devices (PyObject *self, PyObject *args)
{
    OskUDev *this = (OskUDev*) self;
    PyObject* result = NULL;

    struct udev_enumerate *enumerate = NULL;
    struct udev_list_entry *devices;
    struct udev_list_entry *list_entry;

    result = PyList_New (0);
    if (!result)
    {
        PyErr_SetString(PyExc_MemoryError, "failed to allocate results list");
    }
    else
    {
        enumerate = udev_enumerate_new(this->udev);
        udev_enumerate_add_match_subsystem(enumerate, "input");
        udev_enumerate_add_match_property(enumerate, "ID_INPUT_KEYBOARD", "1");
        udev_enumerate_scan_devices(enumerate);

        devices = udev_enumerate_get_list_entry(enumerate);
        udev_list_entry_foreach(list_entry, devices)
        {
            const char* path = udev_list_entry_get_name(list_entry);
            struct udev_device* device = udev_device_new_from_syspath(this->udev, path);
            if (udev_device_get_property_value(device, "PHYS"))
            {
                const char *str;
                PyObject* d = PyDict_New();
                PyDict_SetItemString(d, "path", PyUnicode_FromString(path));

                str = not_null(udev_device_get_devnode(device));
                PyDict_SetItemString(d, "devnode", PyUnicode_FromString(str));

                str = not_null(udev_device_get_sysname(device));
                PyDict_SetItemString(d, "sysname", PyUnicode_FromString(str));

                str = not_null(udev_device_get_sysnum(device));
                PyDict_SetItemString(d, "sysnum", PyUnicode_FromString(str));

                str = not_null(udev_device_get_syspath(device));
                PyDict_SetItemString(d, "syspath", PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "NAME"));
                PyDict_SetItemString(d, "NAME", PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "ID_BUS"));
                PyDict_SetItemString(d, "ID_BUS", PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "ID_VENDOR_ID"));
                PyDict_SetItemString(d, "ID_VENDOR_ID",
                                     PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "ID_MODEL_ID"));
                PyDict_SetItemString(d, "ID_MODEL_ID",
                                     PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "ID_SERIAL"));
                PyDict_SetItemString(d, "ID_SERIAL",
                                     PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "ID_USB_INTERFACE_NUM"));
                PyDict_SetItemString(d, "ID_USB_INTERFACE_NUM",
                                     PyUnicode_FromString(str));

                str = not_null(udev_device_get_property_value(
                                     device, "ID_USB_INTERFACES"));
                PyDict_SetItemString(d, "ID_USB_INTERFACES",
                                     PyUnicode_FromString(str));

                PyList_Append(result, d);
            }
        }
    }

    if (enumerate)
        udev_enumerate_unref(enumerate);

    if (PyErr_Occurred())
        return NULL;

    if (result)
        return result;

    Py_RETURN_NONE;
}

static gboolean
on_udev_event(GIOChannel* source, GIOCondition condition, gpointer data)
{
    OskUDev *this = (OskUDev*) data;

    if (this->input_monitor)
    {
        struct udev_device* device =
            udev_monitor_receive_device (this->input_monitor);
        if (device)
        {
            /* GLib dispatches this outside of any Python call, i.e. without
             * the GIL (PyGObject releases it in Gtk.main()). Touching the
             * Python C-API here without it crashes, reliably so with the
             * per-thread state checks of Python >= 3.12/3.14. */
            PyGILState_STATE state = PyGILState_Ensure();

            const char* path = udev_device_get_devpath(device);
            PyObject* arglist = Py_BuildValue("(s)", path ? path : "");
            if (arglist)
            {
                osk_util_idle_call(this->event_handler, arglist);
                Py_DECREF(arglist);
            }
            else
                PyErr_Print();

            PyGILState_Release(state);

            udev_device_unref(device);
        }
    }

    return TRUE;
}

static PyObject *
osk_udev_connect (PyObject *self, PyObject *args)
{
    OskUDev *this = (OskUDev*) self;
    char* event_name;
    PyObject* handler;

    if (!PyArg_ParseTuple (args, "sO", &event_name, &handler))
        return NULL;

    Py_XDECREF (this->event_handler);
    this->event_handler = handler;
    Py_XINCREF (this->event_handler);

    // monitor udev  events
    this->input_monitor = udev_monitor_new_from_netlink(this->udev, "udev");
    if (this->input_monitor)
    {
        GIOChannel *channel;
        int fd;

        udev_monitor_filter_add_match_subsystem_devtype(this->input_monitor,
                                                        "input", NULL);
        udev_monitor_enable_receiving(this->input_monitor);
        fd = udev_monitor_get_fd(this->input_monitor);

        // plug  udev fd into the glib mainloop machinery
        channel = g_io_channel_unix_new(fd);
        this->watch_source = g_io_create_watch(channel, G_IO_IN);
        g_io_channel_unref(channel);
        g_source_set_callback (this->watch_source,
                               (GSourceFunc) on_udev_event, this, NULL);
        g_source_attach (this->watch_source,
                         g_main_context_get_thread_default ());
        g_source_unref (this->watch_source);
    }

    Py_RETURN_NONE;
}

static PyObject *
osk_udev_disconnect (PyObject *self, PyObject *args)
{
    OskUDev *this = (OskUDev*) self;
    char* event_name;
    PyObject* handler;

    if (!PyArg_ParseTuple (args, "sO", &event_name, &handler))
        return NULL;

    disconnect_monitor(this);

    Py_RETURN_NONE;
}

static void
disconnect_monitor (OskUDev *this)
{
    if (this->watch_source)
    {
        g_source_destroy (this->watch_source);
        this->watch_source = NULL;
    }

    if (this->input_monitor)
    {
        udev_monitor_unref (this->input_monitor);
        this->input_monitor = NULL;
    }

    Py_XDECREF (this->event_handler);
}


static PyMethodDef osk_udev_methods[] = {
    { "connect",
        osk_udev_connect,
        METH_VARARGS, NULL },
    { "disconnect",
        osk_udev_disconnect,
        METH_VARARGS, NULL },
    { "get_keyboard_devices",
        osk_udev_get_keyboard_devices,
        METH_NOARGS, NULL },

    { NULL, NULL, 0, NULL }
};

