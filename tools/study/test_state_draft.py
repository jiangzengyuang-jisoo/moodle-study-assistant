"""Generic full-state drafts preserve records and original evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from state_io import atomic, bridge, read, validate_draft


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'Study/_System/state.json'
        self.state = {
            'revision': 1,
            'courses': [{'id': 'C', 'moodle_course_ids': [1], 'current_topic': 'Chapter 1',
                         'assignment_focus': {'task_ids': ['T1'], 'topic': 'old'},
                         'teaching_cursor': {'stage': 'review', 'topic': 'Chapter 1', 'pages': [4],
                                             'pending_question': {'prompt': 'previous prompt'}},
                         'learning_progress': {'chapter1': {'independent': True}}}],
            'materials': [{'id': 'OLD', 'sha256': 'old-hash', 'relative_path': 'C/Lectures/original.pdf'}],
            'mistakes': [{'id': 'M', 'user_answer': 'original answer', 'resolution_status': 'pending',
                          'source': {'material_id': 'OLD', 'pages': [4]},
                          'occurrences': [{'at': '2026-10-30', 'question': 'original question'}]}],
            'review_queue': [{'id': 'R', 'mistake_id': 'M', 'priority': 'High',
                              'fsrs': {'history': [{'rating': 'good'}]}}],
            'review_attempts': [{'attempt_id': 'A', 'answer': 'first answer'}],
            'learning_feedback': [{'feedback_id': 'F', 'completed': 'chapter 1'}],
            'mock_attempts': [{'id': 'MA', 'answer': 'first mock answer'}],
            'review_sessions': [{'session_id': 'S', 'items': [{'id': 'I', 'status': 'pending'}]}],
            'mock_exams': [{'id': 'EX', 'questions': [{'id': 'Q', 'prompt': 'original question'}]}],
            'tasks': [{'id': 'T1', 'course_id': 'C', 'title': 'Homework', 'status': 'In progress',
                       'remaining_workload_minutes': 20, 'deadline_id': 'D1',
                       'source': {'url': 'https://school.example/mod/assign/view.php?id=1'}}],
            'deadlines': [{'id': 'D1', 'task_id': 'T1', 'date': '2026-11-01', 'time': None,
                           'source': {'post_id': 10}}],
            'discussion_notes': [{'id': 'N1', 'course_id': 'C', 'author_role': 'teacher',
                                  'content': 'Original teacher instruction', 'status': 'pending',
                                  'source': {'post_id': 10}}],
        }
        atomic(self.path, self.state)

    def reject(self, draft):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            bridge(self.path, 'replace', {'expected': self.state, 'state': draft})
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.path.with_name('state.previous.json').exists())

    def test_id_only_records_cannot_erase_any_protected_collection(self):
        for name in ('courses', 'materials', 'mistakes', 'review_queue', 'review_attempts',
                     'learning_feedback', 'mock_attempts', 'review_sessions', 'mock_exams',
                     'tasks', 'deadlines', 'discussion_notes'):
            with self.subTest(collection=name):
                draft = deepcopy(self.state)
                row = draft[name][0]
                draft[name][0] = {k: v for k, v in row.items() if k in {'id', 'attempt_id', 'feedback_id', 'session_id'}}
                self.reject(draft)

    def test_task_deadline_and_discussion_collections_cannot_be_cleared(self):
        for name in ('tasks', 'deadlines', 'discussion_notes'):
            for replacement in ([], None):
                with self.subTest(collection=name, replacement=replacement):
                    draft = deepcopy(self.state)
                    draft[name] = replacement
                    self.reject(draft)

    def test_task_completion_deadline_change_and_appended_notes_remain_allowed(self):
        draft = deepcopy(self.state)
        draft['tasks'][0].update(status='Completed', submission_status='submitted',
                                 remaining_workload_minutes=0)
        draft['tasks'].append({'id': 'T2', 'course_id': 'C', 'status': None})
        draft['deadlines'][0].update(date='2026-11-03', time='23:59')
        draft['deadlines'][0]['source']['extension_post_id'] = 11
        draft['deadlines'].append({'id': 'D2', 'task_id': 'T2', 'date': '2026-11-07'})
        draft['discussion_notes'][0]['status'] = 'reviewed'
        draft['discussion_notes'][0]['corrections'] = [{'post_id': 11, 'detail': 'Teacher extended the deadline'}]
        draft['discussion_notes'].append({'id': 'N2', 'course_id': 'C', 'content': 'New instruction',
                                         'source': {'post_id': 11}})
        bridge(self.path, 'replace', {'expected': self.state, 'state': draft})
        saved = read(self.path)
        self.assertEqual(saved['tasks'][0]['status'], 'Completed')
        self.assertEqual(saved['tasks'][0]['remaining_workload_minutes'], 0)
        self.assertEqual(saved['deadlines'][0]['date'], '2026-11-03')
        self.assertEqual(saved['discussion_notes'][0]['content'], 'Original teacher instruction')
        self.assertEqual(saved['review_attempts'], self.state['review_attempts'])
        self.assertEqual(read(self.path.with_name('state.previous.json')), self.state)

    def test_task_deadline_and_discussion_sources_cannot_be_rewritten(self):
        for name in ('tasks', 'deadlines', 'discussion_notes'):
            with self.subTest(collection=name):
                draft = deepcopy(self.state)
                draft[name][0]['source'] = {'post_id': 999}
                self.reject(draft)

    def test_clear_nested_progress_and_remove_occurrence_are_rejected(self):
        for empty in (None, {}, [], ''):
            with self.subTest(value=empty):
                draft = deepcopy(self.state)
                draft['courses'][0]['teaching_cursor'] = empty
                self.reject(draft)
        draft = deepcopy(self.state)
        draft['mistakes'][0]['occurrences'] = []
        self.reject(draft)

    def test_evidence_cannot_be_rewritten_even_with_same_shape(self):
        for field, value in [('user_answer', 'rewritten'), ('source', {'material_id': 'NEW', 'pages': [4]})]:
            draft = deepcopy(self.state); draft['mistakes'][0][field] = value
            self.reject(draft)
        draft = deepcopy(self.state); draft['review_queue'][0]['fsrs']['history'][0]['rating'] = 'easy'
        self.reject(draft)
        draft = deepcopy(self.state); draft['materials'][0]['sha256'] = 'new-hash'
        self.reject(draft)
        draft = deepcopy(self.state); draft['learning_feedback'][0]['completed'] = 'chapter 2'
        self.reject(draft)

    def test_duplicate_ids_and_missing_history_are_rejected(self):
        draft = deepcopy(self.state); draft['mistakes'].append(deepcopy(draft['mistakes'][0]))
        self.reject(draft)
        draft = deepcopy(self.state); draft['review_sessions'] = []
        self.reject(draft)

    def test_legitimate_course_selection_progress_and_new_evidence_are_allowed(self):
        draft = deepcopy(self.state)
        draft['courses'][0]['moodle_course_ids'] = []
        draft['courses'][0]['assignment_focus'] = {'task_ids': ['T2'], 'topic': 'new'}
        draft['courses'][0]['current_topic'] = 'Chapter 2'
        old_cursor = self.state['courses'][0]['teaching_cursor']
        draft['courses'][0]['teaching_cursor']['history'] = [
            deepcopy({k: v for k, v in old_cursor.items() if k not in {'checks', 'history'}})]
        draft['courses'][0]['teaching_cursor']['topic'] = 'Chapter 2'
        draft['courses'][0]['teaching_cursor']['pages'] = [7, 8]
        draft['courses'][0]['teaching_cursor']['pending_question'] = None
        draft['courses'][0]['learning_progress']['chapter2'] = {'independent': False}
        draft['mistakes'][0]['resolution_status'] = 'resolved'
        draft['mistakes'][0]['occurrences'].append({'at': '2026-10-31', 'question': 'new question'})
        draft['mistakes'].append({'id': 'M2', 'source': None})
        bridge(self.path, 'replace', {'expected': self.state, 'state': draft})
        saved = read(self.path)
        self.assertEqual(saved['courses'][0]['teaching_cursor']['topic'], 'Chapter 2')
        self.assertEqual(saved['mistakes'][0]['source'], self.state['mistakes'][0]['source'])
        self.assertEqual(saved['review_queue'], self.state['review_queue'])
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(read(self.path.with_name('state.previous.json')), self.state)

    def test_existing_null_can_be_filled_without_making_it_evidence(self):
        current = {'courses': [{'id': 'C', 'teaching_cursor': None, 'learning_progress': None}]}
        draft = {'courses': [{'id': 'C', 'teaching_cursor': {'topic': 'Chapter 1'},
                              'learning_progress': {'verified': False}}]}
        validate_draft(current, draft)

    def test_source_enrichment_preserves_original_and_allows_verified_details(self):
        draft = deepcopy(self.state)
        draft['mistakes'][0]['source']['detail'] = 'verified physical page'
        draft['mistakes'][0]['source']['pages'].append(5)
        bridge(self.path, 'replace', {'expected': self.state, 'state': draft})
        self.assertEqual(read(self.path)['mistakes'][0]['source']['pages'], [4, 5])
        current = {'mistakes': [{'id': 'M', 'sources': [{'material_id': 'PPT-1'}]}]}
        enriched = {'mistakes': [{'id': 'M', 'sources': [{'material_id': 'PPT-1', 'pages': [4]},
                                                        {'material_id': 'PPT-2', 'pages': [2]}]}]}
        validate_draft(current, enriched)
        enriched['mistakes'][0]['sources'][0]['material_id'] = 'WRONG'
        with self.assertRaises(ValueError):
            validate_draft(current, enriched)


if __name__ == '__main__': unittest.main()
