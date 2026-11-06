"""Real material promotion across mock/review flows; synthetic files and state."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest

import exam_prep as exam
import learning_loop as loop
from learning_library import versions
from library_test_fixtures import temporary_directory
from test_exam_prep import AT, fixture, paper, attempt as mock_attempt
from test_learning_loop import fixture as review_fixture, attempt as review_attempt


class MaterialLearningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory('material-learning-')
        self.addCleanup(self.temp.cleanup)
        self.study = Path(self.temp.name) / 'Study'
        self.path = self.study / '_System/state.json'
        self.path.parent.mkdir(parents=True)
        self.state = fixture()
        old = self.material('SLIDES', 'COURSE/Lectures/lesson.txt', 'original lesson')
        self.incoming = self.material('SLIDES-v2', '_Inbox/lesson.txt', 'changed lesson')
        self.state['materials'] = [old]
        review_state = review_fixture(1, 2)
        self.state['settings'] = review_state['settings']
        self.state['mistakes'] = review_state['mistakes']
        item = review_state['review_queue'][0]
        item['course_id'] = 'COURSE'
        for variant in item['practice_variants']:
            variant['source']['material_id'] = 'SLIDES'
        self.state['review_queue'] = [item]
        exam.register_mock(self.state, paper(), AT)
        self.changed_at = AT + timedelta(hours=1)

    def material(self, identity, relative, text):
        path = self.study / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return {'id': identity, 'course_id': 'COURSE', 'category': 'Lectures',
                'filename': 'lesson.txt', 'relative_path': relative,
                'sha256': versions.sha(path), 'pages': 10,
                'moodle_file_sources': [{'module_id': 500, 'remote_filename': 'lesson.txt'}]}

    def promote(self):
        self.path.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
        result = versions.promote(self.path, self.incoming, at=self.changed_at.isoformat())
        self.assertEqual(result['archived_id'], 'SLIDES')
        self.state = json.loads(self.path.read_text(encoding='utf-8'))
        old, new = self.state['materials']
        self.assertEqual(old['superseded_by'], 'SLIDES-v2')
        self.assertEqual(new['supersedes'], 'SLIDES')
        self.assertTrue((self.study / old['relative_path']).is_file())

    def test_real_promotion_stales_paper_rejects_new_old_source_and_preserves_answers(self):
        exam.record_mock_attempt(self.state, mock_attempt(), AT + timedelta(minutes=40))
        historical_paper = deepcopy(self.state['mock_exams'][0])
        historical_answers = deepcopy(self.state['mock_attempts'])
        old_hash = self.state['materials'][0]['sha256']
        self.promote()
        self.assertEqual(self.state['materials'][0]['sha256'], old_hash)
        self.assertFalse(exam.mock_is_current(self.state, self.state['mock_exams'][0]))
        self.assertIn('已归档', exam.mock_stale_reason(self.state, self.state['mock_exams'][0]))
        self.assertIn('已归档', exam.render_mock(self.state, 'P1'))
        self.assertEqual(self.state['mock_exams'][0], historical_paper)
        self.assertEqual(self.state['mock_attempts'], historical_answers)
        new_paper = paper(); new_paper['id'] = 'P2'
        before = deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, '归档|替代'):
            exam.register_mock(self.state, new_paper, self.changed_at)
        self.assertEqual(self.state, before)

    def test_new_review_skips_archived_variants_until_explicit_new_source_verification(self):
        self.promote()
        result = loop.prepare(self.state, self.changed_at)
        self.assertEqual(result['items'], [])
        self.assertEqual(result['status'], 'needs_preparation')
        self.assertIn('归档', result['needs_preparation'][0]['reason'])
        previous = deepcopy(self.state['review_queue'][0]['practice_variants'])
        new_variant = deepcopy(previous[0])
        new_variant.update(id='V-NEW', prompt='A newly checked question on the revised lesson')
        new_variant['source']['material_id'] = 'SLIDES-v2'
        self.state['review_queue'][0]['practice_variants'].append(new_variant)
        result = loop.prepare(self.state, self.changed_at)
        self.assertEqual([i['variant_id'] for i in result['items']], ['V-NEW'])
        self.assertEqual(self.state['review_queue'][0]['practice_variants'][:2], previous)

    def test_pending_question_is_withdrawn_same_day_and_not_carried_next_day(self):
        package = loop.prepare(self.state, AT)
        original = deepcopy(self.state['review_sessions'][0]['items'][0])
        self.promote()
        result = loop.prepare(self.state, self.changed_at)
        self.assertEqual(result['items'][0]['status'], 'needs_review')
        self.assertNotIn('prompt', result['items'][0])
        self.assertEqual(result['committed_minutes'], 0)
        self.assertIn('题源待核验', loop.render_today_review(self.state, self.changed_at))
        saved = self.state['review_sessions'][0]['items'][0]
        self.assertEqual(saved['prompt'], original['prompt'])
        self.assertEqual(saved['source'], original['source'])
        tomorrow = loop.prepare(self.state, self.changed_at + timedelta(days=1))
        self.assertEqual(tomorrow['items'], [])
        self.assertEqual(tomorrow['status'], 'needs_preparation')
        self.assertEqual(package['items'][0]['variant_id'], original['variant_id'])

    def test_answered_review_and_history_remain_unchanged_after_update(self):
        package = loop.prepare(self.state, AT)
        loop.record_attempt(self.state, review_attempt(package, AT + timedelta(minutes=10)))
        records = deepcopy(self.state['review_attempts'])
        answered = deepcopy(self.state['review_sessions'][0]['items'][0])
        self.promote()
        loop.prepare(self.state, self.changed_at + timedelta(days=10))
        self.assertEqual(self.state['review_attempts'], records)
        self.assertEqual(self.state['review_sessions'][0]['items'][0], answered)
        self.assertEqual(records[0]['source']['material_id'], 'SLIDES')

    def test_answer_completed_before_update_can_be_recorded_after_update(self):
        package = loop.prepare(self.state, AT)
        self.promote()
        loop.prepare(self.state, self.changed_at)
        result = loop.record_attempt(self.state, review_attempt(package, AT + timedelta(minutes=10)))
        self.assertTrue(result['fsrs_rated'])
        self.assertEqual(self.state['review_attempts'][0]['source']['material_id'], 'SLIDES')
        exam.record_mock_attempt(self.state, mock_attempt(), self.changed_at + timedelta(minutes=10))
        self.assertEqual(self.state['mock_exams'][0]['questions'][0]['sources'][0]['material_id'], 'SLIDES')
        self.assertFalse(exam.mock_is_current(self.state, self.state['mock_exams'][0]))

    def test_new_independent_attempt_on_stale_source_is_rejected_without_losing_original(self):
        package = loop.prepare(self.state, AT)
        self.promote()
        loop.prepare(self.state, self.changed_at)
        before = deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, '归档|替代'):
            loop.record_attempt(self.state, review_attempt(package, self.changed_at + timedelta(minutes=5)))
        self.assertEqual(self.state, before)
        with self.assertRaisesRegex(ValueError, '归档|替代'):
            exam.record_mock_attempt(self.state, mock_attempt(submitted_at=(self.changed_at + timedelta(minutes=5)).isoformat()),
                                     self.changed_at + timedelta(minutes=10))
        self.assertEqual(self.state, before)
        result = loop.record_attempt(self.state, review_attempt(package, self.changed_at + timedelta(minutes=5),
            independent=False, rating=None))
        self.assertFalse(result['fsrs_rated'])
        self.assertEqual(self.state['review_attempts'][0]['source']['material_id'], 'SLIDES')

    def test_current_reverified_paper_can_be_registered_without_rewriting_old_paper(self):
        self.promote()
        old = deepcopy(self.state['mock_exams'][0])
        self.state['exam_profiles'][0]['topics'][0]['material_ids'] = ['SLIDES-v2']
        payload = paper(); payload['id'] = 'P2'
        for question in payload['questions']:
            question['sources'][0]['material_id'] = 'SLIDES-v2'
        exam.register_mock(self.state, payload, self.changed_at)
        self.assertTrue(exam.mock_is_current(self.state, self.state['mock_exams'][1]))
        self.assertEqual(self.state['mock_exams'][0], old)


if __name__ == '__main__':
    unittest.main()
