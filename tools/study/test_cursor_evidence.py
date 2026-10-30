"""Real learner attempts survive current-question clearing, switching and aliases."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cursor_evidence import PENDING_KEYS
from learning_library.context import build_context
from state_io import atomic, bridge, read
from test_library_context import fixture

HERE = Path(__file__).resolve().parent


class CursorEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cursor 学习 ', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'Study/_System/state.json'

    def initial(self, key='pending_question', answer='original learner answer'):
        row = {'id': 'Q', 'prompt': 'Original question', 'source': {'material_id': 'OLD', 'pages': [4]},
               'answer': answer, 'assessment': {'correct': False, 'comment': 'original assessment'},
               'assisted': True, 'status': 'partially_answered', 'answered_at': '2026-10-30T10:00:00+08:00'}
        value = [row] if key.endswith('s') else row
        return {'revision': 1, 'courses': [{'id': 'C', 'teaching_cursor': {
            'stage': 'practice', key: value, 'checks': [{'id': 'earlier', 'answer': 'old evidence'}]}}]}, row

    def submit(self, initial, draft, accept=True):
        atomic(self.path, initial)
        before = self.path.read_bytes()
        if accept:
            bridge(self.path, 'replace', {'expected': initial, 'state': draft})
            return read(self.path)
        with self.assertRaises(ValueError):
            bridge(self.path, 'replace', {'expected': initial, 'state': draft})
        self.assertEqual(before, self.path.read_bytes())

    def test_every_supported_alias_requires_archive_before_clearing(self):
        for key in PENDING_KEYS:
            with self.subTest(key=key):
                initial, row = self.initial(key)
                draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
                cursor[key] = None
                self.submit(initial, draft, False)
                cursor['checks'].append(deepcopy(row))
                result = self.submit(initial, draft)
                self.assertEqual(result['courses'][0]['teaching_cursor']['checks'][-1], row)

    def test_original_library_fixture_archives_before_advancing_all_aliases(self):
        for key in PENDING_KEYS:
            with self.subTest(key=key):
                initial = fixture(); cursor = initial['courses'][0]['teaching_cursor']
                row = cursor.pop('pending_check'); cursor[key] = [row] if key.endswith('s') else row
                draft = deepcopy(initial); next_cursor = draft['courses'][0]['teaching_cursor']
                next_cursor['checks'].append(deepcopy(row)); next_cursor.pop(key)
                next_cursor['next_step'] = 'next independent question'
                result = self.submit(initial, draft)
                self.assertIn('PARTIAL_ANSWER', json.dumps(result))
                self.assertNotIn('pending_questions', build_context(result, 'A')['sections']['course']['teaching_cursor'])

    def test_pure_unanswered_questions_can_be_replaced_or_cleared(self):
        for key in PENDING_KEYS:
            for field in ('answer_key', 'correct_answer', 'expected_answer'):
                with self.subTest(key=key, field=field):
                    row = {'id': 'Q', 'prompt': 'unanswered', field: 'solution', 'answer': None}
                    initial = {'courses': [{'id': 'C', 'teaching_cursor': {key: [row] if key.endswith('s') else row}}]}
                    draft = deepcopy(initial); draft['courses'][0]['teaching_cursor'][key] = None
                    self.submit(initial, draft)
                    draft['courses'][0]['teaching_cursor'] = {key: {'id': 'NEW', 'prompt': 'new question'}}
                    self.submit(initial, draft)

    def test_nested_solution_and_marking_data_are_not_learner_attempts(self):
        for key in ('answer_key', 'rubric', 'solution', 'marking_scheme', 'answer_check', 'grading_key'):
            with self.subTest(key=key):
                record = {'prompt': 'not attempted', key: {'answer': 42, 'status': 'correct'}}
                initial = {'courses': [{'id': 'C', 'teaching_cursor': {'pending_question': record}}]}
                draft = deepcopy(initial); draft['courses'][0]['teaching_cursor']['pending_question'] = None
                self.submit(initial, draft)
                initial['courses'][0]['teaching_cursor']['pending_question']['parts'] = [{'user_answer': 0}]
                self.submit(initial, draft, False)

    def test_zero_false_blank_and_empty_response_are_not_no_attempt(self):
        for answer in (0, False, '', [], {}):
            with self.subTest(answer=answer):
                initial, row = self.initial(answer=answer)
                for key in ('assessment', 'status', 'answered_at'):
                    row.pop(key)
                draft = deepcopy(initial); draft['courses'][0]['teaching_cursor']['pending_question'] = None
                self.submit(initial, draft, False)
                draft['courses'][0]['teaching_cursor']['checks'].append(deepcopy(row))
                self.submit(initial, draft)

    def test_archive_must_preserve_answer_question_source_assessment_and_type(self):
        initial, row = self.initial(answer=0)
        for key, replacement in [('answer', False), ('prompt', 'different'), ('source', {}),
                                 ('assessment', {}), ('assisted', False)]:
            with self.subTest(field=key):
                draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
                archived = deepcopy(row); archived[key] = replacement
                cursor['checks'].append(archived); cursor['pending_question'] = None
                self.submit(initial, draft, False)

    def test_archive_in_other_course_or_unrelated_field_is_not_accepted(self):
        initial, row = self.initial()
        for place in ('other_course', 'unrelated', 'wrapper'):
            with self.subTest(place=place):
                draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
                cursor['pending_question'] = None
                if place == 'other_course':
                    draft['courses'].append({'id': 'OTHER', 'teaching_cursor': {'checks': [deepcopy(row)]}})
                elif place == 'unrelated':
                    cursor['temporary_backup'] = deepcopy(row)
                else:
                    cursor['checks'].append({'archive': deepcopy(row)})
                self.submit(initial, draft, False)

    def test_alias_migration_keeps_same_attempt_without_duplicate_history(self):
        initial, row = self.initial()
        draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
        cursor.pop('pending_question'); cursor['pending_checks'] = [deepcopy(row)]
        result = self.submit(initial, draft)
        self.assertEqual(result['courses'][0]['teaching_cursor']['checks'], initial['courses'][0]['teaching_cursor']['checks'])

    def test_answer_addition_and_grading_are_allowed_but_rewrite_needs_archive(self):
        initial, row = self.initial()
        row.pop('answer'); row.pop('assessment'); row.pop('answered_at'); row['status'] = 'pending'
        draft = deepcopy(initial); current = draft['courses'][0]['teaching_cursor']['pending_question']
        current.update(answer='first answer', assessment={'correct': False}, status='answered')
        self.submit(initial, draft)
        initial = deepcopy(draft); draft['courses'][0]['teaching_cursor']['pending_question']['answer'] = 'second answer'
        self.submit(initial, draft, False)
        draft['courses'][0]['teaching_cursor']['checks'].append(deepcopy(initial['courses'][0]['teaching_cursor']['pending_question']))
        self.submit(initial, draft)
        initial = deepcopy(draft); draft['courses'][0]['teaching_cursor']['pending_question']['status'] = 'graded'
        draft['courses'][0]['teaching_cursor']['pending_question']['grading_note'] = 'additional note'
        self.submit(initial, draft)

    def test_multi_question_list_requires_preservation_of_each_removed_attempt(self):
        initial, row = self.initial('pending_questions')
        other = deepcopy(row); other['id'] = 'Q2'; other['answer'] = 'other answer'
        initial['courses'][0]['teaching_cursor']['pending_questions'].append(other)
        draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
        cursor['pending_questions'] = [deepcopy(other)]
        self.submit(initial, draft, False)
        cursor['checks'].append(deepcopy(row))
        self.submit(initial, draft)
        cursor['pending_questions'] = None
        self.submit(initial, draft, False)
        cursor['checks'].append(deepcopy(other))
        self.submit(initial, draft)

    def test_partial_then_completed_answer_then_next_question_preserves_both_attempts(self):
        initial, row = self.initial(answer='partial')
        draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
        cursor['checks'].append(deepcopy(row))
        cursor['pending_question']['answer'] = 'completed'
        self.submit(initial, draft)
        initial = deepcopy(draft); cursor = draft['courses'][0]['teaching_cursor']
        cursor['checks'].append(deepcopy(cursor['pending_question']))
        cursor['pending_question'] = {'id': 'Q2', 'prompt': 'next independent question'}
        saved = self.submit(initial, draft)
        attempts = saved['courses'][0]['teaching_cursor']['checks'][-2:]
        self.assertEqual([row['id'] for row in attempts], ['Q', 'Q'])
        self.assertEqual([row['answer'] for row in attempts], ['partial', 'completed'])
        # A later draft may append another event for that question, not rewrite
        # the two observations or discard one as a duplicate question id.
        initial = deepcopy(draft)
        draft['courses'][0]['teaching_cursor']['checks'].append(deepcopy(attempts[-1]))
        self.submit(initial, draft)

    def test_check_history_is_append_only_even_when_question_ids_repeat(self):
        initial, row = self.initial()
        initial['courses'][0]['teaching_cursor']['checks'] += [deepcopy(row), deepcopy(row)]
        for mutation in ('edit', 'remove', 'reorder'):
            with self.subTest(mutation=mutation):
                draft = deepcopy(initial); history = draft['courses'][0]['teaching_cursor']['checks']
                if mutation == 'edit': history[-1]['answer'] = 'rewritten'
                elif mutation == 'remove': history.pop()
                else: history.reverse()
                self.submit(initial, draft, False)

    def test_nested_handoff_pending_attempt_uses_same_course_history(self):
        initial, row = self.initial()
        cursor = initial['courses'][0]['teaching_cursor']
        cursor['handoff'] = {'pending_check': cursor.pop('pending_question'), 'next_step': 'finish current'}
        draft = deepcopy(initial); new = draft['courses'][0]['teaching_cursor']
        new['handoff']['pending_check'] = None
        self.submit(initial, draft, False)
        new['checks'].append(deepcopy(row))
        self.submit(initial, draft)

    def test_nested_partial_answer_and_status_only_evidence_are_protected(self):
        for record in ({'prompt': 'multi-part', 'parts': [{'prompt': 'a', 'user_answer': ''}]},
                       {'prompt': 'question', 'status': 'answered'},
                       {'prompt': 'older record missing raw answer', 'assessment': {'correct': False}},
                       {'prompt': 'older record', 'feedback': ''},
                       {'prompt': 'older record', 'fsrs_rating': 0}):
            initial = {'courses': [{'id': 'C', 'teaching_cursor': {'pending_question': record}}]}
            draft = deepcopy(initial); draft['courses'][0]['teaching_cursor']['pending_question'] = None
            self.submit(initial, draft, False)

    def test_existing_checks_cannot_be_removed_to_make_room_for_archive(self):
        initial, row = self.initial()
        draft = deepcopy(initial); cursor = draft['courses'][0]['teaching_cursor']
        cursor['pending_question'] = None; cursor['checks'] = [deepcopy(row)]
        self.submit(initial, draft, False)

    def test_actual_cli_rejects_loss_without_changing_state(self):
        initial, _ = self.initial()
        atomic(self.path, initial); before = self.path.read_bytes()
        draft = deepcopy(initial); draft['courses'][0]['teaching_cursor']['pending_question'] = None
        result = subprocess.run([sys.executable, str(HERE / 'state_io.py'), str(self.path), 'replace'],
                                input=json.dumps({'expected': initial, 'state': draft}),
                                text=True, encoding='utf-8', capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__': unittest.main()
