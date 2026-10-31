"""Real state bridge regressions for evidence types and legacy cursor history."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from cursor_evidence import active_snapshot
from learning_library.context import build_context
from state_io import atomic, bridge, read, same_json


def fixture():
    return {'revision': 1,
            'courses': [{'id': 'C', 'current_topic': 'Chapter 6', 'assistant_notes': [],
                         'teaching_cursor': {'topic': 'Chapter 6', 'chapter': 6, 'next_step': 'old question',
                                             'checks': [{'id': 'earlier', 'answer': False}]}}],
            'mistakes': [{'id': 'm1', 'course_id': 'C', 'user_answer': 0,
                          'source': {'material_id': 'm6', 'pages': [1]}, 'resolution_status': 'pending'}],
            'review_attempts': [{'id': 'a1', 'course_id': 'C', 'user_answer': 0}],
            'learning_feedback': [{'id': 'f1', 'course_id': 'C', 'actual_minutes': 1}],
            'mock_attempts': [{'id': 'mock1', 'course_id': 'C', 'answer': False}]}


class TeachingPreservationFixesTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[2] / 'tmp' / 'teaching-preservation-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='records ', dir=base)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'Study/_System/state.json'
        self.original = Path(self.temp.name) / 'Study/C/Lectures/original.txt'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'Original material: do not modify.\n')

    def save(self, initial, draft, *, accept=True, expected=None):
        atomic(self.path, initial)
        previous = self.path.with_name('state.previous.json')
        before, backup = self.path.read_bytes(), previous.read_bytes() if previous.exists() else None
        original_bytes = self.original.read_bytes()
        payload = {'expected': deepcopy(initial) if expected is None else expected, 'state': draft}
        if accept:
            bridge(self.path, 'replace', payload)
            saved = read(self.path)
            self.assertEqual(saved['revision'], initial['revision'] + 1)
            self.assertTrue(same_json(read(previous), initial))
            self.assertEqual(self.original.read_bytes(), original_bytes)
            return saved
        with self.assertRaises(ValueError):
            bridge(self.path, 'replace', payload)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(previous.read_bytes() if previous.exists() else None, backup)
        self.assertEqual(self.original.read_bytes(), original_bytes)

    def test_mistake_answer_cannot_change_type_or_blank_response(self):
        for field in ('user_answer', 'answer', 'user_answer_or_method', 'user_attempt',
                      'submitted_answer', 'response', 'response_text'):
            for old, replacement in ((0, False), (False, 0), (1, True), ('', 'invented'), ([], [1]), ({}, {'x': 1})):
                with self.subTest(field=field, old=old):
                    initial = fixture()
                    initial['mistakes'][0][field] = deepcopy(old)
                    draft = deepcopy(initial)
                    draft['mistakes'][0][field] = replacement
                    self.save(initial, draft, accept=False)

    def test_nested_answers_and_fsrs_history_preserve_types(self):
        initial = fixture()
        initial['mistakes'][0]['user_answer'] = {'rows': [{'amount': 0, 'selected': False}]}
        initial['review_queue'] = [{'id': 'r1', 'fsrs': {'history': [{'rating': 1}]}}]
        draft = deepcopy(initial)
        draft['mistakes'][0]['user_answer']['rows'][0]['amount'] = False
        self.save(initial, draft, accept=False)
        draft = deepcopy(initial)
        draft['review_queue'][0]['fsrs']['history'][0]['rating'] = True
        self.save(initial, draft, accept=False)

    def test_immutable_learning_events_reject_equal_boolean_numbers(self):
        for collection, field, replacement in (('review_attempts', 'user_answer', False),
                                                ('learning_feedback', 'actual_minutes', True),
                                                ('mock_attempts', 'answer', 0)):
            with self.subTest(collection=collection):
                initial = fixture(); draft = deepcopy(initial)
                draft[collection][0][field] = replacement
                self.save(initial, draft, accept=False)

    def test_source_scalar_and_list_types_cannot_be_rewritten(self):
        initial = fixture()
        initial['mistakes'][0]['source']['offset'] = 0
        for field, replacement in (('pages', [True]), ('offset', False)):
            with self.subTest(field=field):
                draft = deepcopy(initial)
                draft['mistakes'][0]['source'][field] = replacement
                self.save(initial, draft, accept=False)

    def test_expected_base_is_type_sensitive_before_any_write(self):
        initial = fixture(); draft = deepcopy(initial); expected = deepcopy(initial)
        expected['mistakes'][0]['user_answer'] = False
        self.save(initial, draft, accept=False, expected=expected)
        expected = deepcopy(initial); expected['revision'] = True
        self.save(initial, draft, accept=False, expected=expected)

    def test_owned_receipts_and_exam_profiles_remain_type_sensitive(self):
        for field in ('operation_receipts', 'refresh_receipts', 'exam_profiles'):
            with self.subTest(field=field):
                initial = fixture(); initial[field] = [{'id': 'r1', 'sequence': 1}]
                draft = deepcopy(initial); draft[field][0]['sequence'] = True
                self.save(initial, draft, accept=False)

    def test_legitimate_progress_source_enrichment_and_unknown_fill_still_work(self):
        initial = fixture()
        initial['mistakes'].append({'id': 'unknown', 'user_answer': None, 'source': None})
        draft = deepcopy(initial)
        draft['courses'][0]['assistant_notes'].append('New progress note')
        draft['courses'][0]['teaching_cursor']['next_step'] = 'next question in chapter'
        draft['mistakes'][0]['resolution_status'] = 'resolved'
        draft['mistakes'][0]['source']['verified_page'] = 2
        draft['mistakes'][1].update(user_answer=False, source={'material_id': 'm6'})
        saved = self.save(initial, draft)
        self.assertIs(type(saved['mistakes'][0]['user_answer']), int)
        self.assertIs(saved['mistakes'][1]['user_answer'], False)
        self.assertIs(saved['courses'][0]['teaching_cursor']['checks'][0]['answer'], False)
        self.assertTrue(same_json(saved['review_attempts'], initial['review_attempts']))

    def test_pending_structured_answers_cannot_gain_or_change_content(self):
        for field in ('user_answer', 'answer', 'user_answer_or_method', 'user_attempt',
                      'submitted_answer', 'response', 'response_text'):
            for old, replacement in (([], ['new']), ({}, {'new': 'response'}),
                                     ({'a': 1}, {'a': 1, 'b': 2}), ([1], [1, 2])):
                with self.subTest(field=field, old=old):
                    initial = fixture()
                    initial['courses'][0]['teaching_cursor']['pending_question'] = {
                        'id': 'q1', 'prompt': 'Original question', field: old}
                    draft = deepcopy(initial)
                    draft['courses'][0]['teaching_cursor']['pending_question'][field] = replacement
                    self.save(initial, draft, accept=False)

    def test_pending_record_can_gain_annotations_new_subquestions_and_first_answer(self):
        initial = fixture()
        initial['courses'][0]['teaching_cursor']['pending_question'] = {
            'id': 'q1', 'parts': [{'id': 'p1', 'user_answer': []}, {'id': 'p2', 'user_answer': None}]}
        draft = deepcopy(initial)
        current = draft['courses'][0]['teaching_cursor']['pending_question']
        current['parts'][1]['user_answer'] = 0
        current['parts'].append({'id': 'p3', 'prompt': 'Next subquestion'})
        current['feedback'] = 'Additional note'
        saved = self.save(initial, draft)
        result = saved['courses'][0]['teaching_cursor']['pending_question']
        self.assertEqual(result['parts'][0]['user_answer'], [])
        self.assertIs(type(result['parts'][1]['user_answer']), int)
        self.assertEqual(len(result['parts']), 3)

    def test_evaluated_status_cannot_be_rewritten_in_place(self):
        for old in ('correct', 'incorrect', 'partially_answered_correct'):
            for replacement in ('correct', 'incorrect', 'graded', 'completed'):
                if replacement == old:
                    continue
                with self.subTest(old=old, replacement=replacement):
                    initial = fixture()
                    initial['courses'][0]['teaching_cursor']['pending_question'] = {
                        'id': 'q1', 'user_answer': 0, 'status': old}
                    draft = deepcopy(initial)
                    draft['courses'][0]['teaching_cursor']['pending_question']['status'] = replacement
                    self.save(initial, draft, accept=False)

    def test_non_evaluation_status_progression_remains_writable(self):
        for old, following in (('pending', 'answered'), ('answered', 'graded'),
                               ('partially_answered', 'completed')):
            with self.subTest(old=old):
                initial = fixture()
                initial['courses'][0]['teaching_cursor']['pending_question'] = {
                    'id': 'q1', 'user_answer': 0, 'status': old}
                draft = deepcopy(initial)
                draft['courses'][0]['teaching_cursor']['pending_question']['status'] = following
                saved = self.save(initial, draft)
                self.assertEqual(saved['courses'][0]['teaching_cursor']['pending_question']['status'], following)

    def test_incorrect_status_cannot_be_upgraded_to_delayed_pass_in_same_save(self):
        initial = fixture()
        conditions = {'independent': True, 'assisted': False, 'answer_exposed': False}
        course = initial['courses'][0]
        course['topic_evidence'] = [{'id': 'e0', 'topic': 'FOB', 'level': 'explained',
                                     'at': '2026-10-01T10:00:00+08:00', 'source': {'kind': 'user_report'}}]
        course['teaching_cursor']['pending_question'] = {
            'id': 'q1', 'topic': 'FOB', 'user_answer': 0, 'status': 'incorrect',
            'conditions': conditions, 'answered_at': '2026-10-03T10:00:00+08:00'}
        draft = deepcopy(initial)
        draft['courses'][0]['teaching_cursor']['pending_question']['status'] = 'correct'
        draft['courses'][0]['topic_evidence'].append({
            'id': 'e1', 'topic': 'FOB', 'level': 'delayed_pass', 'passed': True,
            'previous_event_id': 'e0', 'at': '2026-10-03T10:00:00+08:00',
            'conditions': conditions,
            'source': {'kind': 'observed_attempt', 'record_path': '/courses/0/teaching_cursor/pending_question'}})
        self.save(initial, draft, accept=False)
        # The observation itself is valid when its already-recorded verdict was
        # correct; rejection above must come from rewriting the old verdict.
        valid = deepcopy(initial)
        valid['courses'][0]['teaching_cursor']['pending_question']['status'] = 'correct'
        self.save(valid, deepcopy(draft))

    def legacy(self):
        initial = fixture()
        cursor = initial['courses'][0]['teaching_cursor']
        cursor['history'] = {'old_note': 'Keep prior chapter', 'response': 0,
                             'pending_check': {'prompt': 'historical question', 'answer': False}}
        cursor['pending_check'] = {'id': 'q6', 'prompt': 'old question', 'user_answer': 0,
                                    'assisted': True, 'source': {'material_id': 'm6', 'pages': [1]}}
        return initial

    def migrated(self, initial):
        draft = deepcopy(initial)
        old = initial['courses'][0]['teaching_cursor']
        current = draft['courses'][0]['teaching_cursor']
        current['history'] = [deepcopy(old['history']), deepcopy(active_snapshot(old))]
        current['checks'].append(deepcopy(old['pending_check']))
        current.pop('pending_check')
        current.update(topic='Chapter 7', chapter=7, next_step='new question')
        draft['courses'][0]['current_topic'] = 'Chapter 7'
        draft['courses'][0]['review_backlog'] = [{'id': 'r6', 'topic': 'Chapter 6', 'status': 'pending'}]
        return draft

    def test_legacy_dictionary_history_migrates_losslessly_during_real_save(self):
        initial = self.legacy()
        saved = self.save(initial, self.migrated(initial))
        cursor = saved['courses'][0]['teaching_cursor']
        self.assertTrue(same_json(cursor['history'][0], initial['courses'][0]['teaching_cursor']['history']))
        self.assertTrue(same_json(cursor['history'][1], active_snapshot(initial['courses'][0]['teaching_cursor'])))
        self.assertEqual(cursor['checks'][-1]['user_answer'], 0)
        self.assertTrue(same_json(saved['mistakes'], initial['mistakes']))
        context = build_context(saved, 'C')['sections']['course']['teaching_cursor']
        self.assertNotIn('pending_questions', context)
        self.assertEqual(context['topic'], 'Chapter 7')

    def test_legacy_history_untouched_for_unrelated_update(self):
        initial = self.legacy(); draft = deepcopy(initial)
        draft['courses'][0]['assistant_notes'].append('unrelated note')
        saved = self.save(initial, draft)
        self.assertTrue(same_json(saved['courses'][0]['teaching_cursor']['history'],
                                  initial['courses'][0]['teaching_cursor']['history']))

    def test_legacy_migration_rejects_missing_reordered_or_modified_evidence(self):
        initial = self.legacy()
        for kind in ('missing_old', 'missing_snapshot', 'reorder', 'changed_old_type', 'changed_snapshot'):
            with self.subTest(kind=kind):
                draft = self.migrated(initial)
                history = draft['courses'][0]['teaching_cursor']['history']
                if kind == 'missing_old': history.pop(0)
                elif kind == 'missing_snapshot': history.pop()
                elif kind == 'reorder': history.reverse()
                elif kind == 'changed_old_type': history[0]['response'] = False
                else: history[1]['pending_check']['user_answer'] = False
                self.save(initial, draft, accept=False)

    def test_legacy_migration_does_not_replace_pending_answer_checks_requirement(self):
        initial = self.legacy(); draft = self.migrated(initial)
        draft['courses'][0]['teaching_cursor']['checks'].pop()
        self.save(initial, draft, accept=False)

    def test_after_migration_history_is_append_only_and_can_advance_again(self):
        initial = self.legacy(); migrated = self.save(initial, self.migrated(initial))
        draft = deepcopy(migrated)
        draft['courses'][0]['teaching_cursor']['history'][0]['response'] = False
        self.save(migrated, draft, accept=False)
        draft = deepcopy(migrated)
        cursor = draft['courses'][0]['teaching_cursor']
        cursor['history'].append(deepcopy(active_snapshot(cursor)))
        cursor.update(topic='Chapter 8', chapter=8)
        saved = self.save(migrated, draft)
        self.assertEqual(saved['courses'][0]['teaching_cursor']['chapter'], 8)
        self.assertTrue(same_json(saved['mistakes'], initial['mistakes']))


if __name__ == '__main__':
    unittest.main()
