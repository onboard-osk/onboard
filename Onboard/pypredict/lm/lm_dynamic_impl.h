/*
 * Copyright © 2010, 2012-2014 marmuta <marmvta@gmail.com>
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

#include <stdio.h>
#include <assert.h>
#include <cstring>
#include <set>
#include <iostream>
#include <locale>

//------------------------------------------------------------------------
// NGramTrie - root node of the ngram trie
//------------------------------------------------------------------------

// Lookup node or create it if it doesn't exist
template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
BaseNode* NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>::
    add_node(const WordId* wids, int n)
{
    BaseNode* node = this;
    BaseNode* parent = NULL;
    TNODE* grand_parent = NULL;
    int parent_index = 0;
    int grand_parent_index = 0;

    for (int i=0; i<n; i++)
    {
        WordId wid = wids[i];
        grand_parent = static_cast<TNODE*>(parent);
        parent = node;
        grand_parent_index = parent_index;
        node = get_child(parent, i, wid, parent_index);
        if (!node)
        {
            if (i == order-1)
            {
                TBEFORELASTNODE* p = static_cast<TBEFORELASTNODE*>(parent);

                // check the available space for LastNodes
                int size = p->children.size();
                int old_capacity = p->children.capacity();
                if (size >= old_capacity)  // no space for another TLASTNODE?
                {
                    // grow the memory block of the parent node
                    int new_capacity = p->children.capacity(size + 1);
                    int old_bytes = sizeof(TBEFORELASTNODE) +
                               old_capacity*sizeof(TLASTNODE);
                    int new_bytes = sizeof(TBEFORELASTNODE) +
                               new_capacity*sizeof(TLASTNODE);
                    TBEFORELASTNODE* pnew = (TBEFORELASTNODE*) MemAlloc(new_bytes);
                    if (!pnew)
                        return NULL;

                    // copy the data over, no need for constructor calls
                    memcpy(pnew, p, old_bytes);

                    // replace grand_parent pointer
                    ASSERT(p == grand_parent->children[grand_parent_index]);
                    grand_parent->children[grand_parent_index] = pnew;
                    MemFree(p);
                    p = pnew;
                }

                // add the new child node
                node = p->add_child(wid);
            }
            else
            if (i == order-2)
            {
                int bytes = sizeof(TBEFORELASTNODE) +
                        inplace_vector<TLASTNODE>::capacity(0)*sizeof(TLASTNODE);
                TBEFORELASTNODE* nd = (TBEFORELASTNODE*)MemAlloc(bytes);
                //node = new TBEFORELASTNODE(wid);
                if (!nd)
                    return NULL;
                node = new(nd) TBEFORELASTNODE(wid);
                static_cast<TNODE*>(parent)->add_child(node);
            }
            else
            {
                TNODE* nd = (TNODE*)MemAlloc(sizeof(TNODE));
                if (!nd)
                    return NULL;
                node = new(nd) TNODE(wid);
                static_cast<TNODE*>(parent)->add_child(node);
            }

            // Create only a single node per call. For a valid model we
            // expect count_ngram() to be called extra for each node in every
            // path, in particular for all unigrams. Use learn_tokens() to
            // enforce this. Only then is the model ready for use with
            // predict().
            break;
        }
    }
    return node;
}

template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
void NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>::
    get_probs_witten_bell_i(const std::vector<WordId>& history,
                            const std::vector<WordId>& words,
                            std::vector<double>& vp,
                            int num_word_types)
{
    int i,j;
    int n = history.size() + 1;
    int size = words.size();   // number of candidate words
    std::vector<int32_t> vc(size);  // vector of counts, reused for order 1..n

    // order 0
    vp.resize(size);
    fill(vp.begin(), vp.end(), 1.0/num_word_types); // uniform distribution

    // order 1..n
    for(j=0; j<n; j++)
    {
        std::vector<WordId> h(history.begin()+(n-j-1), history.end()); // tmp history
        BaseNode* hnode = get_node(h);
        if (hnode)
        {
            int N1prx = get_N1prx(hnode, j);   // number of word types following the history
            if (!N1prx)  // break early, don't reset probabilities to 0
                break;   // for unknown histories

            // total number of occurences of the history
            int cs = sum_child_counts(hnode, j);
            if (cs)
            {
                // get ngram counts
                fill(vc.begin(), vc.end(), 0);
                int num_children = get_num_children(hnode, j);
                for(i=0; i<num_children; i++)
                {
                    BaseNode* child = get_child_at(hnode, j, i);
                    int index = binsearch(words, child->word_id); // word_indices have to be sorted by index
                    if (index >= 0)
                        vc[index] = child->get_count();
                }

                double l1 = N1prx / (N1prx + float(cs)); // normalization factor
                                                         // 1 - lambda
                for(i=0; i<size; i++)
                {
                    double pmle = vc[i] / float(cs);
                    vp[i] = (1.0 - l1) * pmle + l1 * vp[i];
                }
            }
        }
    }
}

// absolute discounting
template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
void NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>::
     get_probs_abs_disc_i(const std::vector<WordId>& history,
                          const std::vector<WordId>& words,
                          std::vector<double>& vp,
                          int num_word_types,
                          const std::vector<double>& Ds)
{
    int i,j;
    int n = history.size() + 1;
    int size = words.size();   // number of candidate words
    std::vector<int32_t> vc(size);  // vector of counts, reused for order 1..n

    // order 0
    vp.resize(size);
    fill(vp.begin(), vp.end(), 1.0/num_word_types); // uniform distribution

    // order 1..n
    for(j=0; j<n; j++)
    {
        std::vector<WordId> h(history.begin()+(n-j-1), history.end()); // tmp history
        BaseNode* hnode = get_node(h);
        if (hnode)
        {
            int N1prx = get_N1prx(hnode, j);   // number of word types following the history
            if (!N1prx)  // break early, don't reset probabilities to 0
                break;   // for unknown histories

            // total number of occurences of the history
            int cs = sum_child_counts(hnode, j);
            if (cs)
            {
                // get ngram counts
                fill(vc.begin(), vc.end(), 0);
                int num_children = get_num_children(hnode, j);
                for(i=0; i<num_children; i++)
                {
                    BaseNode* child = get_child_at(hnode, j, i);
                    int index = binsearch(words, child->word_id); // word_indices have to be sorted by index
                    if (index >= 0)
                        vc[index] = child->get_count();
                }

                double D = Ds[j];
                double l1 = D / float(cs) * N1prx; // normalization factor
                                                   // 1 - lambda
                for(i=0; i<size; i++)
                {
                    double a = vc[i] - D;
                    if (a < 0)
                        a = 0;
                    vp[i] = a / float(cs) + l1 * vp[i];
                }
            }
        }
    }
}

//------------------------------------------------------------------------
// DynamicModel - dynamically updatable language model
//------------------------------------------------------------------------

template <class TNGRAMS>
void _DynamicModel<TNGRAMS>::set_order(int n)
{
    if(n < 2)  // use UnigramModel for order 1
        n = 2;

    n1s = std::vector<int>(n, 0);
    n2s = std::vector<int>(n, 0);
    Ds  = std::vector<double>(n, 0);

    ngrams.set_order(n);
    NGramModel::set_order(n);  // calls clear()
}

template <class TNGRAMS>
void _DynamicModel<TNGRAMS>::clear()
{
    ngrams.clear();
    DynamicModelBase::clear();  // clears dictionary
}

// Add increment to the count of the given ngram.
// Unknown words will be added to the dictionary and
// unknown ngrams will cause new trie nodes to be created as needed.
template <class TNGRAMS>
BaseNode* _DynamicModel<TNGRAMS>::count_ngram(const wchar_t* const* ngram, int n,
                                      int increment, bool allow_new_words)
{
    std::vector<WordId> wids(n);
    if (dictionary.query_add_words(ngram, n, wids, allow_new_words))
        return count_ngram(wids.data(), n, increment);
    return NULL;
}

// Add increment to the count of the given ngram.
// Unknown words will be added to the dictionary first and
// unknown ngrams will cause new trie nodes to be created as needed.
template <class TNGRAMS>
BaseNode* _DynamicModel<TNGRAMS>::count_ngram(const WordId* wids, int n,
                                        int increment)
{
    int i;

    // get/add node for ngram
    BaseNode* node = ngrams.add_node(wids, n);
    if (!node)
        return NULL;

    // remove old state
    if (node->count == 1)
        n1s[n-1]--;
    if (node->count == 2)
        n2s[n-1]--;

    int count = increment_node_count(node, wids, n, increment);

    // add new state
    if (node->count == 1)
        n1s[n-1]++;
    if (node->count == 2)
        n2s[n-1]++;

    // estimate discounting parameters for absolute discounting, kneser-ney
    for (i = 0; i < order; i++)
    {
        double D;
        int n1 = n1s[i];
        int n2 = n2s[i];
        if (n1 == 0 || n2 == 0)
            D = 0.1;          // training corpus too small, take a guess
        else
            // deleted estimation, Ney, Essen, and Kneser 1994
            D = n1 / (n1 + 2.0*n2);
        ASSERT(0 <= D and D <= 1.0);
        //D = 0.1;
        Ds[i] = D;
    }

    return count >= 0 ? node : NULL;
}

// Return the number of occurences of the given ngram
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::get_ngram_count(const wchar_t* const* ngram, int n)
{
    BaseNode* node = get_ngram_node(ngram, n);
    return (node ? node->get_count() : 0);
}

// Calculate a vector of probabilities for the ngrams formed
// by history + word[i], for all i.
// input:  constant history and a vector of candidate words
// output: vector of probabilities, one value per candidate word
template <class TNGRAMS>
void _DynamicModel<TNGRAMS>::get_probs(const std::vector<WordId>& history,
                                       const std::vector<WordId>& words,
                                       std::vector<double>& probabilities)
{
    // pad/cut history so it's always of length order-1
    int n = std::min((int)history.size(), order-1);
    std::vector<WordId> h(order-1, UNKNOWN_WORD_ID);
    copy_backward(history.end()-n, history.end(), h.end());

    #ifndef NDEBUG
    for (int i=0; i<order; i++)
        printf("%d: n1=%8d n2=%8d D=%f\n", i, n1s[i], n2s[i], Ds[i]);
    #endif

    switch(smoothing)
    {
        case WITTEN_BELL_I:
            ngrams.get_probs_witten_bell_i(h, words, probabilities,
                                              get_num_word_types());
            break;

        case ABS_DISC_I:
            ngrams.get_probs_abs_disc_i(h, words, probabilities,
                                           get_num_word_types(), Ds);
            break;

         default:
            break;
    }
}

// Same functionality as, but slightly faster than
// DynamicModelBase::write_arpa_ngrams().
template <class TNGRAMS>
LMError _DynamicModel<TNGRAMS>::
write_arpa_ngrams(FILE* f)
{
    int i;

    for (i=0; i<order; i++)
    {
        fwprintf(f, L"\n");
        fwprintf(f, L"\\%d-grams:\n", i+1);

        std::vector<WordId> wids;
        for (typename TNGRAMS::iterator it = ngrams.begin(); *it; it++)
        {
            if (it.get_level() == i+1)
            {
                it.get_ngram(wids);
                LMError error = write_arpa_ngram(f, *it, wids);
                if (error)
                    return error;
            }
        }
    }

    return ERR_NONE;
}


