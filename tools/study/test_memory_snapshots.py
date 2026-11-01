"""Durable correction sources, tested only with isolated synthetic records."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from learning_library.evidence import record_hash, search_evidence
from learning_library.memory import apply_correction, correction_rows, inspect_memory, records
from memory_store import save_correction
from state_io import bridge


def fixture():
    return {'revision': 7, 'courses': [
        {'id': 'C', 'current_topic': 'Chapter 3 not yet studied'},
        {'id': 'OTHER', 'current_topic': 'Chapter 3 not yet studied'}]}


def request(state, target=None, path='/courses/0/current_topic'):
    value = state['courses'][0]['current_topic'] if target is None else target
    return {'id': 'CORRECTION-1', 'expected_revision': state['revision'],
            'target_path': path, 'target_sha256': record_hash(value),
            'statement': 'This claim is incorrect; retain what was originally recorded.'}


def replace_state(path, current, draft, original):
    bridge(path, 'replace', {'expected': current, 'state': draft})


class MemorySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'state.json'
        self.state = fixture()
        self.path.write_text(json.dumps(self.state), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_bytes())

    def update(self, change):
        original = self.path.read_bytes()
        current = json.loads(original)
        draft = deepcopy(current)
        change(draft)
        replace_state(self.path, current, draft, original)

    def test_original_claim_survives_two_normal_saves_and_fresh_process_detail(self):
        original_claim = self.state['courses'][0]['current_topic']
        save_correction(self.path, 'C', request(self.state))
        for topic in ('Chapter 4 assignment', 'Chapter 5 assignment'):
            self.update(lambda draft: draft['courses'][0].update(current_topic=topic))
        current = self.read()
        self.assertFalse(any('/target_snapshot' in path for path in records(current, 'C')))
        # This term exists only in the snapshot (and the unrelated course),
        # unlike generic words such as "not" in provenance metadata.
        self.assertEqual([], search_evidence(current, 'C', 'studied')['matches'])
        row = inspect_memory(current, 'C')['corrections'][0]
        self.assertEqual(10, current['revision'])
        self.assertEqual('changed_or_missing', row['target_status'])
        self.assertEqual('preserved', row['snapshot_status'])
        self.assertEqual('/courses/0/memory_corrections/0/target_snapshot', row['snapshot_path'])
        cmd = [sys.executable, '-B', str(Path(__file__).with_name('library.py')),
               '--state', str(self.path), 'detail', 'path', row['snapshot_path']]
        run = subprocess.run(cmd, capture_output=True, encoding='utf-8',
                             env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(0, run.returncode, run.stderr)
        detail = json.loads(run.stdout)
        self.assertEqual(original_claim, detail['record'])
        self.assertEqual(row['target_sha256'], record_hash(detail['record']))
        self.assertTrue(detail['answer_keys_may_be_present'])

    def test_snapshot_answers_stay_private_and_snapshot_is_not_a_live_target(self):
        state = fixture()
        original_cursor = {'topic': 'FOB', 'stage': 'explained', 'next_step': 'practice',
                           'answer': 'PRIVATE-ANSWER-7',
                           'checks': [{'user_answer': 'PRIVATE-ANSWER-9'}]}
        state['courses'][0]['teaching_cursor'] = deepcopy(original_cursor)
        draft, _ = apply_correction(state, 'C', request(state, original_cursor,
                                                      '/courses/0/teaching_cursor'))
        draft['courses'][0]['teaching_cursor']['next_step'] = 'later independent check'
        row = correction_rows(draft, 'C')[0]
        self.assertEqual('changed_or_missing', row['target_status'])
        self.assertEqual('preserved', row['snapshot_status'])
        self.assertNotIn('target_snapshot', row)
        self.assertFalse(any('/target_snapshot' in path for path in records(draft, 'C')))
        for output in (inspect_memory(draft, 'C', 'FOB'), search_evidence(draft, 'C', 'FOB')):
            serialized = json.dumps(output)
            self.assertNotIn('PRIVATE-ANSWER-7', serialized)
            self.assertNotIn('PRIVATE-ANSWER-9', serialized)
        self.assertEqual(original_cursor, draft['courses'][0]['memory_corrections'][0]['target_snapshot'])

    def test_corrupted_snapshot_is_not_advertised_as_verified_source(self):
        draft, _ = apply_correction(self.state, 'C', request(self.state))
        draft['courses'][0]['memory_corrections'][0]['target_snapshot'] = 'CORRUPTED-SOURCE'
        row = correction_rows(draft, 'C')[0]
        self.assertEqual('invalid', row['snapshot_status'])
        self.assertIsNone(row['snapshot_path'])
        self.assertNotIn('target_snapshot', row)
        self.assertNotIn('CORRUPTED-SOURCE', json.dumps(inspect_memory(draft, 'C')))

    def test_legacy_correction_stays_readable_without_fabricating_a_snapshot(self):
        draft, _ = apply_correction(self.state, 'C', request(self.state))
        draft['courses'][0]['memory_corrections'][0].pop('target_snapshot', None)
        draft['courses'][0]['current_topic'] = 'Chapter 4 assignment'
        before = deepcopy(draft)
        row = inspect_memory(draft, 'C')['corrections'][0]
        self.assertEqual('unavailable_legacy', row['snapshot_status'])
        self.assertIsNone(row['snapshot_path'])
        self.assertEqual('changed_or_missing', row['target_status'])
        self.assertEqual(before, draft)

    def test_retry_preserves_snapshot_and_does_not_add_a_second_correction(self):
        payload = request(self.state)
        save_correction(self.path, 'C', payload)
        saved = self.path.read_bytes()
        retry = save_correction(self.path, 'C', payload)
        self.assertTrue(retry['already_saved'])
        self.assertEqual(saved, self.path.read_bytes())
        corrections = self.read()['courses'][0]['memory_corrections']
        self.assertEqual(1, len(corrections))
        self.assertEqual(self.state['courses'][0]['current_topic'], corrections[0]['target_snapshot'])

    def test_same_content_in_other_course_cannot_rebind_the_correction(self):
        save_correction(self.path, 'C', request(self.state))
        def move_and_reorder(draft):
            draft['courses'][0]['current_topic'] = 'Chapter 4 assignment'
            draft['courses'].reverse()
        self.update(move_and_reorder)
        current = self.read()
        self.assertEqual([], inspect_memory(current, 'OTHER')['corrections'])
        row = inspect_memory(current, 'C')['corrections'][0]
        self.assertEqual('changed_or_missing', row['target_status'])
        self.assertIsNone(row['current_target_path'])
        self.assertEqual('preserved', row['snapshot_status'])
        self.assertEqual('/courses/1/memory_corrections/0/target_snapshot', row['snapshot_path'])

    def test_normal_save_cannot_edit_or_delete_a_preserved_snapshot(self):
        save_correction(self.path, 'C', request(self.state))
        original = self.path.read_bytes()
        current = json.loads(original)
        for operation in ('edit', 'delete'):
            draft = deepcopy(current)
            correction = draft['courses'][0]['memory_corrections'][0]
            if operation == 'edit':
                correction['target_snapshot'] = 'replacement'
            else:
                del correction['target_snapshot']
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                replace_state(self.path, current, draft, original)
            self.assertEqual(original, self.path.read_bytes())

    def test_normal_save_rejects_a_new_snapshot_with_the_wrong_fingerprint(self):
        original = self.path.read_bytes()
        draft, _ = apply_correction(self.state, 'C', request(self.state))
        draft['courses'][0]['memory_corrections'][0]['target_snapshot'] = 'not the original claim'
        with self.assertRaises(ValueError):
            replace_state(self.path, self.state, draft, original)
        self.assertEqual(original, self.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
