/*
 * Copyright © 2009-2010, 2013-2014 marmuta <marmvta@gmail.com>
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

#include "lm_dynamic.h"

using namespace std;

//------------------------------------------------------------------------
// DynamicModelBase
//------------------------------------------------------------------------

// Load from ARPA-like format, expects counts instead of log probabilities
// and no back-off values. N-grams don't have to be sorted alphabetically.
// State machine driven version, still the fastest.
LMError DynamicModelBase::load_arpac(const char* filename)
{
    int i;
    int new_order = 0;
    int current_level = 0;
    int line_number = -1;
    std::vector<int> counts;
    LMError err_code = ERR_NONE;

    enum {BEGIN, COUNTS, NGRAMS_HEAD, NGRAMS, DONE}
    state = BEGIN;

    vector<Unigram> unigrams;

    clear();

    FILE* f = fopen(filename, "r,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s\n", filename);
        #endif
        return ERR_FILE;
    }

    while(1)
    {
        // read line
        wchar_t buf[4096];
        if (fgetws(buf, ALEN(buf), f) == NULL)
            break;
        line_number++;

        // chop line into tokens
        wchar_t *tstate;
        wchar_t* tokens[32] = {wcstok(buf, L" \n", &tstate)};
        for (i=0; tokens[i] && i < ALEN(tokens)-1; i++)
            tokens[i+1] = wcstok(NULL, L" \n", &tstate);
        int ntoks = i;

        if (ntoks)  // any tokens there?
        {
            // check for n-grams first, this is by far the most frequent case
            if (state == NGRAMS)
            {
                if (tokens[0][0] == L'\\')  // end of section?
                {
                    // add unigrams
                    if (current_level == 1)
                    {
                        err_code = set_unigrams(unigrams);
                        vector<Unigram>().swap(unigrams); // really free mem
                        if (err_code)
                            break;
                    }

                    // check count
                    int ngrams_expected = counts[current_level-1];
                    int ngrams_read = get_num_ngrams(current_level-1);
                    if (ngrams_read != ngrams_expected)
                    {
                        error (0, 0, "unexpected n-gram count for level %d: "
                                     "expected %d n-grams, but read %d",
                              current_level,
                              ngrams_expected, ngrams_read);
                        err_code = ERR_COUNT; // count doesn't match number of unique ngrams
                        break;
                    }
                    state = NGRAMS_HEAD;
                }
                else
                {
                    if (ntoks < current_level+1)
                    {
                        err_code = ERR_NUMTOKENS; // too few tokens for cur. level
                        error (0, 0, "too few tokens for n-gram level %d: "
                              "line %d, tokens found %d/%d",
                              current_level,
                              line_number, ntoks, current_level+1);
                        break;
                    }

                    int itok = 0;
                    int count = wcstol(tokens[itok++], NULL, 10);

                    uint32_t time = 0;
                    if (ntoks >= current_level+2)
                        time  = wcstol(tokens[itok++], NULL, 10);

                    // There is a slight possibility that old models have
                    // zero counts. Since rev. 1845 n-grams with zero count
                    // are considered removed, which causes load failures.
                    // -> ignore n-grams with count 0
                    if (count <= 0)
                    {
                        // Expect one n-gram fewer for this level.
                        counts[current_level-1]--;
                    }
                    else
                    {
                        if (current_level == 1)
                        {
                            // Temporarily collect unigrams so we can sort them.
                            Unigram unigram = {tokens[itok],
                                               (CountType)count,
                                               time};
                            unigrams.push_back(unigram);
                        }
                        else
                        {
                            BaseNode* node = count_ngram(tokens+itok,
                                                        current_level,
                                                        count);
                            if (!node)
                            {
                                err_code = ERR_MEMORY; // out of memory
                                break;
                            }
                            set_node_time(node, time);
                        }
                    }

                    continue;
                }
            }
            else
            if (state == BEGIN)
            {
                if (wcsncmp(tokens[0], L"\\data\\", 6) == 0)
                {
                    state = COUNTS;
                }
            }
            else
            if (state == COUNTS)
            {
                if (wcsncmp(tokens[0], L"ngram", 5) == 0 && ntoks >= 2)
                {
                    int level;
                    int count;
                    if (swscanf(tokens[1], L"%d=%d", &level, &count) == 2)
                    {
                        new_order = std::max(new_order, level);
                        counts.resize(new_order);
                        counts[level-1] = count;
                    }
                }
                else
                {
                    int max_order = get_max_order();
                    if (max_order && max_order < new_order)
                    {
                        err_code = ERR_ORDER_UNSUPPORTED;
                        break;
                    }

                    // clear language model and set it up for the new order
                    set_order(new_order);
                    if (new_order)
                    {
                        // This drops control words! They are added back
                        // with assure_valid_control_words() below.
                        reserve_unigrams(counts[0]);
                    }
                    state = NGRAMS_HEAD;
                }
            }

            if (state == NGRAMS_HEAD)
            {
                if (swscanf(tokens[0], L"\\%d-grams", &current_level) == 1)
                {
                    if (current_level < 1 || current_level > new_order)
                    {
                        err_code = ERR_ORDER_UNEXPECTED;
                        break;
                    }
                    state = NGRAMS;
                }
                else
                if (wcsncmp(tokens[0], L"\\end\\", 5) == 0)
                {
                    state = DONE;
                    break;
                }
            }
        }
    }

    // didn't make it until the end?
    if (state != DONE)
    {
        clear();
        if (!err_code)
            err_code = ERR_UNEXPECTED_EOF;  // unexpected end of file
    }

    // At this point, control words might possibly have been loaded with
    // zero counts. Make sure they exist with at least count 1.
    assure_valid_control_words();

    return err_code;
}


// Save to ARPA-like format, stores counts instead of log probabilities
// and no back-off values.
LMError DynamicModelBase::save_arpac(const char* filename)
{
    int i;

    FILE* f = fopen(filename, "w,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s", filename);
        #endif
        return ERR_FILE;
    }

    fwprintf(f, L"\n");
    fwprintf(f, L"\\data\\\n");

    for (i=0; i<order; i++)
        fwprintf(f, L"ngram %d=%d\n", i+1, get_num_ngrams(i));

    write_arpa_ngrams(f);

    fwprintf(f, L"\n");
    fwprintf(f, L"\\end\\\n");

    fclose(f);

    return ERR_NONE;
}

LMError DynamicModelBase::write_arpa_ngrams(FILE* f)
{
    int i;

    for (i=0; i<order; i++)
    {
        fwprintf(f, L"\n");
        fwprintf(f, L"\\%d-grams:\n", i+1);

        std::vector<WordId> wids;
        DynamicModelBase::ngrams_iter* it;
        for (it = ngrams_begin(); ; (*it)++)
        {
            BaseNode* node = *(*it);
            if (!node)
                break;

            if (it->get_level() == i+1)
            {
                it->get_ngram(wids);
                LMError error = write_arpa_ngram(f, node, wids);
                if (error)
                    return error;
            }
        }
    }

    return ERR_NONE;
}

// add unigrams in bulk
LMError DynamicModelBase::set_unigrams(const vector<Unigram>& unigrams)
{
    LMError error = ERR_NONE;

    // Add all words in bulk to the dictionary.
    // -> they are stored sorted and don't need
    // the sorted array -> saves memory.
    vector<wchar_t*> words;
    words.reserve(unigrams.size());
    vector<Unigram>::const_iterator it;
    for (it=unigrams.begin(); it != unigrams.end(); it++)
    {
        const Unigram& unigram = *it;
        words.push_back(const_cast<wchar_t*>(unigram.word.c_str()));
    }
    error = dictionary.set_words(words);

    if (!error)
    {
        // finally add all the unigrams
        for (it=unigrams.begin(); it < unigrams.end(); it++)
        {
            const Unigram& unigram = *it;
            const wchar_t* word = unigram.word.c_str();
            BaseNode* node = count_ngram(&word,
                                         1,
                                         unigram.count);
            if (!node)
            {
                error = ERR_MEMORY; // out of memory
                break;
            }
            set_node_time(node, unigram.time);
        }
    }
    return error;
}

