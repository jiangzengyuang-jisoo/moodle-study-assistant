"""Memory inspection follows the same active continuation as course context."""
from copy import deepcopy
import json
import unittest

from learning_library.context import build_context
from learning_library.evidence import record_hash
from learning_library.memory import inspect_memory


def state_with(cursor):
    return {'revision': 1, 'courses': [{'id': 'C', 'teaching_cursor': cursor}]}


def claim(state):
    return inspect_memory(state, 'C')['claims'][0]


class MemoryContinuityTests(unittest.TestCase):
    def test_newer_nested_followup_matches_context_and_keeps_root_locator(self):
        cursor = {'next_step': 'Continue Q1', 'updated_at': '2026-10-08',
                  'latest_followup': {'topic': 'Topic 2', 'next_step': 'Continue Q2',
                                      'updated_at': '2026-10-10'}}
        state = state_with(cursor); before = deepcopy(state)
        expected = build_context(state, 'C')['sections']['course']['teaching_cursor']['latest_continuation']
        result = claim(state)
        self.assertEqual(expected['path'], result['path'])
        self.assertEqual('Continue Q2', result['value']['next_step'])
        self.assertEqual('Topic 2', result['value']['topic'])
        self.assertEqual(record_hash(cursor['latest_followup']), result['record_sha256'])
        self.assertEqual('/courses/0/teaching_cursor', result['root_record']['path'])
        self.assertEqual(record_hash(cursor), result['root_record']['record_sha256'])
        self.assertTrue(result['omitted'])
        self.assertEqual(before, state)

    def test_nested_handoff_keeps_its_source_path_and_hides_answers(self):
        cursor = {'next_step': 'Old', 'updated_at': '2026-10-08',
                  'session_handoff': {'next_step': 'Continue Q3', 'updated_at': '2026-10-12',
                                      'user_answer': 'PRIVATE-ANSWER', 'expected_answer': 'SECRET-KEY'}}
        result = claim(state_with(cursor))
        self.assertEqual('Continue Q3', result['value']['next_step'])
        self.assertEqual('/courses/0/teaching_cursor/session_handoff', result['path'])
        self.assertNotIn('PRIVATE-ANSWER', json.dumps(result))
        self.assertNotIn('SECRET-KEY', json.dumps(result))

    def test_next_check_only_continuation_is_not_lost(self):
        result = claim(state_with({'followup': {'next_check': 'Unassisted check', 'updated_at': '2026-10-10'}}))
        self.assertEqual('Unassisted check', result['value']['next_check'])
        self.assertEqual('/courses/0/teaching_cursor/followup', result['path'])

    def test_newer_top_level_still_wins(self):
        cursor = {'next_step': 'Current', 'updated_at': '2026-10-12',
                  'latest_followup': {'next_step': 'Older', 'updated_at': '2026-10-10'}}
        result = claim(state_with(cursor))
        self.assertEqual('Current', result['value']['next_step'])
        self.assertEqual('/courses/0/teaching_cursor', result['path'])
        self.assertEqual(record_hash(cursor), result['record_sha256'])

    def test_oversized_current_record_still_points_to_selected_nested_source(self):
        result = claim(state_with({'latest_followup': {'next_step': 'x' * 2000, 'updated_at': '2026-10-10'}}))
        self.assertEqual('/courses/0/teaching_cursor/latest_followup', result['path'])
        self.assertTrue(result['value']['omitted'])
        self.assertEqual(result['path'], result['value']['detail_path'])

    def test_nested_answer_fields_are_removed_from_allowed_preview_fields(self):
        result = claim(state_with({'next_step': {'prompt': 'Try this', 'expected_answer': 'SECRET'}}))
        self.assertEqual('Try this', result['value']['next_step']['prompt'])
        self.assertNotIn('SECRET', json.dumps(result))
