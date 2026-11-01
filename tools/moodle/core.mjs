import { saveState } from './state-bridge.mjs';
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { MoodleClient } from 'moodle-mcp/dist/moodle-client.js';
import { redactEvidence } from './refresh.mjs';
import {connectionError, classifyTransportError} from './connection-errors.mjs';

export function normalizeSite(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash)
    throw new Error('Moodle URL must be HTTPS, without credentials, query or fragment');
  return url.href.replace(/\/+$/, '');
}

export function atomicJSON(path, value) {
  const content = JSON.stringify(value, null, 2) + '\n';
  JSON.parse(content);
  const temp = `${path}.${process.pid}.tmp`;
  writeFileSync(temp, content, {encoding: 'utf8', flag: 'wx'});
  renameSync(temp, path);
}

export function initialize(root) {
  for (const dir of ['Study/_System', 'Study/_Inbox', '.private', '.codex']) mkdirSync(join(root, dir), {recursive: true});
  const configPath = join(root, 'config.local.json');
  if (!existsSync(configPath)) atomicJSON(configPath, {moodle_url: 'https://moodle.hku.hk', timezone: 'Asia/Hong_Kong'});
  const path = join(root, 'Study/_System/state.json');
  if (!existsSync(path)) {
    const state = {schema_version: 1, revision: 0, timezone: 'Asia/Hong_Kong',
      study_root: 'Study', created_at: new Date().toISOString(), settings: {
        brief_send_authorized: false, study_capacity_target: 0.8,
        study_capacity_range: [0.7, 0.85], review_minutes_range: [15, 30],
        moodle_refresh_policy: {mode: 'first_learning_turn_or_explicit_request', conversation_boundary: 'new_chat_thread',
          unrelated_tasks: 'skip_learning_entry', maintenance: 'diagnosis_only',
          automatic_interval_minutes: null, scheduled_background_run: false,
          refresh_on_course_switch: false, refresh_on_date_change: false,
          refresh_on_context_compaction: false, refresh_on_reopen_existing_conversation: false},
        learning_loop: {enabled: true, version: 1, default_review_minutes: 15, daily_max_minutes: 30, same_day_budget_shared: true},
        exam_preparation: {enabled: true, version: 1, answer_keys_separate: true, auto_download_materials: false, mastery_from_generation: false}
      }, integrations: {}, daily_plans: {}, weekly_plan: null};
    for (const key of ['courses','deadlines','tasks','materials','schedules','mistakes','review_queue','course_updates','discussion_notes','pending_inputs','activity_log',
      'review_sessions','review_attempts','learning_feedback','exam_profiles','mock_exams','mock_attempts']) state[key] = [];
    saveState(root, 'init', state);
  }
  const mcpPath = join(root, '.codex/config.toml');
  if (!existsSync(mcpPath)) writeFileSync(mcpPath,
    '[mcp_servers.moodle_study]\ncommand = ' + JSON.stringify(process.execPath.replaceAll('\\', '/')) +
    '\nargs = [' + JSON.stringify(join(root, 'tools/moodle/run.mjs').replaceAll('\\', '/')) + ', "mcp"]\nstartup_timeout_sec = 60\ntool_timeout_sec = 180\n', 'utf8');
  return JSON.parse(readFileSync(path, 'utf8'));
}

const READ_METHODS = new Set([
  'core_webservice_get_site_info', 'core_enrol_get_users_courses', 'core_course_get_contents',
  'mod_assign_get_assignments', 'mod_assign_get_submission_status', 'mod_quiz_get_quizzes_by_courses',
  'mod_quiz_get_user_attempts', 'mod_forum_get_forum_discussions', 'mod_forum_get_discussion_posts',
  'core_calendar_get_action_events_by_timesort',
]);

export class ReadOnlyClient extends MoodleClient {
  constructor(site, token, transport = fetch) {
    super(normalizeSite(site), token, 0);
    this.transport = transport;
  }
  async call(method, params = {}) {
    if (!READ_METHODS.has(method)) throw new Error('This API method is not allowed by the read-only policy');
    if (['wstoken', 'wsfunction', 'moodlewsrestformat'].some(key => key in params)) throw new Error('Reserved API parameter');
    const body = new URLSearchParams({wstoken: this.token, wsfunction: method, moodlewsrestformat: 'json',
      ...Object.fromEntries(Object.entries(params).map(([k,v]) => [k, String(v)]))});
    let data, response;
    try {
      response = await this.transport(`${this.baseUrl}/webservice/rest/server.php`, {
        method: 'POST', body, redirect: 'error', signal: AbortSignal.timeout(30000),
      });
    } catch (error) { throw classifyTransportError(error); }
    if (!response.ok) throw connectionError([401, 403].includes(response.status) ? 'STUDY_ACCESS' : 'STUDY_HTTP');
    try { data = await response.json(); }
    catch (error) { throw error instanceof SyntaxError ? connectionError('STUDY_RESPONSE') : classifyTransportError(error); }
    if (!data || typeof data !== 'object') throw connectionError('STUDY_RESPONSE');
    if (data.exception || data.error) {
      const code = String(data.errorcode ?? 'unknown').replace(/[^a-z_]/gi, '').slice(0, 80);
      if (['invalidtoken', 'expiredtoken'].includes(code)) throw connectionError('STUDY_AUTH');
      if (['accessexception', 'accesscontrol', 'webservice_access_exception', 'nopermissions'].includes(code)) throw connectionError('STUDY_ACCESS');
      throw new Error(`Moodle API failed: ${code}`);
    }
    return redactEvidence(data, this.token);
  }
  async downloadFile() { throw new Error('Course file downloads are disabled'); }
  async connect() {
    const info = await this.call('core_webservice_get_site_info');
    if (!Number.isInteger(info.userid) || info.userid <= 0) throw new Error('Invalid site identity response');
    this.userId = info.userid;
    this.siteName = info.sitename;
    this.supportedFunctions = new Set(info.functions?.map(f => f.name) ?? []);
    return this;
  }
}
