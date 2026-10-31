"""An observation must belong to an actual, correctly scoped learner attempt."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from state_io import atomic, bridge, read, validate_draft


CONDITIONS = {'independent': True, 'assisted': False, 'answer_exposed': False}


def fixture(record=None):
    if record is None:
        record = {'id': 'Q1', 'topic': 'FOB', 'user_answer': 0,
                  'conditions': deepcopy(CONDITIONS), 'answered_at': '2026-10-03T10:00:00+08:00',
                  'assessment': {'correct': True}}
    return {'courses': [{'id': 'C', 'teaching_cursor': {'topic': 'Chapter 7', 'checks': [record]},
                         'topic_evidence': [{'id': 'prior', 'topic': 'FOB', 'level': 'explained',
                                             'at': '2026-10-01', 'source': {'kind': 'user_report'}}]}]}


def observation(delayed=False, path='/courses/0/teaching_cursor/checks/0'):
    row = {'id': 'new', 'topic': 'FOB', 'level': 'independent',
           'source': {'kind': 'observed_attempt', 'record_path': path}, 'conditions': deepcopy(CONDITIONS)}
    if delayed:
        row.update(level='delayed_pass', previous_event_id='prior', passed=True,
                   at='2026-10-03T10:00:00+08:00')
    return row


class TeachingEvidenceGuardTests(unittest.TestCase):
    def submit(self, initial, row=None, accept=True):
        before = deepcopy(initial)
        draft = deepcopy(initial)
        draft['courses'][0]['topic_evidence'].append(row or observation())
        if accept:
            validate_draft(initial, draft)
        else:
            with self.assertRaises(ValueError):
                validate_draft(initial, draft)
        self.assertEqual(initial, before)
        return draft

    def test_answer_and_grading_subpaths_cannot_be_presented_as_attempts(self):
        for field in ('answer_key', 'solution', 'rubric', 'assessment', 'feedback', 'user_answer'):
            with self.subTest(field=field):
                record = {'id': 'Q', field: {'answer': 42, 'conditions': deepcopy(CONDITIONS)}}
                self.submit(fixture(record), observation(path='/courses/0/teaching_cursor/checks/0/' + field), False)

    def test_grading_and_history_answers_do_not_turn_parent_into_attempt(self):
        for field in ('assessment', 'feedback', 'history', 'checks', 'parts'):
            with self.subTest(field=field):
                nested = {'answer': 42, 'topic': 'Bank reconciliation', 'answered_at': '2020-01-01'}
                record = {'id': 'Q', 'topic': 'FOB', 'answered_at': '2026-10-03T10:00:00+08:00',
                          'conditions': deepcopy(CONDITIONS), 'correct': True, field: nested}
                self.submit(fixture(record), observation(), False)
                self.submit(fixture(record), observation(True), False)

    def test_child_attempt_cannot_override_parent_conditions(self):
        for flag, value in (('assisted', True), ('independent', False), ('answer_exposed', True),
                            ('answer_previously_exposed', True), ('answer_seen_before_attempt', True), ('hints_used', 1)):
            for nested in (False, True):
                with self.subTest(flag=flag, nested=nested):
                    parent = {'id': 'Q', 'parts': [{'answer': 0, 'conditions': deepcopy(CONDITIONS)}]}
                    parent['conditions' if nested else flag] = {flag: value} if nested else value
                    self.submit(fixture(parent), observation(path='/courses/0/teaching_cursor/checks/0/parts/0'), False)

    def test_nested_parent_source_conditions_are_inherited(self):
        parent = {'source': {'conditions': {'assisted': True}},
                  'parts': [{'answer': 0, 'conditions': deepcopy(CONDITIONS)}]}
        self.submit(fixture(parent), observation(path='/courses/0/teaching_cursor/checks/0/parts/0'), False)

    def test_unassisted_child_does_not_inherit_sibling_help(self):
        parent = {'id': 'Q', 'topic': 'FOB', 'parts': [
            {'answer': False, 'conditions': deepcopy(CONDITIONS)}, {'answer': 10, 'assisted': True}]}
        self.submit(fixture(parent), observation(path='/courses/0/teaching_cursor/checks/0/parts/0'))

    def test_unknown_parent_conditions_do_not_override_explicit_child_evidence(self):
        parent = {'conditions': {'independent': None, 'assisted': None, 'answer_exposed': None},
                  'parts': [{'answer': 0, 'conditions': deepcopy(CONDITIONS)}]}
        self.submit(fixture(parent), observation(path='/courses/0/teaching_cursor/checks/0/parts/0'))

    def test_explicit_wrong_topic_is_rejected(self):
        initial = fixture()
        initial['courses'][0]['teaching_cursor']['checks'][0]['topic'] = 'Bank reconciliation'
        self.submit(initial, accept=False)

    def test_child_uses_nearest_recorded_topic_not_course_chapter_label(self):
        parent = {'topic': 'Bank reconciliation', 'parts': [{'answer': 0, 'conditions': deepcopy(CONDITIONS)}]}
        self.submit(fixture(parent), observation(path='/courses/0/teaching_cursor/checks/0/parts/0'), False)
        parent['parts'][0]['topic'] = 'FOB'
        self.submit(fixture(parent), observation(path='/courses/0/teaching_cursor/checks/0/parts/0'))

    def test_explicit_topic_ids_and_multiple_topic_scope(self):
        initial = fixture()
        initial['courses'][0]['teaching_cursor']['checks'][0]['topic_ids'] = ['ownership', 'fob']
        self.submit(initial, accept=False)
        row = observation(); row['topic_id'] = 'fob'
        self.submit(initial, row)
        row['topic_id'] = 'bank'
        self.submit(initial, row, False)
        initial['courses'][0]['teaching_cursor']['checks'][0].pop('topic_ids')
        initial['courses'][0]['teaching_cursor']['checks'][0].pop('topic')
        initial['courses'][0]['teaching_cursor']['checks'][0]['topics'] = ['FOB', 'Inventory']
        self.submit(initial)

    def test_missing_topic_is_not_inferred_from_question_text(self):
        initial = fixture(); record = initial['courses'][0]['teaching_cursor']['checks'][0]
        record.pop('topic'); record['prompt'] = 'Bank reconciliation'
        self.submit(initial)  # Text understanding is still the tutor's responsibility.

    def test_explicit_old_answer_time_cannot_be_relabelled_as_new_delayed_pass(self):
        initial = fixture(); record = initial['courses'][0]['teaching_cursor']['checks'][0]
        record.update(answered_at='2020-01-01', at='2020-01-01')
        self.submit(initial, observation(True), False)

    def test_independent_observation_cannot_relabel_known_answer_time(self):
        row = observation(); row['at'] = '2026-10-01'
        self.submit(fixture(), row, False)
        row['at'] = '2026-10-03T02:00:00Z'
        self.submit(fixture(), row)

    def test_same_answer_cannot_supply_prior_and_delayed_events(self):
        from evidence_bindings import _digest
        initial = fixture()
        old = observation(); old.update(id='old-attempt', at='2026-10-01')
        # Existing records are preserved even if the old date was inaccurate.
        initial['courses'][0]['topic_evidence'].append(old)
        initial['courses'][0]['evidence_bindings'] = {'old-attempt': {
            'status': 'bound', 'course_id': 'C', 'record_path_at_binding': old['source']['record_path'],
            'sha256': _digest(initial['courses'][0]['teaching_cursor']['checks'][0])}}
        row = observation(True); row['previous_event_id'] = 'old-attempt'
        draft = deepcopy(initial); draft['courses'][0]['topic_evidence'].append(row)
        with self.assertRaisesRegex(ValueError, '同一原答'):
            validate_draft(initial, draft)

    def test_distinct_actual_attempts_can_supply_prior_and_delayed_events(self):
        initial = fixture()
        first = deepcopy(initial['courses'][0]['teaching_cursor']['checks'][0])
        first.update(id='Q0', answered_at='2026-10-02', user_answer=1)
        initial['courses'][0]['teaching_cursor']['checks'].append(first)
        old = observation(path='/courses/0/teaching_cursor/checks/1')
        old.update(id='old-attempt', at='2026-10-02')
        initial = self.submit(initial, old)
        row = observation(True); row['previous_event_id'] = 'old-attempt'
        self.submit(initial, row)

    def test_unbound_existing_prior_path_is_not_guessed_after_potential_move(self):
        initial = fixture()
        first = deepcopy(initial['courses'][0]['teaching_cursor']['checks'][0])
        first.update(id='other-attempt', answered_at='2026-10-02', user_answer=1)
        initial['courses'][0]['teaching_cursor']['checks'].append(first)
        old = observation(path='/courses/0/teaching_cursor/checks/1')
        old.update(id='unbound-old', at='2026-10-02')
        initial['courses'][0]['topic_evidence'].append(old)
        row = observation(True); row['previous_event_id'] = 'unbound-old'
        self.submit(initial, row, False)

    def test_new_same_batch_prior_can_resolve_its_current_source(self):
        initial = fixture()
        first = deepcopy(initial['courses'][0]['teaching_cursor']['checks'][0])
        first.update(id='Q0', answered_at='2026-10-02', user_answer=1)
        initial['courses'][0]['teaching_cursor']['checks'].append(first)
        old = observation(path='/courses/0/teaching_cursor/checks/1')
        old.update(id='new-prior', at='2026-10-02')
        new = observation(True); new['previous_event_id'] = 'new-prior'
        draft = deepcopy(initial)
        draft['courses'][0]['topic_evidence'].extend([old, new])
        validate_draft(initial, draft)

    def test_updated_and_created_metadata_do_not_establish_attempt_time(self):
        for field in ('created_at', 'updated_at', 'recorded_at'):
            initial = fixture(); record = initial['courses'][0]['teaching_cursor']['checks'][0]
            record.pop('answered_at'); record[field] = '2026-10-03T10:00:00+08:00'
            self.submit(initial, observation(True), False)

    def test_parent_time_does_not_date_child_attempt(self):
        parent = {'answered_at': '2026-10-03T10:00:00+08:00',
                  'parts': [{'answer': 0, 'conditions': deepcopy(CONDITIONS), 'correct': True}]}
        self.submit(fixture(parent), observation(True, '/courses/0/teaching_cursor/checks/0/parts/0'), False)

    def test_explicit_legacy_attempt_time_fields_and_direct_event_at(self):
        for field in ('submitted_at', 'attempted_at', 'reviewed_at', 'at'):
            initial = fixture(); record = initial['courses'][0]['teaching_cursor']['checks'][0]
            record[field] = record.pop('answered_at')
            self.submit(initial, observation(True))

    def test_answer_time_precedes_unrelated_update_and_review_metadata(self):
        initial = fixture(); record = initial['courses'][0]['teaching_cursor']['checks'][0]
        record.update(reviewed_at='2026-10-04', at='2026-10-05', updated_at='2026-10-06')
        self.submit(initial, observation(True))
        row = observation(True); row['at'] = '2026-10-04'
        self.submit(initial, row, False)

    def test_equivalent_timezone_instant_matches(self):
        row = observation(True); row['at'] = '2026-10-03T02:00:00Z'
        self.submit(fixture(), row)

    def test_independent_incorrect_is_valid_but_delayed_pass_is_not(self):
        initial = fixture(); initial['courses'][0]['teaching_cursor']['checks'][0]['assessment']['correct'] = False
        self.submit(initial)
        self.submit(initial, observation(True), False)

    def test_unknown_grading_cannot_be_upgraded_to_delayed_pass(self):
        initial = fixture(); initial['courses'][0]['teaching_cursor']['checks'][0].pop('assessment')
        self.submit(initial)
        self.submit(initial, observation(True), False)

    def test_unchanged_historical_observation_is_not_reassessed_or_migrated(self):
        initial = fixture()
        initial['courses'][0]['topic_evidence'].append(observation(True, '/invalid/old/path'))
        validate_draft(initial, deepcopy(initial))

    def recorded_review(self, *, independent=True):
        import learning_loop as loop
        from test_learning_loop import AT, attempt, fixture as review_fixture
        state = review_fixture(1)
        package = loop.prepare(state, AT)
        loop.record_attempt(state, attempt(package, independent=independent,
                                           rating='good' if independent else None))
        return state

    def test_actual_record_attempt_can_save_observation_without_rewriting_source(self):
        initial = self.recorded_review()
        original = deepcopy(initial['review_attempts'][0])
        self.assertNotIn('course_id', original)
        draft = deepcopy(initial)
        row = observation(path='/review_attempts/0'); row['at'] = original['reviewed_at']
        draft['courses'][0]['topic_evidence'] = [row]
        temp_root = Path(__file__).resolve().parents[2] / 'tmp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='evidence-guards-', dir=temp_root) as folder:
            path = Path(folder) / 'state.json'
            atomic(path, initial)
            bridge(path, 'replace', {'expected': initial, 'state': draft})
            saved = read(path)
        self.assertEqual(saved['review_attempts'][0], original)
        self.assertEqual(saved['courses'][0]['topic_evidence'][0]['level'], 'independent')
        self.assertNotIn('mastery_verified', json.dumps(saved['courses'][0]))
        self.assertEqual(initial['review_attempts'][0], original)

    def test_actual_assisted_record_attempt_does_not_become_independent(self):
        initial = self.recorded_review(independent=False)
        initial['courses'][0]['topic_evidence'] = []
        self.submit(initial, observation(path='/review_attempts/0'), False)

    def test_actual_record_attempt_with_conflicting_course_is_rejected(self):
        initial = self.recorded_review()
        initial['courses'][0]['topic_evidence'] = []
        initial['review_attempts'][0]['course_id'] = 'ECON-DEMO'
        self.submit(initial, observation(path='/review_attempts/0'), False)

    def test_actual_review_rating_alone_does_not_mean_delayed_pass(self):
        initial = self.recorded_review()
        initial['courses'][0]['topic_evidence'] = [
            {'id': 'prior', 'topic': 'FOB', 'level': 'explained', 'at': '2026-01-01',
             'source': {'kind': 'user_report'}}]
        row = observation(True, '/review_attempts/0')
        row['at'] = initial['review_attempts'][0]['reviewed_at']
        self.submit(initial, row, False)


if __name__ == '__main__':
    unittest.main()
