"""Material moves preserve historical evidence, using only invented local files."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from learning_library import versions
from learning_library.evidence import record_hash
from learning_library.memory import apply_correction, correction_rows
from library import Library
from state_io import bridge
from evidence_bindings import resolve_observation


class MaterialVersionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix='version-evidence-', dir=self.base)
        self.addCleanup(self.cleanup)
        self.study = Path(self.temp.name) / 'Study'
        self.path = self.study / '_System/state.json'
        self.path.parent.mkdir(parents=True)
        self.old = self.material('old', 'C/Lectures/lesson.txt', 'OLD ORIGINAL')
        self.new = self.material('new', '_Inbox/new/lesson.txt', 'NEW ORIGINAL')
        self.state = {'revision': 1, 'settings': {}, 'materials': [self.old],
                      'courses': [{'id': 'C', 'teaching_cursor': {'topic': 'FOB',
                          'pending_check': self.answer(self.old)}}],
                      'mistakes': [{'id': 'm1', 'course_id': 'C',
                          'material_id': 'old', 'source': self.old['relative_path'],
                          'user_answer': 'I used ' + self.old['relative_path']}]}
        self.write(self.state)

    def cleanup(self):
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(self.base))
        self.temp.cleanup()

    def material(self, mid, relative, text):
        path = self.study / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return {'id': mid, 'course_id': 'C', 'filename': path.name, 'category': 'Lectures',
                'relative_path': relative, 'sha256': versions.sha(path),
                'moodle_file_sources': [{'module_id': 123, 'remote_filename': path.name}]}

    def answer(self, material):
        return {'id': 'q1', 'topic': 'FOB', 'prompt': 'Explain the source.',
                'source': {'material_id': material['id'], 'path': material['relative_path']},
                'user_answer': 'The original source is ' + material['relative_path'],
                'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False}}

    def write(self, state):
        self.path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_bytes())

    def ordinary_save(self):
        current = self.read()
        bridge(self.path, 'replace', {'expected': current, 'state': deepcopy(current)})

    def with_correction(self, state):
        return apply_correction(state, 'C', {
            'id': 'correction1', 'expected_revision': state['revision'],
            'target_path': '/courses/0/teaching_cursor',
            'target_sha256': record_hash(state['courses'][0]['teaching_cursor']),
            'statement': 'Retain this original learning record.'})[0]

    def assert_history(self, expected):
        actual = self.read()
        self.assertEqual(actual['courses'], expected['courses'])
        self.assertEqual(actual['mistakes'], expected['mistakes'])
        for row in actual['materials']:
            self.assertEqual(versions.sha(self.study / row['relative_path']), row['sha256'])
        return actual

    def test_two_updates_preserve_original_answers_correction_and_history_reading(self):
        initial = self.with_correction(self.state)
        self.write(initial)
        third = self.material('third', '_Inbox/third/lesson.txt', 'THIRD ORIGINAL')
        for incoming in (self.new, third):
            versions.promote(self.path, incoming)
            self.assert_history(initial)
            self.assertEqual(correction_rows(self.read(), 'C')[0]['snapshot_status'], 'preserved')
            self.ordinary_save()
            self.assert_history(initial)
        lib = Library(self.path)
        lib.sync()
        historical = lib.read('old', include_archived=True)
        self.assertIn('OLD ORIGINAL', historical['pages'][0]['text'])
        self.assertTrue(historical['historical_version'])
        self.assertEqual([row['id'] for row in lib.outline('C')['materials']], ['third'])

    def test_first_install_preserves_staging_paths_in_existing_evidence(self):
        initial = deepcopy(self.state)
        initial['materials'] = []
        (self.study / self.old['relative_path']).unlink()
        initial['courses'][0]['teaching_cursor']['pending_check'] = self.answer(self.new)
        initial = self.with_correction(initial)
        self.write(initial)
        versions.promote(self.path, self.new)
        self.assert_history(initial)
        self.assertEqual(self.read()['materials'][0]['relative_path'], 'C/Lectures/lesson.txt')
        self.ordinary_save()
        self.assertEqual(correction_rows(self.read(), 'C')[0]['snapshot_status'], 'preserved')

    def test_only_markdown_destinations_move_not_quoted_original_paths(self):
        note = self.study / 'C/notes.md'
        literal = 'Original answer: C/Lectures/lesson.txt; _Inbox/new/lesson.txt'
        note.write_text(literal + '\n[old](Lectures/lesson.txt#page=1)\n'
                        '[new](../_Inbox/new/lesson.txt)\n', encoding='utf-8')
        versions.promote(self.path, self.new)
        text = note.read_text(encoding='utf-8')
        archived = self.read()['materials'][0]['relative_path'].removeprefix('C/')
        self.assertIn(literal, text)
        self.assertIn('[old](<' + archived + '#page=1>)', text)
        self.assertIn('[new](<Lectures/lesson.txt>)', text)

    def bind_answer(self, material, nested=False):
        initial = deepcopy(self.state)
        answer = self.answer(material)
        pointer = '/courses/0/teaching_cursor/pending_check'
        if nested:
            # The path belongs to the parent question, not to its child answer.
            child = {'id': 'part1', 'topic': answer['topic'],
                     'user_answer': 'Original subanswer', 'conditions': answer['conditions']}
            answer.pop('user_answer')
            answer['parts'] = [child]
            pointer += '/parts/0'
        initial['courses'][0]['teaching_cursor']['pending_check'] = answer
        self.write(initial)
        draft = deepcopy(initial)
        draft['courses'][0]['topic_evidence'] = [{
            'id': 'e1', 'topic': 'FOB', 'level': 'independent',
            'conditions': {'independent': True, 'assisted': False, 'answer_exposed': False},
            'source': {'kind': 'observed_attempt', 'record_path': pointer}}]
        bridge(self.path, 'replace', {'expected': initial, 'state': draft})
        bound = self.with_correction(self.read())
        self.write(bound)
        return bound

    def assert_binding(self, initial):
        self.assert_history(initial)
        self.assertEqual(resolve_observation(self.read(), 'C', 'e1')['status'], 'resolved')
        self.assertEqual(correction_rows(self.read(), 'C')[0]['snapshot_status'], 'preserved')
        self.ordinary_save()
        self.assert_history(initial)

    def test_bound_answer_survives_two_material_updates_and_complete_saves(self):
        initial = self.bind_answer(self.old)
        third = self.material('third', '_Inbox/third/lesson.txt', 'THIRD ORIGINAL')
        for incoming in (self.new, third):
            versions.promote(self.path, incoming)
            self.assert_binding(initial)

    def test_parent_question_binding_survives_archiving_and_complete_save(self):
        initial = self.bind_answer(self.old, nested=True)
        self.assertTrue(initial['courses'][0]['evidence_bindings']['e1']['ancestors'])
        versions.promote(self.path, self.new)
        self.assert_binding(initial)

    def test_staged_material_install_preserves_preexisting_answer_binding(self):
        initial = self.bind_answer(self.new)
        initial['materials'] = []
        self.write(initial)
        (self.study / self.old['relative_path']).unlink()
        versions.promote(self.path, self.new)
        self.assert_binding(initial)


if __name__ == '__main__':
    unittest.main()
