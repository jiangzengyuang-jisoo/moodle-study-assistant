"""Small teaching-position events merge through the existing save pipeline.

Synthetic state only: temporary Study directories, no school network, no real
``Study/_System/state.json``. Each test is a read-only check of guard semantics
unless it explicitly asserts a committed change.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import daily
import review
import state_io
from cursor_evidence import active_snapshot
from session import Session

HERE = Path(__file__).resolve().parent
CLI = HERE / 'session.py'
NOW = '2026-11-08T10:00:00+08:00'
VIEW_KEYS = ('status', 'course_id', 'operation_id', 'revision', 'state_written',
             'views_updated', 'changed_fields', 'pending_check_id', 'history_appended',
             'onboarding_status', 'mastery_updated')


def fixture():
    return {
        'revision': 1, 'updated_at': '2026-11-08T09:00:00+08:00',
        'timezone': 'Asia/Hong_Kong', 'settings': {}, 'integrations': {},
        'activity_log': [],
        'courses': [{'id': 'DEMO', 'name': 'Demo', 'current_topic': None,
                     'teaching_cursor': None}],
        'tasks': [], 'deadlines': [], 'schedules': [], 'daily_plans': [],
        'weekly_plan': {}, 'materials': [],
        'mistakes': [], 'review_queue': [], 'review_sessions': [],
        'review_attempts': [], 'learning_feedback': [], 'exam_profiles': [],
        'mock_exams': [], 'mock_attempts': [],
    }


def env_without_chat_identity():
    env = dict(os.environ)
    for key in ('STUDY_CHAT_ID', 'DSH_SESSION_ID'):
        env.pop(key, None)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return env


class TeachingEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.study = self.root / 'Study'
        self.path = self.study / '_System' / 'state.json'
        self.state = fixture()
        self.state['materials'] = [{'id': 'MAT-1', 'course_id': 'DEMO', 'version_status': 'current'}]
        state_io.atomic(self.path, self.state)
        self.session = Session(self.root, 'chat-a')
        self.now = review.timestamp(NOW)

    # -- helpers ---------------------------------------------------------
    def read(self):
        return state_io.read(self.path)

    def raw(self):
        return self.path.read_bytes()

    def cursor(self):
        return self.read()['courses'][0]['teaching_cursor']

    def event(self, body, operation_id=None):
        return self.session.teaching_event(body, operation_id)

    def payload(self, fields=None, **extra):
        body = {'course_id': 'DEMO'}
        if fields is not None:
            body['fields'] = fields
        body.update(extra)
        return body

    def seed_cursor(self, cursor):
        state = self.read()
        state['courses'][0]['teaching_cursor'] = deepcopy(cursor)
        state['revision'] += 1
        state_io.atomic(self.path, state)

    def view_bytes(self):
        return {item.name: item.read_bytes() for item in sorted(self.path.parent.glob('*.md'))}

    def timing_stages(self):
        file = self.path.parent / 'Runtime' / 'timings.jsonl'
        if not file.exists():
            return []
        return [json.loads(line)['stage'] for line in file.read_text(encoding='utf-8').splitlines()
                if line.strip()]

    def run_cli(self, args, payload=None, env=None, cwd=HERE):
        command = [sys.executable, '-B', str(CLI), '--root', str(self.root)]
        if payload is not None:
            return subprocess.run(command + args, input=json.dumps(payload, ensure_ascii=False),
                                  text=True, encoding='utf-8', capture_output=True,
                                  cwd=str(cwd), env=env or env_without_chat_identity(), timeout=60)
        return subprocess.run(command + args, capture_output=True, text=True, encoding='utf-8',
                              cwd=str(cwd), env=env or env_without_chat_identity(), timeout=60)

    # -- create / advance / audit ---------------------------------------
    def test_creates_cursor_without_touching_other_records(self):
        body = self.payload({'stage': 'intro', 'next_step': 'wait for answer',
                             'source': {'kind': 'assistant_general_explanation',
                                        'scope_note': '合成测试来源'},
                             'pending_check': {'id': 'q1', 'prompt': '用自己的话解释',
                                               'type': 'feynman'}},
                            checks_append=[{'id': 'archive-1', 'prompt': '旧题', 'answer': '旧答'}])
        before = self.read()
        result = self.event(body)
        saved = self.read()
        self.assertTrue(result['state_written'])
        self.assertEqual(result['revision'], saved['revision'])
        self.assertEqual(saved['revision'], before['revision'] + 1)
        for key in VIEW_KEYS:
            self.assertIn(key, result)
        self.assertFalse(result['mastery_updated'])
        self.assertEqual(len(result['views_updated']), 9)
        cursor = saved['courses'][0]['teaching_cursor']
        self.assertEqual(cursor['mastery_status'], 'not_assessed')
        self.assertEqual(cursor['chat_id'], 'chat-a')
        self.assertTrue(cursor['operation_id'].startswith('teach-'))
        self.assertTrue(cursor['updated_at'].endswith('+08:00'))
        self.assertEqual([row['id'] for row in cursor['checks']], ['archive-1'])
        self.assertEqual(cursor['pending_check']['id'], 'q1')
        self.assertEqual(result['pending_check_id'], 'q1')
        self.assertEqual(saved['mistakes'], before['mistakes'])
        self.assertEqual(saved['review_attempts'], before['review_attempts'])

    def test_advances_position_and_keeps_existing_checks(self):
        self.event(self.payload({'stage': 'intro', 'next_step': 'first'}))
        self.event(self.payload({'stage': 'checking', 'next_step': 'second'},
                                checks_append=[{'id': 'c1', 'prompt': 'P', 'user_answer': 'A'}]))
        cursor = self.cursor()
        self.assertEqual(cursor['stage'], 'checking')
        self.assertEqual(cursor['next_step'], 'second')
        self.assertEqual([row['id'] for row in cursor['checks']], ['c1'])
        self.assertEqual(len(cursor['history'] or []), 0)

    def test_checks_append_is_append_only_and_deduplicates_identical_rows(self):
        record = {'id': 'c1', 'prompt': 'P', 'user_answer': 'A'}
        self.event(self.payload({'stage': 'intro'},
                                checks_append=[deepcopy(record), deepcopy(record)]))
        self.assertEqual([row['id'] for row in self.cursor()['checks']], ['c1'])
        before = self.raw()
        # A later event repeating the same complete record adds nothing.
        result = self.event(self.payload({'stage': 'intro'}, checks_append=[deepcopy(record)]))
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(self.raw(), before)

    def test_two_attempts_with_same_question_id_are_kept_in_order(self):
        first = {'id': 'q1', 'prompt': 'P', 'user_answer': 'first'}
        second = {'id': 'q1', 'prompt': 'P', 'user_answer': 'second'}
        self.event(self.payload({'stage': 'intro'}, checks_append=[first, second]))
        rows = self.cursor()['checks']
        self.assertEqual([row['user_answer'] for row in rows], ['first', 'second'])

    # -- descriptive lesson records -------------------------------------
    def test_creates_descriptive_lesson_records(self):
        body = self.payload({'stage': 'intro',
                             'user_request': '给我讲一下第8章',
                             'lesson_scope': ['外部性定义', '私人成本与社会成本'],
                             'worked_example': {'kind': 'assistant_created',
                                                'description': '额外一单位商品的私人净收益与社会净收益对比'},
                             'coverage_note': '已讲主内容；作业要求未核对'})
        result = self.event(body)
        self.assertEqual(result['status'], 'recorded')
        cursor = self.cursor()
        self.assertEqual(cursor['user_request'], '给我讲一下第8章')
        self.assertEqual(cursor['lesson_scope'], ['外部性定义', '私人成本与社会成本'])
        self.assertEqual(cursor['worked_example']['kind'], 'assistant_created')
        self.assertEqual(cursor['coverage_note'], '已讲主内容；作业要求未核对')
        for key in ('user_request', 'lesson_scope', 'worked_example', 'coverage_note'):
            self.assertIn(key, result['changed_fields'])

    def test_stage_update_keeps_descriptive_lesson_records(self):
        self.event(self.payload({'stage': 'intro',
                                 'user_request': '讲第8章',
                                 'lesson_scope': ['外部性'],
                                 'worked_example': {'kind': 'assistant_created',
                                                    'description': '示例'},
                                 'coverage_note': '覆盖说明'}))
        before = deepcopy(self.cursor())
        result = self.event(self.payload({'stage': 'checking'}))
        cursor = self.cursor()
        self.assertEqual(cursor['stage'], 'checking')
        for key in ('user_request', 'lesson_scope', 'worked_example', 'coverage_note'):
            self.assertEqual(cursor[key], before[key])
            self.assertNotIn(key, result['changed_fields'])

    def test_worked_example_update_keeps_existing_fields(self):
        self.event(self.payload({'stage': 'intro',
                                 'worked_example': {'kind': 'assistant_created',
                                                    'description': '旧示例'}}))
        result = self.event(self.payload({'stage': 'intro',
                                          'worked_example': {'kind': 'assistant_created',
                                                             'description': '新示例',
                                                             'note': '补充说明'}}))
        self.assertEqual(result['status'], 'recorded')
        example = self.cursor()['worked_example']
        self.assertEqual(example['description'], '新示例')
        self.assertEqual(example['note'], '补充说明')
        self.assertEqual(example['kind'], 'assistant_created')

    def test_descriptive_lesson_records_accept_boundary_values(self):
        body = self.payload({'stage': 'intro',
                             'user_request': 'x' * 500,
                             'lesson_scope': ['s%02d' % index + 'x' * 297
                                              for index in range(12)],
                             'worked_example': {'description': 'y' * 3900},
                             'coverage_note': 'z' * 800})
        self.event(body)
        cursor = self.cursor()
        self.assertEqual(len(cursor['user_request']), 500)
        self.assertEqual(len(cursor['lesson_scope']), 12)
        self.assertTrue(all(len(item) == 300 for item in cursor['lesson_scope']))
        self.assertEqual(len(cursor['coverage_note']), 800)

    def test_descriptive_lesson_records_reject_bad_types_and_limits(self):
        before = self.raw()
        cases = [
            ('user_request', ''),
            ('user_request', 'x' * 501),
            ('user_request', 5),
            ('user_request', None),
            ('coverage_note', ''),
            ('coverage_note', 'x' * 801),
            ('coverage_note', ['x']),
            ('lesson_scope', 'not-a-list'),
            ('lesson_scope', []),
            ('lesson_scope', ['ok'] * 13),
            ('lesson_scope', ['x' * 301]),
            ('lesson_scope', ['ok', 3]),
            ('worked_example', 'text'),
            ('worked_example', []),
            ('worked_example', {}),
            ('worked_example', {'description': 'x' * 4000}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=str(value)[:20]):
                with self.assertRaises(ValueError) as caught:
                    self.event(self.payload({key: value}))
                self.assertIn('fields.', str(caught.exception))
                self.assertEqual(self.raw(), before)

    def test_descriptive_lesson_records_cannot_bypass_mastery_or_evidence(self):
        self.event(self.payload({'stage': 'intro'}))
        before = self.read()
        raw_before = self.raw()
        attempts = [
            {'course_id': 'DEMO', 'fields': {'user_request': 'x', 'mastery_status': 'mastered'}},
            {'course_id': 'DEMO', 'fields': {'mastery_status': 'mastered'}},
            {'course_id': 'DEMO', 'fields': {'coverage_note': 'x', 'topic_evidence': []}},
            {'course_id': 'DEMO', 'fields': {'worked_example': {'kind': 'k'},
                                             'assistant_notes': ['x']}},
            {'course_id': 'DEMO', 'fields': {'lesson_scope': ['x']}, 'topic_evidence': []},
            {'course_id': 'DEMO', 'mastery_status': 'mastered', 'fields': {'stage': 's'}},
        ]
        for body in attempts:
            with self.assertRaises(ValueError):
                self.event(body)
            self.assertEqual(self.raw(), raw_before)
        cursor = self.cursor()
        self.assertEqual(cursor['mastery_status'], 'not_assessed')
        # Nested prose is descriptive data only; it cannot change the recorded
        # mastery state or any evidence collection.
        self.event(self.payload({'worked_example': {'kind': 'assistant_created',
                                                    'description': '示例',
                                                    'mastery_status': 'mastered'}}))
        cursor = self.cursor()
        self.assertEqual(cursor['mastery_status'], 'not_assessed')
        self.assertEqual(cursor['worked_example']['mastery_status'], 'mastered')
        saved = self.read()
        self.assertEqual(saved['courses'][0].get('topic_evidence'),
                         before['courses'][0].get('topic_evidence'))
        self.assertEqual(saved.get('mistakes'), before.get('mistakes'))
        self.assertEqual(saved.get('review_attempts'), before.get('review_attempts'))

    def test_chapter_switch_can_update_lesson_records_with_old_copy_archived(self):
        self.event(self.payload({'topic': 'Topic A', 'stage': 'intro',
                                 'lesson_scope': ['旧范围']}))
        result = self.event(self.payload({'topic': 'Topic B', 'stage': 'intro',
                                          'lesson_scope': ['新范围'],
                                          'coverage_note': '新章覆盖说明'}))
        self.assertTrue(result['history_appended'])
        cursor = self.cursor()
        self.assertEqual(cursor['topic'], 'Topic B')
        self.assertEqual(cursor['lesson_scope'], ['新范围'])
        self.assertEqual(cursor['coverage_note'], '新章覆盖说明')
        self.assertEqual(cursor['history'][0]['lesson_scope'], ['旧范围'])

    def test_chapter_switch_can_replace_source_while_same_chapter_rewrite_stays_rejected(self):
        self.event(self.payload({'topic': 'Topic A', 'stage': 'intro',
                                 'source': {'kind': 'assistant_general_explanation',
                                            'scope_note': '旧章范围'}}))
        old_source = deepcopy(self.cursor()['source'])
        before = self.raw()
        with self.assertRaisesRegex(ValueError, 'source'):
            self.event(self.payload({'topic': 'Topic A', 'stage': 'next',
                                     'source': {'scope_note': '同章改写'}}))
        self.assertEqual(self.raw(), before)
        result = self.event(self.payload({'topic': 'Topic B', 'stage': 'intro',
                                          'source': {'kind': 'assistant_general_explanation',
                                                     'scope_note': '新章范围'}}))
        self.assertTrue(result['history_appended'])
        self.assertIn('source', result['changed_fields'])
        cursor = self.cursor()
        self.assertEqual(cursor['source'],
                         {'kind': 'assistant_general_explanation', 'scope_note': '新章范围'})
        self.assertEqual(cursor['history'][0]['source'], old_source)

    # -- chapter switch / history --------------------------------------
    def test_switch_archives_previous_cursor_exactly_once(self):
        self.event(self.payload({'topic': 'Topic A', 'chapter': 'Ch 1', 'stage': 'intro'}))
        before = self.cursor()
        expected = active_snapshot(before)
        result = self.event(self.payload({'topic': 'Topic B', 'chapter': 'Ch 2', 'stage': 'intro'},
                                         review_backlog_append=[{'id': 'review-a', 'topic': 'Topic A',
                                                                'chapter': 'Ch 1'}]))
        self.assertTrue(result['history_appended'])
        cursor = self.cursor()
        self.assertEqual(cursor['topic'], 'Topic B')
        self.assertEqual(cursor['history'], [expected])
        backlog = self.read()['courses'][0]['review_backlog']
        self.assertEqual(backlog[0]['id'], 'review-a')
        self.assertEqual(backlog[0]['status'], 'pending')
        self.assertEqual(cursor['checks'], before['checks'])

    def test_switch_twice_appends_history_and_never_rewrites_it(self):
        self.event(self.payload({'topic': 'A', 'stage': 's1'}))
        snapshot_a = active_snapshot(self.cursor())
        self.event(self.payload({'topic': 'B', 'stage': 's1'}))
        snapshot_b = active_snapshot(self.cursor())
        self.event(self.payload({'topic': 'C', 'stage': 's1'}))
        cursor = self.cursor()
        self.assertEqual(cursor['topic'], 'C')
        self.assertEqual(cursor['history'], [snapshot_a, snapshot_b])

    def test_legacy_dictionary_history_is_migrated_losslessly_on_switch(self):
        legacy = {'old_note': 'keep this', 'pending_check': {'answer': 0}}
        old = {'topic': 'A', 'stage': 's1', 'history': legacy}
        self.seed_cursor(deepcopy(old))
        result = self.event(self.payload({'topic': 'B', 'stage': 's2'}))
        cursor = self.cursor()
        self.assertTrue(result['history_appended'])
        self.assertEqual(cursor['history'][0], legacy)
        self.assertEqual(cursor['history'][1], active_snapshot(old))

    # -- pending questions ---------------------------------------------
    def test_answered_pending_requires_explicit_archive_before_replacement(self):
        old = {'id': 'q1', 'prompt': 'P', 'user_answer': '42', 'answer': '42'}
        self.seed_cursor({'topic': 'A', 'pending_check': old})
        before = self.raw()
        with self.assertRaisesRegex(ValueError, '待答题已有实际作答'):
            self.event(self.payload({'pending_check': {'id': 'q2', 'prompt': 'Q2'}}))
        self.assertEqual(self.raw(), before)
        result = self.event(self.payload({'pending_check': {'id': 'q2', 'prompt': 'Q2'}},
                                         archive_pending=True))
        self.assertTrue(result['pending_archived'])
        cursor = self.cursor()
        self.assertEqual(cursor['pending_check']['id'], 'q2')
        self.assertEqual(cursor['checks'][-1], old)

    def test_answered_pending_can_be_archived_through_checks_append(self):
        old = {'id': 'q1', 'prompt': 'P', 'answer': '42', 'conditions': {'independent': True}}
        self.seed_cursor({'topic': 'A', 'pending_check': old})
        self.event(self.payload({'pending_check': {'id': 'q2', 'prompt': 'Q2'}},
                                checks_append=[deepcopy(old)]))
        cursor = self.cursor()
        self.assertEqual(cursor['pending_check']['id'], 'q2')
        self.assertEqual(cursor['checks'], [old])

    def test_pending_payload_cannot_smuggle_an_answer(self):
        before = self.raw()
        for record in ({'id': 'q2', 'prompt': 'Q2', 'answer': 'x'},
                       {'id': 'q2', 'prompt': 'Q2', 'response': 'x'},
                       {'id': 'q2', 'prompt': 'Q2', 'status': 'answered'},
                       {'id': 'q2', 'prompt': 'Q2', 'parts': [{'user_answer': 0}]}):
            with self.subTest(record=record):
                with self.assertRaisesRegex(ValueError, '待作答'):
                    self.event(self.payload({'pending_check': record}))
                self.assertEqual(self.raw(), before)

    def test_pending_without_answer_is_replaced_and_cleared(self):
        self.event(self.payload({'pending_check': {'id': 'q1', 'prompt': 'P'}}))
        self.event(self.payload({'pending_check': {'id': 'q2', 'prompt': 'P2'}}))
        self.assertEqual(self.cursor()['pending_check']['id'], 'q2')
        self.event(self.payload({'pending_check': None}))
        self.assertNotIn('pending_check', self.cursor())

    # -- protected content ---------------------------------------------
    def test_mastery_and_other_collections_are_not_event_fields(self):
        before = self.raw()
        for fields in ({'mastery_status': 'mastered'}, {'checks': []}, {'history': []},
                       {'topic_evidence': []}, {'source': {}}):
            if fields == {'source': {}}:
                with self.assertRaisesRegex(ValueError, 'source'):
                    self.event(self.payload(fields))
                continue
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, '未知'):
                    self.event(self.payload(fields))
                self.assertEqual(self.raw(), before)

    def test_unknown_top_level_and_record_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '未知事件字段'):
            self.event({'course_id': 'DEMO', 'mistakes': []})
        with self.assertRaisesRegex(ValueError, '未知'):
            self.event(self.payload({'stage': 's'}, unknown_key='x'))

    def test_source_allows_new_keys_and_rejects_existing_rewrite(self):
        self.event(self.payload({'source': {'kind': 'assistant_general_explanation',
                                            'scope_note': 'old'}}))
        with self.assertRaisesRegex(ValueError, '既有来源证据不能改写'):
            self.event(self.payload({'source': {'scope_note': 'new'}}))
        result = self.event(self.payload({'source': {'verified_pages': [1, 2]}}))
        self.assertEqual(result['status'], 'recorded')
        source = self.cursor()['source']
        self.assertEqual(source['kind'], 'assistant_general_explanation')
        self.assertEqual(source['scope_note'], 'old')
        self.assertEqual(source['verified_pages'], [1, 2])

    def test_unknown_course_and_material_are_rejected_without_write(self):
        before = self.raw()
        with self.assertRaisesRegex(ValueError, '课程不存在'):
            self.event({'course_id': 'OTHER', 'fields': {'stage': 's'}})
        with self.assertRaisesRegex(ValueError, 'material_id'):
            self.event(self.payload({'material_id': 'MISSING'}))
        self.assertEqual(self.raw(), before)
        self.event(self.payload({'material_id': 'MAT-1'}))
        self.assertEqual(self.cursor()['material_id'], 'MAT-1')

    def test_size_limits_and_empty_event_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '上限'):
            self.event(self.payload({'chapter': 'x' * 301}))
        with self.assertRaisesRegex(ValueError, '最多'):
            self.event(self.payload({'stage': 's'}, checks_append=[
                {'id': 'c%d' % index, 'prompt': 'P'} for index in range(6)]))
        with self.assertRaisesRegex(ValueError, '没有可应用的变化'):
            self.event({'course_id': 'DEMO'})
        with self.assertRaisesRegex(ValueError, 'archive_pending'):
            self.event({'course_id': 'DEMO', 'archive_pending': True})

    def test_existing_learning_evidence_is_byte_identical(self):
        state = self.read()
        state['mistakes'] = [{'id': 'M', 'course_id': 'DEMO', 'user_answer': 'original',
                              'source': {'material_id': 'MAT-1', 'pages': [1]}}]
        state['review_attempts'] = [{'attempt_id': 'A', 'answer': 'original'}]
        state['learning_feedback'] = [{'feedback_id': 'F', 'completed': 'chapter 1'}]
        state['courses'][0]['topic_evidence'] = [{'id': 'E', 'topic': 'T', 'level': 'explained',
                                                  'source': {'kind': 'user_report'}}]
        state_io.atomic(self.path, state)
        self.event(self.payload({'stage': 'intro'}))
        saved = self.read()
        for name in ('mistakes', 'review_attempts', 'learning_feedback'):
            self.assertEqual(saved[name], state[name])
        self.assertEqual(saved['courses'][0]['topic_evidence'], state['courses'][0]['topic_evidence'])

    # -- no-op / idempotency / conflicts -------------------------------
    def test_unchanged_event_does_not_write_or_rebuild_views(self):
        body = self.payload({'stage': 'intro', 'next_step': 'wait'})
        self.event(body, operation_id='op-1')
        before_state, before_views = self.raw(), self.view_bytes()
        stages_before = self.timing_stages()
        result = self.event(body, operation_id='op-2')
        self.assertEqual(result['status'], 'unchanged')
        self.assertFalse(result['state_written'])
        self.assertEqual(result['views_updated'], [])
        self.assertEqual(self.raw(), before_state)
        self.assertEqual(self.view_bytes(), before_views)
        after = self.timing_stages()
        self.assertEqual(after.count('state_save'), stages_before.count('state_save'))
        self.assertEqual(after.count('view_generation'), stages_before.count('view_generation'))

    def test_same_content_retry_is_duplicate_not_a_second_event(self):
        body = self.payload({'stage': 'intro'},
                            checks_append=[{'id': 'c1', 'prompt': 'P'}])
        first = self.event(body)
        self.assertFalse(first.get('duplicate'))
        committed = self.raw()
        second = self.event(body)
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['status'], 'duplicate')
        self.assertEqual(self.raw(), committed)
        self.assertEqual([row['id'] for row in self.cursor()['checks']], ['c1'])

    def test_same_operation_with_different_content_conflicts(self):
        self.event(self.payload({'stage': 'intro'}), operation_id='shared-op')
        before = self.raw()
        with self.assertRaisesRegex(ValueError, '同一操作编号内容冲突'):
            self.event(self.payload({'stage': 'other'}), operation_id='shared-op')
        self.assertEqual(self.raw(), before)

    def test_view_crash_keeps_commit_and_retry_only_repairs_views(self):
        body = self.payload({'stage': 'intro'}, checks_append=[{'id': 'c1', 'prompt': 'P'}])
        with patch.object(daily, 'write_views', side_effect=OSError('模拟视图崩溃')):
            with self.assertRaises(OSError):
                self.event(body)
        saved = self.read()
        self.assertEqual(saved['revision'], 2)
        self.assertIn('operation_receipts', saved)
        self.assertEqual([row['id'] for row in saved['courses'][0]['teaching_cursor']['checks']],
                         ['c1'])
        retried = self.event(body)
        self.assertTrue(retried['duplicate'])
        self.assertEqual(self.read()['revision'], 2)
        self.assertEqual(len(self.read()['courses'][0]['teaching_cursor']['checks']), 1)
        self.assertTrue(daily.execute(self.path, 'check', review.timestamp())['fresh'])

    def test_planning_cross_day_and_teaching_change_commit_once(self):
        day1 = review.timestamp('2026-11-08T21:00:00+08:00')
        day2 = review.timestamp('2026-11-09T09:00:00+08:00')
        first = daily.execute(self.path, 'teaching-event', day1,
                              payload={**self.payload({'stage': 'intro'}), 'chat_id': 'chat-a'},
                              operation_id='day-1')
        self.assertFalse(first.get('duplicate'))
        revision_after_day1 = self.read()['revision']
        saves_before = self.timing_stages().count('state_save')
        second = daily.execute(self.path, 'teaching-event', day2,
                               payload={**self.payload({'stage': 'reviewed'}), 'chat_id': 'chat-a'},
                               operation_id='day-2')
        self.assertEqual(second['status'], 'recorded')
        saved = self.read()
        self.assertEqual(saved['revision'], revision_after_day1 + 1)
        self.assertEqual(saved['planning']['plan_date'], '2026-11-09')
        self.assertEqual(saved['courses'][0]['teaching_cursor']['stage'], 'reviewed')
        self.assertEqual(self.timing_stages().count('state_save'), saves_before + 1)
        self.assertIn('planning', second['changed_fields'])

    # -- onboarding in the same transaction ----------------------------
    def test_onboarding_is_merged_once_in_the_same_save(self):
        body = self.payload({'stage': 'intro'}, onboarding='shown')
        result = self.event(body)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['onboarding_status'], 'recorded')
        saved = self.read()
        events = saved['settings']['product_onboarding']['events']
        self.assertEqual([event['outcome'] for event in events], ['shown'])
        self.assertEqual(saved['revision'], 2)
        # Same content, a different explicit operation: onboarding is already
        # recorded, so the whole event is a no-op with one view rebuild avoided.
        committed = self.raw()
        again = self.event(body, operation_id='onboarding-repeat')
        self.assertEqual(again['status'], 'unchanged')
        self.assertEqual(again['onboarding_status'], 'already_recorded')
        self.assertEqual(self.raw(), committed)
        self.assertEqual(len(self.read()['settings']['product_onboarding']['events']), 1)

    def test_skipped_onboarding_is_saved_without_cursor_claim_of_mastery(self):
        result = self.event(self.payload({'stage': 'intro'}, onboarding='skipped'))
        self.assertEqual(result['onboarding_status'], 'recorded')
        self.assertEqual(self.read()['settings']['product_onboarding']['events'][0]['outcome'],
                         'skipped')
        self.assertFalse(result['mastery_updated'])

    def test_onboarding_only_event_does_not_create_an_empty_position(self):
        result = self.event(self.payload(onboarding='shown'))
        self.assertEqual(result['status'], 'recorded')
        saved = self.read()
        self.assertEqual(len(saved['settings']['product_onboarding']['events']), 1)
        self.assertIsNone(saved['courses'][0]['teaching_cursor'])

    def test_malformed_onboarding_history_is_not_reset(self):
        state = self.read()
        state['settings']['product_onboarding'] = {'events': 'bad'}
        state_io.atomic(self.path, state)
        before = self.raw()
        with self.assertRaises(ValueError):
            self.event(self.payload({'stage': 'intro'}, onboarding='shown'))
        self.assertEqual(self.raw(), before)

    # -- CLI identity and subtask gate ---------------------------------
    def test_cli_records_event_with_resolved_chat_identity(self):
        body = self.payload({'stage': 'cli-intro'})
        result = self.run_cli(['--chat', 'cli-chat', 'teaching-event'], body)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(output['status'], 'recorded')
        cursor = self.read()['courses'][0]['teaching_cursor']
        self.assertEqual(cursor['chat_id'], 'cli-chat')

    def test_cli_subtask_cannot_write_teaching_events(self):
        result = self.run_cli(['--chat', 'cli-chat', '--subtask', 'teaching-event'],
                              self.payload({'stage': 'x'}))
        self.assertEqual(result.returncode, 1)
        self.assertIn('内部任务只允许读取接续和状态', result.stderr)

    def test_cli_forged_chat_id_is_rejected(self):
        body = {**self.payload({'stage': 'x'}), 'chat_id': 'another-chat'}
        result = self.run_cli(['--chat', 'cli-chat', 'teaching-event'], body)
        self.assertEqual(result.returncode, 1)
        self.assertIn('聊天 ID', result.stderr)
        self.assertIsNone(self.read()['courses'][0]['teaching_cursor'])

    def test_cli_requires_a_stable_chat_identity(self):
        result = subprocess.run([sys.executable, '-B', str(CLI), '--root', str(self.root),
                                 'teaching-event'], input=json.dumps(self.payload({'stage': 'x'})),
                                text=True, encoding='utf-8', capture_output=True,
                                cwd=str(HERE), env=env_without_chat_identity(), timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn('原始用户聊天 ID', result.stderr)

    def test_concurrent_event_writers_are_serialized(self):
        env = env_without_chat_identity()
        children = []
        for index in range(2):
            body = {'course_id': 'DEMO',
                    'fields': {'stage': 'worker-%d' % index},
                    'checks_append': [{'id': 'c-%d' % index, 'prompt': 'P%d' % index}]}
            children.append(subprocess.Popen(
                [sys.executable, '-B', str(CLI), '--root', str(self.root),
                 '--chat', 'chat-%d' % index, 'teaching-event'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', cwd=str(HERE), env=env))
        for index, child in enumerate(children):
            body = {'course_id': 'DEMO',
                    'fields': {'stage': 'worker-%d' % index},
                    'checks_append': [{'id': 'c-%d' % index, 'prompt': 'P%d' % index}]}
            child.stdin.write(json.dumps(body, ensure_ascii=False))
            child.stdin.close()
            child.stdin = None
        for child in children:
            _, error = child.communicate(timeout=60)
            self.assertEqual(child.returncode, 0, error)
        cursor = self.read()['courses'][0]['teaching_cursor']
        self.assertEqual({row['id'] for row in cursor['checks']}, {'c-0', 'c-1'})
        self.assertEqual(len(cursor['checks']), 2)
        self.assertIn(cursor['stage'], {'worker-0', 'worker-1'})
        self.assertTrue(daily.execute(self.path, 'check', review.timestamp())['fresh'])

    # -- crash recovery of a pending material transaction --------------
    def test_incomplete_material_transaction_is_recovered_before_event(self):
        from learning_library import versions

        class SimulatedProcessExit(BaseException):
            """Bypass ordinary Exception rollback, like process termination does."""

        state = self.read()
        old = self.study / 'DEMO' / 'Lectures' / 'lesson.txt'
        incoming = self.study / '_Inbox' / 'new' / 'lesson.txt'
        old.parent.mkdir(parents=True)
        incoming.parent.mkdir(parents=True)
        old.write_text('OLD ORIGINAL', encoding='utf-8')
        incoming.write_text('NEW ORIGINAL', encoding='utf-8')

        def row(identifier, path):
            return {'id': identifier, 'course_id': 'DEMO', 'filename': 'lesson.txt',
                    'category': 'Lectures',
                    'relative_path': path.relative_to(self.study).as_posix(),
                    'sha256': versions.sha(path),
                    'moodle_file_sources': [{'module_id': 1, 'remote_filename': 'lesson.txt'}]}

        state['materials'] = [row('OLD', old), row('NEW', incoming)]
        state_io.atomic(self.path, state)
        journal = self.path.with_name('material-version-transaction.json')
        original_write = versions._write

        def interrupted(path, value):
            if Path(path) == self.path:
                raise SimulatedProcessExit()
            original_write(path, value)

        with patch.object(versions, '_write', side_effect=interrupted):
            with self.assertRaises(SimulatedProcessExit):
                versions.promote(self.path, row('NEW', incoming))
        self.assertTrue(journal.exists())
        result = self.event(self.payload({'stage': 'after-recovery'}))
        self.assertEqual(result['status'], 'recorded')
        self.assertFalse(journal.exists())
        saved = self.read()
        self.assertEqual(saved['courses'][0]['teaching_cursor']['stage'], 'after-recovery')
        for material in saved['materials']:
            self.assertEqual(versions.sha(self.study / material['relative_path']),
                             material['sha256'])

    def test_event_views_are_fresh_after_one_rebuild(self):
        self.event(self.payload({'stage': 'intro'}))
        self.assertTrue(daily.execute(self.path, 'check', review.timestamp())['fresh'])


if __name__ == '__main__':
    unittest.main()
