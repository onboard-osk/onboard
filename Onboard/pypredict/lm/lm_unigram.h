/*
 * Copyright © 2013-2014 marmuta <marmvta@gmail.com>
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

#ifndef LM_UNIGRAM_H
#define LM_UNIGRAM_H

#include "lm_dynamic.h"

//------------------------------------------------------------------------
// UnigramModel - Memory efficient model for word frequencies.
//------------------------------------------------------------------------
class UnigramModel : public DynamicModelBase
{
    public:
        class ngrams_iter : public DynamicModelBase::ngrams_iter
        {
            public:
                ngrams_iter(UnigramModel* lm) :
                    it(lm->m_counts.begin()), model(lm)
                {}

                virtual BaseNode* operator*() const // dereference operator
                {
                    if (it == model->m_counts.end())
                        return NULL;
                    else
                    {
                        BaseNode* pnode = const_cast<BaseNode*>(&node);
                        pnode->count = *it;
                        return const_cast<BaseNode*>(&node);
                    }
                }

                virtual void operator++(int unused) // postfix operator
                { it++; }

                virtual void get_ngram(std::vector<WordId>& ngram)
                {
                    WordId wid = it - model->m_counts.begin();
                    ngram.resize(1);
                    ngram[0] = wid;
                }

                virtual int get_level()
                { return 1; }

                virtual bool at_root()
                { return false; }

            public:
                std::vector<CountType>::iterator it;
                UnigramModel* model;
                BaseNode node;  // dummy node to satisfy the NGramIter interface
        };
        virtual DynamicModelBase::ngrams_iter* ngrams_begin()
        {return new ngrams_iter(this);}

    public:
        UnigramModel()
        {
            set_order(1);
        }

        virtual ~UnigramModel()
        {
            #ifndef NDEBUG
            uint64_t v = dictionary.get_memory_size();
            uint64_t n = ngrams.get_memory_size();
            printf("memory: dictionary=%ld, ngrams=%ld, total=%ld\n", v, n, v+n);
            #endif
        }

        virtual void clear()
        {
            std::vector<CountType>().swap(m_counts); // clear and really free the memory
            DynamicModelBase::clear();  // clears dictionary
        }

        virtual int get_max_order()
        {
            return 1;
        }

        virtual BaseNode* count_ngram(const wchar_t* const* ngram, int n,
                                int increment=1, bool allow_new_words=true)
        {
            if (n == 1)
            {
                std::vector<WordId> wids(n);
                if (dictionary.query_add_words(ngram, n, wids, allow_new_words))
                    return count_ngram(wids.data(), n, increment);
            }
            return NULL;
        }

        virtual BaseNode* count_ngram(const WordId* wids, int n, int increment)
        {
            if (n != 1)
                return NULL;

            WordId wid = wids[0];
            if (m_counts.size() <= wid)
                m_counts.push_back(0);

            m_counts.at(wid) += increment;

            node.word_id = wid;
            node.count = m_counts[wid];
            return &node;
        }

        virtual int get_ngram_count(const wchar_t* const* ngram, int n)
        {
            if (!n)
                return 0;
            WordId wid = dictionary.word_to_id(ngram[0]);
            return wid >= 0 && wid < m_counts.size() ? m_counts.at(wid) : 0;
        }

        virtual void get_node_values(BaseNode* node, int level,
                                     std::vector<int>& values)
        {
            values.push_back(node->count);
        }

        virtual bool is_model_valid()
        {
            int num_unigrams = get_num_ngrams(0);
            return num_unigrams == dictionary.get_num_word_types();
        }

        virtual void get_memory_sizes(std::vector<long>& values)
        {
            values.push_back(dictionary.get_memory_size());
            values.push_back(sizeof(CountType) * m_counts.capacity());
        }

    protected:
        virtual void get_words_with_predictions(
                                       const std::vector<WordId>& history,
                                       std::vector<WordId>& wids)
        {}
        virtual void get_probs(const std::vector<WordId>& history,
                               const std::vector<WordId>& words,
                               std::vector<double>& probabilities);

        virtual int get_num_ngrams(int level)
        {
            if (level == 0)
                return m_counts.size();
            else
                return 0;
        }

        virtual void reserve_unigrams(int count)
        {
            m_counts.resize(count);
            fill(m_counts.begin(), m_counts.end(), 0);
        }

    protected:
        std::vector<CountType> m_counts;
        BaseNode node;  // dummy node to satisfy the count_ngram interface
};

#endif

