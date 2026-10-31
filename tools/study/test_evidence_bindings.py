"""Stable answer resolution through real saves, reordering and question changes."""
from copy import deepcopy
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
