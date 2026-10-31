"""Evidence links must preserve original answers and their recorded conditions."""
from copy import deepcopy
import unittest

from cursor_evidence import active_snapshot
from state_io import validate_draft


def attempt(answer=0):
    return {'id': 'a1', 'user_answer': answer,
            'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False}}


def fixture():
    return {'courses': [{'id': 'C', 'teaching_cursor': {'topic': 'A', 'checks': [attempt()]}},
                        {'id': 'D', 'teaching_cursor': {'checks': [attempt()]}}]}


def observation():
    return {'id': 'e1', 'topic': 'A', 'level': 'independent',
            'source': {'kind': 'observed_attempt', 'record_path': '/courses/0/teaching_cursor/checks/0'},
            'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False}}


class TeachingProvenanceTests(unittest.TestCase):
    def submit(self, initial, row, accept=True):
        draft = deepcopy(initial)
        draft['courses'][0].setdefault('topic_evidence', []).append(row)
        if accept:
            validate_draft(initial, draft)
        else:
            with self.assertRaises(ValueError):
                validate_draft(initial, draft)
        return draft

    def test_original_zero_false_blank_and_structured_answers_exist(self):
        for answer in (0, False, '', [], {}, {'independent': False}):
            with self.subTest(answer=answer):
                initial = fixture()
                initial['courses'][0]['teaching_cursor']['checks'][0]['user_answer'] = answer
                self.submit(initial, observation())

    def test_pointer_must_exist_and_have_valid_type(self):
        for path in (None, 1, {}, '', 'courses/0', '/courses/0/teaching_cursor/checks/9',
                     '/courses/0/teaching_cursor/checks/01', '/courses/0/teaching_cursor/~bad'):
            with self.subTest(path=path):
                row = observation()
                row['source']['record_path'] = path
                self.submit(fixture(), row, False)

    def test_other_course_and_arbitrary_state_fields_are_rejected(self):
        initial = fixture()
        initial['settings'] = attempt()
        initial['courses'][0]['settings'] = attempt()
        for path in ('/courses/1/teaching_cursor/checks/0', '/settings', '/courses/0/settings'):
            with self.subTest(path=path):
                row = observation()
                row['source']['record_path'] = path
                self.submit(initial, row, False)

    def test_top_level_attempt_requires_same_course_owner(self):
        for owner, accepted in (('C', True), ('D', False), (None, False)):
            with self.subTest(owner=owner):
                initial, row = fixture(), observation()
                initial['review_attempts'] = [{**attempt(False), 'course_id': owner}]
                row['source']['record_path'] = '/review_attempts/0'
                self.submit(initial, row, accepted)

    def test_question_or_solution_is_not_an_actual_answer(self):
        for source in ({'prompt': 'Q'}, {'answer': None}, {'answer_key': {'answer': 42}}):
            with self.subTest(source=source):
                initial = fixture()
                initial['courses'][0]['teaching_cursor']['checks'][0] = {
                    **source, 'conditions': attempt()['conditions']}
                self.submit(initial, observation(), False)

    def test_source_conditions_must_be_explicit(self):
        for key in ('independent', 'assisted', 'answer_exposed'):
            with self.subTest(key=key):
                initial = fixture()
                initial['courses'][0]['teaching_cursor']['checks'][0]['conditions'].pop(key)
                self.submit(initial, observation(), False)

    def test_recorded_help_and_exposure_cannot_be_denied_by_observation(self):
        for extra in ({'assisted': True}, {'independent': False}, {'answer_exposed': True},
                      {'answer_previously_exposed': True}, {'hints_used': 1},
                      {'source': {'kind': 'user_report'}}):
            with self.subTest(extra=extra):
                initial = fixture()
                initial['courses'][0]['teaching_cursor']['checks'][0].update(extra)
                self.submit(initial, observation(), False)

    def test_pointer_escape_and_nested_answer(self):
        initial, row = fixture(), observation()
        initial['courses'][0]['teaching_cursor']['checks'][0] = {'parts': {'a/b~c': attempt()}}
        row['source']['record_path'] = '/courses/0/teaching_cursor/checks/0/parts/a~1b~0c'
        self.submit(initial, row)

    def delayed(self):
        initial, row = fixture(), observation()
        initial['courses'][0]['teaching_cursor']['checks'][0].update(answered_at='2026-01-03', correct=True)
        initial['courses'][0]['topic_evidence'] = [
            {'id': 'earlier', 'topic': 'A', 'level': 'explained',
             'source': {'kind': 'user_report'}, 'at': '2026-01-02'}]
        row.update(level='delayed_pass', previous_event_id='earlier', passed=True, at='2026-01-03')
        return initial, row

    def test_delayed_check_requires_existing_same_topic_prior_observation(self):
        initial, row = self.delayed()
        self.submit(initial, row)
        row['previous_event_id'] = 'nonexistent'
        self.submit(initial, row, False)
        row['previous_event_id'] = 'earlier'
        row['topic'] = 'Different'
        self.submit(initial, row, False)

    def test_delayed_check_time_must_be_valid_and_later(self):
        for stamp in ('2026-01-01', '2026-01-02', 'not-a-date', 0, None):
            with self.subTest(stamp=stamp):
                initial, row = self.delayed()
                row['at'] = stamp
                self.submit(initial, row, False)
        initial, row = self.delayed()
        initial['courses'][0]['topic_evidence'][0].pop('at')
        self.submit(initial, row, False)

    def test_delayed_pass_cannot_contradict_recorded_failure(self):
        initial, row = self.delayed()
        initial['courses'][0]['teaching_cursor']['checks'][0]['assessment'] = {'correct': False}
        self.submit(initial, row, False)

    def test_observation_append_only_comparison_preserves_bool_type(self):
        initial = self.submit(fixture(), observation())
        draft = deepcopy(initial)
        draft['courses'][0]['topic_evidence'][0]['conditions']['independent'] = 1
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)

    def test_archived_original_answer_zero_cannot_become_false(self):
        initial = fixture()
        old = initial['courses'][0]['teaching_cursor']
        old['last_answer'] = 0
        draft = deepcopy(initial)
        new = draft['courses'][0]['teaching_cursor']
        new['history'] = [deepcopy(active_snapshot(old))]
        new.update(topic='B')
        new.pop('last_answer')
        validate_draft(initial, draft)
        new['history'][0]['last_answer'] = False
        with self.assertRaises(ValueError):
            validate_draft(initial, draft)

    def test_correction_must_refer_to_existing_observation(self):
        initial = fixture()
        row = {'id': 'e1', 'topic': 'A', 'level': 'explained', 'source': {'kind': 'user_report'}}
        initial = self.submit(initial, row)
        correction = {**row, 'id': 'e2', 'corrects': 'e1'}
        self.submit(initial, correction)
        correction['corrects'] = 'missing'
        self.submit(initial, correction, False)

    def test_unchanged_legacy_observation_is_not_reassessed(self):
        initial = fixture()
        initial['courses'][0]['topic_evidence'] = [
            {'id': 'legacy', 'topic': 'A', 'level': 'independent', 'source': {'legacy': True}}]
        validate_draft(initial, deepcopy(initial))


if __name__ == '__main__':
    unittest.main()
