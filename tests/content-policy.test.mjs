import test from 'node:test';
import assert from 'node:assert/strict';
import {authorizedCoursePages, selectedCoursePages, grantCourseContent, contentModeFromAnswer} from '../tools/moodle/content-policy.mjs';

const scope = {site: 'https://school.example/', userId: 17};
const state = () => ({courses: [{id: 'math', moodle_course_ids: [9, 10]}, {id: 'econ', moodle_course_ids: [11]}],
  settings: {existing: true}, learning_feedback: [{answer: 'original'}]});
const grant = (value = state(), options = {}) => grantCourseContent(value, {...scope, courseIds: [9, 10], mode: 'full', ...options});

test('old course selections and metadata-only grants never authorize downloads', () => {
  assert.equal(authorizedCoursePages(state(), scope).size, 0);
  assert.equal(authorizedCoursePages(grant(state(), {mode: 'metadata'}), scope).size, 0);
  assert.equal(contentModeFromAnswer(''), null);
  assert.equal(contentModeFromAnswer('1'), 'full');
  assert.equal(contentModeFromAnswer('2'), 'metadata');
  assert.throws(() => contentModeFromAnswer('yes'));
});

test('authorization is bound to site/account and current selected page ids', () => {
  const value = grant();
  assert.deepEqual([...authorizedCoursePages(value, scope)], [[9, 'math'], [10, 'math']]);
  assert.equal(authorizedCoursePages(value, {...scope, userId: 18}).size, 0);
  assert.equal(authorizedCoursePages(value, {...scope, site: 'https://other.example'}).size, 0);
  value.courses[0].moodle_course_ids = [10];
  assert.deepEqual([...authorizedCoursePages(value, scope)], [[10, 'math']]);
  assert.equal(authorizedCoursePages(value, {...scope, userId: '17'}).size, 0);
});

test('grant updates preserve unrelated learning history without mutating input', () => {
  const original = state();
  const value = grant(original);
  assert.equal(original.settings.course_content_sync, undefined);
  assert.deepEqual(value.learning_feedback, original.learning_feedback);
  assert.equal(value.settings.existing, true);
  const revoked = grant(value, {courseIds: [9], mode: 'metadata'});
  assert.deepEqual([...authorizedCoursePages(revoked, scope)], [[10, 'math']]);
  assert.throws(() => grant(value, {courseIds: [999]}));
  assert.throws(() => grant(value, {mode: 'all'}));
});

test('personal course-page mappings work and ambiguous mappings fail closed', () => {
  const value = {courses: [{id: 'math', moodle_progress: {pages: [{course_id: 9}, {course_id: 10}]}}]};
  assert.deepEqual([...selectedCoursePages(value)], [[9, 'math'], [10, 'math']]);
  assert.equal(authorizedCoursePages(grant(value), scope).size, 2);
  value.courses.push({id: 'other', moodle_course_ids: [9]});
  assert.deepEqual([...selectedCoursePages(value)], [[10, 'math']]);
});

test('malformed or conflicting authorization does not become full consent', () => {
  const value = grant();
  value.settings.course_content_sync.grants.push({...value.settings.course_content_sync.grants[0], mode: 'metadata'});
  assert.deepEqual([...authorizedCoursePages(value, scope)], [[10, 'math']]);
  value.settings.course_content_sync.grants[1].authorized_at = 'invalid';
  assert.equal(authorizedCoursePages(value, scope).size, 0);
  value.settings.course_content_sync.version = 2;
  assert.throws(() => grant(value));
});
