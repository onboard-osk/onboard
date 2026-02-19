/*
 * Copyright © 2011-2013 Gerd Kohlberger <lowfi@chello.at>
 * Copyright © 2011-2014, 2016 marmuta <marmvta@gmail.com>
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

#include <signal.h>
#include <glib-unix.h>

#include <gdk/gdkx.h>
#include <X11/Xatom.h>
#include <X11/extensions/XTest.h>

#ifdef USE_LANGUAGE_CLASSIFIER
#include <textcat.h>
#endif


typedef struct {
    PyObject_HEAD

    GdkDisplay *display;
    Atom atom_net_active_window;
#ifdef __FreeBSD__
#ifndef _NSIG
#define _NSIG NSIG
#endif
#endif
    PyObject* signal_callbacks[_NSIG];
    PyObject* onboard_toplevels;

    Atom* watched_root_properties;
    int  num_watched_root_properties;
    PyObject* root_property_callback;
} OskUtil;

static Display* get_x_display(OskUtil* util);

OSK_REGISTER_TYPE(OskUtil, osk_util, "Util")

static int
osk_util_init (OskUtil *util, PyObject *args, PyObject *kwds)
{
    Display* xdisplay;
    util->display = gdk_display_get_default ();
    xdisplay = get_x_display(util);
    if (xdisplay) // not on wayland?
    {
        util->atom_net_active_window = \
                            XInternAtom (xdisplay, "_NET_ACTIVE_WINDOW", True);
    }

    return 0;
}

static void
osk_util_dealloc (OskUtil *util)
{
    gsize i;

    for (i=0; i<G_N_ELEMENTS(util->signal_callbacks); i++)
    {
        Py_XDECREF(util->signal_callbacks[i]);
        util->signal_callbacks[i] = NULL;
    }

    Py_XDECREF(util->onboard_toplevels);
    util->onboard_toplevels = NULL;

    Py_XDECREF(util->root_property_callback);
    util->root_property_callback = NULL;

    PyMem_Free(util->watched_root_properties);

    OSK_FINISH_DEALLOC (util);
}

static Display*
get_x_display (OskUtil* util)
{
    if (GDK_IS_X11_DISPLAY (util->display)) // not on wayland?
        return GDK_DISPLAY_XDISPLAY (util->display);
    return NULL;
}

static PyObject *
osk_util_set_x_property (PyObject *self, PyObject *args)
{
    OskUtil *util = (OskUtil*) self;
    int wid;
    char* property_name;
    PyObject* property_value;
    Atom value_name;

    Display* xdisplay = get_x_display(util);
    if (xdisplay == NULL)
    {
        PyErr_SetString(PyExc_TypeError, "Not an X display");
        return NULL;
    }

    if (!PyArg_ParseTuple (args, "isO:set_x_property",
                           &wid, &property_name, &property_value))
        return NULL;

    value_name  = XInternAtom(xdisplay, property_name, False);

    if (PyInt_Check(property_value))
    {
        guint32 int_value = (guint32) PyInt_AsLong(property_value);
        XChangeProperty (xdisplay, wid,
                         value_name, XA_CARDINAL, 32, PropModeReplace,
                         (guchar*) &int_value, 1);
    }
    else if (PyUnicode_Check(property_value))
    {
        Atom atom_value;
        PyObject* string_value = PyUnicode_AsUTF8String(property_value);
        if (!string_value)
        {
            PyErr_SetString(PyExc_ValueError, "failed to encode value as utf-8");
            return NULL;
        }
        atom_value = XInternAtom(xdisplay,
                                 PyString_AsString(string_value), False);
        XChangeProperty (xdisplay, wid,
                         value_name, XA_ATOM, 32, PropModeReplace,
                         (guchar*) &atom_value, 1);

        Py_DECREF(string_value);
    }
    else
    {
        PyErr_SetString(PyExc_TypeError, "Unsupported value type");
        return NULL;
    }
    Py_RETURN_NONE;
}

static gboolean
signal_handler(gpointer user_data)
{
    PyGILState_STATE state = PyGILState_Ensure();
    PyObject* callback = (PyObject*) user_data;
    PyObject* arglist = NULL;

    PyObject* result  = PyObject_CallObject(callback, arglist);
    Py_XDECREF(arglist);
    Py_XDECREF(result);

    PyGILState_Release(state);
    return True;
}

static PyObject *
osk_util_set_unix_signal_handler (PyObject *self, PyObject *args)
{
    OskUtil *util = (OskUtil*) self;
    int signal = 0;
    PyObject*        callback = NULL;

    if (!PyArg_ParseTuple (args, "IO", &signal, &callback))
        return NULL;

    Py_XINCREF(callback);              /* Add a reference to new callback */
    Py_XDECREF(util->signal_callbacks[signal]); /* Dispose of previous callback */
    util->signal_callbacks[signal] = callback;  /* Remember new callback */

    g_unix_signal_add(signal, signal_handler, callback);

    Py_RETURN_NONE;
}


static Window
get_xid_of_gtkwidget(PyObject* widget)
{
    Window xid = None;
    if (widget)
    {
        PyObject* gdk_win = PyObject_CallMethod(widget, "get_window", NULL);
        if (gdk_win)
        {
            if (gdk_win != Py_None)
            {
                PyObject* _xid = PyObject_CallMethod(gdk_win,
                                                    "get_xid", NULL);
                if (_xid)
                {
                    xid = (Window)PyLong_AsLong(_xid);
                    Py_DECREF(_xid);
                }
            }
            Py_DECREF(gdk_win);
        }
    }
    return xid;
}

/* Replacement for gdk_x11_screen_get_active_window().
 * The gdk original somehow failed repeatedly with X error BadWindow on
 * Francesco's system.
 */
static Window
osk_util_get_active_window (OskUtil* util)
{
    Display* xdisplay = get_x_display(util);
    Window result = None;
    Atom actual_type;
    gint actual_format;
    gulong nwindows;
    gulong nleft;
    guchar *data = NULL;

    Window root = DefaultRootWindow (xdisplay);
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (xdisplay);
    gdk_x11_display_error_trap_push (gdk_display);
    if (XGetWindowProperty (xdisplay, root,
                util->atom_net_active_window,
                0, 1, False, XA_WINDOW,
                &actual_type, &actual_format,
                &nwindows, &nleft, &data)
            == Success)
    {
        if ((actual_type == XA_WINDOW) && (actual_format == 32) && (data))
        {
            Window window = *(Window *) data;
            if (window != None)
                result = window;
        }
    }

    if (gdk_x11_display_error_trap_pop (gdk_display))
        result = None;

    if (data)
        XFree (data);

    return result;
}


// Raise Onboard's windows on top of unity dash and full-screen windows.
static void
raise_windows_to_top (OskUtil *util)
{
    int i, n;
    Display* xdisplay = get_x_display(util);
    XTextProperty text_prop = {NULL};

    // find xid of the active window (_NET_ACTIVE_WINDOW)
    Window parent_xid = None;
    Window active_xid = osk_util_get_active_window(util);
    if (active_xid != None)
    {
        int ret;

        // Is the active window unity dash or unity-2d dash?
        GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (xdisplay);
        gdk_x11_display_error_trap_push (gdk_display);
        ret = XGetWMName(xdisplay, active_xid, &text_prop);
        if (!gdk_x11_display_error_trap_pop (gdk_display) && ret)
        {
            if (// Precise
                strcmp((char*)text_prop.value, "launcher") == 0 ||
                strcmp((char*)text_prop.value, "Dash") == 0 ||
                strcmp((char*)text_prop.value, "unity-2d-shell") == 0 ||
                // Quantal
                strcmp((char*)text_prop.value, "unity-launcher") == 0 ||
                strcmp((char*)text_prop.value, "unity-dash") == 0
                )
            {
                //printf("%s, 0x%x\n", text_prop.value, active_xid);
                parent_xid = active_xid;
            }
        }
    }

    // Loop through onboard's toplevel windows.
    n = PySequence_Length(util->onboard_toplevels);
    for (i = 0; i < n; i++)
    {
        Window xid;
        PyObject* window = PySequence_GetItem(util->onboard_toplevels, i);
        if (window == NULL)
            break;

        xid = get_xid_of_gtkwidget(window);
        if (xid)
        {
            // Raise onboard.
            // TransientForHint=None seems to be enough to rise over
            // full-screen windows.
            //printf("raising on top of 0x%x\n", parent_xid);
            XSetTransientForHint (xdisplay, xid, parent_xid);
            XRaiseWindow(xdisplay, xid);
        }
        Py_DECREF(window);
    }
}

static GdkFilterReturn
event_filter_keep_windows_on_top (GdkXEvent *gdk_xevent,
                                  GdkEvent  *gdk_event,
                                  OskUtil   *util)
{
    PyGILState_STATE state = PyGILState_Ensure();
    XEvent *event = gdk_xevent;

    if (event->type == PropertyNotify)
    {
        XPropertyEvent *e = (XPropertyEvent *) event;
        if (e->atom == util->atom_net_active_window)
        {
            raise_windows_to_top(util);
        }
    }

    PyGILState_Release(state);

    return GDK_FILTER_CONTINUE;
}

static PyObject *
osk_util_keep_windows_on_top (PyObject *self, PyObject *args)
{
    OskUtil *util = (OskUtil*) self;
    PyObject* windows = NULL;
    GdkWindow* root;

    Display* xdisplay = get_x_display(util);
    if (xdisplay == NULL)
        Py_RETURN_NONE;

    if (!PyArg_ParseTuple (args, "O", &windows))
        return NULL;

    if (!PySequence_Check(windows))
    {
        PyErr_SetString(PyExc_ValueError, "expected sequence type");
        return NULL;
    }

   root = gdk_get_default_root_window();

    XSelectInput(xdisplay, GDK_WINDOW_XID(root), PropertyChangeMask);

    Py_XINCREF(windows);
    Py_XDECREF(util->onboard_toplevels);
    util->onboard_toplevels = windows;

    // raise windows immediately on top of existing full-screen windows
    raise_windows_to_top(util);

    // install filter to raise them again when top-levels are activated
    gdk_window_add_filter (root,
                           (GdkFilterFunc) event_filter_keep_windows_on_top,
                           util);
    Py_RETURN_NONE;
}

static GdkFilterReturn
event_filter_root_property_notify (GdkXEvent *gdk_xevent,
                                   GdkEvent  *gdk_event,
                                   OskUtil   *util)
{
    PyGILState_STATE state = PyGILState_Ensure ();
    XEvent *event = gdk_xevent;

    if (event->type == PropertyNotify)
    {
        XPropertyEvent *e = (XPropertyEvent *) event;
        int i;
        Atom* atoms = util->watched_root_properties;
        PyObject* callback = util->root_property_callback;
        for (i=0; i<util->num_watched_root_properties; i++)
        {
            if (e->atom == atoms[i])
            {
                char* name = XGetAtomName(e->display, e->atom);
                PyObject* arglist = Py_BuildValue("(s)", name);
                if (arglist)
                {
                    osk_util_idle_call(callback, arglist);
                    Py_DECREF(arglist);
                }
                XFree(name);
            }
        }

    }

    PyGILState_Release(state);

    return GDK_FILTER_CONTINUE;
}

static PyObject *
osk_util_connect_root_property_notify (PyObject *self, PyObject *args)
{
    OskUtil *util = (OskUtil*) self;
    PyObject* properties = NULL;
    PyObject* callback = NULL;
    GdkWindow* root;
    int i, n;

    Display* xdisplay = get_x_display(util);
    if (xdisplay == NULL)
        Py_RETURN_NONE;

    if (!PyArg_ParseTuple (args, "OO", &properties, &callback))
        return NULL;

    if (!PySequence_Check(properties))
    {
        PyErr_SetString(PyExc_ValueError, "expected sequence type");
        return NULL;
    }

    n = PySequence_Length(properties);
    util->watched_root_properties = (Atom*) PyMem_Realloc(
                          util->watched_root_properties, sizeof(Atom) * n);
    util->num_watched_root_properties = 0;

    for (i = 0; i < n; i++)
    {
        PyObject* str_prop;
        PyObject* property;
        char* str;
        Atom atom;

        property  = PySequence_GetItem(properties, i);
        if (property == NULL)
            break;
        if (!PyUnicode_Check(property))
        {
            Py_DECREF(property);
            PyErr_SetString(PyExc_ValueError, "elements must be unicode strings");
            return NULL;
        }
        str_prop = PyUnicode_AsUTF8String(property);
        if (!str_prop)
        {
            Py_DECREF(property);
            PyErr_SetString(PyExc_ValueError, "failed to encode value as utf-8");
            return NULL;
        }

        str = PyString_AsString(str_prop);
        atom = XInternAtom(xdisplay, str, True);
        util->watched_root_properties[i] = atom;   // may be None

        Py_DECREF(str_prop);
        Py_DECREF(property);
    }
    util->num_watched_root_properties = n;

    Py_XINCREF(callback);                 /* Add a reference to new callback */
    Py_XDECREF(util->root_property_callback); /* Dispose of previous callback */
    util->root_property_callback = callback;    /* Remember new callback */

    root = gdk_get_default_root_window();

    XSelectInput(xdisplay, GDK_WINDOW_XID(root), PropertyChangeMask);

    // install filter to raise them again when top-levels are activated
    gdk_window_add_filter (root,
                           (GdkFilterFunc) event_filter_root_property_notify,
                           util);
    Py_RETURN_NONE;
}

static PyObject*
get_window_name(Display* display, Window window)
{
    XTextProperty prop;
    int len;
    char **list = NULL;
    PyObject* result = NULL;
    Status stat;
    Atom _NET_WM_NAME = XInternAtom(display, "_NET_WM_NAME", True);

//  Display *xdisplay = gdk_x11_display_get_xdisplay (display);
//  OskUtil *util = (OskUtil*) self;
//  Display* xdisplay = get_x_display(util);
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (display);
    gdk_x11_display_error_trap_push (gdk_display);
    stat = XGetTextProperty(display, window, &prop, _NET_WM_NAME)
           ? Success : BadRequest;
    if (stat != Success || prop.nitems == 0)
        stat = XGetWMName(display, window, &prop);
    gdk_x11_display_error_trap_pop_ignored (gdk_display);
    if (stat != Success || prop.nitems == 0)
        Py_RETURN_NONE;

    if(prop.encoding == XA_STRING)
    {
        result = PyUnicode_FromString((char*)prop.value);
    }
    else if(!XmbTextPropertyToTextList(display, &prop, &list, &len) && len > 0)
    {
        result = PyUnicode_FromString(list[0]);
        XFreeStringList(list);
    }
    XFree(prop.value);

    if (result)
        return result;

    Py_RETURN_NONE;
}

/*
 * Return the name of the active window manager.
 */
static PyObject *
osk_util_get_current_wm_name (PyObject *self)
{
    OskUtil *util = (OskUtil*) self;
    PyObject* result = NULL;
    Display* xdisplay = get_x_display(util);
    Atom _NET_SUPPORTING_WM_CHECK;

    if (xdisplay == NULL)
        Py_RETURN_NONE;

    _NET_SUPPORTING_WM_CHECK =
                        XInternAtom(xdisplay, "_NET_SUPPORTING_WM_CHECK", True);
    if (_NET_SUPPORTING_WM_CHECK != None)
    {
        GdkWindow*    root = gdk_get_default_root_window();
        Atom          actual_type;
        int           actual_format;
        unsigned long nwindows, nleft;
        Window        *xwindows;

        XGetWindowProperty (xdisplay, GDK_WINDOW_XID(root),
                            _NET_SUPPORTING_WM_CHECK, 0L, UINT_MAX, False,
                            XA_WINDOW, &actual_type, &actual_format,
                            &nwindows, &nleft, (unsigned char **) &xwindows);
        if (actual_type == XA_WINDOW && nwindows > 0 && xwindows[0] != None)
            result = get_window_name(xdisplay, xwindows[0]);

        XFree(xwindows);
    }

    if (result)
        return result;
    Py_RETURN_NONE;
}

static PyObject *
osk_util_remove_atom_from_property(PyObject *self, PyObject *args)
{
    OskUtil *util = (OskUtil*) self;
    PyObject* window = NULL;
    PyObject* result = NULL;
    char* property_name = NULL;
    char* value_name = NULL;

    Atom property_atom;
    Atom value_atom;
    Window xwindow;

    Display* xdisplay = get_x_display(util);
    if (xdisplay == NULL)
    {
        PyErr_SetString(PyExc_TypeError, "Not an X display");
        return NULL;
    }

    if (!PyArg_ParseTuple (args, "Oss", &window, &property_name, &value_name))
        return NULL;

    property_atom = XInternAtom(xdisplay, property_name, True);
    value_atom    = XInternAtom(xdisplay, value_name, True);
    xwindow = get_xid_of_gtkwidget(window);
    if (property_atom != None &&
        value_atom != None &&
        xwindow)
    {
        Atom          actual_type;
        int           actual_format;
        unsigned long nstates, nleft;
        Atom         *states;

        // Get all current states
        XGetWindowProperty (xdisplay, xwindow, property_atom,
                            0L, 12L, False,
                            XA_ATOM, &actual_type, &actual_format,
                            &nstates, &nleft, (unsigned char **) &states);
        if (actual_type == XA_ATOM)
        {
	    unsigned int i;
            int new_len;
            Atom new_states[12];
            Bool value_found = False;

            for (i=0, new_len=0; i<nstates; i++)
                if (states[i] == value_atom)
                    value_found = True;
                else
                    new_states[new_len++] = states[i];

            // Set the new states without value_atom
            if (value_found)
                XChangeProperty (xdisplay, xwindow, property_atom, XA_ATOM,
                               32, PropModeReplace, (guchar*) new_states, new_len);

            result = PyBool_FromLong(value_found);
        }
        XFree(states);
    }

    if (result)
        return result;
    Py_RETURN_NONE;
}


// Guess for the layout of GdkWindow's python wrapper.
typedef struct {
    PyObject_HEAD
    GdkWindow* window;
} PyGdkWindow;

static PyObject *
osk_util_set_input_rect (PyObject *self, PyObject *args)
{
    PyObject* owin;
    int       x, y, w, h;

    if (!PyArg_ParseTuple (args, "Oiiii:set_input_rect", &owin, &x, &y, &w, &h))
        return NULL;

    if (!PyObject_HasAttrString(owin, "set_child_input_shapes"))
    {
        PyErr_SetString(PyExc_ValueError, "parameter 1 must be Gdk.Window\n");
        return NULL;
    }

    {  // appease -Wdeclaration-after-statement on Arch
        cairo_region_t* region = NULL;
        const cairo_rectangle_int_t rect = {x, y, w, h};
        GdkWindow* win = ((PyGdkWindow*) owin)->window;  // risky, just a guess
        if (win)
        {
            region = cairo_region_create_rectangle (&rect);
            if (cairo_region_status (region) == CAIRO_STATUS_SUCCESS)
            {
                gdk_window_input_shape_combine_region (win, NULL, 0, 0);
                gdk_window_input_shape_combine_region (win, region, 0, 0);
            }
            cairo_region_destroy (region);
        }
    }
    Py_RETURN_NONE;
}

typedef struct {
    PyObject *callback;
    PyObject *arglist;
} IdleData;

static gboolean
idle_call (IdleData *data)
{
    PyGILState_STATE state = PyGILState_Ensure ();
    PyObject *result;

    result = PyObject_CallObject(data->callback, data->arglist);
    if (result)
        Py_DECREF (result);
    else
        PyErr_Print ();

    Py_XDECREF (data->arglist);
    Py_DECREF (data->callback);

    PyGILState_Release (state);

    g_slice_free (IdleData, data);

    return FALSE;
}

void
osk_util_idle_call (PyObject* callback, PyObject* arglist)
{
    IdleData *data;

    data = g_slice_new (IdleData);
    data->callback = callback;
    data->arglist = arglist;

    Py_INCREF (data->callback);
    Py_XINCREF (data->arglist);

    g_idle_add ((GSourceFunc) idle_call, data);
}

static PyMethodDef osk_util_methods[] = {
    { "set_x_property",
        osk_util_set_x_property,
        METH_VARARGS, NULL },
    { "set_unix_signal_handler",
        osk_util_set_unix_signal_handler,
        METH_VARARGS, NULL },
    { "keep_windows_on_top",
        osk_util_keep_windows_on_top,
        METH_VARARGS, NULL },
    { "connect_root_property_notify",
        osk_util_connect_root_property_notify,
        METH_VARARGS, NULL },
    { "get_current_wm_name",
        (PyCFunction) osk_util_get_current_wm_name,
        METH_NOARGS, NULL },
    { "remove_atom_from_property",
       osk_util_remove_atom_from_property,
        METH_VARARGS, NULL },
    { "set_input_rect",
       osk_util_set_input_rect,
        METH_VARARGS, NULL },

    { NULL, NULL, 0, NULL }
};

