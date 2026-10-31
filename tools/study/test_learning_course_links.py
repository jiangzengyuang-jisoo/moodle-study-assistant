"""Real review pipeline and legacy ownership joins use synthetic state only."""
from copy import deepcopy
import json
import unittest

from course_links import record_course_ids, review_attempt_context
from learning_library.context import build_context, read_detail
from learning_library.evidence import search_evidence
import learning_loop as loop
from test_learning_loop import AT, attempt, fixture


def recorded(count=1):
    state = fixture(count)
    for index, row in enumerate(state['review_queue']):
        row['practice_variants'][0]['prompt'] = f'FOB application {index}'
    package = loop.prepare(state, AT)
    for index, row in enumerate(package['items']):
        request = attempt(package, attempt_id=f'A{index}', review_id=row['review_id'],
                          variant_id=row['variant_id'], answer=f'PRIVATE_RESPONSE_{index}')
        loop.record_attempt(state, request)
    return state


class LearningCourseLinksTests(unittest.TestCase):
    def assert_unassigned(self, state, status=None):
        row = state['review_attempts'][0]
        self.assertEqual(record_course_ids(state, row, 'review_attempts'), frozenset())
        if status:
            self.assertEqual(review_attempt_context(state, row)['status'], status)
        for course in ('MATH-DEMO', 'ECON-DEMO'):
            self.assertEqual(build_context(state, course)['summary']['record_counts']['review_attempts'], 0)
            self.assertEqual(search_evidence(state, course, 'FOB')['status'], 'no_matching_record')

    def test_actual_prepare_record_attempt_is_found_without_rewriting_answer(self):
        state = recorded()
        before = deepcopy(state)
        row = state['review_attempts'][0]
        self.assertNotIn('course_id', row)
        self.assertEqual(record_course_ids(state, row, 'review_attempts'), {'MATH-DEMO'})
        result = search_evidence(state, 'MATH-DEMO', 'FOB')
        self.assertEqual(result['status'], 'candidates_found')
        match = next(item for item in result['matches'] if item['path'] == '/review_attempts/0')
        self.assertIn('linked_question_paths', match)
        self.assertEqual(read_detail(state, 'path', match['path'])['record'], row)
        context = build_context(state, 'MATH-DEMO')
        self.assertEqual(context['summary']['record_counts']['review_attempts'], 1)
        self.assertTrue(any(item['path'] == '/review_attempts' for item in context['omitted']))
        rendered = json.dumps([result, context])
        for secret in ('PRIVATE_RESPONSE', 'PRIVATE_FEEDBACK', 'PRIVATE_ANSWER', 'LEGACY ANSWER'):
            self.assertNotIn(secret, rendered)
        self.assertEqual(state, before)

    def test_multi_course_session_keeps_answers_separate(self):
        state = recorded(2)
        for course, index in (('MATH-DEMO', 0), ('ECON-DEMO', 1)):
            result = search_evidence(state, course, 'FOB')
            self.assertEqual([row['path'] for row in result['matches']], [f'/review_attempts/{index}'])
            context = build_context(state, course, detail=True)
            self.assertEqual(len(context['sections']['review_attempts']), 1)
            self.assertEqual(context['sections']['review_attempts'][0]['attempt_id'], f'A{index}')

    def test_legacy_answer_without_prompt_uses_session_question(self):
        state = recorded()
        state['review_attempts'][0].pop('prompt')
        state['review_queue'] = []
        result = search_evidence(state, 'MATH-DEMO', 'FOB')
        self.assertEqual(result['total_matching_locations'], 1)
        self.assertEqual(result['matches'][0]['path'], '/review_attempts/0')
        self.assertEqual(result['matches'][0]['linked_question_paths'], ['/review_sessions/0/items/0'])

    def test_legacy_request_only_ids_use_exact_queue_variant(self):
        state = recorded()
        row = state['review_attempts'][0]
        for key in ('prompt', 'session_id', 'review_id', 'variant_id'):
            row.pop(key)
        state['review_sessions'] = []
        state['review_queue'][0]['practice_variants'][1]['topic'] = 'UNRELATED_VARIANT'
        result = search_evidence(state, 'MATH-DEMO', 'FOB')
        self.assertEqual(result['total_matching_locations'], 1)
        self.assertIn('/review_queue/0/practice_variants/0', result['matches'][0]['linked_question_paths'])
        self.assertEqual(search_evidence(state, 'MATH-DEMO', 'UNRELATED_VARIANT')['status'], 'no_matching_record')

    def test_explicit_legacy_course_without_any_links_remains_readable(self):
        state = {'courses': [{'id': 'MATH-DEMO'}], 'review_attempts': [
            {'course_id': 'MATH-DEMO', 'topic': 'FOB', 'answer': 'PRIVATE'}]}
        self.assertEqual(search_evidence(state, 'MATH-DEMO', 'FOB')['shown'], 1)
        self.assertEqual(build_context(state, 'MATH-DEMO')['summary']['record_counts']['review_attempts'], 1)

    def test_explicit_other_course_cannot_be_overridden_by_join(self):
        state = recorded()
        state['review_attempts'][0]['course_id'] = 'ECON-DEMO'
        self.assert_unassigned(state, 'conflict')

    def test_explicit_course_ids_conflict_is_not_ignored(self):
        state = recorded()
        state['review_attempts'][0].update(course_id='MATH-DEMO', course_ids=['ECON-DEMO'])
        self.assert_unassigned(state, 'conflict')

    def test_session_queue_course_conflict_is_not_guessed(self):
        state = recorded()
        state['review_queue'][0]['course_id'] = 'ECON-DEMO'
        self.assert_unassigned(state, 'conflict')

    def test_duplicate_session_id_is_ambiguous(self):
        state = recorded()
        state['review_sessions'].append(deepcopy(state['review_sessions'][0]))
        self.assert_unassigned(state, 'ambiguous')

    def test_duplicate_queue_id_is_ambiguous(self):
        state = recorded()
        state['review_queue'].append(deepcopy(state['review_queue'][0]))
        self.assert_unassigned(state, 'ambiguous')

    def test_duplicate_variant_id_does_not_match_wrong_question(self):
        state = recorded()
        variants = state['review_queue'][0]['practice_variants']
        variants.append(deepcopy(variants[0]))
        self.assert_unassigned(state, 'ambiguous')

    def test_request_identifiers_cannot_disagree_with_outer_record(self):
        state = recorded()
        state['review_attempts'][0]['request']['review_id'] = 'R-other'
        self.assert_unassigned(state, 'conflict')

    def test_existing_session_with_no_matching_item_is_not_queue_fallback(self):
        state = recorded()
        state['review_sessions'][0]['items'][0]['variant_id'] = 'different'
        self.assert_unassigned(state, 'conflict')

    def test_ambiguous_and_unresolved_legacy_records_are_not_classified(self):
        state = {'courses': [{'id': 'MATH-DEMO'}, {'id': 'ECON-DEMO'}],
                 'review_attempts': [{'topic': 'FOB', 'answer': 'PRIVATE'}]}
        self.assert_unassigned(state, 'unresolved')
        state['review_attempts'][0]['course_ids'] = ['MATH-DEMO', 'ECON-DEMO']
        self.assert_unassigned(state, 'ambiguous')

    def test_helper_never_returns_answer_payloads(self):
        state = recorded()
        item = state['review_sessions'][0]['items'][0]
        item.update(answer='PRIVATE_SESSION', source={'solution': 'PRIVATE_SOURCE'})
        linked = review_attempt_context(state, state['review_attempts'][0])
        rendered = json.dumps(linked)
        for secret in ('PRIVATE', 'LEGACY ANSWER', 'answer_check', 'feedback'):
            self.assertNotIn(secret, rendered)


if __name__ == '__main__':
    unittest.main()
