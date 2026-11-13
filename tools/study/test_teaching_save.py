"""Teaching persistence regressions run only against synthetic temporary states."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from teaching_progress import save_teaching
from learning_library.context import teaching_progress
from learning_library.memory import validate_preserved


def fixture():
    return {'revision': 7, 'settings': {}, 'courses': [
        {'id': 'C', 'name': 'Synthetic course', 'current_topic': 'Assignment chapter 2',
         'teaching_cursor': {'topic': 'Chapter 3', 'chapter': '3',
                             'updated_at': '2026-01-01T00:00:00Z',
                             'next_step': 'practice', 'stage': 'explained'},
         'mastery': {'legacy': 'unchanged'}}, {'id': 'OTHER', 'name': 'Other'}],
        'mistakes': [{'id': 'M', 'user_answer': 'original mistake'}],
        'review_attempts': [{'id': 'R', 'user_answer': 'original answer'}],
        'review_queue': [], 'fsrs': {'legacy': 'unchanged'}}


def payload(revision=7, event_id='E1', occurred_at='2026-02-01T12:00:00+08:00'):
    return {'id': event_id, 'expected_revision': revision, 'occurred_at': occurred_at,
            'event_type': 'explanation', 'affects_current': True,
            'source': {'kind': 'chat', 'thread_id': 'CHAT-C', 'turn_ids': ['TURN-1', 'TURN-2']},
            'position': {'topic': 'Chapter 5 integration', 'chapter': '5', 'section': '5.1',
                         'stage': 'awaiting_independent_attempt', 'next_step': 'Try a new integral',
                         'pending_question': {'id': 'Q1', 'prompt': 'A synthetic integral question',
                                              'status': 'pending'}}}


class TeachingSaveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'state.json'
        self.original = fixture()
        self.path.write_text(json.dumps(self.original), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8-sig'))

    def test_save_verifies_readback_and_keeps_assignment_and_evidence(self):
        result = save_teaching(self.path, 'C', payload())
        self.assertTrue(result['saved']); self.assertTrue(result['readback_verified'])
        self.assertTrue(result['active_advanced'])
        self.assertEqual('5', result['current_progress']['record']['chapter'])
        self.assertEqual(8, result['revision'])
        current = self.read()
        self.assertEqual(self.original, json.loads(self.path.with_name('state.previous.json').read_text()))
        for key in ('mistakes', 'review_attempts', 'review_queue', 'fsrs'):
            self.assertEqual(self.original[key], current[key])
        for key in ('teaching_cursor', 'current_topic', 'mastery'):
            self.assertEqual(self.original['courses'][0][key], current['courses'][0][key])
        self.assertEqual(self.original['courses'][1], current['courses'][1])
        self.assertFalse(result['mastery_updated']); self.assertFalse(result['network_started'])

    def test_retries_are_idempotent_even_after_rereading_revision(self):
        save_teaching(self.path, 'C', payload())
        before = self.path.read_bytes()
        for revision in (7, 8):
            result = save_teaching(self.path, 'C', payload(revision))
            self.assertTrue(result['saved']); self.assertTrue(result['already_saved'])
            self.assertFalse(result['active_advanced'])
            self.assertEqual(before, self.path.read_bytes())
        changed = payload(8); changed['position']['chapter'] = '6'
        with self.assertRaisesRegex(ValueError, 'different content'):
            save_teaching(self.path, 'C', changed)
        self.assertEqual(before, self.path.read_bytes())

    def test_stale_revision_rejects_without_overwrite(self):
        save_teaching(self.path, 'C', payload())
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '状态已更新'):
            save_teaching(self.path, 'C', payload(event_id='E2'))
        self.assertEqual(before, self.path.read_bytes())

    def test_old_backfill_and_homework_answer_do_not_take_current_position(self):
        save_teaching(self.path, 'C', payload())
        old = payload(8, 'OLD', '2026-01-15T12:00:00+08:00')
        old['event_type'] = 'history_recovery'; old['position']['chapter'] = '4'
        old['source']['reviewed_at'] = datetime.now(timezone.utc).isoformat()
        result = save_teaching(self.path, 'C', old)
        self.assertTrue(result['saved']); self.assertFalse(result['active_advanced'])
        self.assertEqual('5', result['current_progress']['record']['chapter'])
        answer = payload(9, 'ANSWER', '2026-03-01T12:00:00+08:00')
        answer.update(event_type='answer_exposure', affects_current=False)
        answer['position']['chapter'] = '8'
        answer['position']['pending_question']['status'] = 'answer_shown'
        result = save_teaching(self.path, 'C', answer)
        self.assertFalse(result['active_advanced'])
        self.assertEqual('5', result['current_progress']['record']['chapter'])

    def test_return_to_old_chapter_with_new_occurrence_updates_position(self):
        save_teaching(self.path, 'C', payload())
        back = payload(8, 'RETURN', '2026-11-10T12:00:00+08:00')
        back['event_type'] = 'topic_change'; back['position']['chapter'] = '4'
        back['position']['topic'] = 'Reviewing Chapter 4'
        back['position'].pop('pending_question', None)
        result = save_teaching(self.path, 'C', back)
        self.assertTrue(result['saved']); self.assertTrue(result['active_advanced'])
        self.assertEqual('4', result['current_progress']['record']['chapter'])
        self.assertEqual('2026-11-10T12:00:00+08:00', result['current_progress']['as_of'])

    def test_equal_time_different_positions_report_conflict(self):
        save_teaching(self.path, 'C', payload())
        other = payload(8, 'OTHER-POSITION'); other['position']['chapter'] = '6'
        result = save_teaching(self.path, 'C', other)
        self.assertTrue(result['saved']); self.assertFalse(result['active_advanced'])
        self.assertEqual('conflict', result['current_progress']['status'])

    def test_original_attempt_is_preserved_with_conditions_and_no_rating(self):
        request = payload(); request['event_type'] = 'attempt'
        request['position']['pending_question']['status'] = 'answered'
        request['attempts'] = [{'id': 'ATTEMPT1', 'question': 'Synthetic question',
                                'user_answer': 'My original uncertain answer',
                                'conditions': {'independent': None, 'assisted': True, 'answer_exposed': None},
                                'topic': 'Specific original question topic', 'answered_at': None,
                                'source_turn_id': 'TURN-2'}]
        save_teaching(self.path, 'C', request)
        state = self.read(); event = state['courses'][0]['teaching_events'][0]
        self.assertEqual(request['attempts'], event['attempts'])
        for mutate in (lambda e: e['attempts'][0].update(user_answer='regraded'),
                       lambda e: e['position'].update(chapter='9')):
            draft = deepcopy(state); mutate(draft['courses'][0]['teaching_events'][0])
            with self.assertRaisesRegex(ValueError, 'append-only'):
                validate_preserved(state, draft)
        duplicate = deepcopy(request); duplicate.update(id='E2', expected_revision=8)
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            save_teaching(self.path, 'C', duplicate)
        self.assertEqual(self.original['fsrs'], state['fsrs'])

    def test_explicit_independence_scope_and_answer_time_are_only_saved_facts(self):
        request = payload(); request['event_type'] = 'attempt'
        request['attempts'] = [{'id': 'A1', 'question': 'Actual question', 'user_answer': 'Original answer',
                                'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False},
                                'topic': 'specific topic', 'answered_at': '2026-02-01T11:59:00+08:00',
                                'source_turn_id': 'TURN-2'}]
        result = save_teaching(self.path, 'C', request)
        state = self.read()
        self.assertEqual(request['attempts'], state['courses'][0]['teaching_events'][0]['attempts'])
        self.assertNotIn('topic_evidence', state['courses'][0])
        self.assertFalse(result['mastery_updated'])
        self.assertEqual(self.original['fsrs'], state['fsrs'])

    def test_skipping_question_does_not_create_attempt_or_mistake(self):
        request = payload(); request['position']['pending_question']['status'] = 'skipped'
        save_teaching(self.path, 'C', request)
        state = self.read()
        self.assertNotIn('attempts', state['courses'][0]['teaching_events'][0])
        self.assertEqual(self.original['mistakes'], state['mistakes'])

    def test_finite_schema_requires_actual_time_and_source(self):
        invalid = []
        for key in ('source', 'occurred_at'):
            item = payload(); del item[key]; invalid.append(item)
        for value in ('2026-02-01', '2026-02-01T12:00:00',
                      (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
            item = payload(); item['occurred_at'] = value; invalid.append(item)
        for key, value in (('mastery', 'independent'), ('user_answer', 'invented')):
            item = payload(); item[key] = value; invalid.append(item)
        item = payload(); item['event_type'] = 'planned'; invalid.append(item)
        item = payload(); item['event_type'] = 'attempt'; invalid.append(item)
        item = payload(); item['source']['turn_ids'] = []; invalid.append(item)
        item = payload(); item['event_type'] = 'answer_exposure'; invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                save_teaching(self.path, 'C', item)
        self.assertEqual(self.original, self.read())

    def test_failed_write_and_failed_readback_never_return_success(self):
        with patch('teaching_progress._write', side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                save_teaching(self.path, 'C', payload())
        self.assertEqual(self.original, self.read())
        with patch('teaching_progress._write'):
            with self.assertRaisesRegex(RuntimeError, '回读不一致'):
                save_teaching(self.path, 'C', payload())
        self.assertEqual(self.original, self.read())

    def test_new_process_can_resume_saved_position(self):
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        command = [sys.executable, str(Path(__file__).with_name('library.py')),
                   '--state', str(self.path)]
        saved = subprocess.run(command + ['teaching-save', '--course', 'C'],
                               input=json.dumps(payload()), text=True, encoding='utf-8',
                               capture_output=True, env=env, check=True)
        self.assertTrue(json.loads(saved.stdout)['readback_verified'])
        resumed = subprocess.run(command + ['context', '--read-only', '--course', 'C'],
                                 text=True, encoding='utf-8', capture_output=True, env=env, check=True)
        result = json.loads(resumed.stdout)
        self.assertIn('Chapter 5 integration', json.dumps(result))
        self.assertEqual('5', teaching_progress(self.read()['courses'][0])['record']['chapter'])


if __name__ == '__main__':
    unittest.main()
