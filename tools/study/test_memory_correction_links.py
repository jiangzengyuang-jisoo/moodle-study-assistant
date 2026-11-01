"""Ranked history searches retain correction links even when those rows are hidden."""
from copy import deepcopy
import json
import unittest

from learning_library.context import read_detail
from learning_library.evidence import search_evidence


def state_with(rows):
    return {'revision': 1, 'courses': [{'id': 'C', 'topic_evidence': rows}]}


def original(record_id='old'):
    return {'id': record_id, 'topic': 'FOB', 'level': 'independent',
            'user_answer': 'SECRET_ORIGINAL'}


def correction(record_id, target, **extra):
    return {'id': record_id, 'topic': 'Ownership conditions', 'level': 'assisted',
            'corrects': target, 'user_answer': 'SECRET_CORRECTION', **extra}


class MemoryCorrectionLinkTests(unittest.TestCase):
    def test_lower_ranked_correction_stays_attached_at_limit_one(self):
        state = state_with([original('old-' + str(i)) for i in range(8)] + [
            correction('fix', 'old-7', topic='Shipping point', text='FOB was assisted')])
        before = deepcopy(state)
        result = search_evidence(state, 'C', 'FOB', limit=1)
        row = result['matches'][0]
        self.assertEqual('old-7', row['metadata']['id'])
        self.assertEqual('independent', row['metadata']['level'])
        self.assertTrue(row['correction_review_required'])
        self.assertEqual(['/courses/0/topic_evidence/8'], row['topic_corrections']['paths'])
        self.assertEqual(1, row['topic_corrections']['count'])
        self.assertFalse(row['topic_corrections']['truncated'])
        self.assertTrue(result['truncated'])
        self.assertEqual('not_assessed', result['learning_conclusion'])
        self.assertNotIn('SECRET_', json.dumps(result))
        self.assertEqual(before, state)

    def test_nonmatching_chained_and_branching_corrections_remain_traceable(self):
        state = state_with([original(), correction('a', 'old'),
                            correction('b', 'a'), correction('c', 'old')])
        result = search_evidence(state, 'C', 'FOB', limit=1)
        links = result['matches'][0]['topic_corrections']
        self.assertEqual(3, links['count'])
        self.assertEqual({f'/courses/0/topic_evidence/{i}' for i in (1, 2, 3)}, set(links['paths']))
        self.assertEqual(1, result['total_matching_locations'])
        self.assertEqual({'a', 'b', 'c'}, {
            read_detail(state, 'path', path)['record']['id'] for path in links['paths']})

    def test_large_correction_history_is_bounded_with_full_collection_locator(self):
        state = state_with([original()] + [correction('c' + str(i), 'old') for i in range(24)])
        result = search_evidence(state, 'C', 'FOB', limit=1)
        links = result['matches'][0]['topic_corrections']
        self.assertEqual(24, links['count'])
        self.assertEqual(8, len(links['paths']))
        self.assertTrue(links['truncated'])
        self.assertEqual('/courses/0/topic_evidence', links['all_records_path'])
        self.assertEqual(25, len(read_detail(state, 'path', links['all_records_path'])['record']))
        self.assertLess(len(json.dumps(result)), 6000)

    def test_other_course_corrections_are_not_linked(self):
        state = state_with([original(), correction('foreign', 'old', course_id='OTHER'),
                            correction('foreign-list', 'old', course_ids=['OTHER'])])
        state['courses'].append({'id': 'OTHER', 'topic_evidence': [correction('also-foreign', 'old')]})
        result = search_evidence(state, 'C', 'FOB')
        self.assertNotIn('topic_corrections', result['matches'][0])
        self.assertNotIn('foreign', json.dumps(result))

    def test_legacy_list_links_and_cycles_do_not_omit_corrections_or_loop(self):
        state = state_with([original(), correction('a', ['old', 'b']), correction('b', 'a')])
        result = search_evidence(state, 'C', 'FOB', limit=1)
        links = result['matches'][0]['topic_corrections']
        self.assertEqual(2, links['count'])
        self.assertEqual(2, len(set(links['paths'])))


if __name__ == '__main__':
    unittest.main()
