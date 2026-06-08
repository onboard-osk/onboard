/*
 * Copyright © 2011-2012 Gerd Kohlberger <lowfi@chello.at>
 * Copyright © 2011-2016 marmuta <marmvta@gmail.com>
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

#include <gdk/gdkx.h>
#include <X11/Xatom.h>
#include <X11/extensions/XInput2.h>

#define XI_PROP_PRODUCT_ID "Device Product ID"


static unsigned int
translate_event_type (unsigned int xi_type);

static unsigned int
translate_state (XIModifierState *mods_state,
                 XIButtonState   *button_state,
                 XIGroupState    *group_state);

static unsigned int gdk_button_masks[] = {GDK_BUTTON1_MASK,
                                          GDK_BUTTON2_MASK,
                                          GDK_BUTTON3_MASK,
                                          GDK_BUTTON4_MASK,
                                          GDK_BUTTON5_MASK};

//------------------------------------------------------------------------
// DeviceEvent
// -----------------------------------------------------------------------

#define OSK_DEVICE_ADDED_EVENT   1100
#define OSK_DEVICE_REMOVED_EVENT 1101
#define OSK_SLAVE_ATTACHED_EVENT 1102
#define OSK_SLAVE_DETACHED_EVENT 1103

typedef struct {
    PyObject_HEAD

    Display*     display;
    Window       xid_event;
    unsigned int xi_type;
    unsigned int type;
    unsigned int device_id;
    unsigned int source_id;
    double       x;
    double       y;
    double       x_root;
    double       y_root;
    unsigned int button;
    unsigned int state;
    unsigned int keycode;
    unsigned int keyval;   // translated keycode (keysym on X)
    unsigned int sequence;
    unsigned int time;

    PyObject*    source_device;

} OskDeviceEvent;

OSK_REGISTER_TYPE_WITH_MEMBERS (OskDeviceEvent, osk_device_event, "DeviceEvent")

static int
osk_device_event_init (OskDeviceEvent* self, PyObject *args, PyObject *kwds)
{
    self->xid_event = None;
    self->device_id = 0;
    self->source_id = 0;
    self->source_device = Py_None;
    Py_INCREF(self->source_device);
    return 0;
}

static void
osk_device_event_dealloc (OskDeviceEvent* self)
{
    Py_DECREF(self->source_device);
    OSK_FINISH_DEALLOC (self);
}

static OskDeviceEvent*
new_device_event (void)
{
    OskDeviceEvent *ev;
    PyGILState_STATE gstate = PyGILState_Ensure();
    ev = PyObject_New(OskDeviceEvent, &osk_device_event_type);
    if (ev)
    {
        osk_device_event_type.tp_init((PyObject*) ev, NULL, NULL);
    }
    PyGILState_Release(gstate);
    return ev;
}

static PyObject *
osk_device_event_copy (OskDeviceEvent* self, PyObject *args)
{
    OskDeviceEvent *ev = new_device_event();
    if (ev)
    {
        ev->display = self->display;
        ev->xid_event = self->xid_event;
        ev->xi_type = self->xi_type;
        ev->type = self->type;
        ev->device_id = self->device_id;
        ev->source_id = self->source_id;
        ev->x = self->x;
        ev->y = self->y;
        ev->x_root = self->x_root;
        ev->y_root = self->y_root;
        ev->button = self->button;
        ev->state = self->state;
        ev->keycode = self->keycode;
        ev->keyval = self->keyval;
        ev->sequence = self->sequence;
        ev->time = self->time;

        Py_DECREF(ev->source_device);
        ev->source_device = self->source_device;
        Py_INCREF(ev->source_device);
    }
    return (PyObject*) ev;
}

static PyObject *
osk_device_event_get_time (OskDeviceEvent* self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->time);
}

static PyObject *
osk_device_event_set_source_device (OskDeviceEvent* self, PyObject* value)
{
    Py_DECREF(self->source_device);
    self->source_device = value;
    Py_INCREF(self->source_device);
    Py_RETURN_NONE;
}

static PyObject *
osk_device_event_get_source_device (OskDeviceEvent* self, PyObject *args)
{
    Py_INCREF(self->source_device);
    return self->source_device;
}

static PyMethodDef osk_device_event_methods[] = {
    { "__copy__",
      (PyCFunction) osk_device_event_copy, METH_NOARGS,  NULL },
    { "get_time",
      (PyCFunction) osk_device_event_get_time, METH_NOARGS,  NULL },
    { "get_source_device",
      (PyCFunction) osk_device_event_get_source_device, METH_NOARGS,  NULL },
    { "set_source_device",
      (PyCFunction) osk_device_event_set_source_device, METH_O,  NULL },
    { NULL, NULL, 0, NULL }
};

static PyMemberDef osk_device_event_members[] = {
    {"xid_event", T_UINT, offsetof(OskDeviceEvent, xid_event), READONLY, NULL },
    {"xi_type", T_UINT, offsetof(OskDeviceEvent, xi_type), RESTRICTED, NULL },
    {"type", T_UINT, offsetof(OskDeviceEvent, type), RESTRICTED, NULL },
    {"device_id", T_UINT, offsetof(OskDeviceEvent, device_id), READONLY, NULL },
    {"source_id", T_UINT, offsetof(OskDeviceEvent, source_id), READONLY, NULL },
    {"x", T_DOUBLE, offsetof(OskDeviceEvent, x), RESTRICTED, NULL },
    {"y", T_DOUBLE, offsetof(OskDeviceEvent, y), RESTRICTED, NULL },
    {"x_root", T_DOUBLE, offsetof(OskDeviceEvent, x_root), RESTRICTED, NULL },
    {"y_root", T_DOUBLE, offsetof(OskDeviceEvent, y_root), RESTRICTED, NULL },
    {"button", T_UINT, offsetof(OskDeviceEvent, button), RESTRICTED, NULL },
    {"state", T_UINT, offsetof(OskDeviceEvent, state), RESTRICTED, NULL },
    {"keycode", T_UINT, offsetof(OskDeviceEvent, keycode), READONLY, NULL },
    {"keyval", T_UINT, offsetof(OskDeviceEvent, keyval), READONLY, NULL },
    {"sequence", T_UINT, offsetof(OskDeviceEvent, sequence), RESTRICTED, NULL },
    {"time", T_UINT, offsetof(OskDeviceEvent, time), READONLY, NULL },
    {NULL}
};

static PyGetSetDef osk_device_event_getsetters[] = {
    {NULL}
};


//------------------------------------------------------------------------
// Devices
// -----------------------------------------------------------------------

typedef struct {
    PyObject_HEAD

    Display  *dpy;
    int       xi2_opcode;
    Atom      atom_product_id;

    GQueue   *event_queue;
    PyObject *event_handler;
    int       button_states[G_N_ELEMENTS(gdk_button_masks)];
} OskDevices;


static GdkFilterReturn osk_devices_event_filter (GdkXEvent  *gdk_xevent,
                                                 GdkEvent   *gdk_event,
                                                 OskDevices *dev);

static int osk_devices_select (OskDevices    *dev,
                               Window         win,
                               int            id,
                               unsigned char *mask,
                               unsigned int   mask_len);

static gboolean idle_process_event_queue (OskDevices* dev);
static void free_event_queue_element(gpointer data);


OSK_REGISTER_TYPE (OskDevices, osk_devices, "Devices")

static char *init_kwlist[] = {
    "event_handler",
    NULL
};

static int
osk_devices_init (OskDevices *dev, PyObject *args, PyObject *kwds)
{
    int event, error;
    int major = 2;
    int minor = 2;
    Status status;

    /* set display before anything else! */
    GdkDisplay* display = gdk_display_get_default ();

    if (!GDK_IS_X11_DISPLAY (display)) // Wayland, MIR?
    {
        PyErr_SetString (OSK_EXCEPTION, "not an X display");
        return -1;
    }

    dev->dpy = GDK_DISPLAY_XDISPLAY (display);
    memset(dev->button_states, 0, sizeof(dev->button_states));

    if (!XQueryExtension (dev->dpy, "XInputExtension",
                          &dev->xi2_opcode, &event, &error))
    {
        PyErr_SetString (OSK_EXCEPTION, "failed to initialize XInput extension");
        return -1;
    }

    // XIQueryVersion fails with X error BadValue if this isn't
    // the client's very first call. Someone, probably GTK is
    // successfully calling it before us, so just ignore the
    // error and move on.
    // gdk_x11_display_error_trap_push (gdk_display);
    status = XIQueryVersion (dev->dpy, &major, &minor);
    // gdk_x11_display_error_trap_pop_ignored (gdk_display);
    if (status == BadRequest)
    {
        PyErr_SetString (OSK_EXCEPTION, "XInput2 not available");
        return -1;
    }
    if (major * 1000 + minor < 2002)
    {
        PyErr_Format(OSK_EXCEPTION, "XInput 2.2 is not supported (found %d.%d).",
                                    major, minor);
        return -1;
    }

    if (!PyArg_ParseTupleAndKeywords (args, kwds,
                                      "|O", init_kwlist,
                                      &dev->event_handler))
    {
        return -1;
    }

    if (dev->event_handler)
    {
        unsigned char mask[2] = { 0, 0 };

        dev->event_queue = g_queue_new();
        if (!dev->event_queue)
            return -1;

        Py_INCREF (dev->event_handler);

        XISetMask (mask, XI_HierarchyChanged);

        osk_devices_select (dev, 0, XIAllDevices, mask, sizeof (mask));

        gdk_window_add_filter (NULL,
                               (GdkFilterFunc) osk_devices_event_filter,
                               dev);
    }

    dev->atom_product_id = XInternAtom(dev->dpy, XI_PROP_PRODUCT_ID, False);

    return 0;
}

static void
osk_devices_dealloc (OskDevices *dev)
{
    if (dev->event_handler)
    {
        unsigned char mask[2] = { 0, 0 };

        osk_devices_select (dev, 0, XIAllDevices, mask, sizeof (mask));

        gdk_window_remove_filter (NULL,
                                  (GdkFilterFunc) osk_devices_event_filter,
                                  dev);

        Py_DECREF (dev->event_handler);

        if (dev->event_queue)
        {
            g_queue_free_full(dev->event_queue, free_event_queue_element);
            dev->event_queue = NULL;
        }
    }
    OSK_FINISH_DEALLOC (dev);
}

static void
queue_event (OskDevices* dev, OskDeviceEvent* event, Bool discard_pending)
{
    PyGILState_STATE state = PyGILState_Ensure ();
    GQueue* queue = dev->event_queue;
    if (queue)
    {
        if (g_queue_is_empty(queue))
            g_idle_add ((GSourceFunc) idle_process_event_queue, dev);

        // Discard pending elements of the same type, e.g. to clear
        // motion event congestion (LP: #1210665).
        if (discard_pending)
        {
            GList *element = g_queue_peek_head_link (queue);
            for(;element;)
            {
                GList* next = element->next;
                OskDeviceEvent* e = (OskDeviceEvent*)element->data;
                if (e->device_id == event->device_id &&
                    e->type == event->type)
                {
                    //printf("deleting %d %d\n", e->type, event->type);
                    g_queue_delete_link(queue, element);
                    Py_DECREF(e);
                }
                element = next;
            }
        }

        // Enqueue the event.
        Py_INCREF(event);
        g_queue_push_head(queue, event);
    }
PyGILState_Release (state);
}

static gboolean idle_process_event_queue (OskDevices* dev)
{
    PyGILState_STATE state = PyGILState_Ensure ();
    PyObject *result;
    GQueue* queue = dev->event_queue;

    for (;;)
    {
        PyObject* arglist;
        OskDeviceEvent* event = g_queue_pop_tail (queue);
        if (!event)
            break;

        arglist = Py_BuildValue("(O)", event);
        if (arglist)
        {
            Py_INCREF(dev->event_handler);

            result = PyObject_CallObject(dev->event_handler, arglist);
            if (result)
                Py_DECREF (result);
            else
                PyErr_Print ();

            Py_DECREF (dev->event_handler);
            Py_DECREF (arglist);

        }

        Py_DECREF (event);
    }

    PyGILState_Release (state);

    return False;
}

static void free_event_queue_element(gpointer data)
{
    Py_DECREF(data);
}


static void
osk_devices_call_event_handler_device (OskDevices *dev,
                                       int         type,
                                       Display     *display,
                                       int         device_id,
                                       int         source_id
)
{
    OskDeviceEvent *ev = new_device_event();
    if (ev)
    {
        ev->display = display;
        ev->xi_type = type;
        ev->type = translate_event_type(type);
        ev->device_id = device_id;
        ev->source_id = source_id;

        queue_event (dev, ev, False);

        Py_DECREF(ev);
    }
}

static void
osk_devices_call_event_handler_pointer (OskDevices  *dev,
                                        int          type,
                                        Display     *display,
                                        Window       xid_event,
                                        int          device_id,
                                        int          source_id,
                                        double       x,
                                        double       y,
                                        double       x_root,
                                        double       y_root,
                                        unsigned int button,
                                        unsigned int state,
                                        unsigned int sequence,
                                        unsigned int time
)
{
    OskDeviceEvent *ev = new_device_event();
    if (ev)
    {
        ev->display = display;
        ev->xid_event = xid_event;
        ev->xi_type = type;
        ev->type = translate_event_type(type);
        ev->device_id = device_id;
        ev->source_id = source_id;
        ev->x = x;
        ev->y = y;
        ev->x_root = x_root;
        ev->y_root = y_root;
        ev->button = button;
        ev->state = state;
        ev->sequence = sequence;
        ev->time = time;

        queue_event (dev, ev, type == XI_Motion);

        Py_DECREF(ev);
    }
}

static void
osk_devices_call_event_handler_key (OskDevices *dev,
                                    int         type,
                                    Display*    display,
                                    int         device_id,
                                    int         source_id,
                                    int         keycode,
                                    int         keyval
)
{
    OskDeviceEvent *ev = new_device_event();
    if (ev)
    {
        ev->display = display;
        ev->xi_type = type;
        ev->type = translate_event_type(type);
        ev->device_id = device_id;
        ev->source_id = source_id;
        ev->keycode = keycode;
        ev->keyval = keyval;

        queue_event (dev, ev, False);

        Py_DECREF(ev);
    }
}

static int
osk_x_ignore_error (Display *dpy, XErrorEvent *err)
{
    (void) dpy;
    (void) err;
    return 0;
}

static int
osk_devices_select (OskDevices    *dev,
                    Window         win,
                    int            id,
                    unsigned char *mask,
                    unsigned int   mask_len)
{
    XIEventMask   events;
    XErrorHandler old_handler;
    GdkDisplay   *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);

    events.deviceid = id;
    events.mask = mask;
    events.mask_len = mask_len;

    if (win == 0)
        win = DefaultRootWindow (dev->dpy);

    /* GTK destroys the X window before emitting the unrealize signal,
     * so the window may be gone by the time we get here.
     * GDK error traps are unreliable in this context.
     * use a bare Xlib error handler to silently ignore BadWindow. */
    gdk_x11_display_error_trap_push (gdk_display);
    old_handler = XSetErrorHandler (osk_x_ignore_error);
    XISelectEvents (dev->dpy, win, &events, 1);
    XSync (dev->dpy, False);
    XSetErrorHandler (old_handler);
    return gdk_x11_display_error_trap_pop (gdk_display) ? -1 : 0;
}

/*
 * Translate XInput event type to GDK event type.
 * */
static unsigned int
translate_event_type (unsigned int xi_type)
{
    unsigned int type;

    switch (xi_type)
    {
        case XI_Motion:
        case XI_RawMotion:
            type = GDK_MOTION_NOTIFY; break;
        case XI_ButtonPress:
        case XI_RawButtonPress:
            type = GDK_BUTTON_PRESS; break;
        case XI_ButtonRelease:
        case XI_RawButtonRelease:
            type = GDK_BUTTON_RELEASE; break;
        case XI_Enter:
            type = GDK_ENTER_NOTIFY; break;
        case XI_Leave:
            type = GDK_LEAVE_NOTIFY; break;
        case XI_TouchBegin:
        case XI_RawTouchBegin:
            type = GDK_TOUCH_BEGIN; break;
        case XI_TouchUpdate:
        case XI_RawTouchUpdate:
            type = GDK_TOUCH_UPDATE; break;
        case XI_TouchEnd:
        case XI_RawTouchEnd:
            type = GDK_TOUCH_END; break;

        default: type = 0; break;
    }
    return type;
}

/*
 * Translate XInput state to GDK event state.
 * */
static unsigned int
translate_state (XIModifierState *mods_state,
                 XIButtonState   *button_state,
                 XIGroupState    *group_state)
{
    unsigned int state = 0;

    if (mods_state)
        state = mods_state->effective;

    if (button_state)
    {
        int n = MIN ((int)G_N_ELEMENTS(gdk_button_masks), button_state->mask_len * 8);
        int i;
        for (i = 0; i < n; i++)
            if (XIMaskIsSet (button_state->mask, i))
                state |= gdk_button_masks[i];
    }

    if (group_state)
        state |= (group_state->effective) << 13;

    return state;
}

static int
osk_devices_translate_keycode (int              keycode,
                               XIGroupState    *group,
                               XIModifierState *mods)
{
    unsigned int keyval = 0;

//    gdk_keymap_translate_keyboard_state (gdk_keymap_get_default (),
GdkDisplay *gdisplay = gdk_display_get_default ();
gdk_keymap_translate_keyboard_state (gdk_keymap_get_for_display (gdisplay),
                                         keycode,
                                         mods->effective,
                                         group->effective,
                                         &keyval, NULL, NULL, NULL);
    return (int) keyval;
}

/*
 * Get Gdk event state of the master pointer.
 *
 * The master aggregates currently pressed buttons and key presses from all
 * slave devices.
 *
 * Why we need aggregate button presses:
 * Francesco uses one pointing device for button presses and a different one
 * for motion events. The motion slave doesn't know about the button
 * slave's state. We can either aggregate the button state over all slaves
 * ourselves or rely on the master to do it for us.
 */
static unsigned int
get_master_state (OskDevices* dev)
{
    Window          win = DefaultRootWindow (dev->dpy);
    Window          root;
    Window          child;
    double          root_x;
    double          root_y;
    double          win_x;
    double          win_y;
    XIButtonState   buttons;
    XIModifierState mods;
    XIGroupState    group;
    unsigned int    state = 0;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    int master_id = 0;
    XIGetClientPointer(dev->dpy, None, &master_id);

//    gdk_error_trap_push ();
gdk_x11_display_error_trap_push (gdk_display);
    XIQueryPointer(dev->dpy,
                   master_id,
                   win,
                   &root,
                   &child,
                   &root_x,
                   &root_y,
                   &win_x,
                   &win_y,
                   &buttons,
                   &mods,
                   &group);
    if (!gdk_x11_display_error_trap_pop (gdk_display))
    {
        state = translate_state (&mods, &buttons, &group);
    }

    return state;
}

/*
 * Get current GDK event state.
 */
static unsigned int
get_current_state (OskDevices* dev)
{
    gsize i;

    // Get out-of-sync master state, for key state mainly.
    // Button state will be out-dated immediately before or after
    // button press/release events.
    unsigned int state = get_master_state (dev);

    // override button state with what we collected in-sync
    // -> no spurious stuck keys due to erroneous state in
    // motion events.
    for (i = 0; i < G_N_ELEMENTS(gdk_button_masks); i++)
    {
        int mask = gdk_button_masks[i];
        state &= ~mask;
        if (dev->button_states[i] > 0)
            state |= mask;
    }

    return state;
}

/*
 * Keep track of button state changes in sync with the events we receive.
 */
static void
update_state (int evtype, XIDeviceEvent* event, OskDevices* dev)
{
    int button = event->detail;
    if (button >= 1 && button < (int)G_N_ELEMENTS(dev->button_states))
    {
        int* count = dev->button_states + (button-1);
        if (evtype == XI_ButtonPress)
            (*count)++;
        if (evtype == XI_ButtonRelease)
        {
            (*count)--;

            // some protection at least against initially pressed buttons
            if (*count < 0)
                *count = 0;
        }
    }
}

/*
 * Handler for pointer and touch events.
 */
static Bool
handle_pointing_event (int evtype, XIEvent* xievent, OskDevices* dev)
{
    switch (evtype)
    {
        case XI_Motion:
        case XI_ButtonPress:
        case XI_ButtonRelease:
        case XI_TouchBegin:
        case XI_TouchUpdate:
        case XI_TouchEnd:
        {
            XIDeviceEvent *event = (XIDeviceEvent*) xievent;
            unsigned int button = 0;
            unsigned int sequence = 0;
            unsigned int state;

            if (evtype == XI_ButtonPress ||
                evtype == XI_ButtonRelease)
                button = event->detail;

            if (evtype == XI_TouchBegin ||
                evtype == XI_TouchUpdate ||
                evtype == XI_TouchEnd)
                sequence = event->detail;

            update_state(evtype, event, dev);
            state = get_current_state (dev);

            osk_devices_call_event_handler_pointer (dev,
                                                    evtype,
                                                    event->display,
                                                    event->event,
                                                    event->deviceid,
                                                    event->sourceid,
                                                    event->event_x,
                                                    event->event_y,
                                                    event->root_x,
                                                    event->root_y,
                                                    button,
                                                    state,
                                                    sequence,
                                                    event->time);
            return True; // handled
        }
    }
    return False;
}

/*
 * Handler for enter and leave events.
 * No enter leave events are generated for slave devices, we have
 * to rely on the master pointer here.
 */
static Bool
handle_enter_event (int evtype, XIEvent* xievent, OskDevices* dev)
{
    switch (evtype)
    {
        case XI_Enter:
        case XI_Leave:
        {
            XIEnterEvent *event = (XIEnterEvent*) xievent;

            unsigned int button = 0;
            unsigned int sequence = 0;
            unsigned int state = get_master_state (dev);

            osk_devices_call_event_handler_pointer (dev,
                                                    evtype,
                                                    event->display,
                                                    event->event,
                                                    event->deviceid,
                                                    event->sourceid,
                                                    event->event_x,
                                                    event->event_y,
                                                    event->root_x,
                                                    event->root_y,
                                                    button,
                                                    state,
                                                    sequence,
                                                    event->time);
            return True; // handled
        }
    }
    return False;
}

static GdkFilterReturn
osk_devices_event_filter (GdkXEvent  *gdk_xevent,
                          GdkEvent   *gdk_event,
                          OskDevices *dev)
{
    XGenericEventCookie *cookie = &((XEvent *) gdk_xevent)->xcookie;

    if (cookie->type == GenericEvent && cookie->extension == dev->xi2_opcode)
    {
        int evtype = cookie->evtype;
        XIEvent *event = cookie->data;

        //XIDeviceEvent *e = cookie->data;
        //printf("device %d evtype %d type %d  detail %d win %d\n", e->deviceid, evtype, e->type, e->detail, (int)e->event);

        if (handle_pointing_event(evtype, event, dev))
            return GDK_FILTER_CONTINUE;

        if (handle_enter_event(evtype, event, dev))
            return GDK_FILTER_CONTINUE;

        switch (evtype)
        {
            case XI_HierarchyChanged:
            {
                XIHierarchyEvent *event = cookie->data;

                if (event->flags & (XISlaveAdded |
                                    XISlaveRemoved |
                                    XISlaveAttached |
                                    XISlaveDetached))
                {
                    XIHierarchyInfo *info;
                    int              i;

                    for (i = 0; i < event->num_info; i++)
                    {
                        info = &event->info[i];

                        if (info->flags & XISlaveAdded)
                        {
                            osk_devices_call_event_handler_device (dev,
                                                            OSK_DEVICE_ADDED_EVENT,
                                                            event->display,
                                                            info->deviceid,
                                                            0);
                        }
                        else if (info->flags & XISlaveRemoved)
                        {
                            osk_devices_call_event_handler_device (dev,
                                                            OSK_DEVICE_REMOVED_EVENT,
                                                            event->display,
                                                            info->deviceid,
                                                            0);
                        }
                        else if (info->flags & XISlaveAttached)
                        {
                            osk_devices_call_event_handler_device (dev,
                                                            OSK_SLAVE_ATTACHED_EVENT,
                                                            event->display,
                                                            info->deviceid,
                                                            0);
                        }
                        else if (info->flags & XISlaveDetached)
                        {
                            osk_devices_call_event_handler_device (dev,
                                                            OSK_SLAVE_DETACHED_EVENT,
                                                            event->display,
                                                            info->deviceid,
                                                            0);
                        }
                    }
                }
                break;
            }

            case XI_DeviceChanged:
            {
                XIDeviceChangedEvent *event = cookie->data;

                if (event->reason == XISlaveSwitch)
                    osk_devices_call_event_handler_device (dev,
                                                           evtype,
                                                           event->display,
                                                           event->deviceid,
                                                           event->sourceid);
                break;
            }

            case XI_KeyPress:
            {
                XIDeviceEvent *event = cookie->data;

                if (!(event->flags & XIKeyRepeat))
                {
                    int keycode = event->detail;
                    int keyval = osk_devices_translate_keycode (keycode,
                                                                &event->group,
                                                                &event->mods);
                    if (keyval)
                        osk_devices_call_event_handler_key (dev,
                                                            evtype,
                                                            event->display,
                                                            event->deviceid,
                                                            event->sourceid,
                                                            keycode,
                                                            keyval);
                }
                break;
            }

            case XI_KeyRelease:
            {
                XIDeviceEvent *event = cookie->data;
                int keycode = event->detail;
                int keyval = osk_devices_translate_keycode (event->detail,
                                                            &event->group,
                                                            &event->mods);
                if (keyval)
                    osk_devices_call_event_handler_key (dev,
                                                        evtype,
                                                        event->display,
                                                        event->deviceid,
                                                        event->sourceid,
                                                        keycode,
                                                        keyval);
                break;
            }
        }
    }

    return GDK_FILTER_CONTINUE;
}

static Bool
osk_devices_get_product_id (OskDevices   *dev,
                            int           id,
                            unsigned int *vendor_id,
                            unsigned int *product_id)
{
    Status         rc;
    Atom           act_type;
    int            act_format;
    unsigned long  nitems, bytes;
    unsigned char *data;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    *vendor_id  = 0;
    *product_id = 0;

    gdk_x11_display_error_trap_push (gdk_display);
    rc = XIGetProperty (dev->dpy, id, dev->atom_product_id,
                        0, 2, False, XA_INTEGER,
                        &act_type, &act_format, &nitems, &bytes, &data);
    gdk_x11_display_error_trap_pop_ignored (gdk_display);

    if (rc == Success && nitems == 2 && act_format == 32)
    {
        guint32 *data32 = (guint32 *) data;

        *vendor_id  = *data32;
        *product_id = *(data32 + 1);

        XFree (data);

        return True;
    }

    return False;
}

static int
get_touch_mode (XIAnyClassInfo **classes, int num_classes)
{
    int i;
    for (i = 0; i < num_classes; i++)
    {
        XITouchClassInfo *class = (XITouchClassInfo*) classes[i];
        if (class->type == XITouchClass)
        {
            if (class->num_touches)
            {
                if (class->mode == XIDirectTouch ||
                    class->mode == XIDependentTouch)
                {
                    return class->mode;
                }
            }
        }
    }

    return 0;
}

/**
 * osk_devices_get_info:
 * @id: Id of an input device (int)
 *
 * Get a list of all input devices on the system. Each list item
 * is a device info tuple, see osk_devices_get_info().
 *
 * Returns: A list of device info tuples.
 */
static PyObject *
osk_devices_list (PyObject *self, PyObject *args)
{
    OskDevices   *dev = (OskDevices *) self;
    XIDeviceInfo *devices;
    int           i, n_devices;
    PyObject     *list;

    devices = XIQueryDevice (dev->dpy, XIAllDevices, &n_devices);

    list = PyList_New ((Py_ssize_t) n_devices);
    if (!list)
        goto error;

    for (i = 0; i < n_devices; i++)
    {
        PyObject    *value;
        unsigned int vid, pid;
        XIDeviceInfo *device = devices + i;

        osk_devices_get_product_id (dev, device->deviceid, &vid, &pid);

        value = Py_BuildValue ("(siiiBiii)",
                               device->name,
                               device->deviceid,
                               device->use,
                               device->attachment,
                               device->enabled,
                               vid, pid,
                               get_touch_mode(device->classes,
                                              device->num_classes));
        if (!value)
            goto error;

        if (PyList_SetItem (list, i, value) < 0)
        {
            Py_DECREF (value);
            goto error;
        }
    }

    XIFreeDeviceInfo (devices);

    return list;

error:
    PyErr_SetString (OSK_EXCEPTION, "failed to get device list");

    Py_XDECREF (list);
    XIFreeDeviceInfo (devices);

    return NULL;
}

/**
 * osk_devices_get_info:
 * @id: Id of an input device (int)
 *
 * Get information about an input device. The device info is returned
 * as a tuple.
 *
 * 0: name (string)
 * 1: id (int)
 * 2: type/use (int)
 * 3: attachment/master id (int)
 * 4: enabled (bool)
 * 5: vendor id (int)
 * 6: product id (int)
 *
 * Returns: A device info tuple.
 */
static PyObject *
osk_devices_get_info (PyObject *self, PyObject *args)
{
    OskDevices   *dev = (OskDevices *) self;
    XIDeviceInfo *devices;
    PyObject     *value;
    int           id, n_devices;
    unsigned int  vid, pid;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    if (!PyArg_ParseTuple (args, "i", &id))
        return NULL;

    gdk_x11_display_error_trap_push (gdk_display);
    devices = XIQueryDevice (dev->dpy, id, &n_devices);
    gdk_display_flush (gdk_display);

    if (gdk_x11_display_error_trap_pop (gdk_display))
    {
        PyErr_SetString (OSK_EXCEPTION, "invalid device id");
        return NULL;
    }

    osk_devices_get_product_id (dev, id, &vid, &pid);

    value = Py_BuildValue ("(siiiBii)",
                           devices[0].name,
                           devices[0].deviceid,
                           devices[0].use,
                           devices[0].attachment,
                           devices[0].enabled,
                           vid, pid);

    XIFreeDeviceInfo (devices);

    return value;
}

/**
 * osk_devices_attach:
 * @id:     Id of the device to attach (int)
 * @master: Id of a master device (int)
 *
 * Attaches the device with @id to @master.
 *
 */
static PyObject *
osk_devices_attach (PyObject *self, PyObject *args)
{
    OskDevices       *dev = (OskDevices *) self;
    XIAttachSlaveInfo info;
    int               id, master;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    if (!PyArg_ParseTuple (args, "ii", &id, &master))
        return NULL;

    info.type = XIAttachSlave;
    info.deviceid = id;
    info.new_master = master;

    gdk_x11_display_error_trap_push (gdk_display);

    XIChangeHierarchy (dev->dpy, (XIAnyHierarchyChangeInfo *) &info, 1);
    gdk_display_flush (gdk_display);

    if (gdk_x11_display_error_trap_pop (gdk_display))
    {
        PyErr_SetString (OSK_EXCEPTION, "failed to attach device");
        return NULL;
    }
    Py_RETURN_NONE;
}

/**
 * osk_devices_detach:
 * @id: Id of the device to detach (int)
 *
 * Detaches an input device for its master. Detached devices
 * stop sending "core events".
 *
 */
static PyObject *
osk_devices_detach (PyObject *self, PyObject *args)
{
    OskDevices       *dev = (OskDevices *) self;
    XIDetachSlaveInfo info;
    int               id;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    if (!PyArg_ParseTuple (args, "i", &id))
        return NULL;

    info.type = XIDetachSlave;
    info.deviceid = id;

    gdk_x11_display_error_trap_push (gdk_display);

    XIChangeHierarchy (dev->dpy, (XIAnyHierarchyChangeInfo *) &info, 1);
    gdk_display_flush (gdk_display);

    if (gdk_x11_display_error_trap_pop (gdk_display))
    {
        PyErr_SetString (OSK_EXCEPTION, "failed to detach device");
        return NULL;
    }
    Py_RETURN_NONE;
}

/**
 * osk_devices_grab_device:
 * @id:     Id of the device to attach (int)
 *
 * Grabs the device with @id.
 *
 */
static PyObject *
osk_devices_grab_device (PyObject *self, PyObject *args)
{
    OskDevices       *dev = (OskDevices *) self;
    int               id;
    int               _win;
    Window win;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    unsigned char mask[1] = {0};
    XIEventMask events;

    Status status;
    gint error;

    if (!PyArg_ParseTuple (args, "ii", &id, &_win))
        return NULL;

    win = (Window)_win;
    if (!win)
        win = DefaultRootWindow (dev->dpy);

    events.deviceid = id;
    events.mask = mask;
    events.mask_len = sizeof(mask);

    gdk_x11_display_error_trap_push (gdk_display);
    status = XIGrabDevice(dev->dpy, id, win, CurrentTime, None,
                                 XIGrabModeSync, XIGrabModeAsync,
                                 True, &events);
    error = gdk_x11_display_error_trap_pop (gdk_display);

    if (status != Success || error)
    {
        PyErr_Format (OSK_EXCEPTION, "failed to grab device (0x%x, 0x%x)",
                      status, error);
        return NULL;
    }
    Py_RETURN_NONE;
}

/**
 * osk_devices_ungrab_device:
 * @id:     Id of the device to ungrab (int)
 *
 * Ungrabs the device with @id.
 *
 */
static PyObject *
osk_devices_ungrab_device (PyObject *self, PyObject *args)
{
    OskDevices *dev = (OskDevices *) self;
    int         id;
    Status      status;
    gint        error;
    GdkDisplay *gdk_display = gdk_x11_lookup_xdisplay (dev->dpy);
    if (!PyArg_ParseTuple (args, "i", &id))
        return NULL;

    gdk_x11_display_error_trap_push (gdk_display);
    status = XIUngrabDevice(dev->dpy, id, CurrentTime);
    error = gdk_x11_display_error_trap_pop (gdk_display);
    if (status != Success || error)
    {
        PyErr_Format (OSK_EXCEPTION, "failed to ungrab device (0x%x, 0x%x)",
                      status, error);
        return NULL;
    }
    Py_RETURN_NONE;
}

/**
 * osk_devices_select_events:
 * @id:  Id of the device to select events for (int)
 * @event_mask: Bit mask of XI events to select (long)
 *
 * Selects XInput events for a device. The device will send the selected
 * events to the #event_handler. If the calling instance was constructed
 * without the #event_handler keyword, this function is a no-op.
 */
static PyObject *
osk_devices_select_events (PyObject *self, PyObject *args)
{
    OskDevices   *dev = (OskDevices *) self;
    unsigned char mask[4] = { 0, 0, 0, 0};
    int           device_id;
    unsigned long event_mask;
    int           _win;
    Window        win;

    if (!PyArg_ParseTuple (args, "iil", &_win, &device_id, &event_mask))
        return NULL;

    win = (Window) _win;

    if (dev->event_handler)
    {
        int i;
        int nbits = MIN(sizeof(event_mask), sizeof(mask)) * 8;
        for (i = 0; i < nbits; i++)
        {
            if (event_mask & 1<<i)
                XISetMask (mask, i);
        }

        if (osk_devices_select (dev, win, device_id, mask, sizeof (mask)) < 0)
        {
            PyErr_SetString (OSK_EXCEPTION, "failed to open device");
            return NULL;
        }
    }
    Py_RETURN_NONE;
}

/**
 * osk_devices_unselect_events:
 * @id: Id of the device to close (int)
 *
 * "Closes" a device. If the calling instance was constructed
 * without the #event_handler keyword or the device was not
 * previously opened, this function is a no-op.
 *
 */
static PyObject *
osk_devices_unselect_events (PyObject *self, PyObject *args)
{
    OskDevices   *dev = (OskDevices *) self;
    unsigned char mask[1] = { 0 };
    int           device_id;
    int           _win;
    Window        win;

    if (!PyArg_ParseTuple (args, "ii", &_win, &device_id))
        return NULL;

    win = (Window) _win;

    if (dev->event_handler)
    {
        if (osk_devices_select (dev, win, device_id, mask, sizeof (mask)) < 0)
        {
            PyErr_SetString (OSK_EXCEPTION, "failed to close device");
            return NULL;
        }
    }
    Py_RETURN_NONE;
}

static PyObject *
osk_devices_get_client_pointer (PyObject *self, PyObject *args)
{
    OskDevices   *dev = (OskDevices *) self;

    int device_id = 0;
    XIGetClientPointer(dev->dpy, None, &device_id);

    return PyLong_FromLong(device_id);
}

static PyMethodDef osk_devices_methods[] = {
    { "list",            osk_devices_list,            METH_NOARGS,  NULL },
    { "get_info",        osk_devices_get_info,        METH_VARARGS, NULL },
    { "attach",          osk_devices_attach,          METH_VARARGS, NULL },
    { "detach",          osk_devices_detach,          METH_VARARGS, NULL },
    { "grab_device",     osk_devices_grab_device,     METH_VARARGS, NULL },
    { "ungrab_device",   osk_devices_ungrab_device,   METH_VARARGS, NULL },
    { "select_events",   osk_devices_select_events,   METH_VARARGS, NULL },
    { "unselect_events", osk_devices_unselect_events, METH_VARARGS, NULL },
    { "get_client_pointer", osk_devices_get_client_pointer, METH_NOARGS, NULL },
    { NULL, NULL, 0, NULL }
};

