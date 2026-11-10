"""New saved answers remain compatible with explicit, validated observations."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evidence_bindings import resolve_observation
from state_io import bridge, read, validate_draft
from teaching_progress import save_teaching
from test_teaching_save import fixture, payload


class TeachingEventEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'state.json'
        self.path.write_text(json.dumps(fixture()), encoding='utf-8')

    def save_answer(self, conditions=None):
        request = payload(); request['event_type'] = 'attempt'
        request['attempts'] = [{'id': 'A1', 'question': 'Synthetic limit question',
                                'user_answer': 'My unchanged original answer',
                                'topic': 'limits', 'answered_at': '2026-02-01T11:59:00+08:00',
                                'conditions': conditions if conditions is not None else {
                                    'independent': True, 'assisted': False, 'answer_exposed': False},
                                'source_turn_id': 'TURN-2'}]
        save_teaching(self.path, 'C', request)
        return read(self.path)

    def observation(self, state):
        draft = deepcopy(state)
        draft['courses'][0]['topic_evidence'] = [{
            'id': 'OBS1', 'topic': 'limits', 'level': 'independent',
            'at': '2026-02-01T11:59:00+08:00',
            'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False},
            'source': {'kind': 'observed_attempt',
                       'record_path': '/courses/0/teaching_events/0/attempts/0'}}]
        return draft

    def test_explicit_independent_answer_binds_and_cli_resolves_without_auto_rating(self):
        state = self.save_answer()
        self.assertNotIn('topic_evidence', state['courses'][0])
        self.assertEqual(fixture()['fsrs'], state['fsrs'])
        draft = self.observation(state)
        bridge(self.path, 'replace', {'expected': state, 'state': draft})
        bound = read(self.path)
        result = resolve_observation(bound, 'C', 'OBS1', include_record=True)
        self.assertEqual('resolved', result['status'])
        self.assertEqual(state['courses'][0]['teaching_events'][0]['attempts'][0], result['record'])
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        command = [sys.executable, str(Path(__file__).with_name('library.py')), '--state', str(self.path),
                   'evidence-source', '--course', 'C', '--observation', 'OBS1']
        response = subprocess.run(command, text=True, encoding='utf-8', capture_output=True, env=env, check=True)
        resolved = json.loads(response.stdout)
        self.assertEqual('resolved', resolved['status'])
        self.assertEqual('My unchanged original answer', resolved['record']['user_answer'])
        self.assertEqual(fixture()['fsrs'], bound['fsrs'])

    def test_unknown_assisted_and_exposed_answers_cannot_be_declared_independent(self):
        cases = [
            {'assisted': False, 'answer_exposed': False},
            {'independent': None, 'assisted': False, 'answer_exposed': False},
            {'independent': False, 'assisted': False, 'answer_exposed': False},
            {'independent': True, 'assisted': True, 'answer_exposed': False},
            {'independent': True, 'assisted': False, 'answer_exposed': True}]
        for conditions in cases:
            with self.subTest(conditions=conditions):
                self.path.write_text(json.dumps(fixture()), encoding='utf-8')
                state = self.save_answer(conditions)
                with self.assertRaises(ValueError):
                    validate_draft(state, self.observation(state))
                self.assertNotIn('topic_evidence', read(self.path)['courses'][0])

    def test_observation_must_keep_original_answer_scope_and_time(self):
        state = self.save_answer()
        for key, value in (('topic', 'integration'), ('at', '2026-02-02T11:59:00+08:00')):
            with self.subTest(key=key):
                draft = self.observation(state)
                draft['courses'][0]['topic_evidence'][0][key] = value
                with self.assertRaises(ValueError):
                    validate_draft(state, draft)
        # The event's current teaching topic is a different, unrelated scope;
        # the original question's explicitly recorded scope remains authoritative.
        validate_draft(state, self.observation(state))


if __name__ == '__main__':
    unittest.main()
