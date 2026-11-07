"""Stable answer resolution through real saves, reordering and question changes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evidence_bindings import resolve_observation
from state_io import atomic, bridge, read, validate_draft


def fixture():
    return {'revision': 1, 'courses': [
        {'id': 'C', 'teaching_cursor': {'topic': 'FOB', 'checks': [], 'pending_check': {
            'id': 'q1', 'topic': 'FOB', 'prompt': 'Q1', 'user_answer': 0,
            'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False}}}},
        {'id': 'D', 'teaching_cursor': {'checks': []}}],
        'mistakes': [{'id': 'm1', 'course_id': 'C', 'user_answer': False,
                      'source': {'material_id': 'old-slides', 'pages': [6]}}]}


def add_observation(state):
    draft = deepcopy(state)
    draft['courses'][0]['topic_evidence'] = [{'id': 'e1', 'topic': 'FOB', 'level': 'independent',
        'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False},
        'source': {'kind': 'observed_attempt', 'record_path': '/courses/0/teaching_cursor/pending_check'}}]
    return draft


class EvidenceBindingsTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[2] / 'tmp/teaching-binding-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'Study/_System/state.json'

    def save(self, initial, draft):
        atomic(self.path, initial)
        bridge(self.path, 'replace', {'expected': deepcopy(initial), 'state': draft})
        return read(self.path)

    def bound(self):
        initial = fixture()
        return self.save(initial, add_observation(initial))

    def status_bound(self, *, legacy=False, nested=False, notes=False, status='answered'):
        initial = fixture()
        cursor = initial['courses'][0]['teaching_cursor']
        record = cursor['pending_check']
        record.update(status=status, answered_at='2026-10-30T10:00:00+08:00',
                      source={'material_id': 'old-slides', 'pages': [6]})
        if nested:
            child = {'user_answer': record.pop('user_answer'), 'conditions': record['conditions']}
            record['parts'] = [child]
        pointer = '/courses/0/teaching_cursor/pending_check'
        if notes:
            initial['courses'][0]['assistant_notes'] = [cursor.pop('pending_check')]
            pointer = '/courses/0/assistant_notes/0'
        draft = add_observation(initial)
        draft['courses'][0]['topic_evidence'][0]['source']['record_path'] = pointer + ('/parts/0' if nested else '')
        draft['courses'][0]['topic_evidence'][0]['at'] = '2026-10-30T10:00:00+08:00'
        saved = self.save(initial, draft)
        if legacy:
            # Construct the exact pre-fix wire format independently of production
            # helpers, including status in both source and ancestor fingerprints.
            def fingerprint(row):
                fields = sorted(set(row) - {'stage', 'next_step', 'updated_at'})
                content = {key: row[key] for key in fields}
                digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                    allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
                return {'fields': fields, 'sha256': digest}
            binding = saved['courses'][0]['evidence_bindings']['e1']
            binding.update(fingerprint(child if nested else record))
            binding['ancestors'] = [fingerprint({key: value for key, value in record.items()
                                               if key != 'parts'})] if nested else []
        return saved

    def reject(self, initial, draft):
        atomic(self.path, initial)
        before, before_draft = self.path.read_bytes(), deepcopy(draft)
        with self.assertRaises(ValueError):
            bridge(self.path, 'replace', {'expected': deepcopy(initial), 'state': draft})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(draft, before_draft)

    def moved(self, initial):
        draft = deepcopy(initial)
        cursor = draft['courses'][0]['teaching_cursor']
        cursor['checks'].append(deepcopy(cursor['pending_check']))
        cursor['pending_check'] = {'id': 'q2', 'prompt': 'Q2', 'user_answer': 1}
        return draft

    def test_pending_to_checks_preserves_original_source_and_old_mistakes(self):
        initial = self.bound(); saved = self.save(initial, self.moved(initial))
        resolved = resolve_observation(saved, 'C', 'e1', include_record=True)
        self.assertEqual(resolved['record_path'], '/courses/0/teaching_cursor/checks/0')
        self.assertEqual(resolved['record']['prompt'], 'Q1')
        self.assertIs(type(resolved['record']['user_answer']), int)
        self.assertEqual(saved['mistakes'], initial['mistakes'])
        self.assertEqual(saved['courses'][0]['topic_evidence'], initial['courses'][0]['topic_evidence'])
        self.assertEqual(set(saved['courses'][0]['evidence_bindings']['e1']),
                         {'status', 'course_id', 'record_path_at_binding', 'fields', 'ancestors', 'sha256'})

    def test_course_reorder_and_answer_archive_resolve_together(self):
        initial = self.bound(); draft = self.moved(initial)
        draft['courses'].reverse()
        saved = self.save(initial, draft)
        resolved = resolve_observation(saved, 'C', 'e1', include_record=True)
        self.assertEqual(resolved['record_path'], '/courses/1/teaching_cursor/checks/0')
        self.assertEqual(resolved['record']['id'], 'q1')

    def test_question_replacement_cannot_retarget_binding(self):
        initial = self.bound(); draft = self.moved(initial)
        draft['courses'][0]['evidence_bindings']['e1']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.save(initial, draft)
        self.assertEqual(read(self.path), initial)

    def test_same_answer_in_another_course_is_never_used(self):
        state = self.bound()
        original = state['courses'][0]['teaching_cursor'].pop('pending_check')
        state['courses'][1]['teaching_cursor']['checks'].append(original)
        self.assertEqual(resolve_observation(state, 'C', 'e1')['status'], 'source_missing')

    def test_same_answer_but_different_question_is_not_original(self):
        state = self.bound()
        state['courses'][0]['teaching_cursor']['pending_check']['prompt'] = 'Q2'
        self.assertEqual(resolve_observation(state, 'C', 'e1')['status'], 'source_missing')

    def test_routing_only_changes_keep_binding(self):
        initial = self.bound(); draft = deepcopy(initial)
        draft['courses'][0]['teaching_cursor']['pending_check'].update(
            stage='reviewed', next_step='move on', updated_at='2026-10-31')
        saved = self.save(initial, draft)
        self.assertEqual(resolve_observation(saved, 'C', 'e1')['status'], 'resolved')

    def test_later_assessment_can_be_added_without_rewriting_original(self):
        initial = self.bound(); draft = deepcopy(initial)
        draft['courses'][0]['teaching_cursor']['pending_check']['assessment'] = {'correct': False}
        saved = self.save(initial, draft)
        resolved = resolve_observation(saved, 'C', 'e1', include_record=True)
        self.assertEqual(resolved['status'], 'resolved')
        self.assertIs(resolved['record']['assessment']['correct'], False)

    def test_bound_answered_to_graded_saves_through_real_bridge(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                initial = self.status_bound(legacy=legacy)
                draft = deepcopy(initial)
                draft['courses'][0]['teaching_cursor']['pending_check'].update(
                    status='graded', assessment={'correct': False})
                saved = self.save(initial, draft)
                resolved = resolve_observation(saved, 'C', 'e1', include_record=True)
                self.assertEqual(resolved['status'], 'resolved')
                self.assertEqual(resolved['record']['status'], 'graded')
                self.assertIs(resolved['record']['assessment']['correct'], False)
                self.assertEqual(initial['courses'][0]['topic_evidence'], saved['courses'][0]['topic_evidence'])
                self.assertEqual(resolved['record']['user_answer'], 0)
                self.assertNotIn('status', saved['courses'][0]['evidence_bindings']['e1']['fields'])
                bad = deepcopy(saved)
                bad['courses'][0]['teaching_cursor']['pending_check']['assessment']['correct'] = True
                self.reject(saved, bad)

    def test_legacy_parent_status_advances_without_losing_child_binding(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                initial = self.status_bound(legacy=legacy, nested=True)
                draft = deepcopy(initial)
                draft['courses'][0]['teaching_cursor']['pending_check'].update(
                    status='graded', assessment={'correct': True})
                saved = self.save(initial, draft)
                self.assertEqual(resolve_observation(saved, 'C', 'e1')['status'], 'resolved')
                self.assertNotIn('status', saved['courses'][0]['evidence_bindings']['e1']['ancestors'][0]['fields'])

    def test_bound_pending_answer_can_advance_through_answered_and_graded(self):
        for legacy in (False, True):
            for nested in (False, True):
                with self.subTest(legacy=legacy, nested=nested):
                    current = self.status_bound(legacy=legacy, nested=nested, status='pending')
                    for status in ('answered', 'graded'):
                        draft = deepcopy(current)
                        draft['courses'][0]['teaching_cursor']['pending_check']['status'] = status
                        current = self.save(current, draft)
                        resolved = resolve_observation(current, 'C', 'e1', include_record=True)
                        self.assertEqual(resolved['status'], 'resolved')
                        self.assertIs(type(resolved['record']['user_answer']), int)
                        self.assertEqual(resolved['record']['user_answer'], 0)

    def test_legacy_conversion_is_repeatable_and_does_not_change_base(self):
        initial = self.status_bound(legacy=True, nested=True)
        before = deepcopy(initial)
        draft = deepcopy(initial)
        draft['courses'][0]['teaching_cursor']['pending_check']['status'] = 'graded'
        validate_draft(initial, draft)
        once = deepcopy(draft)
        validate_draft(initial, draft)
        self.assertEqual(draft, once)
        self.assertEqual(initial, before)
        self.save(initial, draft)

    def test_legacy_binding_without_fields_verifies_before_conversion(self):
        initial = self.status_bound(legacy=True)
        initial['courses'][0]['evidence_bindings']['e1'].pop('fields')
        draft = deepcopy(initial)
        draft['courses'][0]['teaching_cursor']['pending_check']['status'] = 'graded'
        saved = self.save(initial, draft)
        self.assertEqual(resolve_observation(saved, 'C', 'e1')['status'], 'resolved')

    def test_verdicts_remain_protected_outside_current_cursor(self):
        for nested in (False, True):
            for legacy in (False, True):
                for status in ('correct', 'incorrect', 'partially_answered_correct',
                               'passed', 'failed', 'custom unknown status'):
                    with self.subTest(nested=nested, legacy=legacy, status=status):
                        initial = self.status_bound(legacy=legacy, nested=nested, notes=True, status=status)
                        draft = deepcopy(initial)
                        draft['courses'][0]['assistant_notes'][0]['status'] = 'graded'
                        self.reject(initial, draft)

    def test_verdict_added_after_binding_cannot_later_be_rewritten(self):
        for nested in (False, True):
            for recorded_status, replacement in (('correct', 'incorrect'), ('passed', 'failed'),
                                                   ('custom unknown status', 'graded')):
                with self.subTest(nested=nested, status=recorded_status):
                    initial = self.status_bound(nested=nested, notes=True)
                    draft = deepcopy(initial)
                    draft['courses'][0]['assistant_notes'][0]['status'] = recorded_status
                    graded = self.save(initial, draft)
                    draft = deepcopy(graded)
                    draft['courses'][0]['assistant_notes'][0]['status'] = replacement
                    self.reject(graded, draft)
                    unchanged = self.save(graded, deepcopy(graded))
                    self.assertEqual(resolve_observation(unchanged, 'C', 'e1')['status'], 'resolved')

    def test_workflow_conversion_never_resigns_missing_old_answer(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                initial = self.status_bound(legacy=True, nested=nested)
                record = initial['courses'][0]['teaching_cursor']['pending_check']
                (record['parts'][0] if nested else record)['user_answer'] = 'already damaged'
                draft = deepcopy(initial)
                draft['courses'][0]['teaching_cursor']['pending_check']['status'] = 'graded'
                self.reject(initial, draft)

    def test_grading_cannot_change_original_answer_question_source_or_conditions(self):
        initial = self.status_bound(legacy=True)
        for field, value in (('user_answer', False), ('prompt', 'different question'),
                             ('source', {'material_id': 'NEW'}),
                             ('conditions', {'independent': False, 'assisted': True, 'answer_exposed': False})):
            with self.subTest(field=field):
                draft = deepcopy(initial)
                draft['courses'][0]['teaching_cursor']['pending_check'].update(status='graded', **{field: value})
                self.reject(initial, draft)

    def test_tool_owned_bindings_cannot_be_forged_or_removed(self):
        initial = self.status_bound(legacy=True, nested=True)
        for mutation in ('fields', 'sha256', 'ancestors', 'entry', 'map'):
            with self.subTest(mutation=mutation):
                draft = deepcopy(initial)
                course = draft['courses'][0]
                binding = course['evidence_bindings']['e1']
                if mutation == 'fields': binding['fields'] = []
                elif mutation == 'sha256': binding['sha256'] = '0' * 64
                elif mutation == 'ancestors': binding['ancestors'] = []
                elif mutation == 'entry': course['evidence_bindings'].pop('e1')
                else: course.pop('evidence_bindings')
                self.reject(initial, draft)

    def test_same_save_grading_and_delayed_pass_resolve_upgraded_prior(self):
        initial = self.status_bound(legacy=True)
        draft = deepcopy(initial)
        course = draft['courses'][0]
        course['teaching_cursor']['pending_check']['status'] = 'graded'
        fresh = deepcopy(course['teaching_cursor']['pending_check'])
        fresh.update(id='q2', user_answer=1, status='correct', answered_at='2026-11-01T10:00:00+08:00')
        course['assistant_notes'] = [fresh]
        observation = deepcopy(course['topic_evidence'][0])
        observation.update(id='e2', level='delayed_pass', passed=True,
                           previous_event_id='e1', at=fresh['answered_at'])
        observation['source']['record_path'] = '/courses/0/assistant_notes/0'
        course['topic_evidence'].append(observation)
        saved = self.save(initial, draft)
        for observation_id in ('e1', 'e2'):
            self.assertEqual(resolve_observation(saved, 'C', observation_id)['status'], 'resolved')

    def test_failed_new_observation_does_not_partially_convert_legacy_bindings(self):
        initial = self.status_bound(legacy=True)
        for malformed in ({'id': 'bad', 'level': 'independent', 'source': {}},
                          {'level': 'independent'}, {'id': 'bad', 'source': 'invalid'}, None):
            with self.subTest(observation=malformed):
                draft = deepcopy(initial)
                draft['courses'][0]['teaching_cursor']['pending_check']['status'] = 'graded'
                draft['courses'][0]['topic_evidence'].append(malformed)
                self.reject(initial, draft)

    def test_identical_subanswer_in_new_parent_question_cannot_steal_binding(self):
        initial = fixture()
        cursor = initial['courses'][0]['teaching_cursor']
        child = cursor['pending_check']
        child.pop('id'); child.pop('prompt'); child.pop('topic')
        cursor['pending_check'] = {'id': 'Q1', 'prompt': 'First question', 'topic': 'FOB', 'parts': [child]}
        draft = add_observation(initial)
        draft['courses'][0]['topic_evidence'][0]['source']['record_path'] += '/parts/0'
        initial = self.save(initial, draft)
        draft = deepcopy(initial)
        cursor = draft['courses'][0]['teaching_cursor']
        cursor['checks'].append(deepcopy(cursor['pending_check']))
        cursor['pending_check']['id'] = 'Q2'
        cursor['pending_check']['prompt'] = 'Second question'
        saved = self.save(initial, draft)
        resolved = resolve_observation(saved, 'C', 'e1')
        self.assertEqual(resolved['record_path'], '/courses/0/teaching_cursor/checks/0/parts/0')

    def test_legacy_unbound_is_preserved_but_never_guessed_from_live_path(self):
        initial = add_observation(fixture())
        saved = self.save(initial, self.moved(initial))
        self.assertEqual(resolve_observation(saved, 'C', 'e1')['status'], 'unresolved_legacy')
        self.assertEqual(saved['courses'][0]['topic_evidence'], initial['courses'][0]['topic_evidence'])

    def test_validation_is_repeatable_without_mutating_base(self):
        initial = fixture(); before = deepcopy(initial); draft = add_observation(initial)
        validate_draft(initial, draft)
        once = deepcopy(draft)
        validate_draft(initial, draft)
        self.assertEqual(draft, once)
        self.assertEqual(initial, before)

    def test_cli_resolves_moved_source_without_writing_any_file(self):
        initial = self.bound(); saved = self.save(initial, self.moved(initial))
        folder = Path(self.temp.name)
        before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('library.py')),
                                 '--state', str(self.path), 'evidence-source', '--course', 'C',
                                 '--observation', 'e1'], capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['record']['id'], 'q1')
        after = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
