// Chapter receipts never publish or acknowledge a full school-information snapshot.
import {createHash, randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, realpathSync, renameSync, unlinkSync, writeFileSync} from 'node:fs';
import {join, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {python} from './state-bridge.mjs';
import {resolveChatId} from './chat-identity.mjs';
import {normalizeSite} from './content-policy.mjs';
import {connectionDiagnostic} from './connection-errors.mjs';

const codeRoot = fileURLToPath(new URL('../../', import.meta.url));
const digest = value => createHash('sha256').update(value).digest('hex');
const usage = message => Object.assign(new Error(message), {code: 'CHAPTER_USAGE'});
const readJSON = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const diagnostic = error => connectionDiagnostic(error) ?? {
  connection_error: 'CHAPTER_CHECK_FAILED', message: '本次章节核对未完成；保留本地资料，未输出可能含凭据的错误正文。',
};

export function chapterPaths(root = codeRoot) {
  return {studyRoot: join(root, 'Study'), statePath: join(root, 'Study/_System/state.json'),
    studyToolsDir: join(codeRoot, 'tools/study'), python: python(root)};
}

export function parseChapterArgs(args, environment = process.env) {
  const result = {materialIds: [], force: false};
  const names = {'--course': 'courseId', '--chapter': 'chapter', '--chat': 'sessionId'};
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    if (flag === '--force' && !result.force) result.force = true;
    else if (flag === '--material' || names[flag]) {
      const value = args[++i];
      if (!value || value.startsWith('--') || !value.trim()) throw usage('每个参数必须提供非空值。');
      if (flag === '--material') {
        const ids = value.split(',').map(id => id.trim());
        if (ids.some(id => !id)) throw usage('资料 ID 不能为空。');
        result.materialIds.push(...ids);
      } else {
        if (result[names[flag]] !== undefined) throw usage('课程、章节和聊天参数不可重复。');
        result[names[flag]] = value.trim();
      }
    } else throw usage('章节核对只接受 --chat --course --chapter --material 和 --force。');
  }
  try {result.sessionId = resolveChatId(result.sessionId, environment)?.chat;}
  catch {throw usage('缺少原始用户聊天 ID；内部任务不能使用自己的会话 ID 触发核对。');}
  result.materialIds = [...new Set(result.materialIds)];
  if (!result.courseId || !result.chapter || !result.materialIds.length)
    throw usage('请指定课程、章节和从本地资料索引确认的本章课件 ID。');
  if (!result.sessionId) throw usage('缺少稳定的聊天 ID；请传 --chat，勿为每次调用新建 ID。');
  return result;
}

function envelopePath(paths, command, identity) {
  const root = realpathSync(paths.studyRoot);
  const folder = join(root, '_System', 'CourseContent', 'chapter-checks', 'connections');
  mkdirSync(folder, {recursive: true});
  if (!resolve(folder).startsWith(root + sep) || realpathSync(folder) !== resolve(folder)) throw usage('核对记录目录不安全。');
  return join(folder, digest(JSON.stringify([identity.site, identity.credentialKey, command.sessionId,
    command.courseId, command.chapter])) + '.json');
}

function saveEnvelope(path, value) {
  if (existsSync(path) && realpathSync(path) !== resolve(path)) throw usage('核对记录文件不安全。');
  const temporary = path + '.' + randomUUID() + '.tmp';
  try {
    writeFileSync(temporary, JSON.stringify(value, null, 2), {flag: 'wx'});
    readJSON(temporary); renameSync(temporary, path);
  } finally {if (existsSync(temporary)) unlinkSync(temporary);}
}

// This runs inside the tool. Neither encrypted bytes nor tokens leave the process.
export function localConnectionIdentity(root = codeRoot) {
  const config = readJSON(join(root, 'config.local.json'));
  return {site: normalizeSite(config.moodle_url),
    credentialKey: digest(readFileSync(join(root, '.private/moodle-token.dpapi')))};
}

export async function runChapterCommand(args, {
  root = codeRoot, paths = chapterPaths(root), environment = process.env, createClient,
  connectionIdentity = () => localConnectionIdentity(root), verify, cached,
  output = value => console.log(JSON.stringify(value)),
} = {}) {
  try {
    const command = parseChapterArgs(args, environment);
    const implementation = (!verify || !cached) ? await import('./chapter-verify.mjs') : {};
    verify ??= implementation.verifyChapter;
    cached ??= implementation.readCachedChapterCheck;
    const identity = connectionIdentity();
    identity.site = normalizeSite(identity.site);
    const path = envelopePath(paths, command, identity);
    if (!command.force && existsSync(path)) {
      if (realpathSync(path) !== resolve(path)) throw usage('核对记录文件不安全。');
      const previous = readJSON(path);
      if (previous.startup_failure) {output({...previous.startup_failure, reused: true}); return 1;}
      if (previous.account_scope?.site === identity.site) {
        const receipt = await cached({...paths, ...command, site: identity.site, userId: previous.account_scope.user_id});
        if (receipt) {output(receipt); return receipt.ready_for_teaching === true ? 0 : 1;}
      }
    }
    if (!createClient) throw usage('缺少 Moodle 连接入口。');
    let client;
    try {
      client = await createClient(identity);
      const after = connectionIdentity();
      if (normalizeSite(client.baseUrl) !== identity.site || normalizeSite(after.site) !== identity.site
          || after.credentialKey !== identity.credentialKey) throw new Error('connection_scope_changed');
    } catch (error) {
      const failure = {operation: 'verify_chapter', course_id: command.courseId, chapter: command.chapter,
        status: 'unverified', ready_for_teaching: false, reused: false, attempted_at: new Date().toISOString(),
        ...diagnostic(error), message_suffix: '本次未核实更新；可使用已存本地版。明确重查时使用 --force。'};
      saveEnvelope(path, {startup_failure: failure}); output(failure); return 1;
    }
    saveEnvelope(path, {account_scope: {site: identity.site, user_id: client.userId}});
    const result = await verify(client, {...paths, ...command});
    output(result); return result.ready_for_teaching === true ? 0 : 1;
  } catch (error) {
    output({operation: 'verify_chapter', status: 'unverified', ready_for_teaching: false,
      ...(error.code === 'CHAPTER_USAGE' ? {category: 'configuration', message: error.message} : diagnostic(error))});
    return 1;
  }
}
