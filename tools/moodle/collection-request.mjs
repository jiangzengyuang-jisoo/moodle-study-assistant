// The collector consumes one immutable request, never a changing live state.
import {readFileSync, realpathSync, existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve, relative, isAbsolute, dirname, basename, join} from 'node:path';

export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const parse = bytes => JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));

export function loadCollectionRequest(root, requestPath, outputPath) {
  if (!requestPath || !outputPath) throw new Error('采集需要本轮固定请求和输出路径');
  const runs = realpathSync(join(root, 'Study/_System/Runtime/runs'));
  const folder = realpathSync(dirname(resolve(outputPath)));
  const rel = relative(runs, folder);
  if (!rel || rel.startsWith('..') || isAbsolute(rel) || dirname(rel) !== '.' ||
      !/^[a-f0-9]{32}$/.test(basename(folder)) || basename(outputPath) !== 'snapshot.json' ||
      realpathSync(requestPath) !== join(folder, 'request.json') || existsSync(outputPath))
    throw new Error('采集快照必须写入本轮独立目录');
  const bytes = readFileSync(requestPath);
  const request = parse(bytes);
  const manifest = parse(readFileSync(join(folder, 'manifest.json')));
  if (request.protocol !== 1 || request.run_id !== basename(folder) ||
      manifest.run_id !== request.run_id || manifest.request_sha256 !== sha256(bytes))
    throw new Error('采集请求已变化，不能继续');
  const config = parse(readFileSync(join(root, 'config.local.json')));
  // Capture exactly the bytes that will be decrypted, avoiding a second path read.
  const credential = readFileSync(join(root, '.private/moodle-token.dpapi'));
  if (config.moodle_url !== request.scope.site || sha256(credential) !== request.scope.credential_generation)
    throw new Error('学校或账户已变化，请明确重新检查');
  const ids = [...new Set(request.scope.courses.flatMap(c => c.moodle_course_ids))].sort((a,b) => a-b);
  if (JSON.stringify(ids) !== JSON.stringify(request.scope.course_ids) ||
      ids.some(i => !Number.isInteger(i) || i <= 0))
    throw new Error('采集请求课程范围无效');
  const tasks = request.collect_state?.tasks;
  if (!Array.isArray(tasks) || tasks.some(t => typeof t?.source?.url !== 'string'))
    throw new Error('采集请求缺少已登记任务链接，请明确重新检查');
  return {request, requestSha: sha256(bytes), state: {courses: request.scope.courses, tasks}, config, credential};
}

export function bindCollection(bundle, result, userId) {
  return {...result, account_scope: {site: bundle.config.moodle_url, user_id: userId},
    collection_binding: {protocol: 1, run_id: bundle.request.run_id, request_sha256: bundle.requestSha}};
}
