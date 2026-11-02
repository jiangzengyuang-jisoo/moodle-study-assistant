// Called only by the explicitly authorized download command, after byte validation.
import {spawnSync} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {mkdirSync, writeFileSync, realpathSync, existsSync, unlinkSync, statSync, openSync, readSync, closeSync} from 'node:fs';
import {join, relative, resolve, sep} from 'node:path';

function fileHash(path) {
  const hash = createHash('sha256'), chunk = Buffer.alloc(1024 * 1024), fd = openSync(path, 'r');
  try { for (let size; (size = readSync(fd, chunk)) > 0;) hash.update(chunk.subarray(0, size)); }
  finally { closeSync(fd); }
  return hash.digest('hex');
}

export function installVersion({study, statePath, script, python, source, bytes, stagedPath,
  deferIndex = false, sourceSubdirectory = false, retainUnparsed = false}) {
  study = realpathSync(study);
  if ((bytes === undefined) === (stagedPath === undefined)) throw new Error('Supply bytes or a staged path');
  const directory = stagedPath === undefined ? join(study, '_Inbox', '.versions', randomUUID())
    : resolve(stagedPath, '..');
  if (stagedPath === undefined) mkdirSync(directory, {recursive: true});
  if (realpathSync(directory) !== directory) throw new Error('Redirected staging folder');
  const staged = resolve(directory, source.filename);
  if (!staged.startsWith(directory + sep) || relative(directory, staged) !== source.filename)
    throw new Error('Unsafe material filename');
  if (stagedPath !== undefined && (resolve(stagedPath) !== staged || realpathSync(staged) !== staged
      || !staged.startsWith(join(study, '_Inbox') + sep) || !statSync(staged).isFile()))
    throw new Error('Staged content must be an original file inside _Inbox');
  if (bytes !== undefined) writeFileSync(staged, bytes, {flag: 'wx'});
  const sha256 = fileHash(staged), byteCount = statSync(staged).size;
  const child = spawnSync(python, [script, '--state', statePath, 'install-download'], {
    input: JSON.stringify({source, relative_path: relative(study, staged).replaceAll('\\', '/'), sha256, bytes: byteCount,
      defer_index: deferIndex, source_subdirectory: sourceSubdirectory, retain_unparsed: retainUnparsed}),
    encoding: 'utf8', windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'},
    maxBuffer: 1024 * 1024
  });
  if (child.error || child.status !== 0)
    throw new Error('Version promotion needs review; verified download retained in _Inbox and existing sources preserved');
  const result = JSON.parse(child.stdout);
  // Only an identical staged copy created by this invocation may be removed.
  if (stagedPath === undefined && result.status === 'identical' && existsSync(staged) && realpathSync(staged) === staged
      && fileHash(staged) === sha256) unlinkSync(staged);
  return {...result, sha256, bytes: byteCount};
}
