// Called only by the explicitly authorized download command, after byte validation.
import {spawnSync} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {mkdirSync, writeFileSync, readFileSync, realpathSync, existsSync, unlinkSync} from 'node:fs';
import {join, relative, resolve, sep} from 'node:path';

export function installVersion({study, statePath, script, python, source, bytes}) {
  study = realpathSync(study);
  const directory = join(study, '_Inbox', '.versions', randomUUID());
  mkdirSync(directory, {recursive: true});
  if (realpathSync(directory) !== directory) throw new Error('Redirected staging folder');
  const staged = resolve(directory, source.filename);
  if (!staged.startsWith(directory + sep) || relative(directory, staged) !== source.filename)
    throw new Error('Unsafe material filename');
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  writeFileSync(staged, bytes, {flag: 'wx'});
  const child = spawnSync(python, [script, '--state', statePath, 'install-download'], {
    input: JSON.stringify({source, relative_path: relative(study, staged).replaceAll('\\', '/'), sha256, bytes: bytes.length}),
    encoding: 'utf8', windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'},
    maxBuffer: 1024 * 1024
  });
  if (child.error || child.status !== 0)
    throw new Error('Version promotion needs review; verified download retained in _Inbox and existing sources preserved');
  const result = JSON.parse(child.stdout);
  // Only an identical staged copy created by this invocation may be removed.
  if (result.status === 'identical' && existsSync(staged) && realpathSync(staged) === staged
      && createHash('sha256').update(readFileSync(staged)).digest('hex') === sha256) unlinkSync(staged);
  return {...result, sha256, bytes: bytes.length};
}
