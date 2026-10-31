"""Synthetic cross-chapter handoffs, coverage and observed learning evidence."""
from copy import deepcopy
import unittest

from cursor_evidence import active_snapshot
from learning_library.context import build_context
from state_io import validate_draft


def state():
    return {'courses': [{'id': 'C', 'current_topic': 'Chapter 7', 'assistant_notes': [],
                        'teaching_cursor': {'chapter': 6, 'topic': 'FOB', 'next_step': 'old exercise',
                                            'checks': [{'id': 'old', 'answer': 0}]}}]}


class TeachingContinuityTests(unittest.TestCase):
    def test_unchanged_legacy_history_shape_allows_unrelated_update(self):
        initial = state()
        initial['courses'][0]['teaching_cursor']['history'] = {'old_note': 'keep'}
        draft = deepcopy(initial)
        draft['courses'][0]['assistant_notes'].append('unrelated')
        validate_draft(initial, draft)

    def test_long_observation_is_omitted_with_detail_pointer(self):
        initial = state()
        initial['courses'][0]['topic_evidence'] = [{'id': 'long', 'topic': 'x' * 20000, 'level': 'explained'}]
        result = build_context(initial, 'C')
        self.assertNotIn('topic', result['sections']['course']['topic_evidence'][0])
        self.assertTrue(any(row['path'] == '/courses/0/topic_evidence/0/topic' for row in result['omitted']))

    def test_history_cannot_become_active_even_with_newer_timestamps(self):
        initial = state()
        cursor = initial['courses'][0]['teaching_cursor']
        cursor.update(chapter=7, topic='Inventory', next_step='current exercise', updated_at='2026-01-02')
        old = {'next_step': 'OLD_NEXT', 'pending_question': {'prompt': 'OLD_QUESTION'},
               'session_handoff': {'next_step': 'OLD_HANDOFF'}, 'updated_at': '2099-01-01'}
        for field in ('checks', 'history', 'review_backlog'):
            with self.subTest(field=field):
                sample = deepcopy(initial)
                sample['courses'][0]['teaching_cursor'][field] = [old]
                result = build_context(sample, 'C')['sections']['course']['teaching_cursor']
                self.assertNotIn('pending_questions', result)
                self.assertNotIn('session_handoff', result)
                self.assertEqual(result['latest_continuation']['record']['next_step'], 'current exercise')
                self.assertEqual(result['chapter'], 7)

    def test_switch_requires_lossless_old_position_and_preserves_checks(self):
        initial, draft = state(), state()
        old = initial['courses'][0]['teaching_cursor']
        new = draft['courses'][0]['teaching_cursor']
        new.update(chapter=7, topic='Inventory', next_step='new exercise')
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)
        new['history'] = [deepcopy(active_snapshot(old))]
        validate_draft(initial, draft)
        new['checks'] = []
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)

    def test_archive_does_not_bypass_actual_answer_preservation(self):
        initial = state()
        old = initial['courses'][0]['teaching_cursor']
        old['pending_check'] = {'prompt': 'Q', 'answer': 0, 'assisted': True}
        draft = deepcopy(initial)
        new = draft['courses'][0]['teaching_cursor']
        new['history'] = [deepcopy(active_snapshot(old))]
        new.update(chapter=7, topic='Inventory')
        new.pop('pending_check')
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)
        new['checks'].append(deepcopy(old['pending_check']))
        validate_draft(initial, draft)

    def test_old_handoff_can_move_to_history_without_reappearing(self):
        initial = state()
        old = initial['courses'][0]['teaching_cursor']
        old['session_handoff'] = {'next_step': 'old', 'updated_at': '2099-01-01'}
        draft = deepcopy(initial)
        new = draft['courses'][0]['teaching_cursor']
        new['history'] = [deepcopy(active_snapshot(old))]
        new.pop('session_handoff')
        new.update(chapter=7, topic='Inventory')
        draft['courses'][0]['review_backlog'] = [{'id': 'review-old', 'chapter': 6, 'topic': 'FOB', 'status': 'pending'}]
        validate_draft(initial, draft)
        result = build_context(draft, 'C')['sections']['course']
        self.assertNotIn('session_handoff', result['teaching_cursor'])
        self.assertEqual(result['review_backlog'][0]['chapter'], 6)

    def test_cursor_history_cannot_be_rewritten(self):
        initial = state()
        initial['courses'][0]['teaching_cursor']['history'] = [{'topic': 'Older'}]
        draft = deepcopy(initial)
        draft['courses'][0]['teaching_cursor']['history'][0]['topic'] = 'changed'
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)

    def test_user_correction_is_explained_not_mastery(self):
        initial, draft = state(), state()
        record = {'id': 'e1', 'topic': 'FOB', 'chapter': 6, 'level': 'explained',
                  'source': {'kind': 'user_report', 'text': 'Previously explained'}}
        draft['courses'][0]['topic_evidence'] = [record]
        validate_draft(initial, draft)
        result = build_context(draft, 'C')['sections']['course']
        self.assertEqual(result['topic_evidence'][0]['level'], 'explained')
        self.assertNotIn('mastery_verified', result['topic_evidence'][0])
        for level in ('independent', 'delayed_pass'):
            record['level'] = level
            with self.assertRaises(ValueError):
                validate_draft(initial, draft)

    def test_independent_and_delayed_require_explicit_conditions(self):
        initial, draft = state(), state()
        for sample in (initial, draft):
            sample['courses'][0]['teaching_cursor']['checks'][0].update(
                independent=True, assisted=False, answer_exposed=False, at='2026-01-03', correct=True)
        row = {'id': 'e1', 'topic': 'FOB', 'level': 'independent', 'passed': False,
               'source': {'kind': 'observed_attempt', 'record_path': '/courses/0/teaching_cursor/checks/0'},
               'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False}}
        draft['courses'][0]['topic_evidence'] = [row]
        validate_draft(initial, draft)  # Independent and incorrect remains possible.
        row['conditions']['assisted'] = True
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)
        row['conditions']['assisted'] = False
        row['level'] = 'delayed_pass'
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)
        earlier = {'id': 'earlier', 'topic': 'FOB', 'level': 'explained', 'at': '2026-01-01',
                   'source': {'kind': 'user_report'}}
        draft['courses'][0]['topic_evidence'].insert(0, earlier)
        row.update(passed=True, previous_event_id='earlier', at='2026-01-03')
        validate_draft(initial, draft)

    def test_learning_observations_are_append_only_with_corrections(self):
        initial = state()
        row = {'id': 'e1', 'topic': 'FOB', 'level': 'not_found', 'source': {'kind': 'record_search'}}
        initial['courses'][0]['topic_evidence'] = [row]
        draft = deepcopy(initial)
        draft['courses'][0]['topic_evidence'][0]['level'] = 'explained'
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)
        draft = deepcopy(initial)
        draft['courses'][0]['topic_evidence'].append({**row, 'id': 'e2', 'level': 'explained',
                                                    'corrects': 'e1', 'source': {'kind': 'user_report'}})
        validate_draft(initial, draft)

    def test_coverage_requires_three_sources_and_does_not_rate_mastery(self):
        initial, draft = state(), state()
        row = {'id': 'c1', 'scope': 'Chapter 7 inventory ownership', 'status': 'checked_complete',
               'sources': {'materials': ['slide 5 diagram'], 'assignments': ['Q2'], 'history': ['Ch6 FOB']}}
        draft['courses'][0]['coverage_records'] = [row]
        for field in ('materials', 'assignments', 'history'):
            missing = deepcopy(draft)
            missing['courses'][0]['coverage_records'][0]['sources'][field] = []
            with self.assertRaises(ValueError):
                validate_draft(initial, missing)
        validate_draft(initial, draft)
        result = build_context(draft, 'C')['sections']['course']
        self.assertNotIn('topic_evidence', result)
        self.assertEqual(result['coverage_records'][0]['status'], 'checked_complete')

    def test_legacy_notes_are_not_migrated_or_interpreted(self):
        initial = state()
        initial['courses'][0]['assistant_notes'] = [{'text': 'Chapter 7 explained; FOB was in Chapter 6'}]
        before = deepcopy(initial)
        result = build_context(initial, 'C')['sections']['course']
        self.assertEqual(initial, before)
        self.assertEqual(result['teaching_cursor']['chapter'], 6)
        self.assertNotIn('topic_evidence', result)
        self.assertIn('未查到', result['continuity_policy']['missing_evidence'])


if __name__ == '__main__':
    unittest.main()
