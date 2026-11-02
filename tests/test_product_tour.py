"""Product orientation persists independently of course progress and networking."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/study'))
from product_tour import record_tour, tour_status
from state_io import atomic, read
from session import Session


class ProductTourTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'Study/_System/state.json'
        self.state = {'revision': 4, 'settings': {'existing': True}, 'courses': [
            {'id': 'demo', 'teaching_cursor': {'next_step': 'continue', 'checks': [
                {'id': 'q1', 'user_answer': 'original'}]}}], 'mistakes': []}
        atomic(self.path, self.state)

    def test_status_is_read_only_and_old_installs_need_orientation(self):
        before = self.path.read_bytes()
        self.assertTrue(tour_status()['pending'])
        self.assertTrue(tour_status(read(self.path))['pending'])
        self.assertEqual(before, self.path.read_bytes())

    def test_learning_entry_reminds_until_presented_and_preserves_read_only_state(self):
        before = self.path.read_bytes()
        session = Session(Path(self.temp.name), 'chat-a')
        self.assertTrue(session.context()['product_onboarding']['pending'])
        self.assertEqual(before, self.path.read_bytes())
        record_tour(self.path, 'shown', 'chat-a')
        self.assertFalse(Session(Path(self.temp.name), 'new-chat').context()['product_onboarding']['pending'])

    def test_shown_preserves_progress_and_is_idempotent(self):
        self.assertFalse(record_tour(self.path, 'shown', 'chat-a')['pending'])
        after = self.path.read_bytes()
        self.assertEqual('already_recorded', record_tour(self.path, 'shown', 'chat-a')['status'])
        self.assertEqual(after, self.path.read_bytes())
        state = read(self.path)
        self.assertEqual(self.state['courses'], state['courses'])
        self.assertTrue(state['settings']['existing'])
        self.assertEqual(5, state['revision'])
        self.assertEqual(self.state, read(self.path.with_name('state.previous.json')))

    def test_skip_is_respected_and_later_replay_retains_history(self):
        self.assertFalse(record_tour(self.path, 'skipped', 'a')['pending'])
        record_tour(self.path, 'shown', 'b')
        self.assertEqual(['skipped', 'shown'], [e['outcome'] for e in read(self.path)['settings']['product_onboarding']['events']])

    def test_status_survives_a_new_process(self):
        record_tour(self.path, 'shown', 'a')
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys,json; sys.path.insert(0,sys.argv[1]); from product_tour import tour_status; '
            'from state_io import read; print(json.dumps(tour_status(read(sys.argv[2]))))',
            str(ROOT / 'tools/study'), str(self.path)], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(json.loads(result.stdout)['pending'])

    def test_invalid_ack_does_not_write(self):
        before = self.path.read_bytes()
        for outcome, chat in [('installed', 'a'), ('shown', '')]:
            with self.assertRaises(ValueError): record_tour(self.path, outcome, chat)
        self.assertEqual(before, self.path.read_bytes())

    def test_corrupt_history_is_not_reset(self):
        state = copy.deepcopy(self.state)
        state['settings']['product_onboarding'] = {'events': 'bad'}
        atomic(self.path, state)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError): record_tour(self.path, 'shown', 'a')
        self.assertEqual(before, self.path.read_bytes())


if __name__ == '__main__': unittest.main()
