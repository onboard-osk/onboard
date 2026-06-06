/*
 * Copyright © 2009-2010, 2012-2014 marmuta <marmvta@gmail.com>
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
 * Examples:
 *
 * import pypredict
 * model = pypredict.DynamicModel()
 *
 * model.count_ngram([u"we"])
 * model.count_ngram([u"we", u"saw"])
 * model.count_ngram([u"we", u"saw", u"dolphins"])
 * model.count_ngram([u"saw"])
 * model.count_ngram([u"saw", u"dolphins"])
 * model.count_ngram([u"dolphins"])
 *
 * for ng in model.iter_ngrams():
 *     print ng
 *
 * model.save("/tmp/dolphins.lm")
 * model.predict([u"we", u"saw", u""])
 *
 * model.load("/tmp/dolphins.lm")
 * model.predict([u"we", u"saw", u"dol"], 2)
 */

#include "Python.h"
#include "structmember.h"
#include <string>

#include "lm_unigram.h"
#include "lm_dynamic.h"
#include "lm_dynamic_kn.h"
#include "lm_dynamic_cached.h"
#include "lm_merged.h"

using namespace std;

#if PY_MAJOR_VERSION >= 3
    #define PyString_Check PyUnicode_Check
    #define PyString_FromString PyUnicode_FromString
    #define PyString_AsString PyByteArray_AsString

    #define PyInt_Check PyLong_Check
    #define PyInt_FromLong PyLong_FromLong
    #define PyInt_AsLong PyLong_AsLong

    #define PyNumber_Int PyNumber_Long
#else
#endif

// hide warning: invalid access to non-static data member of NULL object
#define my_offsetof(TYPE, MEMBER) \
        ((size_t)((char *)&(((TYPE *)0x10)->MEMBER) - (char*)0x10))

#if 0
// NOTE: disabled - PoolAllocator is a static singleton whose destructor
// runs in atexit after Python teardown, making PyMem_Free invalid (SIGSEGV).
// python recommends using it's own memory allocator for extensions
void* HeapAlloc(size_t size)
{
    void* p = PyMem_Malloc(size);
    return p;
}

void HeapFree(void* p)
{
    PyMem_Free(p);
}
#else
void* HeapAlloc(size_t size)
{
    return malloc(size);
}

void HeapFree(void* p)
{
    free(p);
}
#endif

// Non-virtual helper class to protect the vtable of LanguageModels.
// The vtable pointer is located in the first 8(4) byte of the object.
// However that is where python expects PyObject_HEAD to reside and
// therefor the vtable is destroyed when python touches the objects
// reference count. Wrapping LanguageModels in this class keeps
// the vtable safe.
template <class T>
class  PyWrapper
{
    public:
        PyWrapper()
        {
            o = new T;
        }
        ~PyWrapper()
        {
            delete o;
        }
        T* operator->()
        {
            return o;
        }

        // python support
        PyObject_HEAD
        T* o;
};

typedef PyWrapper<LanguageModel> PyLanguageModel;
typedef PyWrapper<UnigramModel> PyUnigramModel;
typedef PyWrapper<DynamicModel> PyDynamicModel;
typedef PyWrapper<DynamicModelKN> PyDynamicModelKN;
typedef PyWrapper<CachedDynamicModel> PyCachedDynamicModel;

// Another, derived wrapper to encapsulate python reference handling
// of a vector of LanguageModels.
template <class T>
class  PyMergedModelWrapper : public PyWrapper<T>
{
    public:
        PyMergedModelWrapper(const vector<PyLanguageModel*>& models)
        {
            // extract the c++ language models
            vector<LanguageModel*> cmodels;
            for (int i=0; i<(int)models.size(); i++)
            {
                cmodels.push_back(models[i]->o);
                Py_INCREF(models[i]);  // don't let the python objects go away
            }
            (*this)->set_models(cmodels);  // class T must be of type MergedModel

            // store python objects so we can later decrement their refcounts
            references = models;
        }

        ~PyMergedModelWrapper()
        {
            // let go of the python objects now
            for (int i=0; i<(int)references.size(); i++)
                Py_DECREF(references[i]);
        }

        vector<PyLanguageModel*> references;
};

typedef PyMergedModelWrapper<OverlayModel> PyOverlayModel;
typedef PyMergedModelWrapper<LinintModel> PyLinintModel;
typedef PyMergedModelWrapper<LoglinintModel> PyLoglinintModel;


//------------------------------------------------------------------------
// python helper functions
//------------------------------------------------------------------------

// Extract wchar_t string from PyUnicodeObject.
// Allocates string through python memory manager, call PyMem_Free() when done.
static wchar_t*
pyunicode_to_wstr(PyObject* object)
{
    if (PyUnicode_Check(object))
    {
#if PY_MAJOR_VERSION >= 3
        return PyUnicode_AsWideCharString(object, NULL);
#else
        PyUnicodeObject* o = (PyUnicodeObject*) object;
        int size = PyUnicode_GET_SIZE(o);
        wchar_t* wstr = (wchar_t*) PyMem_Malloc(sizeof(wchar_t) * (size+1));
        if (PyUnicode_AsWideChar(o, wstr, size) < 0)
        {
            PyErr_SetString(PyExc_ValueError, "cannot convert to wide string");
            PyMem_Free(wstr);
            return NULL;
        }
        wstr[size] = 0;
        return wstr;
#endif
    }
    PyErr_SetString(PyExc_TypeError, "expected unicode object");
    return NULL;
}

// free an array of unicode strings
void free_strings(wchar_t** words, int n)
{
    int i;
    if (words)
    {
        for(i=0; i<n; i++)
            if (words[i])
                PyMem_Free(words[i]);
        PyMem_Free(words);
    }
}

// Extracts array of wchar_t strings from a PySequence object.
// Allocates array through python memory manager, must call PyMem_Free() on it.
// Somewhat redundant as there is now a vector<> version doing the same below.
static wchar_t**
pyseqence_to_strings(PyObject* sequence, int* num_elements)
{
    int i;
    int n = 0;
    int error = 0;
    PyObject *item;
    wchar_t** strings = NULL;

    if (!PySequence_Check(sequence))
    {
        PyErr_SetString(PyExc_ValueError, "expected sequence type");
    }
    else
    {
        n = PySequence_Length(sequence);
        strings = (wchar_t**)PyMem_Malloc(sizeof(*strings) * n);
        if (!strings)
        {
            PyErr_SetString(PyExc_MemoryError, "failed to allocate strings");
            return NULL;
        }
        memset(strings, 0, sizeof(*strings) * n);

        for (i = 0; i < n; i++)
        {
            item = PySequence_GetItem(sequence, i);
            if (item == NULL)
            {
                PyErr_SetString(PyExc_ValueError, "bad item in sequence");
                error = 1;
                break;
            }
            if (!PyUnicode_Check(item))
            {
                PyErr_SetString(PyExc_ValueError, "item is not a unicode string");
                error = 1;
                break;
            }

            strings[i] = pyunicode_to_wstr(item);
            if (!strings[i])
            {
                error = 1;
                break;
            }

            Py_DECREF(item); /* Discard reference ownership */
        }
    }

    if (error)
    {
        free_strings(strings, n);
        return NULL;
    }

    *num_elements = n;
    return strings;
}


// free a vector of unicode strings
void free_strings(vector<wchar_t*>& strings)
{
    vector<wchar_t*>::iterator it;
    for(it=strings.begin(); it!=strings.end(); it++)
        PyMem_Free(*it);
}

// Extracts vector of wchar_t strings from a PySequence object.
static bool
pyseqence_to_strings(PyObject* sequence, vector<wchar_t*>& strings)
{
    int i;
    int n = 0;
    int error = 0;
    PyObject *item;

    if (!PySequence_Check(sequence))
    {
        PyErr_SetString(PyExc_ValueError, "expected sequence type");
        error = 1;
    }
    else
    {
        n = PySequence_Length(sequence);
        strings.reserve(n);

        for (i = 0; i < n; i++)
        {
            item = PySequence_GetItem(sequence, i);
            if (item == NULL)
            {
                PyErr_SetString(PyExc_ValueError, "bad item in sequence");
                error = 1;
            }
            if (!PyUnicode_Check(item))
            {
                PyErr_SetString(PyExc_ValueError, "item is not a unicode string");
                error = 1;
            }
            wchar_t* s = pyunicode_to_wstr(item);
            if (!s)
            {
                error = 1;
            }
            Py_XDECREF(item);

            if (error)
                break;

            strings.push_back(s);
        }
    }

    if (error)
    {
        free_strings(strings);
        return false;
    }

    return true;
}

static bool
pyseqence_to_doubles(PyObject* sequence, vector<double>& results)
{
    if (!PySequence_Check(sequence))
    {
        PyErr_SetString(PyExc_ValueError, "expected sequence type");
        return false;
    }
    else
    {
        int n = PySequence_Length(sequence);
        for (int i = 0; i < n; i++)
        {
            PyObject* item = PySequence_GetItem(sequence, i);
            if (item == NULL)
            {
                PyErr_SetString(PyExc_ValueError, "bad item in sequence");
                return false;
            }

            results.push_back(PyFloat_AsDouble(item));

            Py_DECREF(item); /* Discard reference ownership */
        }
    }

    return true;
}

template <class T, class PYTYPE>
static bool
pyseqence_to_objects(PyObject* sequence, vector<T*>& results, PYTYPE* type)
{
    int i;
    int n = 0;
    PyObject *item;

    if (!PySequence_Check(sequence))
    {
        PyErr_SetString(PyExc_ValueError, "expected sequence type");
        return false;
    }
    else
    {
        n = PySequence_Length(sequence);
        for (i = 0; i < n; i++)
        {
            item = PySequence_GetItem(sequence, i);
            if (item == NULL)
            {
                PyErr_SetString(PyExc_ValueError, "bad item in sequence");
                return false;
            }
            if (!PyObject_TypeCheck(item, type))
            {
                PyErr_SetString(PyExc_ValueError,
                                      "unexpected item type in sequence");
                return false;
            }

            results.push_back(((T*)item));

            Py_DECREF(item); /* Discard reference ownership */
        }
    }

    return true;
}

//------------------------------------------------------------------------
// LanguageModel - python interface for LanguageModel
//------------------------------------------------------------------------
// Abstract base class of all python accessible language models.

bool check_error(const LMError err, const char* filename = NULL)
{
//        char msg[128];
//        snprintf(msg, ALEN(msg)-1, "loading failed (%d)", err);
//        PyErr_SetString(PyExc_IOError, msg);
    if (!err)
        return false;

    string filestr = (filename ? string(" in '") + filename + "'" : "");
    switch(err)
    {
        case ERR_NONE:
            break;
        case ERR_NOT_IMPL:
            PyErr_SetString(PyExc_NotImplementedError, "Not implemented");
            break;
        case ERR_FILE:
            if (filename)
                PyErr_SetFromErrnoWithFilename(PyExc_IOError,
                                               const_cast<char*>(filename));
            else
                PyErr_SetFromErrno(PyExc_IOError);
            break;
        case ERR_MEMORY:
            PyErr_SetString(PyExc_MemoryError, "Out of memory");
            break;
        default:
        {
            string msg;
            switch (err)
            {
                case ERR_NUMTOKENS:
                    msg = "too few tokens"; break;
                case ERR_ORDER_UNEXPECTED:
                    msg = "unexpected ngram order"; break;
                case ERR_ORDER_UNSUPPORTED:
                    msg = "ngram order not supported by this model"; break;
                case ERR_COUNT:
                    msg = "ngram count mismatch"; break;
                case ERR_UNEXPECTED_EOF:
                    msg = "unexpected end of file"; break;
                case ERR_WC2MB:
                    msg = "error encoding to UTF-8"; break;
                case ERR_MD2WC:
                    msg = "error decoding to Unicode"; break;
                default:
                    PyErr_SetString(PyExc_ValueError, "Unknown Error");
                    return true;
            }

            PyErr_Format(PyExc_IOError,
                     "Bad file format, %s%s",
                      msg.c_str(), filestr.c_str());
        }
    }
    return true;
}

static PyObject *
predict(PyLanguageModel* self, PyObject* args, PyObject *kwds,
        bool with_probs = false)
{
    int i;
    int error = 0;
    PyObject *result = NULL;
    PyObject *ocontext = NULL;
    vector<wchar_t*> context;
    int limit = -1;
    long options = 0;

    static char *kwlist[] = {(char*)"context",
                             (char*)"limit",
                             (char*)"options",
                             NULL};
    if (PyArg_ParseTupleAndKeywords(args, kwds, "O|IL:predict", kwlist,
                                    &ocontext,
                                    &limit,
                                    &options))
    {
        if (!pyseqence_to_strings(ocontext, context))
            return NULL;

        vector<LanguageModel::Result> results;
        (*self)->predict(results, context, limit, (uint32_t) options);

        // build return list
        result = PyList_New(results.size());
        if (!result)
        {
            PyErr_SetString(PyExc_MemoryError, "failed to allocate results list");
            error = 1;
        }
        else
        {
            for (i=0; i<(int)results.size(); i++)
            {
                const wstring& word = results[i].word;

                PyObject* oword  = PyUnicode_FromWideChar(word.c_str(), word.size());
                if (!oword)
                {
                    PyErr_SetString(PyExc_ValueError, "failed to create unicode string for return list");
                    error = 1;
                    Py_XDECREF(oword);
                    break;
                }
                if (with_probs)
                {
                    double p = results[i].p;
                    PyObject* op     = PyFloat_FromDouble(p);
                    PyObject* otuple = PyTuple_New(2);
                    PyTuple_SetItem(otuple, 0, oword);
                    PyTuple_SetItem(otuple, 1, op);
                    PyList_SetItem(result, i, otuple);
                }
                else
                {
                    PyList_SetItem(result, i, oword);
                }
            }
        }

        free_strings(context);

        if (error)
        {
            Py_XDECREF(result);
            return NULL;
        }
    }
    return result;
}

static PyObject *
LanguageModel_clear(PyLanguageModel* self)
{
    (*self)->clear();
    Py_RETURN_NONE;
}

// predict returns a list of words
static PyObject *
LanguageModel_predict(PyLanguageModel* self, PyObject* args, PyObject* kwds)
{
    return predict(self, args, kwds);
}

// predictp returns a list of (word, probability) tuples
static PyObject *
LanguageModel_predictp(PyLanguageModel* self, PyObject* args, PyObject* kwds)
{
    return predict(self, args, kwds, true);
}

static PyObject *
LanguageModel_get_probability(PyLanguageModel* self, PyObject* args)
{
    int n;
    PyObject *result = NULL;
    PyObject *ongram = NULL;
    wchar_t** ngram = NULL;

    if (PyArg_ParseTuple(args, "O:get_probability", &ongram))
    {
        ngram = pyseqence_to_strings(ongram, &n);
        if (!ngram)
            return NULL;

        double p = (*self)->get_probability(ngram, n);
        result = PyFloat_FromDouble(p);

        free_strings(ngram, n);
    }
    return result;
}

static PyObject *
LanguageModel_lookup_word(PyLanguageModel* self, PyObject* value)
{
    wchar_t* word = pyunicode_to_wstr(value);
    if (!word)
    {
        PyErr_SetString(PyExc_ValueError, "parameter must be unicode string");
        return NULL;
    }

    int result = (*self)->lookup_word(word);

    if (word)
        PyMem_Free(word);

    return PyInt_FromLong(result);
}

static PyObject *
LanguageModel_load(PyLanguageModel *self, PyObject *args)
{
    char* filename = NULL;

    if (!PyArg_ParseTuple(args, "s:load", &filename))
        return NULL;

    LMError e;
    //Py_BEGIN_ALLOW_THREADS;
    e = (*self)->load(filename);
    //Py_END_ALLOW_THREADS;

    if (check_error(e, filename))
        return NULL;

    Py_RETURN_NONE;
}

static PyObject *
LanguageModel_save(PyLanguageModel *self, PyObject *args)
{
    char* filename = NULL;

    if (!PyArg_ParseTuple(args, "s:save", &filename))
        return NULL;

    if (check_error((*self)->save(filename), filename))
        return NULL;

    Py_RETURN_NONE;
}


static PyMethodDef LanguageModel_methods[] = {
    {"clear", (PyCFunction)LanguageModel_clear, METH_NOARGS,
     ""
    },
    {"predict", (PyCFunction)LanguageModel_predict, METH_VARARGS | METH_KEYWORDS,
     ""
    },
    {"predictp", (PyCFunction)LanguageModel_predictp, METH_VARARGS | METH_KEYWORDS,
     ""
    },
    {"get_probability", (PyCFunction)LanguageModel_get_probability, METH_VARARGS,
     ""
    },
    {"lookup_word", (PyCFunction)LanguageModel_lookup_word, METH_O,
     ""
    },
    {"load", (PyCFunction)LanguageModel_load, METH_VARARGS,
     ""
    },
    {"save", (PyCFunction)LanguageModel_save, METH_VARARGS,
     ""
    },
    {NULL}  /* Sentinel */
};

static PyTypeObject LanguageModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.LanguageModel",             /*tp_name*/
    sizeof(PyLanguageModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    0, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "LanguageModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    LanguageModel_methods,     /* tp_methods */
    0,                         /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,                         /* tp_init */
    0,                         /* tp_alloc */
    0                          /* tp_new */
};


//------------------------------------------------------------------------
// NGramIter - python iterator object for traversal of the n-gram trie
//------------------------------------------------------------------------
class NGramIter
{
    public:
        NGramIter(DynamicModelBase* lm)
        {
            this->lm = lm;
            it = lm->ngrams_begin();
            first_time = true;
        }
        ~NGramIter()
        {
            delete it;
        }

        // next() function of pythons iterator interface
        BaseNode* next()
        {
            do
            {
                // python semantics: first item _after_ initial next()
                if (first_time)
                    first_time = false;
                else
                    (*it)++;
            } while (it->at_root());
            return *(*it);
        }

        void get_ngram(vector<WordId>& ngram)
        {
            it->get_ngram(ngram);
        }

    public:
        PyObject_HEAD

        DynamicModelBase* lm;
        DynamicModelBase::ngrams_iter* it;
        bool first_time;
};

//typedef NGramIter<DynamicModel> DynamicModel;
static void
NGramIter_dealloc(NGramIter* self)
{
    #ifndef NDEBUG
    printf("NGramIter_dealloc: NGramIter=%p, ob_refcnt=%d\n", self, (int)((PyObject*)self)->ob_refcnt);
    #endif
    self->~NGramIter();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyObject *
NGramIter_iter(PyObject *self)
{
    Py_INCREF(self);
    return self;  // python iterator interface: iter() on iterators returns self
}

static PyObject *
NGramIter_iternext(PyObject *self)
{
    int i;
    int error = 0;
    PyObject *result = NULL;

    NGramIter* iter = (NGramIter*) self;

    BaseNode* node = iter->next();
    if (!node)
        return NULL;

    vector<WordId> ngram;
    iter->get_ngram(ngram);

    vector<int> values;
    iter->lm->get_node_values(node, ngram.size(), values);

    // build return value
    result = PyTuple_New(1+values.size());
    if (!result)
    {
        PyErr_SetString(PyExc_MemoryError, "failed to allocate result tuple");
        error = 1;
    }
    else
    {
        PyObject *ongram = PyTuple_New(ngram.size());
        for (i=0; i<(int)ngram.size(); i++)
        {
            PyObject* oword = NULL;
            const wchar_t* word = iter->lm->dictionary.id_to_word(ngram[i]);
            if (word)
            {
                oword = PyUnicode_FromWideChar(word, wcslen(word));
                if (!oword)
                {
                    PyErr_SetString(PyExc_ValueError, "failed to create unicode string for ngram tuple");
                    error = 1;
                    Py_XDECREF(oword);
                    break;
                }
            }
            else
            {
                Py_INCREF(Py_None);
                oword = Py_None;
            }
            PyTuple_SetItem(ongram, i, oword);
        }

        if (!error)
        {
            PyTuple_SetItem(result, 0, ongram);
            for (int i=0; i<(int)values.size(); i++)
                PyTuple_SetItem(result, 1+i, PyInt_FromLong(values[i]));
        }
    }

    if (error)
    {
        Py_XDECREF(result);
        return NULL;
    }

    return result;
}

static PyTypeObject NGramIterType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.NGramIter",             /*tp_name*/
    sizeof(NGramIter),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    //(destructor)(deallocfunc)NGramIter_dealloc<DynamicModel>(), /*tp_dealloc*/
    (destructor)NGramIter_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "NGramIter objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    NGramIter_iter,		               /* tp_iter */
    NGramIter_iternext,		               /* tp_iternext */
    0,     /* tp_methods */
    0,     /* tp_members */
    0,   /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,      /* tp_init */
    0,                         /* tp_alloc */
    0,             /* tp_new */
};

//------------------------------------------------------------------------
// UnigramModel - python interface for UnigramModel
//------------------------------------------------------------------------

static PyObject *
UnigramModel_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    PyUnigramModel *self;

    self = (PyUnigramModel *)type->tp_alloc(type, 0);
    if (self != NULL) {
        self = new(self) PyUnigramModel;   // placement new
    }
    return (PyObject *)self;
}

static int
UnigramModel_init(PyUnigramModel *self, PyObject *args, PyObject *kwds)
{
    return 0;
}

static void
UnigramModel_dealloc(PyUnigramModel* self)
{
    self->~PyUnigramModel();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyObject *
UnigramModel_count_ngram(PyUnigramModel* self, PyObject* args)
{
    PyObject* ngram = NULL;
    int increment = 1;
    int allow_new_words = true;

    if (! PyArg_ParseTuple(args, "O|II:count_ngram",
              &ngram, &increment, &allow_new_words))
        return NULL;

    vector<wchar_t*> words;
    if (!pyseqence_to_strings(ngram, words))
        return NULL;

    if (!(*self)->count_ngram(&words[0], words.size(),
                              increment, allow_new_words))
    {
        PyErr_SetString(PyExc_MemoryError, "out of memory");
        return NULL;
    }

    free_strings(words);

    Py_RETURN_NONE;
}

static PyObject *
UnigramModel_get_ngram_count(PyUnigramModel* self, PyObject* ngram)
{
    int n;
    wchar_t** words = pyseqence_to_strings(ngram, &n);
    if (!words)
        return NULL;

    int count = (*self)->get_ngram_count((const wchar_t**) words, n);
    PyObject* result = PyInt_FromLong(count);

    free_strings(words, n);

    return result;
}

static PyObject *
UnigramModel_memory_size(PyUnigramModel* self)
{
    vector<long> values;
    (*self)->get_memory_sizes(values);

    PyObject* result = PyTuple_New(values.size());
    if (!result)
    {
        PyErr_SetString(PyExc_MemoryError, "failed to allocate tuple");
        return NULL;
    }
    for (int i=0; i<(int)values.size(); i++)
        PyTuple_SetItem(result, i, PyInt_FromLong(values[i]));

    return result;
}

// returns an object implementing pythons iterator interface
static PyObject *
UnigramModel_iter_ngrams(PyUnigramModel *self)
{
    NGramIter* iter = PyObject_New(NGramIter, &NGramIterType);
    if (!iter)
        return NULL;
    iter = new(iter) NGramIter(self->o);   // placement new
    Py_INCREF(iter);

    #ifndef NDEBUG
    printf("UnigramModel_iter_ngrams: NGramIter=%p, ob_refcnt=%d\n", iter, (int)((PyObject*)iter)->ob_refcnt);
    #endif

    return (PyObject*) iter;
}

static PyObject *
UnigramModel_get_order(PyUnigramModel *self, void *closure)
{
    return PyInt_FromLong(1);
}

static PyMemberDef UnigramModel_members[] = {
    {NULL}  /* Sentinel */
};

static PyGetSetDef UnigramModel_getsetters[] = {
    {(char*)"order",
     (getter)UnigramModel_get_order, (setter)NULL,
     (char*)"order of the language model",
     NULL},
    {NULL}  /* Sentinel */
};

static PyMethodDef UnigramModel_methods[] = {
    {"count_ngram", (PyCFunction)UnigramModel_count_ngram, METH_VARARGS,
     ""
    },
    {"get_ngram_count", (PyCFunction)UnigramModel_get_ngram_count, METH_O,
     ""
    },
    {"iter_ngrams", (PyCFunction)UnigramModel_iter_ngrams, METH_NOARGS,
     ""
    },
    {"memory_size", (PyCFunction)UnigramModel_memory_size, METH_NOARGS,
     ""
    },
    {NULL}  /* Sentinel */
};

static PyTypeObject UnigramModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.UnigramModel",             /*tp_name*/
    sizeof(PyUnigramModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)UnigramModel_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "UnigramModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    UnigramModel_methods,     /* tp_methods */
    UnigramModel_members,     /* tp_members */
    UnigramModel_getsetters,   /* tp_getset */
    &LanguageModelType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    (initproc)UnigramModel_init,      /* tp_init */
    0,                         /* tp_alloc */
    UnigramModel_new,                 /* tp_new */
};



//------------------------------------------------------------------------
// DynamicModel - python interface for DynamicModel
//------------------------------------------------------------------------

bool set_order(PyDynamicModel *self, int order)
{
    if (order < 2)
    {
        PyErr_SetString(PyExc_ValueError, "DynamicModel doesn't support orders less than 2");
        return false;
    }

    (*self)->set_order(order);

    return true;
}

static PyObject *
DynamicModel_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    PyDynamicModel *self;

    self = (PyDynamicModel *)type->tp_alloc(type, 0);
    if (self != NULL) {
        self = new(self) PyDynamicModel;   // placement new
    }
    return (PyObject *)self;
}

static int
DynamicModel_init(PyDynamicModel *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {(char*)"order", NULL};
    int order = 3;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|i", kwlist,
                                      &order))
        return -1;

    if (!set_order(self, order))
        return -1;


    return 0;
}

static void
DynamicModel_dealloc(PyDynamicModel* self)
{
    self->~PyDynamicModel();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyObject *
DynamicModel_count_ngram(PyDynamicModel* self, PyObject* args)
{
    PyObject* ngram = NULL;
    int increment = 1;
    int allow_new_words = true;

    if (! PyArg_ParseTuple(args, "O|II:count_ngram",
              &ngram, &increment, &allow_new_words))
        return NULL;

    vector<wchar_t*> words;
    if (!pyseqence_to_strings(ngram, words))
        return NULL;

    if (!(*self)->count_ngram(&words[0], words.size(),
                              increment, allow_new_words))
    {
        PyErr_SetString(PyExc_MemoryError, "out of memory");
        return NULL;
    }

    free_strings(words);

    Py_RETURN_NONE;
}

static PyObject *
DynamicModel_get_ngram_count(PyDynamicModel* self, PyObject* ngram)
{
    int n;
    wchar_t** words = pyseqence_to_strings(ngram, &n);
    if (!words)
        return NULL;

    int count = (*self)->get_ngram_count((const wchar_t**) words, n);
    PyObject* result = PyInt_FromLong(count);

    free_strings(words, n);

    return result;
}

static PyObject *
DynamicModel_memory_size(PyDynamicModel* self)
{
    vector<long> values;
    (*self)->get_memory_sizes(values);

    PyObject* result = PyTuple_New(values.size());
    if (!result)
    {
        PyErr_SetString(PyExc_MemoryError, "failed to allocate tuple");
        return NULL;
    }
    for (int i=0; i<(int)values.size(); i++)
        PyTuple_SetItem(result, i, PyInt_FromLong(values[i]));

    return result;
}

// returns an object implementing pythons iterator interface
static PyObject *
DynamicModel_iter_ngrams(PyDynamicModel *self)
{
    NGramIter* iter = PyObject_New(NGramIter, &NGramIterType);
    if (!iter)
        return NULL;
    iter = new(iter) NGramIter(self->o);   // placement new

    #ifndef NDEBUG
    printf("DynamicModel_iter_ngrams: NGramIter=%p, ob_refcnt=%d\n", iter, (int)((PyObject*)iter)->ob_refcnt);
    #endif

    return (PyObject*) iter;
}

static PyObject *
DynamicModel_get_order(PyDynamicModel *self, void *closure)
{
    return PyInt_FromLong((*self)->get_order());
}

static int
DynamicModel_set_order(PyDynamicModel *self, PyObject *value, void *closure)
{
    int order = (int) PyInt_AsLong(value);
    if (order == -1)
    {
        PyErr_SetString(PyExc_TypeError, "The value must be an integer");
        return -1;
    }

    if (!set_order(self, order))
        return -2;

    return 0;
}

static struct
{
    const wchar_t* short_short_name;
    const wchar_t* short_name;
    const wchar_t* name;
    Smoothing id;
} smoothing_table[] =
{
    {L"j", L"jm", L"jelinek-mercer", JELINEK_MERCER_I},
    {L"w", L"wb", L"witten-bell", WITTEN_BELL_I},
    {L"d", L"ad", L"abs-disc", ABS_DISC_I},
    {L"k", L"kn", L"kneser-ney", KNESER_NEY_I},
};

const wchar_t* smoothing_to_string(Smoothing smoothing)
{
    for (int i=0; i<ALEN(smoothing_table); i++)
        if(smoothing == smoothing_table[i].id)
            return smoothing_table[i].name;
    return NULL;
}

Smoothing pystring_to_smoothing(PyObject *value)
{
    Smoothing sm = SMOOTHING_NONE;
    if (value != NULL)
    {
        wchar_t* s = pyunicode_to_wstr(value);
        if (!s)
        {
            return SMOOTHING_NONE;
        }
        int i;
        for (i=0; i<ALEN(smoothing_table); i++)
        {
            if(wcscmp(smoothing_table[i].short_short_name, s) == 0)
            {
                sm = smoothing_table[i].id;
                break;
            }
            if(wcscmp(smoothing_table[i].short_name, s) == 0)
            {
                sm = smoothing_table[i].id;
                break;
            }
            if(wcscmp(smoothing_table[i].name, s) == 0)
            {
                sm = smoothing_table[i].id;
                break;
            }
        }
        PyMem_Free(s);

        if (i >= ALEN(smoothing_table))
        {
            PyErr_SetString(PyExc_ValueError, "invalid smoothing option");
            return SMOOTHING_NONE;
        }
    }
    return sm;
}

static PyObject *
DynamicModel_get_smoothing(PyDynamicModel *self, void *closure)
{
    const wchar_t* s = smoothing_to_string((*self)->get_smoothing());
    if (s)
        return PyUnicode_FromWideChar(s, wcslen(s));
    Py_RETURN_NONE;
}

static int
DynamicModel_set_smoothing(PyDynamicModel *self, PyObject *value, void *closure)
{
    Smoothing sm = pystring_to_smoothing(value);
    if (!sm)
        return -1;

    vector<Smoothing> smoothings = (*self)->get_smoothings();
    if (!count(smoothings.begin(), smoothings.end(), sm))
    {
        PyErr_SetString(PyExc_ValueError, "unsupported smoothing option, "
                                          "try a different model type");
        return -1;
    }

    (*self)->set_smoothing(sm);

    return 0;
}


static PyMemberDef DynamicModel_members[] = {
    {NULL}  /* Sentinel */
};

static PyGetSetDef DynamicModel_getsetters[] = {
    {(char*)"order",
     (getter)DynamicModel_get_order, (setter)DynamicModel_set_order,
     (char*)"order of the language model",
     NULL},
    {(char*)"smoothing",
     (getter)DynamicModel_get_smoothing, (setter)DynamicModel_set_smoothing,
     (char*)"ngram smoothing: 'witten-bell' (default) or 'kneser-ney'",
     NULL},
    {NULL}  /* Sentinel */
};

static PyMethodDef DynamicModel_methods[] = {
    {"count_ngram", (PyCFunction)DynamicModel_count_ngram, METH_VARARGS,
     ""
    },
    {"get_ngram_count", (PyCFunction)DynamicModel_get_ngram_count, METH_O,
     ""
    },
    {"iter_ngrams", (PyCFunction)DynamicModel_iter_ngrams, METH_NOARGS,
     ""
    },
    {"memory_size", (PyCFunction)DynamicModel_memory_size, METH_NOARGS,
     ""
    },
    {NULL}  /* Sentinel */
};

static PyTypeObject DynamicModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.DynamicModel",             /*tp_name*/
    sizeof(PyDynamicModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)DynamicModel_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "DynamicModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    DynamicModel_methods,     /* tp_methods */
    DynamicModel_members,     /* tp_members */
    DynamicModel_getsetters,   /* tp_getset */
    &LanguageModelType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    (initproc)DynamicModel_init,      /* tp_init */
    0,                         /* tp_alloc */
    DynamicModel_new,                 /* tp_new */
};



//------------------------------------------------------------------------
// DynamicModelKN - python interface for DynamicModelKN
//------------------------------------------------------------------------

static PyObject *
DynamicModelKN_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    PyDynamicModelKN *self;

    self = (PyDynamicModelKN*)type->tp_alloc(type, 0);
    if (self != NULL) {
        self = new(self) PyDynamicModelKN;   // placement new
    }
    return (PyObject *)self;
}

static void
DynamicModelKN_dealloc(PyDynamicModelKN* self)
{
    self->~PyDynamicModelKN();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyTypeObject DynamicModelKNType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.DynamicModelKN",             /*tp_name*/
    sizeof(PyDynamicModelKN),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)DynamicModelKN_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "DynamicModelKN objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    0,     /* tp_methods */
    0,     /* tp_members */
    0,   /* tp_getset */
    &DynamicModelType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,      /* tp_init */
    0,                         /* tp_alloc */
    DynamicModelKN_new,                 /* tp_new */
};


//------------------------------------------------------------------------
// CachedDynamicModel - python interface for CachedDynamicModel
//------------------------------------------------------------------------

static PyObject *
CachedDynamicModel_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    PyCachedDynamicModel *self;

    self = (PyCachedDynamicModel*)type->tp_alloc(type, 0);
    if (self != NULL) {
        self = new(self) PyCachedDynamicModel;   // placement new
    }
    return (PyObject *)self;
}

static void
CachedDynamicModel_dealloc(PyCachedDynamicModel* self)
{
    self->~PyCachedDynamicModel();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyObject *
CachedDynamicModel_get_recency_halflife(PyCachedDynamicModel *self, void *closure)
{
    return PyInt_FromLong((*self)->get_recency_halflife());
}

static int
CachedDynamicModel_set_recency_halflife(PyCachedDynamicModel *self, PyObject *value, void *closure)
{
    if (!PyInt_Check(value) &&
        !PyFloat_Check(value))
    {
        PyErr_SetString(PyExc_TypeError, "number expected");
        return -1;
    }

    long halflife = 0;
    PyObject* o = PyNumber_Int(value);
    if (o)
    {
        halflife = PyInt_AsLong(o);
        Py_DECREF(o);
    }

    if (halflife <= 0)
    {
        PyErr_SetString(PyExc_ValueError,
                         "The value must be greater than zero");
        return -1;
    }

    (*self)->set_recency_halflife(halflife);

    return 0;
}

static PyObject *
CachedDynamicModel_get_recency_lambdas(PyCachedDynamicModel *self, void *closure)
{
    vector<double> lambdas;
    (*self)->get_recency_lambdas(lambdas);

    PyObject* otuple = PyTuple_New(lambdas.size());
    for (int i = 0; i<(int)lambdas.size(); i++)
        PyTuple_SetItem(otuple, i, PyFloat_FromDouble(lambdas[i]));
    return otuple;
}

static int
CachedDynamicModel_set_recency_lambdas(PyCachedDynamicModel *self, PyObject *value, void *closure)
{
    vector<double> lambdas;
    if (!pyseqence_to_doubles(value, lambdas))
    {
        PyErr_SetString(PyExc_ValueError, "list of numbers expected");
        return false;
    }

    (*self)->set_recency_lambdas(lambdas);

    return 0;
}

static PyObject *
CachedDynamicModel_get_recency_ratio(PyCachedDynamicModel *self, void *closure)
{
    return PyFloat_FromDouble((*self)->get_recency_ratio());
}

static int
CachedDynamicModel_set_recency_ratio(PyCachedDynamicModel *self, PyObject *value, void *closure)
{
    double recency_ratio = PyFloat_AsDouble(value);
    if (recency_ratio < 0.0 || recency_ratio > 1.0)
    {
        PyErr_SetString(PyExc_ValueError, "value must be in the range [0..1]");
        return -1;
    }

    (*self)->set_recency_ratio(recency_ratio);

    return 0;
}

static PyObject *
CachedDynamicModel_get_recency_smoothing(PyCachedDynamicModel *self, void *closure)
{
    const wchar_t* s = smoothing_to_string((*self)->get_recency_smoothing());
    if (s)
        return PyUnicode_FromWideChar(s, wcslen(s));
    Py_RETURN_NONE;
}

static int
CachedDynamicModel_set_recency_smoothing(PyCachedDynamicModel *self, PyObject *value, void *closure)
{
    Smoothing sm = pystring_to_smoothing(value);
    if (!sm)
        return -1;

    vector<Smoothing> smoothings = (*self)->get_recency_smoothings();
    if (!count(smoothings.begin(), smoothings.end(), sm))
    {
        PyErr_SetString(PyExc_ValueError, "unsupported smoothing option, "
                                          "try a different model type");
        return -1;
    }

    (*self)->set_recency_smoothing(sm);

    return 0;
}

static PyGetSetDef CachedDynamicModel_getsetters[] = {
    {(char*)"recency_halflife",
     (getter)CachedDynamicModel_get_recency_halflife, (setter)CachedDynamicModel_set_recency_halflife,
     (char*)"halflife of exponential falloff in number of recently used words"
            " until w=0.5",
     NULL},
    {(char*)"recency_lambdas",
     (getter)CachedDynamicModel_get_recency_lambdas, (setter)CachedDynamicModel_set_recency_lambdas,
     (char*)"jelinec-mercer smoothing weights, one per order",
     NULL},
    {(char*)"recency_ratio",
     (getter)CachedDynamicModel_get_recency_ratio, (setter)CachedDynamicModel_set_recency_ratio,
     (char*)"ratio of recency-based to count-based probabilities",
     NULL},
    {(char*)"recency_smoothing",
     (getter)CachedDynamicModel_get_recency_smoothing, (setter)CachedDynamicModel_set_recency_smoothing,
     (char*)"ngram recency smoothing: "
            "'jelinec-mercer' (default) or 'witten-bell'",
     NULL},
    {NULL}  /* Sentinel */
};

static PyTypeObject CachedDynamicModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.CachedDynamicModel",             /*tp_name*/
    sizeof(PyCachedDynamicModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)CachedDynamicModel_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "CachedDynamicModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    0,     /* tp_methods */
    0,     /* tp_members */
    CachedDynamicModel_getsetters,   /* tp_getset */
    &DynamicModelKNType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,      /* tp_init */
    0,                         /* tp_alloc */
    CachedDynamicModel_new,                 /* tp_new */
};


//------------------------------------------------------------------------
// OverlayModel - python interface for OverlayModel
//------------------------------------------------------------------------

static void
OverlayModel_dealloc(PyOverlayModel* self)
{
    self->~PyOverlayModel();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyMethodDef OverlayModel_methods[] = {
    {NULL}  /* Sentinel */
};

static PyTypeObject OverlayModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.OverlayModel",             /*tp_name*/
    sizeof(PyOverlayModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)OverlayModel_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "OverlayModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    OverlayModel_methods,     /* tp_methods */
    0,     /* tp_members */
    0,   /* tp_getset */
    &LanguageModelType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,      /* tp_init */
    0,                         /* tp_alloc */
    0,                 /* tp_new */
};

//------------------------------------------------------------------------
// LinintModel - python interface for LinintModel
//------------------------------------------------------------------------

static void
LinintModel_dealloc(PyLinintModel* self)
{
    self->~PyLinintModel();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyMethodDef LinintModel_methods[] = {
    {NULL}  /* Sentinel */
};

static PyTypeObject LinintModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.LinintModel",             /*tp_name*/
    sizeof(PyLinintModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)LinintModel_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "LinintModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    LinintModel_methods,     /* tp_methods */
    0,     /* tp_members */
    0,   /* tp_getset */
    &LanguageModelType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,      /* tp_init */
    0,                         /* tp_alloc */
    0,                 /* tp_new */
};

//------------------------------------------------------------------------
// LoglinintModel - python interface for LoglinintModel
//------------------------------------------------------------------------

static void
LoglinintModel_dealloc(PyLoglinintModel* self)
{
    self->~PyLoglinintModel();   // call destructor
    Py_TYPE(self)->tp_free((PyObject*)self);
}

static PyMethodDef LoglinintModel_methods[] = {
    {NULL}  /* Sentinel */
};

static PyTypeObject LoglinintModelType = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "lm.LoglinintModel",             /*tp_name*/
    sizeof(PyLoglinintModel),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)LoglinintModel_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "LoglinintModel objects",           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    LoglinintModel_methods,     /* tp_methods */
    0,     /* tp_members */
    0,   /* tp_getset */
    &LanguageModelType,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,      /* tp_init */
    0,                         /* tp_alloc */
    0,                 /* tp_new */
};


//------------------------------------------------------------------------
// more python helper functions, depending on LanguageModelType
//------------------------------------------------------------------------

static bool
parse_params(const char* func_name, PyDynamicModel *self, PyObject* args,
             vector<PyLanguageModel*>& models)
{
    PyObject *omodels = NULL;
    string format = "O:" + string(func_name);
    if (PyArg_ParseTuple(args, format.c_str(), &omodels))
    {
        if (!pyseqence_to_objects(omodels, models, &LanguageModelType))
        {
            PyErr_SetString(PyExc_ValueError, "list of LanguageModels expected");
            return false;
        }
    }
    return true;
}

static bool
parse_params(const char* func_name, PyDynamicModel *self, PyObject* args,
             vector<PyLanguageModel*>& models, vector<double>& weights)
{
    PyObject *omodels = NULL;
    PyObject *oweights = NULL;
    string format = "O|O:" + string(func_name);
    if (PyArg_ParseTuple(args, format.c_str(), &omodels, &oweights))
    {
        if (!pyseqence_to_objects(omodels, models, &LanguageModelType))
        {
            PyErr_SetString(PyExc_ValueError, "list of LanguageModels expected");
            return false;
        }
        if (oweights)  // optional parameter
        {
            if (!pyseqence_to_doubles(oweights, weights))
            {
                PyErr_SetString(PyExc_ValueError, "list of numbers expected");
                return false;
            }
        }
    }
    return true;
}


//------------------------------------------------------------------------
// Module methods
//------------------------------------------------------------------------

static PyObject *
overlay(PyDynamicModel *self, PyObject* args)
{
    vector<PyLanguageModel*> models;
    if (!parse_params("overlay", self, args, models))
        return NULL;

    PyOverlayModel* model = PyObject_New(PyOverlayModel, &OverlayModelType);
    if (!model)
    {
        PyErr_SetString(PyExc_MemoryError, "failed to allocate PyOverlayModel");
        return NULL;
    }

    model = new(model) PyOverlayModel(models);   // placement new

    return (PyObject*) model;
}

static PyObject *
linint(PyDynamicModel *self, PyObject* args)
{
    vector<PyLanguageModel*> models;
    vector<double> weights;
    if (!parse_params("linint", self, args, models, weights))
        return NULL;

    PyLinintModel* model = PyObject_New(PyLinintModel, &LinintModelType);
    if (!model)
    {
        PyErr_SetString(PyExc_MemoryError, "failed to allocate PyLinintModel");
        return NULL;
    }

    model = new(model) PyLinintModel(models);   // placement new
    (*model)->set_weights(weights);

    return (PyObject*) model;
}

static PyObject *
loglinint(PyDynamicModel *self, PyObject* args)
{
    vector<PyLanguageModel*> models;
    vector<double> weights;
    if (!parse_params("loglinint", self, args,  models, weights))
        return NULL;

    PyLoglinintModel* model = PyObject_New(PyLoglinintModel,
                                           &LoglinintModelType);
    if (!model)
    {
        PyErr_SetString(PyExc_MemoryError,
                        "failed to allocate PyLoglinintModel");
        return NULL;
    }

    model = new(model) PyLoglinintModel(models);   // placement new
    (*model)->set_weights(weights);

    return (PyObject*) model;
}


static PyMethodDef module_methods[] = {
    {"overlay", (PyCFunction)overlay, METH_VARARGS,
     ""
    },
    {"linint", (PyCFunction)linint, METH_VARARGS,
     ""
    },
    {"loglinint", (PyCFunction)loglinint, METH_VARARGS,
     ""
    },
    {NULL}  /* Sentinel */
};

#if PY_MAJOR_VERSION >= 3
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        "lm",                 /* m_name */
        "Dynamically updatable n-gram language models.", /* m_doc */
        -1,                   /* m_size */
        module_methods,       /* m_methods */
        NULL,                 /* m_reload */
        NULL,                 /* m_traverse */
        NULL,                 /* m_clear */
        NULL,                 /* m_free */
    };
#endif

static PyObject *
moduleinit (void)
{
    PyObject* module;

    #if PY_MAJOR_VERSION >= 3
        module = PyModule_Create(&moduledef);
    #else
        module = Py_InitModule3("lm", module_methods,
                           "Dynamically updatable n-gram language models.");
    #endif

    //setlocale(LC_CTYPE, "");
    //setlocale(LC_CTYPE, "en_US.UTF-8");
    //utf8_mode = (strcmp(nl_langinfo(CODESET), "UTF-8") == 0);

    if (module)
    {
        // finalize type objects
        if (PyType_Ready(&NGramIterType) < 0)
            return NULL;
        if (PyType_Ready(&LanguageModelType) < 0)
            return NULL;
        if (PyType_Ready(&UnigramModelType) < 0)
            return NULL;
        if (PyType_Ready(&DynamicModelType) < 0)
            return NULL;
        if (PyType_Ready(&DynamicModelKNType) < 0)
            return NULL;
        if (PyType_Ready(&CachedDynamicModelType) < 0)
            return NULL;
        if (PyType_Ready(&OverlayModelType) < 0)
            return NULL;
        if (PyType_Ready(&LinintModelType) < 0)
            return NULL;
        if (PyType_Ready(&LoglinintModelType) < 0)
            return NULL;

        // add top level objects to be instantiated from python
        Py_INCREF(&LanguageModelType);
        PyModule_AddObject(module, "LanguageModel", (PyObject *)&LanguageModelType);
        Py_INCREF(&UnigramModelType);
        PyModule_AddObject(module, "UnigramModel", (PyObject *)&UnigramModelType);
        Py_INCREF(&DynamicModelType);
        PyModule_AddObject(module, "DynamicModel", (PyObject *)&DynamicModelType);
        Py_INCREF(&DynamicModelKNType);
        PyModule_AddObject(module, "DynamicModelKN", (PyObject *)&DynamicModelKNType);
        Py_INCREF(&CachedDynamicModelType);
        PyModule_AddObject(module, "CachedDynamicModel", (PyObject *)&CachedDynamicModelType);

        // add constants
        PyDict_SetItemString(LanguageModelType.tp_dict, "CASE_INSENSITIVE",
                             PyInt_FromLong(LanguageModel::CASE_INSENSITIVE));
        PyDict_SetItemString(LanguageModelType.tp_dict, "CASE_INSENSITIVE_SMART",
                             PyInt_FromLong(LanguageModel::CASE_INSENSITIVE_SMART));
        PyDict_SetItemString(LanguageModelType.tp_dict, "ACCENT_INSENSITIVE",
                             PyInt_FromLong(LanguageModel::ACCENT_INSENSITIVE));
        PyDict_SetItemString(LanguageModelType.tp_dict, "ACCENT_INSENSITIVE_SMART",
                             PyInt_FromLong(LanguageModel::ACCENT_INSENSITIVE_SMART));
        PyDict_SetItemString(LanguageModelType.tp_dict, "IGNORE_CAPITALIZED",
                             PyInt_FromLong(LanguageModel::IGNORE_CAPITALIZED));
        PyDict_SetItemString(LanguageModelType.tp_dict, "IGNORE_NON_CAPITALIZED",
                             PyInt_FromLong(LanguageModel::IGNORE_NON_CAPITALIZED));
        PyDict_SetItemString(LanguageModelType.tp_dict, "INCLUDE_CONTROL_WORDS",
                             PyInt_FromLong(LanguageModel::INCLUDE_CONTROL_WORDS));
        PyDict_SetItemString(LanguageModelType.tp_dict, "NORMALIZE",
                             PyInt_FromLong(LanguageModel::NORMALIZE));
        PyDict_SetItemString(LanguageModelType.tp_dict, "NO_SORT",
                             PyInt_FromLong(LanguageModel::NO_SORT));
        PyDict_SetItemString(LanguageModelType.tp_dict, "NUM_CONTROL_WORDS",
                             PyInt_FromLong(NUM_CONTROL_WORDS));
    }

    return module;
}

#if PY_MAJOR_VERSION >= 3
    PyMODINIT_FUNC
    PyInit_lm(void)
    {
        return moduleinit();
    }
#else
    PyMODINIT_FUNC
    initlm(void)
    {
        moduleinit();
    }
#endif
