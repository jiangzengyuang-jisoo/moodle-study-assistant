"""Mixed-topic memory queries retain each requested concept without phrase noise."""
import unittest

from learning_library.evidence import search_evidence
from learning_library.recall import plan


def state_with_topics(*topics, aliases=()):
    return {'revision': 1, 'courses': [{'id': 'C', 'memory_aliases': list(aliases),
            'assistant_notes': [{'id': str(i), 'topic': topic}
                                for i, topic in enumerate(topics)]}]}


def result_ids(state, query):
    return {row['metadata']['id'] for row in search_evidence(state, 'C', query)['matches']}


class MemoryQueryTests(unittest.TestCase):
    def test_alias_and_uncovered_keywords_both_retrieve_records(self):
        state = state_with_topics('FIFO', 'weighted average')
        for query in ('FIFO weighted average', 'ＦｉＦｏ WEIGHTED_average',
                      '先进先出 / weighted average'):
            with self.subTest(query=query):
                self.assertEqual({'0', '1'}, result_ids(state, query))
        chinese = state_with_topics('FIFO', '加权平均')
        self.assertEqual({'0', '1'}, result_ids(chinese, 'FIFO、加权平均'))

    def test_standalone_short_alias_survives_long_alias_elsewhere(self):
        state = state_with_topics('gross profit', 'gross profit margin')
        for query in ('gross profit and gross profit margin',
                      'gross profit margin and gross profit',
                      '毛利和毛利率', '毛利率和毛利', '毛利 and GROSS PROFIT MARGIN'):
            with self.subTest(query=query):
                self.assertEqual({'0', '1'}, result_ids(state, query))

    def test_words_inside_recognized_phrase_do_not_retrieve_noise(self):
        state = state_with_topics('拉格朗日中值定理', 'value', 'weighted average')
        self.assertEqual({'0'}, result_ids(state, 'mean value theorem'))
        self.assertEqual({'0', '2'}, result_ids(state, 'mean value theorem and weighted average'))
        self.assertEqual({'0', '2'}, result_ids(state, 'weighted average and MVT'))

    def test_long_alias_alone_does_not_expand_its_shorter_component(self):
        state = state_with_topics('gross profit', 'gross profit margin')
        for query in ('毛利率以前讲过吗', 'GROSS PROFIT MARGIN', '毛利率 / gross profit margin'):
            with self.subTest(query=query):
                self.assertEqual({'1'}, result_ids(state, query))

    def test_custom_aliases_obey_the_same_position_rule(self):
        state = state_with_topics('whole concept', 'part concept', aliases=[
            ['specific topic', 'whole concept'], ['topic', 'part concept']])
        self.assertEqual({'0'}, result_ids(state, 'specific topic'))
        self.assertEqual({'0', '1'}, result_ids(state, 'topic and specific topic'))
        self.assertEqual({'0', '1'}, result_ids(state, 'specific topic and topic'))

    def test_empty_normalized_query_is_rejected_instead_of_matching_everything(self):
        state = state_with_topics('FIFO', 'weighted average')
        for query in ('', ' ', '___', '__ \t＿'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                search_evidence(state, 'C', query)

    def test_empty_normalized_custom_alias_is_rejected(self):
        for term in ('___', '＿ _'):
            with self.subTest(term=term), self.assertRaises(ValueError):
                plan('FOB', [['FOB', term]])

    def test_abbreviation_boundaries_remain_intact(self):
        state = state_with_topics('FOB', 'FOBish', 'weighted average')
        self.assertEqual({'0', '2'}, result_ids(state, 'FOB weighted average'))


if __name__ == '__main__':
    unittest.main()
