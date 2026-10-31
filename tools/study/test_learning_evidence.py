"""Cross-chapter evidence locators do not invent mastery or expose answers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from learning_library.context import read_detail
from learning_library.evidence import search_evidence
from library_test_fixtures import temporary_directory


def fixture():
    return {'revision': 7, 'courses': [
        {'id': 'C', 'name': 'Accounting', 'aliases': ['会计'],
         'current_topic': 'Chapter 7',
         'teaching_cursor': {'topic': 'Chapter 7', 'next_step': 'inventory'},
         'topic_evidence': [{'id': 'old-fob', 'topic': 'FOB', 'chapter': 'Chapter 6',
                             'level': 'explained', 'at': '2026-10-30',
                             'source': {'material_id': 'SLIDES6', 'pages': [4], 'excerpt': 'SECRET_EXCERPT'}}],
         'assistant_notes': []},
        {'id': 'OTHER', 'name': 'Other', 'topic_evidence': [{'topic': 'FOB', 'level': 'independent'}]}],
        'learning_feedback': [], 'mistakes': [], 'review_attempts': []}


class LearningEvidenceTests(unittest.TestCase):
    def test_previous_chapter_is_found_without_changing_current_cursor_or_level(self):
        state = fixture(); before = deepcopy(state)
        result = search_evidence(state, 'C', 'fob')
        row = result['matches'][0]
        self.assertEqual(row['metadata']['chapter'], 'Chapter 6')
        self.assertEqual(row['metadata']['level'], 'explained')
        self.assertFalse(row['level_inferred'])
        self.assertEqual(result['learning_conclusion'], 'not_assessed')
        self.assertEqual(state, before)
        self.assertEqual(read_detail(state, 'path', row['path'])['record']['id'], 'old-fob')

    def test_old_long_note_is_searchable_but_not_echoed_or_classified(self):
        state = fixture(); course = state['courses'][0]
        course['topic_evidence'] = []
        course['assistant_notes'] = [{'text': 'old ' * 10000 + 'FOB was explained; SECRET_NOTE', 'at': '2020-01-01'}]
        result = search_evidence(state, 'C', 'FOB')
        self.assertEqual(result['shown'], 1)
        self.assertEqual(result['matches'][0]['record_kind'], 'legacy_candidate')
        self.assertNotIn('level', result['matches'][0]['metadata'])
        self.assertNotIn('SECRET_NOTE', json.dumps(result))
        self.assertLess(len(json.dumps(result)), 2000)

    def test_answers_solutions_and_source_excerpts_are_hidden(self):
        state = fixture()
        state['courses'][0]['topic_evidence'][0].update(
            answer='SECRET_ANSWER', user_answer='SECRET_USER', solution='SECRET_SOLUTION',
            custom_notes='SECRET_CUSTOM', source={'material_id': 'SLIDES6', 'pages': [4],
                                                  'url': 'https://example.test/?token=SECRET_TOKEN',
                                                  'excerpt': 'SECRET_EXCERPT'})
        rendered = json.dumps(search_evidence(state, 'C', 'FOB'))
        for secret in ('SECRET_ANSWER', 'SECRET_USER', 'SECRET_SOLUTION', 'SECRET_CUSTOM',
                       'SECRET_TOKEN', 'SECRET_EXCERPT'):
            self.assertNotIn(secret, rendered)
        self.assertIn('SLIDES6', rendered)

    def test_alias_name_and_ambiguity(self):
        state = fixture()
        for name in ('会计', 'accounting', ' C '):
            self.assertEqual(search_evidence(state, name, 'FOB')['course_id'], 'C')
        for invalid in ('unknown', ''):
            with self.assertRaises(ValueError): search_evidence(state, invalid, 'FOB')
        state['courses'][1]['aliases'] = ['会计']
        with self.assertRaises(ValueError): search_evidence(state, '会计', 'FOB')

    def test_other_courses_and_nested_shared_records_are_excluded(self):
        state = fixture()
        state['learning_feedback'] = [
            {'course_id': 'OTHER', 'completed': 'FOB', 'id': 'other-feedback'},
            {'course_ids': ['C', 'OTHER'], 'items': [
                {'course_id': 'C', 'topic': 'FOB', 'id': 'shared-current'},
                {'course_id': 'OTHER', 'topic': 'FOB', 'id': 'shared-other'},
                {'course_ids': ['OTHER'], 'topic': 'FOB', 'id': 'nested-other'}]}]
        result = search_evidence(state, 'C', 'FOB')
        rendered = json.dumps(result)
        self.assertIn('shared-current', rendered)
        for excluded in ('other-feedback', 'shared-other', 'nested-other'):
            self.assertNotIn(excluded, rendered)
        self.assertEqual(result['total_matching_locations'], 2)

    def test_legacy_cursor_history_feedback_mistakes_and_attempts_are_searchable(self):
        state = fixture(); course = state['courses'][0]; course['topic_evidence'] = []
        course['teaching_cursor']['checks'] = [{'topic': 'FOB', 'answer': 0, 'independent': True}]
        course['teaching_history'] = [{'topic': 'FOB', 'next_step': 'old task'}]
        course['history'] = [{'text': 'FOB formerly covered'}]
        course['learning_progress'] = {'chapter6': {'text': 'FOB'}}
        for key in ('learning_feedback', 'mistakes', 'review_attempts'):
            state[key] = [{'course_id': 'C', 'topic': 'FOB', 'answer': 'SECRET_OLD_ANSWER'}]
        result = search_evidence(state, 'C', 'FOB')
        self.assertEqual(result['total_matching_locations'], 7)
        self.assertTrue(all(row['record_kind'] == 'legacy_candidate' for row in result['matches']))
        self.assertNotIn('SECRET_OLD_ANSWER', json.dumps(result))

    def test_zero_hits_means_no_matching_record_not_unlearned(self):
        result = search_evidence(fixture(), 'C', 'unseen phrase')
        self.assertEqual(result['status'], 'no_matching_record')
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['learning_conclusion'], 'not_assessed')
        self.assertIn('不等于没学过', result['note'])

    def test_backlog_coverage_and_corrections_are_located_without_highest_level_inference(self):
        state = fixture(); course = state['courses'][0]
        course['topic_evidence'][0]['level'] = 'independent'
        course['topic_evidence'].append({'id': 'correction', 'topic': 'FOB', 'level': 'explained',
                                         'corrects': 'old-fob', 'at': '2026-10-31'})
        course['review_backlog'] = [{'id': 'R', 'topic': 'FOB', 'chapter': 'Chapter 6'}]
        course['coverage_records'] = [{'id': 'scope', 'topic': 'FOB', 'chapter': 'Chapter 7'}]
        result = search_evidence(state, 'C', 'FOB')
        self.assertEqual(result['matches'][0]['metadata']['corrects'], 'old-fob')
        self.assertEqual(result['matches'][0]['metadata']['level'], 'explained')
        self.assertEqual(result['learning_conclusion'], 'not_assessed')
        self.assertEqual(result['total_matching_locations'], 4)
        self.assertTrue(any('/review_backlog/' in row['path'] for row in result['matches']))
        self.assertTrue(any('/coverage_records/' in row['path'] for row in result['matches']))
        result = search_evidence(state, 'C', 'FOB', limit=1)
        self.assertEqual(result['matches'][0]['metadata']['id'], 'correction')
        self.assertTrue(result['truncated'])

    def test_matching_is_literal_casefolded_phrase_or_any_whitespace_term(self):
        state = fixture()
        state['courses'][0]['assistant_notes'] = ['shipping point discussed', 'FOBish is not FOB', 'ＦＯＢ discussed']
        self.assertEqual(search_evidence(state, 'C', 'fob')['total_matching_locations'], 3)
        self.assertEqual(search_evidence(state, 'C', 'shipping missing')['total_matching_locations'], 1)
        self.assertEqual(search_evidence(state, 'C', 'fobish')['total_matching_locations'], 1)

    def test_limit_and_budget_do_not_dump_all_history(self):
        state = fixture(); state['courses'][0]['topic_evidence'] = []
        state['mistakes'] = [{'id': f'M{i}', 'course_id': 'C', 'topic': 'FOB', 'user_answer': 'SECRET'} for i in range(300)]
        result = search_evidence(state, 'C', 'FOB', limit=2)
        self.assertEqual(result['total_matching_locations'], 300)
        self.assertEqual(result['shown'], 2)
        self.assertTrue(result['truncated'])
        self.assertLess(len(json.dumps(result)), 3000)
        self.assertEqual(len(result['searched_paths']), 6)
        for limit in (0, 21, True):
            with self.assertRaises(ValueError): search_evidence(state, 'C', 'FOB', limit)
        for query in (' ', 'x' * 241):
            with self.assertRaises(ValueError): search_evidence(state, 'C', query)

    def test_cli_is_read_only_including_no_timing_or_maintenance_files(self):
        with temporary_directory('evidence 中文 ') as folder:
            root = Path(folder); state_path = root / 'Study/_System/state.json'
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(fixture(), ensure_ascii=False), encoding='utf-8')
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
            command = [sys.executable, '-B', str(Path(__file__).with_name('library.py')),
                       '--state', str(state_path), 'evidence', '--course', '会计', '--query', 'FOB']
            run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['matches'][0]['metadata']['level'], 'explained')
            self.assertEqual(before, {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()})


if __name__ == '__main__':
    unittest.main()
