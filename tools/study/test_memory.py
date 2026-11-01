"""Memory recall, provenance and correction tests use synthetic isolated records."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from learning_library.evidence import search_evidence, record_hash
from learning_library.memory import inspect_memory, apply_correction, validate_preserved, correction_rows
from learning_library.context import build_context
from memory_store import save_correction


def fixture():
    return {'revision': 7, 'settings': {}, 'courses': [
        {'id': 'C', 'name': 'Course C', 'current_topic': 'Chapter 7',
         'teaching_cursor': {'topic': 'Chapter 6', 'stage': 'explained',
                             'next_step': 'independent check', 'answer': 'SECRET'},
         'topic_evidence': [{'id': 'E', 'topic': 'FOB', 'level': 'explained',
                             'chapter': 'Chapter 6', 'source': {'material_id': 'DEMO', 'pages': [4]}}],
         'assistant_notes': [{'topic': 'bank reconciliation', 'text': 'A worked example was explained; SECRET'}]},
        {'id': 'OTHER', 'topic_evidence': [{'id': 'OTHER-E', 'topic': 'unrelated private concept', 'level': 'independent'}]}],
        'mistakes': [], 'review_attempts': [], 'learning_feedback': []}


def payload(state, path='/courses/0/teaching_cursor', value=None):
    return {'id': 'CORR-1', 'expected_revision': state['revision'], 'target_path': path,
            'target_sha256': record_hash(state['courses'][0]['teaching_cursor'] if value is None else value),
            'statement': 'I saw an explanation but have not independently answered a new question.'}


class MemoryTests(unittest.TestCase):
    def test_bilingual_paraphrase_and_previous_chapter(self):
        state = fixture(); before = deepcopy(state)
        for query, expected in [('离岸价以前讲过吗', 'E'), ('free on board', 'E')]:
            with self.subTest(query=query):
                result = search_evidence(state, 'C', query)
                self.assertEqual(expected, result['matches'][0]['metadata']['id'])
                self.assertEqual('explained', result['matches'][0]['metadata']['level'])
                self.assertEqual('not_assessed', result['learning_conclusion'])
                self.assertTrue(result['expanded_terms'])
                self.assertEqual('alias', result['matches'][0]['match_reason'])
        result = search_evidence(state, 'C', '那个银行对账讲过吗')
        self.assertTrue(any('assistant_notes' in r['path'] for r in result['matches']))
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(before, state)

    def test_precise_phrase_avoids_generic_word_flood(self):
        state = fixture()
        state['courses'][0]['assistant_notes'] = [{'topic': 'value', 'text': 'mean price value'} for _ in range(40)]
        state['courses'][0]['assistant_notes'].insert(0, {'id': 'MVT', 'topic': '拉格朗日中值定理'})
        result = search_evidence(state, 'C', 'mean value theorem')
        self.assertEqual(1, result['total_matching_locations'])
        self.assertEqual('MVT', result['matches'][0]['metadata']['id'])

    def test_specific_alias_does_not_expand_to_broader_concept(self):
        state = fixture()
        state['courses'][0]['assistant_notes'] = [
            {'id': 'AMOUNT', 'topic': 'gross profit'},
            {'id': 'RATE', 'topic': 'gross profit margin'}]
        result = search_evidence(state, 'C', '毛利率以前讲过吗')
        self.assertEqual(['RATE'], [hit['metadata']['id'] for hit in result['matches']])

    def test_expansion_is_bounded_and_does_not_cross_courses(self):
        state = fixture()
        self.assertEqual([], search_evidence(state, 'OTHER', '离岸价')['matches'])
        self.assertEqual([], search_evidence(state, 'C', 'unknown concept')['matches'])
        self.assertEqual([], search_evidence(state, 'C', 'FOBish')['matches'])
        state['courses'][0]['memory_aliases'] = [['custom phrase', 'bank reconciliation']]
        self.assertTrue(search_evidence(state, 'C', 'custom phrase')['matches'])
        state['courses'][0]['memory_aliases'] = [['single']]
        with self.assertRaises(ValueError): search_evidence(state, 'C', 'FOB')

    def test_inspection_separates_current_assignment_and_tutoring_without_mastery_inference(self):
        state = fixture(); before = deepcopy(state)
        view = inspect_memory(state, 'C', 'FOB')
        self.assertEqual('Chapter 7', view['claims'][0]['value'])
        self.assertEqual('Chapter 6', view['claims'][1]['value']['topic'])
        self.assertEqual(record_hash(state['courses'][0]['teaching_cursor']), view['claims'][1]['record_sha256'])
        self.assertNotIn('SECRET', json.dumps(view))
        self.assertFalse(view['state_written']); self.assertFalse(view['network_started'])
        self.assertEqual(before, state)

    def test_correction_appends_with_explicit_user_provenance(self):
        state = fixture(); original = deepcopy(state); request = payload(state)
        draft, changed = apply_correction(state, 'C', request)
        self.assertTrue(changed); self.assertEqual(original, state)
        self.assertEqual(original['courses'][0]['teaching_cursor'], draft['courses'][0]['teaching_cursor'])
        note = draft['courses'][0]['memory_corrections'][0]
        self.assertEqual('user_reported_not_independently_verified', note['verification'])
        self.assertNotIn('level', note)
        view = inspect_memory(draft, 'C')
        self.assertEqual('located', view['corrections'][0]['target_status'])
        self.assertTrue(build_context(draft, 'C')['sections']['memory_review']['review_required'])
        again, changed = apply_correction(draft, 'C', request)
        self.assertFalse(changed); self.assertEqual(again, draft)
        with self.assertRaises(ValueError): apply_correction(draft, 'C', {**request, 'statement': 'changed'})

    def test_corrections_are_attached_and_searchable(self):
        state=fixture(); record=state['courses'][0]['topic_evidence'][0]
        draft,_=apply_correction(state,'C',payload(state,'/courses/0/topic_evidence/0',record))
        hit=search_evidence(draft,'C','离岸价')['matches'][0]
        self.assertTrue(hit['correction_review_required'])
        self.assertEqual('explained',hit['metadata']['level'])
        self.assertTrue(search_evidence(draft,'C','independently answered')['matches'])

    def test_stale_foreign_or_changed_targets_are_rejected(self):
        state=fixture(); request=payload(state)
        for bad in ({**request,'expected_revision':6}, {**request,'target_sha256':'0'*64},
                    {**request,'target_path':'/courses/1/topic_evidence/0'},
                    {**request,'target_path':'/settings'}, {**request,'level':'independent'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): apply_correction(state,'C',bad)

    def test_target_follows_content_after_reorder_but_not_changed_or_ambiguous_records(self):
        state=fixture(); record=state['courses'][0]['topic_evidence'][0]
        draft,_=apply_correction(state,'C',payload(state,'/courses/0/topic_evidence/0',record))
        draft['courses'].reverse()
        self.assertEqual('/courses/1/topic_evidence/0',correction_rows(draft,'C')[0]['current_target_path'])
        course=draft['courses'][1]; course['topic_evidence'].append(deepcopy(record))
        self.assertEqual('ambiguous',correction_rows(draft,'C')[0]['target_status'])
        course['topic_evidence']=[]
        self.assertEqual('changed_or_missing',correction_rows(draft,'C')[0]['target_status'])

    def test_normal_save_must_preserve_prior_corrections(self):
        draft,_=apply_correction(fixture(),'C',payload(fixture()))
        for altered in ([], [{**draft['courses'][0]['memory_corrections'][0], 'statement':'erased'}]):
            update=deepcopy(draft); update['courses'][0]['memory_corrections']=altered
            with self.assertRaises(ValueError): validate_preserved(draft,update)

    def test_atomic_writer_and_idempotent_retry_keep_original_evidence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path=Path(folder)/'state.json'; state=fixture()
            path.write_text(json.dumps(state),encoding='utf-8'); original=path.read_bytes()
            result=save_correction(path,'C',payload(state))
            self.assertTrue(result['saved']); self.assertEqual(8,result['revision'])
            self.assertEqual(json.loads(original),json.loads(path.with_name('state.previous.json').read_bytes()))
            saved=path.read_bytes()
            retry = save_correction(path,'C',payload(state))
            self.assertTrue(retry['already_saved'])
            self.assertTrue(retry['views_need_refresh'])
            self.assertEqual(saved,path.read_bytes())
            current=json.loads(saved)
            self.assertEqual(state['courses'][0]['teaching_cursor'],current['courses'][0]['teaching_cursor'])
            with self.assertRaises(ValueError):
                save_correction(path, 'C', {**payload(state), 'id': 'STALE-CORR'})
            self.assertEqual(saved, path.read_bytes())

    def test_regular_state_save_cannot_erase_corrections(self):
        from state_io import bridge
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'state.json'
            path.write_text(json.dumps(fixture()), encoding='utf-8')
            save_correction(path, 'C', payload(fixture()))
            original = path.read_bytes()
            current = json.loads(original)
            draft = deepcopy(current)
            del draft['courses'][0]['memory_corrections']
            with self.assertRaisesRegex(ValueError, '只能追加'):
                bridge(path, 'replace', {'expected': current, 'state': draft})
            self.assertEqual(original, path.read_bytes())

    def test_cli_memory_is_readonly_and_fresh_process_recovers_correction(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder); path=root/'state.json'; state=fixture()
            path.write_text(json.dumps(state),encoding='utf-8')
            cmd=[sys.executable,'-B',str(Path(__file__).with_name('library.py')),'--state',str(path)]
            env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
            before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
            run=subprocess.run(cmd+['memory','--course','C','--query','离岸价'],capture_output=True,encoding='utf-8',env=env)
            self.assertEqual(0,run.returncode,run.stderr)
            self.assertTrue(json.loads(run.stdout)['evidence']['matches'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir() if p.is_file()})
            run=subprocess.run(cmd+['memory-correct','--course','C'],input=json.dumps(payload(state)),capture_output=True,encoding='utf-8',env=env)
            self.assertEqual(0,run.returncode,run.stderr)
            run=subprocess.run(cmd+['memory','--course','C'],capture_output=True,encoding='utf-8',env=env)
            self.assertEqual(0,run.returncode,run.stderr)
            self.assertEqual('CORR-1',json.loads(run.stdout)['corrections'][0]['id'])
