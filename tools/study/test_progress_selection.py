"""Saved positions are consistent across entry points without rewriting evidence."""
from copy import deepcopy
import json
import unittest

from learning_library.context import build_context, read_detail, teaching_progress
from learning_library.evidence import record_hash, search_evidence
from learning_library.memory import apply_correction, inspect_memory, records, validate_preserved


def event(ident, when, chapter, *, affects_current=True):
    return {'id': ident, 'occurred_at': when, 'recorded_at': '2026-11-09T10:00:00+08:00',
            'affects_current': affects_current, 'event_type': 'topic_change',
            'source': {'kind': 'chat', 'thread_id': 'synthetic-chat', 'turn_ids': ['turn-1']},
            'position': {'topic': 'Chapter ' + str(chapter), 'chapter': str(chapter),
                         'stage': 'practice', 'next_step': 'Independent check ' + str(chapter)}}


def state_with(course):
    return {'revision': 4, 'courses': [{'id': 'C', **course}]}


class ProgressSelectionTests(unittest.TestCase):
    def assert_entry_points_agree(self, state):
        before = deepcopy(state)
        overview = build_context(state)['sections']['courses'][0]
        course = build_context(state, 'C')['sections']['course']
        memory = inspect_memory(state, 'C')
        self.assertEqual(overview['teaching_progress'], course['teaching_progress'])
        self.assertEqual(course['teaching_progress'], memory['teaching_progress'])
        self.assertEqual(overview['assignment_scope'], memory['assignment_scope'])
        self.assertEqual('not_assessed', overview['mastery']['status'])
        self.assertEqual(before, state)
        return overview['teaching_progress']

    def test_nested_new_position_wins_in_all_views_not_assignment_chapter(self):
        state = state_with({'current_topic': 'Chapter 8', 'assignment_focus': {'required_chapters': [8]},
                            'learning_progress': {'mastery_verified': False},
                            'teaching_cursor': {'topic': '4.3', 'next_step': 'Old question', 'at': '2026-10-01',
                                                'latest_followup': {'topic': '5.3', 'next_step': 'Next practice',
                                                                    'at': '2026-10-03'}}})
        selected = self.assert_entry_points_agree(state)
        self.assertEqual('/courses/0/teaching_cursor/latest_followup', selected['path'])
        self.assertEqual('5.3', selected['record']['topic'])
        self.assertFalse(state['courses'][0]['learning_progress']['mastery_verified'])

    def test_history_previous_cursor_sources_and_question_keys_are_not_positions(self):
        future = {'next_step': 'HISTORICAL', 'updated_at': '2099-01-01'}
        cursor = {'next_step': 'CURRENT', 'at': '2026-10-02'}
        for field in ('history', 'previous_cursor', 'source', 'checks', 'review_backlog', 'evidence', 'pending_question'):
            cursor[field] = deepcopy(future)
        result = self.assert_entry_points_agree(state_with({'teaching_cursor': cursor}))
        self.assertEqual('CURRENT', result['record']['next_step'])

    def test_legacy_occurrence_precedes_later_recording_timestamp(self):
        state = state_with({'teaching_cursor': {'next_step': 'Newer teaching', 'occurred_at': '2026-10-05',
                            'latest_followup': {'next_step': 'Backfilled older lesson', 'occurred_at': '2026-10-02',
                                                'updated_at': '2026-11-09', 'recorded_at': '2026-11-09'}}})
        result = self.assert_entry_points_agree(state)
        self.assertEqual('Newer teaching', result['record']['next_step'])
        self.assertEqual('2026-10-05', result['as_of'])

    def test_backfilled_event_does_not_roll_back_or_select_highest_chapter(self):
        recent = event('recent', '2026-10-08T10:00:00+08:00', 5)
        older = event('older', '2026-10-06T10:00:00+08:00', 9)
        older['recorded_at'] = '2099-01-01T00:00:00+08:00'
        older['position']['updated_at'] = '2099-01-01'
        older['source']['next_step'] = 'NOT CURRENT'
        older['source']['at'] = '2099-01-01'
        result = self.assert_entry_points_agree(state_with({'teaching_events': [recent, older]}))
        self.assertEqual('/courses/0/teaching_events/0/position', result['path'])
        self.assertEqual('5', result['record']['chapter'])
        self.assertEqual(recent['source'], result['source'])
        self.assertEqual(recent['occurred_at'], result['as_of'])

    def test_answer_only_and_historical_events_do_not_move_current_position(self):
        current = event('current', '2026-10-06T10:00:00+08:00', 5)
        answer = event('answer', '2026-10-08T10:00:00+08:00', 10, affects_current=False)
        answer['event_type'] = 'answer_exposure'
        result = self.assert_entry_points_agree(state_with({'teaching_events': [current, answer]}))
        self.assertEqual('5', result['record']['chapter'])

    def test_answer_only_without_cursor_is_history_not_a_memory_position_claim(self):
        answer = event('answer', '2026-10-08T10:00:00+08:00', 10, affects_current=False)
        state = state_with({'teaching_events': [answer]})
        result = self.assert_entry_points_agree(state)
        self.assertEqual('missing', result['status'])
        claim = inspect_memory(state, 'C')['claims'][0]
        self.assertIsNone(claim['value'])
        self.assertEqual('/courses/0/teaching_events', claim['root_record']['path'])

    def test_long_chat_sources_keep_exact_detail_locator(self):
        current = event('new', '2026-10-08T10:00:00+08:00', 5)
        current['source']['turn_ids'] = ['turn-' + str(i) + 'x' * 100 for i in range(100)]
        state = state_with({'teaching_events': [current]})
        result = self.assert_entry_points_agree(state)
        self.assertTrue(result['source']['omitted'])
        self.assertEqual(current['source'], read_detail(state, 'path', result['source']['detail_path'])['record'])

    def test_equal_occurrence_conflict_is_explicit_in_all_views(self):
        one = event('one', '2026-10-08T10:00:00+08:00', 5)
        two = event('two', '2026-10-08T02:00:00Z', 6)
        state = state_with({'teaching_events': [one, two]})
        result = self.assert_entry_points_agree(state)
        self.assertTrue(result['conflict'])
        self.assertEqual('conflict', result['status'])
        self.assertIsNone(result['record'])
        self.assertIsNone(result['path'])
        self.assertEqual(2, len(result['candidates']))
        self.assertIsNone(build_context(state)['sections']['courses'][0]['next_step'])
        self.assertIsNone(inspect_memory(state, 'C')['claims'][0]['value'])

    def test_equal_occurrence_identical_position_is_not_a_conflict(self):
        one = event('one', '2026-10-08T10:00:00+08:00', 5)
        two = deepcopy(one); two['id'] = 'two'
        result = self.assert_entry_points_agree(state_with({'teaching_events': [one, two]}))
        self.assertFalse(result['conflict'])
        self.assertEqual('5', result['record']['chapter'])

    def test_undated_note_is_review_pointer_not_inferred_position_or_date(self):
        state = state_with({'teaching_cursor': {'topic': 'Chapter 6', 'at': '2026-10-08'},
                            'assistant_notes': [{'id': 'chapter-9-2099-12-30', 'text': 'Chapter 9 practiced'}]})
        result = self.assert_entry_points_agree(state)
        self.assertEqual('Chapter 6', result['record']['topic'])
        self.assertTrue(result['notes_review']['required'])
        self.assertEqual(['/courses/0/assistant_notes/0'], result['notes_review']['paths'])
        self.assertEqual('2026-10-08', result['as_of'])
        self.assertIn('newer_or_undated_notes_need_review_no_position_inferred', result['limitations'])

    def test_active_pending_follows_selected_event_older_question_is_only_a_pointer(self):
        current = event('new', '2026-10-08T10:00:00+08:00', 5)
        current['position']['pending_question'] = {'id': 'new-q', 'prompt': 'New question', 'answer_key': 'SECRET'}
        state = state_with({'teaching_cursor': {'topic': 'Chapter 4', 'at': '2026-10-01',
                                                'pending_check': {'prompt': 'OLD QUESTION'}},
                            'teaching_events': [current]})
        course = build_context(state, 'C')['sections']['course']
        cursor = course['teaching_cursor']
        self.assertEqual('New question', cursor['pending_questions'][0]['record']['prompt'])
        self.assertEqual('/courses/0/teaching_cursor/pending_check', cursor['prior_pending_questions'][0]['path'])
        self.assertNotIn('OLD QUESTION', json.dumps(course))
        self.assertNotIn('SECRET', json.dumps(course))
        self.assertEqual('OLD QUESTION', read_detail(state, 'path', cursor['prior_pending_questions'][0]['path'])['record']['prompt'])

    def test_missing_or_undated_position_does_not_mean_unlearned(self):
        missing = self.assert_entry_points_agree(state_with({}))
        self.assertEqual('missing', missing['status'])
        self.assertIn('saved_records_only_not_complete_chat_history', missing['limitations'])
        undated = teaching_progress({'teaching_cursor': {'topic': 'Known topic'}})
        self.assertIsNone(undated['as_of'])
        self.assertIn('undated_positions_cannot_be_ordered', undated['limitations'])

    def test_closed_questions_do_not_reopen_but_partial_answers_remain_pending(self):
        for status in ('answered', 'skipped', 'answer_shown', 'partially_answered_correct'):
            current = event('new', '2026-10-08T10:00:00+08:00', 5)
            current['position']['pending_question'] = {'id': 'q', 'prompt': 'Question', 'status': status}
            state = state_with({'teaching_events': [current]})
            cursor = build_context(state, 'C')['sections']['course']['teaching_cursor']
            with self.subTest(status=status):
                if status == 'partially_answered_correct':
                    self.assertEqual(status, cursor['pending_questions'][0]['record']['status'])
                else:
                    self.assertNotIn('pending_questions', cursor)
                    self.assertEqual(status, cursor['resolved_questions'][0]['status'])

    def test_new_actual_answer_context_uses_event_occurrence_and_original_pointer(self):
        current = event('new', '2026-10-08T10:00:00+08:00', 5)
        current['attempts'] = [{'id': 'new-answer', 'question': 'New question', 'user_answer': 'SECRET',
                                'conditions': {'assisted': True, 'answer_exposed': False}}]
        old = event('backfill', '2026-10-02T10:00:00+08:00', 3)
        old['attempts'] = [{'id': 'old-answer', 'user_answer': 'OLDER SECRET'}]
        state = state_with({'teaching_cursor': {'checks': [{'id': 'old-check', 'at': '2026-10-01',
                                                           'recorded_at': '2026-11-09', 'user_answer': 'OLD SECRET'}]},
                            'teaching_events': [current, old]})
        cursor = build_context(state, 'C')['sections']['course']['teaching_cursor']
        latest = cursor['latest_attempt_context']
        self.assertEqual('new-answer', latest['record']['id'])
        self.assertEqual('/courses/0/teaching_events/0/attempts/0', latest['path'])
        self.assertEqual(current['occurred_at'], latest['record']['occurred_at'])
        self.assertTrue(latest['record']['conditions']['assisted'])
        self.assertNotIn('independent', latest['record'])
        self.assertNotIn('SECRET', json.dumps(cursor))
        self.assertEqual(current['attempts'][0], read_detail(state, 'path', latest['path'])['record'])

    def test_new_event_evidence_search_and_correction_keep_original(self):
        current = event('new', '2026-10-08T10:00:00+08:00', 5)
        current['attempts'] = [{'id': 'answer-1', 'question': 'Derivative practice', 'user_answer': 'ORIGINAL'}]
        state = state_with({'teaching_events': [current]})
        path = '/courses/0/teaching_events/0/position'
        self.assertEqual(current['position'], records(state, 'C')[path])
        hits = search_evidence(state, 'C', 'Derivative practice')['matches']
        self.assertTrue(any('/teaching_events/0/attempts/0' == hit['path'].removeprefix('/courses/0') for hit in hits))
        draft, changed = apply_correction(state, 'C', {
            'id': 'correction', 'expected_revision': 4, 'target_path': path,
            'target_sha256': record_hash(current['position']), 'statement': 'Only practiced with help.'})
        self.assertTrue(changed)
        validate_preserved(state, draft)
        self.assertEqual(current, draft['courses'][0]['teaching_events'][0])
        self.assertEqual('located', inspect_memory(draft, 'C')['corrections'][0]['target_status'])

    def test_normal_save_guard_rejects_event_rewrite_or_removal(self):
        state = state_with({'teaching_events': [event('new', '2026-10-08T10:00:00+08:00', 5)]})
        for change in ('rewrite', 'remove', 'drop_course'):
            draft = deepcopy(state)
            if change == 'rewrite':
                draft['courses'][0]['teaching_events'][0]['position']['chapter'] = '6'
            elif change == 'remove':
                draft['courses'][0]['teaching_events'] = []
            else:
                draft['courses'] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_preserved(state, draft)


if __name__ == '__main__':
    unittest.main()
