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

#ifndef LM_DYNAMIC_H
#define LM_DYNAMIC_H

#include <math.h>
#include <assert.h>
#include <cstring>   // memcpy
#include <string>

#include "lm.h"

#define HONOR_REMOVED_NODES true


#pragma pack(2)

//------------------------------------------------------------------------
// inplace_vector - expects its elements in anonymous memory right after itself
//------------------------------------------------------------------------

template <class T>
class inplace_vector
{
    public:
        inplace_vector()
        {
            num_items = 0;
        }

        int capacity()
        {
            return capacity(num_items);
        }

        static int capacity(int n)
        {
            if (n == 0)
                n = 1;

            // growth factor, lower for slower growth and less wasted memory
            // g=2.0: quadratic growth, double capacity per step
            // [int(1.25**math.ceil(math.log(x)/math.log(1.25))) for x in range (1,100)]
            double g = 1.25;
            return (int) pow(g,ceil(log(n)/log(g)));
        }

        int size()
        {
            return num_items;
        }

        T* buffer()
        {
            return (T*) (((uint8_t*)(this) + sizeof(inplace_vector<T>)));
        }

        T& operator [](int index)
        {
            ASSERT(index >= 0 && index <= capacity());
            return buffer()[index];
        }

        T& back()
        {
            ASSERT(size() > 0);
            return buffer()[size()-1];
        }

        void push_back(T& item)
        {
            buffer()[size()] = item;
            num_items++;
            ASSERT(size() <= capacity());
        }

        void insert(int index, T& item)
        {
            T* p = buffer();
            for (int i=size()-1; i>=index; --i)
                p[i+1] = p[i];
            p[index] = item;
            num_items++;
            ASSERT(size() <= capacity());
        }

    public:
        InplaceSize num_items;
};


//------------------------------------------------------------------------
// BaseNode - base class of all trie nodes
//------------------------------------------------------------------------

class BaseNode
{
    public:
        BaseNode(WordId wid = -1)
        {
            word_id = wid;
            count = 0;
        }

        void clear()
        {
            count = 0;
        }

        const int get_count() const
        {
            return count;
        }

        void set_count(int c)
        {
            count = c;
        }


    public:
        WordId word_id;
        CountType count;
};

//------------------------------------------------------------------------
// LastNode - leaf node of the ngram trie, trigram for order 3
//------------------------------------------------------------------------
template <class TBASE>
class LastNode : public TBASE
{
    public:
        LastNode(WordId wid = (WordId)-1)
        : TBASE(wid)
        {
        }
};

//------------------------------------------------------------------------
// BeforeLastNode - second to last node of the ngram trie, bigram for order 3
//------------------------------------------------------------------------
template <class TBASE, class TLASTNODE>
class BeforeLastNode : public TBASE
{
    public:
        BeforeLastNode(WordId wid = (WordId)-1)
        : TBASE(wid)
        {
        }

        TLASTNODE* add_child(WordId wid)
        {
            TLASTNODE node(wid);
            if (children.size())
            {
                int index = search_index(wid);
                children.insert(index, node);
                //printf("insert: index=%d wid=%d\n",index, wid);
                return &children[index];
            }
            else
            {
                children.push_back(node);
                //printf("push_back: size=%d wid=%d\n",(int)children.size(), wid);
                return &children.back();
            }
        }

        BaseNode* get_child(WordId wid)
        {
            if (children.size())
            {
                int index = search_index(wid);
                if (index < (int)children.size())
                    if (children[index].word_id == wid)
                        return &children[index];
            }
            return NULL;
        }

        BaseNode* get_child_at(int index)
        {
            return &children[index];
        }

        int search_index(WordId wid)
        {
            int lo = 0;
            int hi = children.size();
            while (lo < hi)
            {
                int mid = (lo+hi)>>1;
                if (children[mid].word_id < wid)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

        int get_N1prx()
        {
            // Take removed nodes into account (count==0)
            int n = 0;
            if (HONOR_REMOVED_NODES)  // any removed nodes in the model?
            {
                for (int i=0; i<children.size(); i++)
                    if (children[i].get_count() > 0)
                        n++;
            }
            else
            {
                n = children.size();  // assumes all have counts>=1
            }

            return n;
        }

        int sum_child_counts()
        {
            int sum = 0;
            for (int i=0; i<children.size(); i++)
                sum += children[i].get_count();
            return sum;
        }
    public:
        inplace_vector<TLASTNODE> children;  // has to be last
};

//------------------------------------------------------------------------
// TrieNode - node for all lower levels of the ngram trie, unigrams for order 3
//------------------------------------------------------------------------
template <class TBASE>
class TrieNode : public TBASE
{
    public:
        TrieNode(WordId wid = (WordId)-1)
        : TBASE(wid)
        {
        }

        void add_child(BaseNode* node)
        {
            if (children.size())
            {
                int index = search_index(node->word_id);
                children.insert(children.begin()+index, node);
                //printf("insert: index=%d wid=%d\n",index, wid);
            }
            else
            {
                children.push_back(node);
                //printf("push_back: size=%d wid=%d\n",(int)children.size(), wid);
            }
        }

        BaseNode* get_child(WordId wid, int& index)
        {
            if (children.size())
            {
                index = search_index(wid);
                if (index < (int)children.size())
                    if (children[index]->word_id == wid)
                        return children[index];
            }
            return NULL;
        }

        BaseNode* get_child_at(int index)
        {
            return children[index];
        }

        int search_index(WordId wid)
        {
            // binary search like lower_bound()
            int lo = 0;
            int hi = children.size();
            while (lo < hi)
            {
                int mid = (lo+hi)>>1;
                if (children[mid]->word_id < wid)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

        int get_N1prx()
        {
            int n = 0;
            if (HONOR_REMOVED_NODES)  // any removed nodes in the model?
            {
                for (int i=0; i<(int)children.size(); i++)
                    if (children[i]->get_count() > 0)
                        n++;
            }
            else
            {
                n = children.size();  // assumes all children have counts > 0

                // Unigrams <unk>, <s>,... may be empty initially. Don't count them
                // or predictions for small models won't sum close to 1.0
                for (int i=0; i<n && i<NUM_CONTROL_WORDS; i++)
                    if (children[0]->get_count() == 0)
                        n--;
            }
            return n;
        }

        int sum_child_counts()
        {
            int sum = 0;
            std::vector<BaseNode*>::iterator it;
            for (it=children.begin(); it!=children.end(); it++)
                sum += (*it)->get_count();
            return sum;
        }
    public:
        std::vector<BaseNode*> children;
};

//------------------------------------------------------------------------
// NGramTrie - root node of the ngram trie
//------------------------------------------------------------------------
template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
class NGramTrie : public TNODE
{
    public:
        class iterator
        {
            public:
                iterator()
                {
                    root = NULL;
                }
                iterator(NGramTrie* root)
                {
                    this->root = root;
                    nodes.push_back(root);
                    indexes.push_back(0);
                    operator++(0);
                }

                BaseNode* operator*() const // dereference operator
                {
                    if (nodes.empty())
                        return NULL;
                    else
                        return nodes.back();
                }

                void operator++(int unused) // postfix operator
                {
                    BaseNode* node;
                    for(;;)
                    {
                        node = next();
                        // skip removed nodes, i.e. nodes with count==0
                        if (node == NULL || node->count != 0)
                            break;
                    }
                }

                // next for all nodes, including deleted ones with count==0
                BaseNode* next()
                {
                    // preorder traversal with shallow stack
                    // nodes stack: path to node
                    // indexes stack: index of _next_ child
                    BaseNode* node = nodes.back();
                    int index = indexes.back();

                    int level = get_level();
                    while (index >= root->get_num_children(node, level))
                    {
                        nodes.pop_back();
                        indexes.pop_back();
                        if (nodes.empty())
                            return NULL;

                        node = nodes.back();
                        index = ++indexes.back();
                        level = nodes.size()-1;
                        //printf ("back %d %d\n", node->word_id, index);
                    }
                    node = root->get_child_at(node, level, index);
                    nodes.push_back(node);
                    indexes.push_back(0);
                    //printf ("pushed %d %d %d\n", nodes.back()->word_id, index, indexes.back());
                    return node;
                }

                void get_ngram(std::vector<WordId>& ngram)
                {
                    ngram.resize(nodes.size()-1);
                    for(int i=1; i<(int)nodes.size(); i++)
                        ngram[i-1] = nodes[i]->word_id;
                }

                int get_level()
                {
                    return nodes.size()-1;
                }

                int at_root()
                {
                    return get_level() == 0;
                }

            private:
                NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>* root;
                std::vector<BaseNode*> nodes;   // path to node
                std::vector<int> indexes;       // index of _next_ child
        };

        NGramTrie::iterator begin()
        {
            return NGramTrie::iterator(this);
        }


    public:
        NGramTrie(WordId wid = (WordId)-1)
        : TNODE(wid)
        {
            order = 0;
        }

        void set_order(int order)
        {
            this->order = order;
            clear();
        }

        void clear()
        {
            clear(this, 0);
            num_ngrams   = std::vector<int>(order, 0);
            total_ngrams = std::vector<int>(order, 0);
            TNODE::clear();
        }

        // Add increment to node->count
        int increment_node_count(BaseNode* node, const WordId* wids, int n,
                                 int increment)
        {
            total_ngrams[n-1] += increment;

            // Adding n-gram?
            if (node->count == 0 && increment > 0)
                num_ngrams[n-1]++;

            node->count += increment;

            // Removing n-gram?
            if (node->count <= 0 && increment < 0)
            {
                num_ngrams[n-1]--;

                // Control words must not be removed.
                if (n == 1 && wids[0] < NUM_CONTROL_WORDS)
                {
                    node->count = 1;
                }
            }
            return node->count;
        }

        BaseNode* add_node(const std::vector<WordId>& wids)
        {return add_node(wids.data(), wids.size());}
        BaseNode* add_node(const WordId* wids, int n);

        void get_probs_witten_bell_i(const std::vector<WordId>& history,
                                     const std::vector<WordId>& words,
                                     std::vector<double>& vp,
                                     int num_word_types);

        void get_probs_abs_disc_i(const std::vector<WordId>& history,
                                  const std::vector<WordId>& words,
                                  std::vector<double>& vp,
                                  int num_word_types,
                                  const std::vector<double>& Ds);

        // Get number of unique ngrams per level, excluding removed ones
        // with count==0.
        int get_num_ngrams(int level) { return num_ngrams[level]; }

        // Get total number of all ngram occurences per level.
        int get_total_ngrams(int level) { return total_ngrams[level]; }

        // Number of distinct words excluding removed ones with count=0.
        virtual int get_num_word_types() {return get_num_ngrams(0);}

        // Get number of occurences of a specific ngram.
        int get_ngram_count(const std::vector<WordId>& wids)
        {
            BaseNode* node = get_node(wids);
            if (node)
                return node->get_count();
            return 0;
        }

        BaseNode* get_node(const std::vector<WordId>& wids)
        {
            BaseNode* node = this;
            for (int i=0; i<(int)wids.size(); i++)
            {
                int index;
                node = get_child(node, i, wids[i], index);
                if (!node)
                    break;
            }
            return node;
        }

        int get_num_children(BaseNode* node, int level)
        {
            if (level == order)
                return 0;
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(node)->children.size();
            return static_cast<TNODE*>(node)->children.size();
        }

        int sum_child_counts(BaseNode* node, int level)
        {
            if (level == order)
                return -1;  // undefined for leaf nodes
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(node)->sum_child_counts();
            return static_cast<TNODE*>(node)->sum_child_counts();
        }

        BaseNode* get_child_at(BaseNode* parent, int level, int index)
        {
            if (level == order)
                return NULL;
            if (level == order - 1)
                return &static_cast<TBEFORELASTNODE*>(parent)->children[index];
            return static_cast<TNODE*>(parent)->children[index];
        }

        // Return the word ids of all direct child nodes,
        // excluding removed n-grams, i.g. count == 0.
        void get_child_wordids(const std::vector<WordId>& wids,
                          std::vector<WordId>& child_wids)
        {
            int level = wids.size();
            BaseNode* node = get_node(wids);
            if (node)
            {
                int num_children = get_num_children(node, level);
                for(int i=0; i<num_children; i++)
                {
                    BaseNode* child = get_child_at(node, level, i);
                    if (child->count)
                        child_wids.push_back(child->word_id);
                }
            }
        }

        int get_N1prx(BaseNode* node, int level)
        {
            if (level == order)
                return 0;
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(node)->get_N1prx();
            return static_cast<TNODE*>(node)->get_N1prx();
        }

        // -------------------------------------------------------------------
        // implementation specific
        // -------------------------------------------------------------------

        // reserve an exact number of items to avoid unessarily
        // overallocated memory when loading language models
        void reserve_unigrams(int count)
        {
            clear();
            TNODE::children.reserve(count);
        }


        // Estimate a lower bound for the memory usage of the whole trie.
        // This includes overallocations by std::vector, but excludes memory
        // used for heap management and possible heap fragmentation.
        uint64_t get_memory_size()
        {
            NGramTrie::iterator it = begin();
            uint64_t sum = 0;
            for (; *it; it++)
                sum += get_node_memory_size(*it, it.get_level());
            return sum;
        }


    protected:
        void clear(BaseNode* node, int level)
        {
            if (level < order-1)
            {
                TNODE* tn = static_cast<TNODE*>(node);
                std::vector<BaseNode*>::iterator it;
                for (it=tn->children.begin(); it<tn->children.end(); it++)
                {
                    clear(*it, level+1);
                    if (level < order-2)
                        static_cast<TNODE*>(*it)->~TNODE();
                    else
                    if (level < order-1)
                        static_cast<TBEFORELASTNODE*>(*it)->~TBEFORELASTNODE();
                    MemFree(*it);

                }
                std::vector<BaseNode*>().swap(tn->children);  // really free the memory
            }
            TNODE::set_count(0);
        }


        BaseNode* get_child(BaseNode* parent, int level, int wid, int& index)
        {
            if (level == order)
                return NULL;
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(parent)->get_child(wid);
            return static_cast<TNODE*>(parent)->get_child(wid, index);
        }

        int get_node_memory_size(BaseNode* node, int level)
        {
            if (level == order)
                return sizeof(TLASTNODE);
            if (level == order - 1)
            {
                TBEFORELASTNODE* nd = static_cast<TBEFORELASTNODE*>(node);
                return sizeof(TBEFORELASTNODE) +
                       sizeof(TLASTNODE) *
                       (nd->children.capacity() - nd->children.size());
            }

            TNODE* nd = static_cast<TNODE*>(node);
            return sizeof(TNODE) +
                   sizeof(TNODE*) * nd->children.capacity();
        }


    public:
        int order;

        // Keep track of these counts to avoid
        // traversing the tree for these numbers.
        //
        // Number of unique ngrams with count > 0, per level.
        std::vector<int> num_ngrams;

        // Number of total occurences of all n-grams, per level.
        std::vector<int> total_ngrams;
};

#pragma pack()


enum Smoothing
{
    SMOOTHING_NONE,
    JELINEK_MERCER_I,    // jelinek-mercer interpolated
    WITTEN_BELL_I,       // witten-bell interpolated
    ABS_DISC_I,          // absolute discounting interpolated
    KNESER_NEY_I,        // kneser-ney interpolated
};

//------------------------------------------------------------------------
// DynamicModelBase - non-template abstract base class of all DynamicModels
//------------------------------------------------------------------------
class DynamicModelBase : public NGramModel
{
    public:
        // iterator for template-free, polymorphy based ngram traversel
        class ngrams_iter
        {
            public:
                virtual ~ngrams_iter() {}
                virtual BaseNode* operator*() const = 0;
                virtual void operator++(int unused) = 0;
                virtual void get_ngram(std::vector<WordId>& ngram) = 0;
                virtual int get_level() = 0;
                virtual bool at_root() = 0;
        };
        virtual DynamicModelBase::ngrams_iter* ngrams_begin() = 0;

        virtual void clear()
        {
            LanguageModel::clear();
            assure_valid_control_words();
        }

        // Make sure control words exist as unigrams.
        // They must have a count of at least 1. 0 means removed and
        // it also throws off the normalization of witten-bell smoothing.
        virtual void assure_valid_control_words()
        {
            const wchar_t* words[NUM_CONTROL_WORDS] =
                        {L"<unk>", L"<s>", L"</s>", L"<num>"};

            for (WordId i=0; i<ALEN(words); i++)
            {
                // Control words must have fixed positions at
                // the very beginning of the dictionary.
                assert(dictionary.word_to_id(words[i]) == i);

                if (get_ngram_count(words+i, 1) <= 0)
                    count_ngram(words+i, 1, 1);

            }
        }

        virtual int  get_ngram_count(const wchar_t* const* ngram, int n) = 0;
        virtual void get_node_values(BaseNode* node, int level,
                                     std::vector<int>& values) = 0;
        virtual BaseNode* count_ngram(const wchar_t* const* ngram, int n,
                                int increment=1, bool allow_new_words=true) = 0;
        virtual BaseNode* count_ngram(const WordId* wids,
                                      int n, int increment) = 0;

        virtual LMError load(const char* filename)
        {return load_arpac(filename);}
        virtual LMError save(const char* filename)
        {return save_arpac(filename);}

        // Debug output, dump all n-grams.
        virtual void dump()
        {
            std::vector<WordId> wids;
            DynamicModelBase::ngrams_iter* it;
            for (it = ngrams_begin(); ; (*it)++)
            {
                BaseNode* node = *(*it);
                if (!node)
                    break;

                it->get_ngram(wids);

                std::vector<int> values;
                get_node_values(node, wids.size(), values);

                unsigned i;
                for (i=0; i<wids.size(); i++)
                    printf("%ls ", dictionary.id_to_word(wids[i]));
                for (i=0; i<values.size(); i++)
                    printf("%d ", values[i]);
                printf("\n");
            }
            printf("\n");
        }

    protected:
        // temporary unigram, only used during loading
        typedef struct
        {
            std::wstring word;
            uint32_t count;
            uint32_t time;
        } Unigram;
        virtual LMError set_unigrams(const std::vector<Unigram>& unigrams);

        virtual LMError write_arpa_ngram(FILE* f,
                                       const BaseNode* node,
                                       const std::vector<WordId>& wids)
        {
            fwprintf(f, L"%d", node->get_count());

            std::vector<WordId>::const_iterator it;
            for(it = wids.begin(); it != wids.end(); it++)
                fwprintf(f, L" %ls", id_to_word(*it));

            fwprintf(f, L"\n");

            return ERR_NONE;
        }
        virtual LMError write_arpa_ngrams(FILE* f);

        virtual LMError load_arpac(const char* filename);
        virtual LMError save_arpac(const char* filename);

        virtual void set_node_time(BaseNode* node, uint32_t time)
        {}
        virtual int get_num_ngrams(int level) = 0;
        virtual void reserve_unigrams(int count) = 0;

        // Number of distinct words excluding removed ones with count=0.
        virtual int get_num_word_types() {return get_num_ngrams(0);}

};


//------------------------------------------------------------------------
// DynamicModel - dynamically updatable language model
//------------------------------------------------------------------------
template <class TNGRAMS>
class _DynamicModel : public DynamicModelBase
{
    public:
        static const Smoothing DEFAULT_SMOOTHING = ABS_DISC_I;

        class ngrams_iter : public DynamicModelBase::ngrams_iter
        {
            public:
                ngrams_iter(_DynamicModel<TNGRAMS>* lm)
                : it(&lm->ngrams)
                {}

                virtual BaseNode* operator*() const // dereference operator
                { return *it; }

                virtual void operator++(int unused) // postfix operator
                { it++; }

                virtual void get_ngram(std::vector<WordId>& ngram)
                { it.get_ngram(ngram); }

                virtual int get_level()
                { return it.get_level(); }

                virtual bool at_root()
                { return it.at_root(); }

            public:
                typename TNGRAMS::iterator it;
        };
        virtual DynamicModelBase::ngrams_iter* ngrams_begin()
        {return new ngrams_iter(this);}

    public:
        _DynamicModel()
        {
            smoothing = DEFAULT_SMOOTHING;
            set_order(3);
        }

        virtual ~_DynamicModel()
        {
            #ifndef NDEBUG
            uint64_t v = dictionary.get_memory_size();
            uint64_t n = ngrams.get_memory_size();
            printf("memory: dictionary=%ld, ngrams=%ld, total=%ld\n", v, n, v+n);
            #endif

            clear();
        }

        virtual void clear();
        virtual void set_order(int order);
        virtual Smoothing get_smoothing() {return smoothing;}
        virtual void set_smoothing(Smoothing s) {smoothing = s;}

        virtual std::vector<Smoothing> get_smoothings()
        {
            std::vector<Smoothing> smoothings;
            smoothings.push_back(WITTEN_BELL_I);
            smoothings.push_back(ABS_DISC_I);
            return smoothings;
        }

        virtual void filter_candidates(const std::vector<WordId>& in,
                                             std::vector<WordId>& out)
        {
            // filter out removed unigrams
            int num_candidates = in.size();
            out.reserve(num_candidates);
            for (int i=0; i<num_candidates; i++)
            {
                WordId wid = in[i];
                // can crash if is_model_valid() == false
                BaseNode* node = ngrams.get_child_at(&ngrams, 0, wid);
                if (node->get_count())
                    out.push_back(wid);
            }
        }

        // Plausibilty check befor predict() calls can be performed.
        //
        // When count_ngram() is called manually, it isn't guaranteed
        // that all nodes are created that are required for a valid model.
        // Unigrams in particular may be missing. This can lead to crashes
        // in filter_candidates or probability calculations.
        //
        // For filling models it's best to avoid count_ngram() and
        // use the safer learn_tokens() instead.
        virtual bool is_model_valid()
        {
            // including removed unigrams with count==0
            int num_unigrams = ngrams.get_num_children(&ngrams, 0);
            return num_unigrams == dictionary.get_num_word_types();
        }

        virtual BaseNode* count_ngram(const wchar_t* const* ngram, int n,
                                int increment=1, bool allow_new_words=true);
        virtual BaseNode* count_ngram(const WordId* wids, int n, int increment);
        virtual int get_ngram_count(const wchar_t* const* ngram, int n);

        virtual void get_node_values(BaseNode* node, int level,
                                     std::vector<int>& values)
        {
            values.push_back(node->count);
            values.push_back(ngrams.get_N1prx(node, level));
        }
        virtual void get_memory_sizes(std::vector<long>& values)
        {
            values.push_back(dictionary.get_memory_size());
            values.push_back(ngrams.get_memory_size());
        }

    protected:
        virtual LMError write_arpa_ngrams(FILE* f);

        virtual void get_words_with_predictions(
                                       const std::vector<WordId>& history,
                                       std::vector<WordId>& wids)
        {
            std::vector<WordId> h(history.end()-1, history.end()); // bigram history
            ngrams.get_child_wordids(h, wids);
        }

        virtual void get_probs(const std::vector<WordId>& history,
                               const std::vector<WordId>& words,
                               std::vector<double>& probabilities);

        virtual int increment_node_count(BaseNode* node, const WordId* wids,
                                         int n, int increment)
        {
            return ngrams.increment_node_count(node, wids, n, increment);
        }

        // Number of n-grams per level, excluding removed ones with count==0.
        virtual int get_num_ngrams(int level)
        {
            return ngrams.get_num_ngrams(level);
        }

        virtual void reserve_unigrams(int count)
        {
            ngrams.reserve_unigrams(count);
        }

   private:
        BaseNode* get_ngram_node(const wchar_t* const* ngram, int n)
        {
            std::vector<WordId> wids(n);
            for (int i=0; i<n; i++)
                wids[i] = dictionary.word_to_id(ngram[i]);
            return ngrams.get_node(wids);
        }

    protected:
        // n-gram trie
        TNGRAMS ngrams;

        // smoothing
        Smoothing smoothing;

        // total number of n-grams with exactly one count, per level
        std::vector<int> n1s;

        // total number of n-grams with exactly two counts, per level
        std::vector<int> n2s;

        // discounting parameters for abs. discounting, kneser-ney, per level
        std::vector<double> Ds;
};

typedef _DynamicModel<NGramTrie<TrieNode<BaseNode>,
                                BeforeLastNode<BaseNode, LastNode<BaseNode> >,
                                LastNode<BaseNode> > > DynamicModel;

#include "lm_dynamic_impl.h"

#endif

