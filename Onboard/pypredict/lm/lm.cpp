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

#include <stdlib.h>
#include <stdio.h>
#ifdef __FreeBSD__
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

static inline void error(int status, int errnum, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    if (errnum)
        fprintf(stderr, ": %s", strerror(errnum));
    fprintf(stderr, "\n");
    if (status)
        exit(status);
}
#else
#include <error.h>
#endif
#include <algorithm>
#include <cmath>
#include <string>
#include <wctype.h>

#include "lm.h"
#include "accent_transform.h"

using namespace std;


StrConv::StrConv()
{
    cd_mb_wc = iconv_open ("WCHAR_T", "UTF-8");
    if (cd_mb_wc == (iconv_t) -1)
    {
        if (errno == EINVAL)
            error (0, 0, "conversion from UTF-8 to wchar_t not available");
        else
            perror ("iconv_open mb2wc");
    }
    cd_wc_mb = iconv_open ("UTF-8", "WCHAR_T");
    if (cd_wc_mb == (iconv_t) -1)
    {
        if (errno == EINVAL)
            error (0, 0, "conversion from wchar_t to UTF-8 not available");
        else
            perror ("iconv_open wc2mb");
    }
}

StrConv::~StrConv()
{
    if (cd_mb_wc != (iconv_t) -1)
        if (iconv_close (cd_mb_wc) != 0)
            perror ("iconv_close mb2wc");
    if (cd_wc_mb != (iconv_t) -1)
        if (iconv_close (cd_wc_mb) != 0)
            perror ("iconv_close wc2mb");
}


// Sort an index array according to values from the cmp array, descending.
// Shellsort in place: stable and fast for already sorted arrays.
template <class T, class TCMP>
void stable_argsort_desc(vector<T>& v, const vector<TCMP>& cmp)
{
    int i, j, gap;
    int n = v.size();
    T t;

    for (gap = n/2; gap > 0; gap >>= 1)
    {
        for (i = gap; i < n; i++)
        {
            for (j = i-gap; j >= 0; j -= gap)
            {
                if (!(cmp[v[j]] < cmp[v[j+gap]]))
                    break;

                // Swap p with q
                t = v[j+gap];
                v[j+gap] = v[j];
                v[j] = t;
            }
        }
    }
}

// Replacement for wcscmp with optional case-
// and/or accent-insensitive comparison.
class PrefixCmp
{
    public:
        PrefixCmp(const wchar_t* _prefix, uint32_t _options)
        {
            if (_prefix)
                prefix = _prefix;
            options = _options;

            if (options & LanguageModel::CASE_INSENSITIVE_SMART)
                ;
            else
            if (options & LanguageModel::CASE_INSENSITIVE)
                transform (prefix.begin(), prefix.end(), prefix.begin(),
                           op_lower);

            if (options & LanguageModel::ACCENT_INSENSITIVE_SMART)
                ;
            else
            if (options & LanguageModel::ACCENT_INSENSITIVE)
                transform (prefix.begin(), prefix.end(), prefix.begin(),
                           op_remove_accent);
        }

        int matches(const char* s)
        {
            const wchar_t* stmp = conv.mb2wc(s);
            if (stmp)
                return matches(stmp);
            return false;
        }

        bool matches(const wchar_t* s)
        {
            wint_t c1, c2;
            const wchar_t* p = prefix.c_str();
            size_t n = prefix.size();

            wint_t c = s[0];
            if (c)
            {
                if ((options & LanguageModel::IGNORE_CAPITALIZED) &&
                    iswupper(c))
                    return false;

                if ((options & LanguageModel::IGNORE_NON_CAPITALIZED) &&
                    !iswupper(c))
                    return false;
            }

            if (n == 0)
                return true;

            do
            {
                c1 = (wint_t) *s++;
                c2 = (wint_t) *p++;

                if (options & LanguageModel::CASE_INSENSITIVE_SMART)
                {
                    if (!iswupper(c2))
                        c1 = (wint_t) towlower(c1);
                }
                else
                if (options & LanguageModel::CASE_INSENSITIVE)
                {
                    c1 = (wint_t) towlower(c1);
                }

                if (options & LanguageModel::ACCENT_INSENSITIVE_SMART)
                {
                    if (!has_accent(c2))
                        c1 = (wint_t) op_remove_accent(c1);
                }
                else
                if (options & LanguageModel::ACCENT_INSENSITIVE)
                {
                    c1 = (wint_t) op_remove_accent(c1);
                }

                if (c1 == L'\0' || c1 != c2)
                    return false;
            } while (--n > 0);

            return c1 == c2;
        }

    private:
        static wint_t op_lower(wint_t c)
        {
            return towlower(c);
        }

        static wint_t op_remove_accent(wint_t c)
        {
            if (c > 0x7f)
            {
                wint_t i = lookup_transform(c, _accent_transform,
                                                ALEN(_accent_transform));
                if (i<ALEN(_accent_transform) &&
                    _accent_transform[i][0] == c)
                    return _accent_transform[i][1];
            }
            return c;
        }

        static wint_t has_accent(wint_t c)
        {
            return op_remove_accent(c) != c;
        }

        static int lookup_transform(wint_t c, wint_t table[][2], int len)
        {
            int lo = 0;
            int hi = len;
            while (lo < hi)
            {
                int mid = (lo+hi)>>1;
                if (table[mid][0] < c)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

    private:
        wstring prefix;
        uint32_t options;
        StrConv conv;
};


//------------------------------------------------------------------------
// Dictionary - holds the vocabulary of the language model
//------------------------------------------------------------------------

void Dictionary::clear()
{
    vector<char*>::iterator it;
    for (it=words.begin(); it < words.end(); it++)
        MemFree(*it);

    vector<char*>().swap(words);  // clear and really free the memory

    if (sorted)
    {
        delete sorted;
        sorted = NULL;
    }
    sorted_words_begin = 0;
}


struct cmp_str
{
    bool operator() (const char* w1, const char* w2)
    { return strcmp(w1, w2) < 0; }
};

// Set words in bulk.
// Allows us to sort "words" and ignore the "sorted" vector.
//
// Preconditions:
// - Control words and only those are expected to exist in
//   the dictionary already (vector "words").
// - If new_words contains control words, they are
//   located close to its begin.
LMError Dictionary::set_words(const vector<wchar_t*>& new_words)
{
    // This is the goal: keep "sorted" unallocated
    // (for large static system models).
    if (sorted)
    {
        delete sorted;
        sorted = NULL;
    }

    // encode as utf-8 and store in "words"
    int initial_size = words.size(); // number of initial control words
    int n = new_words.size();
    for (int i = 0; i<n; i++)
    {
        // encode word to utf-8, this is how it is stored in the dictionary
        const char* wtmp = conv.wc2mb(new_words[i]);
        if (!wtmp)
            return ERR_WC2MB;
        char* w = (char*)MemAlloc((strlen(wtmp) + 1) * sizeof(char));
        if (!w)
            return ERR_MEMORY;
        strcpy(w, wtmp);

        // is this a known control word?
        bool exists = false;
        if (i < 100) // control words have to come in as some
                     // of the first entries.
        {
            for (int j = 0; j<initial_size; j++)
            {
                if (strcmp(w, words[j]) == 0)
                {
                    exists = true;
                    break;
                }
            }
        }

        // add it, if it wasn't a known control word
        if (!exists)
            words.push_back(w);
    }

    // sort words, make sure to use the same comparison function
    // as Dictionary::search_index.
    cmp_str cmp;
    sort(words.begin()+initial_size, words.end(), cmp);

    sorted_words_begin = initial_size;

    return ERR_NONE;
}

// Lookup the given word and return its id, binary search
WordId Dictionary::word_to_id(const wchar_t* word)
{
    const char* w = conv.wc2mb(word);
    int index = search_index(w);
    if (index >= 0 && index < (int)words.size())
    {
        WordId wid = sorted ? (*sorted)[index] : index;
        if (strcmp(words[wid], w) == 0)
            return wid;
    }
    return WIDNONE;
}

vector<WordId> Dictionary::words_to_ids(const wchar_t** word, int n)
{
    vector<WordId> wids;
    for(int i=0; i<n; i++)
        wids.push_back(word_to_id(word[i]));
    return wids;
}

// return the word for the given id, fast index lookup
const wchar_t* Dictionary::id_to_word(WordId wid)
{
    if (0 <= wid && wid < (WordId)words.size())
    {
        const char* w = words[wid];
        const wchar_t* word = conv.mb2wc(w);
        return word;
    }
    return NULL;
}

// Add a word to the dictionary
WordId Dictionary::add_word(const wchar_t* word)
{
    const char* wtmp = conv.wc2mb(word);
    if (!wtmp)
        return -2;

    char* w = (char*)MemAlloc((strlen(wtmp) + 1) * sizeof(char));
    if (!w)
        return -1;
    strcpy(w, wtmp);

    WordId wid = (WordId)words.size();
    update_sorting(w, wid);

    words.push_back(w);

    return wid;
}

void Dictionary::update_sorting(const char* word, WordId wid)
{
    // first add_word() after set_words()?
    // -> create the sorted vector
    if (sorted == NULL)
    {
        int i;
        int size = words.size();
        sorted = new vector<WordId>;
        for (i = sorted_words_begin; i<size; i++)
            sorted->push_back(i);

        // Control words weren't sorted before, insert them sorted.
        // -> inefficient, but presumably there is few enough data
        //    to not matter.
        for (i = 0; i<sorted_words_begin; i++)
        {
            int index = binsearch_sorted(words[i]);
            sorted->insert(sorted->begin()+index, i);
        }
    }

    // Bottle neck here, this is rather inefficient.
    // Everything else just appends, this inserts.
    // Mitigated due to usage of set_words() for bulk data,
    // but eventually there should be a better performing
    // data structure (though this one is pretty memory efficient).
    int index = search_index(word);
    sorted->insert(sorted->begin()+index, wid);
}

// Find all word ids of words starting with prefix
void Dictionary::prefix_search(const wchar_t* prefix,
                               std::vector<WordId>* wids_in,  // may be NULL
                               std::vector<WordId>& wids_out,
                               uint32_t options)
{
    WordId min_wid = (options & LanguageModel::INCLUDE_CONTROL_WORDS) \
                     ? 0 : NUM_CONTROL_WORDS;

    // filter the given word ids only
    if (wids_in)
    {
        PrefixCmp cmp = PrefixCmp(prefix, options);
        std::vector<WordId>::const_iterator it;
        for(it = wids_in->begin(); it != wids_in->end(); it++)
        {
            WordId wid = *it;
            if (wid >= min_wid &&
                cmp.matches(words[wid]))
                wids_out.push_back(wid);
        }
    }
    else
    // exhaustive search through the dictionary
    {
        PrefixCmp cmp = PrefixCmp(prefix, options);
        int size = words.size();
        for (int i = min_wid; i<size; i++)
            if (cmp.matches(words[i]))
                wids_out.push_back(i);
    }
}

// lookup word
// return value: 0 = no match
//               1 = exact match
//              -n = number of partial matches (prefix search)
int Dictionary::lookup_word(const wchar_t* word)
{
    const char* w = conv.wc2mb(word);
    if (!w)
        return 0;

    // binary search for the first match
    // then linearly collect all subsequent matches
    int len = strlen(w);
    int size = words.size();
    int count = 0;

    int index = search_index(w);

    // try exact match first
    if (index >= 0 && index < (int)words.size())
    {
        WordId wid = sorted ? (*sorted)[index] : index;
        if (strcmp(words[wid], w) == 0)
            return 1;
    }

    // then count partial matches
    for (int i=index; i<size; i++)
    {
        WordId wid = sorted ? (*sorted)[index] : index;
        if (strncmp(words[wid], w, len) != 0)
            break;
        count++;
    }
    return -count;
}

// Estimate a lower bound for the memory usage of the dictionary.
// This includes overallocations by std::vector, but excludes memory
// used for heap management and possible heap fragmentation.
uint64_t Dictionary::get_memory_size()
{
    uint64_t sum = 0;

    uint64_t d = sizeof(Dictionary);
    sum += d;

    uint64_t w = 0;
    for (unsigned i=0; i<words.size(); i++)
        w += (strlen(words[i]) + 1);
    sum += w;

    uint64_t wc = sizeof(char*) * words.capacity();
    sum += wc;

    uint64_t sc = sorted ? sizeof(WordId) * sorted->capacity() : 0;
    sum += sc;

    #ifndef NDEBUG
    printf("dictionary object: %12ld Byte\n", d);
    printf("strings:           %12ld Byte (%u)\n", w, (unsigned)words.size());
    printf("words.capacity:    %12ld Byte (%u)\n", wc, (unsigned)words.capacity());
    printf("sorted.capacity:   %12ld Byte (%u)\n", sc, (unsigned)sorted.capacity());
    printf("Dictionary total:  %12ld Byte\n", sum);
    #endif

    return sum;
}


//------------------------------------------------------------------------
// LanguageModel - base class of all language models
//------------------------------------------------------------------------

// return a list of word ids to be considered during the prediction
void LanguageModel::get_candidates(const std::vector<WordId>& history,
                                   const wchar_t* prefix,
                                   std::vector<WordId>& candidates,
                                   uint32_t options)
{
    bool has_prefix = (prefix && wcslen(prefix));
    int history_size = history.size();
    bool only_predictions =
                  !has_prefix &&
                  history_size >= 1 &&
                  // turn it off when running some unit tests
                  !(options & LanguageModel::INCLUDE_CONTROL_WORDS);

    if (has_prefix ||
        only_predictions ||
        options & LanguageModel::FILTER_OPTIONS)
    {
        if (only_predictions)
        {
            // Return a list of word ids with existing predictions.
            // Reduces clutter predicted between words by ignoring
            // unigram-only matches.
            std::vector<WordId> wids_in;

            // Only words that have predecessors and
            // implicitely filter out words with removed unigrams
            get_words_with_predictions(history, wids_in);
            dictionary.prefix_search(NULL, &wids_in, candidates, options);
        }
        else
        {
            std::vector<WordId> wids;
            dictionary.prefix_search(prefix, NULL, wids, options);

            // Filter out words with removed unigrams.
            filter_candidates(wids, candidates);
        }

        // candidate word indices have to be sorted for binsearch in kneser-ney
        sort(candidates.begin(), candidates.end());
    }
    else
    {
        int min_wid = (options & INCLUDE_CONTROL_WORDS) ? 0 : NUM_CONTROL_WORDS;
        int size = dictionary.get_num_word_types();
        std::vector<WordId> wids;
        wids.reserve(size);
        for (int i=min_wid; i<size; i++)
        {
            wids.push_back(i);
        }

        // Filter out words with removed unigrams.
        filter_candidates(wids, candidates);
    }

}

void LanguageModel::predict(std::vector<LanguageModel::Result>& results,
                            const std::vector<wchar_t*>& context,
                            int limit, uint32_t options)
{
    int i;

    if (!context.size())
        return;

    // Must not crash in get_candidates().
    // When filling a model use learn_tokens() instead of count_ngram() to
    // ensure the model is valid.
    if (!is_model_valid())
        return;

    // split context into history and completion-prefix
    vector<wchar_t*> h;
    const wchar_t* prefix = split_context(context, h);
    vector<WordId> history = words_to_ids(h);

    // get candidate words, completion
    vector<WordId> wids;
    get_candidates(history, prefix, wids, options);

    // calculate probability vector
    vector<double> probabilities(wids.size());
    get_probs(history, wids, probabilities);

    // prepare results vector
    int result_size = wids.size();
    if (limit >= 0 && limit < result_size)
        result_size = limit;
    results.clear();
    results.reserve(result_size);

    if (!(options & NO_SORT)) // allow to skip sorting for calls from another model, i.e. linint
    {
        // sort by descending probabilities
        vector<int32_t> argsort(wids.size());
        for (i=0; i<(int)wids.size(); i++)
            argsort[i] = i;
        stable_argsort_desc(argsort, probabilities);

        // merge word ids and probabilities into the return array
        for (i=0; i<result_size; i++)
        {
            int index = argsort[i];
            const wchar_t* word = id_to_word(wids[index]);
            if (word)
            {
                Result result = {word, probabilities[index]};
                results.push_back(result);
            }
        }
    }
    else
    {
        // merge word ids and probabilities into the return array
        for (int i=0; i<result_size; i++)
        {
            const wchar_t* word = id_to_word(wids[i]);
            if (word)
            {
                Result result = {word, probabilities[i]};
                results.push_back(result);
            }
        }
    }
}

// Return the probability of a single n-gram.
// This is very inefficient, not optimized for speed at all, but it's
// basically only there for entropy testing and not involved in
// actual word prediction tasks.
double LanguageModel::get_probability(const wchar_t* const* ngram, int n)
{
#if 1
    if (n)
    {
        // clear the last word of the context
        vector<wchar_t*> ctx((wchar_t**)ngram, (wchar_t**)ngram+n-1);
        const wchar_t* word = ngram[n-1];
        ctx.push_back((wchar_t*)L"");

        // run an unlimited prediction to get normalization right for
        // overlay and loglinint
        vector<Result> results;
        predict(results, ctx, -1, NORMALIZE);

        double psum = 0;
        for (int i=0; i<(int)results.size(); i++)
            psum += results[i].p;
        if (fabs(1.0 - psum) > 1e5)
            printf("%f\n", psum);

        for (int i=0; i<(int)results.size(); i++)
            if (results[i].word == word)
                return results[i].p;
        for (int i=0; i<(int)results.size(); i++)
            if (results[i].word == L"<unk>")
                return results[i].p;
    }
    return 0.0;
#else
    // split ngram into history and last word
    const wchar_t* word = ngram[n-1];
    vector<WordId> history;
    for (int i=0; i<n-1; i++)
        history.push_back(word_to_id(ngram[i]));

    // build candidate word vector
    vector<WordId> wids(1, word_to_id(word));

    // calculate probability
    vector<double> vp(1);
    get_probs(history, wids, vp);

    return vp[0];
#endif
}

// split context into history and prefix
const wchar_t* LanguageModel::split_context(const vector<wchar_t*>& context,
                                                  vector<wchar_t*>& history)
{
    int n = context.size();
    wchar_t* prefix = context[n-1];
    for (int i=0; i<n-1; i++)
        history.push_back(context[i]);
    return prefix;
}

LMError LanguageModel::read_utf8(const char* filename, wchar_t*& text)
{
    text = NULL;

    FILE* f = fopen(filename, "r,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s\n", filename);
        #endif
        return ERR_FILE;
    }

    int size = 0;
    const size_t bufsize = 1024*1024;
    wchar_t* buf = new wchar_t[bufsize];
    if (!buf)
        return ERR_MEMORY;

    while(1)
    {
        if (fgetws(buf, bufsize, f) == NULL)
            break;
        int l = wcslen(buf);
        text = (wchar_t*) realloc(text, (size + l + 1) * sizeof(*text));
        wcscpy (text + size, buf);
        size += l;
    }

    delete [] buf;

    return ERR_NONE;
}


//------------------------------------------------------------------------
// NGramModel - base class of n-gram language models, may go away
//------------------------------------------------------------------------

#ifndef NDEBUG
void NGramModel::print_ngram(const std::vector<WordId>& wids)
{
    for (int i=0; i<(int)wids.size(); i++)
    {
        printf("%ls(%d)", id_to_word(wids[i]), wids[i]);
        if (i<(int)wids.size())
            printf(" ");
    }
    printf("\n");
}
#endif



