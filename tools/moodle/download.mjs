// A separate opt-in command. Regular refresh and MCP never download files.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { basename, extname, join, relative, resolve, sep } from 'node:path';

const LIMIT = 64 * 1024 * 1024;
const EXTENSIONS = new Set(['.pdf', '.pptx', '.docx', '.xlsx', '.ppt', '.doc', '.xls', '.odt', '.txt', '.csv', '.rtf']);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
function safeName(value) {
  return typeof value === 'string' && value && value === basename(value) && !/[<>:"/\\|?*\x00-\x1f]/.test(value)
    && !/[. ]$/.test(value) && value !== '.' && value !== '..'
    && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value);
}

function sourceURL(site, source) {
  const base = new URL(site), url = new URL(source);
  const prefix = base.pathname.replace(/\/$/, '');
  if (base.protocol !== 'https:' || url.origin !== base.origin || url.username || url.password || url.hash)
    throw new Error('Unsupported course file source');
  if (url.pathname.startsWith(prefix + '/pluginfile.php/'))
    url.pathname = prefix + '/webservice' + url.pathname.slice(prefix.length);
  if (!url.pathname.startsWith(prefix + '/webservice/pluginfile.php/'))
    throw new Error('Unsupported course file endpoint');
  url.search = '';
  url.searchParams.set('forcedownload', '1');
  return url;
}

async function readFile(client, file, maxBytes) {
  const clean = sourceURL(client.baseUrl, file.fileurl);
  const authenticated = new URL(clean);
  authenticated.searchParams.set('token', client.token);
  let response;
  try {
    response = await client.transport(authenticated.toString(), {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(60000)});
    if (!response.ok || !response.body || /text\/html|application\/json/i.test(response.headers.get('content-type') ?? ''))
      throw new Error('invalid response');
    const declared = Number(response.headers.get('content-length'));
    if (declared > maxBytes) throw new Error('too large');
    const chunks = []; let length = 0;
    for await (const chunk of response.body) {
      length += chunk.length;
      if (length > maxBytes) throw new Error('too large');
      chunks.push(Buffer.from(chunk));
    }
    if (!length || (Number.isFinite(file.filesize) && file.filesize !== length)) throw new Error('size mismatch');
    const bytes = Buffer.concat(chunks), extension = extname(file.filename).toLowerCase();
    if (extension === '.pdf' && !bytes.subarray(0, 1024).includes(Buffer.from('%PDF-'))) throw new Error('not PDF');
    if (['.pptx','.docx','.xlsx','.odt'].includes(extension) && bytes.subarray(0, 2).toString() !== 'PK') throw new Error('not Office file');
    if (['.ppt','.doc','.xls'].includes(extension) && bytes.subarray(0, 8).toString('hex') !== 'd0cf11e0a1b11ae1') throw new Error('not Office file');
    return {bytes, source_url: clean.toString()};
  } catch {
    // Fetch errors may contain the authenticated URL. Never print it.
    throw new Error('Course file download failed (network, redirect, file type, size or integrity)');
  }
}

export async function downloadModules(client, state, root, {moduleIds, authorized = false, maxBytes = LIMIT} = {}) {
  if (authorized !== true) throw new Error('Explicit user authorization is required for these course files');
  if (!Array.isArray(moduleIds) || !moduleIds.length || moduleIds.length > 20
      || moduleIds.some(id => !Number.isInteger(id) || id <= 0) || new Set(moduleIds).size !== moduleIds.length)
    throw new Error('Specify 1–20 unique authorized module IDs');
  if (!Number.isInteger(maxBytes) || maxBytes <= 0 || maxBytes > LIMIT) throw new Error('Invalid file size limit');
  const selected = new Map((state.courses ?? []).flatMap(c => (c.moodle_course_ids ?? []).map(id => [id, c.id])));
  if (!selected.size) throw new Error('Choose courses first');
  const wanted = new Set(moduleIds), found = new Map();
  for (const [courseid, course_id] of selected) {
    const sections = await client.call('core_course_get_contents', {courseid});
    for (const section of sections) for (const module of section.modules ?? []) {
      if (wanted.has(module.id) && module.uservisible !== false && module.visible !== 0
          && ['resource', 'folder'].includes(module.modname)) found.set(module.id, {module, course_id});
    }
  }
  if (moduleIds.some(id => !found.has(id))) throw new Error('Requested file module is not visible in the selected courses');
  const project = realpathSync(root), study = resolve(project, 'Study'), inbox = join(study, '_Inbox');
  mkdirSync(inbox, {recursive: true});
  if (realpathSync(inbox) !== inbox) throw new Error('Download folder must not be redirected');
  const result = {checked_at: new Date().toISOString(), requested_module_ids: moduleIds, files: [], errors: [],
    note: 'Files remain unindexed in _Inbox until the assistant reads, classifies and records them. No learning progress inferred.'};
  for (const id of moduleIds) {
    const {module, course_id} = found.get(id);
    if (!safeName(course_id)) throw new Error('Course ID is not safe for a local folder');
    const files = (module.contents ?? []).filter(f => f.type === 'file');
    if (!files.length) result.errors.push({module_id: id, error: 'No downloadable files in this module'});
    for (const file of files) {
      if (!safeName(file.filename) || !EXTENSIONS.has(extname(file.filename).toLowerCase())) {
        result.errors.push({module_id: id, error: 'Unsupported or unsafe course filename'}); continue;
      }
      let temporary;
      try {
        const {bytes, source_url} = await readFile(client, file, maxBytes);
        const sha256 = digest(bytes);
        let directory = join(inbox, course_id, String(id));
        mkdirSync(directory, {recursive: true});
        if (realpathSync(directory) !== directory) throw new Error('Redirected folder');
        let target = join(directory, file.filename), status = 'downloaded';
        if (existsSync(target)) {
          if (realpathSync(target) !== target) throw new Error('Redirected file');
          if (digest(readFileSync(target)) === sha256) status = 'already_present_identical';
          else {directory = join(directory, 'Versions', sha256); mkdirSync(directory, {recursive: true}); target = join(directory, file.filename);}
        }
        if (!resolve(target).startsWith(study + sep) || realpathSync(directory) !== directory) throw new Error('Unsafe target');
        if (existsSync(target)) {
          if (realpathSync(target) !== target || digest(readFileSync(target)) !== sha256) throw new Error('File conflict');
          status = 'already_present_identical';
        } else {
          temporary = target + '.' + process.pid + '.part';
          writeFileSync(temporary, bytes, {flag: 'wx'});
          if (digest(readFileSync(temporary)) !== sha256) throw new Error('Saved file mismatch');
          renameSync(temporary, target); temporary = null;
        }
        result.files.push({course_id, module_id: id, filename: file.filename, bytes: bytes.length, sha256,
          relative_path: relative(study, target).replaceAll('\\', '/'), source_url, status});
      } catch {
        if (temporary && existsSync(temporary)) unlinkSync(temporary);
        result.errors.push({module_id: id, filename: file.filename, error: 'Course file could not be verified or saved; original files preserved'});
      }
    }
  }
  return result;
}
