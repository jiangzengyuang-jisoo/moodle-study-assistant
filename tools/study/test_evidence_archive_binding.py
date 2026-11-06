"""Evidence bindings must survive the real material archive migration.

The real chain is: save an original answer through state_io.atomic/bridge, move a
versioned courseware file with learning_library.versions.promote (real files and
real state rewrite), resolve the bound observation, then save the complete state
again. Migration must never guess and must never loosen the tamper checks.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from evidence_bindings import resolve_observation
from learning_library import versions as v
from library_test_fixtures import temporary_directory, write_pdf
from state_io import atomic, bridge, read

T0 = '2026-01-01T00:00:00+00:00'
OLD_FILE = 'A/Lectures/lesson.pdf'
NEW_FILE = '_Inbox/new/lesson.pdf'
CONDITIONS = {'independent': True, 'assisted': False, 'answer_exposed': False}


def fixture(materials):
    return {
        'revision': 1,
        'settings': {},
        'materials': materials,
        'courses': [{
            'id': 'A',
            'teaching_cursor': {
                'stage': 'pending',
                'pending_check': {
                    'id': 'q1', 'topic': 'FOB', 'prompt': 'Q1', 'user_answer': 'A',
                    'conditions': dict(CONDITIONS),
                    'source': {'material_id': 'old', 'relative_path': OLD_FILE, 'pages': [2]},
                    'parts': [{'id': 'p1', 'prompt': 'part 1', 'user_answer': 'B',
                               'conditions': dict(CONDITIONS)}]},
            }}],
        'mistakes': [{
            'id': 'm1', 'course_id': 'A', 'user_answer': '42',
            'conditions': dict(CONDITIONS),
            'answered_at': '2026-01-01T00:00:00+00:00',
            'source': {'material_id': 'old', 'relative_path': OLD_FILE, 'pages': [1]},
        }],
        'review_attempts': [{
            'id': 'r1', 'course_id': 'A', 'user_answer': 'C',
            'conditions': dict(CONDITIONS),
            'source': {'material_id': 'old', 'relative_path': OLD_FILE, 'pages': [3]},
        }],
    }


def observed(state):
    draft = deepcopy(state)
    draft['courses'][0]['topic_evidence'] = [
        {'id': 'e1', 'topic': 'FOB', 'level': 'independent',
         'conditions': dict(CONDITIONS),
         'source': {'kind': 'observed_attempt', 'record_path': '/mistakes/0'}},
        {'id': 'e2', 'topic': 'FOB', 'level': 'independent',
         'conditions': dict(CONDITIONS),
         'source': {'kind': 'observed_attempt',
                    'record_path': '/courses/0/teaching_cursor/pending_check'}},
        {'id': 'e3', 'topic': 'FOB', 'level': 'independent',
         'conditions': dict(CONDITIONS),
         'source': {'kind': 'observed_attempt',
                    'record_path': '/courses/0/teaching_cursor/pending_check/parts/0'}},
        {'id': 'e4', 'topic': 'FOB', 'level': 'independent',
         'conditions': dict(CONDITIONS),
         'source': {'kind': 'observed_attempt', 'record_path': '/review_attempts/0'}},
    ]
    return draft


class EvidenceArchiveBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory('binding-archive-')
        self.addCleanup(self.temp.cleanup)
        self.study = Path(self.temp.name) / 'Study'
        self.path = self.study / '_System/state.json'
        self.path.parent.mkdir(parents=True)

    def material(self, mid, relative, text):
        target = self.study / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(target, [text])
        return {'id': mid, 'course_id': 'A', 'filename': 'lesson.pdf',
                'category': 'Lectures', 'title': 'Lecture', 'relative_path': relative,
                'sha256': v.sha(target), 'contains_solutions': False,
                'moodle_file_sources': [{'module_id': 123, 'remote_filename': 'lesson.pdf'}]}

    def save(self, expected, draft):
        bridge(self.path, 'replace', {'expected': deepcopy(expected), 'state': draft})
        return read(self.path)

    def bind(self):
        self.old = self.material('old', OLD_FILE, 'OLD_SENTINEL')
        self.new = self.material('new', NEW_FILE, 'NEW_SENTINEL')
        base = fixture([self.old, self.new])
        atomic(self.path, base)
        return self.save(base, observed(base))

    def material_row(self, material_id):
        return next(m for m in read(self.path)['materials'] if m['id'] == material_id)

    def promote(self, incoming=None, old_id=None):
        row = incoming if incoming is not None else self.material_row('new')
        return v.promote(self.path, deepcopy(row), old_id, at=T0)

    def test_real_archive_chain_keeps_answers_resolvable_and_savable(self):
        bound = self.bind()
        before_bindings = deepcopy(bound['courses'][0]['evidence_bindings'])
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            self.assertEqual(resolve_observation(bound, 'A', observation_id)['status'], 'resolved')
        self.assertEqual(self.promote()['status'], 'promoted')
        saved = read(self.path)
        old = next(m for m in saved['materials'] if m['id'] == 'old')
        current = next(m for m in saved['materials'] if m['id'] == 'new')
        # Real files moved: old original under _Archive, incoming in its place.
        self.assertTrue(old['relative_path'].startswith('A/_Archive/'))
        self.assertEqual(current['relative_path'], OLD_FILE)
        self.assertEqual(v.sha(self.study / old['relative_path']), self.old['sha256'])
        self.assertEqual(v.sha(self.study / OLD_FILE), self.new['sha256'])
        self.assertFalse((self.study / NEW_FILE).exists())
        # Material history keeps the original location and hash, not a new answer.
        self.assertEqual(old['original_relative_path'], OLD_FILE)
        self.assertEqual(old['archived_at'], T0)
        self.assertEqual(old['sha256'], self.old['sha256'])
        self.assertEqual(old['superseded_by'], 'new')
        self.assertEqual(old['version_status'], 'archived')
        self.assertEqual(current['version_status'], 'current')
        # Original answer and its historical source are unchanged except the move.
        pending = saved['courses'][0]['teaching_cursor']['pending_check']
        for record, pages in ((saved['mistakes'][0], [1]), (pending, [2]),
                              (saved['review_attempts'][0], [3])):
            self.assertIs(type(record['user_answer']), str)
            self.assertEqual(record['conditions'], CONDITIONS)
            self.assertEqual(record['source']['material_id'], 'old')
            self.assertEqual(record['source']['pages'], pages)
            self.assertEqual(record['source']['relative_path'], old['relative_path'])
        self.assertEqual(pending['parts'][0]['user_answer'], 'B')
        self.assertEqual(pending['parts'][0]['conditions'], CONDITIONS)
        self.assertEqual(saved['mistakes'][0]['answered_at'], '2026-01-01T00:00:00+00:00')
        # Bindings were migrated instead of deleted or forged.
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            binding = saved['courses'][0]['evidence_bindings'][observation_id]
            self.assertEqual(binding['status'], 'bound')
            self.assertEqual(binding['course_id'], 'A')
            self.assertEqual(binding['record_path_at_binding'],
                             before_bindings[observation_id]['record_path_at_binding'])
            self.assertEqual(binding['fields'], before_bindings[observation_id]['fields'])
        # The answer records that contain the moved path get a new fingerprint;
        # the part record keeps its own sha but moves with its parent question.
        for observation_id in ('e1', 'e2', 'e4'):
            self.assertNotEqual(saved['courses'][0]['evidence_bindings'][observation_id]['sha256'],
                                before_bindings[observation_id]['sha256'])
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e3']['sha256'],
                         before_bindings['e3']['sha256'])
        # Parent-free records keep their empty anchors; a nested record whose
        # parent question contains the path has its ancestor fingerprints moved too.
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e1']['ancestors'], [])
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e2']['ancestors'], [])
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e4']['ancestors'], [])
        self.assertNotEqual(saved['courses'][0]['evidence_bindings']['e3']['ancestors'],
                            before_bindings['e3']['ancestors'])
        for observation_id, record_path in (
                ('e1', '/mistakes/0'),
                ('e2', '/courses/0/teaching_cursor/pending_check'),
                ('e3', '/courses/0/teaching_cursor/pending_check/parts/0'),
                ('e4', '/review_attempts/0')):
            resolved = resolve_observation(saved, 'A', observation_id, include_record=True)
            self.assertEqual(resolved['status'], 'resolved')
            self.assertEqual(resolved['record_path'], record_path)
        self.assertEqual(resolve_observation(saved, 'A', 'e1', include_record=True)['record']
                         ['source']['relative_path'], old['relative_path'])
        self.assertEqual(resolve_observation(saved, 'A', 'e3', include_record=True)['record']
                         ['user_answer'], 'B')
        # The documented read-only CLI resolves the archived original as well.
        before_cli = self.path.read_bytes()
        result = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).with_name('library.py')),
             '--state', str(self.path), 'evidence-source', '--course', 'A',
             '--observation', 'e1'], capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['record']['user_answer'], '42')
        self.assertEqual(json.loads(result.stdout)['status'], 'resolved')
        self.assertEqual(self.path.read_bytes(), before_cli)
        # A full-state save after the archive succeeds and is repeatable.
        first = self.save(saved, deepcopy(saved))
        self.assertEqual(first['revision'], saved['revision'] + 1)
        second_draft = deepcopy(first)
        second_draft['settings']['marker'] = 'after-archive'
        second = self.save(first, second_draft)
        self.assertEqual(second['revision'], first['revision'] + 1)
        self.assertEqual(second['settings']['marker'], 'after-archive')
        self.assertEqual(second['courses'][0]['evidence_bindings'],
                         saved['courses'][0]['evidence_bindings'])
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            self.assertEqual(resolve_observation(second, 'A', observation_id)['status'], 'resolved')

    def test_repeated_promotion_is_idempotent_and_second_archive_resolves(self):
        self.bind()
        self.promote()
        byte_before = self.path.read_bytes()
        again = self.promote(self.material_row('new'), 'old')
        self.assertEqual(again['status'], 'already_promoted')
        self.assertEqual(self.path.read_bytes(), byte_before)
        # A second real archive moves the now-current file again.
        new2 = self.material('new2', '_Inbox/new2/lesson.pdf', 'NEW2_SENTINEL')
        self.assertEqual(v.promote(self.path, new2, 'new', at=T0)['status'], 'promoted')
        saved = read(self.path)
        self.assertEqual(saved['materials'][-1]['id'], 'new2')
        self.assertEqual(saved['materials'][-1]['relative_path'], OLD_FILE)
        self.assertEqual(saved['materials'][-1]['version_status'], 'current')
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            self.assertEqual(resolve_observation(saved, 'A', observation_id)['status'], 'resolved')
        self.save(saved, deepcopy(saved))

    def test_manual_answer_change_after_archive_is_still_rejected(self):
        self.bind()
        self.promote()
        state = read(self.path)
        baseline = self.path.read_bytes()
        for location, value in (
                (lambda draft: draft['mistakes'][0].__setitem__('user_answer', '99'), 'answer'),
                (lambda draft: draft['mistakes'][0]['conditions'].__setitem__('independent', False), 'conditions'),
                (lambda draft: draft['mistakes'][0].__setitem__('answered_at', '2030-01-01T00:00:00+00:00'), 'time'),
                (lambda draft: draft['courses'][0]['teaching_cursor']['pending_check'].__setitem__(
                    'prompt', 'edited prompt'), 'prompt')):
            draft = deepcopy(state)
            location(draft)
            with self.assertRaises(ValueError, msg=value):
                self.save(state, draft)
            self.assertEqual(self.path.read_bytes(), baseline, value)

    def test_forged_binding_after_archive_is_still_rejected(self):
        self.bind()
        self.promote()
        state = read(self.path)
        baseline = self.path.read_bytes()
        for mutate in (
                lambda draft: draft['courses'][0]['evidence_bindings']['e1'].__setitem__('sha256', '0' * 64),
                lambda draft: draft['courses'][0]['evidence_bindings']['e3'].__setitem__('ancestors', []),
                lambda draft: draft['courses'][0]['evidence_bindings']['e2'].__setitem__('status', 'unresolved_legacy'),
                lambda draft: draft['courses'][0]['evidence_bindings'].pop('e2')):
            draft = deepcopy(state)
            mutate(draft)
            with self.assertRaises(ValueError):
                self.save(state, draft)
            self.assertEqual(self.path.read_bytes(), baseline)

    def test_stale_binding_is_not_guessed_and_stays_unresolved(self):
        self.bind()
        state = read(self.path)
        # A foreign write that never passed validate_draft makes the binding stale.
        state['mistakes'][0]['user_answer'] = 'tampered'
        atomic(self.path, state)
        stale = read(self.path)
        stale_e1 = deepcopy(stale['courses'][0]['evidence_bindings']['e1'])
        self.promote()
        saved = read(self.path)
        # The stale fingerprint is byte-identical (no guessing); the still-valid
        # bindings of the same archive are migrated normally.
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e1'], stale_e1)
        self.assertEqual(resolve_observation(saved, 'A', 'e1')['status'], 'source_missing')
        self.assertEqual(resolve_observation(saved, 'A', 'e2')['status'], 'resolved')
        self.assertEqual(resolve_observation(saved, 'A', 'e3')['status'], 'resolved')
        self.assertEqual(resolve_observation(saved, 'A', 'e4')['status'], 'resolved')
        # Guessing is refused: the stale binding cannot be silently saved around.
        baseline = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.save(saved, deepcopy(saved))
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_failed_promotion_rolls_back_migrated_bindings(self):
        self.bind()
        before = self.path.read_bytes()
        real_write = v._write

        def fail(path, value):
            if Path(path) == self.path:
                raise OSError('simulated disk failure')
            return real_write(path, value)

        with patch.object(v, '_write', side_effect=fail):
            with self.assertRaises(OSError):
                self.promote()
        self.assertEqual(self.path.read_bytes(), before)
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            self.assertEqual(
                resolve_observation(read(self.path), 'A', observation_id)['status'], 'resolved')
        # The same real transaction retries cleanly after the rollback.
        self.assertEqual(self.promote()['status'], 'promoted')
        saved = read(self.path)
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            self.assertEqual(resolve_observation(saved, 'A', observation_id)['status'], 'resolved')
        self.save(saved, deepcopy(saved))

    def test_ambiguous_original_record_is_never_guessed(self):
        self.bind()
        state = read(self.path)
        # A duplicated original record makes the old fingerprint non-unique.
        state['mistakes'].append(deepcopy(state['mistakes'][0]))
        atomic(self.path, state)
        stale_e1 = deepcopy(read(self.path)['courses'][0]['evidence_bindings']['e1'])
        self.promote()
        saved = read(self.path)
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e1'], stale_e1)
        self.assertEqual(resolve_observation(saved, 'A', 'e1')['status'], 'source_missing')
        self.assertEqual(resolve_observation(saved, 'A', 'e2')['status'], 'resolved')
        baseline = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.save(saved, deepcopy(saved))
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_first_registration_moves_binding_that_names_the_staged_file(self):
        staged = self.material('new', NEW_FILE, 'NEW_SENTINEL')
        base = fixture([staged])
        base['mistakes'][0]['source']['relative_path'] = NEW_FILE
        base['courses'][0]['teaching_cursor']['pending_check']['source']['relative_path'] = NEW_FILE
        atomic(self.path, base)
        bound = self.save(base, observed(base))
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            self.assertEqual(
                resolve_observation(bound, 'A', observation_id)['status'], 'resolved')
        self.assertEqual(v.promote(self.path, deepcopy(staged), at=T0)['status'], 'promoted')
        saved = read(self.path)
        self.assertEqual(saved['materials'][0]['relative_path'], OLD_FILE)
        self.assertFalse((self.study / NEW_FILE).exists())
        for observation_id in ('e1', 'e2', 'e3', 'e4'):
            resolved = resolve_observation(saved, 'A', observation_id, include_record=True)
            self.assertEqual(resolved['status'], 'resolved')
        self.assertEqual(resolve_observation(saved, 'A', 'e1', include_record=True)['record']
                         ['source']['relative_path'], OLD_FILE)
        self.assertEqual(resolve_observation(saved, 'A', 'e2', include_record=True)['record']
                         ['source']['relative_path'], OLD_FILE)
        self.save(saved, deepcopy(saved))

    def test_legacy_unbound_observation_stays_unresolved_and_remains_savable(self):
        self.old = self.material('old', OLD_FILE, 'OLD_SENTINEL')
        self.new = self.material('new', NEW_FILE, 'NEW_SENTINEL')
        base = fixture([self.old, self.new])
        base['courses'][0]['topic_evidence'] = [
            {'id': 'e1', 'topic': 'FOB', 'level': 'independent',
             'conditions': dict(CONDITIONS),
             'source': {'kind': 'observed_attempt', 'record_path': '/mistakes/0'}}]
        atomic(self.path, base)
        first = self.save(base, deepcopy(base))
        expected_binding = {'status': 'unresolved_legacy', 'course_id': 'A'}
        self.assertEqual(first['courses'][0]['evidence_bindings']['e1'], expected_binding)
        self.promote()
        saved = read(self.path)
        self.assertEqual(saved['courses'][0]['evidence_bindings']['e1'], expected_binding)
        self.assertEqual(resolve_observation(saved, 'A', 'e1')['status'], 'unresolved_legacy')
        self.save(saved, deepcopy(saved))
        after = read(self.path)
        self.assertEqual(after['courses'][0]['evidence_bindings']['e1'], expected_binding)
        self.assertEqual(resolve_observation(after, 'A', 'e1')['status'], 'unresolved_legacy')


if __name__ == '__main__':
    unittest.main()
